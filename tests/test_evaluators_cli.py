"""The decisions sink and the evaluator entry point (P3-08).

What this module is about, in one sentence: the walk-forward writes trials, the evaluator layer
writes **decisions**, and the two files never share a record shape.

**The trial ledger is written to, and that is correct.** The driver calls
``volab.walkforward.run_configuration`` (R97), which appends one trial per contestant per loss per
configuration (R74) -- real trials, because a walk-forward ran. What the evaluator layer must not do
is add lines of its own, or put a second record shape in ``data/trials.jsonl``. So the ledger tests
below pin the *count the walk-forward wrote* (losses x contestants x configurations, derived), the
*shape of every trial line*, and that every decision went to ``data/decisions.jsonl``.

Every run here is against a ``tmp_path`` root, and the suite never sets ``allow_holdout``. The real
``data/trials.jsonl`` and ``data/comparisons.jsonl`` are not touched by anything in this file.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import SyntheticStore
from test_compare import TOY_COHORT, WINDOW
from test_evaluators import HAND_ORIGINS, _hand_evaluation
from test_walkforward_cli import _settings_with_root, _toy_panel_and_targets_by_horizon

import volab.evaluators.__main__ as cli
from volab.compare.cohorts import PHASE3_IV, MonteCarlo
from volab.compare.sink import Comparison
from volab.config import Settings, load_settings
from volab.evaluators import (
    ALPHAS,
    POSITION_CAP,
    TARGET_VOL,
    VolTargetResult,
    coverage,
)
from volab.evaluators.sink import DECISION_LEDGER, Decision, JsonlSink
from volab.features.build import SESSIONS_PER_YEAR
from volab.ingest.curate import BARS_DATASET
from volab.paths import Tier, curated_path
from volab.sessions import get_calendar, sessions_between
from volab.walkforward import LOSSES, TRIAL_LEDGER, Trial, count_trials

TOY = TOY_COHORT
NO_ERRORS = MonteCarlo(percent={}, source=None)
AT = dt.datetime(2026, 10, 29, 12, 0, tzinfo=dt.UTC)


def _strict(line: str) -> dict:
    """Parse a ledger line as a strict JSON reader would: a bare ``NaN`` is an error."""

    def refuse(token: str) -> object:
        raise ValueError(f"not strict JSON: {token}")

    return json.loads(line, parse_constant=refuse)


def _lines(path: Path) -> list[dict]:
    return [_strict(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# decide(): the pure part. Hand-built evaluations, hand-computed expectations.
# ---------------------------------------------------------------------------

# Ten origins, two contestants with very different variance forecasts, and returns chosen so the
# two contestants breach a different number of times at each alpha. A mutation that scores one
# contestant's breaches against the other's VaR, or reads the wrong alpha, changes a count.
TIGHT, WIDE = 1e-4, 1e-3  # variance forecasts: sigma = 0.01 and 0.0316
RETURNS = [-0.03, 0.01, -0.025, 0.0, -0.1, 0.02, -0.01, 0.0, 0.005, -0.021]
# VaR (positive magnitudes): tight 0.02326 at 1% and 0.01960 at 2.5%; wide 0.0736 and 0.0620.
BREACHES = {("tight", 0.01): 3, ("tight", 0.025): 4, ("wide", 0.01): 1, ("wide", 0.025): 1}


def _window(flags: Sequence[bool] = (False,) * 10, affected: int = 0) -> cli.DistributionWindow:
    return cli.DistributionWindow(flags=list(flags), holdings_affected=affected)


def _decisions(
    distributions: Sequence[bool] = (False,) * 10,
    count: int = 10,
    every: int = 5,
    *,
    affected: int = 0,
    known: bool = True,
) -> list[Decision]:
    evaluation = _hand_evaluation(
        {"tight": [TIGHT] * count, "wide": [WIDE] * count}, horizon=5, every=every
    )
    returns = {HAND_ORIGINS[i]: RETURNS[i] for i in range(count)}
    return cli.decide(
        evaluation,
        cohort="toy",
        returns=returns,
        distributions=_window(distributions, affected),
        distribution_flags_known=known,
        errors=NO_ERRORS,
    )


def test_decide_gives_one_decision_per_alpha() -> None:
    decisions = _decisions()
    assert [d.alpha for d in decisions] == list(ALPHAS)
    assert {d.symbol for d in decisions} == {"SPY"} and {d.horizon for d in decisions} == {5}
    assert {d.cohort for d in decisions} == {"toy"} and {d.stride for d in decisions} == {5}
    assert {d.origins for d in decisions} == {10}
    assert all(d.contestants == ("tight", "wide") for d in decisions)


def test_each_contestants_breaches_are_counted_against_its_own_var_at_each_alpha() -> None:
    for decision in _decisions():
        for name in ("tight", "wide"):
            result = decision.coverage[name]
            assert result.breaches == BREACHES[(name, decision.alpha)], (name, decision.alpha)
            assert result.expected == pytest.approx(decision.alpha * 10)


def test_the_fz0_comparison_is_the_decision_series_at_that_alpha() -> None:
    for decision in _decisions():
        assert isinstance(decision.fz0, Comparison)
        assert decision.fz0.loss == f"fz0_a{decision.alpha}"
        assert decision.fz0.origins == 10
        assert decision.fz0.cohort == "toy"


def test_the_vol_target_of_each_contestant_is_sized_from_its_own_forecast() -> None:
    periods = SESSIONS_PER_YEAR / 5
    for decision in _decisions():
        assert decision.vol_target is not None
        for name, variance in (("tight", TIGHT), ("wide", WIDE)):
            position = TARGET_VOL / (math.sqrt(variance) * math.sqrt(periods))
            assert position < POSITION_CAP, "the cap must not bind or this stops testing sizing"
            expected = sum(position * r for r in RETURNS) / 10 * periods
            assert decision.vol_target[name].mean_return == pytest.approx(expected)
            assert decision.vol_target[name].capped_origins == 0
        # Two different forecasts, two different positions: the numbers are not interchangeable.
        assert decision.vol_target["tight"].mean_return != pytest.approx(
            decision.vol_target["wide"].mean_return
        )


def test_the_distribution_flags_reach_vol_target_and_the_window_size_is_kept() -> None:
    flags = (True, False, True, False, False, False, False, False, False, False)
    for decision in _decisions(flags, affected=7):
        assert decision.vol_target is not None
        assert {r.distribution_sessions for r in decision.vol_target.values()} == {2}
        assert decision.distribution_window == 10
        assert decision.distribution_holdings_affected == 7, "passed through, not re-derived"


def test_whether_the_flags_could_be_known_is_carried_on_the_decision() -> None:
    assert all(d.distribution_flags_known for d in _decisions(known=True))
    assert not any(d.distribution_flags_known for d in _decisions(known=False))


def test_decide_refuses_a_cohort_the_evaluation_did_not_run() -> None:
    """The named cohort is threaded through, so ``decision_series``'s cross-check compares two
    different things and not the evaluation's label with itself."""
    with pytest.raises(ValueError, match="'other' was asked for but the evaluation ran cohort"):
        cli.decide(
            _hand_evaluation({"tight": [TIGHT], "wide": [WIDE]}),
            cohort="other",
            returns={HAND_ORIGINS[0]: RETURNS[0]},
            distributions=_window(),
            distribution_flags_known=True,
            errors=NO_ERRORS,
        )


