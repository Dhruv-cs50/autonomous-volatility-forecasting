"""The dated feature table: trailing windows only, and a cutoff separate from the panel's extent.

``build_features`` is handed an already-loaded panel that **may extend past** ``through``, which
is the whole point: the store's own filter cannot protect a caller who legitimately holds future
rows -- Phase 2's walk-forward loads the dev panel once and then evaluates at many earlier
cutoffs -- so the honesty has to live in the feature code and be tested where the future is
actually in front of it. Two tests here do exactly that: one grows the panel, one perturbs it
past the cutoff. Sprint 5 owns the byte-identity version of the same property.

Expected values are written out by hand wherever a value is asserted. ``RETURNS`` is
``0.001 * (k + 1)``, negated every third session, so every squared return is distinct, both
signs appear, and a window shifted by a single session changes the answer.
"""

from __future__ import annotations

import ast
import datetime as dt
import math
import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd
import pytest

import volab
import volab.features.build as build_module
from volab.config import load_settings
from volab.errors import SchemaError
from volab.features.build import COLUMNS, EXOGENOUS_SERIES, build_features, log_returns
from volab.ingest._frames import session_close_utc
from volab.sessions import sessions_between

# Built once: constructing XNYS is slow enough that per-test construction shows up in the suite.
CAL = xcals.get_calendar("XNYS")
INGESTED = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)

# A full year, so a 66-session window has room to become complete and to stay null before it.
SESSIONS = sessions_between(CAL, dt.date(2023, 1, 3), dt.date(2023, 12, 29))
RETURNS = [0.001 * (k + 1) * (-1 if k % 3 == 2 else 1) for k in range(len(SESSIONS) - 1)]

# VIX1D's first publication. Before it the column is legitimately null, not broken.
VIX_FAMILY: tuple[str, ...] = ("VIX", "VIX1D", "VIX9D", "VVIX")

INCEPTION = dt.date(2022, 5, 13)
MAY_2022 = sessions_between(CAL, dt.date(2022, 5, 2), dt.date(2022, 5, 20))

SPEC_COLUMNS = [
    "symbol",
    "ts",
    "as_of",
    "ingested_at",
    "rv_proxy_d1",
    "semivar_neg_proxy_d1",
    "semivar_pos_proxy_d1",
    "signed_jump_proxy_d1",
    "rv_proxy_d5",
    "semivar_neg_proxy_d5",
    "semivar_pos_proxy_d5",
    "signed_jump_proxy_d5",
    "bipower_proxy_d5",
    "rv_proxy_d22",
    "semivar_neg_proxy_d22",
    "semivar_pos_proxy_d22",
    "signed_jump_proxy_d22",
    "bipower_proxy_d22",
    "rv_proxy_d66",
    "semivar_neg_proxy_d66",
    "semivar_pos_proxy_d66",
    "signed_jump_proxy_d66",
    "bipower_proxy_d66",
    "har_d_proxy",
    "har_w_proxy",
    "har_m_proxy",
    "iv_h1",
    "iv_h5",
    "iv_h22",
    "iv_var_h1",
    "iv_var_h5",
    "iv_var_h22",
    "vvix",
    "baa10y",
    "dgs10",
    "t10y2y",
    "has_distribution",
]


def _closes(returns: Sequence[float], *, first: float = 100.0) -> list[float]:
    """A close path whose consecutive log ratios are exactly ``returns``."""
    closes = [first]
    for value in returns:
        closes.append(closes[-1] * math.exp(value))
    return closes


CLOSES = _closes(RETURNS)


def _stamps(days: Sequence[dt.date]) -> pd.Series:
    return pd.Series(
        pd.to_datetime([dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC) for d in days], utc=True)
    )


