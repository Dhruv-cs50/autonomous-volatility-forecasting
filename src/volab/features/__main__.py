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
the environment: ``VOLAB_SPLITS_DEV_END`` may only move it *earlier*, and
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
from volab.features.build import (
    CLOSE_COLUMN,
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
from volab.ingest.curate import FRED_DATASET, VIX_DATASET
from volab.panel import (
    collapse_superseded_observations,
    load_bars,
    load_exogenous,
)
from volab.paths import MANDATORY_COLUMNS
from volab.sessions import get_calendar, sessions_between
from volab.store import write_features

# The persisted feature table's name in the store: ``features/har_daily/<symbol>/date=<as_of>``.
FEATURE_SET = "har_daily"

_KEYS: tuple[str, ...] = tuple(MANDATORY_COLUMNS)
_VIX_SERIES: tuple[str, ...] = (*IV_SERIES.values(), VVIX_SERIES)
_FRED_SERIES: tuple[str, ...] = tuple(FRED_SERIES.values())


def _exogenous(
    settings: Settings, dataset: str, series: Sequence[str], column: str
) -> pd.DataFrame | None:
    """One exogenous dataset, read up to ``dev_end``, or ``None`` if the store holds none of it.

    The read itself lives in ``volab.panel``, which the walk-forward and the truncation-
    equivalence tests also go through. Assembling the panel twice is how the two paths drift,
    and the second copy here cost two failed runs before it was removed -- a ``DatasetNotFound``
    from omitting ``symbols``, and a ``SchemaError`` from omitting the superseded-observation
    collapse. This wrapper is the CLI's half: it pins the cutoff to ``dev_end``, which is the
    one thing this entry point may never be asked to move, and prints what the library reports.
    """
    return load_exogenous(
        settings, dataset, series, column, end=settings.splits.dev_end, report=print
    )


def _latest_observation_per_session(frame: pd.DataFrame, dataset: str) -> pd.DataFrame:
    """Collapse a series to one observation per knowable session. See ``volab.panel``."""
    return collapse_superseded_observations(frame, dataset, report=print)


def _bars(settings: Settings, symbol: str) -> pd.DataFrame:
    return load_bars(settings, symbol, end=settings.splits.dev_end)


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
