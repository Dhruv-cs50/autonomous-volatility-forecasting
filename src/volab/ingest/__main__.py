"""``python -m volab.ingest`` -- fetch, build-raw, curate, backfill.

Four verbs, deliberately separable. ``fetch`` touches the network and the archive and nothing
else; ``build-raw`` replays the archive into raw Parquet with no network at all; ``curate`` types
raw into the panel. ``backfill`` runs the three in order.

**There is no unlock flag and no way to ask for an ``end`` past ``dev_end``.** Ingestion writes
past the boundary -- it must, or the holdout could never be scored later -- but every read, and
therefore every curated row, stops at ``settings.splits.dev_end``. Phase 6 unlocks once, with
both keys and an audit line, to derive the holdout's curated rows from raw.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from volab.config import Settings, load_settings
from volab.errors import DatasetNotFound, VolabError
from volab.ingest import SOURCES, build_raw, fetch_source
from volab.ingest.curate import curate_bars, curate_fred, curate_vix

VIX_SERIES: tuple[str, ...] = ("VIX", "VIX9D", "VIX1D", "VVIX")
FRED_SERIES: tuple[str, ...] = ("BAA10Y", "DGS10", "T10Y2Y")


def _plan(settings: Settings) -> list[tuple[str, tuple[str, ...]]]:
    """Which symbols each source covers. Bars and dividends follow the configured universe."""
    return [
        ("nasdaq_bars", settings.universe),
        ("nasdaq_dividends", settings.universe),
        ("cboe_vix", VIX_SERIES),
        ("fred", FRED_SERIES),
    ]


def _selected(settings: Settings, only: str | None) -> list[tuple[str, tuple[str, ...]]]:
    plan = _plan(settings)
    if only is None:
        return plan
    chosen = [row for row in plan if row[0] == only]
    if not chosen:
        raise SystemExit(f"unknown source {only!r}; known sources are {list(SOURCES)}")
    return chosen


def _fetch(settings: Settings, only: str | None) -> int:
    failures = 0
    for source, symbols in _selected(settings, only):
        for symbol in symbols:
            try:
                path = fetch_source(source, symbol, settings=settings)
            except VolabError as exc:
                # A refused payload is reported and the run continues: one source being down
                # must not stop the others, and nothing unparseable reaches the archive.
                failures += 1
                print(f"  {source}/{symbol}: REFUSED {type(exc).__name__}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - a vendor outage is not a crash here
                failures += 1
                print(f"  {source}/{symbol}: FAILED {type(exc).__name__}: {exc}")
                continue
            print(f"  {source}/{symbol}: {'archived ' + path.name if path else 'nothing new'}")
    return failures


def _build_raw(settings: Settings, only: str | None) -> int:
    total = 0
    for source, symbols in _selected(settings, only):
        written = build_raw(source, symbols=list(symbols), settings=settings)
        total += written
        print(f"  {source}: {written} partitions")
    return total


def _curate(settings: Settings, only: str | None) -> int:
    end = settings.splits.dev_end
    total = 0
    jobs: list[tuple[str, str, object]] = []
    for source, symbols in _selected(settings, only):
        if source == "nasdaq_bars":
            jobs += [("bars", symbol, curate_bars) for symbol in symbols]
        elif source == "cboe_vix":
            jobs += [("vix", symbol, curate_vix) for symbol in symbols]
        elif source == "fred":
            jobs += [("fred", symbol, curate_fred) for symbol in symbols]
    for kind, symbol, function in jobs:
        try:
            written = function(symbol, end=end, settings=settings)  # type: ignore[operator]
        except DatasetNotFound:
            print(f"  {kind}/{symbol}: no raw data yet, skipped")
            continue
        total += written
        print(f"  {kind}/{symbol}: {written} partitions")
    return total


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m volab.ingest", description=__doc__)
    parser.add_argument(
        "command", choices=("fetch", "build-raw", "curate", "backfill"), help="what to run"
    )
    parser.add_argument("--source", default=None, help=f"limit to one source: {', '.join(SOURCES)}")
    arguments = parser.parse_args(argv)
    settings = load_settings()
    print(f"storage root {settings.storage_root}")
    print(f"reads stop at dev_end {settings.splits.dev_end}")

    if arguments.command == "fetch":
        return 1 if _fetch(settings, arguments.source) else 0
    if arguments.command == "build-raw":
        _build_raw(settings, arguments.source)
        return 0
    if arguments.command == "curate":
        _curate(settings, arguments.source)
        return 0

    failures = _fetch(settings, arguments.source)
    _build_raw(settings, arguments.source)
    _curate(settings, arguments.source)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