def _bars(
    days: Sequence[dt.date],
    closes: Sequence[float],
    *,
    symbol: str = "SPY",
    distributions: Iterable[dt.date] = (),
) -> pd.DataFrame:
    """A curated-bars-shaped frame: raw OHLCV, the distribution flag, and no return column."""
    marked = set(distributions)
    return pd.DataFrame(
        {
            "symbol": [symbol] * len(days),
            "ts": _stamps(days),
            "as_of": list(days),
            "ingested_at": _stamps([INGESTED.date()] * len(days)),
            "open": list(closes),
            "high": list(closes),
            "low": list(closes),
            "close": list(closes),
            "volume": [1_000_000.0] * len(days),
            "has_distribution": [day in marked for day in days],
        }
    )


def _exogenous(
    series: str, days: Sequence[dt.date], values: Sequence[float], *, column: str = "close"
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [series] * len(days),
            "ts": _stamps(days),
            "as_of": list(days),
            "ingested_at": _stamps([INGESTED.date()] * len(days)),
            column: list(values),
        }
    )


def _panel(*frames: pd.DataFrame) -> pd.DataFrame:
    """The frames as ``load_panel`` would hand them over: concatenated, sorted by symbol then ts."""
    joined = pd.concat(frames, ignore_index=True)
    return joined.sort_values(["symbol", "ts"]).reset_index(drop=True)


def _vix_family(days: Sequence[dt.date]) -> list[pd.DataFrame]:
    """The three horizon-matched series plus VVIX, each with its own distinguishable level."""
    return [
        _exogenous("VIX1D", days, [10.0 + k for k in range(len(days))]),
        _exogenous("VIX9D", days, [20.0 + k for k in range(len(days))]),
        _exogenous("VIX", days, [30.0 + k for k in range(len(days))]),
        _exogenous("VVIX", days, [90.0 + k for k in range(len(days))]),
    ]


def _fred(days: Sequence[dt.date]) -> list[pd.DataFrame]:
    return [
        _exogenous("BAA10Y", days, [1.5] * len(days), column="value"),
        _exogenous("DGS10", days, [4.0] * len(days), column="value"),
        _exogenous("T10Y2Y", days, [-0.5] * len(days), column="value"),
    ]


def _full_panel(days: Sequence[dt.date] = SESSIONS) -> pd.DataFrame:
    closes = CLOSES[: len(days)]
    return _panel(_bars(days, closes), *_vix_family(days), *_fred(days))


def _at(frame: pd.DataFrame, column: str, day: dt.date) -> float:
    """One value, looked up by session rather than by position."""
    row = frame[frame["as_of"] == day]
    assert len(row) == 1, f"expected one row for {day}, got {len(row)}"
    return float(row[column].iloc[0])


def _return_at(index: int) -> float:
    """The log return of session ``SESSIONS[index]``; session 0 has none."""
    return RETURNS[index - 1]


def test_build_features_emits_one_row_per_session_through_the_cutoff() -> None:
    through = SESSIONS[100]
    frame = build_features(_full_panel(), through=through, calendar=CAL)

    assert list(frame["as_of"]) == SESSIONS[:101]
    assert set(frame["symbol"]) == {"SPY"}


def test_the_panel_may_extend_well_past_the_cutoff() -> None:
    through = SESSIONS[100]
    frame = build_features(_full_panel(), through=through, calendar=CAL)

    # The panel carried 249 sessions; the table stops at the cutoff and says so in ``as_of``.
    assert max(frame["as_of"]) == through
    assert len(SESSIONS) > 200


def test_build_features_columns_match_the_spec() -> None:
    frame = build_features(_full_panel(), through=SESSIONS[100], calendar=CAL)

    assert list(frame.columns) == SPEC_COLUMNS
    assert list(COLUMNS) == SPEC_COLUMNS
    # The panel's raw OHLCV is consumed, not republished.
    assert not {"open", "high", "low", "volume", "close", "value"} & set(frame.columns)


def test_proxy_columns_are_named_proxy() -> None:
    frame = build_features(_full_panel(), through=SESSIONS[100], calendar=CAL)

    daily = [c for c in frame.columns if "semivar" in c or "bipower" in c or c.startswith("har_")]
    assert daily, "expected the daily-return estimators to be present"
    assert all("_proxy" in c for c in daily)
    assert {"har_d", "har_w", "har_m"}.isdisjoint(frame.columns)


