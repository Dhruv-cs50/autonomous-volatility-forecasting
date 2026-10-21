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
import os
import tempfile
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

# The store's inception. Earlier rows are dropped. The bars window's floor is a trailing ten
# years -- 2016-10-20 as of 2026-10-21, and it moves forward a session a day -- and the longest
# trailing window any Phase 1 feature uses is 66 sessions. 2016-07-01 sits 77 sessions before
# that floor: more warmup than 66 needs, with room for the floor to drift.
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


def monthly_slot_taken(
    settings: Settings, source: str, symbol: str, fetched_at: dt.datetime
) -> bool:
    """Whether a full-history source already holds a payload for ``fetched_at``'s month.

    Checked before fetching as well as before archiving, so a skipped month does not download a
    vendor's entire history only to discard it.
    """
    if source not in ARCHIVE_MONTHLY:
        return False
    directory = _payload_root(settings, source, symbol)
    if not directory.is_dir():
        return False
    prefix = fetched_at.astimezone(dt.UTC).strftime("%Y%m")
    return any(entry.name.startswith(prefix) for entry in directory.glob("*.gz"))


def _latest_payload(settings: Settings, source: str, symbol: str) -> Path | None:
    """The most recently stamped payload already archived for this source and symbol."""
    directory = _payload_root(settings, source, symbol)
    if not directory.is_dir():
        return None
    existing = sorted(directory.glob("*.gz"))
    return existing[-1] if existing else None


def body_already_archived(settings: Settings, source: str, symbol: str, body: bytes) -> bool:
    """Whether ``body`` is byte-identical to the newest payload already held.

    A trailing-window source is archived on every fetch, because each payload holds sessions
    later ones will have lost. But two fetches on the same day see the *same* window, and this
    archive is committed to git, so storing a second identical copy costs history forever and
    buys nothing. Compared by content rather than by date so the rule needs no per-source
    calendar: a genuinely new window has different bytes.
    """
    latest = _latest_payload(settings, source, symbol)
    if latest is None:
        return False
    return gzip.decompress(latest.read_bytes()) == body


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through a temp file in the same directory, then rename.

    The archive is append-only and committed, so a truncated file is permanent: it would make
    every later ``build_raw`` for that source and symbol fail on decompression, and for a
    monthly source it would occupy the month's slot. ``store._write`` guards its Parquet writes
    the same way, for the same reason.
    """
    # Suffix ".tmp", never the payload's own ".gz": build_raw and the dedupe check both glob
    # "*.gz", pathlib's glob matches dotfiles, and a temp named ".tmp-X.gz" left by a hard kill --
    # where the cleanup below never runs -- would be replayed as a payload, crash on its
    # unparseable timestamp, and be committable besides.
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def archive_payload(
    body: bytes,
    *,
    source: str,
    symbol: str,
    fetched_at: dt.datetime,
    extension: str,
    source_url: str,
    settings: Settings | None = None,
) -> Path | None:
    """Store ``body`` untouched and return its path, or ``None`` if this month already has one.

    ``source_url`` is **recorded, not reconstructed**: the caller passes the URL it actually
    requested, which for a bars fetch carries the date range it asked for. Rebuilding it here
    would defeat the sidecar's purpose, because that range cannot be recovered afterwards.

    Returns ``None`` when there is nothing new to store: a source in ``ARCHIVE_MONTHLY`` whose
    month is already taken, or a body byte-identical to the newest payload already held. Refuses
    to rewrite an existing payload: the archive is append-only.
    """
    if fetched_at.tzinfo is None:
        raise ValueError(f"fetched_at must be timezone-aware, got {fetched_at!r}")
    resolved = load_settings() if settings is None else settings
    stamp = fetched_at.astimezone(dt.UTC)
    if monthly_slot_taken(resolved, source, symbol, stamp):
        return None
    if body_already_archived(resolved, source, symbol, body):
        return None

    directory = _payload_root(resolved, source, symbol)
    path = directory / f"{stamp.strftime(_STAMP)}.{extension}.gz"
    if path.exists():
        raise RawOverwrite(
            f"{path} already exists; the payload archive is append-only, and two fetches of one "
            "source and symbol inside a second collide on the filename stamp"
        )
    directory.mkdir(parents=True, exist_ok=True)
    # The sidecar lands first and the body second, so the only partial state a crash can leave is
    # a sidecar with no payload -- and ``build_raw`` globs ``*.gz``, so it ignores one.
    sidecar = path.with_suffix("").with_suffix(".meta.json")
    _atomic_write(
        sidecar,
        (
            json.dumps(
                {
                    "source": source,
                    "symbol": symbol,
                    "fetched_at": fetched_at.isoformat(),
                    "source_url": source_url,
                },
                indent=2,
            )
            + "\n"
        ).encode("utf-8"),
    )
    # mtime=0 so an identical body always produces identical bytes: a committed archive should
    # not show a diff just because it was re-gzipped.
    _atomic_write(path, gzip.compress(body, mtime=0))
    return path


def _url_for(source: str, symbol: str, *, end: dt.date) -> str:
    """The URL to request for a source and symbol.

    ``end`` is passed in rather than read from the clock, so the URL a fetch records is the URL
    that fetch actually requested.
    """
    if source == "nasdaq_bars":
        return bars.bars_url(symbol, start=INGEST_FLOOR, end=end)
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
    # Before fetching, not after: a monthly source serves its whole history on every request, so
    # a skipped month must not download megabytes in order to discard them.
    if monthly_slot_taken(resolved, source, symbol, stamp):
        return None
    url = _url_for(source, symbol, end=stamp.date())
    body = transport(url)
    # Parsed before it is archived, and the result discarded. The archive is append-only and
    # committed, so an error page or a truncated response would be permanent -- and because
    # build_raw replays every payload for a symbol, one unparseable body would abort that replay
    # for good, taking every later valid payload with it. For a monthly source it would occupy
    # the month's slot as well. This check is what stops a bad response poisoning the archive.
    _parse(source, body, symbol=symbol, fetched_at=stamp, source_url=url, settings=resolved)
    extension = "json" if source.startswith("nasdaq") else "csv"
    return archive_payload(
        body,
        source=source,
        symbol=symbol,
        fetched_at=stamp,
        extension=extension,
        source_url=url,
        settings=resolved,
    )


def build_raw(
    source: str,
    *,
    symbols: Sequence[str],
    since: dt.date = INGEST_FLOOR,
    settings: Settings | None = None,
) -> int:
    """Replay archived payloads into raw Parquet. Returns the number of partitions written.

    Reads the archive and writes through the store. It takes no transport and has no path to
    one: replay never touches the network, which is what lets a parse change be re-applied to
    data a vendor may no longer serve.
    """
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
            # A missing sidecar means provenance was never recorded. Say so, rather than
            # fabricating a URL from today's clock that this payload was never fetched from.
            source_url = (
                json.loads(sidecar.read_text())["source_url"]
                if sidecar.is_file()
                else f"archive://{source}/{symbol}/{payload.name}"
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
