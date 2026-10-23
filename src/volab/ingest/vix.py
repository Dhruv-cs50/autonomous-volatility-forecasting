"""The Cboe VIX family from Cboe's published daily index CSVs.

Keyless, and full history on every request -- VIX from 1990, VIX9D from 2011, VVIX from 2006 and
VIX1D from its 2022-05-13 inception. Because the history is never trailing, these payloads are
archived at most monthly: any one of them subsumes every earlier one.

``VVIX_History.csv`` carries two columns where the other three carry five. That difference is
honoured rather than padded: a VVIX frame has no ``open``/``high``/``low`` at all, instead of
inventing them.
"""

from __future__ import annotations

import csv
import datetime as dt
import io

import pandas as pd

from volab.ingest._frames import build, parse_us_date
from volab.ingest._http import Transport
from volab.sessions import session_close_utc

_HOST = "https://cdn.cboe.com/api/global/us_indices/daily_prices"
OHLC_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")
CLOSE_ONLY_COLUMNS: tuple[str, ...] = ("close",)


def vix_url(series: str) -> str:
    return f"{_HOST}/{series}_History.csv"


def parse_vix(
    body: bytes, *, symbol: str, fetched_at: dt.datetime, source_url: str
) -> pd.DataFrame:
    # newline="" plus the csv module handles the CRLF line endings Cboe sends.
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig"), newline=""))
    header = [name.strip().upper() for name in (reader.fieldnames or [])]
    if not header or header[0] != "DATE":
        raise ValueError(f"{source_url} has no DATE column, got header {reader.fieldnames!r}")
    close_only = len(header) == 2
    rows: list[dict[str, object]] = []
    for record in reader:
        clean = {
            (key or "").strip().upper(): (value or "").strip() for key, value in record.items()
        }
        text = clean["DATE"]
        if not text:
            continue
        day = parse_us_date(text)
        row: dict[str, object] = {
            "ts": session_close_utc(day),
            "as_of": day,
            "vendor_date_text": text,
            "close": clean[symbol.upper()] if close_only else clean["CLOSE"],
        }
        if not close_only:
            row["open"] = clean["OPEN"]
            row["high"] = clean["HIGH"]
            row["low"] = clean["LOW"]
        rows.append(row)
    return build(
        rows,
        symbol=symbol,
        fetched_at=fetched_at,
        source_url=source_url,
        payload_columns=CLOSE_ONLY_COLUMNS if close_only else OHLC_COLUMNS,
    )


def fetch_vix(
    series: str, *, transport: Transport, fetched_at: dt.datetime | None = None
) -> pd.DataFrame:
    """One Cboe index's full published history, exactly as served."""
    stamp = dt.datetime.now(dt.UTC) if fetched_at is None else fetched_at
    url = vix_url(series)
    return parse_vix(transport(url), symbol=series, fetched_at=stamp, source_url=url)