def test_bipower_has_no_one_session_column() -> None:
    frame = build_features(_full_panel(), through=SESSIONS[100], calendar=CAL)

    # A single session holds no adjacent pair, so the column would be entirely null.
    assert "bipower_proxy_d1" not in frame.columns


def test_incomplete_windows_are_null_not_dropped() -> None:
    frame = build_features(_full_panel(), through=SESSIONS[100], calendar=CAL)

    # Session 0 has no return at all, so a 66-session window is full only at session 66.
    assert frame["rv_proxy_d66"].iloc[:66].isna().all()
    assert frame["rv_proxy_d66"].iloc[66:].notna().all()
    assert frame["bipower_proxy_d66"].iloc[:66].isna().all()
    assert len(frame) == 101


def test_a_missing_session_does_not_shorten_a_window() -> None:
    gap = SESSIONS[10]
    kept = [day for day in SESSIONS if day != gap]
    closes = [CLOSES[k] for k, day in enumerate(SESSIONS) if day != gap]
    frame = build_features(
        _panel(_bars(kept, closes), *_vix_family(SESSIONS), *_fred(SESSIONS)),
        through=SESSIONS[30],
        calendar=CAL,
    )

    # The gap is a session of the table, with null features rather than no row.
    assert list(frame["as_of"]) == SESSIONS[:31]
    assert pd.isna(_at(frame, "rv_proxy_d5", gap))
    # Not a null ts: see test_gap_sessions_carry_a_distinct_ascending_timestamp.
    assert frame.loc[frame["as_of"] == gap, "ts"].iloc[0] == pd.Timestamp(session_close_utc(gap))

    # A 5-session window ending at session 14 spans the gap. Counting *rows* there are still
    # five of them to reach back over (sessions 9, 11, 12, 13, 14), which is exactly the silent
    # shortening this guards: the window stays null until the gap and the return across it fall
    # out of it, at session 16.
    for index in range(10, 16):
        assert pd.isna(_at(frame, "rv_proxy_d5", SESSIONS[index])), index
    assert not pd.isna(_at(frame, "rv_proxy_d5", SESSIONS[16]))


def test_iv_columns_are_null_before_vix1d_inception() -> None:
    closes = _closes(RETURNS[: len(MAY_2022) - 1])
    published = [day for day in MAY_2022 if day >= INCEPTION]
    frame = build_features(
        _panel(
            _bars(MAY_2022, closes),
            _exogenous("VIX1D", published, [11.0 + k for k in range(len(published))]),
            _exogenous("VIX9D", MAY_2022, [20.0 + k for k in range(len(MAY_2022))]),
        ),
        through=MAY_2022[-1],
        calendar=CAL,
    )

    before = frame[frame["as_of"] < INCEPTION]
    after = frame[frame["as_of"] >= INCEPTION]
    assert not before.empty and not after.empty
    assert before["iv_h1"].isna().all()
    assert before["iv_var_h1"].isna().all()
    assert after["iv_h1"].notna().all()
    # The series that does exist over the whole span is unaffected by the one that does not.
    assert frame["iv_h5"].notna().all()


def test_fred_columns_are_null_when_the_series_is_absent() -> None:
    # FRED is down as of this writing, so the panel carries no ``value`` column at all.
    panel = _panel(_bars(SESSIONS, CLOSES), *_vix_family(SESSIONS))
    assert "value" not in panel.columns

    frame = build_features(panel, through=SESSIONS[100], calendar=CAL)

    for column in ("baa10y", "dgs10", "t10y2y"):
        assert column in frame.columns
        assert frame[column].isna().all()


def test_build_features_never_calls_load_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    import volab.store as store

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("build_features must not read the store")

    monkeypatch.setattr(store, "load_panel", refuse)
    monkeypatch.setattr(build_module, "load_panel", refuse, raising=False)

    frame = build_features(_full_panel(), through=SESSIONS[100], calendar=CAL)

    assert len(frame) == 101


