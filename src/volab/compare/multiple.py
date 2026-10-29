"""The stationary-bootstrap block length, the model confidence set and SPA (R93, R94).

``arch.bootstrap`` is the implementation of MCS and SPA (R82 declined to hand-roll a bootstrap: it
is the easiest place in this project to be subtly wrong, and an MCS we wrote would be evidence
about our bootstrap rather than about the models). What this module owns is everything *around*
that call, because that is where the mistakes this design has already made live.

**The block length comes from the loss LEVELS, never the differentials** (R93). ``block_length``
runs Politis-White on the matrix MCS and SPA consume. Two contestants forecasting one overlapping
target share the overlap, which cancels in their differential: Politis-White on the differential
collapses towards 1 and would resample heavily overlapping losses as if independent. Witness:
``test_the_block_length_is_estimated_from_loss_levels_not_from_cross_model_differentials``. The
figure per contestant is reduced by ``max`` -- the longest block preserves the most dependence, so
that is the conservative direction; the design does not say how to reduce a matrix, and this is the
choice made here. It is floored at ``q + 1`` so a series whose Politis-White block is near 1 is
never resampled as independent (``test_the_block_length_is_floored_by_the_overlap``).

**One bootstrap, two sizes.** MCS is run once and membership at 10% and 25% is read from the same
``pvalues``, so the two sets are nested by construction (two ``MCS(size=...)`` runs need not be).
Witness: ``test_one_bootstrap_serves_both_sizes_and_the_sets_are_nested``.

**What is unusable is refused with its reason** (R94). ``n_eff = n / (q + 1)`` is on every result,
and membership at size ``alpha`` is refused where ``n_eff < 2 / alpha`` -- 20 at 10% and 8 at 25%.
That threshold is *chosen*, not derived: a confidence set at size alpha needs the sample to be able
to place an observation in an alpha tail at all (``1/alpha``), with a factor of two for margin.
At h=66, n=238, q=13 it is 17.0, so the 10% set is refused and the 25% set reported
(``test_the_model_confidence_set_is_refused_where_the_effective_sample_is_too_small``). No stride
rescues this
(``test_effective_observations_are_invariant_to_the_stride_when_the_horizon_exceeds_it``).

**Degenerate input is detected, not silenced.** arch divides by a bootstrap spread that is zero for
two identical contestants, and Politis-White divides by zero below 11 origins (measured); under
``filterwarnings = ["error"]`` either is a failing test, and R101 is the precedent for detecting the
condition instead. Both are recorded refusals.

**So is a contestant that swamps the others' loss scale.** ``arch`` 8.0.0's ``MCS._compute_r`` reads
the eliminated model as ``loc.squeeze()[0]``, which is a scalar only when the argmax of the
standardized loss differential is unique. With one mean loss at 1.7e12 and the rest near 1e-5,
``L_i - L_j`` is below half an ulp of the large value, the standardized row is one repeated float,
and ``[0]`` returns an ``(i, j)`` *pair*: an ``ndarray`` in ``eliminated`` (an ``IndexError`` out of
``Index.__getitem__``) and two models eliminated in one step. The fix is **not** to format the
losses so arch never indexes that Index: measured, that turns the crash into a silently wrong set
that eliminates the SPA benchmark alongside the exploding contestant. The condition is detected in
``_unestimable`` instead, on the mean losses, with ``same_losses``' scaled tolerance one level up.
It is a conservative superset of arch's tie and refuses some matrices arch would survive; that is
the right direction. Witness, on its own line so it can be grepped:
``test_a_contestant_that_dominates_the_loss_scale_makes_the_confidence_set_a_refusal``.

``reps``, ``seed`` and the bootstrap are fixed and recorded on every result, so membership is
reproducible from the file (``test_the_bootstrap_is_reproducible_from_the_recorded_seed``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

import numpy as np
import pandas as pd
from arch.bootstrap import MCS, SPA, optimal_block_length

from volab.compare.dm import same_losses
from volab.compare.series import LossSeries

# The sizes MCS membership is reported at, the bootstrap's replications, its seed and its kind.
SIZES: tuple[float, ...] = (0.10, 0.25)
REPS = 10_000
SEED = 20261025
BOOTSTRAP: Final = "stationary"
# arch's elimination rule. The default, named so it is on the record rather than assumed.
METHOD: Final = "R"

# Politis-White divides by zero below this many origins (measured: every one of 20 random 3-column
# panels failed at n = 9 and 10, none at n = 11 through 23).
MINIMUM_ORIGINS = 11


@dataclass(frozen=True)
class BlockLength:
    """The block length and both of its inputs, so the record can show how it was chosen."""

    # Politis-White on the loss levels, the longest across contestants.
    politis_white: float
    # ``q + 1``: the shortest block that does not resample an overlapping series as independent.
    overlap_floor: int
    # ``max(overlap_floor, ceil(politis_white))``.
    length: int


def effective_observations(n: int, q: int) -> float:
    """``n / (q + 1)``: the independent origins an MA(``q``) series of length ``n`` is worth."""
    return n / (q + 1)


def politis_white(levels: np.ndarray) -> float:
    """Politis-White's stationary-bootstrap block length, longest across the columns of ``levels``.

    A function of the matrix it is given, which is the point: callers hand it **levels**.
    """
    frame = pd.DataFrame(np.asarray(levels, dtype="float64").reshape(len(levels), -1))
    figures = optimal_block_length(frame)["stationary"]
    value = float(figures.max())
    if not math.isfinite(value):
        raise ValueError("Politis-White returned a non-finite block length")
    return value


def _matrix(series: LossSeries) -> pd.DataFrame:
    return pd.DataFrame({name: list(values) for name, values in series.values.items()})


def _swamped(columns: dict[str, np.ndarray]) -> str | None:
    """Two contestants whose mean losses float64 cannot tell apart at the scale of the largest.

    Refuses if for some contestant ``i`` there are two others ``j != k`` with
    ``|mean_j - mean_k| <= 4 * eps * max_m |mean_m|``: then ``mean_i - mean_j`` and
    ``mean_i - mean_k`` are the same float when ``i`` dominates, the standardized differentials tie
    and arch's elimination is ill-defined (see the module docstring). With three or more
    contestants that is any such pair, since any third contestant can be ``i``; the largest mean is
    the one named, because it sets the scale that annihilates the difference.
    """
    if len(columns) < 3:
        return None
    means = {name: float(np.mean(column)) for name, column in columns.items()}
    dominant = max(means, key=lambda name: abs(means[name]))
    tolerance = 4 * np.finfo(np.float64).eps * abs(means[dominant])
    names = list(means)
    for position, j in enumerate(names):
        for k in names[position + 1 :]:
            if abs(means[j] - means[k]) <= tolerance:
                return (
                    f"{dominant} dominates the loss scale (mean loss {means[dominant]:.3g}) and "
                    f"the mean losses of {j} and {k} differ by {abs(means[j] - means[k]):.3g}, "
                    f"within float64 rounding of it: its differentials against the others are "
                    f"equal to rounding, so no confidence set can be formed"
                )
    return None


def _unestimable(
    series: LossSeries, names: tuple[str, ...] | None = None, *, confidence_set: bool = True
) -> str | None:
    """Why the bootstrap cannot be run on ``series``, or ``None``. Detected, never caught.

    ``names`` restricts the identical-pair check to pairs involving the first name (SPA only
    differences against its benchmark); ``None`` checks every pair. ``confidence_set=False`` skips
    the check that is arch's MCS elimination alone (SPA never reaches it).
    """
    if len(series.values) < 2:
        return f"{len(series.values)} contestant(s): there is nothing to compare"
    if series.n < MINIMUM_ORIGINS:
        return f"{series.n} origin(s), fewer than the {MINIMUM_ORIGINS} Politis-White needs"
    columns = {name: np.asarray(values, dtype="float64") for name, values in series.values.items()}
    for name, column in columns.items():
        if same_losses(column, np.zeros_like(column)):
            return f"{name} has a constant loss, which has no bootstrap spread"
    pairs = (
        [(names[0], other) for other in columns if other != names[0]]
        if names
        else [(a, b) for i, a in enumerate(columns) for b in list(columns)[i + 1 :]]
    )
    for a, b in pairs:
        if same_losses(columns[a], columns[b]):
            return f"{a} and {b} have identical losses, so their bootstrap spread is zero"
    if confidence_set:
        return _swamped(columns)
    return None


def block_length(series: LossSeries, *, q: int, confidence_set: bool = True) -> BlockLength:
    """``max(q + 1, ceil(Politis-White on the loss levels))``, from the matrix MCS consumes.

    ``confidence_set=False`` is SPA's: it is refused only for what SPA cannot survive.
    """
    reason = _unestimable(series, confidence_set=confidence_set)
    if reason is not None:
        raise ValueError(f"no block length can be estimated: {reason}")
    pw = politis_white(_matrix(series).to_numpy())
    floor = q + 1
    return BlockLength(politis_white=pw, overlap_floor=floor, length=max(floor, math.ceil(pw)))


@dataclass(frozen=True)
class McsResult:
    n: int
    q: int
    n_eff: float
    # ``None`` when no size reached the bootstrap.
    block: BlockLength | None
    reps: int
    seed: int
    bootstrap: str
    # arch's MCS p-values, one bootstrap for every size. ``None`` when none was run.
    pvalues: dict[str, float] | None
    # Size -> members, in cohort order, for the sizes that were reported.
    members: dict[float, tuple[str, ...]]
    # Size -> the reason, for the sizes that were refused.
    refusals: dict[float, str]


def model_confidence_set(
    series: LossSeries,
    *,
    q: int,
    sizes: tuple[float, ...] = SIZES,
    reps: int = REPS,
    seed: int = SEED,
) -> McsResult:
    """MCS membership at each size in ``sizes``, from one bootstrap, or the reason it is refused."""
    n = series.n
    n_eff = effective_observations(n, q)
    refusals: dict[float, str] = {}
    reportable: list[float] = []
    for size in sizes:
        if len(series.values) < 2:
            refusals[size] = f"{len(series.values)} contestant(s): there is nothing to compare"
        elif n_eff < 2 / size:
            refusals[size] = (
                f"n_eff = {n_eff:.1f} is below 2/alpha = {2 / size:.0f} at alpha = {size:g}: "
                "too few effective observations for a confidence set at this size"
            )
        else:
            reportable.append(size)

    def result(
        block: BlockLength | None,
        pvalues: dict[str, float] | None,
        members: dict[float, tuple[str, ...]],
    ) -> McsResult:
        return McsResult(n, q, n_eff, block, reps, seed, BOOTSTRAP, pvalues, members, refusals)

    if not reportable:
        return result(None, None, {})
    reason = _unestimable(series)
    if reason is not None:
        refusals.update({size: reason for size in reportable})
        return result(None, None, {})

    block = block_length(series, q=q)
    # ``size`` only decides arch's ``included``; the p-values run the whole elimination path, and
    # membership at any size is read below from this one bootstrap. The rule is arch's own,
    # ``pvalue > size``: Hansen-Lunde-Nason write ``>=``, which differs only at an exact tie
    # (``p`` is a multiple of ``1/reps``, so ~1 in 10,000), and matching ``MCS(size).included``
    # keeps the membership checkable against the library it comes from.
    procedure = MCS(
        _matrix(series),
        min(sizes),
        reps=reps,
        block_size=block.length,
        method=METHOD,
        bootstrap=BOOTSTRAP,
        seed=seed,
    )
    procedure.compute()
    pvalues = {str(name): float(p) for name, p in procedure.pvalues["Pvalue"].items()}
    members = {
        size: tuple(name for name in series.values if pvalues[name] > size) for size in reportable
    }
    return result(block, pvalues, members)


@dataclass(frozen=True)
class SpaResult:
    benchmark: str
    n: int
    q: int
    n_eff: float
    block: BlockLength | None
    reps: int
    seed: int
    # Hansen's three p-values. ``consistent`` is the headline.
    lower: float | None
    consistent: float | None
    upper: float | None
    refusal: str | None


def superior_predictive_ability(
    series: LossSeries, benchmark: str, *, q: int, reps: int = REPS, seed: int = SEED
) -> SpaResult:
    """Hansen's SPA: does any other contestant beat ``benchmark``? Small p says one does.

    **No ``n_eff`` refusal here.** R94 refuses MCS membership; for SPA the figure is printed beside
    the p-value and the reader discounts it. Only the structural impossibilities are refused.
    """
    if benchmark not in series.values:
        raise ValueError(f"benchmark {benchmark!r} is not among {list(series.values)}")
    n = series.n
    n_eff = effective_observations(n, q)

    def refused(reason: str) -> SpaResult:
        return SpaResult(benchmark, n, q, n_eff, None, reps, seed, None, None, None, reason)

    reason = _unestimable(series, names=(benchmark,), confidence_set=False)
    if reason is not None:
        return refused(reason)
    block = block_length(series, q=q, confidence_set=False)
    losses = _matrix(series)
    procedure = SPA(
        losses[benchmark],
        losses.drop(columns=benchmark),
        block_size=block.length,
        reps=reps,
        bootstrap=BOOTSTRAP,
        seed=seed,
    )
    procedure.compute()
    p = procedure.pvalues
    return SpaResult(
        benchmark,
        n,
        q,
        n_eff,
        block,
        reps,
        seed,
        float(p["lower"]),
        float(p["consistent"]),
        float(p["upper"]),
        None,
    )
