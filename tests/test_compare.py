"""The comparison layer: Diebold-Mariano, the model confidence set, SPA, and the sink (US-3.4).

The statistics here are tested against **closed forms and independent implementations**, not
against plausible-looking output. Three oracles carry most of the weight:

* a constant-plus-alternating loss differential, ``d_i = c + (-1)^i`` with ``n`` even, whose
  centred autocovariances are exactly ``gamma_k = (-1)^k (n - k) / n`` -- so the rectangular and
  the Bartlett variances, the non-positive-definite case included, are arithmetic;
* ``scipy.stats.ttest_1samp``, which the headline statistic must equal **exactly** at ``q = 0``
  (Harvey's factor at ``h_eff = 1`` is ``sqrt((n - 1) / n)``, which undoes the ``1/n`` in
  ``gamma_0``);
* a brute-force count of which returns two origins share.

``filterwarnings = ["error"]`` applies throughout, so a numpy ``RuntimeWarning`` from a square
root of a negative variance fails the test that provoked it.

Nothing here reads the real store or appends to ``data/trials.jsonl``: every ledger lives under a
``tmp_path``, and the suite never sets ``allow_holdout``.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import json
import math
import re
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

import volab.compare.__main__ as compare_cli
import volab.compare.cohorts as cohorts_module
import volab.walkforward as wf
import volab.walkforward.__main__ as walkforward_cli
from volab.compare import MC_ERROR_CEILING_PERCENT, analyse, describe, multiple, series_source
from volab.compare.cohorts import (
    COHORTS,
    MULTISTEP,
    SHOOTOUT,
    MonteCarlo,
    answering,
    monte_carlo_error,
    multistep_members,
)
from volab.compare.dm import (
    Column,
    bartlett_lag,
    diebold_mariano,
    harvey_factor,
    newey_west_lag,
    overlap_lag,
)
from volab.compare.multiple import (
    BOOTSTRAP,
    REPS,
    SEED,
    SIZES,
    block_length,
    effective_observations,
    model_confidence_set,
    politis_white,
    superior_predictive_ability,
)
from volab.compare.series import LossSeries, loss_series
from volab.compare.sink import COMPARISON_LEDGER, Comparison, JsonlSink, Sink
from volab.config import load_settings
from volab.features.targets import HORIZONS
from volab.models.base import (
    Cohort,
    Domain,
    Forecast,
    Forecaster,
    Measurement,
    Model,
    Sampling,
    TrainingView,
)
from volab.models.garch import EGARCH11_SKEWT, GARCH11_SKEWT, GARCH_MODELS, GJR11_SKEWT
from volab.walkforward import (
    BASELINE_COHORT,
    BASELINE_COHORT_NAME,
    LOSSES,
    TRIAL_LEDGER,
    Evaluation,
    OriginResult,
    Trial,
    append_trials,
    count_trials,
)

# ---------------------------------------------------------------------------
# series.py -- the one way losses enter
# ---------------------------------------------------------------------------

ORIGINS = tuple(dt.date(2024, 3, 1) + dt.timedelta(days=7 * i) for i in range(8))


def _evaluation(
    forecasts: dict[str, list[float]],
    *,
    realized: list[float] | None = None,
    every: int = 5,
    horizon: int = 1,
    cohort: str = "unit",
) -> Evaluation:
    """A hand-built ``Evaluation``: no harness, so the expected losses are arithmetic."""
    names = tuple(forecasts)
    count = len(next(iter(forecasts.values())))
    truth = realized if realized is not None else [1.0 + 0.1 * i for i in range(count)]
    results = tuple(
        OriginResult(
            origin=ORIGINS[i],
            realized=truth[i],
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


def test_every_loss_series_is_rectangular_and_on_comparable_origins() -> None:
    """The seam exists so a ragged matrix can never reach MCS.

    One contestant refuses an origin (a NaN, as ``ModelSkip`` writes it) and another forecasts a
    non-positive variance at a different one, where QLIKE -- and only QLIKE -- is undefined. So
    the two losses' comparable sets differ, and each series must sit exactly on its own.
    """
    nan = float("nan")
    evaluation = _evaluation(
        {
            "a": [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7],
            "b": [0.9, nan, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6],
            "c": [1.2, 1.3, 1.4, -0.5, 1.6, 1.7, 1.8, 1.9],
        }
    )
    for loss in LOSSES:
        series = loss_series(evaluation, loss, cohort="unit")
        assert list(series.origins) == sorted(evaluation.comparable(loss))
        assert set(series.values) == {"a", "b", "c"}
        assert {len(column) for column in series.values.values()} == {len(series.origins)}, (
            "a ragged matrix reached the statistics"
        )
    qlike = loss_series(evaluation, "qlike", cohort="unit")
    mse = loss_series(evaluation, "mse", cohort="unit")
    assert ORIGINS[1] not in qlike.origins and ORIGINS[1] not in mse.origins, "b's refusal"
    assert ORIGINS[3] not in qlike.origins and ORIGINS[3] in mse.origins, (
        "a non-positive forecast removes the origin from QLIKE only"
    )
    assert len(qlike.origins) == 6 and len(mse.origins) == 7


def test_a_loss_series_holds_the_loss_of_each_forecast_in_origin_order() -> None:
    evaluation = _evaluation(
        {"a": [1.0, 2.0, 3.0], "b": [2.0, 2.0, 2.0]}, realized=[1.5, 1.5, 4.0], every=5, horizon=22
    )
    series = loss_series(evaluation, "mse", cohort="unit")
    assert series.origins == ORIGINS[:3]
    assert series.values["a"] == (0.25, 0.25, 1.0)
    assert series.values["b"] == (0.25, 0.25, 4.0)
    qlike = loss_series(evaluation, "qlike", cohort="unit")
    assert qlike.values["a"][1] == LOSSES["qlike"](1.5, 2.0)
    assert (series.symbol, series.horizon, series.loss, series.cohort, series.stride) == (
        "SPY",
        22,
        "mse",
        "unit",
        5,
    )


def test_a_loss_series_refuses_a_cohort_label_the_evaluation_does_not_carry() -> None:
    """The label reaches the sink, and a wrong one is a comparison filed under the wrong name."""
    evaluation = _evaluation({"a": [1.0, 2.0], "b": [2.0, 1.0]}, cohort="real")
    with pytest.raises(ValueError, match="cohort"):
        loss_series(evaluation, "mse", cohort="other")
    with pytest.raises(ValueError, match="loss"):
        loss_series(evaluation, "nonsense", cohort="real")


def test_a_loss_series_over_an_empty_evaluation_is_empty_not_an_error() -> None:
    empty = Evaluation(
        symbol="SPY",
        horizon=1,
        results=(),
        features=pd.DataFrame(),
        skipped=(),
        requested=0,
        every=5,
        contestants=("a", "b"),
        cohort="unit",
    )
    series = loss_series(empty, "mse", cohort="unit")
    assert series.origins == () and series.values == {"a": (), "b": ()}


# ---------------------------------------------------------------------------
# dm.py -- the overlap, the lags, the Harvey factor
# ---------------------------------------------------------------------------


def _shared_returns(horizon: int, stride: int, strides_apart: int) -> int:
    """How many returns two origins ``strides_apart`` strides apart both enter their target with.

    Counted directly from the target's definition -- the sum of ``r[t+1 .. t+h]`` -- by
    intersecting the two index sets, so it does not restate the formula under test.
    """
    first = set(range(1, horizon + 1))
    second = set(range(strides_apart * stride + 1, strides_apart * stride + horizon + 1))
    return len(first & second)


def test_the_overlap_lag_is_derived_from_the_horizon_and_the_stride() -> None:
    """The exact table the design records, and then the derivation behind it."""
    assert {(h, s): overlap_lag(h, s) for h, s in [(1, 5), (5, 5), (22, 5), (66, 5)]} == {
        (1, 5): 0,
        (5, 5): 0,
        (22, 5): 4,
        (66, 5): 13,
    }
    assert overlap_lag(22, 1) == 21 and overlap_lag(66, 1) == 65
    # h=5 at stride 5 is the intuitive wrong answer: the windows t+1..t+5 and t+6..t+10 are
    # disjoint, so there is no overlap at all.
    assert _shared_returns(5, 5, 1) == 0


@pytest.mark.parametrize("stride", [1, 2, 3, 5, 7, 10])
@pytest.mark.parametrize("horizon", [1, 2, 5, 6, 11, 22, 23, 66, 67])
def test_the_overlap_lag_is_the_largest_separation_that_still_shares_a_return(
    horizon: int, stride: int
) -> None:
    """``q`` is the last ``m`` with a shared return and ``q + 1`` is the first without one."""
    q = overlap_lag(horizon, stride)
    assert q == 0 or _shared_returns(horizon, stride, q) > 0
    assert _shared_returns(horizon, stride, q + 1) == 0
    assert all(_shared_returns(horizon, stride, m) > 0 for m in range(1, q + 1))


def test_the_overlap_lag_refuses_a_non_positive_horizon_or_stride() -> None:
    for horizon, stride in [(0, 5), (-1, 5), (5, 0), (5, -2)]:
        with pytest.raises(ValueError):
            overlap_lag(horizon, stride)


def test_the_newey_west_lag_is_the_automatic_truncation() -> None:
    """``floor(4 (n/100)^(2/9))``: 4 at every sample size the recorded run has."""
    assert [newey_west_lag(n) for n in (251, 250, 247, 238)] == [4, 4, 4, 4]
    assert newey_west_lag(100) == 4
    assert newey_west_lag(1000) == math.floor(4 * 10 ** (2 / 9)) == 6
    assert bartlett_lag(238, 13) == 13, "the Bartlett lag is never below the overlap order"
    assert bartlett_lag(247, 4) == 4
    assert bartlett_lag(251, 0) == 4, "at q = 0 the automatic truncation is what it falls back to"


def test_the_harvey_correction_uses_sampling_units_not_the_daily_horizon() -> None:
    """The specific wrong answer R92 exists to prevent.

    At h=66, stride 5, n=238 the daily horizon gives a factor of 0.725 and ``h_eff = q + 1 = 14``
    gives 0.943. Passing 66 to a parameter that means "horizon in units of the loss series" is the
    default that looks fine and is wrong -- so the assertion is on what ``diebold_mariano``
    *used*, reached through ``q`` as the entry point derives it.
    """
    n, horizon, stride = 238, 66, 5
    q = overlap_lag(horizon, stride)
    assert harvey_factor(n, q + 1) == pytest.approx(math.sqrt((n + 1 - 28 + 14 * 13 / n) / n))
    assert round(harvey_factor(n, q + 1), 3) == 0.943
    assert round(harvey_factor(n, horizon), 3) == 0.725, "the wrong answer, for contrast"

    d = [math.sin(i) for i in range(n)]
    result = diebold_mariano([x + 1.0 for x in d], [1.0] * n, q=q)
    assert result.h_eff == 14
    assert result.harvey is not None and round(result.harvey, 3) == 0.943


def test_the_harvey_factor_at_one_step_undoes_the_one_over_n_in_gamma_zero() -> None:
    """``(n + 1 - 2 + 0) / n = (n - 1) / n``: the identity the t-test check below rests on."""
    for n in (5, 50, 251):
        assert harvey_factor(n, 1) == pytest.approx(math.sqrt((n - 1) / n))


def test_the_harvey_factor_refuses_a_sample_too_short_for_it() -> None:
    with pytest.raises(ValueError):
        harvey_factor(3, 20)
    with pytest.raises(ValueError):
        harvey_factor(10, 0)


# ---------------------------------------------------------------------------
# dm.py -- the statistic, against known answers
# ---------------------------------------------------------------------------


def _alternating(n: int, c: float, base: float = 5.0) -> tuple[list[float], list[float]]:
    """Losses whose differential is exactly ``c + (-1)^i``, in binary floating point.

    ``base`` and ``c`` are dyadic, so ``(base + c + s) - base`` has no rounding and the centred
    differential is exactly ``+-1``: the autocovariances below are then exact arithmetic.
    """
    right = [base] * n
    left = [base + c + (1.0 if i % 2 == 0 else -1.0) for i in range(n)]
    return left, right


def _gamma(n: int, k: int) -> Fraction:
    return Fraction((-1) ** k * (n - k), n)


def test_the_headline_statistic_at_q_zero_is_exactly_the_one_sample_t_statistic() -> None:
    """An independent implementation: ``scipy.stats.ttest_1samp``.

    With ``q = 0`` the variance is ``gamma_0 = (n - 1) s^2 / n`` and Harvey's factor at
    ``h_eff = 1`` is ``sqrt((n - 1) / n)``; the product is ``d_bar / (s / sqrt(n))`` -- Student's
    t -- and the reference is ``t(n - 1)``. So statistic and p-value must both match scipy's.
    """
    d = [math.sin(1.7 * i) + 0.3 * math.cos(0.4 * i) + 0.25 for i in range(60)]
    result = diebold_mariano([1.0 + x for x in d], [1.0] * 60, q=0)
    reference = stats.ttest_1samp(d, 0.0)
    assert result.headline.statistic == pytest.approx(reference.statistic, rel=1e-12)
    assert result.headline.p_value == pytest.approx(reference.pvalue, rel=1e-9)
    assert result.headline.refusal is None


def test_the_rectangular_hac_and_the_bartlett_hac_match_the_closed_form() -> None:
    """``d_i = c + (-1)^i``, n = 20: ``gamma_k = (-1)^k (20 - k) / 20`` exactly.

    Rectangular at q=2: ``V = gamma_0 + 2 gamma_1 + 2 gamma_2 = 0.9``. Bartlett at
    ``L = max(2, floor(4 (20/100)^(2/9))) = 2``: ``V = gamma_0 + 2 (2/3 gamma_1 + 1/3 gamma_2)``
    ``= 1/3``. The headline carries Harvey at ``h_eff = 3`` against ``t(19)``; the Bartlett column
    carries no Harvey factor and a standard normal.
    """
    n, c, q = 20, 0.5, 2
    left, right = _alternating(n, c)
    result = diebold_mariano(left, right, q=q)

    v_rect = _gamma(n, 0) + 2 * _gamma(n, 1) + 2 * _gamma(n, 2)
    assert v_rect == Fraction(9, 10)
    v_bartlett = _gamma(n, 0) + 2 * (Fraction(2, 3) * _gamma(n, 1) + Fraction(1, 3) * _gamma(n, 2))
    assert v_bartlett == Fraction(1, 3)

    assert result.mean_difference == pytest.approx(c)
    assert result.q == 2 and result.bartlett_lag == 2 and result.h_eff == 3
    assert result.headline.variance == pytest.approx(float(v_rect), rel=1e-12)
    factor = math.sqrt((n + 1 - 2 * 3 + 3 * 2 / n) / n)
    statistic = c / math.sqrt(float(v_rect) / n) * factor
    assert result.harvey == pytest.approx(factor)
    assert result.headline.statistic == pytest.approx(statistic, rel=1e-12)
    assert result.headline.p_value == pytest.approx(2 * stats.t.sf(abs(statistic), n - 1))

    assert result.bartlett.variance == pytest.approx(float(v_bartlett), rel=1e-12)
    z = c / math.sqrt(float(v_bartlett) / n)
    assert result.bartlett.statistic == pytest.approx(z, rel=1e-12), "no Harvey factor"
    assert result.bartlett.p_value == pytest.approx(2 * stats.norm.sf(abs(z)))


def test_the_bartlett_lag_is_the_larger_of_the_overlap_and_the_automatic_truncation() -> None:
    """At n=238 and q=13 the lag is 13; at q=0 it is 4 -- read off the result, not recomputed."""
    d = [math.sin(0.9 * i) for i in range(238)]
    long_overlap = diebold_mariano([1.0 + x for x in d], [1.0] * 238, q=13)
    none = diebold_mariano([1.0 + x for x in d], [1.0] * 238, q=0)
    assert long_overlap.bartlett_lag == 13 and none.bartlett_lag == 4


def test_a_non_positive_rectangular_hac_variance_is_refused_before_the_square_root() -> None:
    """The rectangular estimator is not positive semi-definite, and this is a real case.

    Alternating +-1 at q=1: ``V = 1 - 2 (n - 1) / n = (2 - n) / n < 0``. A ``sqrt`` of that is a
    numpy ``RuntimeWarning`` (a failing test here) or a ``ValueError`` from ``math.sqrt``, so the
    test fails for an implementation that takes the root first. The Bartlett column on the same
    data is positive semi-definite and must still be answered.
    """
    n = 20
    left, right = _alternating(n, 0.5)
    result = diebold_mariano(left, right, q=1)
    assert result.headline.statistic is None and result.headline.p_value is None
    assert result.headline.variance == pytest.approx((2 - n) / n)
    assert result.headline.refusal is not None
    assert "not positive" in result.headline.refusal
    assert "identical" not in result.headline.refusal
    assert result.bartlett.refusal is None
    assert result.bartlett.statistic is not None and result.bartlett.variance is not None
    assert result.bartlett.variance > 0


def test_an_identical_pair_of_contestants_is_refused_rather_than_divided_by_zero() -> None:
    """``V == 0``: its own reason, distinct from the non-positive one, on both columns."""
    losses = [1.0, 2.5, 0.5, 3.0, 1.5, 2.0]
    result = diebold_mariano(losses, list(losses), q=0)
    for column in (result.headline, result.bartlett):
        assert column.statistic is None and column.p_value is None
        assert column.refusal is not None and "identical" in column.refusal
        assert "not positive" not in column.refusal


def test_a_constant_nonzero_differential_is_refused_not_explosive() -> None:
    """A constant ``d`` minus its own float mean can leave ~1e-17, so ``V`` comes out as a tiny
    positive number and a naive ``V == 0`` test lets a statistic of order 1e15 through. The
    constant is detected on ``d`` itself, to a tolerance."""
    right = [0.1 * i + 0.03 for i in range(40)]
    left = [x + 0.3 for x in right]
    assert len({a - b for a, b in zip(left, right, strict=True)}) > 1, (
        "the fixture must carry the float residue this test is about"
    )
    result = diebold_mariano(left, right, q=0)
    for column in (result.headline, result.bartlett):
        assert column.statistic is None
        assert column.refusal is not None and "identical" in column.refusal


def test_a_sample_no_longer_than_the_overlap_is_refused_on_both_columns() -> None:
    result = diebold_mariano([1.0, 2.0, 4.0], [1.5, 1.0, 2.0], q=5)
    assert result.headline.refusal is not None and result.bartlett.refusal is not None
    one = diebold_mariano([1.0], [2.0], q=0)
    assert one.headline.refusal is not None and one.bartlett.refusal is not None
    with pytest.raises(ValueError, match="length"):
        diebold_mariano([1.0, 2.0], [1.0], q=0)


def test_a_non_finite_loss_is_a_recorded_refusal_not_a_warning() -> None:
    result = diebold_mariano([1.0, float("inf"), 3.0, 4.0], [1.0, 2.0, 3.0, 5.0], q=0)
    assert result.headline.refusal is not None and "finite" in result.headline.refusal
    assert result.bartlett.refusal is not None


def test_the_sign_convention_is_left_minus_right_so_a_negative_statistic_favours_left() -> None:
    d = [-0.5 + 0.1 * math.sin(i) for i in range(50)]
    result = diebold_mariano([1.0 + x for x in d], [1.0] * 50, q=0)
    assert result.headline.statistic is not None and result.headline.statistic < 0
    assert result.headline.verdict() == -1, "left is significantly better"
    flipped = diebold_mariano([1.0] * 50, [1.0 + x for x in d], q=0)
    assert flipped.headline.verdict() == 1


def test_a_disagreement_between_the_two_columns_is_flagged_and_an_agreement_is_not() -> None:
    """Both statistics share the numerator ``d_bar`` over a positive denominator, so their
    signs can never differ: the disagreement that exists is in the 5% *verdict*. Red case: a
    slowly varying differential (two full sine periods around a mean of 0.2), whose ``q = 0``
    sample variance ignores the autocorrelation the Bartlett estimator at ``L = 4`` sees."""
    n = 100
    slow = [0.2 + math.sin(2 * math.pi * i / 50) for i in range(n)]
    result = diebold_mariano([1.0 + x for x in slow], [1.0] * n, q=0)
    assert result.headline.p_value is not None and result.headline.p_value < 0.05
    assert result.bartlett.p_value is not None and result.bartlett.p_value > 0.05
    assert result.disagree is True

    steady = [1.0 + 0.1 * (-1) ** i for i in range(n)]
    agree = diebold_mariano([1.0 + x for x in steady], [1.0] * n, q=0)
    assert agree.headline.verdict() == agree.bartlett.verdict() == 1
    assert agree.disagree is False


def test_a_refusal_is_not_a_disagreement() -> None:
    left, right = _alternating(20, 0.5)
    refused_headline = diebold_mariano(left, right, q=1)
    assert refused_headline.headline.verdict() is None
    assert refused_headline.disagree is False
    assert Column(None, None, None, "x").verdict() is None


# ---------------------------------------------------------------------------
# multiple.py -- the block length, the model confidence set, SPA
# ---------------------------------------------------------------------------


def _series(
    columns: dict[str, np.ndarray],
    *,
    horizon: int = 66,
    stride: int = 5,
    loss: str = "mse",
) -> LossSeries:
    """A ``LossSeries`` over made-up per-origin losses, on consecutive weekdays."""
    n = len(next(iter(columns.values())))
    origins = tuple(dt.date(2021, 1, 4) + dt.timedelta(days=i) for i in range(n))
    return LossSeries(
        symbol="SPY",
        horizon=horizon,
        loss=loss,
        cohort="unit",
        stride=stride,
        origins=origins,
        values={name: tuple(float(x) for x in column) for name, column in columns.items()},
    )


def _overlapping(n: int, order: int, seed: int, noise: float = 0.3) -> tuple[np.ndarray, ...]:
    """Two loss series sharing one MA(``order``) component, plus their own idiosyncratic noise.

    The shared component is what a pair of contestants forecasting the **same overlapping
    target** have in common: ``order + 1`` consecutive shocks summed, like consecutive
    ``h``-step windows. Returns ``(a, b, common)``.
    """
    rng = np.random.default_rng(seed)
    common = np.convolve(rng.chisquare(1, size=n + order), np.ones(order + 1), "valid")
    return (
        common + noise * rng.normal(size=n),
        common + noise * rng.normal(size=n),
        common,
    )


def test_effective_observations_is_n_over_the_overlap_window() -> None:
    assert effective_observations(238, 13) == pytest.approx(17.0)
    assert effective_observations(247, 4) == pytest.approx(49.4)
    assert effective_observations(251, 0) == 251


@pytest.mark.parametrize("horizon", [5, 50, 66, 100])
def test_effective_observations_are_invariant_to_the_stride_when_the_horizon_exceeds_it(
    horizon: int,
) -> None:
    """``n_eff ~ W / h`` whatever the stride, for ``h >= s`` -- so no stride rescues h=66.

    ``W`` sessions of origins, the last ``h`` of which have no completed target: ``n`` is
    ``ceil((W - h) / s)`` and ``q + 1`` is ``floor((h - 1) / s) + 1``. Stride 1 against stride 5
    at the project's own window (W = 1253, 2021-2025) lands within 10%.
    """
    window = 1253
    by_stride = {
        stride: effective_observations(
            -(-(window - horizon) // stride), overlap_lag(horizon, stride)
        )
        for stride in (1, 5)
    }
    assert by_stride[1] == pytest.approx(by_stride[5], rel=0.10), by_stride


def test_effective_observations_are_not_stride_invariant_below_the_horizon_floor() -> None:
    """The scoping of the test above: at h=1 a smaller stride *does* buy observations."""
    window = 1253
    one = effective_observations(window - 1, overlap_lag(1, 1))
    five = effective_observations(-(-(window - 1) // 5), overlap_lag(1, 5))
    assert one / five == pytest.approx(5, rel=0.05)


def test_the_block_length_is_floored_by_the_overlap() -> None:
    """On iid losses Politis-White returns a block near 1, which would resample an MA(13)
    series as though it were independent. ``max(q + 1, ceil(PW))`` is what stops that."""
    rng = np.random.default_rng(5)
    iid = _series({"a": rng.chisquare(1, 238), "b": rng.chisquare(1, 238)})
    for q in (13, 4):
        block = block_length(iid, q=q)
        assert block.politis_white < q + 1, "the fixture must have PW below the floor"
        assert block.overlap_floor == q + 1
        assert block.length == q + 1
    free = block_length(iid, q=0)
    assert free.length == max(1, math.ceil(free.politis_white))
    assert isinstance(free.length, int)


def test_the_block_length_is_estimated_from_loss_levels_not_from_cross_model_differentials() -> (
    None
):
    """The design's 14.21 against 0.20, on this test's own series (so the figures differ).

    Two contestants forecasting one overlapping target share the overlap, and the shared part
    **cancels in their differential**: Politis-White on the differential collapses to ~iid and
    would resample the overlapping losses as if independent. On the levels it recovers the
    overlap order. ``block_length`` must therefore agree with the levels figure.
    """
    a, b, _ = _overlapping(238, 13, seed=7)
    series = _series({"a": a, "b": b})
    levels = politis_white(np.column_stack([a, b]))
    differential = politis_white((a - b).reshape(-1, 1))
    assert levels > 13, f"PW on the levels should recover the overlap order, got {levels}"
    assert differential < 2, f"the shared overlap should cancel in the differential: {differential}"

    block = block_length(series, q=13)
    assert block.politis_white == pytest.approx(levels)
    assert block.politis_white != pytest.approx(differential, abs=1.0)
    assert block.length == max(14, math.ceil(levels)) > 14


def test_the_block_length_takes_the_longest_block_across_contestants() -> None:
    a, b, _ = _overlapping(238, 13, seed=7)
    rng = np.random.default_rng(1)
    series = _series({"a": a, "b": b, "iid": rng.chisquare(1, 238)})
    columns = [politis_white(np.asarray(v).reshape(-1, 1)) for v in series.values.values()]
    assert block_length(series, q=0).politis_white == pytest.approx(max(columns))


def _contenders(
    n: int, q: int, seed: int = 3, gaps: tuple[float, ...] = (0.0, 0.01, 3.0)
) -> LossSeries:
    """Contestants sharing one overlapping component, shifted apart by ``gaps``."""
    _, _, common = _overlapping(n, q, seed)
    rng = np.random.default_rng(seed + 100)
    columns = {
        name: common + gap + 0.3 * rng.normal(size=n)
        for name, gap in zip("abcdefg", gaps, strict=False)
    }
    return _series(columns, horizon=q * 5 + 1)


def test_the_constants_the_record_names_are_the_ones_the_design_fixed() -> None:
    assert (REPS, SEED, BOOTSTRAP) == (10_000, 20261025, "stationary")
    assert SIZES == (0.10, 0.25)


def test_the_model_confidence_set_drops_a_clearly_worse_contestant_at_both_sizes() -> None:
    """Known structure: ``c`` loses three full units on every origin against noise of 0.3."""
    result = model_confidence_set(_contenders(238, 4), q=4)
    assert result.refusals == {}
    assert result.members[0.10] == ("a", "b")
    assert result.members[0.25] == ("a", "b")
    assert result.pvalues is not None and result.pvalues["c"] < 0.01
    assert result.pvalues["a"] >= 0.25 or result.pvalues["b"] >= 0.25
    assert (result.reps, result.seed, result.bootstrap) == (REPS, SEED, "stationary")
    assert result.n == 238 and result.q == 4 and result.n_eff == pytest.approx(47.6)
    assert result.block is not None and result.block.length >= 5


def test_one_bootstrap_serves_both_sizes_and_the_sets_are_nested() -> None:
    """Two ``MCS(size=...)`` runs can give non-nested sets; reading both sizes off one
    ``pvalues`` cannot. Membership is also checked against arch's own ``included``."""
    from arch.bootstrap import MCS

    series = _contenders(238, 0, gaps=(0.0, 0.04, 0.07, 0.1, 3.0))
    result = model_confidence_set(series, q=0, reps=2000)
    assert result.pvalues is not None
    # A larger size is a *narrower* set: membership is ``pvalue > size``.
    assert set(result.members[0.25]) <= set(result.members[0.10])
    assert len(result.members[0.10]) > len(result.members[0.25]), (
        "the fixture should separate the two sizes, or nesting is untested"
    )
    losses = pd.DataFrame({k: list(v) for k, v in series.values.items()})
    assert result.block is not None
    for size in (0.10, 0.25):
        reference = MCS(
            losses,
            size,
            reps=2000,
            block_size=result.block.length,
            bootstrap="stationary",
            seed=SEED,
        )
        reference.compute()
        assert set(result.members[size]) == set(reference.included), size
        assert list(result.members[size]) == [m for m in series.values if m in reference.included]


