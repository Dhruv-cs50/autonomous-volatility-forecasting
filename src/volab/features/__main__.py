"""``python -m volab.features build`` -- the feature table and the four target datasets.

One verb. It reads the curated panel, derives the feature table and the forward targets, and
writes both back to the store, one partition per ``as_of``. This is the only code in the features
package that touches the store: ``build_features`` is handed a loaded panel and never reads, which
is what lets the point-in-time suite perturb it.

**There is no unlock flag and no way to ask for an ``end`` past ``dev_end``.** Writes may go past
the boundary elsewhere in this project -- ingestion must, or the holdout could never be scored --
but every read here, and therefore every feature and target row, stops at
``settings.splits.dev_end``. Phase 6 unlocks once, with both keys and an audit line, to derive the
holdout's rows. An ``--end`` argument accepting a later date would quietly defeat that.

Nor can the boundary itself be moved *later* -- the direction that would open the seal -- by
configuration or environment: ``VOLAB_SPLITS_DEV_END`` may only bring it forward, and
``load_settings`` refuses a value past the literal in ``config/base.yaml``. Editing that literal
is the only way.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence

import pandas as pd

from volab.config import Settings, load_settings
from volab.errors import DatasetNotFound
from volab.features.build import (
    CLOSE_COLUMN,
    DISTRIBUTION_COLUMN,
    FRED_SERIES,
    IV_SERIES,
    VALUE_COLUMN,
    VVIX_SERIES,
    build_features,
    log_returns,
)
from volab.features.targets import (
    HORIZONS,
    RETURN_COLUMN,
    build_targets,
    target_column,
    targets_dataset,
    write_targets,
)
from volab.ingest.curate import BARS_DATASET, FRED_DATASET, VIX_DATASET
from volab.paths import MANDATORY_COLUMNS
from volab.sessions import get_calendar, sessions_between
from volab.store import load_panel, write_features

# The persisted feature table's name in the store: ``features/har_daily/<symbol>/date=<as_of>``.
FEATURE_SET = "har_daily"

_KEYS: tuple[str, ...] = tuple(MANDATORY_COLUMNS)
_VIX_SERIES: tuple[str, ...] = (*IV_SERIES.values(), VVIX_SERIES)
_FRED_SERIES: tuple[str, ...] = tuple(FRED_SERIES.values())


def _exogenous(
    settings: Settings, dataset: str, series: Sequence[str], column: str
) -> pd.DataFrame | None:
    """One exogenous dataset, read up to ``dev_end``, or ``None`` if the store holds none of it.

    Read **one series at a time**, which is no longer forced and is parked rather than load-
    bearing. It was forced: the curated VIX family is not one schema -- VIX carries
    open/high/low beside its close and VVIX only a close -- and a single ``load_panel`` over
    both used to fail inside DuckDB with a schema mismatch. US-6.8 fixed that. Measured since:
    one read over the real ``vix_family`` returns the same 8,079 rows as this loop, row for
    row, and a read naming a series the store holds nothing for returns the others rather than
    raising, so the per-series ``DatasetNotFound`` tolerance is not buying anything either.

    What this function is still for is everything around the read: projecting each series to
    the same columns, reporting which ones the store has nothing for, and the
    latest-observation-per-session collapse below. Those are the reasons it exists, and
    rewriting the read inside it would change working code to no measured end. A single read
    is the simpler shape if a reason to touch this ever appears.

    A series with no rows is reported and skipped rather than aborting the build, because the
    feature table is defined for an absent exogenous series: those columns come out null. Both
    families are ingested today -- ``fred_daily`` has 7,111 curated partitions -- so this is
    tolerance for a state the repository is not in, kept because an exogenous source can be
    added before it is backfilled. Only absence is tolerated; a dataset that exists and is
    malformed still raises, because that is a fault and not a gap.
    """
    frames: list[pd.DataFrame] = []
    for name in series:
        try:
            frames.append(
                load_panel(
                    dataset,
                    end=settings.splits.dev_end,
                    symbols=[name],
                    columns=(*_KEYS, column),
                    settings=settings,
                )
            )
        except DatasetNotFound:
            continue
    if not frames:
        print(
            f"  {dataset}: skipped, none of {list(series)} is in the store; the {column!r} "
            "columns will be null"
        )
        return None
    absent = sorted(set(series) - {str(frame["symbol"].iloc[0]) for frame in frames})
    if absent:
        print(f"  {dataset}: {absent} not in the store, those columns will be null")
    return _latest_observation_per_session(pd.concat(frames, ignore_index=True), dataset)


def _latest_observation_per_session(frame: pd.DataFrame, dataset: str) -> pd.DataFrame:
    """Collapse a series to one observation per knowable session, keeping the latest.

    Two observations can share an ``as_of`` because FRED's publication calendar and XNYS are
    different calendars. When the NYSE closes on a day FRED still publishes, the preceding
    business day and that day both lag onto the same next session.

    Measured over the full curated dev window, this happens **7 times across 3 dates**, not once:
    Good Friday 2021-04-02 and 2023-04-07, and 2025-01-09, the national day of mourning. Each
    collides for DGS10 and T10Y2Y; only the mourning day also collides for BAA10Y, because BAA10Y
    is one of the series FRED leaves empty on Good Friday. So the recurring case is Good Friday
    and the freak case is the one-off -- the reverse of how an earlier version of this comment
    read, which cited only the mourning day and made the collapse look like near-dead code.

    Keeping the **latest** observation is the point-in-time reading: standing on Friday, the
    newest value known is Thursday's. The earlier one is not lost information so much as
    superseded information.

    This is done here rather than in ``build_features``, which still refuses a panel carrying two
    rows for one session. That guard is worth keeping strict: curated data legitimately holds
    both observations, and choosing between them is the caller's decision, not something the
    engine should quietly absorb.
    """
    counts = frame.groupby(["symbol", "as_of"], sort=False)["ts"].transform("size")
    if not bool((counts > 1).any()):
        return frame
    collapsed = (
        frame.sort_values(["symbol", "as_of", "ts"], kind="stable")
        .drop_duplicates(["symbol", "as_of"], keep="last")
        .reset_index(drop=True)
    )
    print(
        f"  {dataset}: collapsed {len(frame) - len(collapsed)} superseded observation(s) that "
        "shared a knowable session, keeping the latest of each"
    )
    return collapsed


def _bars(settings: Settings, symbol: str) -> pd.DataFrame:
    return load_panel(
        BARS_DATASET,
        end=settings.splits.dev_end,
        symbols=[symbol],
        columns=(*_KEYS, CLOSE_COLUMN, DISTRIBUTION_COLUMN),
        settings=settings,
    )


def _target_panel(bars: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """The one symbol's bars with a ``log_return`` column, which is what ``build_targets`` reads.

    Curated bars carry no return, and ``log_returns`` is the one place a return is derived.
    It is handed the close **reindexed onto the calendar's sessions**: its ``shift(1)`` looks one
    row back, so only on a session index is that row the previous session, and a session with no
    bar then poisons the two returns spanning it instead of being bridged by a return over two.
    ``build_features`` has already refused any bar dated off the calendar by the time this runs.
    """
    days = list(pd.to_datetime(bars["as_of"]).dt.date)
    sessions = sessions_between(get_calendar(settings), min(days), max(days))
    close = pd.Series(bars[CLOSE_COLUMN].to_numpy(dtype="float64"), index=days).reindex(sessions)
    returns = log_returns(close)
    panel = bars[list(_KEYS)].copy()
    panel[RETURN_COLUMN] = returns.loc[pd.Index(days)].to_numpy()
    return panel


def _write_features(table: pd.DataFrame, symbol: str, settings: Settings) -> int:
    """One partition per session. ``as_of`` is read through pandas so a ``Timestamp`` key can
    never name the partition ``date=2023-06-16T00:00:00``."""
    days = list(pd.to_datetime(table["as_of"]).dt.date)
    for position, day in enumerate(days):
        write_features(
            table.iloc[[position]],
            feature_set=FEATURE_SET,
            symbol=symbol,
            as_of=day,
            settings=settings,
        )
    return len(days)


def _build(settings: Settings) -> None:
    started = time.perf_counter()
    dev_end = settings.splits.dev_end
    calendar = get_calendar(settings)

    # The exogenous datasets are shared by every symbol, so they are read once.
    vix = _exogenous(settings, VIX_DATASET, _VIX_SERIES, CLOSE_COLUMN)
    fred = _exogenous(settings, FRED_DATASET, _FRED_SERIES, VALUE_COLUMN)
    exogenous = [frame for frame in (vix, fred) if frame is not None]

    for symbol in settings.universe:
        bars = _bars(settings, symbol)
        # The panel ``build_features`` expects: this symbol's bars beside the exogenous series,
        # concatenated long-form and keyed by ``symbol``.
        panel = pd.concat([bars, *exogenous], ignore_index=True)
        table = build_features(panel, through=dev_end, calendar=calendar)
        written = _write_features(table, symbol, settings)
        print(f"  {FEATURE_SET}/{symbol}: {written} partitions")

        returns = _target_panel(bars, settings)
        for horizon in HORIZONS:
            targets = build_targets(returns, horizon=horizon, calendar=calendar)
            name = targets_dataset(horizon)
            if targets.empty:
                # ``write_targets`` refuses an empty frame. A horizon longer than the history is
                # a fact about the data, not a fault, so it is reported rather than raised.
                print(f"  {name}/{symbol}: no session has a complete {target_column(horizon)}")
                continue
            paths = write_targets(targets, horizon=horizon, symbol=symbol, settings=settings)
            print(f"  {name}/{symbol}: {len(paths)} partitions")
    print(f"built in {time.perf_counter() - started:.1f}s")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m volab.features", description=__doc__)
    parser.add_argument("command", choices=("build",), help="what to run")
    parser.parse_args(argv)
    settings = load_settings()
    print(f"storage root {settings.storage_root}")
    print(f"reads stop at dev_end {settings.splits.dev_end}")
    _build(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
