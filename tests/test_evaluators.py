"""The decision evaluators: Gaussian VaR/ES, the Fissler-Ziegel joint loss and its signed returns.

``volab.evaluators`` scores *decisions* where the rest of the repository scores forecasts. The
loss it uses is strictly consistent for the (VaR, ES) pair, and that is what licenses handing it to
``volab.compare.analyse`` as a ``LossSeries``: Diebold-Mariano, the confidence set and SPA are then
statistics about which forecaster makes the better *decisions*, not merely the better variance.

Three things are witnessed here rather than argued, and each had a way to be silently wrong:

* the formula (``test_fz0_matches_the_signed_form_of_patton_ziegel_chen`` pins it against the
  source's own sign convention; ``test_fz0_is_minimised_at_the_true_quantile_pair`` and
  ``test_the_expected_fz0_at_the_truth_is_the_log_of_the_expected_shortfall`` pin its meaning);
* that scoring a decision adds **no ledger line** (``LOSSES`` is read by the driver loop, so an
  entry there would write a third trial line per contestant per configuration);
* the standing of the signed horizon return. ``OriginResult.realized`` is a *variance*; a breach
  needs the signed return, which nothing in an ``Evaluation`` carries, so ``horizon_returns``
  reads it from the store. It is a scoring read, like ``realized``. Two separate things are shown:
  the ``horizon_returns`` tests show it reads to ``dev_end`` and no further, and
  ``test_no_horizon_return_reaches_a_fit`` shows the walk-forward hands no contestant a signed
  forward return -- that test does not call ``horizon_returns``; it is about what the harness
  gives a fit, which is what makes the read a scoring read.

Nothing here appends to ``data/trials.jsonl`` or reads past ``dev_end``; the store tests write a
synthetic one under ``tmp_path``.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import SyntheticStore
from model_conformance import Spy, Toy, make_toy, run_cohort
from scipy import integrate, stats
from test_import_discipline import _unlocks

import volab.evaluators as evaluators
import volab.walkforward as wf
from volab.compare import analyse
from volab.config import Settings, Splits, load_settings
from volab.errors import HoldoutViolation
from volab.evaluators import ALPHAS, coverage, decision_series, fz0, horizon_returns, var_es
from volab.features.targets import build_targets
from volab.models.base import Cohort
from volab.panel import as_of_dates
from volab.paths import curated_path
from volab.sessions import get_calendar, sessions_between
from volab.store import load_panel
from volab.walkforward import Evaluation, OriginResult, evaluate, target_panel_for

# ---------------------------------------------------------------------------
# var_es
# ---------------------------------------------------------------------------


def test_gaussian_var_and_es_at_one_percent() -> None:
    var, es = var_es(variance=0.0004, alpha=0.01)  # sigma = 0.02
    assert var == pytest.approx(0.02 * 2.3263478740, rel=1e-9)
    # The brief quoted 2.6652142437 here, which is 8.7e-9 away, relatively, from the true ratio
    # phi(z_0.01) / 0.01 = 2.66521422035 and so fails its own rel=1e-9. This is the true value; the
    # next test derives it independently so a transcribed digit cannot be what pins it.
    assert es == pytest.approx(0.02 * 2.6652142203, rel=1e-9)


@pytest.mark.parametrize("alpha", ALPHAS)
def test_the_gaussian_es_is_the_tail_mean_by_quadrature(alpha: float) -> None:
    """ES as the mean loss given a breach, integrated rather than read off a closed form.

    ``var_es`` uses ``phi(z) / alpha``. This integrates ``-x * phi(x)`` over the lower tail
    ``(-inf, -z)`` and divides by the tail mass, sharing no code with it.
    """
    var, es = var_es(variance=1.0, alpha=alpha)
    tail_mass, _ = integrate.quad(stats.norm.pdf, -np.inf, -var)
    tail_loss, _ = integrate.quad(lambda x: -x * stats.norm.pdf(x), -np.inf, -var)
    assert tail_mass == pytest.approx(alpha, rel=1e-9)
    assert es == pytest.approx(tail_loss / tail_mass, rel=1e-9)


def test_the_pair_scales_with_the_standard_deviation() -> None:
    """Both are sigma times a constant, so quadrupling the variance doubles both."""
    for alpha in ALPHAS:
        var, es = var_es(variance=0.0001, alpha=alpha)
        var4, es4 = var_es(variance=0.0004, alpha=alpha)
        assert var4 == pytest.approx(2 * var, rel=1e-12)
        assert es4 == pytest.approx(2 * es, rel=1e-12)


def test_es_is_always_more_extreme_than_var() -> None:
    for alpha in ALPHAS:
        var, es = var_es(variance=0.01, alpha=alpha)
        assert es > var > 0


@pytest.mark.parametrize("variance", [0.0, -1e-12, float("nan"), float("inf")])
def test_a_nonpositive_variance_forecast_is_refused(variance: float) -> None:
    # Review Focus 1. A square root of a negative is a nan and the vol-target task divides by the
    # result, so the refusal is here. ``nan <= 0`` is False, which is why the guard is not that.
    with pytest.raises(ValueError, match="variance must be positive and finite"):
        var_es(variance=variance, alpha=0.01)


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.01, 1.5, float("nan")])
def test_var_es_refuses_a_level_outside_the_open_unit_interval(alpha: float) -> None:
    with pytest.raises(ValueError, match=r"alpha must lie in the open interval"):
        var_es(variance=0.0004, alpha=alpha)


# ---------------------------------------------------------------------------
# fz0
# ---------------------------------------------------------------------------


def _patton_ziegel_chen(y: float, var: float, es: float, alpha: float) -> float:
    """FZ0 exactly as Patton, Ziegel and Chen (2019) write it, in their sign convention.

    There VaR ``v`` and ES ``e`` are *signed* quantiles of the return (both negative for a loss
    tail, ``e <= v < 0``):

        S(v, e, y; alpha) = -1/(alpha e) * 1{y <= v} * (v - y) + v/e + log(-e) - 1

    ``fz0`` takes magnitudes, so this is called with ``v = -var`` and ``e = -es``. It is written
    here separately, with the opposite signs, so a sign slip in either does not cancel.
    """
    v, e = -var, -es
    return -1.0 / (alpha * e) * (1.0 if y <= v else 0.0) * (v - y) + v / e + math.log(-e) - 1.0


@pytest.mark.parametrize("realized", [-0.05, -0.031, -0.03, -0.02, 0.0, 0.04])
@pytest.mark.parametrize("alpha", ALPHAS)
def test_fz0_matches_the_signed_form_of_patton_ziegel_chen(realized: float, alpha: float) -> None:
    # -0.05 and -0.031 breach, -0.03 sits on the boundary (no penalty either way), the rest do not.
    assert fz0(realized, var=0.03, es=0.04, alpha=alpha) == pytest.approx(
        _patton_ziegel_chen(realized, 0.03, 0.04, alpha), rel=1e-12, abs=1e-12
    )


def test_fz0_by_hand_at_one_breach_and_one_miss() -> None:
    # Breach: (0.05 - 0.03) / (0.01 * 0.04) + 0.03 / 0.04 + log(0.04) - 1.
    assert fz0(-0.05, var=0.03, es=0.04, alpha=0.01) == pytest.approx(
        50.0 + 0.75 + math.log(0.04) - 1.0, rel=1e-12
    )
    # No breach: the penalty term vanishes and only v/e + log(-e) - 1 remains.
    assert fz0(0.01, var=0.03, es=0.04, alpha=0.01) == pytest.approx(
        0.75 + math.log(0.04) - 1.0, rel=1e-12
    )


def test_a_breach_costs_more_than_a_miss_and_by_the_breach_depth() -> None:
    miss = fz0(-0.01, var=0.03, es=0.04, alpha=0.01)
    shallow = fz0(-0.04, var=0.03, es=0.04, alpha=0.01)
    deep = fz0(-0.08, var=0.03, es=0.04, alpha=0.01)
    assert miss < shallow < deep
    # Linear in the shortfall beyond VaR, slope 1 / (alpha * es).
    assert (deep - shallow) == pytest.approx(0.04 / (0.01 * 0.04), rel=1e-12)


def test_fz0_refuses_an_es_that_is_not_more_extreme_than_the_var() -> None:
    # Review Focus 3.
    with pytest.raises(ValueError, match="es must be more extreme than var"):
        fz0(realized_return=-0.01, var=0.03, es=0.02, alpha=0.01)
    # Equal is refused too: the pair is strictly ordered or it is not an (VaR, ES) pair.
    with pytest.raises(ValueError, match="es must be more extreme than var"):
        fz0(realized_return=-0.01, var=0.03, es=0.03, alpha=0.01)


@pytest.mark.parametrize("var", [0.0, -0.01])
def test_fz0_refuses_a_var_that_is_not_a_positive_magnitude(var: float) -> None:
    with pytest.raises(ValueError, match="var must be a positive magnitude"):
        fz0(realized_return=-0.01, var=var, es=0.05, alpha=0.01)


@pytest.mark.parametrize("realized", [float("nan"), float("inf"), float("-inf")])
def test_fz0_refuses_a_realized_return_that_is_not_finite(realized: float) -> None:
    # ``nan <= -var`` is False, so without this a nan return scores as a clean miss: the most
    # favourable loss the pair can get, handed out silently.
    with pytest.raises(ValueError, match="realized_return must be finite"):
        fz0(realized_return=realized, var=0.03, es=0.04, alpha=0.01)


def test_fz0_refuses_a_level_outside_the_open_unit_interval() -> None:
    with pytest.raises(ValueError, match=r"alpha must lie in the open interval"):
        fz0(realized_return=-0.01, var=0.03, es=0.04, alpha=1.0)


def _draws(count: int = 20_000, *, seed: int = 20260101) -> list[float]:
    return [float(x) for x in np.random.default_rng(seed).normal(0.0, 0.02, count)]


def _mean_fz0(draws: Sequence[float], var: float, es: float, alpha: float) -> float:
    return sum(fz0(y, var, es, alpha) for y in draws) / len(draws)


def test_fz0_is_minimised_at_the_true_quantile_pair() -> None:
    """Strict consistency, which is the property that licenses scoring with it.

    Twenty thousand N(0, 0.02**2) returns from a fixed seed. The mean FZ0 at the true pair is
    strictly below its mean at the pair scaled up, scaled down, and moved one coordinate at a
    time -- so neither VaR nor ES alone can be mis-stated for free. The draws are shared, so the
    comparison is paired and its noise is far smaller than the loss's own.
    """
    draws = _draws()
    for alpha in ALPHAS:
        var, es = var_es(variance=0.0004, alpha=alpha)
        truth = _mean_fz0(draws, var, es, alpha)
        for label, (v, e) in {
            "wide": (var * 1.2, es * 1.2),
            "tight": (var * 0.8, es * 0.8),
            "var too low": (var * 0.8, es),
            "var too high": (var * 1.1, es),  # 1.2 would put var past es, which fz0 refuses
            "es too low": (var, es * 0.9),
            "es too high": (var, es * 1.3),
        }.items():
            assert truth < _mean_fz0(draws, v, e, alpha), f"alpha={alpha}: {label} scored better"


def test_the_expected_fz0_at_the_truth_is_the_log_of_the_expected_shortfall() -> None:
    """Pins the *meaning* of the formula, not only the ordering it induces.

    At the true VaR the penalty term has expectation ``alpha * (ES - VaR) / (alpha * e)`` and so
    the expected loss is ``ES / e + log(e) - 1``, which at ``e = ES`` is ``log(ES)``. A formula
    that dropped the ``- 1`` or the ``log`` would still be minimised somewhere sensible and fail
    here. The tolerance is four standard errors of the sample mean, measured on the sample.
    """
    draws = _draws()
    for alpha in ALPHAS:
        var, es = var_es(variance=0.0004, alpha=alpha)
        losses = [fz0(y, var, es, alpha) for y in draws]
        standard_error = float(np.std(losses, ddof=1)) / math.sqrt(len(losses))
        assert abs(float(np.mean(losses)) - math.log(es)) < 4 * standard_error


# ---------------------------------------------------------------------------
# decision_series
# ---------------------------------------------------------------------------

HAND_ORIGINS = tuple(dt.date(2024, 3, 1) + dt.timedelta(days=7 * i) for i in range(40))


def _hand_evaluation(
    forecasts: dict[str, list[float]], *, horizon: int = 5, cohort: str = "toy", every: int = 5
) -> Evaluation:
    names = tuple(forecasts)
    count = len(next(iter(forecasts.values())))
    results = tuple(
        OriginResult(
            origin=HAND_ORIGINS[i],
            realized=4e-4 * horizon,
            forecasts={name: forecasts[name][i] for name in names},
            training_rows=100,
        )
        for i in range(count)
    )
    return Evaluation(
        symbol="SPY",
        horizon=horizon,
        results=results,
        features=pd.DataFrame(),
        skipped=(),
        requested=count,
        every=every,
        contestants=names,
        cohort=cohort,
    )


def _variances(count: int, *, seed: int, level: float = 4e-4 * 5) -> list[float]:
    rng = np.random.default_rng(seed)
    return [float(level * math.exp(x)) for x in rng.normal(0.0, 0.3, count)]


def _returns_for(count: int, *, seed: int = 11, sigma: float = 0.045) -> dict[dt.date, float]:
    rng = np.random.default_rng(seed)
    return {HAND_ORIGINS[i]: float(rng.normal(0.0, sigma)) for i in range(count)}


def test_decision_series_is_rectangular_and_on_comparable_origins() -> None:
    nan = float("nan")
    forecasts = {
        "a": _variances(10, seed=1),
        "b": _variances(10, seed=2),
        "c": _variances(10, seed=3),
    }
    forecasts["b"][2] = nan  # a refusal, as ``ModelSkip`` writes it
    forecasts["c"][6] = -1e-4  # QLIKE undefined here, and so is a Gaussian VaR
    evaluation = _hand_evaluation(forecasts)
    series = decision_series(evaluation, alpha=0.01, cohort="toy", returns=_returns_for(10))

    assert all(len(v) == len(series.origins) for v in series.values.values())
    assert set(series.origins) <= evaluation.comparable("qlike")
    assert set(series.values) == set(forecasts)
    # Every comparable origin has a return here, so nothing was dropped on the way.
    assert set(series.origins) == evaluation.comparable("qlike")
    assert HAND_ORIGINS[2] not in series.origins and HAND_ORIGINS[6] not in series.origins
    assert list(series.origins) == sorted(series.origins)


def test_decision_series_carries_the_evaluations_identity_and_names_its_loss() -> None:
    evaluation = _hand_evaluation({"a": _variances(10, seed=1), "b": _variances(10, seed=2)})
    for alpha in ALPHAS:
        series = decision_series(evaluation, alpha=alpha, cohort="toy", returns=_returns_for(10))
        assert series.loss == f"fz0_a{alpha}"
        assert (series.symbol, series.horizon, series.stride, series.cohort) == (
            evaluation.symbol,
            evaluation.horizon,
            evaluation.every,
            "toy",
        )
    assert (
        decision_series(evaluation, alpha=0.01, cohort="toy", returns=_returns_for(10)).loss
        != decision_series(evaluation, alpha=0.025, cohort="toy", returns=_returns_for(10)).loss
    )


def test_decision_series_holds_the_fz0_of_each_contestants_own_var_es() -> None:
    forecasts = {"a": _variances(10, seed=1), "b": _variances(10, seed=2)}
    returns = _returns_for(10)
    # Make one origin a certain breach for every contestant, so the penalty branch is exercised.
    returns[HAND_ORIGINS[4]] = -0.9
    series = decision_series(
        _hand_evaluation(forecasts), alpha=0.025, cohort="toy", returns=returns
    )
    for name, column in series.values.items():
        for origin, loss in zip(series.origins, column, strict=True):
            variance = forecasts[name][HAND_ORIGINS.index(origin)]
            var, es = var_es(variance, 0.025)
            assert loss == fz0(returns[origin], var, es, 0.025)
    breach = series.origins.index(HAND_ORIGINS[4])
    assert all(
        column[breach] > max(column[:breach] + column[breach + 1 :])
        for column in series.values.values()
    ), "the certain breach is not the dearest origin"


def test_decision_series_refuses_a_comparable_origin_with_no_return() -> None:
    """A dropped origin would leave a loss series on a different set than the trial beside it.

    The silent denominator, one level up, and the same shape ``Evaluation.comparable`` exists to
    prevent. ``horizon_returns`` omits an origin only when it cannot be scored, and then the
    evaluation could not have scored it either, so this is an inconsistency to surface.
    """
    returns = _returns_for(10)
    del returns[HAND_ORIGINS[3]]
    evaluation = _hand_evaluation({"a": _variances(10, seed=1), "b": _variances(10, seed=2)})
    with pytest.raises(ValueError, match="no horizon return for 1 comparable origin"):
        decision_series(evaluation, alpha=0.01, cohort="toy", returns=returns)


def test_a_missing_return_error_says_how_to_get_one() -> None:
    returns = _returns_for(10)
    del returns[HAND_ORIGINS[3]]
    evaluation = _hand_evaluation({"a": _variances(10, seed=1)})
    with pytest.raises(ValueError, match=r"pass horizon_returns\(\.\.\., origins="):
        decision_series(evaluation, alpha=0.01, cohort="toy", returns=returns)


def test_an_infinite_forecast_is_refused_naming_the_contestant_and_origin() -> None:
    """``inf`` is inside QLIKE's domain, so ``comparable`` keeps it, and ``var_es`` refuses it.

    The refusal stays -- the volatility-target task divides by the result -- but it must say
    *whose* forecast and *where*, or a long run dies with no locus.
    """
    forecasts = {"a": _variances(10, seed=1), "culprit": _variances(10, seed=2)}
    forecasts["culprit"][4] = float("inf")
    evaluation = _hand_evaluation(forecasts)
    assert HAND_ORIGINS[4] in evaluation.comparable("qlike"), "the premise: QLIKE admits inf"
    with pytest.raises(ValueError, match=rf"contestant 'culprit' at origin {HAND_ORIGINS[4]}"):
        decision_series(evaluation, alpha=0.01, cohort="toy", returns=_returns_for(10))


def test_decision_series_refuses_a_cohort_the_evaluation_did_not_run() -> None:
    evaluation = _hand_evaluation({"a": _variances(10, seed=1)}, cohort="real")
    with pytest.raises(ValueError, match="was asked for but the evaluation ran cohort"):
        decision_series(evaluation, alpha=0.01, cohort="other", returns=_returns_for(10))


def test_decision_series_over_an_empty_evaluation_is_empty_not_an_error() -> None:
    empty = Evaluation(
        symbol="SPY",
        horizon=5,
        results=(),
        features=pd.DataFrame(),
        skipped=(),
        contestants=("a", "b"),
        cohort="toy",
    )
    series = decision_series(empty, alpha=0.01, cohort="toy", returns={})
    assert series.origins == () and series.n == 0
    assert series.values == {"a": (), "b": ()}


def _long_evaluation() -> Evaluation:
    return _hand_evaluation(
        {
            "har_levels": _variances(40, seed=21),
            "random_walk": _variances(40, seed=22, level=4e-4 * 5 * 1.4),
            "better": _variances(40, seed=23, level=4e-4 * 5 * 0.95),
        }
    )


def test_analyse_accepts_a_decision_series_without_losses_gaining_an_entry() -> None:
    """The point of building the ``LossSeries`` by hand: the statistics, and no new trial line.

    ``LOSSES`` is read by ``Evaluation.trials`` and so by the driver loop; an entry there makes
    every future run of every cohort write another trial line per contestant per configuration,
    and ``count_trials`` is what the holdout pre-registration pins.

    What guards that is ``series.loss not in LOSSES`` (and its restatement from the consumer's
    side: no trial a run would write carries the decision loss's name -- the same fact, since
    ``trials`` iterates ``LOSSES``, not a second witness). The before/after equality is weaker and
    only catches ``decision_series`` or ``analyse`` mutating the dicts at run time. No enumeration
    of today's loss names is asserted (R151).
    """
    before = dict(wf.LOSSES)
    domain_before = dict(wf.LOSS_DOMAIN)
    evaluation = _long_evaluation()
    series = decision_series(evaluation, alpha=0.01, cohort="toy", returns=_returns_for(40))

    comparison = analyse(series, reps=200)

    assert comparison.loss == series.loss == "fz0_a0.01"
    assert comparison.origins == series.n == 40
    assert set(comparison.contestants) == {"har_levels", "random_walk", "better"}
    assert comparison.refusal is None and comparison.pairs, "nothing was compared"
    assert dict(wf.LOSSES) == before
    assert dict(wf.LOSS_DOMAIN) == domain_before
    assert series.loss not in wf.LOSSES
    assert not any(name.lower().startswith("fz") for name in wf.LOSSES)
    assert series.loss not in {trial.loss for trial in evaluation.trials()}


def test_a_decision_series_with_a_wrong_variance_loses_to_the_right_one() -> None:
    """The series is a ranking of decisions: returns drawn at sigma favour the forecast at sigma.

    The same evaluation machinery, three contestants: one forecasting the variance the returns were
    drawn from, one a third of it and one three times it. Over enough origins the mean FZ0 of the
    first is lower than both -- the sign the DM statistic's ``d_bar`` reports. **Both directions
    are needed.** For a Gaussian forecaster ``var / es`` is the constant ``z / k``, so without the
    ``log(es)`` term the loss is the breach penalty alone, under-forecasting is still punished, and
    inflating sigma is *rewarded*: only the over-forecaster discriminates the full formula.
    """
    count = 40
    sigma = 0.045
    evaluation = _hand_evaluation(
        {
            "right": [sigma**2] * count,
            "too_small": [sigma**2 / 3.0] * count,
            "too_large": [sigma**2 * 3.0] * count,
        }
    )
    returns = _returns_for(count, sigma=sigma)
    for alpha in ALPHAS:
        series = decision_series(evaluation, alpha=alpha, cohort="toy", returns=returns)
        assert sum(series.values["right"]) < sum(series.values["too_small"])
        # A real margin, not a float-noise tie: no draw breaches at either, so what separates them
        # is the ``log(es)`` term, 40 * log(sqrt(3)) ~ 22.
        assert sum(series.values["too_large"]) - sum(series.values["right"]) > 1.0


# ---------------------------------------------------------------------------
# horizon_returns -- the standing of the signed return, demonstrated (spec 7.3a)
# ---------------------------------------------------------------------------

BARS = "bars_daily"
SEALED_CLOSE = 1.0e9  # a close that, were it ever read, would make a return unmistakable


def _close_at(index: int) -> float:
    """Deterministic, strictly positive and never repeating a ratio, so a wrong offset shows."""
    return 100.0 + 0.7 * index + 3.0 * math.sin(index / 3.0)


def _store_with_bars(
    store: SyntheticStore,
    *,
    drop: Sequence[dt.date] = (),
    null_close: Sequence[dt.date] = (),
    first: dt.date = dt.date(2023, 11, 1),
    last: dt.date = dt.date(2024, 1, 31),
) -> list[dt.date]:
    """Curated SPY bars from ``first`` to ``last`` (2023-11-01 to 2024-01-31), written whole.

    Everything after ``dev_end`` (2023-12-31 in this store's settings) is written too, at
    ``SEALED_CLOSE``: the file is on disk and ``load_panel`` is what must keep it out. ``drop``
    leaves a session with no bar; ``null_close`` writes the bar with a null close.
    """
    calendar = get_calendar(store.settings)
    sessions = sessions_between(calendar, first, last)
    dev_end = store.settings.splits.dev_end
    schema = pa.schema(
        [
            ("symbol", pa.string()),
            ("ts", pa.timestamp("us", tz="UTC")),
            ("as_of", pa.date32()),
            ("ingested_at", pa.timestamp("us", tz="UTC")),
            ("close", pa.float64()),
        ]
    )
    for index, day in enumerate(sessions):
        if day in drop:
            continue
        close: float | None = SEALED_CLOSE if day > dev_end else _close_at(index)
        if day in null_close:
            close = None
        frame = pd.DataFrame(
            {
                "symbol": ["SPY"],
                "ts": [dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC)],
                "as_of": [day],
                "ingested_at": [dt.datetime(2025, 1, 1, tzinfo=dt.UTC)],
                "close": [close],
            }
        )
        path = curated_path(store.root, BARS, "SPY", day)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(frame, schema=schema, preserve_index=False), path)
    return sessions


def test_the_horizon_return_is_the_log_return_from_the_origin_to_h_sessions_later(
    synthetic_store: SyntheticStore,
) -> None:
    sessions = _store_with_bars(synthetic_store)
    for horizon in (1, 5):
        origins = sessions[3:15]
        returns = horizon_returns(synthetic_store.settings, "SPY", origins, horizon=horizon)
        assert set(returns) == set(origins)
        for index, origin in zip(range(3, 15), origins, strict=True):
            expected = math.log(_close_at(index + horizon) / _close_at(index))
            assert returns[origin] == pytest.approx(expected, rel=1e-12)
    # And the sign is the return's: this series rises on net, with a sine wiggle in it, so both
    # signs occur and a sign-dropping implementation (an absolute value) would not pass.
    wide = horizon_returns(synthetic_store.settings, "SPY", sessions[:30], horizon=5)
    assert any(value < 0 for value in wide.values()) and any(value > 0 for value in wide.values())


def test_it_reads_no_further_than_dev_end(
    synthetic_store: SyntheticStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The standing argument, executable.

    An origin whose horizon runs past ``dev_end`` has no entry rather than a value read past the
    seal. The post-``dev_end`` bars are on disk at ``SEALED_CLOSE``, so a read that reached them
    would produce a return of about 20: the assertion on magnitude is what the seal looks like
    from outside. The two guards are the ``reads`` assertion (every ``load_panel`` call carries
    ``end == dev_end``) and the magnitude bound against ``SEALED_CLOSE``. The autouse holdout
    fixture is *not* a third: it fails only for ``end`` past the repository's own ``dev_end``
    (2025-12-31), and this store's seal is earlier, so it cannot fire here.
    """
    sessions = _store_with_bars(synthetic_store)
    settings = synthetic_store.settings
    dev_end = settings.splits.dev_end
    inside = [day for day in sessions if day <= dev_end]
    outside = [day for day in sessions if day > dev_end]
    assert len(outside) > 5 and len(inside) > 20, "the fixture must straddle the seal"

    reads: list[dict[str, object]] = []
    real_load_panel = evaluators.load_panel

    def recording(*args: object, **kwargs: object) -> pd.DataFrame:
        reads.append(dict(kwargs))
        return real_load_panel(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(evaluators, "load_panel", recording)

    last = inside[-1]
    late = {inside[-2], inside[-3], last, outside[0], outside[2]}
    for horizon in (5, 66):
        assert late.isdisjoint(horizon_returns(settings, "SPY", sorted(late), horizon=horizon))
    # The boundary is inclusive: the session at ``dev_end`` is a legitimate end of a horizon.
    boundary = inside[-1 - 5]
    returns = horizon_returns(settings, "SPY", [boundary], horizon=5)
    assert returns[boundary] == pytest.approx(
        math.log(_close_at(sessions.index(last)) / _close_at(sessions.index(boundary)))
    )
    # One session later and the horizon ends past the seal: absent.
    assert horizon_returns(settings, "SPY", [inside[-1 - 4]], horizon=5) == {}

    everything = horizon_returns(settings, "SPY", sessions, horizon=1)
    assert everything and max(everything) < last
    assert all(abs(value) < 0.1 for value in everything.values()), "a sealed close was read"
    assert reads and all(read["end"] == dev_end for read in reads), reads
    assert not any("allow_holdout" in read for read in reads), "the unlock was passed"


def test_a_missing_or_null_close_is_omitted_not_bridged(synthetic_store: SyntheticStore) -> None:
    """A gap poisons the returns spanning it. It is not interpolated over."""
    calendar = get_calendar(synthetic_store.settings)
    reference = sessions_between(calendar, dt.date(2023, 11, 1), dt.date(2024, 1, 31))
    gap, hole = reference[10], reference[20]
    sessions = _store_with_bars(synthetic_store, drop=[gap], null_close=[hole])
    origins = sessions[2:30]
    returns = horizon_returns(synthetic_store.settings, "SPY", origins, horizon=5)

    for index, origin in zip(range(2, 30), origins, strict=True):
        spans = {sessions[index], sessions[index + 5]}
        if spans & {gap, hole}:
            assert origin not in returns, f"{origin} was scored across a hole"
        else:
            assert origin in returns, f"{origin} lost its return for no reason"
    # The two ends of the horizon are what matter; a hole in the middle does not poison a
    # point-to-point return, so the origin one session before ``gap - 5`` is present.
    assert sessions[2] in returns and gap not in returns and hole not in returns


@pytest.mark.parametrize("horizon", [5, 22])
def test_every_comparable_origin_has_a_signed_return(
    synthetic_store: SyntheticStore, horizon: int
) -> None:
    """The premise behind ``decision_series`` raising rather than dropping.

    A comparable origin is one the QLIKE trial scores, so it has a realized variance, so every
    return in ``t+1..t+h`` is present -- the forward variance target is NaN if *any* of them is.
    ``horizon_returns`` needs only the two *endpoint* closes. The first condition implies the
    second, hence ``comparable("qlike") <= set(returns)`` for any symbol, and ``decision_series``
    can treat a comparable origin without a return as an inconsistency to surface.

    The inclusion is shown on a real ``evaluate`` over a store with two gaps -- one session with
    no bar, one with a null close, so both mechanisms by which a close goes missing -- and each
    gap is classified against the origins around it:

    * a **boundary** origin, whose window *ends* on the gap, loses its return (the endpoint is
      missing) and its target;
    * an **interior** origin, with the gap strictly inside its window, keeps its return -- both
      endpoints are present -- but has already lost its target, and with it its place in
      ``comparable``. That is the case that makes the inclusion strict, and the one a target
      built from endpoints alone would break.

    Every classified origin is first shown to have been *scored* with a NaN realized value, so
    its absence from ``comparable`` is the target's doing and not a skip upstream of it.
    """
    store = synthetic_store
    settings = store.settings
    calendar = get_calendar(settings)
    first, last = dt.date(2023, 1, 3), dt.date(2023, 12, 29)
    reference = sessions_between(calendar, first, last)
    # Far enough apart that the 66-session feature window after the first gap has closed before the
    # boundary origin of the second (200 - 22 > 92 + 66), and far enough in that the boundary
    # origin of the first (92 - 22) still has a full window behind it.
    absent_bar, null_close = reference[92], reference[200]
    sessions = _store_with_bars(
        store, first=first, last=last, drop=[absent_bar], null_close=[null_close]
    )
    assert sessions == reference

    panel = load_panel(
        BARS, end=settings.splits.dev_end, symbols=["SPY"], settings=settings
    ).assign(has_distribution=False)
    targets = build_targets(target_panel_for(panel, calendar), horizon=horizon, calendar=calendar)
    origins = sessions[66 : len(sessions) - horizon - 1]
    spy = Spy()
    evaluation = evaluate(
        panel,
        targets,
        origins,
        symbol="SPY",
        horizon=horizon,
        calendar=calendar,
        minimum_training=20,
        cohort=Cohort("seam-decisions", (spy,)),
    )
    returns = horizon_returns(settings, "SPY", origins, horizon=horizon)
    comparable = evaluation.comparable("qlike")

    # The premise itself, and that it is not an inclusion of the empty set in something.
    assert comparable <= set(returns)
    assert len(comparable) > 50
    realized = {result.origin: result.realized for result in evaluation.results}

    position = {day: index for index, day in enumerate(sessions)}
    for gap in (absent_bar, null_close):
        at = position[gap]
        boundary = sessions[at - horizon]
        interior = sessions[at - horizon + 1 : at]
        assert len(interior) == horizon - 1
        assert gap not in returns, "a gap is not an origin that can be scored"

        assert boundary in realized and math.isnan(realized[boundary]), "not scored, or not NaN"
        assert boundary not in returns, f"{boundary}: its horizon ends on the gap"
        assert boundary not in comparable

        for origin in interior:
            assert origin in realized and math.isnan(realized[origin]), f"{origin}: not scored"
            assert origin in returns, f"{origin}: both endpoints are present"
            assert origin not in comparable, f"{origin}: a gap inside the window left a target"


def test_an_origin_that_is_not_a_session_gets_no_entry(synthetic_store: SyntheticStore) -> None:
    sessions = _store_with_bars(synthetic_store)
    saturday = next(
        sessions[0] + dt.timedelta(days=n)
        for n in range(1, 10)
        if (sessions[0] + dt.timedelta(days=n)).weekday() == 5
    )
    assert horizon_returns(synthetic_store.settings, "SPY", [saturday, sessions[0]], horizon=1) == {
        sessions[0]: pytest.approx(math.log(_close_at(1) / _close_at(0)))
    }


def test_a_horizon_below_one_is_refused(synthetic_store: SyntheticStore) -> None:
    _store_with_bars(synthetic_store)
    with pytest.raises(ValueError, match="horizon must be at least 1"):
        horizon_returns(synthetic_store.settings, "SPY", [dt.date(2023, 11, 1)], horizon=0)


# ---- no signed forward return reaches a fit ------------------------------------------------


def _forward_returns(toy: Toy) -> tuple[dict[dt.date, float], list[dt.date]]:
    """``log(close_{t+h} / close_t)`` per session of the toy's SPY bars, and those sessions."""
    calendar = get_calendar(toy.settings)
    bars = toy.panel[toy.panel["symbol"] == "SPY"]
    days = list(as_of_dates(bars))
    sessions = sessions_between(calendar, min(days), max(days))
    close = pd.Series(bars["close"].astype("float64").to_list(), index=days).reindex(sessions)
    found: dict[dt.date, float] = {}
    for index in range(len(sessions) - toy.horizon):
        start, finish = close.iloc[index], close.iloc[index + toy.horizon]
        if not (pd.isna(start) or pd.isna(finish)):
            found[sessions[index]] = math.log(finish / start)
    return found, sessions


def _numbers(view_frame: pd.DataFrame | pd.Series) -> np.ndarray:
    """Every numeric cell, as one flat float array.

    A row of the feature table is an ``object`` Series (it holds dates beside floats), so
    selecting numeric *dtypes* would see none of it; each cell is coerced instead, and what is not
    a number -- a date, a string, a flag -- becomes NaN and matches nothing.
    """
    values = view_frame.to_numpy().ravel()
    return pd.to_numeric(pd.Series(values, dtype="object"), errors="coerce").to_numpy(
        dtype="float64", na_value=np.nan
    )


def _unknowable_forward_returns_in(
    cells: np.ndarray,
    forward: dict[dt.date, float],
    sessions: list[dt.date],
    origin: dt.date,
    horizon: int,
) -> list[dt.date]:
    """Base sessions whose signed h-day return is among ``cells`` but unknowable at ``origin``.

    A base session ``t`` has its return knowable once ``t + h <= origin``; any later one is the
    future. Equality is to 1e-12 absolute, which a coincidence among float features does not reach.
    """
    position = {day: index for index, day in enumerate(sessions)}
    found = []
    for day, value in forward.items():
        if position[day] + horizon <= position[origin]:
            continue  # completed by the origin: knowable, and allowed to be in a feature
        if abs(value) > 1e-6 and bool(np.isclose(cells, value, rtol=0.0, atol=1e-12).any()):
            found.append(day)
    return found


def _view_cells(
    view_training: pd.DataFrame, view_row: pd.Series, view_returns: pd.Series
) -> np.ndarray:
    return np.concatenate(
        [_numbers(view_training), _numbers(view_row), view_returns.to_numpy(dtype="float64")]
    )


def test_no_horizon_return_reaches_a_fit() -> None:
    """A Spy contestant records every ``TrainingView``; none carries a signed forward return.

    This is what separates a scoring read from a look-ahead. Checked on the numbers rather than on
    a column name: ``"horizon_return" not in columns`` passes for any name nobody thought of, so the
    check is that no cell of the training frame, the origin's row or the return series equals the
    signed return of any horizon not yet complete at the origin.
    """
    toy = make_toy(5)
    spy = Spy()
    run_cohort(toy, Cohort("seam-decisions", (spy,)))
    assert spy.views

    forward, sessions = _forward_returns(toy)
    # Non-vacuous: there are many distinct signed returns to look for, and both signs.
    assert len(forward) > 100 and min(forward.values()) < 0 < max(forward.values())

    for view in spy.views:
        assert "horizon_return" not in view.training.columns
        assert "horizon_return" not in view.row.index
        cells = _view_cells(view.training, view.row, view.returns)
        assert cells.size > 0
        leaked = _unknowable_forward_returns_in(cells, forward, sessions, view.origin, 5)
        assert not leaked, f"{view.origin}: the view carries the signed return of {leaked[:3]}"
        # The one return series a contestant gets ends at the origin.
        assert max(view.returns.index) <= view.origin


def test_the_leak_detector_can_fail() -> None:
    """The red case for the test above: plant the return in a view and watch it be found.

    Without this the detector is a check nobody has seen fire (R32), and "no cell equals the
    forward return" is exactly the sentence that is true of a detector with a bug in it.
    """
    toy = make_toy(5)
    spy = Spy()
    run_cohort(toy, Cohort("seam-decisions", (spy,)))
    forward, sessions = _forward_returns(toy)
    view = spy.views[len(spy.views) // 2]
    value = forward[view.origin]
    assert abs(value) > 1e-6

    # Under a column name nobody would think to forbid ...
    planted = view.training.assign(tomorrows_news=value)
    cells = _view_cells(planted, view.row, view.returns)
    assert _unknowable_forward_returns_in(cells, forward, sessions, view.origin, 5) == [view.origin]
    # ... and in the origin's own row.
    row = view.row.copy()
    row["tomorrows_news"] = value
    cells = _view_cells(view.training, row, view.returns)
    assert _unknowable_forward_returns_in(cells, forward, sessions, view.origin, 5) == [view.origin]
    # A forward return that has already completed by the origin is knowable and is not flagged.
    completed = [
        day
        for day in forward
        if sessions.index(day) + 5 <= sessions.index(view.origin) and abs(forward[day]) > 1e-6
    ]
    assert completed
    known = view.training.assign(prior=forward[completed[0]])
    cells = _view_cells(known, view.row, view.returns)
    assert completed[0] not in _unknowable_forward_returns_in(
        cells, forward, sessions, view.origin, 5
    )


# ---- the package's own boundaries ----------------------------------------------------------


def _package_sources() -> list[Path]:
    return sorted(Path(evaluators.__file__).parent.rglob("*.py"))


def test_the_package_is_found_so_the_scan_below_is_not_vacuous() -> None:
    assert _package_sources(), "no evaluator module found to scan"


@pytest.mark.parametrize("module", _package_sources(), ids=lambda p: p.name)
def test_the_evaluators_never_unlock_the_holdout(module: Path) -> None:
    """``horizon_returns`` is a scoring read to ``dev_end`` and has no business near the key.

    The same scan ``tools/`` is held to (``allow_holdout`` as keyword, name or attribute, and the
    environment key outside a docstring), applied to every file in the package -- derived by glob,
    so a module added next sprint is covered the day it is written.
    """
    found = _unlocks(module.read_text(encoding="utf-8"))
    assert not found, f"{module.name}: {found}; the holdout is Phase 6's single read"


def test_settings_is_what_horizon_returns_is_handed(synthetic_store: SyntheticStore) -> None:
    """A guard on the guard: a ``Settings`` with a *later* ``dev_end`` is refused by the store, so
    ``horizon_returns`` cannot be used to move the seal by handing it one."""
    _store_with_bars(synthetic_store)
    # One day past the repository's own seal, derived so it cannot go stale when the seal rolls.
    later = load_settings().splits.dev_end + dt.timedelta(days=1)
    moved = Settings(
        universe=synthetic_store.settings.universe,
        exogenous=synthetic_store.settings.exogenous,
        calendar=synthetic_store.settings.calendar,
        splits=Splits(dev_end=later, holdout_start=later + dt.timedelta(days=1)),
        storage_root=synthetic_store.settings.storage_root,
    )
    with pytest.raises(HoldoutViolation, match="later than the repository's configured dev_end"):
        horizon_returns(moved, "SPY", [dt.date(2023, 11, 1)], horizon=1)


# ---------------------------------------------------------------------------
# coverage -- Kupiec and Christoffersen, and the stride they refuse below
# ---------------------------------------------------------------------------


def _hits(length: int, at: Sequence[int]) -> list[bool]:
    return [index in set(at) for index in range(length)]


# Ten breaches in 251 origins either way, so the unconditional count is the same and only the
# arrangement differs: one run of ten against ten singletons 25 apart.
SPREAD = _hits(251, range(10, 251, 25))
CLUSTERED = _hits(251, range(100, 110))


def test_coverage_refuses_where_the_stride_is_shorter_than_the_horizon() -> None:
    result = coverage([True, False, False], alpha=0.01, horizon=22, stride=5)
    assert result.refusal is not None
    assert "stride 5 < horizon 22" in result.refusal and "overlap" in result.refusal
    assert result.kupiec_p is None and result.christoffersen_p is None
    # What was counted is still reported: only the p-values are withheld.
    assert result.breaches == 1 and result.expected == pytest.approx(0.03)


@pytest.mark.parametrize(("horizon", "stride"), [(5, 5), (5, 6), (22, 22), (22, 66), (1, 1)])
def test_coverage_does_not_refuse_where_the_stride_reaches_the_horizon(
    horizon: int, stride: int
) -> None:
    result = coverage(SPREAD, alpha=0.01, horizon=horizon, stride=stride)
    assert result.refusal is None
    assert result.kupiec_p is not None and result.christoffersen_p is not None


@pytest.mark.parametrize(("horizon", "stride"), [(5, 4), (22, 5), (22, 21), (2, 1)])
def test_coverage_refuses_one_session_short_of_the_horizon_too(horizon: int, stride: int) -> None:
    refusal = coverage(SPREAD, alpha=0.01, horizon=horizon, stride=stride).refusal
    assert refusal is not None and f"stride {stride} < horizon {horizon}" in refusal


@pytest.mark.parametrize(
    ("length", "breaches"), [(251, 0), (251, 1), (251, 2), (251, 10), (100, 3), (20, 0), (20, 20)]
)
def test_kupiec_is_the_binomial_likelihood_ratio(length: int, breaches: int) -> None:
    """Kupiec's statistic from the binomial log-likelihood, not from the closed form it rewrites.

    ``LR = -2 [ log L(alpha) - log L(x / n) ]`` with ``L`` the binomial pmf; the coefficient
    cancels, so ``binom.logpmf`` supplies it independently of ``coverage``'s own terms. The zero-
    and all-breach rows are the boundaries where the maximised likelihood is ``1``.
    """
    alpha = 0.01
    hits = _hits(length, range(breaches))
    expected = float(
        stats.chi2.sf(
            -2.0
            * (
                stats.binom.logpmf(breaches, length, alpha)
                - stats.binom.logpmf(breaches, length, breaches / length)
            ),
            1,
        )
    )
    result = coverage(hits, alpha=alpha, horizon=1, stride=1)
    assert result.breaches == breaches
    assert result.expected == pytest.approx(alpha * length)
    assert result.kupiec_p == pytest.approx(expected, rel=1e-9)


def test_zero_breaches_gives_a_defined_kupiec_statistic() -> None:
    # Review Focus 2. Zero breaches is ordinary at alpha = 0.01 over ~251 origins (expectation
    # 2.5), and the unrestricted likelihood there is ``0 * log(0)``. It must not be evaluated, and
    # no warning may be raised: ``filterwarnings = ["error"]`` turns one into a failure.
    result = coverage([False] * 251, alpha=0.01, horizon=1, stride=5)
    assert result.breaches == 0
    assert result.kupiec_p is not None and math.isfinite(result.kupiec_p)
    # By hand: LR = -2 * 251 * log(0.99), a little over 5 against chi-squared(1).
    assert result.kupiec_p == pytest.approx(
        float(stats.chi2.sf(-2.0 * 251 * math.log(0.99), 1)), rel=1e-12
    )
    assert 0.02 < result.kupiec_p < 0.03
    assert result.christoffersen_p is not None and math.isfinite(result.christoffersen_p)


def test_a_statistic_that_rounds_just_below_zero_is_a_p_value_of_one() -> None:
    # 3 / 10 is the maximum-likelihood level and ``0.1 + 0.2`` is the same number to rounding, so
    # the likelihood ratio is zero analytically and -1.8e-15 in floating point.
    result = coverage(_hits(10, [0, 1, 2]), alpha=0.1 + 0.2, horizon=1, stride=1)
    assert result.kupiec_p == 1.0


def test_all_breaches_gives_defined_statistics() -> None:
    result = coverage([True] * 30, alpha=0.025, horizon=5, stride=5)
    assert result.breaches == 30
    assert result.kupiec_p is not None and 0.0 <= result.kupiec_p < 1e-6
    assert result.christoffersen_p is not None and math.isfinite(result.christoffersen_p)


def _markov_log_likelihood(hits: Sequence[bool], p01: float, p11: float) -> float:
    """The log-likelihood of ``hits[1:]`` given ``hits[0]``, one factor per step.

    Written as a loop over the sequence, where ``coverage`` multiplies counts, so a miscounted
    transition cannot be repeated here.
    """
    total = 0.0
    for before, now in zip(hits, hits[1:], strict=False):
        p = p11 if before else p01
        total += math.log(p if now else 1.0 - p)
    return total


def _christoffersen_by_the_loop(hits: Sequence[bool]) -> float:
    pairs = list(zip(hits, hits[1:], strict=False))
    from_clear = [now for before, now in pairs if not before]
    from_breach = [now for before, now in pairs if before]
    p01 = sum(from_clear) / len(from_clear)
    p11 = sum(from_breach) / len(from_breach)
    p = sum(now for _, now in pairs) / len(pairs)
    unrestricted = _markov_log_likelihood(hits, p01, p11)
    restricted = _markov_log_likelihood(hits, p, p)
    return float(stats.chi2.sf(-2.0 * (restricted - unrestricted), 1))


@pytest.mark.parametrize("hits", [CLUSTERED, SPREAD, _hits(60, [3, 4, 20, 41, 42, 43])])
def test_christoffersen_is_the_markov_likelihood_ratio(hits: list[bool]) -> None:
    result = coverage(hits, alpha=0.01, horizon=1, stride=1)
    assert result.christoffersen_p == pytest.approx(_christoffersen_by_the_loop(hits), rel=1e-9)


def test_christoffersen_detects_clustered_breaches() -> None:
    """Independence is rejected for a run of consecutive breaches, not for the same count spread.

    Both series have ten breaches in 251 origins, so Kupiec cannot tell them apart -- its two
    p-values are equal -- and that equality is what makes the Christoffersen contrast the whole
    difference between the two tests.
    """
    clustered = coverage(CLUSTERED, alpha=0.04, horizon=1, stride=1)
    spread = coverage(SPREAD, alpha=0.04, horizon=1, stride=1)
    assert clustered.christoffersen_p is not None and spread.christoffersen_p is not None
    assert clustered.christoffersen_p < 0.001
    assert spread.christoffersen_p > 0.05  # measured 0.36: no run, so nothing to reject
    assert clustered.kupiec_p == pytest.approx(spread.kupiec_p, rel=1e-12)
    assert clustered.breaches == spread.breaches == 10


def test_no_breach_pattern_is_a_cluster() -> None:
    # With nothing to cluster the independence statistic is zero, so its p-value is one -- which
    # says the test could not reject, not that independence was shown.
    assert coverage([False] * 251, alpha=0.01, horizon=1, stride=1).christoffersen_p == 1.0
    assert coverage([True] * 30, alpha=0.01, horizon=1, stride=1).christoffersen_p == 1.0


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.01, float("nan")])
def test_coverage_refuses_a_level_outside_the_open_unit_interval(alpha: float) -> None:
    with pytest.raises(ValueError, match=r"alpha must lie in the open interval"):
        coverage(SPREAD, alpha=alpha, horizon=1, stride=1)


def test_coverage_refuses_a_horizon_or_stride_below_one() -> None:
    with pytest.raises(ValueError, match="horizon must be at least 1"):
        coverage(SPREAD, alpha=0.01, horizon=0, stride=5)
    with pytest.raises(ValueError, match="stride must be at least 1"):
        coverage(SPREAD, alpha=0.01, horizon=1, stride=0)


@pytest.mark.parametrize("hits", [[], [True]])
def test_coverage_withholds_p_values_it_cannot_compute_and_says_why(hits: list[bool]) -> None:
    # Christoffersen counts transitions, so it needs two origins; Kupiec is withheld with it
    # rather than reported beside a refusal for its sibling.
    result = coverage(hits, alpha=0.01, horizon=1, stride=1)
    assert result.refusal is not None and "fewer than two origins" in result.refusal
    assert result.kupiec_p is None and result.christoffersen_p is None
    assert result.breaches == sum(hits)


def test_the_stride_refusal_is_checked_before_the_sample_size() -> None:
    # An overlapping window is the more fundamental reason; say that one.
    refusal = coverage([True], alpha=0.01, horizon=22, stride=5).refusal
    assert refusal is not None and "overlap" in refusal


def test_a_coverage_result_is_frozen() -> None:
    result = coverage(SPREAD, alpha=0.01, horizon=1, stride=1)
    with pytest.raises(AttributeError):
        result.breaches = 0  # type: ignore[misc]