def test_a_contestant_whose_pvalue_equals_the_size_is_excluded_as_arch_excludes_it() -> None:
    """Membership is ``p > alpha``, matching arch's own ``included`` at an exact tie.

    Hansen-Lunde-Nason write ``p >= alpha``; arch writes ``>``, and the two differ only when a
    p-value lands exactly on the size. The library's rule is the one kept, so the membership is
    checkable against ``MCS(size).included``, and a tie is its own test here rather than a surprise.

    With 20 replications p-values are multiples of 0.05, so 0.25 is hit exactly: at this seed
    three contestants sit on it, and the precondition below fails loudly if the fixture drifts.
    """
    from arch.bootstrap import MCS

    series = _contenders(120, 0, gaps=(0.0, 0.04, 0.07, 0.1, 3.0))
    result = model_confidence_set(series, q=0, reps=20, seed=2)
    assert result.pvalues is not None
    tied = [name for name, p in result.pvalues.items() if p == 0.25]
    assert len(tied) >= 1, "the fixture must put a p-value exactly on the size"
    assert not set(tied) & set(result.members[0.25])
    assert result.block is not None
    reference = MCS(
        pd.DataFrame({k: list(v) for k, v in series.values.items()}),
        0.25,
        reps=20,
        block_size=result.block.length,
        bootstrap="stationary",
        seed=2,
    )
    reference.compute()
    assert set(result.members[0.25]) == set(reference.included)