def test_coverage_is_given_the_evaluations_stride_and_horizon_not_literals() -> None:
    """Stride 2 < horizon 5 is refused; stride 5 is not. A literal for either cannot do both."""
    refused = _decisions(every=2)
    assert all(
        r.refusal is not None and "stride 2 < horizon 5" in r.refusal
        for d in refused
        for r in d.coverage.values()
    )
    fine = _decisions(every=5)
    assert all(r.refusal is None for d in fine for r in d.coverage.values())
    assert all(r.kupiec_p is not None for d in fine for r in d.coverage.values())


def test_an_empty_evaluation_is_decided_without_calling_vol_target() -> None:
    """``vol_target([], [])`` raises, ``decision_series`` accepts an empty evaluation."""
    decisions = cli.decide(
        _hand_evaluation({"tight": [], "wide": []}),
        cohort="toy",
        returns={},
        distributions=_window([]),
        distribution_flags_known=True,
        errors=NO_ERRORS,
    )
    assert len(decisions) == len(ALPHAS)
    for decision in decisions:
        assert decision.origins == 0 and decision.vol_target is None
        assert decision.vol_target_refusal is not None
        assert "no comparable origin" in decision.vol_target_refusal
        assert all(r.refusal is not None for r in decision.coverage.values())


def test_a_populated_vol_target_has_no_refusal() -> None:
    assert all(d.vol_target_refusal is None for d in _decisions())


# ---------------------------------------------------------------------------
# distribution_window(): where has_distribution comes from.
# ---------------------------------------------------------------------------


def _bars_panel(sessions: Sequence[dt.date], flagged: Sequence[dt.date]) -> pd.DataFrame:
    """SPY bars with a flag, beside an exogenous row whose flag is missing, as the real panel is."""
    spy = pd.DataFrame(
        {
            "symbol": "SPY",
            "as_of": list(sessions),
            "close": 100.0,
            "has_distribution": [day in flagged for day in sessions],
        }
    )
    # ``load_curated_panel`` concatenates the bars with the exogenous series, whose rows have no
    # such column: the flag is missing there, and ``bool(pd.NA)`` raises.
    other = pd.DataFrame({"symbol": ["VIX"], "as_of": [sessions[2]], "close": [15.0]})
    return pd.concat([spy, other], ignore_index=True)


def _sessions() -> tuple[object, list[dt.date]]:
    calendar = get_calendar(load_settings())
    return calendar, sessions_between(calendar, dt.date(2024, 3, 1), dt.date(2024, 3, 29))


def _window_of(
    panel: pd.DataFrame, calendar: object, origins: Sequence[dt.date], horizon: int
) -> cli.DistributionWindow:
    return cli.distribution_window(
        panel, "SPY", calendar, origins, horizon=horizon, end=dt.date(2024, 12, 31)
    )


def test_the_window_is_the_sessions_the_holdings_span_and_counts_each_ex_date_once() -> None:
    calendar, sessions = _sessions()
    origins = [sessions[0], sessions[5]]
    # Holdings of 3 sessions: (S0, S3] and (S5, S8]. S0 is the origin itself, S4 falls between the
    # holdings, S9 is after the last: three ex-dates that no holding contains.
    flagged = [sessions[0], sessions[2], sessions[4], sessions[7], sessions[9]]
    window = _window_of(_bars_panel(sessions, flagged), calendar, origins, 3)
    assert window.flags.count(True) == 2, "S2 and S7 are the ex-dates a holding contains"
    assert len(window.flags) == 6, "S1-S3 and S6-S8"
    assert window.holdings_affected == 2, "one holding holds each"


def test_an_ex_date_in_overlapping_holdings_is_one_session_and_several_affected_holdings() -> None:
    """The reason the second count exists. Horizon 5 from S0, S2 and S4 overlap on S4-S5, and the
    ex-date on S4 sits inside the first two holdings but not the third (it is that holding's
    origin): one session, two affected holdings of three."""
    calendar, sessions = _sessions()
    window = _window_of(
        _bars_panel(sessions, [sessions[4]]), calendar, [sessions[0], sessions[2], sessions[4]], 5
    )
    assert window.flags.count(True) == 1 and len(window.flags) == 9
    assert window.holdings_affected == 2


