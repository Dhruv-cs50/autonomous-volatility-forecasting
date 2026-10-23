from __future__ import annotations

import datetime as dt

import pandas as pd
from fixture_transport import fixture_transport

from volab.ingest.vix import fetch_vix

FETCHED = dt.datetime(2026, 10, 19, 22, 30, tzinfo=dt.UTC)


def test_fetch_vix_parses_mm_dd_yyyy_dates_and_ohlc() -> None:
    frame = fetch_vix(
        "VIX", transport=fixture_transport("cboe_vix.csv"), fetched_at=FETCHED
    ).sort_values("as_of")

    assert list(frame["as_of"]) == [
        dt.date(2023, 6, 15),
        dt.date(2023, 6, 16),
        dt.date(2023, 6, 20),
    ]
    assert frame["close"].iloc[0] == "14.800000"
    assert frame["open"].iloc[0] == "14.500000"


def test_fetch_vvix_handles_its_two_column_shape() -> None:
    frame = fetch_vix(
        "VVIX", transport=fixture_transport("cboe_vvix.csv"), fetched_at=FETCHED
    ).sort_values("as_of")

    assert frame["close"].iloc[0] == "78.120000"
    # Cboe serves no OHLC for VVIX, so those columns must be absent rather than invented.
    assert "open" not in frame.columns


def test_fetch_vix_as_of_equals_the_session_and_ts_is_aware() -> None:
    frame = fetch_vix(
        "VIX", transport=fixture_transport("cboe_vix.csv"), fetched_at=FETCHED
    ).sort_values("as_of")

    assert isinstance(frame["ts"].dtype, pd.DatetimeTZDtype)
    assert set(frame["symbol"]) == {"VIX"}
    # The name promises as_of equals the session, so assert it: ts is that session's close.
    assert frame["as_of"].iloc[0] == dt.date(2023, 6, 15)
    assert frame["ts"].iloc[0] == pd.Timestamp("2023-06-15 20:00", tz="UTC")


def test_fetch_vix_tolerates_crlf_line_endings() -> None:
    frame = fetch_vix("VIX", transport=fixture_transport("cboe_vix.csv"), fetched_at=FETCHED)

    assert not any(str(value).endswith("\r") for value in frame["close"])
