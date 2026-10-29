"""Assembling the curated panel ``build_features`` reads, and truncating it at a cutoff.

Two jobs, and they belong together because the second is only meaningful against the first.

**Assembly.** ``build_features`` wants one long-form frame: a subject symbol's curated bars
concatenated with every exogenous series, keyed by ``symbol`` and joined on ``as_of``. Three
things about that frame are not obvious and each cost a run to discover, so the assembly lives
here once rather than in each caller:

* The exogenous reads must name their series explicitly. ``load_panel`` defaults ``symbols`` to
  the configured *universe*, so a read of ``vix_family`` without them raises ``DatasetNotFound``.
* FRED's publication calendar is not XNYS, so two observations can become knowable on one
  session -- measured, 7 times across 3 dates over the dev window. ``build_features`` refuses a
  panel with two rows for a session, deliberately, so the choice between them is the caller's
  and is made by ``collapse_superseded_observations``.
* A real loaded panel's ``as_of`` is ``datetime64[us]``, not the ``date`` objects a synthetic
  test panel carries. ``panel["as_of"] <= a_date`` raises ``TypeError: Invalid comparison between
  dtype=datetime64[us] and date``, so every date comparison here goes through ``as_of_dates``.

**Truncation.** ``truncate(panel, cutoff)`` is the panel that *physically stops* at ``cutoff``,
and it is the other half of Phase 2's primary point-in-time test: build the feature table at
``cutoff`` from the full panel, build it again from this, and compare byte-exactly. A trailing
feature cannot tell the two apart. A forward peek can, and unlike a perturbation there is no
image for a thresholded predicate to be constant across -- the future row is simply gone.

That makes ``truncate`` load-bearing, so it carries a witness of its own rather than an argument.
The thing that could go wrong is subtle: a post-load slice equals what the store would have
handed over at ``cutoff`` only if no key carries several vintages, because the store keeps the
latest vintage at or before ``end`` while a slice would drop a revised row instead of falling
back to its earlier one. Measured on the real curated store: no multi-vintage key exists
(``dup(symbol, ts)`` is 0 in all three datasets), and the slice is byte-identical to
``load_panel(end=cutoff)`` at three cutoffs in each. Both are pinned by
``test_the_truncated_panel_is_what_the_store_would_have_handed_over``, in
``tests/test_truncation_equivalence.py``, so the day curated starts carrying revisions the claim
fails instead of quietly becoming false; ``tools/measure_vintages.py`` prints the measurement
itself. An earlier version of this docstring cited a ``tests/test_panel.py`` that has never
existed -- the same defect the Phase 1 spec had, where five named tests were never written.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence

import pandas as pd

from volab.config import Settings
from volab.errors import DatasetNotFound
from volab.features.build import (
    CLOSE_COLUMN,
    DISTRIBUTION_COLUMN,
    FRED_SERIES,
    IV_SERIES,
    VALUE_COLUMN,
    VVIX_SERIES,
)
from volab.ingest.curate import BARS_DATASET, FRED_DATASET, VIX_DATASET
from volab.paths import MANDATORY_COLUMNS
from volab.store import load_panel

# The mandatory key columns every curated dataset carries.
KEYS: tuple[str, ...] = tuple(MANDATORY_COLUMNS)

# The exogenous series, by the dataset that holds them and the column their payload is in.
VIX_SERIES: tuple[str, ...] = (*IV_SERIES.values(), VVIX_SERIES)
FRED_SERIES_NAMES: tuple[str, ...] = tuple(FRED_SERIES.values())

EXOGENOUS_READS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (VIX_DATASET, VIX_SERIES, CLOSE_COLUMN),
    (FRED_DATASET, FRED_SERIES_NAMES, VALUE_COLUMN),
)

Report = Callable[[str], None] | None


def _say(report: Report, message: str) -> None:
    if report is not None:
        report(message)


def as_of_dates(panel: pd.DataFrame) -> pd.Series:
    """``panel``'s ``as_of`` as ``datetime.date`` objects, whatever dtype it arrived in.

    A panel from ``load_panel`` carries ``datetime64[us]`` and a synthetic test panel carries
    ``date`` objects. Comparing the first against a ``date`` raises rather than returning a mask,
    so nothing in this project compares ``as_of`` without coming through here.
    """
    return pd.to_datetime(panel["as_of"]).dt.date


def collapse_superseded_observations(
    frame: pd.DataFrame, dataset: str, *, report: Report = None
) -> pd.DataFrame:
    """Collapse a series to one observation per knowable session, keeping the latest.

    Two observations can share an ``as_of`` because FRED's publication calendar and XNYS are
    different calendars: when the NYSE closes on a day FRED still publishes, the preceding
    business day and that day both lag onto the same next session. Measured over the full curated
    dev window this happens **7 times across 3 dates** -- Good Friday 2021-04-02 and 2023-04-07,
    and 2025-01-09, the national day of mourning.

    Keeping the **latest** is the point-in-time reading: standing on Friday, the newest value
    known is Thursday's. The earlier one is superseded information, not lost information.

    Done here rather than in ``build_features``, which still refuses a panel carrying two rows
    for one session. That guard is worth keeping strict: curated data legitimately holds both
    observations, and choosing between them is the caller's decision, not something the engine
    should quietly absorb.
    """
    counts = frame.groupby(["symbol", "as_of"], sort=False)["ts"].transform("size")
    if not bool((counts > 1).any()):
        return frame
    collapsed = (
        frame.sort_values(["symbol", "as_of", "ts"], kind="stable")
        .drop_duplicates(["symbol", "as_of"], keep="last")
        .reset_index(drop=True)
    )
    _say(
        report,
        f"  {dataset}: collapsed {len(frame) - len(collapsed)} superseded observation(s) that "
        "shared a knowable session, keeping the latest of each",
    )
    return collapsed


def load_exogenous(
    settings: Settings,
    dataset: str,
    series: Sequence[str],
    column: str,
    *,
    end: dt.date,
    report: Report = None,
) -> pd.DataFrame | None:
    """One exogenous dataset read up to ``end``, or ``None`` if the store holds none of it.

    A series with no rows is reported and skipped rather than aborting, because the feature table
    is defined for an absent exogenous series: those columns come out null. Only absence is
    tolerated; a dataset that exists and is malformed still raises, because that is a fault and
    not a gap.
    """
    frames: list[pd.DataFrame] = []
    for name in series:
        try:
            frames.append(
                load_panel(
                    dataset,
                    end=end,
                    symbols=[name],
                    columns=(*KEYS, column),
                    settings=settings,
                )
            )
        except DatasetNotFound:
            continue
    if not frames:
        _say(
            report,
            f"  {dataset}: skipped, none of {list(series)} is in the store; the {column!r} "
            "columns will be null",
        )
        return None
    absent = sorted(set(series) - {str(frame["symbol"].iloc[0]) for frame in frames})
    if absent:
        _say(report, f"  {dataset}: {absent} not in the store, those columns will be null")
    return collapse_superseded_observations(
        pd.concat(frames, ignore_index=True), dataset, report=report
    )


def load_bars(settings: Settings, symbol: str, *, end: dt.date) -> pd.DataFrame:
    """One symbol's curated bars up to ``end``: the close and the distribution flag."""
    return load_panel(
        BARS_DATASET,
        end=end,
        symbols=[symbol],
        columns=(*KEYS, CLOSE_COLUMN, DISTRIBUTION_COLUMN),
        settings=settings,
    )


