"""Ingestion: fetch vendor payloads, archive them, and replay them into immutable raw Parquet.

Three stages, deliberately separate:

1. **fetch** -- a ``Transport`` call plus parsing, in ``bars``, ``vix`` and ``fred``. No file is
   written and no Parquet is touched; these return DataFrames.
2. **archive** -- ``archive_payload`` stores the untouched response body under
   ``data/vendor_payloads/<source>/<symbol>/``, one immutable gzip file per payload, with a
   sidecar recording its provenance. This tree is committed, because Nasdaq's endpoint serves
   only a trailing ten-year window and a session that falls out of it cannot be refetched at
   any price. The archive is therefore the only thing that makes a rebuild possible.
3. **build_raw** -- replays archived payloads into raw Parquet through ``store.write_raw``.

Nothing here imports pyarrow or duckdb. Writes go through the store, which is the only module
permitted to touch Parquet, and the import ratchet fails the build if that ever stops being true.

Two properties worth naming, because they are what make a half-finished backfill safe:

- ``build_raw`` **groups by** ``as_of`` **and writes one partition per session**, because
  ``write_raw`` validates that every row's ``as_of`` equals the single partition it is given.
- ``build_raw`` **skips** a partition that already exists rather than letting ``write_raw``
  refuse it. Raw is immutable, so without the skip a backfill of thousands of partitions
  interrupted halfway could never be resumed.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from volab.config import Settings, load_settings
from volab.errors import RawOverwrite
from volab.ingest import bars, fred, vix
from volab.ingest._http import Transport, default_transport
from volab.paths import Tier, validate_segment
from volab.sessions import get_calendar
from volab.store import write_raw

__all__ = [
    "ARCHIVE_MONTHLY",
    "INGEST_FLOOR",
    "PAYLOAD_DIRECTORY",
    "SOURCES",
    "Transport",
    "archive_payload",
    "build_raw",
    "default_transport",
    "fetch_source",
]

# The store's inception. Earlier rows are dropped: the bars window starts 2016-10-19 and this is
# the 66th session before it, which is the longest trailing window any Phase 1 feature uses.
INGEST_FLOOR: dt.date = dt.date(2016, 7, 1)

# Sources that serve their FULL history on every request. One payload subsumes every earlier
# one, so archiving each daily fetch would add ~194 MB a year of almost entirely redundant data.
# At most one payload per series per calendar month, which still leaves a monthly vintage trail.
ARCHIVE_MONTHLY: frozenset[str] = frozenset({"cboe_vix", "fred"})

PAYLOAD_DIRECTORY = "vendor_payloads"
_STAMP = "%Y%m%dT%H%M%SZ"

SOURCES: tuple[str, ...] = ("nasdaq_bars", "nasdaq_dividends", "cboe_vix", "fred")


def _payload_root(settings: Settings, source: str, symbol: str) -> Path:
    return (
        settings.storage_root
        / PAYLOAD_DIRECTORY
        / validate_segment("source", source)
        / validate_segment("symbol", symbol)
    )


def archive_payload(
    body: bytes,
    *,
    source: str,
    symbol: str,
    fetched_at: dt.datetime,
    extension: str,
    settings: Settings | None = None,
) -> Path | None:
    """Store ``body`` untouched and return its path, or ``None`` if this month already has one.

    Returns ``None`` only for a source in ``ARCHIVE_MONTHLY``; a trailing-window source is always
    archived, because each of its payloads holds sessions that later ones will have lost.
    Refuses to rewrite an existing payload: the archive is append-only.
    """
    if fetched_at.tzinfo is None:
        raise ValueError(f"fetched_at must be timezone-aware, got {fetched_at!r}")
    stamp = fetched_at.astimezone(dt.UTC)
    directory = _payload_root(load_settings() if settings is None else settings, source, symbol)
    if source in ARCHIVE_MONTHLY and directory.is_dir():
        prefix = stamp.strftime("%Y%m")
        if any(entry.name.startswith(prefix) for entry in directory.glob("*.gz")):
            return None

    path = directory / f"{stamp.strftime(_STAMP)}.{extension}.gz"
    if path.exists():
        raise RawOverwrite(
            f"{path} already exists; the payload archive is append-only, and two fetches of one "
            "source and symbol inside a second collide on the filename stamp"
        )
    directory.mkdir(parents=True, exist_ok=True)
    # mtime=0 so the same body always produces the same bytes: a committed archive should not
    # show a diff just because it was re-gzipped.
    path.write_bytes(gzip.compress(body, mtime=0))
    sidecar = path.with_suffix("").with_suffix(".meta.json")
    sidecar.write_text(
        json.dumps(
            {
                "source": source,
                "symbol": symbol,
                "fetched_at": fetched_at.isoformat(),
                "source_url": _url_for(source, symbol),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _url_for(source: str, symbol: str) -> str:
    """The canonical URL for a source and symbol, for the archive's provenance sidecar.

    A bars URL additionally carries the date range it was fetched with, which is why the sidecar
    exists at all rather than the URL being rebuilt at replay time.
    """
    if source == "nasdaq_bars":
        return bars.bars_url(symbol, start=INGEST_FLOOR, end=dt.date.today())
    if source == "nasdaq_dividends":
        return bars.dividends_url(symbol)
    if source == "cboe_vix":
        return vix.vix_url(symbol)
    if source == "fred":
        return fred.fred_url(symbol)
    raise ValueError(f"unknown source {source!r}; known sources are {list(SOURCES)}")


def _parse(
    source: str,
    body: bytes,
    *,
    symbol: str,
    fetched_at: dt.datetime,
    source_url: str,
    settings: Settings,
) -> pd.DataFrame:
    if source == "nasdaq_bars":
        return bars.parse_bars(body, symbol=symbol, fetched_at=fetched_at, source_url=source_url)
    if source == "nasdaq_dividends":
        return bars.parse_dividends(
            body, symbol=symbol, fetched_at=fetched_at, source_url=source_url
        )
    if source == "cboe_vix":
        return vix.parse_vix(body, symbol=symbol, fetched_at=fetched_at, source_url=source_url)
    if source == "fred":
        return fred.parse_fred(
            body,
            symbol=symbol,
            fetched_at=fetched_at,
            source_url=source_url,
            calendar=get_calendar(settings),
        )
    raise ValueError(f"unknown source {source!r}; known sources are {list(SOURCES)}")


def fetch_source(
    source: str,
    symbol: str,
    *,
    transport: Transport = default_transport,
    fetched_at: dt.datetime | None = None,
    settings: Settings | None = None,
) -> Path | None:
    """Fetch one source and symbol and archive the response. Returns the payload path.

    Deliberately does not write Parquet: ``build_raw`` does that, from the archive, so a parse
    change never requires refetching data the vendor may no longer serve.
    """
    resolved = load_settings() if settings is None else settings
    stamp = dt.datetime.now(dt.UTC) if fetched_at is None else fetched_at
    url = _url_for(source, symbol)
    extension = "json" if source.startswith("nasdaq") else "csv"
    return archive_payload(
        transport(url),
        source=source,
        symbol=symbol,
        fetched_at=stamp,
        extension=extension,
        settings=resolved,
    )


def build_raw(
    source: str,
    *,
    symbols: Sequence[str],
    since: dt.date = INGEST_FLOOR,
    settings: Settings | None = None,
    transport: Transport = default_transport,
) -> int:
    """Replay archived payloads into raw Parquet. Returns the number of partitions written.

    ``transport`` is accepted but unused: replay reads the archive, never the network. It is in
    the signature so a caller can pass a transport that raises and prove that.
    """
    del transport
    resolved = load_settings() if settings is None else settings
    written = 0
    for symbol in symbols:
        directory = _payload_root(resolved, source, symbol)
        if not directory.is_dir():
            continue
        # Oldest payload first, so the earliest vintage to carry a session owns its partition and
        # later payloads only contribute sessions the earlier ones did not have.
        for payload in sorted(directory.glob("*.gz")):
            fetched_at = dt.datetime.strptime(payload.name.split(".")[0], _STAMP).replace(
                tzinfo=dt.UTC
            )
            sidecar = payload.with_suffix("").with_suffix(".meta.json")
            source_url = (
                json.loads(sidecar.read_text())["source_url"]
                if sidecar.is_file()
                else _url_for(source, symbol)
            )
            frame = _parse(
                source,
                gzip.decompress(payload.read_bytes()),
                symbol=symbol,
                fetched_at=fetched_at,
                source_url=source_url,
                settings=resolved,
            )
            if frame.empty:
                continue
            frame = frame[frame["as_of"] >= since]
            for as_of, partition in frame.groupby("as_of", sort=True):
                day: dt.date = as_of  # type: ignore[assignment]
                if _raw_exists(resolved, source, symbol, day):
                    continue
                write_raw(
                    partition.reset_index(drop=True),
                    source=source,
                    symbol=symbol,
                    as_of=day,
                    ingested_at=fetched_at,
                    settings=resolved,
                )
                written += 1
    return written


def _raw_exists(settings: Settings, source: str, symbol: str, as_of: dt.date) -> bool:
    """Whether any raw file already occupies this partition.

    Checked rather than catching ``RawOverwrite``, so a resumed backfill is ordinary control
    flow instead of an exception per already-written partition.
    """
    partition = (
        settings.storage_root / Tier.RAW.value / source / symbol / f"date={as_of.isoformat()}"
    )
    return partition.is_dir() and any(partition.glob("*.parquet"))
