from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from fixture_transport import fixture_transport

from volab.ingest.bars import fetch_bars, fetch_dividends

FETCHED = dt.datetime(2026, 10, 19, 22, 30, tzinfo=dt.UTC)
START = dt.date(2023, 6, 1)
END = dt.date(2023, 6, 30)


def _bars() -> pd.DataFrame:
    return fetch_bars(
        "SPY",
        start=START,
        end=END,
        transport=fixture_transport("nasdaq_bars_spy.json"),
        fetched_at=FETCHED,
    )


def test_fetch_bars_parses_every_row() -> None:
    frame = _bars()

    assert len(frame) == 3
    assert set(frame["symbol"]) == {"SPY"}


def test_fetch_bars_keeps_prices_as_served() -> None:
    frame = _bars().sort_values("as_of").reset_index(drop=True)

    # Bare numeric strings, no currency symbol -- verified against the live endpoint.
    assert frame["close"].iloc[0] == "437.21"
    # Thousands separators live in volume, not price, and are left for curation to strip.
    assert frame["volume"].iloc[0] == "1,234,567"


def test_fetch_bars_as_of_is_the_session_date() -> None:
    frame = _bars().sort_values("as_of").reset_index(drop=True)

    assert list(frame["as_of"]) == [
        dt.date(2023, 6, 15),
        dt.date(2023, 6, 16),
        dt.date(2023, 6, 20),
    ]


def test_fetch_bars_ts_is_timezone_aware_at_the_close() -> None:
    frame = _bars()

    assert isinstance(frame["ts"].dtype, pd.DatetimeTZDtype)
    # 16:00 America/New_York on 2023-06-15 is 20:00 UTC during daylight saving.
    first = frame.sort_values("as_of")["ts"].iloc[0]
    assert first == pd.Timestamp("2023-06-15 20:00", tz="UTC")


def test_fetch_bars_carries_provenance() -> None:
    frame = _bars()

    assert frame["ingested_at"].iloc[0] == pd.Timestamp(FETCHED)
    assert "api.nasdaq.com" in frame["source_url"].iloc[0]
    assert frame.sort_values("as_of")["vendor_date_text"].iloc[0] == "06/15/2023"


def test_fetch_dividends_parses_the_amount_with_its_symbol() -> None:
    frame = fetch_dividends(
        "QQQ",
        transport=fixture_transport("nasdaq_dividends_qqq.json"),
        fetched_at=FETCHED,
    )

    assert len(frame) == 1
    # The dividends endpoint DOES use a currency symbol, unlike bars.
    assert frame["amount"].iloc[0] == "$0.51234"
    assert frame["as_of"].iloc[0] == dt.date(2023, 6, 16)


def test_fetch_dividends_empty_is_not_an_error() -> None:
    frame = fetch_dividends(
        "SPY",
        transport=fixture_transport("nasdaq_dividends_empty.json"),
        fetched_at=FETCHED,
    )

    assert frame.empty
    assert list(frame.columns)[:4] == ["symbol", "ts", "as_of", "ingested_at"]


def test_fetch_bars_refuses_a_payload_whose_status_is_not_ok() -> None:
    def broken(url: str) -> bytes:
        return b'{"data":null,"message":"blocked","status":{"rCode":403}}'

    with pytest.raises(ValueError, match="403"):
        fetch_bars("SPY", start=START, end=END, transport=broken, fetched_at=FETCHED)