def test_the_build_module_never_names_load_panel() -> None:
    """Structural, because a monkeypatch only covers the bindings a test thought to patch."""
    tree = ast.parse(Path(build_module.__file__).read_text(encoding="utf-8"))
    named = [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "load_panel")
        or (isinstance(node, ast.Attribute) and node.attr == "load_panel")
        or (isinstance(node, ast.alias) and node.name == "load_panel")
    ]

    assert not named, "only the CLI may touch the store; the engine takes a panel"


def test_growing_the_panel_does_not_change_the_features() -> None:
    through = SESSIONS[100]
    short = SESSIONS[:101]
    narrow = build_features(_full_panel(short), through=through, calendar=CAL)
    wide = build_features(_full_panel(), through=through, calendar=CAL)

    pd.testing.assert_frame_equal(narrow, wide)


# Every group of values the engine reads. One test per group, because a perturbation that
# misses an input entirely misses every forward peek in it -- the same reasoning that showed a
# scale-only perturbation cannot catch a peek in a sign-sensitive column.
INPUT_GROUPS: tuple[str, ...] = ("close", "vix", "value", "has_distribution")


def _perturb(panel: pd.DataFrame, group: str, through: dt.date) -> pd.DataFrame:
    """Rewrite one group of the panel's values, after ``through`` and nowhere else."""
    future = panel["as_of"] > through
    assert int(future.sum()) > 100, "the perturbation must have rows to act on"
    perturbed = panel.copy()
    if group == "close":
        subject = future & (panel["symbol"] == "SPY")
        # A decaying path, so every future return is large and **negative**. Scaling the closes
        # is not enough on its own: the sign-sensitive columns would survive it unchanged, and a
        # forward peek into one of those is exactly the defect these tests exist to catch.
        perturbed.loc[subject, "close"] = [0.5**k for k in range(int(subject.sum()))]
    elif group == "vix":
        family = future & panel["symbol"].isin(VIX_FAMILY)
        perturbed.loc[family, "close"] = 999.0
    elif group == "value":
        perturbed.loc[future, "value"] = 99.0
    elif group == "has_distribution":
        perturbed.loc[future, "has_distribution"] = True
    else:
        raise AssertionError(f"unknown input group {group!r}")
    # A perturbation that silently no-ops would make its test pass for the wrong reason.
    changed = [
        column
        for column in ("close", "value", "has_distribution")
        if not perturbed[column].equals(panel[column])
    ]
    assert changed, f"perturbing {group} changed nothing"
    return perturbed


@pytest.mark.parametrize("group", INPUT_GROUPS)
def test_perturbing_one_input_group_after_the_cutoff_leaves_features_unchanged(group: str) -> None:
    through = SESSIONS[100]
    panel = _full_panel()
    before = build_features(panel, through=through, calendar=CAL)

    after = build_features(_perturb(panel, group, through), through=through, calendar=CAL)

    pd.testing.assert_frame_equal(before, after)


def test_perturbing_every_input_group_at_once_leaves_features_unchanged() -> None:
    through = SESSIONS[100]
    panel = _full_panel()
    before = build_features(panel, through=through, calendar=CAL)

    perturbed = panel
    for group in INPUT_GROUPS:
        perturbed = _perturb(perturbed, group, through)

    pd.testing.assert_frame_equal(before, build_features(perturbed, through=through, calendar=CAL))


def test_deleting_and_appending_sessions_after_the_cutoff_leaves_features_unchanged() -> None:
    through = SESSIONS[100]
    before = build_features(_full_panel(), through=through, calendar=CAL)
    trimmed = _full_panel(SESSIONS[:150])
    extra = sessions_between(CAL, dt.date(2024, 1, 2), dt.date(2024, 2, 15))
    grown = _panel(
        _full_panel(),
        _bars(extra, [5.0] * len(extra)),
        *_vix_family(extra),
    )

    pd.testing.assert_frame_equal(before, build_features(trimmed, through=through, calendar=CAL))
    pd.testing.assert_frame_equal(before, build_features(grown, through=through, calendar=CAL))


