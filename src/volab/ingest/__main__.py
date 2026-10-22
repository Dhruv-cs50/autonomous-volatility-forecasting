"""``python -m volab.ingest`` -- fetch, build-raw, curate, backfill.

Four verbs, deliberately separable. ``fetch`` touches the network and the archive and nothing
else; ``build-raw`` replays the archive into raw Parquet with no network at all; ``curate`` types
raw into the panel. ``backfill`` runs the three in order.

**There is no unlock flag and no way to ask for an ``end`` past ``dev_end``.** Ingestion writes
past the boundary -- it must, or the holdout could never be scored later -- but every read, and
therefore every curated row, stops at ``settings.splits.dev_end``. Phase 6 unlocks once, with
both keys and an audit line, to derive the holdout's curated rows from raw.

The boundary cannot be moved *later* by configuration or environment either:
``VOLAB_SPLITS_DEV_END`` may only bring it forward, and ``load_settings`` refuses a value past
the literal committed in ``config/base.yaml``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from volab.config import Settings, load_settings
from volab.errors import DatasetNotFound, VolabError
from volab.ingest import SOURCES, build_raw, fetch_source
from volab.ingest.curate import curate_bars, curate_fred, curate_vix

# Which source serves each exogenous series. The fetch plan is ``settings.exogenous`` split by
# this map rather than a second copy of the list: a hand-kept copy was pinned to nothing, so a
# series added to ``config.exogenous`` was silently never fetched. A series with no entry here
# stops the run instead, because there is no source that could serve it.
EXOGENOUS_SOURCE: dict[str, str] = {
    "VIX": "cboe_vix",
    "VIX9D": "cboe_vix",
    "VIX1D": "cboe_vix",
    "VVIX": "cboe_vix",
    "BAA10Y": "fred",
    "DGS10": "fred",
    "T10Y2Y": "fred",
}


_EPILOG = (
    "fetch reports each series as archived, nothing new, REFUSED or FAILED. "
    "REFUSED means the response arrived and was rejected (it would not parse, or failed "
    "validation): nothing was archived, retrying now will refuse again, so look at what the "
    "vendor served. "
    "FAILED means the fetch did not complete (timeout, connection error, non-200 status): "
    "retrying later is reasonable. "
    "Exit status 1 if any fetch was REFUSED or FAILED, because either way the run is "
    "incomplete; 0 otherwise. build-raw and curate exit 0 when they complete, and a series "
    "with no raw data yet is skipped rather than failed."
)


def _plan(settings: Settings) -> list[tuple[str, tuple[str, ...]]]:
    """Which symbols each source covers: the configured universe, and the configured exogenous.

    Both halves come from the settings, so the only way to change what is fetched is to change
    the configuration. Order within a source follows ``settings.exogenous``, so the fetch
    reports in the order the file lists.
    """
    unplaced = sorted(set(settings.exogenous) - set(EXOGENOUS_SOURCE))
    if unplaced:
        raise SystemExit(
            f"exogenous series {unplaced} have no source in EXOGENOUS_SOURCE, so nothing would "
            "fetch them; add them there, or remove them from the configuration"
        )
    exogenous = {
        source: tuple(name for name in settings.exogenous if EXOGENOUS_SOURCE[name] == source)
        for source in ("cboe_vix", "fred")
    }
    return [
        ("nasdaq_bars", settings.universe),
        ("nasdaq_dividends", settings.universe),
        ("cboe_vix", exogenous["cboe_vix"]),
        ("fred", exogenous["fred"]),
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
    """Fetch every selected series. Returns how many did not end in an archived or skipped state.

    Two states are counted, and they are different things an operator acts on differently:

    * **REFUSED** -- the response arrived and this code rejected it: an HTML maintenance page, a
      body that will not parse, a payload that fails validation. ``fetch_source`` parses before
      it archives, so nothing was written and the archive is intact. Retrying now refuses again;
      the thing to do is look at what the vendor served.
    * **FAILED** -- the fetch itself did not complete: a timeout, a connection error, a non-200
      status (the transport raises once its retries are spent). Retrying later is reasonable.

    Both count toward the return value, and so toward exit code 1: either way the run is
    incomplete, and the operator needs to know. One series failing never stops the others.
    """
    failures = 0
    for source, symbols in _selected(settings, only):
        for symbol in symbols:
            try:
                path = fetch_source(source, symbol, settings=settings)
            except VolabError as exc:
                # ParseRefused (a body the parser could not read, however it broke) and the other
                # VolabErrors are raised by this code after the response arrived. A fetch that
                # did not complete raises something else -- the transport raises RuntimeError --
                # and falls through to FAILED below.
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


# Sources that are valid to fetch and to build raw but have no curated dataset of their own,
# mapped to the source whose curation consumes them. Dividends are read by ``curate_bars`` to set
# ``has_distribution``; curating them "alone" is not a thing, but it is not an error either.
CURATED_WITH: dict[str, str] = {"nasdaq_dividends": "nasdaq_bars"}


def _curate(settings: Settings, only: str | None) -> int:
    end = settings.splits.dev_end
    total = 0
    jobs: list[tuple[str, str, object]] = []
    elsewhere: list[str] = []
    for source, symbols in _selected(settings, only):
        if source == "nasdaq_bars":
            jobs += [("bars", symbol, curate_bars) for symbol in symbols]
        elif source == "cboe_vix":
            jobs += [("vix", symbol, curate_vix) for symbol in symbols]
        elif source == "fred":
            jobs += [("fred", symbol, curate_fred) for symbol in symbols]
        elif source in CURATED_WITH:
            elsewhere.append(
                f"  {source}: curated as part of {CURATED_WITH[source]}, which reads it;"
                " it has no curated dataset of its own"
            )
        else:
            # A source with no rule would otherwise produce no jobs, print nothing and exit 0:
            # a request that did no work, reported as success.
            raise RuntimeError(f"no curation rule for source {source!r}; add one to _curate")
    for kind, symbol, function in jobs:
        try:
            written = function(symbol, end=end, settings=settings)  # type: ignore[operator]
        except DatasetNotFound:
            print(f"  {kind}/{symbol}: no raw data yet, skipped")
            continue
        total += written
        print(f"  {kind}/{symbol}: {written} partitions")
    for line in elsewhere:
        print(line)
    return total


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m volab.ingest", description=__doc__, epilog=_EPILOG
    )
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