def test_the_bootstrap_is_reproducible_from_the_recorded_seed() -> None:
    series = _contenders(120, 0, gaps=(0.0, 0.1, 0.2))
    first = model_confidence_set(series, q=0, reps=1000)
    again = model_confidence_set(series, q=0, reps=1000)
    other = model_confidence_set(series, q=0, reps=1000, seed=SEED + 1)
    assert first == again
    assert other.seed == SEED + 1
    assert other.pvalues != first.pvalues, "the seed must actually reach the bootstrap"


def test_the_model_confidence_set_is_refused_where_the_effective_sample_is_too_small() -> None:
    """h=66 at stride 5: n=238, q=13, ``n_eff = 17.0 < 20``. The 10% set is refused with its
    reason on the record and the 25% set (threshold 8) is reported."""
    series = _contenders(238, 13)
    result = model_confidence_set(series, q=13)
    assert result.n_eff == pytest.approx(17.0)
    assert 0.10 not in result.members
    assert "n_eff" in result.refusals[0.10]
    assert "17.0" in result.refusals[0.10] and "20" in result.refusals[0.10]
    assert result.members[0.25] == ("a", "b")
    assert result.pvalues is not None


@pytest.mark.parametrize(
    ("n", "q", "reported_at_10"), [(280, 13, True), (266, 13, False), (20, 0, True), (19, 0, False)]
)
def test_the_effective_sample_threshold_is_two_over_alpha_inclusive(
    n: int, q: int, reported_at_10: bool
) -> None:
    """``n_eff = 20.0`` exactly is reported; just under is not."""
    result = model_confidence_set(_contenders(n, q), q=q, reps=500)
    assert (0.10 in result.members) is reported_at_10
    assert (0.10 in result.refusals) is not reported_at_10
    assert 0.25 in result.members


def test_no_bootstrap_is_run_when_every_size_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("arch was called for a series both sizes refuse")

    monkeypatch.setattr(multiple, "MCS", boom)
    result = model_confidence_set(_contenders(60, 13), q=13)  # n_eff = 4.3 < 8
    assert result.members == {} and set(result.refusals) == {0.10, 0.25}
    assert result.pvalues is None and result.block is None


def test_a_lone_contestant_has_no_confidence_set_to_form() -> None:
    result = model_confidence_set(_series({"a": np.arange(1.0, 51.0)}, horizon=1), q=0)
    assert result.members == {} and set(result.refusals) == {0.10, 0.25}


def test_identical_contestants_are_refused_not_handed_to_arch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """arch divides by the bootstrap spread of the pair's mean difference, which is zero for
    identical losses: a numpy ``RuntimeWarning`` (a failing test here). R101's precedent is to
    detect the condition, not to silence the warning."""
    rng = np.random.default_rng(2)
    x = rng.chisquare(1, 80)
    series = _series({"a": x, "b": x.copy(), "c": rng.chisquare(1, 80)}, horizon=1)
    result = model_confidence_set(series, q=0, reps=500)
    assert result.members == {} and set(result.refusals) == {0.10, 0.25}
    assert all("identical" in reason for reason in result.refusals.values())
    spa = superior_predictive_ability(series, "a", q=0, reps=500)
    assert spa.consistent is None and spa.refusal is not None and "identical" in spa.refusal


def test_spa_finds_a_model_that_beats_the_benchmark_and_not_one_that_does_not() -> None:
    a, b, common = _overlapping(238, 4, seed=9)
    rng = np.random.default_rng(10)
    better = _series(
        {
            "bench": common + 0.4 * rng.normal(size=238),
            "rival": common - 1.5 + 0.4 * rng.normal(size=238),
        },
        horizon=21,
    )
    worse = _series(
        {
            "bench": common + 0.4 * rng.normal(size=238),
            "rival": common + 1.5 + 0.4 * rng.normal(size=238),
        },
        horizon=21,
    )
    beaten = superior_predictive_ability(better, "bench", q=4)
    assert beaten.consistent is not None and beaten.consistent < 0.01
    unbeaten = superior_predictive_ability(worse, "bench", q=4)
    assert unbeaten.consistent is not None and unbeaten.consistent > 0.25
    assert beaten.lower is not None and beaten.upper is not None
    assert beaten.lower <= beaten.consistent <= beaten.upper
    assert (beaten.benchmark, beaten.n, beaten.q, beaten.reps, beaten.seed) == (
        "bench",
        238,
        4,
        REPS,
        SEED,
    )
    assert beaten.n_eff == pytest.approx(47.6)
    assert beaten.block is not None and beaten.block.length >= 5
    assert beaten == superior_predictive_ability(better, "bench", q=4), "reproducible"


def test_spa_names_a_benchmark_the_series_does_not_hold() -> None:
    with pytest.raises(ValueError, match="benchmark"):
        superior_predictive_ability(_contenders(60, 0), "missing", q=0)


def _dynamic_range(n: int = 247, *, big: float = 1.7e12, seed: int = 0) -> LossSeries:
    """Three contestants near 1e-5 and ``egarch`` near ``big``: SPY's h=22 MSE, as recorded."""
    rng = np.random.default_rng(seed)
    columns = {
        name: np.abs(rng.normal(1e-5, 3e-6, n)) for name in ("har_levels", "har_semilog", "garch")
    }
    columns["egarch"] = np.abs(rng.normal(big, big * 0.3, n))
    return _series(columns, horizon=22, stride=5)


def test_a_contestant_that_dominates_the_loss_scale_makes_the_confidence_set_a_refusal() -> None:
    """Numerical degeneracy, which no other fixture in this file reaches.

    Every MCS fixture above is ``common + gap + 0.3 * normal`` with ``gap <= 3``, so the largest
    ratio between two contestants' mean losses anywhere in the suite is about 1.4. The suite
    tested statistical correctness on well-conditioned input and structural degeneracy (identical
    and constant contestants), but never **numerical** degeneracy -- and ``multiple._unestimable``
    shows the project already knew how to reason about float64 resolution one level down
    (``same_losses``' scaled tolerance). Here one mean loss is 1.7e12 and the rest are 1e-5, so
    ``L_i - L_j`` is below half an ulp of the large value and arch 8.0.0's standardized row is one
    repeated float: ``MCS._compute_r`` then takes ``loc.squeeze()[0]`` of a ``(m, 2)`` array, puts
    an ``ndarray`` into ``eliminated`` and raised ``IndexError`` from ``Index.__getitem__``
    (``probe/p7.py``). Witness for the refusal: this test, through ``analyse``'s own path at the
    suite's reps=200, at both sizes."""
    series = _dynamic_range()
    assert (
        max(map(np.mean, series.values.values())) / min(map(np.mean, series.values.values())) > 1e16
    )
    result = model_confidence_set(series, q=4, reps=200)
    assert result.pvalues is None and result.members == {} and result.block is None
    assert set(result.refusals) == {0.10, 0.25}, "refused at both sizes, not crashed"
    for reason in result.refusals.values():
        assert "egarch" in reason and "float64" in reason and "no confidence set" in reason
    # The rest of ``analyse`` still runs on the same series: DM and SPA are not the tie's victims.
    comparison = analyse(series, reps=200)
    assert comparison.mcs.refusals == result.refusals
    assert all(p.dm.headline.refusal is None for p in comparison.pairs)


def _with_means(means: dict[str, float], n: int = 40) -> LossSeries:
    """Independent noisy columns, each recentred so its mean is ``means[name]`` to rounding.

    Independent noise, because a column that is another plus a constant is refused earlier by
    ``same_losses`` and would never reach the guard under test.
    """
    columns = {}
    for k, (name, mean) in enumerate(means.items()):
        noise = np.random.default_rng(50 + k).normal(size=n)
        columns[name] = mean + 0.1 * (noise - noise.mean())
    return _series(columns, horizon=22, stride=5)


def test_the_dynamic_range_guard_is_a_tolerance_and_is_tested_either_side_of_it() -> None:
    """The guard refuses where two contestants' mean losses differ by at most
    ``4 * eps * max|mean|`` (``same_losses``' tolerance, one level up) and some third contestant
    exists to be differenced against them. Scale 1e6 puts the line at 8.9e-10."""
    scale = 4 * np.finfo("float64").eps * 1.0e6
    assert scale == pytest.approx(8.9e-10, rel=0.01)
    clear = multiple._unestimable(_with_means({"a": 1.0, "b": 1.0 + 10 * scale, "huge": 1.0e6}))
    assert clear is None, "ten tolerances apart is resolved, whatever the scale"
    tied = multiple._unestimable(_with_means({"a": 1.0, "b": 1.0 + scale / 4, "huge": 1.0e6}))
    assert tied is not None and "huge" in tied and "float64" in tied
    # A pair has no third contestant to be differenced against them, so nothing can tie.
    assert multiple._unestimable(_with_means({"a": 1.0, "b": 1.0 + scale / 4})) is None


