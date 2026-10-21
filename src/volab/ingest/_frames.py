"""Shared shaping for raw frames: the mandatory columns, provenance, and date handling.

Fetchers leave every vendor value exactly as served -- strings stay strings, separators and
currency symbols stay in place -- because raw is the archive and a parse bug found later must be
a curated rebuild rather than destroyed history. All this module does is attach the four
mandatory columns and the provenance the store contract requires.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

_EXCHANGE_TZ = ZoneInfo("America/New_York")
_CLOSE = dt.time(16, 0)
MANDATORY_FIRST: tuple[str, ...] = ("symbol", "ts", "as_of", "ingested_at")
PROVENANCE: tuple[str, ...] = ("source_url", "vendor_date_text")


def session_close_utc(day: dt.date) -> dt.datetime:
    """16:00 America/New_York on ``day``, in UTC.

    Correct for every regular session and three hours late on an early close, which no Phase 1
    feature can see: ``as_of`` is the point-in-time axis and nothing keys on intraday time. A
    later phase needing true closes should take them from the exchange calendar at curation.
    """
    return dt.datetime.combine(day, _CLOSE, tzinfo=_EXCHANGE_TZ).astimezone(dt.UTC)


def midnight_utc(day: dt.date) -> dt.datetime:
    """``day`` at 00:00 UTC, for a series whose observation carries no time of day."""
    return dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC)


def parse_us_date(text: str) -> dt.date:
    """Parse the ``MM/DD/YYYY`` form Nasdaq and Cboe both use."""
    try:
        return dt.datetime.strptime(text.strip(), "%m/%d/%Y").date()
    except ValueError:
        raise ValueError(f"could not parse vendor date {text!r} as MM/DD/YYYY") from None


def parse_iso_date(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text.strip())
    except ValueError:
        raise ValueError(f"could not parse vendor date {text!r} as YYYY-MM-DD") from None


def build(
    rows: list[dict[str, object]],
    *,
    symbol: str,
    fetched_at: dt.datetime,
    source_url: str,
    payload_columns: tuple[str, ...],
) -> pd.DataFrame:
    """Assemble a raw frame with the mandatory columns first, then provenance, then payload.

    ``rows`` each carry ``ts``, ``as_of``, ``vendor_date_text`` and the payload fields. An empty
    list yields an empty frame with the full column set, so a vendor that legitimately has no
    rows -- Nasdaq's dividends endpoint for SPY -- is distinguishable from a parse failure by
    its shape rather than by an exception.
    """
    if fetched_at.tzinfo is None:
        raise ValueError(f"fetched_at must be timezone-aware, got {fetched_at!r}")
    columns = (*MANDATORY_FIRST, *PROVENANCE, *payload_columns)
    frame = pd.DataFrame(rows, columns=list(columns))
    frame["symbol"] = symbol
    frame["ingested_at"] = pd.Timestamp(fetched_at).tz_convert("UTC")
    frame["source_url"] = source_url
    if rows:
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    else:
        frame["ts"] = pd.Series(dtype="datetime64[us, UTC]")
        frame["ingested_at"] = pd.Series(dtype="datetime64[us, UTC]")
    return frame
