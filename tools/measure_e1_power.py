"""M2 -- E1's detection power on real data, derived before any sweep is run.

Reproduces the measurement recorded as **M2** in ``docs/superpowers/phase2-decisions.md``, which
is what ruling **R35** rests on: sampling is kept as the cheap per-commit tier *with its power
stated*, and the dev-window proof is the recorded exhaustive sweep instead.

The mechanism, which is why this is a derivation and not a guess: E1 ORs a band on the **next**
session's return into the semivariance sign mask, so it moves exactly one row -- cutoff ``t``'s
own. Truncating at ``t`` removes ``t``'s forward view and nothing else, so the full-panel and
truncated-panel tables disagree at ``t`` **iff** ``r[t+1]`` lands inside the band. That makes
the detectable surface a set this script can compute from the return distribution alone, before
the sweep has been run. ``tools/sweep_truncation.py`` then checks the sweep finds precisely it,
which is stronger evidence than a matching count.

Returns come from ``volab.features.build.log_returns`` over the session-reindexed close, the
engine's own path, rather than from a ``pct_change`` written here: band-edge membership is
decided at the fifth decimal and a second implementation of "the return" would drift.

The standing witnesses for the same property are
``test_e1_is_detectable_at_exactly_the_cutoffs_where_its_band_is_reached`` and ``blind_cutoffs``,
on the synthetic fixture. This script is the *real-data* number, which no standing test prints.

    uv run --locked python tools/measure_e1_power.py
"""

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence

import pandas as pd
from _harness import heading, note, test_module

import volab.features.build as build_module
from volab.config import load_settings
from volab.panel import as_of_dates, load_curated_panel
from volab.sessions import get_calendar, sessions_between

# The sample size whose power the log quotes, so the probability below is the one it quotes.
QUOTED_SAMPLE = 40


def returns_of(panel: pd.DataFrame, symbol: str, sessions: Sequence[dt.date]) -> pd.Series:
    """``symbol``'s session-indexed log returns, as ``build_features`` computes them."""
    bars = panel[panel["symbol"] == symbol]
    close = pd.Series(
        bars["close"].astype("float64").to_list(), index=list(as_of_dates(bars))
    ).reindex(list(sessions))
    return build_module.log_returns(close)


def detectable_cutoffs(
    returns: pd.Series,
    sessions: Sequence[dt.date],
    cutoffs: Sequence[dt.date],
    band: tuple[float, float],
) -> set[dt.date]:
    """``{t in cutoffs : r[t+1] lands inside the band}`` -- exactly where E1 is visible.

    A null ``r[t+1]`` is **not** detectable and is excluded here rather than tolerated: the peek
    reads null from the full panel too, agrees with the truncated one, and there is no difference
    to find. That is a blind spot of the leak, not of the method (R42).
    """
    positions = {day: index for index, day in enumerate(sessions)}
    found: set[dt.date] = set()
    for cutoff in cutoffs:
        index = positions[cutoff] + 1
        if index >= len(sessions):
            continue
        ahead = returns.iloc[index]
        if not pd.isna(ahead) and band[0] < abs(float(ahead)) < band[1]:
            found.add(cutoff)
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY", help="the subject symbol (default SPY)")
    parser.add_argument(
        "--sample",
        type=int,
        default=QUOTED_SAMPLE,
        help=f"the sample size whose miss probability is reported (default {QUOTED_SAMPLE})",
    )
    arguments = parser.parse_args(argv)

    equivalence = test_module("test_truncation_equivalence")
    band: tuple[float, float] = equivalence.BAND

    settings = load_settings()
    note(f"storage root {settings.storage_root}")
    note(f"reads stop at dev_end {settings.splits.dev_end}")
    calendar = get_calendar(settings)
    panel = load_curated_panel(settings, arguments.symbol, end=settings.splits.dev_end)
    bars = panel[panel["symbol"] == arguments.symbol]
    sessions = sessions_between(calendar, min(as_of_dates(bars)), max(as_of_dates(bars)))
    returns = returns_of(panel, arguments.symbol, sessions)

    # The next session's return, per session, so each band is counted on its own sign.
    ahead = [
        returns.iloc[index + 1] if index + 1 < len(sessions) else float("nan")
        for index in range(len(sessions))
    ]
    positive = [value for value in ahead if not pd.isna(value) and band[0] < value < band[1]]
    negative = [value for value in ahead if not pd.isna(value) and -band[1] < value < -band[0]]

    heading("M2: E1'S DETECTION POWER ON REAL DATA")
    print(f"sessions: {len(sessions)}")
    print(f"sessions whose NEXT return is in ({band[0]:.2f}, {band[1]:.2f}): {len(positive)}")
    print(f"  ... in ({-band[1]:.2f}, {-band[0]:.2f}): {len(negative)}")
    # Spelled out because the log's single "hit rate" line was ambiguous: the figure it printed
    # was the positive band alone, while the sentence beside it described the union.
    union = len(positive) + len(negative)
    print(f"hit rate: {len(positive) / len(sessions):.4f}  (the positive band alone)")
    print(f"union: {union} of {len(sessions)}, rate {union / len(sessions):.4f}")
    rate = union / len(sessions)
    print(f"a {arguments.sample}-cutoff sample expects {arguments.sample * rate:.1f} hit(s)")
    print(
        f"P(a {arguments.sample}-cutoff sample sees none): {(1.0 - rate) ** arguments.sample:.3f}"
    )

    # The prediction the sweep is then checked against. The cutoff set is the sweep's own, so the
    # denominator here is the sweep's denominator and not the session count.
    cutoffs = equivalence.informative_cutoffs(panel, sessions)
    predicted = detectable_cutoffs(returns, sessions, cutoffs, band)
    print(f"informative cutoffs the sweep would visit: {len(cutoffs)}")
    print(f"of those, E1 is detectable at: {len(predicted)}")
    print(f"first three: {sorted(predicted)[:3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