def test_the_dynamic_range_guard_is_a_conservative_superset_of_the_tie_arch_cannot_survive() -> (
    None
):
    """Deliberately so. Means a quarter of the tolerance apart leave arch's standardized
    differentials unequal in their last bits, so arch 8.0.0 runs (measured, this fixture); the
    guard refuses anyway, because a result that depends on a one-ulp difference surviving the
    subtraction is not one to report. The refusal direction is the safe one: the alternative
    failure is a set that eliminates two models in a step."""
    from arch.bootstrap import MCS

    tolerance = 4 * np.finfo("float64").eps * 1.0e6
    series = _with_means({"a": 1.0, "b": 1.0 + tolerance / 4, "huge": 1.0e6}, n=60)
    assert multiple._unestimable(series) is not None
    losses = pd.DataFrame({k: list(v) for k, v in series.values.items()})
    MCS(losses, 0.1, reps=100, block_size=5, method="R", bootstrap="stationary", seed=1).compute()


def test_the_dynamic_range_guard_is_for_the_confidence_set_and_not_for_spa() -> None:
    """SPA differences against its benchmark and never reaches arch's ``_compute_r``, so the tie
    is no reason to refuse it (``block_length`` is shared and must not raise on SPA's behalf)."""
    series = _dynamic_range()
    result = superior_predictive_ability(series, "har_levels", q=4, reps=200)
    assert result.refusal is None and result.consistent is not None


# ---------------------------------------------------------------------------
# __init__.py and sink.py -- the analysis, its record, and the one thing that writes it
# ---------------------------------------------------------------------------

BASELINE = ("har_levels", "har_semilog", "random_walk")
SOURCE = "synthetic probe series, not SPY"


def _big_evaluation(
    n: int = 40, *, horizon: int = 22, every: int = 5, extra: tuple[str, ...] = ("rival",)
) -> Evaluation:
    """``n`` origins, the three baseline names and ``extra``, with forecasts near a noisy truth."""
    rng = np.random.default_rng(21)
    names = (*BASELINE, *extra)
    origins = [dt.date(2021, 1, 4) + dt.timedelta(days=7 * i) for i in range(n)]
    truth = rng.uniform(1.0, 2.0, size=n)
    results = tuple(
        OriginResult(
            origin=origins[i],
            realized=float(truth[i]),
            forecasts={
                name: float(truth[i] * (1 + 0.1 * (j + 1)) + 0.05 * rng.normal())
                for j, name in enumerate(names)
            },
            training_rows=100,
        )
        for i in range(n)
    )
    return Evaluation(
        symbol="SPY",
        horizon=horizon,
        results=results,
        features=pd.DataFrame(),
        skipped=(),
        requested=n,
        every=every,
        contestants=names,
        cohort="unit",
    )


def _comparison(evaluation: Evaluation | None = None, loss: str = "mse", **kwargs: object):
    evaluation = evaluation if evaluation is not None else _big_evaluation()
    return analyse(loss_series(evaluation, loss, cohort="unit"), **kwargs)  # type: ignore[arg-type]


def test_analyse_derives_the_overlap_from_the_series_horizon_and_stride() -> None:
    """h=22 at stride 5 is q=4, so 40 origins are worth 8: exactly the 25% threshold."""
    comparison = _comparison()
    assert (comparison.symbol, comparison.horizon, comparison.loss, comparison.cohort) == (
        "SPY",
        22,
        "mse",
        "unit",
    )
    assert (comparison.stride, comparison.origins, comparison.q) == (5, 40, 4)
    assert comparison.bartlett_lag == max(4, newey_west_lag(40))
    assert comparison.n_eff == pytest.approx(8.0)
    assert comparison.mcs.n_eff == pytest.approx(8.0)
    assert 0.10 in comparison.mcs.refusals and 0.25 in comparison.mcs.members
    assert (comparison.reps, comparison.seed, comparison.bootstrap) == (
        10_000,
        20261025,
        "stationary",
    )


def test_analyse_counts_every_pairwise_test_it_runs() -> None:
    comparison = _comparison()
    k = len(comparison.contestants)
    assert k == 4 and comparison.dm_tests == k * (k - 1) // 2 == len(comparison.pairs) == 6
    assert {(p.left, p.right) for p in comparison.pairs} == {
        (a, b)
        for i, a in enumerate(comparison.contestants)
        for b in comparison.contestants[i + 1 :]
    }


def test_analyse_runs_spa_against_har_levels_and_random_walk_and_records_a_missing_one() -> None:
    comparison = _comparison()
    assert [spa.benchmark for spa in comparison.spa] == ["har_levels", "random_walk"]
    assert all(spa.refusal is None and spa.consistent is not None for spa in comparison.spa)
    missing = _comparison(benchmarks=("har_levels", "ghost"))
    ghost = missing.spa[1]
    assert ghost.benchmark == "ghost" and ghost.consistent is None
    assert ghost.refusal is not None and "ghost" in ghost.refusal


def test_analyse_records_an_empty_configuration_rather_than_failing() -> None:
    empty = Evaluation(
        symbol="SPY",
        horizon=22,
        results=(),
        features=pd.DataFrame(),
        skipped=(),
        requested=0,
        every=5,
        contestants=BASELINE,
        cohort="unit",
    )
    comparison = _comparison(empty)
    assert comparison.origins == 0 and comparison.refusal is not None
    assert comparison.mcs.members == {} and len(comparison.mcs.refusals) == 2
    assert all(spa.refusal is not None for spa in comparison.spa)
    assert all(p.dm.headline.refusal is not None for p in comparison.pairs)
    json.dumps(comparison.record(), allow_nan=False)


def test_the_record_is_strict_json_and_carries_what_the_design_names() -> None:
    comparison = _comparison(
        monte_carlo_error={"rival": 0.514, "random_walk": None}, monte_carlo_source=SOURCE
    )
    record = comparison.record()
    text = json.dumps(record, allow_nan=False)
    assert json.loads(text) == record
    assert {
        "symbol",
        "horizon",
        "loss",
        "cohort",
        "stride",
        "origins",
        "q",
        "bartlett_lag",
        "n_eff",
        "block",
        "reps",
        "seed",
        "bootstrap",
        "dm",
        "dm_tests",
        "mcs",
        "spa",
        "monte_carlo_error",
    } <= set(record)
    block = record["block"]
    assert isinstance(block, dict) and set(block) == {"politis_white", "overlap_floor", "length"}
    mcs = record["mcs"]
    assert isinstance(mcs, dict) and set(mcs["sizes"]) == {"0.1", "0.25"}  # type: ignore[index]
    assert "refused" in mcs["sizes"]["0.1"] and "members" in mcs["sizes"]["0.25"]  # type: ignore[index]
    dm = record["dm"]
    assert isinstance(dm, list) and len(dm) == 6
    assert set(dm[0]) >= {"left", "right", "mean_difference", "headline", "bartlett", "disagree"}
    assert set(dm[0]["headline"]) == {"statistic", "p_value", "variance", "refusal", "verdict"}
    assert set(dm[0]) >= {"claimed"}
    assert record["monte_carlo_error"] == {"rival": 0.514, "random_walk": None}


def test_a_refused_dm_column_is_null_in_the_record_not_nan() -> None:
    losses = [1.0, 2.5, 0.5, 3.0, 1.5, 2.0, 2.2, 1.1, 0.9, 1.7, 2.9, 3.1]
    series = _series({"a": np.array(losses), "b": np.array(losses)}, horizon=1)
    record = analyse(series).record()
    text = json.dumps(record, allow_nan=False)
    first = json.loads(text)["dm"][0]
    assert first["headline"]["statistic"] is None and "identical" in first["headline"]["refusal"]


def test_a_simulated_contestant_above_the_error_floor_has_no_significance_claimed() -> None:
    """R102. EGARCH at h=66 measures 3.273% at S=100,000 -- worse than 0.951% at S=10,000 -- and
    R72's floor for a believable difference tops out at 1.5%. The flag is on the record and the
    printed lines say so; one below the floor, on this series, is not flagged."""
    assert MC_ERROR_CEILING_PERCENT == 1.5
    comparison = _comparison(
        monte_carlo_error={"har_levels": 0.514, "rival": 3.273},
        monte_carlo_source="SPY h22",
    )
    assert comparison.unclaimed == ("rival",), "the figure is on this series, so only size decides"
    assert comparison.record()["unclaimed"] == ["rival"]
    text = "\n".join(describe(comparison))
    assert "3.273%" in text and "0.514%" in text
    rival_lines = [line for line in describe(comparison) if "rival" in line and " vs " in line]
    assert len(rival_lines) == 3
    assert all("no claim" in line for line in rival_lines)
    clean = [line for line in describe(comparison) if "rival" not in line and " vs " in line]
    assert clean and not any("no claim" in line for line in clean)


def test_a_figure_measured_on_another_series_can_withhold_a_claim_but_never_authorise_one() -> None:
    """R102 follow-up. ``("egarch11_skewt", 22)`` is 0.163% on the probe's series, under the 1.5%
    ceiling, so EGARCH at h=22 was *not* withheld on its value. The probe's series is a gentler
    process (alpha 0.05, beta 0.87 against SPY's ~0.25, ~0.96), and a six-seed sd is not a
    measurement of an estimator with no finite mean: a small spread there is no evidence of
    precision. So a figure from another series is withhold-only, whatever its value.

    This docstring quoted "230.79% on SPY" until R129 struck it: no committed probe prints it, and
    a percentage error against a quantity R114 showed to be divergent is not an error bar."""
    own = series_source(_contenders(60, 0))
    assert own == "SPY h1" and own != monte_carlo_error(SHOOTOUT, 1).source
    series = _contenders(60, 0)
    figures = {"a": 0.163, "b": 3.273, "c": None}
    foreign = analyse(series, monte_carlo_error=figures, monte_carlo_source=SOURCE, reps=200)
    assert foreign.unclaimed == ("a", "b", "c"), (
        "the low figure is withheld too, and so is no figure"
    )
    measured = analyse(series, monte_carlo_error=figures, monte_carlo_source=own, reps=200)
    assert measured.unclaimed == ("b", "c"), "only a figure measured on this series can clear it"
    assert not any("a 0.163%, no claim" in x for x in describe(measured))
    shown = [x for x in describe(foreign) if "a 0.163%" in x]
    assert shown and all("a 0.163%, no claim" in x for x in shown)
    assert all(SOURCE in x for x in shown), "the source stays named beside the figure"
    # The same-named series at another horizon is another series.
    other = dataclasses.replace(series, horizon=5)
    assert analyse(
        other, monte_carlo_error=figures, monte_carlo_source=own, reps=200
    ).unclaimed == ("a", "b", "c")


def test_a_simulated_contestant_with_no_measurement_is_withheld() -> None:
    """No figure is the purest form of the error the foreign-source rule closes: absence of
    measured spread read as presence of precision. Two simulated contestants (synthetic ``gjr`` and
    ``egarch``; in the shipped roster only ``egarch11_skewt`` simulates, GJR being exact) with no
    figure are withheld even on a source naming this very series; a deterministic contestant has
    no entry in the map and keeps its claims."""
    names = ("har_levels", "random_walk", "gjr", "egarch")
    base = _named_contenders(247, 0, names, (0.0, 0.0, 0.01, 3.0))
    series = dataclasses.replace(base, horizon=5)
    comparison = analyse(
        series,
        monte_carlo_error={"gjr": None, "egarch": None},
        monte_carlo_source=series_source(series),
        reps=200,
    )
    assert not comparison.claims_withheld, "n_eff is large, so only the Monte Carlo rule is in play"
    assert comparison.unclaimed == ("gjr", "egarch")
    assert comparison.record()["unclaimed"] == ["gjr", "egarch"]
    by_pair = {(p.left, p.right): p for p in comparison.pairs}
    assert not comparison.pair_claimed(by_pair[("har_levels", "gjr")])
    assert comparison.pair_claimed(by_pair[("har_levels", "random_walk")]), "deterministic: kept"
    text = "\n".join(describe(comparison))
    assert "gjr not measured, no claim" in text and "egarch not measured, no claim" in text
    assert "or where none was measured" in text


def test_a_shipped_cohort_figure_is_withheld_whatever_series_it_is_compared_on() -> None:
    """The shipped figures, through the shipped source, onto a SPY series. EGARCH is the only
    simulated contestant (GJR is exact) and the only horizon it can be evaluated at is 1, where it
    has no figure -- and no figure is no claim, even if the source it is given names the very
    series being compared."""
    assert SHOOTOUT.horizons(HORIZONS) == (1,)
    base = _named_contenders(60, 0, ("har_levels", "random_walk", "egarch"), (0.0,) * 3)
    series = dataclasses.replace(base, horizon=1)
    errors = monte_carlo_error(SHOOTOUT, 1)
    assert set(errors.percent) == {"egarch11_skewt"}, "GJR is exact: nothing of it to withhold"
    figures = {"egarch": errors.percent["egarch11_skewt"]}
    assert figures == {"egarch": None}
    for source in (errors.source, series_source(series)):
        comparison = analyse(series, monte_carlo_error=figures, monte_carlo_source=source, reps=200)
        assert comparison.unclaimed == ("egarch",), source


