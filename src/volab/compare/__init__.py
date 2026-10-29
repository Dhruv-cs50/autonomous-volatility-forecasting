"""Diebold-Mariano, the model confidence set and SPA over every contestant (US-3.4).

``analyse`` turns one :class:`LossSeries` into one :class:`Comparison`; ``describe`` prints it. The
statistics live in ``dm`` and ``multiple`` and the only writer is ``sink`` (R95). The ruling index:

* R92 -- the headline DM is a rectangular HAC at ``q = floor((h - 1)/s)`` with Harvey at
  ``h_eff = q + 1``, and a Bartlett column beside it;
* R93 -- the bootstrap block is ``max(q + 1, ceil(Politis-White on the loss levels))``;
* R94 -- ``n_eff = n/(q + 1)`` is printed beside every multiple-comparison figure and MCS
  membership is refused where ``n_eff < 2/alpha``;
* R95 -- results go to a ``Sink``; nothing here opens a path;
* R102 -- the measured Monte Carlo error is printed beside every simulated contestant, and no
  significance is claimed for one above ``MC_ERROR_CEILING_PERCENT``, **measured on a series
  other than the one compared, or not measured at all**: only a figure measured on the compared
  series can authorise a claim, and everything else withholds one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from volab.compare.dm import DMResult, PairResult, bartlett_lag, overlap_lag, pairwise
from volab.compare.multiple import (
    BOOTSTRAP,
    METHOD,
    REPS,
    SEED,
    SIZES,
    McsResult,
    SpaResult,
    effective_observations,
    model_confidence_set,
    superior_predictive_ability,
)
from volab.compare.series import LossSeries
from volab.compare.sink import Comparison

# SPA's benchmark is HAR in levels -- the question this project is organised around is whether
# anything beats HAR -- and a second against the random walk, because a contestant that cannot beat
# a scaled squared return is worth seeing.
BENCHMARKS: tuple[str, ...] = ("har_levels", "random_walk")

# R72's band for a difference worth believing is 0.2-1.5% of Monte Carlo error. A simulated
# contestant measured above the top of it has no significance claimed (R102): EGARCH at h=66 is
# 3.273% at S = 100,000. The ceiling can only *withhold*. Clearing it is no evidence of precision
# unless the figure was measured on the series being compared: EGARCH at h=22 is 0.163% on the
# probe's gentler synthetic process (alpha 0.05, beta 0.87) and nothing at all on SPY, whose fit
# is far harsher (alpha ~0.25, beta ~0.96). A six-seed sd of an estimator with **no finite mean**
# is not a measurement of it at any value -- R129 struck the "230.79% on SPY" this comment used
# to quote, both because no committed probe prints it and because a percentage error against a
# divergent quantity (R114) reads as an error bar for something that has none. No figure at all is
# the same error in its purest form: absence of measured spread is not presence of precision.
MC_ERROR_CEILING_PERCENT = 1.5


def series_source(series: LossSeries) -> str:
    """The name a Monte Carlo error carries when it was measured on exactly ``series``.

    A figure's ``monte_carlo_source`` is compared to this by equality: it is the only way a figure
    can clear :data:`MC_ERROR_CEILING_PERCENT` (see ``analyse``). The horizon is part of it because
    the error is a property of the forecast at one horizon on one symbol's history.
    """
    return f"{series.symbol} h{series.horizon}"


def analyse(
    series: LossSeries,
    *,
    monte_carlo_error: Mapping[str, float | None] | None = None,
    monte_carlo_source: str | None = None,
    benchmarks: tuple[str, ...] = BENCHMARKS,
    reps: int = REPS,
    seed: int = SEED,
) -> Comparison:
    """Every pairwise DM, the MCS at both sizes and SPA against each benchmark, for one series.

    ``monte_carlo_error`` maps each simulated contestant to its measured error in percent, or to
    ``None`` where none was measured. The caller supplies it because the figures belong to the
    model layer and this module imports no model. ``monte_carlo_source`` names the series they
    were measured on and is **required with them**: the number is printed beside a result, and
    without its source it reads as that result's own error.

    **A figure authorises a claim only if it was measured on this series**, i.e. its source equals
    :func:`series_source`. Three things put a contestant in ``unclaimed``: a figure above the
    ceiling, a figure measured on any other series (whatever its value), and **no figure**
    (``None``).
    The map holds simulated contestants only (``cohorts.monte_carlo_error`` builds it from
    ``method == "simulation"``), so a deterministic contestant has no entry and keeps its claims.
    Witnesses:
    ``test_a_figure_measured_on_another_series_can_withhold_a_claim_but_never_authorise_one`` and
    ``test_a_simulated_contestant_with_no_measurement_is_withheld``.
    """
    q = overlap_lag(series.horizon, series.stride)
    pairs = pairwise(series, q=q)
    mcs = model_confidence_set(series, q=q, reps=reps, seed=seed)
    spa: list[SpaResult] = []
    for benchmark in benchmarks:
        if benchmark in series.values:
            spa.append(superior_predictive_ability(series, benchmark, q=q, reps=reps, seed=seed))
        else:
            n_eff = effective_observations(series.n, q)
            spa.append(
                SpaResult(
                    benchmark,
                    series.n,
                    q,
                    n_eff,
                    None,
                    reps,
                    seed,
                    None,
                    None,
                    None,
                    f"the benchmark {benchmark!r} is not in this cohort",
                )
            )
    errors = dict(monte_carlo_error or {})
    if errors and not monte_carlo_source:
        raise ValueError(
            "a Monte Carlo error needs the series it was measured on (monte_carlo_source): "
            "printed bare it reads as the error on the series being compared"
        )
    block = mcs.block or next((s.block for s in spa if s.block is not None), None)
    return Comparison(
        symbol=series.symbol,
        horizon=series.horizon,
        loss=series.loss,
        cohort=series.cohort,
        stride=series.stride,
        origins=series.n,
        contestants=tuple(series.values),
        q=q,
        bartlett_lag=bartlett_lag(series.n, q),
        n_eff=effective_observations(series.n, q),
        block=block,
        reps=reps,
        seed=seed,
        bootstrap=BOOTSTRAP,
        method=METHOD,
        pairs=pairs,
        dm_tests=len(pairs),
        mcs=mcs,
        spa=tuple(spa),
        monte_carlo_error=errors,
        monte_carlo_source=monte_carlo_source if errors else None,
        unclaimed=tuple(
            name
            for name, value in errors.items()
            if value is None
            or monte_carlo_source != series_source(series)
            or value > MC_ERROR_CEILING_PERCENT
        ),
        refusal=None if series.n else "no origin is comparable on this loss: nothing to compare",
    )


def _number(value: float | None, spec: str = "+.4g") -> str:
    return "n/a" if value is None else format(value, spec)


def _column_text(
    label: str, column_statistic: float | None, p: float | None, refusal: str | None, symbol: str
) -> str:
    if refusal is not None:
        return f"{label} REFUSED ({refusal})"
    return f"{label} {symbol}={_number(column_statistic)} p={_number(p, '.4f')}"


def _error_parts(comparison: Comparison, names: Sequence[str]) -> list[str]:
    """The measured Monte Carlo error of each simulated contestant among ``names`` (R102).

    Empty if none of them is simulated. ``no claim`` marks one that is withheld (above the floor,
    measured elsewhere or not measured), **whether it is in a set or out of it**: an exclusion is
    as much a significance claim as an inclusion.
    """
    parts = []
    for name in names:
        if name not in comparison.monte_carlo_error:
            continue
        value = comparison.monte_carlo_error[name]
        shown = "not measured" if value is None else f"{value:.3f}%"
        parts.append(
            f"{name} {shown}, no claim" if name in comparison.unclaimed else f"{name} {shown}"
        )
    return parts


def _error_text(comparison: Comparison, names: Sequence[str]) -> str:
    parts = _error_parts(comparison, names)
    if not parts:
        return ""
    return f"  [MC error ({comparison.monte_carlo_source}): {'; '.join(parts)}]"


def _pair_line(comparison: Comparison, pair: PairResult) -> str:
    dm: DMResult = pair.dm
    text = (
        f"    {pair.left} vs {pair.right}: d_bar={_number(dm.mean_difference)} | "
        + _column_text(
            "headline", dm.headline.statistic, dm.headline.p_value, dm.headline.refusal, "t"
        )
        + " | "
        + _column_text(
            "bartlett", dm.bartlett.statistic, dm.bartlett.p_value, dm.bartlett.refusal, "z"
        )
    )
    if dm.disagree:
        text += "  [columns disagree at 5%: both shown, neither claimed]"
    if comparison.claims_withheld:
        text += f"  [n_eff={comparison.n_eff:.1f}: no significance claimed]"
    text += _error_text(comparison, (pair.left, pair.right))
    verdict = comparison.pair_verdict(pair)
    if verdict is not None:
        text += f"  => {verdict}"
    return text


def describe(comparison: Comparison, *, trials: int | None = None) -> list[str]:
    """The console lines for one comparison. Every multiple-comparison figure carries ``n_eff``,
    and every line naming a simulated contestant carries its measured Monte Carlo error.

    ``trials`` is the ledger's line count when the sink is one that has a count, printed beside the
    ranking it supports: a leaderboard that reports a winner without saying how many candidates
    were considered is the thing this project refuses.
    """
    c = comparison
    tag = f"n_eff={c.n_eff:.1f}"
    # MCS and SPA are cohort-wide: every contestant's membership or p-value depends on all of them.
    cohort_wide = _error_text(c, c.contestants)
    counted = "" if trials is None else f"; {trials} trial line(s) on the ledger"
    lines = [
        f"{c.symbol} h{c.horizon} {c.loss} [{c.cohort}]: n={c.origins} stride={c.stride} "
        f"q={c.q} bartlett_lag={c.bartlett_lag} {tag}{counted}"
    ]
    if c.refusal is not None:
        lines.append(f"  REFUSED: {c.refusal}")
    if c.block is not None:
        lines.append(
            f"  block length {c.block.length} = max(overlap floor {c.block.overlap_floor}, "
            f"ceil(Politis-White on loss levels {c.block.politis_white:.2f})); "
            f"reps={c.reps} seed={c.seed} {c.bootstrap} bootstrap"
        )
    for size in sorted({*c.mcs.members, *c.mcs.refusals}):
        label = f"MCS {size:.0%}"
        if size in c.mcs.refusals:
            lines.append(f"  {label}: REFUSED [{tag}] -- {c.mcs.refusals[size]}{cohort_wide}")
        else:
            lines.append(f"  {label}: {', '.join(c.mcs.members[size])} [{tag}]{cohort_wide}")
    if c.mcs.pvalues is not None:
        ordered = sorted(c.mcs.pvalues.items(), key=lambda item: -item[1])
        lines.append(
            "  MCS p-values: "
            + ", ".join(f"{name} {p:.3f}" for name, p in ordered)
            + f" [{tag}]{cohort_wide}"
        )
    for spa in c.spa:
        if spa.refusal is not None:
            lines.append(
                f"  SPA (benchmark {spa.benchmark}): REFUSED [{tag}] -- {spa.refusal}{cohort_wide}"
            )
        else:
            line = (
                f"  SPA (benchmark {spa.benchmark}): p lower={_number(spa.lower, '.4f')} "
                f"consistent={_number(spa.consistent, '.4f')} "
                f"upper={_number(spa.upper, '.4f')} [{tag}]"
            )
            if c.claims_withheld:
                line += f"  [n_eff={c.n_eff:.1f}: no significance claimed]"
            line += cohort_wide
            verdict = c.spa_verdict(spa)
            lines.append(line + (f"  => {verdict}" if verdict else ""))
    lines.append(
        f"  DM: {c.dm_tests} pairwise test(s), no selection made on their p-values; "
        "d = left - right, so a negative statistic favours left"
    )
    lines.extend(_pair_line(c, pair) for pair in c.pairs)
    if c.monte_carlo_error:
        parts = _error_parts(c, tuple(c.monte_carlo_error))
        lines.append(
            f"  Monte Carlo error (R102), measured on the {c.monte_carlo_source}, "
            f"no claim above {MC_ERROR_CEILING_PERCENT:g}%, on a figure measured on another "
            f"series, or where none was measured: " + "; ".join(parts)
        )
    return lines


__all__ = [
    "BENCHMARKS",
    "MC_ERROR_CEILING_PERCENT",
    "SIZES",
    "McsResult",
    "analyse",
    "describe",
    "series_source",
]
