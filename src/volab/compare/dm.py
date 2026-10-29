"""Diebold-Mariano on overlapping targets (R92).

Origins come at a stride of ``s`` sessions and the loss at an origin depends on the returns
``r[t+1 .. t+h]``. Two origins ``m`` strides apart share a return iff ``m*s <= h - 1``, so the
loss differential is MA(q) in origin index with ``q = floor((h - 1) / s)`` (:func:`overlap_lag`).
At stride 5 that is ``q = 0, 0, 4, 13`` for ``h = 1, 5, 22, 66`` -- **h=5 has no overlap**, its
windows ``t+1..t+5`` and ``t+6..t+10`` being disjoint. Witness:
``test_the_overlap_lag_is_the_largest_separation_that_still_shares_a_return``, which counts the
shared returns by intersecting the two index sets rather than restating the formula.

Two columns are computed for every pair, from the same differential ``d = L_left - L_right``.

**The headline** pairs the **rectangular** HAC estimator truncated at exactly ``q`` with the
Harvey-Leybourne-Newbold correction at ``h_eff = q + 1`` against ``t(n - 1)``. That pairing is
the point: the correction is derived for the rectangular estimator at the horizon's own MA order.
``h_eff`` is the horizon **in sampling units**; the daily ``h`` is the default that looks fine and
is wrong (0.725 against 0.943 at h=66, n=238 --
``test_the_harvey_correction_uses_sampling_units_not_the_daily_horizon``). At ``q = 0`` the
headline reduces to Student's t exactly, which is the independent check:
``test_the_headline_statistic_at_q_zero_is_exactly_the_one_sample_t_statistic``.

**The second column** is Bartlett / Newey-West at ``L = max(q, floor(4 (n/100)^(2/9)))`` with no
Harvey factor and a standard-normal reference. It exists because at h=1 and h=5 the headline is the
plain sample variance while volatility clustering leaves QLIKE and squared-error losses
autocorrelated anyway. **Where the two columns reach different 5% verdicts, both are printed and
neither is claimed** (:attr:`DMResult.disagree`).

**A non-positive variance is a recorded refusal, checked before any square root.** The rectangular
estimator is not positive semi-definite, so ``V <= 0`` is reachable
(``test_a_non_positive_rectangular_hac_variance_is_refused_before_the_square_root``), and a numpy
``RuntimeWarning`` is a failing test in this repository rather than a NaN. ``V == 0`` -- two
identical contestants -- has its own reason
(``test_an_identical_pair_of_contestants_is_refused_rather_than_divided_by_zero``), and is
detected on the differential, not on ``V``: a constant ``d`` minus its own float mean leaves
about 1e-17, which would pass a ``V == 0`` test and explode the statistic
(``test_a_constant_nonzero_differential_is_refused_not_explosive``).

numpy and scipy are admitted here (R82); this module is not on the point-in-time path.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats

from volab.compare.series import LossSeries


def overlap_lag(horizon: int, stride: int) -> int:
    """``q = floor((h - 1) / s)``: the MA order of the loss differential in origin index."""
    if horizon < 1 or stride < 1:
        raise ValueError(f"horizon and stride must be positive, got {horizon} and {stride}")
    return (horizon - 1) // stride


def newey_west_lag(n: int) -> int:
    """Newey-West's own automatic truncation, ``floor(4 (n/100)^(2/9))``: 4 at n = 238 to 251."""
    return int(math.floor(4 * (n / 100) ** (2 / 9)))


def bartlett_lag(n: int, q: int) -> int:
    """The Bartlett column's lag: never below the overlap order, whatever ``n`` suggests."""
    return max(q, newey_west_lag(n))


def harvey_factor(n: int, h_eff: int) -> float:
    """Harvey-Leybourne-Newbold's ``sqrt((n + 1 - 2h + h(h - 1)/n) / n)``, ``h`` in sampling units.

    Pass ``q + 1``, never the daily horizon. Defined here only for ``n > h_eff``: below that the
    numerator can stay positive while meaning nothing, and the correction was derived for a
    sample longer than the horizon it corrects.
    """
    if h_eff < 1 or n <= h_eff:
        raise ValueError(f"the correction needs 1 <= h_eff < n, got h_eff={h_eff} and n={n}")
    return math.sqrt((n + 1 - 2 * h_eff + h_eff * (h_eff - 1) / n) / n)


@dataclass(frozen=True)
class Column:
    """One variance estimator's answer: a statistic and p-value, or the reason for neither."""

    statistic: float | None
    p_value: float | None
    # The long-run variance estimate ``V`` the statistic divided by. Kept on a refusal for a
    # non-positive one, so the reader can see how negative.
    variance: float | None
    refusal: str | None

    def verdict(self, alpha: float = 0.05) -> int | None:
        """``-1`` left significantly better, ``+1`` right significantly better, ``0`` neither.

        ``None`` for a refused column. The sign follows ``d = L_left - L_right``: a loss is
        smaller when better, so a negative statistic favours the left contestant.
        """
        if self.statistic is None or self.p_value is None:
            return None
        if self.p_value >= alpha:
            return 0
        return -1 if self.statistic < 0 else 1