def test_egarch_is_unclaimed_at_h1_and_its_error_is_not_declared_zero() -> None:
    """At h=1 every one of EGARCH's paths is the same number, so its Monte-Carlo error is
    identically zero, and declaring ``Measurement(percent=0.0, ...)`` would lift it out of
    ``unclaimed`` and hand a significance claim to the contestant leading h=1 QLIKE. R124 refused
    exactly that, by name, as post-hoc selection. Out of scope twice over; this is the guard."""
    base = _named_contenders(60, 0, ("har_levels", "random_walk", "egarch11_skewt"), (0.0,) * 3)
    series = dataclasses.replace(base, horizon=1)
    errors = monte_carlo_error(SHOOTOUT, 1)
    assert errors.percent["egarch11_skewt"] is None
    sampling = EGARCH11_SKEWT.domain.sampling
    assert sampling is not None and sampling.measured == () and sampling.error(1) is None
    # The shipped source, and then the evaluated series' own name: neither may authorise a claim.
    for source in (errors.source, series_source(series)):
        comparison = analyse(
            series, monte_carlo_error=errors.percent, monte_carlo_source=source, reps=200
        )
        assert comparison.unclaimed == ("egarch11_skewt",), source


def _named_contenders(n: int, q: int, names: tuple[str, ...], gaps: tuple[float, ...]):
    """``_contenders`` with distinguishable names, so a line can be searched for one of them."""
    base = _contenders(n, q, gaps=gaps)
    return dataclasses.replace(
        base, values={name: v for name, v in zip(names, base.values.values(), strict=True)}
    )


def test_every_line_naming_a_simulated_contestant_carries_its_error_in_or_out_of_the_set() -> None:
    """R102 asks for the error *beside every simulated figure*, and an exclusion is a figure.

    h=22, n=247, q=4, so ``n_eff = 49.4`` and DM verdicts are claimable. ``gjr`` is measured below
    the floor, ``egarch`` above it and **excluded from every set**, and ``garch`` is analytic.
    """
    series = _named_contenders(247, 4, ("har", "garch", "gjr", "egarch"), (0.0, 0.0, 0.01, 3.0))
    comparison = analyse(
        series,
        benchmarks=("har",),
        monte_carlo_error={"gjr": 0.207, "egarch": 3.273},
        monte_carlo_source=series_source(series),
    )
    assert comparison.claims_withheld is False
    assert all("egarch" not in members for members in comparison.mcs.members.values()), (
        "the fixture must exclude the over-floor contestant, or the exclusion is untested"
    )
    lines = describe(comparison)
    cohort_wide = [
        line for line in lines if line.lstrip().startswith(("MCS", "SPA", "Monte Carlo"))
    ]
    assert len(cohort_wide) >= 4
    for line in cohort_wide:
        assert "gjr 0.207%" in line and "egarch 3.273%, no claim" in line, line
    pair_lines = [line for line in lines if " vs " in line and line.startswith("    ")]
    assert len(pair_lines) == 6
    for line in pair_lines:
        if "gjr" in line:
            assert "gjr 0.207%" in line, line
        if "egarch" in line:
            assert "egarch 3.273%, no claim" in line, line
            assert "=>" not in line, "a verdict was stated about a contestant with no claim"
    printed = [
        part for line in lines for part in re.findall(r"\[MC error \([^)]*\): ([^\]]*)\]", line)
    ]
    names = {item.split()[0] for part in printed for item in part.split("; ")}
    assert names == {"gjr", "egarch"}, "only simulated contestants carry a simulation error"


def test_a_verdict_is_stated_only_where_it_is_claimed() -> None:
    series = _named_contenders(247, 4, ("har", "garch", "gjr", "egarch"), (0.0, 0.0, 0.01, 3.0))
    comparison = analyse(
        series,
        monte_carlo_error={"gjr": 0.207, "egarch": 3.273},
        monte_carlo_source=series_source(series),
    )
    by_pair = {(p.left, p.right): p for p in comparison.pairs}
    clear = by_pair[("har", "gjr")]
    assert comparison.pair_claimed(clear)
    assert comparison.pair_verdict(clear) in {
        "no significant difference at 5% (both columns)",
        "har significantly better at 5% (both columns)",
    }
    assert not comparison.pair_claimed(by_pair[("har", "egarch")])
    assert comparison.pair_verdict(by_pair[("har", "egarch")]) is None
    flags = {(d["left"], d["right"]): d["claimed"] for d in comparison.record()["dm"]}  # type: ignore[union-attr]
    assert flags[("har", "gjr")] is True and flags[("har", "egarch")] is False
    # And not where the effective sample is thin.
    thin = analyse(_contenders(238, 13))
    assert not any(thin.pair_claimed(pair) for pair in thin.pairs)
    assert all(d["claimed"] is False for d in thin.record()["dm"])  # type: ignore[union-attr]


def test_the_ledger_count_is_printed_beside_the_ranking_when_the_sink_has_one() -> None:
    comparison = _comparison()
    assert "72 trial line(s) on the ledger" in describe(comparison, trials=72)[0]
    assert "trial line(s)" not in describe(comparison)[0]


def test_the_effective_sample_and_the_refusal_are_printed_beside_every_figure_at_h66() -> None:
    """h=66, n=238: ``n_eff = 17.0``. The 10% set is refused with its reason printed, the 25% set
    is reported, and the DM lines carry ``n_eff`` and claim no significance."""
    series = _contenders(238, 13)
    comparison = analyse(series)
    lines = describe(comparison)
    text = "\n".join(lines)
    assert comparison.n_eff == pytest.approx(17.0)
    mcs10 = next(line for line in lines if "MCS 10%" in line)
    mcs25 = next(line for line in lines if "MCS 25%" in line)
    assert "REFUSED" in mcs10 and "n_eff" in mcs10 and "17.0" in mcs10
    assert "REFUSED" not in mcs25 and "n_eff=17.0" in mcs25
    assert all("n_eff=17.0" in line for line in lines if line.lstrip().startswith("SPA"))
    pair_lines = [line for line in lines if " vs " in line]
    assert pair_lines and all("no significance claimed" in line for line in pair_lines)
    assert comparison.claims_withheld is True
    assert all("n_eff=17.0" in line for line in lines if line.lstrip().startswith("MCS")), (
        "the p-value line is a multiple-comparison figure too"
    )
    assert f"seed={SEED}" in text and f"reps={REPS}" in text
    assert "politis-white" in text.lower()


def test_dm_significance_is_claimed_where_the_effective_sample_supports_it() -> None:
    comparison = analyse(_contenders(238, 4))
    assert comparison.claims_withheld is False
    assert not any("no significance claimed" in line for line in describe(comparison))


def test_where_the_two_columns_disagree_both_are_printed_and_neither_is_claimed() -> None:
    n = 100
    slow = np.array([0.2 + math.sin(2 * math.pi * i / 50) for i in range(n)])
    series = _series({"a": 1.0 + slow, "b": np.full(n, 1.0)}, horizon=1)
    comparison = analyse(series)
    (pair,) = comparison.pairs
    assert pair.dm.disagree, "the fixture must be a real disagreement, or this tests nothing"
    (line,) = [line for line in describe(comparison) if " vs " in line]
    assert "columns disagree" in line and "neither claimed" in line
    assert "headline" in line and "bartlett" in line
    assert pair.dm.headline.p_value is not None and f"{pair.dm.headline.p_value:.4f}" in line
    assert pair.dm.bartlett.p_value is not None and f"{pair.dm.bartlett.p_value:.4f}" in line


# -- the sink ---------------------------------------------------------------

