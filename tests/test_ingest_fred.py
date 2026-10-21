from __future__ import annotations

import datetime as dt

import exchange_calendars as xcals
import pandas as pd
import pytest
from fixture_transport import fixture_transport

from volab.ingest.fred import FRED_LAG_SESSIONS, fetch_fred

CAL = xcals.get_calendar("XNYS")
FETCHED = dt.datetime(2026, 10, 19, 22, 30, tzinfo=dt.UTC)


def _fred(name: str = "fred_baa10y.csv", series: str = "BAA10Y") -> pd.DataFrame:
    return fetch_fred(
        series, transport=fixture_transport(name), calendar=CAL, fetched_at=FETCHED
    ).sort_values("ts")


def test_fetch_fred_reads_the_series_named_value_column() -> None:
    frame = _fred()

    assert len(frame) == 3
    assert frame["value"].iloc[0] == "1.78"
    assert set(frame["symbol"]) == {"BAA10Y"}


def test_fetch_fred_lags_as_of_to_the_next_session() -> None:
    frame = _fred()

    # An observation dated Thursday 2023-06-15 first becomes knowable on Friday the 16th.
    assert frame["ts"].iloc[0] == pd.Timestamp("2023-06-15", tz="UTC")
    assert frame["as_of"].iloc[0] == dt.date(2023, 6, 16)


def test_fetch_fred_lag_crosses_a_holiday() -> None:
    frame = _fred()

    # Friday 2023-06-16's value lands on Tuesday the 20th: the 19th is Juneteenth.
    assert frame["as_of"].iloc[1] == dt.date(2023, 6, 20)


def test_fred_lag_table_defaults_to_one_session() -> None:
    assert FRED_LAG_SESSIONS.get("DGS10", 1) == 1


def test_fetch_fred_refuses_an_unparseable_value() -> None:
    """Defensive, not observed: fredgraph.csv omits gaps rather than sending '.'.

    A silent 0.0 in a credit spread would be a real hazard, so an unrecognised value fails.
    """
    with pytest.raises(ValueError, match="could not parse"):
        _fred("fred_with_dot.csv", "DGS10")
