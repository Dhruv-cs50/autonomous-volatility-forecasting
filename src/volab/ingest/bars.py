"""Daily OHLCV bars and distribution dates from Nasdaq's public historical endpoint.

The endpoint is the nasdaq.com site's own backend: public, keyless, undocumented, and capped at
a **trailing ten years**. That cap is why raw is immutable and why the payload archive is
committed -- a session that falls out of the window cannot be fetched again at any price.

Prices arrive as bare numeric strings with no currency symbol; the thousands separators are in
volume. The dividends endpoint is the one that uses a currency symbol, on its amount. Both are
left exactly as served; curation parses them.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pandas as pd

from volab.ingest._frames import build, parse_us_date, session_close_utc
from volab.ingest._http import Transport

_HOST = "https://api.nasdaq.com/api/quote"
BARS_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")
DIVIDEND_COLUMNS: tuple[str, ...] = (
    "dividend_type",
    "amount",
    "declaration_date",
    "record_date",
    "payment_date",
    "currency",
)


def bars_url(symbol: str, *, start: dt.date, end: dt.date) -> str:
    return (
        f"{_HOST}/{symbol}/historical?assetclass=etf"
        f"&fromdate={start.isoformat()}&todate={end.isoformat()}&limit=99999"
    )


def dividends_url(symbol: str) -> str:
    return f"{_HOST}/{symbol}/dividends?assetclass=etf"


def _payload(body: bytes, url: str) -> dict[str, Any]:
    try:
        document: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{url} returned a body that is not JSON: {exc}") from None
    status = document.get("status") or {}
    code = status.get("rCode")
    # A non-200 rCode arrives with HTTP 200 and a null payload, so it has to be checked here or
    # an outage would look like a symbol with no history.
    if code is not None and int(code) != 200:
        raise ValueError(f"{url} returned rCode {code}: {document.get('message')!r}")
    return document


def parse_bars(
    body: bytes, *, symbol: str, fetched_at: dt.datetime, source_url: str
) -> pd.DataFrame:
    document = _payload(body, source_url)
    table = (document.get("data") or {}).get("tradesTable") or {}
    rows: list[dict[str, object]] = []
    for row in table.get("rows") or []:
        day = parse_us_date(str(row["date"]))
        rows.append(
            {
                "ts": session_close_utc(day),
                "as_of": day,
                "vendor_date_text": str(row["date"]),
                "open": str(row["open"]),
                "high": str(row["high"]),
                "low": str(row["low"]),
                "close": str(row["close"]),
                "volume": str(row["volume"]),
            }
        )
    return build(
        rows,
        symbol=symbol,
        fetched_at=fetched_at,
        source_url=source_url,
        payload_columns=BARS_COLUMNS,
    )


def parse_dividends(
    body: bytes, *, symbol: str, fetched_at: dt.datetime, source_url: str
) -> pd.DataFrame:
    document = _payload(body, source_url)
    table = (document.get("data") or {}).get("dividends") or {}
    rows: list[dict[str, object]] = []
    for row in table.get("rows") or []:
        day = parse_us_date(str(row["exOrEffDate"]))
        rows.append(
            {
                "ts": session_close_utc(day),
                "as_of": day,
                "vendor_date_text": str(row["exOrEffDate"]),
                "dividend_type": str(row.get("type", "")),
                "amount": str(row.get("amount", "")),
                "declaration_date": str(row.get("declarationDate", "")),
                "record_date": str(row.get("recordDate", "")),
                "payment_date": str(row.get("paymentDate", "")),
                "currency": str(row.get("currency", "")),
            }
        )
    return build(
        rows,
        symbol=symbol,
        fetched_at=fetched_at,
        source_url=source_url,
        payload_columns=DIVIDEND_COLUMNS,
    )


def fetch_bars(
    symbol: str,
    *,
    start: dt.date,
    end: dt.date,
    transport: Transport,
    fetched_at: dt.datetime | None = None,
) -> pd.DataFrame:
    """Daily OHLCV for ``symbol`` over ``start``..``end``, exactly as the vendor served it."""
    stamp = dt.datetime.now(dt.UTC) if fetched_at is None else fetched_at
    url = bars_url(symbol, start=start, end=end)
    return parse_bars(transport(url), symbol=symbol, fetched_at=stamp, source_url=url)


def fetch_dividends(
    symbol: str, *, transport: Transport, fetched_at: dt.datetime | None = None
) -> pd.DataFrame:
    """Distribution history for ``symbol``.

    Verified 2026-10-19: this endpoint returns rows for QQQ only. SPY and IWM come back with an
    empty ``rows`` list under every ``assetclass``, which is a normal outcome and not an error.
    """
    stamp = dt.datetime.now(dt.UTC) if fetched_at is None else fetched_at
    url = dividends_url(symbol)
    return parse_dividends(transport(url), symbol=symbol, fetched_at=stamp, source_url=url)