AT = dt.datetime(2026, 10, 27, 9, 30, tzinfo=dt.UTC)


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_the_sink_writes_trials_through_the_existing_ledger_writer(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exactly one ledger writer (R95): the sink's bytes are ``append_trials``' bytes, and the
    call goes through the walk-forward's own function rather than a copy of it."""
    evaluation = _big_evaluation()
    trials = evaluation.trials()
    JsonlSink(tmp_path / "a").append_trials(trials, at=AT, sha="abc", dirty=False)
    append_trials(trials, root=tmp_path / "b", at=AT, sha="abc", dirty=False)
    assert (tmp_path / "a" / TRIAL_LEDGER).read_bytes() == (
        tmp_path / "b" / TRIAL_LEDGER
    ).read_bytes()

    from volab.compare import sink as sink_module

    seen: list[int] = []
    monkeypatch.setattr(
        sink_module, "append_trials", lambda batch, **kw: seen.append(len(batch)) or len(batch)
    )
    assert JsonlSink(tmp_path / "c").append_trials(trials, at=AT, sha="abc", dirty=False) == len(
        trials
    )
    assert seen == [len(trials)]
    assert not (tmp_path / "c").exists(), "the stand-in wrote nothing, so the sink wrote nothing"


def test_a_comparison_is_not_a_trial_and_never_reaches_the_ledger(tmp_path) -> None:
    """``count_trials`` counts that file's lines: a second kind of record would corrupt the one
    number the write-up reports."""
    comparison = _comparison()
    written = JsonlSink(tmp_path).append_comparisons([comparison], at=AT, sha="abc", dirty=False)
    assert written == 1
    assert count_trials(tmp_path) == 0 and not (tmp_path / TRIAL_LEDGER).exists()
    (line,) = _lines(tmp_path / COMPARISON_LEDGER)
    assert COMPARISON_LEDGER.as_posix() == "data/comparisons.jsonl"
    assert line["at"] == AT.isoformat() and line["git_sha"] == "abc" and line["git_dirty"] is False
    assert line["cohort"] == "unit" and line["horizon"] == 22 and line["loss"] == "mse"


def test_a_value_that_escapes_the_record_cleaning_fails_loudly_rather_than_writing_nan(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``record`` maps NaN to null, and ``allow_nan=False`` is the net under it: ``json.dumps``
    writes a bare ``NaN`` that no strict parser reads, so a leak must raise, not land."""
    comparison = _comparison()
    monkeypatch.setattr(Comparison, "record", lambda self: {"symbol": "SPY", "n_eff": float("nan")})
    with pytest.raises(ValueError):
        JsonlSink(tmp_path).append_comparisons([comparison], at=AT, sha="abc", dirty=False)
    ledger = tmp_path / COMPARISON_LEDGER
    assert not ledger.exists() or "NaN" not in ledger.read_text(encoding="utf-8")


def test_the_comparison_ledger_appends_and_never_rewrites(tmp_path) -> None:
    sink = JsonlSink(tmp_path)
    comparison = _comparison()
    sink.append_comparisons([comparison], at=AT, sha="one", dirty=None)
    first = (tmp_path / COMPARISON_LEDGER).read_text(encoding="utf-8")
    sink.append_comparisons([comparison, comparison], at=AT, sha="two", dirty=True)
    second = (tmp_path / COMPARISON_LEDGER).read_text(encoding="utf-8")
    assert second.startswith(first), "the ledger's existing lines moved"
    assert [line["git_dirty"] for line in _lines(tmp_path / COMPARISON_LEDGER)] == [
        None,
        True,
        True,
    ]


def test_trials_and_comparisons_from_one_run_share_a_join_key(tmp_path) -> None:
    """``(git_sha, at, symbol, horizon, loss, cohort)``: a comparison whose key has no trials is
    a bug, so every key on one side must be found on the other."""
    evaluation = _big_evaluation()
    sink = JsonlSink(tmp_path)
    sink.append_trials(evaluation.trials(), at=AT, sha="abc", dirty=False)
    comparisons = [analyse(loss_series(evaluation, loss, cohort="unit")) for loss in sorted(LOSSES)]
    sink.append_comparisons(comparisons, at=AT, sha="abc", dirty=False)

    def key(line: dict) -> tuple:
        return tuple(line[k] for k in ("git_sha", "at", "symbol", "horizon", "loss", "cohort"))

    trial_keys = {key(line) for line in _lines(tmp_path / TRIAL_LEDGER)}
    comparison_keys = {key(line) for line in _lines(tmp_path / COMPARISON_LEDGER)}
    assert len(comparison_keys) == len(LOSSES)
    assert comparison_keys == trial_keys


def test_the_jsonl_sink_satisfies_the_sink_protocol() -> None:
    assert isinstance(JsonlSink(__import__("pathlib").Path(".")), Sink)


# ---------------------------------------------------------------------------
# cohorts.py -- the named cohorts, and the one module that imports both model modules
# ---------------------------------------------------------------------------

COMPARE_SRC = Path(compare_cli.__file__).parent


def test_the_shootout_cohort_is_the_seven_contestants_and_begins_with_har_levels() -> None:
    assert SHOOTOUT.contestants == (
        "har_levels",
        "har_semilog",
        "random_walk",
        "garch11_skewt",
        "gjr11_skewt",
        "egarch11_skewt",
        "har_levels_mz",
    )
    assert len(set(SHOOTOUT.contestants)) == 7
    assert all(isinstance(model, Model) for model in SHOOTOUT.models)
    assert SHOOTOUT.name != BASELINE_COHORT_NAME, "the baseline name is the recorded run's alone"
    assert {SHOOTOUT.name: SHOOTOUT, MULTISTEP.name: MULTISTEP} == COHORTS
    for cohort in COHORTS.values():
        # ``training_rows`` is the first contestant's ``observations`` (R87).
        assert cohort.models[0].name == "har_levels", cohort.name


def test_the_multistep_cohort_is_the_shootout_without_egarch_and_is_runnable_by_name() -> None:
    """EGARCH(1,1) with skew-t errors has no finite multi-step conditional variance (R114), so
    every h >= 2 forecast it makes estimates infinity and only h=1, which takes no expectation, is
    credible. The cohort for h = 5, 22 and 66 is therefore the seven minus that one. It keeps
    ``har_levels`` first (R87), has a name of its own, and neither reuses the shootout's name nor
    the baseline's, because a ledger row is keyed by cohort name and these are different sets."""
    assert MULTISTEP.contestants == tuple(c for c in SHOOTOUT.contestants if c != "egarch11_skewt")
    assert len(MULTISTEP.contestants) == 6 and MULTISTEP.contestants[0] == "har_levels"
    assert MULTISTEP.models == tuple(m for m in SHOOTOUT.models if m.name != "egarch11_skewt")
    assert len({SHOOTOUT.name, MULTISTEP.name, BASELINE_COHORT_NAME}) == 3
    assert COHORTS[MULTISTEP.name] is MULTISTEP
    for horizon in (5, 22, 66):
        errors = monte_carlo_error(MULTISTEP, horizon)
        assert errors.percent == {} and errors.source is None, (
            "no simulated contestant is left: EGARCH is not in the cohort and GJR is exact"
        )
    assert all(
        phrase in (cohorts_module.__doc__ or "")
        for phrase in ("R114", "polynomial tails", "no finite multi-step conditional variance")
    ), "the reason is the specification-level fact, not 'it was unstable'"


def test_the_cohort_horizons_are_derived_from_the_declarations() -> None:
    """By value, so the registered pairing -- the shootout at h=1, the multistep cohort above --
    is what is asserted. Nobody types either list: EGARCH's bound produces the first."""
    assert SHOOTOUT.horizons(HORIZONS) == (1,)
    assert MULTISTEP.horizons(HORIZONS) == (1, 5, 22, 66)
    assert BASELINE_COHORT.horizons(HORIZONS) == (1, 5, 22, 66)
    assert [SHOOTOUT.refusal(h) is None for h in HORIZONS] == [True, False, False, False]
    assert all(MULTISTEP.refusal(h) is None for h in HORIZONS)


def test_the_shootout_refusal_names_egarch_and_its_bound() -> None:
    reason = SHOOTOUT.refusal(22)
    assert reason is not None
    assert "phase2-shootout" in reason and "'egarch11_skewt'" in reason and "h<=1" in reason
    assert "22" in reason


def test_a_second_horizon_limited_contestant_stays_out_of_the_multistep_cohort() -> None:
    """Under the old name filter this needed someone to remember. Derived, a contestant that
    declares ``max_horizon=5`` is excluded because of what it declares."""

    short = _Limited(BASELINE_COHORT.models[2], 5, name="short_five")
    members = (*SHOOTOUT.models, short)
    kept = multistep_members(members)
    assert short not in kept and EGARCH11_SKEWT not in kept
    assert [m.name for m in kept] == [m.name for m in MULTISTEP.models]
    assert multistep_members(SHOOTOUT.models) == MULTISTEP.models
    # The cohort the shootout becomes with it is still refused at h=5 only by EGARCH's bound.
    assert Cohort("x", members).horizons(HORIZONS) == (1,)


def test_answering_names_the_registered_cohorts_that_can_answer_a_horizon() -> None:
    assert answering(22) == (MULTISTEP.name,)
    assert answering(1) == (SHOOTOUT.name, MULTISTEP.name)
    wide = Cohort("wide", BASELINE_COHORT.models)
    assert answering(22, {"narrow": TOY_LIMITED, "wide": wide}) == ("wide",)
    assert answering(22, {"narrow": TOY_LIMITED}) == ()


def test_the_shootout_cohort_contains_the_baseline_models_unchanged() -> None:
    assert SHOOTOUT.models[:3] == BASELINE_COHORT.models
    assert SHOOTOUT.models[3:6] == GARCH_MODELS


def _imports(module: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_only_cohorts_imports_the_model_modules() -> None:
    """``import volab.compare`` must not pull ``arch``'s GARCH through a side door: the analysis
    modules know nothing of any contestant, and ``cohorts`` is the one place that names them."""
    modules = sorted(COMPARE_SRC.glob("*.py"))
    assert {m.name for m in modules} >= {
        "__init__.py",
        "__main__.py",
        "cohorts.py",
        "dm.py",
        "multiple.py",
        "series.py",
        "sink.py",
    }
    for module in modules:
        names = _imports(module)
        garch = "volab.models.garch" in names
        recalibrated = "volab.models.recalibrated" in names
        if module.name == "cohorts.py":
            assert garch and recalibrated, "cohorts.py is where the shootout is assembled"
        else:
            assert not garch and not recalibrated, f"{module.name} imports a contestant module"


def test_no_monte_carlo_figure_is_invented_for_the_shipped_contestants() -> None:
    """EGARCH is the only shipped contestant that simulates and it has no measurement, so it
    reads ``None`` at every horizon -- including the ones it can no longer be evaluated at, whose
    rows were deleted (the horizon guard makes them unreachable). GJR is exact: absent at every
    horizon, not ``None`` -- ``None`` would say "simulated, unmeasured" and trigger R115's rule."""
    for horizon in HORIZONS:
        errors = monte_carlo_error(SHOOTOUT, horizon)
        assert errors.percent == {"egarch11_skewt": None}, horizon
        assert "garch11_skewt" not in errors.percent, "the analytic one has none"
        assert "gjr11_skewt" not in errors.percent, "the exact one has none"


@dataclasses.dataclass(frozen=True)
class _Sampled:
    """A contestant that is **not** a ``GarchModel`` and simulates, declaring its own figures.

    The shape US-3.9b exists for: nothing in ``cohorts`` names it, so any figure it reports is
    read off the instance and not looked up.
    """

    sampling: Sampling
    name: str = "sampled_rw"

    @property
    def fit_depends_on_horizon(self) -> bool:
        return BASELINE_COHORT.models[2].fit_depends_on_horizon

    @property
    def domain(self) -> Domain:
        return Domain(sampling=self.sampling)

    def fit(self, view: TrainingView) -> Forecaster:
        return BASELINE_COHORT.models[2].fit(view)


def _measured(paths: int = 100_000, source: str = "SPY h22", percent: float = 0.4) -> Sampling:
    return Sampling(paths=paths, measured=(Measurement(percent, source, 22, paths),))


HEAD = BASELINE_COHORT.models[0]


def test_a_figure_measured_at_another_path_count_reads_as_not_measured() -> None:
    """W13. The path count a figure was measured at travels with it, so a contestant run at
    another count reads ``None`` -- through the cohort, not just through ``Sampling.error``."""
    right = _Sampled(_measured(paths=100_000))
    assert cohorts_module.monte_carlo_error(Cohort("probe", (HEAD, right)), 22).percent == {
        "sampled_rw": 0.4
    }
    # The same declared figure, a different path count in use: only the paths differ.
    fewer = _Sampled(Sampling(paths=10_000, measured=right.sampling.measured))
    assert cohorts_module.monte_carlo_error(Cohort("probe", (fewer,)), 22).percent == {
        "sampled_rw": None
    }
    # A real contestant, run at another path count than anything measured.
    egarch = dataclasses.replace(EGARCH11_SKEWT, simulations=10_000)
    assert cohorts_module.monte_carlo_error(Cohort("probe", (egarch,)), 1).percent == {
        "egarch11_skewt": None
    }


def test_a_simulated_contestant_removed_from_a_cohort_takes_its_figures_with_it() -> None:
    """W14, and the structural replacement for the deleted reachability test. The table's sin was
    surviving its reader; a figure attached to the instance that measured it cannot.

    An empty cohort is the trivial case, so the three that bite are here: the figure is reported
    while the contestant is in the cohort, nothing is left of it (not even a source label) when it
    is out, and a **renamed** copy carries it under the new name -- which a name-keyed table
    resurrected under any spelling would fail."""
    assert cohorts_module.monte_carlo_error(Cohort("probe", ()), 22).percent == {}
    spy = _Sampled(_measured())
    inside = cohorts_module.monte_carlo_error(Cohort("probe", (HEAD, spy)), 22)
    assert inside.percent == {"sampled_rw": 0.4} and inside.source == "SPY h22"
    outside = cohorts_module.monte_carlo_error(Cohort("probe", (HEAD,)), 22)
    assert outside.percent == {} and outside.source is None, "no residual label survives"
    renamed = dataclasses.replace(spy, name="renamed")
    assert cohorts_module.monte_carlo_error(Cohort("probe", (HEAD, renamed)), 22).percent == {
        "renamed": 0.4
    }


def test_no_module_level_table_in_cohorts_is_keyed_by_a_contestant_and_a_horizon() -> None:
    """The retired table was ``dict[tuple[str, int], float]``. Gone, and nothing of that shape
    stands in for it under another name."""
    assert not hasattr(cohorts_module, "MONTE_CARLO_ERROR_PERCENT")
    assert not hasattr(cohorts_module, "MONTE_CARLO_ERROR_SOURCE")
    for name, value in vars(cohorts_module).items():
        if isinstance(value, dict):
            assert not any(
                isinstance(key, tuple)
                and any(isinstance(part, str) for part in key)
                and any(isinstance(part, int) for part in key)
                for key in value
            ), f"{name} is a table keyed by (contestant, horizon): a figure with no owner"


def test_two_simulated_contestants_measured_on_different_series_are_refused() -> None:
    """W15. The comparison record stores one source per record, so two contestants naming two
    series would be labelled with one of them -- a mislabelled figure. Raised where the whole
    cohort is first seen, naming both contestants and both series."""
    one = _Sampled(_measured(source="SPY h22"), name="one")
    other = _Sampled(_measured(source="QQQ h22"), name="other")
    with pytest.raises(ValueError, match=r"one.*SPY h22|SPY h22.*one") as caught:
        cohorts_module.monte_carlo_error(Cohort("probe", (HEAD, one, other)), 22)
    message = str(caught.value)
    assert all(part in message for part in ("one", "other", "SPY h22", "QQQ h22")), message
    agreeing = _Sampled(_measured(source="SPY h22", percent=0.9), name="agrees")
    both = cohorts_module.monte_carlo_error(Cohort("probe", (HEAD, one, agreeing)), 22)
    assert both.percent == {"one": 0.4, "agrees": 0.9} and both.source == "SPY h22"


def test_a_simulated_contestant_is_found_by_its_declaration_and_nothing_is_invented() -> None:
    """Not by its class and not by its name: a non-GARCH contestant that declares ``Sampling`` is
    found, the exact and analytic ones declare none and are absent, and an unmeasured one reads
    ``None`` (R115: "not measured" is not zero)."""
    unmeasured = _Sampled(Sampling(paths=1000), name="unmeasured")
    found = cohorts_module.monte_carlo_error(Cohort("probe", (HEAD, unmeasured)), 22)
    assert found.percent == {"unmeasured": None}
    # GJR *simulated*, the legal pairing kept so this proves GARCH is found by its method too.
    renamed = dataclasses.replace(GJR11_SKEWT, method="simulation", name="my_gjr")
    cohort = Cohort("probe", (HEAD, renamed, GARCH11_SKEWT))
    assert cohorts_module.monte_carlo_error(cohort, 66).percent == {"my_gjr": None}
    exact = cohorts_module.monte_carlo_error(Cohort("probe", (GJR11_SKEWT,)), 66)
    assert exact.percent == {} and exact.source is None, "exact: not simulated"
    baseline = cohorts_module.monte_carlo_error(BASELINE_COHORT, 66)
    assert baseline.percent == {} and baseline.source is None


def test_the_shipped_shootout_declares_no_figure_and_says_so_in_its_source() -> None:
    """EGARCH ships with no ``Measurement`` (R124), so at the one horizon it can be evaluated at
    the cohort carries one unmeasured figure and the sentence "no measurement exists" -- which no
    series' name can ever equal, so it can only withhold."""
    errors = cohorts_module.monte_carlo_error(SHOOTOUT, 1)
    assert errors.percent == {"egarch11_skewt": None}
    assert errors.source == "no measurement exists"
    for symbol in ("SPY", "QQQ", "IWM", "no"):
        for horizon in HORIZONS:
            assert errors.source != f"{symbol} h{horizon}"


def test_the_registered_read_withholds_exactly_egarch_at_h1_and_nothing_else() -> None:
    """The one-way door. Every cohort at every horizon it can be evaluated at, through the shipped
    figures and the shipped source and into ``analyse``: ``egarch11_skewt`` at h=1 is unclaimed and
    for the sentence it always had, and no other contestant at any horizon is withheld by this
    machinery. Retiring the table must change what is *printed*, never what is *claimed*."""
    for cohort in COHORTS.values():
        for horizon in cohort.horizons(HORIZONS):
            names = tuple(cohort.contestants)
            base = _named_contenders(60, 0, names, (0.0,) * len(names))
            series = dataclasses.replace(base, horizon=horizon)
            errors = cohorts_module.monte_carlo_error(cohort, horizon)
            comparison = analyse(
                series,
                monte_carlo_error=errors.percent,
                monte_carlo_source=errors.source,
                reps=200,
            )
            expected = ("egarch11_skewt",) if (cohort is SHOOTOUT and horizon == 1) else ()
            assert comparison.unclaimed == expected, (cohort.name, horizon)
            if expected:
                reasons = comparison.record()["unclaimed_reasons"]
                assert reasons == {
                    "egarch11_skewt": [
                        "egarch11_skewt's Monte Carlo error was not measured, "
                        "so no precision is established"
                    ]
                }


def test_the_description_does_not_say_it_was_measured_on_the_lack_of_a_measurement() -> None:
    series = _named_contenders(60, 0, ("har_levels", "random_walk", "egarch11_skewt"), (0.0,) * 3)
    errors = cohorts_module.monte_carlo_error(SHOOTOUT, 1)
    comparison = analyse(
        series, monte_carlo_error=errors.percent, monte_carlo_source=errors.source, reps=200
    )
    lines = describe(comparison)
    summary = [x for x in lines if x.startswith("  Monte Carlo error (R102)")]
    assert len(summary) == 1
    assert "on the no measurement" not in summary[0]
    assert "no measurement exists" in summary[0] and "egarch11_skewt not measured" in summary[0]


# ---------------------------------------------------------------------------
# The witness for R95: nothing in compare/ but the sink opens a path
# ---------------------------------------------------------------------------

# The spellings that reach a file. ``Path(...)`` itself is not one: constructing a path opens
# nothing, and ``__main__`` builds one to find the repository root.
OPENING_NAMES = {"open"}
OPENING_ATTRIBUTES = {
    "open",
    "write_text",
    "write_bytes",
    "read_text",
    "read_bytes",
    "to_json",
    "to_csv",
    "to_parquet",
    "to_pickle",
    "mkdir",
    "touch",
    "unlink",
    "dump",
}


def _opens(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in OPENING_NAMES:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in OPENING_ATTRIBUTES:
            found.append(f".{node.attr}")
    return found


def test_only_the_sink_opens_a_path() -> None:
    """R95: the comparison layer computes and hands results to a ``Sink``; it never opens a path.
    Every module in the package is scanned, so a new one is covered the day it is added."""
    modules = sorted(COMPARE_SRC.glob("*.py"))
    assert len(modules) >= 7, "the package shrank: the scan may be covering nothing"
    for module in modules:
        found = _opens(module.read_text(encoding="utf-8"))
        if module.name == "sink.py":
            assert {".open", ".mkdir"} <= set(found), (
                "sink.py is where the paths are opened; a scan that finds nothing there is blind"
            )
        else:
            assert not found, f"{module.name} opens a path ({found}): only the sink may"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("handle = open('x', 'a')\n", "open"),
        ("path.open('a')\n", ".open"),
        ("path.write_text('x')\n", ".write_text"),
        ("path.read_bytes()\n", ".read_bytes"),
        ("frame.to_json('x')\n", ".to_json"),
        ("frame.to_csv('x')\n", ".to_csv"),
        ("json.dump(x, handle)\n", ".dump"),
        ("path.parent.mkdir(parents=True)\n", ".mkdir"),
    ],
    ids=lambda value: value.strip(),
)
def test_the_path_scan_detects_each_spelling(source: str, expected: str) -> None:
    """The red case: a scan nobody has watched fail is a ban nobody can lean on."""
    assert expected in _opens(source)


def test_the_path_scan_ignores_constructing_a_path_and_prose() -> None:
    assert _opens("root = Path(__file__).resolve().parents[3]\n") == []
    assert _opens('"""opens a file, then write_text and open."""\nx = 1\n') == []


def test_no_compare_module_unlocks_the_holdout() -> None:
    from test_import_discipline import _unlocks

    for module in sorted(COMPARE_SRC.glob("*.py")):
        assert not _unlocks(module.read_text(encoding="utf-8")), module.name


# ---------------------------------------------------------------------------
# The entry point. R97: both CLIs call ``run_configuration``, and the four rulings that live in
# the driver loop are checked against **both**.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _ScaledForecaster:
    base: Forecaster
    factor: float

    @property
    def observations(self) -> int:
        return self.base.observations

    def forecast(self, horizon: int) -> Forecast:
        answer = self.base.forecast(horizon)
        return Forecast(answer.value * self.factor, answer.observations)


@dataclasses.dataclass(frozen=True)
class _Scaled:
    """A cheap fourth contestant: the random walk, scaled. Not a model anyone would ship."""

    base: Model
    factor: float
    name: str = "rw_scaled"

    @property
    def fit_depends_on_horizon(self) -> bool:
        return self.base.fit_depends_on_horizon

    @property
    def domain(self) -> Domain:
        return self.base.domain

    def fit(self, view: TrainingView) -> Forecaster:
        return _ScaledForecaster(self.base.fit(view), self.factor)


@dataclasses.dataclass(frozen=True)
class _Limited:
    """A baseline model that declares it answers only up to ``max_horizon``."""

    base: Model
    max_horizon: int
    name: str = "rw_limited"

    @property
    def fit_depends_on_horizon(self) -> bool:
        return self.base.fit_depends_on_horizon

    @property
    def domain(self) -> Domain:
        return Domain(max_horizon=self.max_horizon)

    def fit(self, view: TrainingView) -> Forecaster:
        return self.base.fit(view)


TOY_COHORT = Cohort(
    "toy-shootout", (*BASELINE_COHORT.models, _Scaled(BASELINE_COHORT.models[2], 1.3))
)
# A cohort a run is refused at h=5, and the unrestricted one a refusal should point at.
TOY_LIMITED = Cohort(
    "toy-limited", (*BASELINE_COHORT.models, _Limited(_Scaled(BASELINE_COHORT.models[2], 1.3), 1))
)
TOY_WIDE = Cohort("toy-wide", BASELINE_COHORT.models)
WINDOW = ["--from", "2024-03-01", "--to", "2024-06-28", "--minimum-training", "20"]


class _Entry:
    """One CLI under test, with the module-level names both of them resolve at run time."""

    def __init__(self, name: str, module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from test_walkforward_cli import _settings_with_root, _toy_panel_and_targets_by_horizon

        self.name, self.module, self.root = name, module, tmp_path
        self.settings = _settings_with_root(load_settings(), tmp_path)
        panel, by_horizon = _toy_panel_and_targets_by_horizon()
        monkeypatch.setattr(module, "load_settings", lambda: self.settings)
        monkeypatch.setattr(module, "_persisted", lambda *_: None)
        monkeypatch.setattr(
            module, "load_for", lambda _s, _sym, horizon, **k: (panel, by_horizon[horizon])
        )
        if name == "compare":
            monkeypatch.setattr(module, "COHORTS", {TOY_COHORT.name: TOY_COHORT})
        self.monkeypatch = monkeypatch

    def main(self, argv: Sequence[str], **kwargs: object) -> int:
        extra = ["--cohort", TOY_COHORT.name] if self.name == "compare" else []
        return int(self.module.main(["run", *extra, *argv], **kwargs))

    def lines(self, ledger: Path) -> list[dict]:
        path = self.root / ledger
        return _lines(path) if path.exists() else []


@pytest.fixture(params=["walkforward", "compare"])
def entry(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> _Entry:
    module = walkforward_cli if request.param == "walkforward" else compare_cli
    return _Entry(request.param, module, tmp_path, monkeypatch)


def test_the_entry_point_has_no_end_argument_and_no_unlock_flag(entry: _Entry) -> None:
    for argv in (["--end", "2026-06-30"], ["--allow-holdout"], ["--unlock"], ["--holdout"]):
        with pytest.raises(SystemExit) as refused:
            entry.module.main(["run", *argv])
        assert refused.value.code == 2


def test_the_entry_point_refuses_a_to_past_dev_end(entry: _Entry, capsys) -> None:
    """R62, against both entry points: refused loudly, not clamped, and nothing is written."""
    past = entry.settings.splits.dev_end + dt.timedelta(days=1)
    assert entry.main(["--to", past.isoformat()]) == 1
    printed = capsys.readouterr().out
    assert "past dev_end" in printed and "sealed" in printed
    assert str(entry.settings.splits.dev_end) in printed
    assert not (entry.root / "data").exists()


def test_an_audit_violation_exits_non_zero(entry: _Entry, capsys) -> None:
    """R63, against both: provoked by making ``knowable_targets`` return the whole frame."""
    entry.monkeypatch.setattr(wf, "knowable_targets", lambda frame, origin: frame)
    code = entry.main(["--symbol", "SPY", "--horizon", "1", "--every", "2", *WINDOW])
    printed = capsys.readouterr().out
    assert code == 2, printed
    assert "REFUSED" in printed and "read past their cutoff" in printed
    assert count_trials(entry.root) == 0 and entry.lines(COMPARISON_LEDGER) == []


def test_a_later_refusal_leaves_the_earlier_trials_counted_exactly_once(
    entry: _Entry, capsys
) -> None:
    """R99, against both. SPY h1 succeeds and SPY h5 trips the audit: the first configuration's
    trials are on the ledger once, under the run's one timestamp, and nothing is appended twice
    by a flush R74 superseded. For the shootout the same holds for its comparison records."""
    seen = {"configurations": 0}

    def violations(self: wf.RunAudit) -> list[tuple[str, dt.date, dt.date]]:
        seen["configurations"] += 1
        return (
            []
            if seen["configurations"] == 1
            else [("panel", dt.date(2024, 4, 1), dt.date(2024, 4, 30))]
        )

    entry.monkeypatch.setattr(wf.RunAudit, "violations", violations)
    code = entry.main(
        ["--symbol", "SPY", "--horizon", "1", "--horizon", "5", "--every", "2", *WINDOW]
    )
    capsys.readouterr()
    assert code == 2 and seen["configurations"] == 2

    cohort = BASELINE_COHORT if entry.name == "walkforward" else TOY_COHORT
    assert count_trials(entry.root) == len(cohort.models) * len(LOSSES)
    lines = entry.lines(TRIAL_LEDGER)
    assert {line["horizon"] for line in lines} == {1}
    assert len({line["at"] for line in lines}) == 1
    if entry.name == "compare":
        comparisons = entry.lines(COMPARISON_LEDGER)
        assert sorted(line["loss"] for line in comparisons) == sorted(LOSSES)
        assert {line["horizon"] for line in comparisons} == {1}
        assert {line["at"] for line in comparisons} == {line["at"] for line in lines}


def test_every_ledger_line_names_the_tree_that_produced_it(entry: _Entry, capsys) -> None:
    """R70, against both entry points rather than ``append_trials`` alone: the sha and dirty flag
    are captured once, at the start, and reach every line of both files."""
    calls: list[object] = []

    def fake_state(*scope: object) -> tuple[str, bool | None]:
        calls.append(scope)
        return "f" * 40, True

    entry.monkeypatch.setattr(entry.module, "git_state", fake_state)
    assert entry.main(["--symbol", "SPY", "--horizon", "1", "--every", "2", *WINDOW]) == 0
    capsys.readouterr()
    assert len(calls) == 1, "the state is read once, at the start of the run"
    ledgers = [TRIAL_LEDGER] + ([COMPARISON_LEDGER] if entry.name == "compare" else [])
    for ledger in ledgers:
        lines = entry.lines(ledger)
        assert lines, ledger
        assert {line["git_sha"] for line in lines} == {"f" * 40}
        assert {line["git_dirty"] for line in lines} == {True}
        assert len({line["at"] for line in lines}) == 1


def test_an_unknown_git_state_is_not_reported_as_clean(entry: _Entry, capsys) -> None:
    entry.monkeypatch.setattr(entry.module, "git_state", lambda *_: ("unknown", None))
    entry.main(["--symbol", "SPY", "--horizon", "1", "--every", "2", *WINDOW])
    printed = capsys.readouterr().out
    assert "UNKNOWN" in printed
    assert "clean" not in printed.replace("UNKNOWN", "")


def test_the_driver_is_called_rather_than_reimplemented(entry: _Entry, capsys) -> None:
    """R97: both entry points reach ``run_configuration``; the shootout has no loop of its own."""
    calls: list[str] = []
    original = wf.run_configuration

    def spy(*args: object, **kwargs: object) -> wf.ConfigurationOutcome:
        calls.append(entry.name)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    entry.monkeypatch.setattr(entry.module, "run_configuration", spy)
    assert entry.main(["--symbol", "SPY", "--horizon", "1", "--every", "2", *WINDOW]) == 0
    capsys.readouterr()
    assert calls == [entry.name]


# -- the shootout only --------------------------------------------------------


@pytest.fixture
def shootout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Entry:
    return _Entry("compare", compare_cli, tmp_path, monkeypatch)


def test_a_shootout_run_writes_trials_and_comparisons_under_one_join_key(
    shootout: _Entry, capsys
) -> None:
    code = shootout.main(["--symbol", "SPY", "--horizon", "1", "--horizon", "5", *WINDOW])
    printed = capsys.readouterr().out
    assert code == 0, printed
    trials = shootout.lines(TRIAL_LEDGER)
    comparisons = shootout.lines(COMPARISON_LEDGER)
    assert len(trials) == 2 * len(TOY_COHORT.models) * len(LOSSES)
    assert len(comparisons) == 2 * len(LOSSES)
    assert {line["cohort"] for line in trials + comparisons} == {TOY_COHORT.name}
    assert {line["every"] for line in trials} == {5}, "the default stride is the design's"
    assert {line["stride"] for line in comparisons} == {5}

    def key(line: dict) -> tuple:
        return tuple(line[k] for k in ("git_sha", "at", "symbol", "horizon", "loss", "cohort"))

    assert {key(line) for line in comparisons} == {key(line) for line in trials}
    assert "MCS 25%" in printed and "Monte Carlo error" not in printed, "no simulated contestant"
    assert "trial line(s) on the ledger" in printed, "the count must sit beside each ranking"
    assert f"{len(comparisons)} comparison record(s)" in printed


def test_the_shootout_counts_the_baseline_models_it_was_given(shootout: _Entry, capsys) -> None:
    shootout.main(["--symbol", "SPY", "--horizon", "1", *WINDOW])
    capsys.readouterr()
    models = {line["model"] for line in shootout.lines(TRIAL_LEDGER)}
    assert models == set(TOY_COHORT.contestants)


class _ListSink:
    """A second ``Sink``: it keeps what it is handed and touches no file."""

    def __init__(self) -> None:
        self.trials: list[Trial] = []
        self.comparisons: list[Comparison] = []
        self.stamps: set[tuple] = set()

    def append_trials(self, trials, *, at, sha, dirty) -> int:
        self.trials.extend(trials)
        self.stamps.add((at, sha, dirty))
        return len(trials)

    def append_comparisons(self, records, *, at, sha, dirty) -> int:
        self.comparisons.extend(records)
        self.stamps.add((at, sha, dirty))
        return len(records)


class _Registry:
    """The toy registry the refusal tests run against: one limited cohort, one that can answer."""

    def __init__(self, shootout: _Entry) -> None:
        shootout.monkeypatch.setattr(
            compare_cli, "COHORTS", {TOY_LIMITED.name: TOY_LIMITED, TOY_WIDE.name: TOY_WIDE}
        )
        self.entry = shootout
        self.sink = _ListSink()

    def run(self, *argv: str) -> int:
        return int(compare_cli.main(["run", "--cohort", TOY_LIMITED.name, *argv], sink=self.sink))


@pytest.fixture
def registry(shootout: _Entry) -> _Registry:
    return _Registry(shootout)


def test_the_compare_cli_refuses_a_cohort_above_its_horizon_and_writes_nothing(
    registry: _Registry, capsys
) -> None:
    """The red case that matters most. The trap is not "a wrong number" but "a plausible number",
    so the assertion that matters is that no number was produced: on the real cohort the old
    behaviour was 7 contestants x 3 symbols x 2 losses of ordinary-looking lines, one of them an
    estimate of infinity."""
    code = registry.run("--symbol", "SPY", "--horizon", "5", *WINDOW)
    printed = capsys.readouterr().out
    assert code == 1, printed
    assert "'rw_limited'" in printed and "h<=1" in printed
    assert registry.sink.trials == [] and registry.sink.comparisons == []
    assert not (registry.entry.root / "data").exists()
    assert "git " not in printed and "storage root" not in printed, "it refused before any work"


def test_the_printed_refusal_is_the_cohorts_own_text(registry: _Registry, capsys) -> None:
    """A guard, not a second rule: the CLI and ``evaluate`` cannot word it differently."""
    registry.run("--symbol", "SPY", "--horizon", "5", *WINDOW)
    reason = TOY_LIMITED.refusal(5)
    assert reason is not None and reason in capsys.readouterr().out


def test_the_refusal_names_a_cohort_that_can_answer_the_horizon(
    registry: _Registry, capsys
) -> None:
    """A refusal nobody can act on is a worse trap than the original."""
    registry.run("--symbol", "SPY", "--horizon", "5", *WINDOW)
    printed = capsys.readouterr().out
    assert TOY_WIDE.name in printed and TOY_LIMITED.name not in printed.split("instead")[-1]


def test_the_refusal_says_so_when_no_registered_cohort_can_answer(
    registry: _Registry, capsys
) -> None:
    registry.entry.monkeypatch.setattr(compare_cli, "COHORTS", {TOY_LIMITED.name: TOY_LIMITED})
    assert registry.run("--symbol", "SPY", "--horizon", "5", *WINDOW) == 1
    assert "no registered cohort" in capsys.readouterr().out


def test_one_out_of_domain_horizon_among_several_refuses_the_whole_run(
    registry: _Registry, capsys
) -> None:
    """Not a partial run: h=1 is answerable, but an operator who named h=5 meant it, and the
    refusal comes before h=1 is evaluated so there is nothing half-written to explain."""
    code = registry.run("--symbol", "SPY", "--horizon", "1", "--horizon", "5", *WINDOW)
    capsys.readouterr()
    assert code == 1 and registry.sink.trials == []


def test_the_real_shootout_is_refused_above_h1_before_anything_is_read(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The invocation the story exists for, against the real registry and the real settings. It
    must return before a git state, a storage read or a fit. Both are armed to fail loudly, so a
    regression of the guard is an assertion and not a twenty-minute run of the real shootout."""

    def unreachable(*_: object, **__: object) -> None:
        raise AssertionError("the refusal came after work had started")

    monkeypatch.setattr(compare_cli, "load_for", unreachable)
    monkeypatch.setattr(compare_cli, "git_state", unreachable)
    for horizon in ("5", "22", "66"):
        sink = _ListSink()
        code = compare_cli.main(
            ["run", "--cohort", SHOOTOUT.name, "--horizon", horizon, "--symbol", "SPY"], sink=sink
        )
        printed = capsys.readouterr().out
        assert code == 1, printed
        assert "'egarch11_skewt'" in printed and "h<=1" in printed
        assert MULTISTEP.name in printed
        assert sink.trials == [] and sink.comparisons == []


def test_the_default_horizons_are_the_cohorts_own_and_the_run_says_why(
    registry: _Registry, capsys
) -> None:
    """A bare ``run`` over a cohort limited to h=1 evaluates h=1 only, and does not make the
    operator pass a flag to get correct behaviour."""
    code = registry.run("--symbol", "SPY", *WINDOW)
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert {trial.horizon for trial in registry.sink.trials} == {1}
    assert "SPY h1:" in printed and "SPY h5:" not in printed
    assert "--horizon" in printed and "'rw_limited'" in printed, "it must say why"


def test_the_default_says_nothing_when_it_narrows_nothing(shootout: _Entry, capsys) -> None:
    sink = _ListSink()
    code = compare_cli.main(
        ["run", "--cohort", TOY_COHORT.name, "--symbol", "SPY", "--horizon", "1", *WINDOW],
        sink=sink,
    )
    printed = capsys.readouterr().out
    assert code == 0
    assert "defaults to" not in printed


def test_a_cohort_that_answers_no_configured_horizon_is_refused(shootout: _Entry, capsys) -> None:
    nothing = Cohort("toy-nothing", (_Limited(BASELINE_COHORT.models[0], 0),))
    shootout.monkeypatch.setattr(compare_cli, "COHORTS", {nothing.name: nothing})
    sink = _ListSink()
    code = compare_cli.main(
        ["run", "--cohort", nothing.name, "--symbol", "SPY", *WINDOW], sink=sink
    )
    printed = capsys.readouterr().out
    assert code == 1 and "answers none" in printed and sink.trials == []


def test_a_run_through_another_sink_writes_no_file_at_all(shootout: _Entry, capsys) -> None:
    """The boundary is real: with a sink that keeps results in memory, a full run leaves no
    ledger anywhere, so the file-writing is the sink's and nothing else's. This is what leaves
    ``MlflowSink`` reachable without touching ``compare/``."""
    sink = _ListSink()
    assert isinstance(sink, Sink)
    code = shootout.main(["--symbol", "SPY", "--horizon", "1", *WINDOW], sink=sink)
    capsys.readouterr()
    assert code == 0
    assert len(sink.trials) == len(TOY_COHORT.models) * len(LOSSES)
    assert len(sink.comparisons) == len(LOSSES)
    assert len(sink.stamps) == 1, "trials and comparisons share one (at, sha, dirty)"
    assert not (shootout.root / "data").exists()
    assert not list(shootout.root.iterdir()), "something wrote to the storage root"


def test_the_drivers_trial_lines_carry_the_error_of_a_simulated_contestant(
    shootout: _Entry, capsys
) -> None:
    """``run_configuration`` prints one line per trial and those are where a reader first sees a
    mean. The shootout passes a ``report`` that appends the measured error to a simulated
    contestant's line (R102), and leaves every other line alone."""
    shootout.monkeypatch.setattr(
        compare_cli,
        "monte_carlo_error",
        lambda cohort, horizon: MonteCarlo({"rw_scaled": 0.5, "ghost": None}, "SPY h1"),
    )
    shootout.main(["--symbol", "SPY", "--horizon", "1", *WINDOW])
    printed = capsys.readouterr().out.splitlines()
    scaled = [line for line in printed if line.startswith("  rw_scaled ")]
    assert len(scaled) == len(LOSSES) and all(
        line.endswith("[MC error 0.500% (SPY h1)]") for line in scaled
    )
    plain = [line for line in printed if line.startswith("  har_levels ")]
    assert plain and not any("MC error" in line for line in plain)
    assert any(line.startswith("  Monte Carlo error (R102)") for line in printed)


# -- provenance of the Monte Carlo error, and SPA under the same n_eff rule ---------------


def test_a_monte_carlo_error_cannot_be_recorded_without_the_series_it_came_from() -> None:
    """``3.273%`` beside an SPY result reads as EGARCH's error on SPY. It was measured on a
    synthetic probe series (R102), so the number does not travel without that."""
    series = _contenders(60, 0)
    with pytest.raises(ValueError, match="series"):
        analyse(series, monte_carlo_error={"a": 0.5})
    with pytest.raises(ValueError, match="series"):
        analyse(series, monte_carlo_error={"a": 0.5}, monte_carlo_source="")
    assert analyse(series).monte_carlo_source is None, "no simulated contestant, nothing to source"


def test_every_printed_monte_carlo_error_names_the_series_it_was_measured_on() -> None:
    comparison = _comparison(
        _big_evaluation(),
        monte_carlo_error={"rival": 3.273, "har_levels": None},
        monte_carlo_source="the probe's synthetic series, not SPY",
    )
    lines = describe(comparison)
    mentioning = [line for line in lines if "3.273%" in line]
    assert len(mentioning) >= 4, "cohort-wide lines, a pair line and the summary"
    assert all("the probe's synthetic series, not SPY" in line for line in mentioning), mentioning
    assert (
        comparison.record()["monte_carlo_error_source"] == "the probe's synthetic series, not SPY"
    )


def test_an_unmeasured_trial_line_says_so_and_names_no_series(capsys) -> None:
    """The line an operator sees for EGARCH at h=1: not measured, and the reason there is no
    series to name. It must not read as zero or as precision."""
    compare_cli._report(monte_carlo_error(SHOOTOUT, 1), "  egarch11_skewt mean=1.0")
    compare_cli._report(monte_carlo_error(SHOOTOUT, 1), "  har_levels mean=1.0")
    egarch, har = capsys.readouterr().out.splitlines()
    assert egarch.endswith("[MC error not measured (no measurement exists)]")
    assert "MC error" not in har


def test_spa_is_withheld_where_the_effective_sample_is_thin_exactly_as_dm_is() -> None:
    """SPA bootstraps the same overlapping series, so n_eff = 17 at h=66 does not become enough
    because the null changed. The ``claimed`` machinery is reused, not a second mechanism."""
    thin = analyse(_series_with_benchmarks(238, 13))
    assert thin.claims_withheld
    assert all(spa.refusal is None for spa in thin.spa), "the fixture must produce SPA p-values"
    assert not any(thin.spa_claimed(spa) for spa in thin.spa)
    assert all(entry["claimed"] is False for entry in thin.record()["spa"])  # type: ignore[union-attr]
    spa_lines = [line for line in describe(thin) if line.lstrip().startswith("SPA")]
    assert len(spa_lines) == 2
    assert all("n_eff=17.0" in x and "no significance claimed" in x for x in spa_lines)
    assert not any("=>" in line for line in spa_lines)


def test_spa_states_a_verdict_where_the_sample_supports_one() -> None:
    thick = analyse(_series_with_benchmarks(247, 4))
    assert not thick.claims_withheld
    spa_lines = [line for line in describe(thick) if line.lstrip().startswith("SPA")]
    assert all("no significance claimed" not in line and "=>" in line for line in spa_lines)
    assert all(entry["claimed"] is True for entry in thick.record()["spa"])  # type: ignore[union-attr]


def test_spa_is_withheld_when_a_contestant_it_tests_has_no_claim() -> None:
    """The test is over the whole cohort, so an over-floor contestant is inside it."""
    series = _series_with_benchmarks(247, 4)
    flagged = analyse(
        series,
        monte_carlo_error={"gjr11_skewt": 3.273},
        monte_carlo_source=series_source(series),
    )
    assert not flagged.claims_withheld and flagged.unclaimed == ("gjr11_skewt",)
    assert not any(flagged.spa_claimed(spa) for spa in flagged.spa)
    lines = [line for line in describe(flagged) if line.lstrip().startswith("SPA")]
    assert all("no claim" in line for line in lines)


def _series_with_benchmarks(n: int, q: int) -> LossSeries:
    """Contestants carrying the two SPA benchmark names and a simulated one."""
    base = _contenders(n, q, gaps=(0.0, 0.01, 0.02, 3.0))
    names = ("har_levels", "random_walk", "gjr11_skewt", "worse")
    return dataclasses.replace(
        base, values={name: v for name, v in zip(names, base.values.values(), strict=True)}
    )
