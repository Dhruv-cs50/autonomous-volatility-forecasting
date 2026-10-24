"""M1 -- is a post-load slice on ``as_of`` what the store would have handed over at ``t``?

Reproduces the measurement recorded as **M1** in ``docs/superpowers/phase2-decisions.md``, which
is what ruling **R33** rests on: ``volab.panel.truncate`` is a post-load slice rather than a
re-read per cutoff, and that is sound only while no curated key carries several vintages. The
store keeps the latest vintage at or before ``end``; a slice would drop a revised row instead of
falling back to its earlier one.

Two things are printed, in the order the log quotes them:

1. per dataset, the row count and the duplicate counts on ``(symbol, ts)`` and ``(symbol,
   as_of)``. A ``(symbol, ts)`` duplicate is a second vintage of one observation, which is the
   case that would break R33. A ``(symbol, as_of)`` duplicate is two *different* observations
   landing on one knowable session -- FRED's publication calendar against XNYS -- which
   ``collapse_superseded_observations`` handles and which is not a vintage.
2. the slice against ``load_panel(end=cutoff)`` at three cutoffs in each of three datasets,
   compared with the project's own byte-exact comparator.

The standing witness for the same property is
``test_the_truncated_panel_is_what_the_store_would_have_handed_over``; this script is the
*number* behind the log's quoted output, which a test does not print.

    uv run --locked python tools/measure_vintages.py
"""

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence

import pandas as pd
from _harness import RECORDED_CUTOFFS, date_argument, heading, note

from volab.config import load_settings
from volab.features.build import CLOSE_COLUMN, VALUE_COLUMN
from volab.identity import first_difference
from volab.ingest.curate import BARS_DATASET, FRED_DATASET, VIX_DATASET
from volab.panel import FRED_SERIES_NAMES, KEYS, VIX_SERIES, truncate
from volab.store import load_panel


def _duplicates(frame: pd.DataFrame, keys: Sequence[str]) -> int:
    """Rows sharing ``keys`` with at least one other row, both members counted.

    ``keep=False`` so a colliding pair contributes 2: that is how the log's ``14`` reads, and it
    is the honest count of rows involved rather than of collisions.
    """
    return int(frame.duplicated(subset=list(keys), keep=False).sum())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cutoff",
        dest="cutoffs",
        action="append",
        type=date_argument,
        help="repeatable; defaults to the three cutoffs the decisions log quotes",
    )
    parser.add_argument(
        "--symbol",
        default="SPY",
        help="the subject symbol the bars slice is compared on (default SPY)",
    )
    arguments = parser.parse_args(argv)
    cutoffs: list[dt.date] = list(arguments.cutoffs or RECORDED_CUTOFFS)

    settings = load_settings()
    note(f"storage root {settings.storage_root}")
    note(f"reads stop at dev_end {settings.splits.dev_end}")

    # M1a asks whether a *vintage* exists anywhere, so it reads the whole universe. M1b compares
    # the slice the feature engine actually takes, and ``volab.panel.load_bars`` reads one
    # symbol, so the bars comparison is per subject. Reading all three there would compare a
    # frame no caller ever asks for.
    counted: tuple[tuple[str, tuple[str, ...], str], ...] = (
        (BARS_DATASET, tuple(settings.universe), CLOSE_COLUMN),
        (VIX_DATASET, VIX_SERIES, CLOSE_COLUMN),
        (FRED_DATASET, FRED_SERIES_NAMES, VALUE_COLUMN),
    )
    reads: tuple[tuple[str, tuple[str, ...], str], ...] = (
        (BARS_DATASET, (arguments.symbol,), CLOSE_COLUMN),
        (VIX_DATASET, VIX_SERIES, CLOSE_COLUMN),
        (FRED_DATASET, FRED_SERIES_NAMES, VALUE_COLUMN),
    )

    heading("M1a: VINTAGES PER CURATED DATASET")
    for dataset, symbols, column in counted:
        frame = load_panel(
            dataset,
            end=settings.splits.dev_end,
            symbols=list(symbols),
            columns=(*KEYS, column),
            settings=settings,
        )
        print(
            f"{dataset}: rows={len(frame)} "
            f"dup(symbol,ts)={_duplicates(frame, ('symbol', 'ts'))} "
            f"dup(symbol,as_of)={_duplicates(frame, ('symbol', 'as_of'))}"
        )

    heading("M1b: THE SLICE AGAINST WHAT THE STORE WOULD HAND OVER")
    full: dict[str, pd.DataFrame] = {
        dataset: load_panel(
            dataset,
            end=settings.splits.dev_end,
            symbols=list(symbols),
            columns=(*KEYS, column),
            settings=settings,
        )
        for dataset, symbols, column in reads
    }
    identical = 0
    attempted = 0
    for dataset, symbols, column in reads:
        for cutoff in cutoffs:
            attempted += 1
            sliced = truncate(full[dataset], cutoff)
            stored = load_panel(
                dataset,
                end=cutoff,
                symbols=list(symbols),
                columns=(*KEYS, column),
                settings=settings,
            )
            difference = first_difference(sliced, stored)
            if difference is None:
                identical += 1
            print(
                f"{dataset} {cutoff}: slice={sliced.shape} store={stored.shape} "
                f"identical={difference is None}"
                + ("" if difference is None else f" -- {difference}")
            )

    print(f"{identical} of {attempted} byte-identical")
    # Non-zero on a disagreement: this is the premise R33 rests on, and a probe that reports a
    # broken premise with a success code is the shape of check this project refuses.
    return 0 if identical == attempted else 1


if __name__ == "__main__":
    raise SystemExit(main())
