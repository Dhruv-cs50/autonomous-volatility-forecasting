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
  significance is claimed for one above ``MC_ERROR_CEILING_PERCENT``.
"""

from __future__ import annotations

from collections.abc import Mapping

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
# 3.273% at S = 100,000.
MC_ERROR_CEILING_PERCENT = 1.5


def analyse(
    series: LossSeries,
    *,
    monte_carlo_error: Mapping[str, float | None] | None = None,
    benchmarks: tuple[str, ...] = BENCHMARKS,
    reps: int = REPS,
    seed: int = SEED,
) -> Comparison:
    """Every pairwise DM, the MCS at both sizes and SPA against each benchmark, for one series.

    ``monte_carlo_error`` maps each simulated contestant to its measured error in percent, or to
    ``None`` where none was measured. The caller supplies it because the figures belong to the
    model layer and this module imports no model.
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
        unclaimed=tuple(
            name
            for name, value in errors.items()
            if value is not None and value > MC_ERROR_CEILING_PERCENT
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
    flagged = [name for name in (pair.left, pair.right) if name in comparison.unclaimed]
    if flagged:
        errors = ", ".join(f"{name} {comparison.monte_carlo_error[name]:.3f}%" for name in flagged)
        text += f"  [no claim: Monte Carlo error {errors}]"
    return text


def describe(comparison: Comparison) -> list[str]:
    """The console lines for one comparison. Every multiple-comparison figure carries ``n_eff``."""
    c = comparison
    tag = f"n_eff={c.n_eff:.1f}"
    lines = [
        f"{c.symbol} h{c.horizon} {c.loss} [{c.cohort}]: n={c.origins} stride={c.stride} "
        f"q={c.q} bartlett_lag={c.bartlett_lag} {tag}"
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
            lines.append(f"  {label}: REFUSED [{tag}] -- {c.mcs.refusals[size]}")
        else:
            members = ", ".join(
                name + (" (no claim)" if name in c.unclaimed else "")
                for name in c.mcs.members[size]
            )
            lines.append(f"  {label}: {members} [{tag}]")
    if c.mcs.pvalues is not None:
        ordered = sorted(c.mcs.pvalues.items(), key=lambda item: -item[1])
        lines.append("  MCS p-values: " + ", ".join(f"{name} {p:.3f}" for name, p in ordered))
    for spa in c.spa:
        if spa.refusal is not None:
            lines.append(f"  SPA (benchmark {spa.benchmark}): REFUSED [{tag}] -- {spa.refusal}")
        else:
            lines.append(
                f"  SPA (benchmark {spa.benchmark}): p lower={_number(spa.lower, '.4f')} "
                f"consistent={_number(spa.consistent, '.4f')} "
                f"upper={_number(spa.upper, '.4f')} [{tag}]"
            )
    lines.append(
        f"  DM: {c.dm_tests} pairwise test(s), no selection made on their p-values; "
        "d = left - right, so a negative statistic favours left"
    )
    lines.extend(_pair_line(c, pair) for pair in c.pairs)
    if c.monte_carlo_error:
        parts = []
        for name, value in c.monte_carlo_error.items():
            if value is None:
                parts.append(f"{name} not measured")
            elif name in c.unclaimed:
                parts.append(f"{name} {value:.3f}% (above {MC_ERROR_CEILING_PERCENT:g}%: no claim)")
            else:
                parts.append(f"{name} {value:.3f}%")
        lines.append("  Monte Carlo error (R102): " + ", ".join(parts))
    return lines


__all__ = ["BENCHMARKS", "MC_ERROR_CEILING_PERCENT", "SIZES", "McsResult", "analyse", "describe"]
