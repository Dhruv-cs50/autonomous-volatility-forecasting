"""FRED macro series from the keyless ``fredgraph.csv`` endpoint.

``as_of`` is where the care goes. A FRED observation dated ``D`` is not knowable on ``D``. How
much later each series lands is **not** established here: the only measurement this repository
holds is one snapshot, fetched 78 minutes after the close on 2026-10-22, in which T10Y2Y runs
through 2026-10-22 while *both* DGS10 and BAA10Y stop at 2026-10-21 -- so on that day the
Treasury constant-maturity yield was no earlier than the Moody's spread, and one snapshot could
not settle a relative publication lag in any case. Rather than model each release calendar,
every series takes the same conservative lag in **sessions**, defaulting to one; the asymmetry
decides it -- too long a lag only withholds information from a model, too short a lag leaks it.
``FRED_LAG_SESSIONS`` is empty, so no series is treated differently from any other today, and
the distinction would need a measurement before it became one.

Two observed quirks. The header is ``observation_date,<SERIES_ID>``, so the value column is
named after the series rather than ``value``. And a missing observation arrives as a row with an
**empty value field**, not as the ``.`` placeholder FRED's other interfaces use and not as an
omitted row: measured on BAA10Y, 444 of 10,645 rows, every one a market holiday. An earlier
version of this docstring claimed the endpoint omitted them entirely, on a probe that had only
counted ``.`` markers; the live backfill disproved it. See the comment at the skip below for how
an empty field is distinguished from a value this parser cannot read, which still raises.
"""

from __future__ import annotations

import csv
import datetime as dt
import io

import exchange_calendars as xcals
import pandas as pd

from volab.ingest._frames import build, midnight_utc, parse_iso_date
from volab.ingest._http import Transport
from volab.sessions import nth_session_after

_HOST = "https://fred.stlouisfed.org/graph/fredgraph.csv"
VALUE_COLUMNS: tuple[str, ...] = ("value",)

# Series id -> publication lag in trading sessions. Anything absent takes the default of 1.
# Editing this dict is how the lag is configured; it is deliberately not a config key, because
# `config.py` rejects unknown keys and one additive key was the agreed budget for this phase.
FRED_LAG_SESSIONS: dict[str, int] = {}
DEFAULT_LAG_SESSIONS = 1


def fred_url(series: str) -> str:
    return f"{_HOST}?id={series}"


def _validate_number(text: str, *, series: str, day: dt.date) -> str:
    try:
        float(text)
    except ValueError:
        raise ValueError(
            f"could not parse {series} value {text!r} on {day} as a number; refusing to coerce "
            "it, because a silent zero in a macro series is worse than a failed ingest"
        ) from None
    return text


def parse_fred(
    body: bytes,
    *,
    symbol: str,
    fetched_at: dt.datetime,
    source_url: str,
    calendar: xcals.ExchangeCalendar,
) -> pd.DataFrame:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    header = [name.strip() for name in (reader.fieldnames or [])]
    if len(header) != 2 or header[0] != "observation_date":
        raise ValueError(
            f"{source_url} header is not 'observation_date,<SERIES_ID>', got {header!r}"
        )
    value_column = header[1]
    lag = FRED_LAG_SESSIONS.get(symbol, DEFAULT_LAG_SESSIONS)
    rows: list[dict[str, object]] = []
    for record in reader:
        text = (record.get("observation_date") or "").strip()
        if not text:
            continue
        day = parse_iso_date(text)
        raw = (record.get(value_column) or "").strip()
        # FRED emits a row for every business day and leaves the value EMPTY on a market
        # holiday: measured on BAA10Y, 444 of 10,645 rows, every one a holiday -- MLK Day,
        # Presidents' Day, Good Friday, Memorial Day, Independence Day, Labor Day. An empty
        # field is an explicit statement that there was no observation, which is different in
        # kind from a value this parser cannot read, so the row is skipped rather than refused.
        # Anything else unparseable still raises below.
        if not raw:
            continue
        rows.append(
            {
                "ts": midnight_utc(day),
                # The date the value first became knowable, never the observation date.
                "as_of": nth_session_after(calendar, day, lag),
                "vendor_date_text": text,
                "value": _validate_number(raw, series=symbol, day=day),
            }
        )
    return build(
        rows,
        symbol=symbol,
        fetched_at=fetched_at,
        source_url=source_url,
        payload_columns=VALUE_COLUMNS,
    )


def fetch_fred(
    series: str,
    *,
    transport: Transport,
    calendar: xcals.ExchangeCalendar,
    fetched_at: dt.datetime | None = None,
) -> pd.DataFrame:
    """One FRED series' full history, with ``as_of`` lagged to the next session after each value.

    Takes the calendar explicitly rather than building one from ``Settings``: a fetcher does no
    configuration lookup, and the lag is the only reason it needs a calendar at all.
    """
    stamp = dt.datetime.now(dt.UTC) if fetched_at is None else fetched_at
    url = fred_url(series)
    return parse_fred(
        transport(url), symbol=series, fetched_at=stamp, source_url=url, calendar=calendar
    )