def test_without_overlap_the_session_count_and_the_affected_holdings_agree() -> None:
    """Stride equal to the horizon, as at h=1 and h=5 under the default stride: no holding shares a
    session, so each ex-date is in exactly one holding and, one to a holding, the counts match."""
    calendar, sessions = _sessions()
    window = _window_of(
        _bars_panel(sessions, [sessions[2], sessions[7]]),
        calendar,
        [sessions[0], sessions[3], sessions[6]],
        3,
    )
    assert window.flags.count(True) == window.holdings_affected == 2


def test_two_ex_dates_in_one_holding_are_one_affected_holding() -> None:
    calendar, sessions = _sessions()
    window = _window_of(
        _bars_panel(sessions, [sessions[1], sessions[2]]), calendar, [sessions[0], sessions[3]], 3
    )
    assert window.flags.count(True) == 2 and window.holdings_affected == 1


def test_a_holding_with_no_ex_date_is_not_affected() -> None:
    calendar, sessions = _sessions()
    window = _window_of(_bars_panel(sessions, []), calendar, [sessions[0], sessions[3]], 3)
    assert window.flags.count(True) == 0 and window.holdings_affected == 0
    assert len(window.flags) == 6, "the sessions looked at are still stated"


def test_the_window_is_empty_with_no_origins() -> None:
    calendar, sessions = _sessions()
    window = _window_of(_bars_panel(sessions, [sessions[3]]), calendar, [], 3)
    assert window.flags == [] and window.holdings_affected == 0


def test_an_origin_that_is_not_a_session_is_refused() -> None:
    calendar, sessions = _sessions()
    saturday = dt.date(2024, 3, 2)
    with pytest.raises(ValueError, match="origin 2024-03-02 is not a session"):
        _window_of(_bars_panel(sessions, []), calendar, [saturday], 3)


def test_a_holding_that_runs_past_the_seal_is_refused_and_not_shortened() -> None:
    """A clamp would quietly shorten the window the disclosure is read against."""
    calendar, sessions = _sessions()
    with pytest.raises(ValueError, match="the holding from 2024-03-28 runs past 2024-03-29"):
        cli.distribution_window(
            _bars_panel(sessions, []),
            "SPY",
            calendar,
            [dt.date(2024, 3, 28)],
            horizon=3,
            end=dt.date(2024, 3, 29),
        )


def test_a_session_in_a_holding_with_no_bar_is_refused_and_not_read_as_no_ex_date() -> None:
    calendar, sessions = _sessions()
    panel = _bars_panel(sessions, [])
    panel = panel[~((panel["symbol"] == "SPY") & (panel["as_of"] == sessions[2]))]
    with pytest.raises(ValueError, match="session 2024-03-05 in the holding from 2024-03-01"):
        _window_of(panel, calendar, [sessions[0]], 3)


# -- whether the flags could be known at all ----------------------------------------------------


def _store(settings: Settings) -> SyntheticStore:
    return SyntheticStore(root=settings.storage_root, settings=settings)


def test_the_flags_are_known_only_where_the_dividend_source_holds_the_symbol(
    tmp_path: Path,
) -> None:
    """SPY and IWM have no dividend source, so their flags are False by absence and a count of
    zero is not a finding. QQQ has one. This reads the raw tier the way the curation does."""
    settings = _settings_with_root(load_settings(), tmp_path)
    assert cli.distribution_flags_known(settings, "SPY") is False
    store = _store(settings)
    store.write("nasdaq_dividends", "QQQ", [dt.date(2024, 2, 20)], tier=Tier.RAW)
    assert cli.distribution_flags_known(settings, "QQQ") is True
    assert cli.distribution_flags_known(settings, "SPY") is False, "per symbol"


def test_a_dividend_row_after_dev_end_does_not_make_the_flags_known(tmp_path: Path) -> None:
    """The read stops at ``dev_end``: what the source holds after it is not evidence about the
    window. (It reads as unknown, which errs the safe way.)"""
    settings = _settings_with_root(load_settings(), tmp_path)
    after = settings.splits.dev_end + dt.timedelta(days=30)
    _store(settings).write("nasdaq_dividends", "QQQ", [after], tier=Tier.RAW)
    assert cli.distribution_flags_known(settings, "QQQ") is False


# ---------------------------------------------------------------------------
# The sink: the record's contracts, written to disk and read back by a strict parser.
# ---------------------------------------------------------------------------


def _decision(**changes: object) -> Decision:
    base = _decisions()[0]
    return Decision(**{**base.__dict__, **changes})  # type: ignore[arg-type]


def _written(tmp_path: Path, decision: Decision) -> dict:
    sink = JsonlSink(tmp_path)
    assert sink.append_decisions([decision], at=AT, sha="a" * 40, dirty=False) == 1
    (line,) = _lines(tmp_path / DECISION_LEDGER)
    return line


def test_the_decision_ledger_is_its_own_file_beside_the_trial_ledger() -> None:
    assert DECISION_LEDGER.as_posix() == "data/decisions.jsonl"
    assert DECISION_LEDGER != TRIAL_LEDGER


def test_a_decision_line_carries_the_brief_s_fields(tmp_path: Path) -> None:
    line = _written(tmp_path, _decisions()[0])
    assert line["at"] == AT.isoformat() and line["git_sha"] == "a" * 40
    assert line["git_dirty"] is False
    assert line["symbol"] == "SPY" and line["horizon"] == 5 and line["cohort"] == "toy"
    assert line["alpha"] == ALPHAS[0] and line["stride"] == 5 and line["origins"] == 10
    assert line["contestants"] == ["tight", "wide"]
    assert line["fz0"]["loss"] == f"fz0_a{ALPHAS[0]}" and line["fz0"]["origins"] == 10
    assert set(line["coverage"]) == {"tight", "wide"}
    assert set(line["vol_target"]) == {"tight", "wide"}
    assert line["coverage"]["tight"]["breaches"] == BREACHES[("tight", ALPHAS[0])]