@dataclass(frozen=True)
class DMResult:
    n: int
    mean_difference: float
    # The overlap order, the Bartlett lag, and ``h_eff = q + 1``.
    q: int
    bartlett_lag: int
    h_eff: int
    # The Harvey factor the headline used, ``None`` where it was not reached.
    harvey: float | None
    headline: Column
    bartlett: Column

    @property
    def disagree(self) -> bool:
        """The columns reach different 5% verdicts. A refusal is not a disagreement.

        Both statistics are ``d_bar`` over a positive denominator, so their *signs* can never
        differ; what differs is whether each clears 5%, or in principle which side it favours.
        """
        left, right = self.headline.verdict(), self.bartlett.verdict()
        return left is not None and right is not None and left != right


def same_losses(left: np.ndarray, right: np.ndarray) -> bool:
    """The two series differ by a constant, to the rounding of their own subtraction.

    The rounding in ``left - right`` is bounded by the size of the *operands*, not of the
    difference: a constant gap of 0.3 between losses near 4 wobbles by ~1e-16, which is larger than
    4 eps times 0.3. So the tolerance is scaled by the larger operand. Measured by
    ``test_a_constant_nonzero_differential_is_refused_not_explosive``. Zero is the case that
    matters (two identical contestants); a constant nonzero gap has the same zero variance.
    """
    scale = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))))
    return bool(float(np.ptp(left - right)) <= 4 * np.finfo(np.float64).eps * scale)


def _refused(reason: str) -> Column:
    return Column(None, None, None, reason)


def _autocovariances(centred: np.ndarray, lags: int) -> list[float]:
    """``gamma_k = (1/n) sum_{i=k..n-1} c_i c_{i-k}``, ``k = 0..lags``; zero beyond the sample."""
    n = len(centred)
    return [
        float(np.dot(centred[k:], centred[: n - k])) / n if k < n else 0.0 for k in range(lags + 1)
    ]


def diebold_mariano(left: Sequence[float], right: Sequence[float], *, q: int) -> DMResult:
    """Both DM columns for ``d = left - right``, at overlap order ``q``.

    ``left`` and ``right`` are the two contestants' per-origin losses, aligned. A negative
    statistic favours ``left``.
    """
    if len(left) != len(right):
        raise ValueError(f"the two loss series differ in length: {len(left)} and {len(right)}")
    if q < 0:
        raise ValueError(f"the overlap order cannot be negative, got {q}")
    a, b = np.asarray(left, dtype="float64"), np.asarray(right, dtype="float64")
    d = a - b
    n = int(d.size)
    h_eff = q + 1
    lag = bartlett_lag(n, q)

    def both_refused(reason: str, mean: float = float("nan")) -> DMResult:
        return DMResult(n, mean, q, lag, h_eff, None, _refused(reason), _refused(reason))

    if n <= h_eff:
        return both_refused(
            f"{n} origin(s) is not more than the {h_eff} an MA({q}) differential spans"
        )
    if not np.all(np.isfinite(d)):
        return both_refused("a loss in this pair is not finite")
    mean = float(d.mean())
    if same_losses(a, b):
        # Two contestants with identical losses (or a constant gap): V is zero by construction,
        # and is not computed, because a float mean leaves a residue that would pass ``V == 0``.
        return both_refused(
            "the loss differential is constant (identical contestants): V == 0", mean
        )

    centred = d - mean
    gamma = _autocovariances(centred, max(q, lag))

    # The rectangular estimator: every autocovariance to q, at full weight. Not PSD.
    rectangular = gamma[0] + 2 * sum(gamma[1 : q + 1])
    # Bartlett / Newey-West: weights 1 - k/(L+1), PSD, so never refused for sign.
    bartlett = gamma[0] + 2 * sum((1 - k / (lag + 1)) * gamma[k] for k in range(1, lag + 1))

    factor = harvey_factor(n, h_eff)
    if rectangular <= 0:
        headline = Column(
            None,
            None,
            rectangular,
            f"the rectangular HAC variance is not positive (V = {rectangular:.6g}) at q = {q}",
        )
    else:
        statistic = mean / math.sqrt(rectangular / n) * factor
        headline = Column(
            statistic, float(2 * stats.t.sf(abs(statistic), n - 1)), rectangular, None
        )
    if bartlett <= 0:
        # Unreachable for a PSD estimator on a non-constant ``d`` bar rounding; recorded rather
        # than assumed, because a square root of a negative is a failing test here.
        second = Column(
            None, None, bartlett, f"the Bartlett variance is not positive (V = {bartlett:.6g})"
        )
    else:
        z = mean / math.sqrt(bartlett / n)
        second = Column(z, float(2 * stats.norm.sf(abs(z))), bartlett, None)
    return DMResult(n, mean, q, lag, h_eff, factor, headline, second)


@dataclass(frozen=True)
class PairResult:
    """``diebold_mariano(left, right)`` for one ordered pair of contestants."""

    left: str
    right: str
    dm: DMResult


def pairwise(series: LossSeries, *, q: int) -> tuple[PairResult, ...]:
    """Every unordered pair, in cohort order, as ``d = L_earlier - L_later``.

    ``k`` contestants make ``k(k - 1)/2`` tests, and **that count is the multiplicity** the
    record carries beside them: no selection is made on these p-values.
    """
    names = list(series.values)
    return tuple(
        PairResult(
            names[i],
            names[j],
            diebold_mariano(series.values[names[i]], series.values[names[j]], q=q),
        )
        for i in range(len(names))
        for j in range(i + 1, len(names))
    )