def test_rv_proxy_is_the_hand_computed_trailing_sum() -> None:
    frame = build_features(_full_panel(), through=SESSIONS[100], calendar=CAL)
    day = SESSIONS[10]

    assert _at(frame, "rv_proxy_d1", day) == pytest.approx(_return_at(10) ** 2)
    assert _at(frame, "rv_proxy_d5", day) == pytest.approx(
        sum(_return_at(k) ** 2 for k in range(6, 11))
    )
    # A 22-session window at session 10 reaches before the panel's first session.
    assert pd.isna(_at(frame, "rv_proxy_d22", day))


def test_semivariance_proxies_split_the_window_by_sign() -> None:
    frame = build_features(_full_panel(), through=SESSIONS[100], calendar=CAL)
    day = SESSIONS[10]
    window = [_return_at(k) for k in range(6, 11)]

    negative = sum(r**2 for r in window if r < 0)
    positive = sum(r**2 for r in window if r > 0)
    assert negative > 0 and positive > 0, "the window must hold both signs to be a real test"
    assert _at(frame, "semivar_neg_proxy_d5", day) == pytest.approx(negative)
    assert _at(frame, "semivar_pos_proxy_d5", day) == pytest.approx(positive)
    assert _at(frame, "signed_jump_proxy_d5", day) == pytest.approx(positive - negative)
    assert _at(frame, "rv_proxy_d5", day) == pytest.approx(positive + negative)


def test_bipower_proxy_is_the_hand_computed_pair_sum() -> None:
    frame = build_features(_full_panel(), through=SESSIONS[100], calendar=CAL)
    day = SESSIONS[10]

    pairs = sum(abs(_return_at(k)) * abs(_return_at(k - 1)) for k in range(7, 11))
    assert _at(frame, "bipower_proxy_d5", day) == pytest.approx((math.pi / 2) * pairs)


def test_har_columns_are_the_trailing_means_of_the_rv_proxy() -> None:
    frame = build_features(_full_panel(), through=SESSIONS[100], calendar=CAL)
    day = SESSIONS[30]

    assert _at(frame, "har_d_proxy", day) == pytest.approx(_return_at(30) ** 2)
    assert _at(frame, "har_w_proxy", day) == pytest.approx(_at(frame, "rv_proxy_d5", day) / 5)
    assert _at(frame, "har_m_proxy", day) == pytest.approx(_at(frame, "rv_proxy_d22", day) / 22)


def test_iv_columns_take_the_horizon_matched_series_on_the_same_session() -> None:
    frame = build_features(_full_panel(), through=SESSIONS[100], calendar=CAL)
    index = 40
    day = SESSIONS[index]

    # _vix_family levels: VIX1D 10+k, VIX9D 20+k, VIX 30+k, VVIX 90+k. A one-session shift in
    # either direction moves every one of these by exactly one.
    assert _at(frame, "iv_h1", day) == pytest.approx(10.0 + index)
    assert _at(frame, "iv_h5", day) == pytest.approx(20.0 + index)
    assert _at(frame, "iv_h22", day) == pytest.approx(30.0 + index)
    assert _at(frame, "vvix", day) == pytest.approx(90.0 + index)


def test_iv_var_columns_are_the_horizon_scaled_variance() -> None:
    frame = build_features(_full_panel(), through=SESSIONS[100], calendar=CAL)
    index = 40
    day = SESSIONS[index]

    for horizon, level in ((1, 10.0), (5, 20.0), (22, 30.0)):
        close = level + index
        assert _at(frame, f"iv_var_h{horizon}", day) == pytest.approx(
            (close / 100) ** 2 * (horizon / 252)
        )


def test_fred_levels_are_passed_through_on_the_session_they_are_knowable() -> None:
    frame = build_features(_full_panel(), through=SESSIONS[100], calendar=CAL)
    day = SESSIONS[40]

    assert _at(frame, "baa10y", day) == pytest.approx(1.5)
    assert _at(frame, "dgs10", day) == pytest.approx(4.0)
    assert _at(frame, "t10y2y", day) == pytest.approx(-0.5)