@pytest.mark.parametrize("known", [True, False])
def test_a_decision_line_states_whether_its_ex_date_count_could_be_known(
    tmp_path: Path, known: bool
) -> None:
    """A count of zero with no source behind it must not read as "no ex-dates occurred"."""
    (decision, *_) = _decisions(affected=4, known=known)
    line = _written(tmp_path, decision)
    assert line["distribution_flags_known"] is known
    assert line["distribution_holdings_affected"] == 4
    assert line["distribution_window"] == 10


def test_the_sink_appends_and_never_rewrites(tmp_path: Path) -> None:
    sink = JsonlSink(tmp_path)
    sink.append_decisions(_decisions(), at=AT, sha="a" * 40, dirty=None)
    first = (tmp_path / DECISION_LEDGER).read_bytes()
    sink.append_decisions(_decisions(), at=AT, sha="a" * 40, dirty=None)
    assert (tmp_path / DECISION_LEDGER).read_bytes().startswith(first)
    assert len(_lines(tmp_path / DECISION_LEDGER)) == 2 * len(ALPHAS)
    assert _lines(tmp_path / DECISION_LEDGER)[0]["git_dirty"] is None


def test_the_sink_writes_trials_through_the_existing_ledger_writer(tmp_path: Path) -> None:
    trial = Trial(
        model="m",
        symbol="SPY",
        horizon=1,
        loss="mse",
        origins=3,
        value=0.5,
        dropped=0,
        requested=3,
        skipped=0,
        every=5,
        first_origin=dt.date(2024, 3, 1),
        last_origin=dt.date(2024, 3, 15),
        cohort="toy",
    )
    sink = JsonlSink(tmp_path)
    assert sink.append_trials([trial], at=AT, sha="b" * 40, dirty=True) == 1
    (line,) = _lines(tmp_path / TRIAL_LEDGER)
    assert line == {"at": AT.isoformat(), "git_sha": "b" * 40, "git_dirty": True, **trial.record()}
    assert not (tmp_path / DECISION_LEDGER).exists()


def test_a_value_that_escapes_the_record_cleaning_fails_loudly(tmp_path: Path) -> None:
    """``allow_nan=False``: a NaN that got past ``record`` is an error, not an unreadable line."""

    class Poisoned:
        def record(self) -> dict[str, object]:
            return {"origins": float("nan")}

    sink = JsonlSink(tmp_path)
    with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
        sink.append_decisions([Poisoned()], at=AT, sha="c" * 40, dirty=False)  # type: ignore[list-item]
    assert (tmp_path / DECISION_LEDGER).read_text(encoding="utf-8") == "", "no half-written line"


# -- contract 3: None is None, never 0.0 ---------------------------------------------------------


@pytest.mark.parametrize("field", ["realized_vol", "sharpe", "turnover"])
def test_an_undefined_vol_target_statistic_is_written_as_null_not_zero(
    tmp_path: Path, field: str
) -> None:
    defined = dict(mean_return=0.07, realized_vol=0.11, sharpe=0.6, turnover=0.2)
    result = VolTargetResult(**{**defined, field: None}, capped_origins=1, distribution_sessions=3)  # type: ignore[arg-type]
    line = _written(tmp_path, _decision(vol_target={"tight": result}))
    written = line["vol_target"]["tight"]
    assert written[field] is None
    # The others are untouched, so the null is not a blanket one.
    for name, value in defined.items():
        if name != field:
            assert written[name] == value
    assert written["capped_origins"] == 1 and written["distribution_sessions"] == 3


def test_a_non_finite_vol_target_statistic_is_written_as_null_too(tmp_path: Path) -> None:
    result = VolTargetResult(float("inf"), float("nan"), float("-inf"), 0.2, 0, 0)
    written = _written(tmp_path, _decision(vol_target={"tight": result}))["vol_target"]["tight"]
    assert written["mean_return"] is None
    assert written["realized_vol"] is None and written["sharpe"] is None
    assert written["turnover"] == 0.2


def test_a_run_with_one_origin_has_null_statistics_end_to_end() -> None:
    (decision, *_) = cli.decide(
        _hand_evaluation({"tight": [TIGHT], "wide": [WIDE]}),
        cohort="toy",
        returns={HAND_ORIGINS[0]: RETURNS[0]},
        distributions=_window([False]),
        distribution_flags_known=True,
        errors=NO_ERRORS,
    )
    assert decision.vol_target is not None
    for result in decision.vol_target.values():
        assert (result.realized_vol, result.sharpe, result.turnover) == (None, None, None)
        assert result.mean_return != 0.0


# -- contract 2: the reason travels beside the p-value -------------------------------------------

REASONS = [
    ("none", [False] * 12, "no breach in 12 origins"),
    ("all", [True] * 12, "every one of 12 origins breached"),
    ("first", [True] + [False] * 11, "the only breach is the first of 12 origins"),
    ("last", [False] * 11 + [True], "the only breach is the last of 12 origins"),
    ("all-but-first", [False] + [True] * 11, "every origin after the first breached"),
    ("all-but-last", [True] * 11 + [False], "every origin before the last breached"),
]


