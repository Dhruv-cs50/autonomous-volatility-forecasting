"""The walk-forward's guarantees, each with an executable witness rather than a sentence.

Ruling R32: a stated "cannot" is a test. The claims this file pins, in the order they matter:

1. **No build is ever handed a row past its own origin.** Not "the feature functions are
   trailing", which is a property of code; this is a property of the data each call receives,
   recorded by ``PanelAudit`` and asserted call by call.
2. **Training targets are knowable at the origin.** Sliced on ``as_of`` -- the session a target's
   horizon completes -- and never on ``ts``, the base session it is *for*. The difference is up to
   ``horizon - 1`` sessions of realized future, which at h=66 is the whole forecast, and the test
   below plants the wrong slice and measures the gap it opens.
3. **Every trial is counted.** A run of N configurations appends exactly N ledger lines, and a
   second run appends rather than rewriting.
4. **The forecast is always positive, so QLIKE is always defined.** Log-HAR by construction, not
   by a floor chosen to make the loss computable.
5. **The per-origin table matches the persisted one, byte for byte.** This is the exhaustive
   truncation-equivalence sweep the sampled one in ``tests/test_truncation_equivalence.py``
   cannot be: E1 is visible at 2.2% of real cutoffs, so a sample is a ratchet and this is proof
   over every origin in the window.

The synthetic tier runs everywhere. The real-data tier skips where the curated store is absent,
which includes any fresh checkout, because ``data/**`` is gitignored and derivable.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest
from test_truncation_equivalence import Synthetic, _synthetic

from volab.config import Settings, load_settings
from volab.errors import DatasetNotFound, SchemaError
from volab.features.targets import build_targets, target_column
from volab.identity import first_difference
from volab.panel import as_of_dates, load_curated_panel
from volab.sessions import get_calendar, sessions_between
from volab.walkforward import (
    HAR_REGRESSORS,
    LOSSES,
    MODELS,
    TRIAL_LEDGER,
    PanelAudit,
    Trial,
    append_trials,
    count_trials,
    evaluate,
    fit_ols,
    git_sha,
    load_targets,
    origins_in,
    qlike,
    scoreable_origins,
    solve,
    squared_error,
    target_panel_for,
)

HORIZON = 1


# ---------------------------------------------------------------------------
# Linear algebra. numpy is absent from pyproject.toml, so the solve is ours and is tested.
# ---------------------------------------------------------------------------


def test_solve_matches_a_hand_computed_system() -> None:
    """A 3x3 with an exact integer answer, so no tolerance is needed to see it is right."""
    matrix = [[2.0, 1.0, -1.0], [-3.0, -1.0, 2.0], [-2.0, 1.0, 2.0]]
    answer = solve(matrix, [8.0, -11.0, -3.0])
    assert answer == pytest.approx([2.0, 3.0, -1.0])


def test_solve_needs_partial_pivoting_to_be_accurate() -> None:
    """The red case for the pivot. A tiny leading entry destroys fixed-pivot elimination.

    This is not decoration: the HAR normal equations are badly scaled, with squared daily
    returns around 1e-8 and their monthly mean smaller again, so the ill-conditioned case is the
    ordinary one here rather than a contrived one.
    """
    matrix = [[1e-18, 1.0], [1.0, 1.0]]
    answer = solve(matrix, [1.0, 2.0])
    assert answer == pytest.approx([1.0, 1.0], abs=1e-9)


def test_solve_refuses_a_singular_system_rather_than_inventing_an_answer() -> None:
    with pytest.raises(SchemaError, match="singular"):
        solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0])


def test_fit_ols_recovers_coefficients_it_was_given_exactly() -> None:
    """Built from a known linear rule with no noise, so the fit must return that rule."""
    # The three regressors must not be affine functions of one another: a first draft used
    # ``har_w = 0.01 * index + 0.5``, which is exactly ``har_d`` rescaled, and the solver
    # correctly refused the rank-deficient system. The test was wrong, not the solve.
    rows = []
    for index in range(40):
        d = 0.1 * index
        w = math.sin(index) + 2.0
        m = 0.002 * index * index
        rows.append(
            {
                "har_d_proxy": d,
                "har_w_proxy": w,
                "har_m_proxy": m,
                "y": 7.0 + 2.0 * d - 3.0 * w + 0.5 * m,
            }
        )
    fit = fit_ols(pd.DataFrame(rows), HAR_REGRESSORS, "y")
    assert fit.intercept == pytest.approx(7.0, abs=1e-6)
    assert list(fit.coefficients) == pytest.approx([2.0, -3.0, 0.5], abs=1e-6)
    assert fit.observations == 40


def test_fit_ols_drops_null_rows_and_says_how_many_survived() -> None:
    rows = [
        {
            "har_d_proxy": float(i),
            "har_w_proxy": math.cos(i) + 3.0,
            "har_m_proxy": float(i) * float(i),
            "y": float(i) * 3,
        }
        for i in range(30)
    ]
    rows[0]["har_w_proxy"] = float("nan")
    rows[1]["y"] = float("nan")
    fit = fit_ols(pd.DataFrame(rows), HAR_REGRESSORS, "y")
    assert fit.observations == 28


def test_fit_ols_refuses_a_window_too_small_for_its_parameters() -> None:
    rows = [
        {"har_d_proxy": 1.0, "har_w_proxy": 2.0, "har_m_proxy": 3.0, "y": 4.0},
        {"har_d_proxy": 2.0, "har_w_proxy": 1.0, "har_m_proxy": 5.0, "y": 6.0},
    ]
    with pytest.raises(SchemaError, match="usable observation"):
        fit_ols(pd.DataFrame(rows), HAR_REGRESSORS, "y")


# ---------------------------------------------------------------------------
# Losses.
# ---------------------------------------------------------------------------


def test_qlike_refuses_a_non_positive_forecast_rather_than_returning_a_nan() -> None:
    """A loss that silently goes missing is how an undefined forecast comes to look like
    an absent observation. Raising keeps the two distinguishable."""
    assert qlike(4.0, 2.0) == pytest.approx(math.log(2.0) + 2.0)
    for broken in (0.0, -1.0, float("nan")):
        with pytest.raises(ValueError, match="positive forecast"):
            qlike(4.0, broken)


def test_squared_error_is_what_it_says() -> None:
    assert squared_error(3.0, 5.0) == pytest.approx(4.0)


def test_both_losses_are_registered_so_a_trial_cannot_be_scored_off_the_books() -> None:
    assert set(LOSSES) == {"qlike", "mse"}
    assert set(MODELS) == {"har_log", "random_walk"}


# ---------------------------------------------------------------------------
# Origins.
# ---------------------------------------------------------------------------


def test_origins_are_sessions_and_the_stride_is_honoured() -> None:
    calendar = get_calendar(load_settings())
    every = origins_in(calendar, dt.date(2024, 1, 2), dt.date(2024, 2, 29))
    strided = origins_in(calendar, dt.date(2024, 1, 2), dt.date(2024, 2, 29), every=5)
    assert every == sessions_between(calendar, dt.date(2024, 1, 2), dt.date(2024, 2, 29))
    assert strided == every[::5]
    # A holiday is not a session, so it cannot be an origin. 2024-01-15 is Martin Luther King Jr
    # Day; asserted against the calendar rather than from memory.
    assert dt.date(2024, 1, 15) not in every


def test_origins_rejects_a_stride_of_zero() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        origins_in(
            get_calendar(load_settings()), dt.date(2024, 1, 2), dt.date(2024, 1, 31), every=0
        )


# ---------------------------------------------------------------------------
# The synthetic walk-forward.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Toy:
    panel: pd.DataFrame
    targets: pd.DataFrame
    origins: tuple[dt.date, ...]
    settings: Settings


@pytest.fixture(scope="module")
def toy() -> Toy:
    """A synthetic panel with its own targets, so the loop runs without a store."""
    settings = load_settings()
    calendar = get_calendar(settings)
    synthetic: Synthetic = _synthetic(calendar)
    bars = synthetic.panel[synthetic.panel["symbol"] == "SPY"].reset_index(drop=True)
    targets = build_targets(target_panel_for(bars, calendar), horizon=HORIZON, calendar=calendar)
    # Origins late enough that the 66-session monthly window is full behind them, and stopping
    # before the last session so every one has a completed target.
    usable = [day for day in synthetic.sessions[70:-3]]
    return Toy(
        panel=synthetic.panel,
        targets=targets,
        origins=tuple(usable[::6]),
        settings=settings,
    )


def _run(toy: Toy, audit: PanelAudit | None = None) -> object:
    return evaluate(
        toy.panel,
        toy.targets,
        toy.origins,
        symbol="SPY",
        horizon=HORIZON,
        calendar=get_calendar(toy.settings),
        minimum_training=20,
        audit=audit,
    )


def test_no_build_is_ever_handed_a_row_past_its_own_origin(toy: Toy) -> None:
    """Claim 1, and the only one that would make the rest worthless if it failed.

    The audit records ``(through, max(as_of))`` for every panel ``build_features`` receives, so
    this is a check on the data each call was handed rather than on the code it ran.
    """
    audit = PanelAudit()
    _run(toy, audit)
    assert audit.calls, "nothing was built, so nothing was checked"
    assert len(audit.calls) == len(toy.origins)
    assert audit.violations() == [], (
        f"{len(audit.violations())} build(s) were handed rows past their cutoff"
    )
    # And the audit can actually fail, so a green result means something.
    audit.record(dt.date(2024, 1, 2), dt.date(2024, 1, 3))
    assert audit.violations() == [(dt.date(2024, 1, 2), dt.date(2024, 1, 3))]


def test_every_origin_is_scored_or_recorded_as_skipped_never_silently_dropped(toy: Toy) -> None:
    evaluation = _run(toy)
    accounted = {result.origin for result in evaluation.results} | {
        origin for origin, _ in evaluation.skipped
    }
    assert accounted == set(toy.origins), (
        f"{len(set(toy.origins) - accounted)} origin(s) vanished without being skipped"
    )


def test_the_log_har_forecast_is_always_positive_so_qlike_is_always_defined(toy: Toy) -> None:
    """Claim 4. Positive by construction -- ``exp`` of a fitted log -- not by a floor."""
    evaluation = _run(toy)
    assert evaluation.results, "nothing was forecast"
    for result in evaluation.results:
        forecast = result.forecasts["har_log"]
        assert not pd.isna(forecast), f"{result.origin}: log-HAR produced NaN"
        assert forecast > 0, f"{result.origin}: log-HAR produced {forecast}"
        qlike(max(result.realized, 0.0), forecast)


def test_training_targets_are_knowable_at_the_origin(toy: Toy) -> None:
    """Claim 2, measured. Slicing on ``ts`` instead of ``as_of`` is the leak, so size it.

    At h=1 the gap is one session, which is the smallest it can be and still the point: the test
    asserts that the ``as_of`` slice admits strictly fewer base sessions than the ``ts`` slice at
    some origin, which is exactly the set a ``ts`` slice would have leaked.
    """
    bases = [stamp.date() for stamp in pd.to_datetime(toy.targets["ts"], utc=True)]
    completions = list(as_of_dates(toy.targets))
    assert all(completion > base for base, completion in zip(bases, completions, strict=True)), (
        "a target completes on or before its own base session, which cannot be"
    )

    leaked_somewhere = False
    for origin in toy.origins:
        honest = {base for base, done in zip(bases, completions, strict=True) if done <= origin}
        by_base = {base for base in bases if base <= origin}
        assert honest <= by_base, "slicing on as_of admitted a base the ts slice would not"
        if honest < by_base:
            leaked_somewhere = True
    assert leaked_somewhere, (
        "the ts slice never differed from the as_of slice, so this fixture cannot show the leak"
    )


def test_the_evaluation_counts_one_trial_per_model_and_loss(toy: Toy) -> None:
    evaluation = _run(toy)
    trials = evaluation.trials()
    assert len(trials) == len(MODELS) * len(LOSSES)
    assert {(trial.model, trial.loss) for trial in trials} == {
        (model, loss) for model in MODELS for loss in LOSSES
    }
    for trial in trials:
        assert trial.horizon == HORIZON
        assert trial.symbol == "SPY"
        assert trial.origins <= len(toy.origins)


# ---------------------------------------------------------------------------
# The trial ledger.
# ---------------------------------------------------------------------------


def _trial(model: str, loss: str) -> Trial:
    return Trial(
        model=model,
        symbol="SPY",
        horizon=1,
        loss=loss,
        origins=10,
        value=1.25,
        dropped=0,
        first_origin=dt.date(2024, 1, 2),
        last_origin=dt.date(2024, 3, 1),
    )


def test_a_run_of_n_configurations_appends_exactly_n_ledger_lines(tmp_path: Path) -> None:
    """Claim 3. "Every trial is counted" is a fact about the code only if this is checked."""
    trials = [_trial(model, loss) for model in MODELS for loss in LOSSES]
    assert count_trials(tmp_path) == 0
    written = append_trials(
        trials, root=tmp_path, at=dt.datetime(2026, 10, 22, tzinfo=dt.UTC), sha="deadbeef"
    )
    assert written == len(trials)
    assert count_trials(tmp_path) == len(trials)


def test_the_ledger_appends_and_never_rewrites(tmp_path: Path) -> None:
    at = dt.datetime(2026, 10, 22, tzinfo=dt.UTC)
    append_trials([_trial("har_log", "qlike")], root=tmp_path, at=at, sha="one")
    first = (tmp_path / TRIAL_LEDGER).read_text(encoding="utf-8")
    append_trials([_trial("random_walk", "mse")], root=tmp_path, at=at, sha="two")
    second = (tmp_path / TRIAL_LEDGER).read_text(encoding="utf-8")
    assert second.startswith(first), "the ledger's existing lines moved"
    assert count_trials(tmp_path) == 2


def test_every_ledger_line_names_the_tree_that_produced_it(tmp_path: Path) -> None:
    """A trial count with no commit behind it cannot be checked against the code that ran."""
    import json

    append_trials(
        [_trial("har_log", "qlike")],
        root=tmp_path,
        at=dt.datetime(2026, 10, 22, tzinfo=dt.UTC),
        sha=git_sha(),
    )
    line = json.loads((tmp_path / TRIAL_LEDGER).read_text(encoding="utf-8").splitlines()[0])
    assert set(line) >= {
        "at",
        "git_sha",
        "model",
        "symbol",
        "horizon",
        "loss",
        "origins",
        "value",
        "first_origin",
        "last_origin",
    }
    assert line["git_sha"] != "", "a line with no sha is a trial nobody can locate"


def test_git_sha_never_takes_an_optional_lock(tmp_path: Path) -> None:
    """Phase 1 lost about an hour to ``index.lock`` files its own audit stamp was creating.

    Asserted on the argv rather than by reproducing the contention, which needs a concurrent
    committer -- the same way Phase 1's own fix is tested.
    """
    import subprocess as sp

    import volab.walkforward as module

    seen: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = "abc123\n"

    def recording(args: list[str], **kwargs: object) -> Done:
        seen.append(args)
        return Done()

    original = sp.run
    module.subprocess.run = recording  # type: ignore[assignment]
    try:
        assert module.git_sha() == "abc123"
    finally:
        module.subprocess.run = original  # type: ignore[assignment]
    assert seen and "--no-optional-locks" in seen[0], seen


# ---------------------------------------------------------------------------
# The real-data tier: the exhaustive truncation sweep the sampled one cannot be.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Real:
    panel: pd.DataFrame
    targets: pd.DataFrame
    settings: Settings


@pytest.fixture(scope="module")
def real() -> Real:
    settings = load_settings()
    try:
        panel = load_curated_panel(settings, "SPY", end=settings.splits.dev_end)
        targets = load_targets(settings, "SPY", HORIZON, end=settings.splits.dev_end)
    except DatasetNotFound as absent:
        pytest.skip(f"the curated store holds no panel to walk forward over: {absent}")
    return Real(panel=panel, targets=targets, settings=settings)


def test_the_per_origin_rows_are_byte_identical_to_the_persisted_table(real: Real) -> None:
    """Claim 5, over a window of real origins: the exhaustive form of truncation equivalence.

    Every row here was built from a panel stopping at its own session; the persisted table was
    built once from the whole panel. If they agree byte for byte then no feature in the table
    used a row from after its own session, over **every** origin in the window rather than over a
    2.2%-powered sample.

    The window is a quarter rather than the whole dev span: at roughly 0.06s per origin the full
    sweep is minutes, and the ``VOLAB_PIT_EXHAUSTIVE=1`` path plus the walk-forward CLI are where
    that is run and recorded.
    """
    from volab.paths import Tier
    from volab.store import load_panel

    origins = scoreable_origins(
        real.settings, real.targets, first=dt.date(2025, 7, 1), last=dt.date(2025, 9, 30)
    )
    assert len(origins) >= 50, f"only {len(origins)} origins in the window"

    audit = PanelAudit()
    evaluation = evaluate(
        real.panel,
        real.targets,
        origins,
        symbol="SPY",
        horizon=HORIZON,
        calendar=get_calendar(real.settings),
        audit=audit,
    )
    assert audit.violations() == []

    stored = (
        load_panel(
            "har_daily",
            end=real.settings.splits.dev_end,
            symbols=["SPY"],
            tier=Tier.FEATURES,
            settings=real.settings,
        )
        .sort_values("as_of")
        .reset_index(drop=True)
    )
    built = evaluation.features
    wanted = set(as_of_dates(built))
    rows = stored[[day in wanted for day in as_of_dates(stored)]].reset_index(drop=True)
    assert len(rows) == len(built) == len(origins)

    left = built.assign(
        as_of=list(as_of_dates(built)),
        has_distribution=built["has_distribution"].astype("boolean"),
    )
    right = rows[list(built.columns)].assign(
        as_of=list(as_of_dates(rows)),
        has_distribution=rows["has_distribution"].astype("boolean"),
    )
    assert first_difference(left, right) is None, (
        "a per-origin row differs from the persisted one, so some feature read a session it "
        "could not have known"
    )


def test_an_origin_whose_horizon_runs_past_the_seal_is_not_scored(real: Real) -> None:
    """The holdout stays sealed, and an unscoreable origin is excluded rather than NaN-diluted."""
    last = real.settings.splits.dev_end
    origins = scoreable_origins(
        real.settings, real.targets, first=last - dt.timedelta(days=20), last=last
    )
    completions = {
        stamp.date(): done
        for stamp, done in zip(
            pd.to_datetime(real.targets["ts"], utc=True), as_of_dates(real.targets), strict=True
        )
    }
    for origin in origins:
        assert completions[origin] <= last, (
            f"origin {origin} is scored against a target completing {completions[origin]}, past "
            f"dev_end {last}"
        )
    assert last not in origins, (
        "dev_end itself was offered as an origin, but its h-session target cannot have completed"
    )


def test_the_target_column_is_the_one_the_targets_dataset_actually_carries(real: Real) -> None:
    assert target_column(HORIZON) in real.targets.columns


def test_a_loss_undefined_for_one_model_drops_the_origin_for_all_of_them(toy: Toy) -> None:
    """R55 and the common-origin rule, together. QLIKE's refusal must not become a silent gap.

    The random-walk forecast is the last daily variance proxy, which is **zero** at a session
    whose close did not move -- found on the first full real run, where QLIKE raised rather than
    returning a NaN that would have been averaged away. The fix is not a floor: the origin is
    dropped for *every* model on that loss, so the two averages stay like-for-like, and the count
    dropped is carried on each trial so nobody can read a comparison as covering more than it does.
    """
    evaluation = _run(toy)
    assert evaluation.results, "nothing was forecast"

    attempted = {result.origin for result in evaluation.results}
    for name in LOSSES:
        usable = evaluation.comparable(name)
        assert usable <= attempted
        for trial in evaluation.trials():
            if trial.loss == name:
                assert trial.origins == len(usable), (
                    f"{trial.model}/{name} scored {trial.origins} origins but only "
                    f"{len(usable)} are comparable"
                )
                assert trial.dropped == len(attempted - usable)

    # MSE is defined everywhere, so it can only lose origins to a NaN forecast, never to its own
    # domain; QLIKE's set is a subset of it. Asserted so a loss added without a domain is caught.
    assert evaluation.comparable("qlike") <= evaluation.comparable("mse")


def test_a_zero_forecast_is_excluded_from_qlike_and_kept_in_mse() -> None:
    """The mechanism on its own, without needing a panel that happens to contain the case."""
    from volab.walkforward import LOSS_DOMAIN, Evaluation, OriginResult

    results = (
        OriginResult(dt.date(2024, 1, 2), 1e-4, {"har_log": 1e-4, "random_walk": 1e-4}, 100),
        OriginResult(dt.date(2024, 1, 3), 1e-4, {"har_log": 1e-4, "random_walk": 0.0}, 100),
    )
    evaluation = Evaluation(
        symbol="SPY", horizon=1, results=results, features=pd.DataFrame(), skipped=()
    )
    assert LOSS_DOMAIN["qlike"](1e-4) and not LOSS_DOMAIN["qlike"](0.0)
    assert evaluation.comparable("qlike") == {dt.date(2024, 1, 2)}
    assert evaluation.comparable("mse") == {dt.date(2024, 1, 2), dt.date(2024, 1, 3)}

    by_key = {(trial.model, trial.loss): trial for trial in evaluation.trials()}
    assert by_key[("har_log", "qlike")].origins == 1
    assert by_key[("har_log", "qlike")].dropped == 1
    # The honest model is not credited for the other model's undefined origin, and vice versa.
    assert by_key[("random_walk", "qlike")].origins == 1
    assert by_key[("har_log", "mse")].origins == 2
    assert by_key[("har_log", "mse")].dropped == 0