def assemble(bars: pd.DataFrame, exogenous: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """The long-form panel ``build_features`` expects: one symbol's bars beside the series."""
    return pd.concat([bars, *exogenous], ignore_index=True)


def load_curated_panel(
    settings: Settings, symbol: str, *, end: dt.date, report: Report = None
) -> pd.DataFrame:
    """``symbol``'s assembled panel, read to ``end``, ready for ``build_features``.

    ``end`` is required, as it is on ``load_panel``: there is no "all data" shape anywhere on
    this project's read path, because the one question the layer exists to ask is how far forward
    the caller may see.
    """
    exogenous = [
        frame
        for dataset, series, column in EXOGENOUS_READS
        if (frame := load_exogenous(settings, dataset, series, column, end=end, report=report))
        is not None
    ]
    return assemble(load_bars(settings, symbol, end=end), exogenous)


def truncate(panel: pd.DataFrame, cutoff: dt.date) -> pd.DataFrame:
    """The panel as it would have been had nothing after ``cutoff`` happened yet.

    A copy, not a view: ``build_features`` is handed this and a view would make any accidental
    write reach back into the caller's panel, which is the one frame a point-in-time test must
    be able to trust.
    """
    return panel[as_of_dates(panel) <= cutoff].reset_index(drop=True).copy()