@pytest.mark.parametrize(("label", "hits", "why"), REASONS, ids=[r[0] for r in REASONS])
def test_a_withheld_christoffersen_p_is_written_with_its_reason_beside_it(
    tmp_path: Path, label: str, hits: list[bool], why: str
) -> None:
    result = coverage(hits, 0.05, horizon=1, stride=1)
    assert result.christoffersen_p is None and result.refusal is None, label
    written = _written(tmp_path, _decision(coverage={"tight": result}))["coverage"]["tight"]
    assert written["christoffersen_p"] is None
    assert why in written["christoffersen_withheld"]
    assert written["refusal"] is None, "the run was fine, so the refusal stays null"
    assert written["kupiec_p"] is not None, "Kupiec is informative on these sequences"
    keys = list(written)
    assert keys.index("christoffersen_withheld") == keys.index("christoffersen_p") + 1


def test_a_computed_christoffersen_p_has_no_withheld_reason(tmp_path: Path) -> None:
    hits = [False, True, False, False, True, True, False, False, False, True, False, False]
    result = coverage(hits, 0.25, horizon=1, stride=1)
    assert result.christoffersen_p is not None
    written = _written(tmp_path, _decision(coverage={"tight": result}))["coverage"]["tight"]
    assert written["christoffersen_p"] == result.christoffersen_p
    assert written["christoffersen_withheld"] is None


def test_a_refused_coverage_result_is_written_with_its_refusal_and_null_p_values(
    tmp_path: Path,
) -> None:
    result = coverage([False] * 12, 0.05, horizon=22, stride=5)
    written = _written(tmp_path, _decision(coverage={"tight": result}))["coverage"]["tight"]
    assert written["kupiec_p"] is None and written["christoffersen_p"] is None
    assert "stride 5 < horizon 22" in written["refusal"]
    assert written["christoffersen_withheld"] is None
    assert written["breaches"] == 0, "the counts are reported whether or not it refuses"


def test_an_empty_decision_is_written_with_a_reason_and_not_a_bare_null(tmp_path: Path) -> None:
    (decision, *_) = cli.decide(
        _hand_evaluation({"tight": [], "wide": []}),
        cohort="toy",
        returns={},
        distributions=_window([]),
        distribution_flags_known=True,
        errors=NO_ERRORS,
    )
    line = _written(tmp_path, decision)
    assert line["vol_target"] is None
    assert "no comparable origin" in line["vol_target_refusal"]
    assert line["origins"] == 0


# ---------------------------------------------------------------------------
# The entry point, end to end, over the toy panel and a bars store under tmp_path.
# ---------------------------------------------------------------------------


def _write_bars(settings: Settings, panel: pd.DataFrame) -> None:
    """The toy panel's SPY bars as curated ``bars_daily`` partitions, flag included.

    ``horizon_returns`` reads these through ``load_panel``, so the return side of the run is the
    real read path and not a patched function.
    """
    schema = pa.schema(
        [
            ("symbol", pa.string()),
            ("ts", pa.timestamp("us", tz="UTC")),
            ("as_of", pa.date32()),
            ("ingested_at", pa.timestamp("us", tz="UTC")),
            ("close", pa.float64()),
            ("has_distribution", pa.bool_()),
        ]
    )
    bars = panel[panel["symbol"] == "SPY"]
    for _, row in bars.iterrows():
        day = pd.Timestamp(row["as_of"]).date()
        frame = pd.DataFrame(
            {
                "symbol": ["SPY"],
                "ts": [dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC)],
                "as_of": [day],
                "ingested_at": [dt.datetime(2025, 1, 1, tzinfo=dt.UTC)],
                "close": [float(row["close"])],
                "has_distribution": [bool(row["has_distribution"])],
            }
        )
        path = curated_path(settings.storage_root, BARS_DATASET, "SPY", day)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(frame, schema=schema, preserve_index=False), path)


class _Run:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = tmp_path
        self.settings = _settings_with_root(load_settings(), tmp_path)
        self.panel, self.targets = _toy_panel_and_targets_by_horizon()
        _write_bars(self.settings, self.panel)
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(cli, "load_settings", lambda: self.settings)
        monkeypatch.setattr(cli, "_persisted", lambda *_: None)
        monkeypatch.setattr(
            cli, "load_for", lambda _s, _sym, horizon, **k: (self.panel, self.targets[horizon])
        )
        monkeypatch.setattr(cli, "COHORTS", {TOY.name: TOY})

    def write_dividends(self, symbol: str, *days: dt.date) -> None:
        """Give ``symbol`` a dividend source in the raw tier, so its flags can be known."""
        _store(self.settings).write("nasdaq_dividends", symbol, days, tier=Tier.RAW)

    def main(self, *argv: str) -> int:
        return int(cli.main(["run", "--cohort", TOY.name, *argv]))

    def lines(self, ledger: Path) -> list[dict]:
        path = self.root / ledger
        return _lines(path) if path.exists() else []


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Run:
    return _Run(tmp_path, monkeypatch)


def test_a_run_writes_one_decision_line_per_alpha_and_configuration(run: _Run, capsys) -> None:
    code = run.main("--symbol", "SPY", "--horizon", "1", "--horizon", "5", *WINDOW)
    printed = capsys.readouterr().out
    assert code == 0, printed
    lines = run.lines(DECISION_LEDGER)
    configurations = 2
    assert len(lines) == len(ALPHAS) * configurations
    assert {(line["horizon"], line["alpha"]) for line in lines} == {
        (horizon, alpha) for horizon in (1, 5) for alpha in ALPHAS
    }
    assert {line["cohort"] for line in lines} == {TOY.name}
    assert {line["stride"] for line in lines} == {5}, "the default stride is the design's"