def test_an_exogenous_gap_is_null_rather_than_carried_forward() -> None:
    gap = SESSIONS[40]
    days = [day for day in SESSIONS if day != gap]
    panel = _panel(
        _bars(SESSIONS, CLOSES),
        _exogenous("VIX", days, [30.0 + k for k in range(len(days))]),
    )
    frame = build_features(panel, through=SESSIONS[100], calendar=CAL)

    assert pd.isna(_at(frame, "iv_h22", gap))
    assert pd.isna(_at(frame, "iv_var_h22", gap))
    # The session before it is present, which is what a forward fill would have copied.
    assert not pd.isna(_at(frame, "iv_h22", SESSIONS[39]))


def test_has_distribution_is_passed_through() -> None:
    marked = SESSIONS[7]
    panel = _panel(_bars(SESSIONS, CLOSES, distributions=[marked]), *_vix_family(SESSIONS))
    frame = build_features(panel, through=SESSIONS[100], calendar=CAL)

    flags = dict(zip(frame["as_of"], frame["has_distribution"], strict=True))
    assert bool(flags[marked]) is True
    assert bool(flags[SESSIONS[8]]) is False
    assert sum(1 for value in flags.values() if bool(value)) == 1


def test_gap_sessions_carry_a_distinct_ascending_timestamp() -> None:
    """A null ``ts`` would not survive a read back through the store.

    ``load_panel`` dedupes on ``(symbol, ts)`` with ``keep="last"`` and pandas treats NaT as
    equal to NaT, so two gap rows carrying a null ``ts`` would collapse into one row and then
    sort after every real one -- losing both "one row per session" and ascending order in
    exactly the case the session index exists for. The stamp is the calendar's own session
    close, which is what the bars carry too.
    """
    gaps = [SESSIONS[10], SESSIONS[20]]
    kept = [day for day in SESSIONS if day not in gaps]
    closes = [CLOSES[k] for k, day in enumerate(SESSIONS) if day not in gaps]
    frame = build_features(_panel(_bars(kept, closes)), through=SESSIONS[30], calendar=CAL)

    assert frame["ts"].notna().all()
    assert frame["ts"].is_monotonic_increasing
    assert frame["ts"].is_unique
    for gap in gaps:
        row = frame[frame["as_of"] == gap]
        assert row["ts"].iloc[0] == pd.Timestamp(session_close_utc(gap))
        # Nothing was ingested for a bar that was never served.
        assert pd.isna(row["ingested_at"].iloc[0])


def test_importing_the_build_module_binds_nothing_from_the_store() -> None:
    """The AST scan cannot see a helper that *binds* the store's reader at import time.

    Run in a subprocess, so the suite's own imports cannot mask what this module pulls in. The
    stronger assertion -- that ``volab.store`` never reaches ``sys.modules`` -- is not available
    to this story: the canonical session close lives in ``volab.ingest._frames``, and importing
    it executes ``volab/ingest/__init__.py``, which imports ``write_raw`` from the store.
    """
    script = (
        "import volab.features.build as build, volab.store as store\n"
        "bound = sorted(\n"
        "    name\n"
        "    for name, value in vars(build).items()\n"
        "    if getattr(value, '__module__', None) == 'volab.store' or value is store\n"
        ")\n"
        "print(bound)\n"
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(volab.__file__).resolve().parent.parent),
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", f"build.py binds {result.stdout.strip()} from the store"


def test_the_exogenous_series_match_the_configured_ones() -> None:
    """A hand-kept copy of a configured list has to be pinned, or it drifts silently."""
    assert set(EXOGENOUS_SERIES) == set(load_settings().exogenous)


def test_log_returns_are_the_log_ratio_of_consecutive_closes() -> None:
    close = pd.Series([100.0, 101.0, 99.0], index=SESSIONS[:3])
    out = log_returns(close)

    assert pd.isna(out.iloc[0])
    assert out.iloc[1] == pytest.approx(math.log(101.0 / 100.0))
    assert out.iloc[2] == pytest.approx(math.log(99.0 / 101.0))
    assert list(out.index) == SESSIONS[:3]


def test_log_returns_refuse_a_non_positive_close() -> None:
    close = pd.Series([100.0, 0.0], index=SESSIONS[:2])

    with pytest.raises(SchemaError, match="close"):
        log_returns(close)


