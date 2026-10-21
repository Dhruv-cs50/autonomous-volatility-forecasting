"""Trading-session arithmetic, in plain ``datetime.date``.

Two kinds of caller need this. Ingestion turns a vendor observation date into the date its
value first became knowable, which for a lagged series is the next session after it. Targets
turn a base session into the session on which the last return in their horizon is known.

Both hand in dates that are **not guaranteed to be sessions** -- a FRED observation can fall on
a holiday, and a configured boundary like ``dev_end`` is deliberately allowed to be a Sunday.
``exchange_calendars``' own ``next_session`` refuses a non-session argument outright, raising
``NotSessionError``, so every helper here is built on the calendar's full session index instead
and takes any date at all.

Everything is a pure function of the calendar: no I/O, no configuration beyond the calendar's
name, and no notion of the holdout. ``get_calendar`` exists so that one place owns turning
``settings.calendar`` into a calendar object, rather than four modules each doing it.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd

from volab.config import Settings


def _as_date(stamp: pd.Timestamp) -> dt.date:
    """A calendar session as a plain date.

    Spelled out because ``exchange_calendars`` carries no type information, so its sessions
    reach mypy as ``Any`` and would otherwise leak that through every return here.
    """
    return dt.date(stamp.year, stamp.month, stamp.day)


@lru_cache(maxsize=8)
def _calendar_by_name(name: str) -> xcals.ExchangeCalendar:
    return xcals.get_calendar(name)


def get_calendar(settings: Settings) -> xcals.ExchangeCalendar:
    """The calendar ``settings.calendar`` names, built once per name.

    Memoised because building an exchange calendar is slow enough to matter when a test suite
    or a per-symbol loop would otherwise rebuild it each time.
    """
    return _calendar_by_name(settings.calendar)


def is_session(calendar: xcals.ExchangeCalendar, day: dt.date) -> bool:
    """Whether ``day`` is a trading session on ``calendar``."""
    return bool(calendar.is_session(pd.Timestamp(day)))


def sessions_between(
    calendar: xcals.ExchangeCalendar, start: dt.date, end: dt.date
) -> list[dt.date]:
    """Every session from ``start`` to ``end``, inclusive at both ends.

    Neither endpoint need be a session. An empty list means the whole stretch was closed.
    """
    sessions = calendar.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    return [_as_date(stamp) for stamp in sessions]


def nth_session_after(calendar: xcals.ExchangeCalendar, day: dt.date, n: int) -> dt.date:
    """The ``n``-th session strictly after ``day``. ``day`` need not be a session.

    ``n=1`` is the next session. Implemented against the calendar's session index rather than
    its ``next_session``, which requires its argument to already be a session.
    """
    if n < 1:
        raise ValueError(f"n must be at least 1, got {n}")
    sessions = calendar.sessions
    # ``side="right"`` makes the search strict: a ``day`` that is itself a session is skipped,
    # so the result is always later than ``day``.
    position = int(sessions.searchsorted(pd.Timestamp(day), side="right")) + n - 1
    if position >= len(sessions):
        raise ValueError(
            f"session {n} after {day} lies beyond the last session the {calendar.name} calendar "
            f"covers ({sessions[-1].date()})"
        )
    return _as_date(sessions[position])


def next_session_after(calendar: xcals.ExchangeCalendar, day: dt.date) -> dt.date:
    """The first session strictly after ``day``. ``day`` need not be a session."""
    return nth_session_after(calendar, day, 1)