def test_an_evaluator_run_writes_the_walk_forwards_trials_and_its_decisions_to_decisions_only(
    run: _Run, capsys
) -> None:
    """R139, as the evaluator layer can honestly honour it.

    The trials are the walk-forward's -- ``run_configuration`` appends them per configuration and
    they are real -- so the ledger *grows*, by exactly losses x contestants x configurations. What
    the evaluator layer must not do is add lines of its own or write a second record shape there.
    """
    before = run.lines(TRIAL_LEDGER)
    assert before == []
    run.main("--symbol", "SPY", "--horizon", "1", "--horizon", "5", *WINDOW)
    capsys.readouterr()
    configurations = 2
    trials = run.lines(TRIAL_LEDGER)
    assert len(trials) == len(LOSSES) * len(TOY.models) * configurations
    assert count_trials(run.root) == len(trials)

    # Every line is a trial of the walk-forward's own shape and no decision is among them.
    sample = Trial(
        model="m",
        symbol="SPY",
        horizon=1,
        loss="mse",
        origins=0,
        value=0.0,
        dropped=0,
        requested=0,
        skipped=0,
        every=1,
        first_origin=dt.date.min,
        last_origin=dt.date.min,
    )
    shape = {"at", "git_sha", "git_dirty", *sample.record()}
    assert all(set(line) == shape for line in trials)
    assert {line["loss"] for line in trials} == set(LOSSES)
    assert not any(str(line["loss"]).startswith("fz0") for line in trials), "no decision loss"
    assert not any("alpha" in line or "coverage" in line for line in trials)

    # And every decision went to its own file.
    decisions = run.lines(DECISION_LEDGER)
    assert len(decisions) == len(ALPHAS) * configurations
    assert all("model" not in line and "loss" not in line for line in decisions)
    assert sorted(path.name for path in (run.root / "data").glob("*.jsonl")) == [
        "decisions.jsonl",
        "trials.jsonl",
    ]


def test_trials_and_decisions_from_one_run_share_a_join_key(run: _Run, capsys) -> None:
    run.main("--symbol", "SPY", "--horizon", "1", "--horizon", "5", *WINDOW)
    capsys.readouterr()

    def key(line: dict) -> tuple:
        return tuple(line[k] for k in ("git_sha", "at", "symbol", "horizon", "cohort"))

    trials, decisions = run.lines(TRIAL_LEDGER), run.lines(DECISION_LEDGER)
    assert {key(line) for line in decisions} == {key(line) for line in trials}
    assert len({line["at"] for line in trials + decisions}) == 1, "one timestamp for the run"


def test_every_decision_line_names_the_tree_that_produced_it(run: _Run, capsys) -> None:
    calls: list[object] = []

    def fake_state(*scope: object) -> tuple[str, bool | None]:
        calls.append(scope)
        return "f" * 40, True

    run.monkeypatch.setattr(cli, "git_state", fake_state)
    assert run.main("--symbol", "SPY", "--horizon", "1", *WINDOW) == 0
    capsys.readouterr()
    assert len(calls) == 1, "the state is read once, at the start of the run"
    for ledger in (TRIAL_LEDGER, DECISION_LEDGER):
        lines = run.lines(ledger)
        assert lines, ledger
        assert {line["git_sha"] for line in lines} == {"f" * 40}
        assert {line["git_dirty"] for line in lines} == {True}


def test_the_driver_is_called_rather_than_reimplemented(run: _Run, capsys) -> None:
    import volab.walkforward as wf

    calls: list[str] = []
    original = wf.run_configuration

    def spy(*args: object, **kwargs: object) -> wf.ConfigurationOutcome:
        calls.append("run_configuration")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    run.monkeypatch.setattr(cli, "run_configuration", spy)
    assert run.main("--symbol", "SPY", "--horizon", "1", *WINDOW) == 0
    capsys.readouterr()
    assert calls == ["run_configuration"]


def test_an_audit_violation_exits_non_zero_and_writes_no_decision(run: _Run, capsys) -> None:
    import volab.walkforward as wf

    run.monkeypatch.setattr(wf, "knowable_targets", lambda frame, origin: frame)
    code = run.main("--symbol", "SPY", "--horizon", "1", "--every", "2", *WINDOW)
    printed = capsys.readouterr().out
    assert code == 2, printed
    assert "REFUSED" in printed and run.lines(DECISION_LEDGER) == []


def test_the_entry_point_refuses_a_to_past_dev_end_and_has_no_unlock_flag(
    run: _Run, capsys
) -> None:
    past = run.settings.splits.dev_end + dt.timedelta(days=1)
    assert run.main("--to", past.isoformat()) == 1
    printed = capsys.readouterr().out
    assert "past dev_end" in printed and "sealed" in printed
    assert not (run.root / "data" / "decisions.jsonl").exists()
    for argv in (["--end", "2026-06-30"], ["--allow-holdout"], ["--unlock"], ["--holdout"]):
        with pytest.raises(SystemExit) as refused:
            cli.main(["run", "--cohort", TOY.name, *argv])
        assert refused.value.code == 2


# -- the numbers a real run produces --------------------------------------------------------------


def _window_ex_dates(run: _Run, origins: Sequence[dt.date], horizon: int) -> tuple[int, int, int]:
    """``(flagged, sessions, affected)`` over the holdings ``(t, t + horizon]``, derived without
    ``distribution_window`` from the bars the test wrote."""
    calendar = get_calendar(run.settings)
    sessions = sessions_between(calendar, origins[0], run.settings.splits.dev_end)
    flags = {
        pd.Timestamp(r["as_of"]).date(): bool(r["has_distribution"])
        for _, r in run.panel[run.panel["symbol"] == "SPY"].iterrows()
    }
    holdings = [{sessions[sessions.index(t) + k] for k in range(1, horizon + 1)} for t in origins]
    covered = set().union(*holdings)
    flagged = sum(flags[day] for day in covered)
    affected = sum(any(flags[day] for day in holding) for holding in holdings)
    return flagged, len(covered), affected


