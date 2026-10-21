"""Session arithmetic, which every ``as_of`` lag and every target horizon runs through.

The awkward cases are the ones that matter: a vendor observation date is not guaranteed to be a
trading session, and ``exchange_calendars``' own ``next_session`` refuses a non-session argument
outright. These helpers exist so no caller has to know that.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import exchange_calendars as xcals
import pytest

from volab.config import Settings, Splits
from volab.sessions import (
    get_calendar,
    is_session,
    next_session_after,
    nth_session_after,
    sessions_between,
)

# Built once: constructing XNYS is slow enough that per-test construction shows up in the suite.
CAL = xcals.get_calendar("XNYS")


def test_next_session_after_a_session() -> None:
    assert next_session_after(CAL, dt.date(2023, 12, 29)) == dt.date(2024, 1, 2)


def test_next_session_after_a_non_session() -> None:
    # 2023-12-31 is a Sunday. exchange_calendars' own next_session raises on it; this must not.
    assert next_session_after(CAL, dt.date(2023, 12, 31)) == dt.date(2024, 1, 2)


def test_next_session_skips_the_new_year_holiday() -> None:
    # The repository's own boundary: dev_end 2025-12-31, holdout_start 2026-01-02.
    assert next_session_after(CAL, dt.date(2025, 12, 31)) == dt.date(2026, 1, 2)


def test_next_session_after_a_saturday() -> None:
    assert next_session_after(CAL, dt.date(2023, 6, 17)) == dt.date(2023, 6, 20)


def test_nth_session_after_crosses_a_holiday_and_a_year_boundary() -> None:
    assert nth_session_after(CAL, dt.date(2023, 12, 29), 1) == dt.date(2024, 1, 2)
    assert nth_session_after(CAL, dt.date(2023, 12, 29), 2) == dt.date(2024, 1, 3)


def test_nth_session_after_at_the_longest_horizon() -> None:
    assert nth_session_after(CAL, dt.date(2023, 12, 29), 66) == dt.date(2024, 4, 5)


def test_nth_session_after_one_equals_next_session_after() -> None:
    for day in (dt.date(2023, 6, 15), dt.date(2023, 12, 31), dt.date(2024, 7, 4)):
        assert nth_session_after(CAL, day, 1) == next_session_after(CAL, day)


@pytest.mark.parametrize("n", [0, -1])
def test_nth_session_after_rejects_a_non_positive_n(n: int) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        nth_session_after(CAL, dt.date(2023, 6, 15), n)


def test_nth_session_after_past_the_calendar_bound_is_refused() -> None:
    last = CAL.sessions[-1].date()
    with pytest.raises(ValueError, match="beyond the last session"):
        nth_session_after(CAL, last, 1)


def test_sessions_between_is_inclusive_and_excludes_the_weekend() -> None:
    # Thursday 2023-06-15 to Tuesday 2023-06-20; the 17th and 18th are a weekend.
    assert sessions_between(CAL, dt.date(2023, 6, 15), dt.date(2023, 6, 20)) == [
        dt.date(2023, 6, 15),
        dt.date(2023, 6, 16),
        dt.date(2023, 6, 20),
    ]


def test_sessions_between_a_single_session() -> None:
    assert sessions_between(CAL, dt.date(2023, 6, 15), dt.date(2023, 6, 15)) == [
        dt.date(2023, 6, 15)
    ]


def test_sessions_between_a_closed_stretch_is_empty() -> None:
    assert sessions_between(CAL, dt.date(2023, 6, 17), dt.date(2023, 6, 18)) == []


def test_is_session_on_a_holiday_and_on_a_session() -> None:
    assert not is_session(CAL, dt.date(2026, 1, 1))
    assert is_session(CAL, dt.date(2026, 1, 2))


def _settings() -> Settings:
    return Settings(
        universe=("SPY",),
        exogenous=("VIX",),
        calendar="XNYS",
        splits=Splits(dev_end=dt.date(2025, 12, 31), holdout_start=dt.date(2026, 1, 2)),
        storage_root=Path("data"),
    )


def test_get_calendar_reads_the_name_from_settings() -> None:
    assert get_calendar(_settings()).name == "XNYS"


def test_get_calendar_is_memoised() -> None:
    assert get_calendar(_settings()) is get_calendar(_settings())