def test_log_returns_refuse_an_index_that_is_not_ascending_sessions() -> None:
    descending = pd.Series([100.0, 101.0], index=list(reversed(SESSIONS[:2])))
    repeated = pd.Series([100.0, 101.0], index=[SESSIONS[0]] * 2)

    for close in (descending, repeated):
        with pytest.raises(SchemaError, match="ascending, unique"):
            log_returns(close)


def test_a_descending_panel_is_refused() -> None:
    panel = _full_panel().sort_values(["symbol", "ts"], ascending=[True, False])

    with pytest.raises(SchemaError, match="ascending"):
        build_features(panel, through=SESSIONS[100], calendar=CAL)


def test_a_repeated_session_is_refused() -> None:
    doubled = _panel(_bars(SESSIONS, CLOSES), _bars(SESSIONS[:3], CLOSES[:3]))

    with pytest.raises(SchemaError, match="more than one row"):
        build_features(doubled, through=SESSIONS[100], calendar=CAL)


def test_a_panel_whose_as_of_order_disagrees_with_its_ts_order_is_refused() -> None:
    """``as_of`` is what every window and join is keyed on, so an ascending ``ts`` is not enough."""
    panel = _full_panel()
    rows = list(panel.index[panel["symbol"] == "SPY"][:2])
    swapped = panel.copy()
    swapped.loc[rows[0], "as_of"] = panel.loc[rows[1], "as_of"]
    swapped.loc[rows[1], "as_of"] = panel.loc[rows[0], "as_of"]
    assert swapped["ts"].equals(panel["ts"])

    with pytest.raises(SchemaError, match="ascending as_of"):
        build_features(swapped, through=SESSIONS[100], calendar=CAL)


def test_two_rows_for_one_session_are_refused_even_with_distinct_timestamps() -> None:
    panel = _full_panel()
    rows = list(panel.index[panel["symbol"] == "SPY"][:2])
    collided = panel.copy()
    collided.loc[rows[1], "as_of"] = panel.loc[rows[0], "as_of"]
    # Per symbol: the whole panel repeats each session's ts once per series.
    assert collided.loc[collided["symbol"] == "SPY", "ts"].is_unique

    with pytest.raises(SchemaError, match="more than one row for a session"):
        build_features(collided, through=SESSIONS[100], calendar=CAL)


def test_a_naive_ts_is_refused() -> None:
    panel = _full_panel()
    panel["ts"] = panel["ts"].dt.tz_localize(None)

    with pytest.raises(SchemaError, match="timezone-aware"):
        build_features(panel, through=SESSIONS[100], calendar=CAL)


def test_a_missing_mandatory_column_is_refused() -> None:
    panel = _full_panel().drop(columns=["close"])

    with pytest.raises(SchemaError, match="close"):
        build_features(panel, through=SESSIONS[100], calendar=CAL)


def test_a_panel_without_bars_is_refused() -> None:
    panel = _panel(*_vix_family(SESSIONS))

    with pytest.raises(SchemaError, match="one symbol"):
        build_features(panel, through=SESSIONS[100], calendar=CAL)


def test_a_panel_holding_two_symbols_bars_is_refused() -> None:
    panel = _panel(_bars(SESSIONS, CLOSES), _bars(SESSIONS, CLOSES, symbol="QQQ"))

    with pytest.raises(SchemaError, match="one symbol"):
        build_features(panel, through=SESSIONS[100], calendar=CAL)


def test_an_empty_panel_yields_an_empty_frame_with_the_full_column_set() -> None:
    frame = build_features(_full_panel().iloc[:0], through=SESSIONS[100], calendar=CAL)

    assert frame.empty
    assert list(frame.columns) == SPEC_COLUMNS


def test_a_cutoff_before_the_first_session_yields_no_rows() -> None:
    frame = build_features(_full_panel(), through=dt.date(2022, 1, 3), calendar=CAL)

    assert frame.empty
    assert list(frame.columns) == SPEC_COLUMNS