def test_the_ex_date_count_beside_a_vol_target_is_the_stores_flag_over_the_holdings(
    run: _Run, capsys
) -> None:
    """Spec 7.4. The count is read from the store's ``has_distribution`` and is the count over the
    window the P&L spans -- not zero (nothing supplied), not every flagged session in the file."""
    assert run.main("--symbol", "SPY", "--horizon", "5", *WINDOW) == 0
    capsys.readouterr()
    lines = run.lines(DECISION_LEDGER)
    flagged_overall = int(run.panel[run.panel["symbol"] == "SPY"]["has_distribution"].sum())
    for line in lines:
        origins = _used_origins(run, line["horizon"])
        flagged, covered, affected = _window_ex_dates(run, origins, line["horizon"])
        assert flagged > 0, "the window must contain an ex-date or this witnesses nothing"
        assert flagged < flagged_overall, "and the file must hold ex-dates outside it"
        assert 0 < affected < len(origins), "some holdings are affected and some are not"
        assert line["distribution_window"] == covered
        assert line["distribution_holdings_affected"] == affected
        counts = {r["distribution_sessions"] for r in line["vol_target"].values()}
        assert counts == {flagged}
        # At h=5 and stride 5 the holdings do not overlap, and the toy flags are one to a holding.
        assert affected == flagged


def test_overlapping_holdings_are_counted_once_each_in_a_real_run(run: _Run, capsys) -> None:
    """At h=5 and stride 3 each holding shares sessions with the next: the two counts can differ,
    and the line must carry the affected holdings, which is the larger measure of the damage."""
    assert run.main("--symbol", "SPY", "--horizon", "5", "--every", "3", *WINDOW) == 0
    capsys.readouterr()
    for line in run.lines(DECISION_LEDGER):
        origins = _used_origins(run, line["horizon"], every=3)
        flagged, _, affected = _window_ex_dates(run, origins, line["horizon"])
        assert line["distribution_holdings_affected"] == affected
        assert affected > flagged, "overlap must make the holdings count the larger"


def test_a_symbol_with_no_dividend_source_is_recorded_as_unknown_not_as_none(
    run: _Run, capsys
) -> None:
    assert run.main("--symbol", "SPY", "--horizon", "5", *WINDOW) == 0
    printed = capsys.readouterr().out
    assert {line["distribution_flags_known"] for line in run.lines(DECISION_LEDGER)} == {False}
    assert "NOT KNOWN" in printed


def test_a_symbol_with_a_dividend_source_is_recorded_as_known(run: _Run, capsys) -> None:
    run.write_dividends("SPY", dt.date(2024, 3, 15))
    assert run.main("--symbol", "SPY", "--horizon", "5", *WINDOW) == 0
    printed = capsys.readouterr().out
    assert {line["distribution_flags_known"] for line in run.lines(DECISION_LEDGER)} == {True}
    assert "NOT KNOWN" not in printed


def _used_origins(run: _Run, horizon: int, every: int = 5) -> list[dt.date]:
    """The origins of the decision series, i.e. the comparable QLIKE origins of the run."""
    import volab.walkforward as wf
    from volab.walkforward import evaluate

    calendar = get_calendar(run.settings)
    origins = wf.scoreable_origins(
        run.settings,
        run.targets[horizon],
        first=dt.date(2024, 3, 1),
        last=dt.date(2024, 6, 28),
        every=every,
    )
    evaluation = evaluate(
        run.panel,
        run.targets[horizon],
        origins,
        symbol="SPY",
        horizon=horizon,
        calendar=calendar,
        minimum_training=20,
        every=every,
        audit=wf.RunAudit(),
        cohort=TOY,
    )
    usable = evaluation.comparable("qlike")
    from volab.evaluators import horizon_returns

    returns = horizon_returns(run.settings, "SPY", sorted(usable), horizon=horizon)
    # The invariant ``decision_series`` relies on, as a witness and not a defensive intersection:
    # a target needs the closes at ``t`` and ``t + h``, so every comparable origin has a return.
    assert usable <= returns.keys()
    return sorted(usable)


def test_a_run_writes_strict_json_with_the_p_values_and_their_reasons(run: _Run, capsys) -> None:
    assert run.main("--symbol", "SPY", "--horizon", "1", *WINDOW) == 0
    capsys.readouterr()
    lines = run.lines(DECISION_LEDGER)  # parsed strictly: a NaN anywhere fails here
    withheld = [
        result
        for line in lines
        for result in line["coverage"].values()
        if result["christoffersen_p"] is None and result["refusal"] is None
    ]
    assert withheld, "the toy run must reach a withheld Christoffersen p or this witnesses nothing"
    assert all(isinstance(result["christoffersen_withheld"], str) for result in withheld)
    # Where the p-value is there, no reason is.
    present = [
        r for line in lines for r in line["coverage"].values() if r["christoffersen_p"] is not None
    ]
    assert all(r["christoffersen_withheld"] is None for r in present)


def test_coverage_at_a_short_stride_is_refused_in_the_written_line(run: _Run, capsys) -> None:
    assert run.main("--symbol", "SPY", "--horizon", "5", "--every", "4", *WINDOW) == 0
    capsys.readouterr()
    for line in run.lines(DECISION_LEDGER):
        assert line["stride"] == 4
        for result in line["coverage"].values():
            assert "stride 4 < horizon 5" in result["refusal"]


def test_a_single_origin_run_writes_null_statistics_not_zeros(run: _Run, capsys) -> None:
    window = ["--from", "2024-06-04", "--to", "2024-06-04", "--minimum-training", "20"]
    assert run.main("--symbol", "SPY", "--horizon", "1", *window) == 0
    capsys.readouterr()
    lines = run.lines(DECISION_LEDGER)
    assert {line["origins"] for line in lines} == {1}
    for line in lines:
        for result in line["vol_target"].values():
            assert result["realized_vol"] is None
            assert result["sharpe"] is None and result["turnover"] is None


def test_a_configuration_with_nothing_to_score_is_decided_without_raising(
    run: _Run, capsys
) -> None:
    """Past the last target, no origin is scoreable: ``vol_target([], [])`` would raise."""
    window = ["--from", "2024-09-02", "--to", "2024-09-30", "--minimum-training", "20"]
    assert run.main("--symbol", "SPY", "--horizon", "1", *window) == 0
    capsys.readouterr()
    lines = run.lines(DECISION_LEDGER)
    assert len(lines) == len(ALPHAS)
    for line in lines:
        assert line["origins"] == 0 and line["vol_target"] is None
        assert "no comparable origin" in line["vol_target_refusal"]
    assert len(run.lines(TRIAL_LEDGER)) == len(LOSSES) * len(TOY.models), "still counted"


# -- the restated feature-table read -----------------------------------------------------------


def test_persisted_returns_the_stored_table_in_order_and_stops_at_dev_end(tmp_path: Path) -> None:
    """``_persisted`` is restated from ``volab.compare.__main__`` and stubbed in every run above,
    so it is tested here against a real store. The bound at ``dev_end`` is what this pins. Its
    ``sort_values("as_of")`` is not witnessed, and that mutant is equivalent for a narrower reason
    than "nothing could witness it" (``SyntheticStore.write(ts=...)`` can make ``ts`` and
    ``as_of`` disagree): ``load_panel`` orders by ``(symbol, ts)`` and the sort is by ``as_of``,
    and the two coincide only because ``features/build.py::_require_ascending_sessions`` refuses a
    panel whose ``ts`` or ``as_of`` is not ascending, so a table it built has both in one order."""
    settings = _settings_with_root(load_settings(), tmp_path)
    store = _store(settings)
    dev_end = settings.splits.dev_end
    days = [dev_end - dt.timedelta(days=3), dev_end - dt.timedelta(days=1)]
    sealed = dev_end + dt.timedelta(days=10)
    store.write(cli.FEATURE_SET, "SPY", [days[1], days[0], sealed], tier=Tier.FEATURES)
    frame = cli._persisted(settings, "SPY")
    assert frame is not None
    assert [d.date() for d in pd.to_datetime(frame["as_of"])] == days, "nothing sealed"
    assert list(frame.index) == [0, 1]


def test_persisted_is_none_where_the_store_holds_no_table(tmp_path: Path) -> None:
    settings = _settings_with_root(load_settings(), tmp_path)
    assert cli._persisted(settings, "SPY") is None
    _store(settings).write(cli.FEATURE_SET, "QQQ", [dt.date(2024, 3, 1)], tier=Tier.FEATURES)
    assert cli._persisted(settings, "SPY") is None, "per symbol"


# -- the refusal ----------------------------------------------------------------------------------


def test_the_cli_refuses_the_phase3_cohort_at_h66(
    capsys, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``Cohort.refusal`` printed, exit 1, before any work -- and the same string ``evaluate``
    raises, because it is the same function."""

    def unreachable(*_: object, **__: object) -> None:
        raise AssertionError("the refusal came after work had started")

    monkeypatch.setattr(cli, "load_for", unreachable)
    monkeypatch.setattr(cli, "git_state", unreachable)
    code = cli.main(["run", "--cohort", PHASE3_IV.name, "--horizon", "66", "--symbol", "SPY"])
    printed = capsys.readouterr().out
    reason = PHASE3_IV.refusal(66)
    assert code == 1, printed
    assert reason is not None and reason in printed
    assert "har_rv_iv" in printed and "h<=22" in printed
    assert "git " not in printed and "storage root" not in printed


def test_the_cohort_must_be_named() -> None:
    """Phase 3's recorded run is held (R139): a bare command must not pick a cohort and append."""
    with pytest.raises(SystemExit) as refused:
        cli.main(["run"])
    assert refused.value.code == 2


def test_the_cli_refuses_one_unanswerable_horizon_among_several(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable(*_: object, **__: object) -> None:
        raise AssertionError("the refusal came after work had started")

    monkeypatch.setattr(cli, "load_for", unreachable)
    monkeypatch.setattr(cli, "git_state", unreachable)
    code = cli.main(
        ["run", "--cohort", PHASE3_IV.name, "--horizon", "5", "--horizon", "66", "--symbol", "SPY"]
    )
    assert code == 1 and "h=66" in capsys.readouterr().out


def test_the_default_horizons_are_the_cohorts_own_and_the_run_says_what_it_left_out(
    capsys, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bare ``run`` over the Phase 3 cohort visits h=1, 5 and 22 and does not walk into the h=66
    refusal. The driver is stubbed to an empty configuration, so nothing is fitted or read."""
    from volab.walkforward import ConfigurationOutcome

    settings = _settings_with_root(load_settings(), tmp_path)
    visited: list[int] = []

    def empty(_settings: object, _symbol: str, horizon: int, *_: object, **__: object):
        visited.append(horizon)
        return ConfigurationOutcome(None, (), 0, refused=False, disagrees=False)

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_for", lambda *_, **__: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(cli, "run_configuration", empty)
    assert cli.main(["run", "--cohort", PHASE3_IV.name, "--symbol", "SPY"]) == 0
    printed = capsys.readouterr().out
    assert visited == [1, 5, 22]
    assert PHASE3_IV.refusal(66) in printed, "it must say why 66 was left out"
    assert len(_lines(tmp_path / DECISION_LEDGER)) == 3 * len(ALPHAS)
