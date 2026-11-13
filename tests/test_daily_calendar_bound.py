"""HR-8: a date the exchange calendar cannot classify is refused, not raised.

HR-7 removed the plan's upper bound once the reveal is committed, correctly. A consequence was that
a revealed ``--as-of`` past the calendar's last session reached ``is_session`` and raised
``DateOutOfBounds`` -- a traceback where an outcome belongs. The same was true of a date before the
calendar's first session. Both are now ``refused``, naming the calendar and its range.

``refused`` and not ``no_session``: ``no_session`` says the exchange was closed and exits 0, and for
a date outside the calendar nobody knows that. ``refused`` exits 1, so an operator sees it.

The bounds are read from the calendar itself rather than written down, because the last session
rolls forward with the clock (``get_calendar`` builds to a year past today).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from volab.config import Settings, Splits
from volab.daily import plan_for
from volab.sessions import get_calendar


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        universe=("SPY",),
        exogenous=(),
        calendar="XNYS",
        splits=Splits(
            dev_end=dt.date(2025, 12, 31),
            holdout_start=dt.date(2026, 1, 2),
            holdout_end=dt.date(2026, 10, 30),
        ),
        storage_root=tmp_path,
    )


def _bounds(settings: Settings) -> tuple[dt.date, dt.date]:
    calendar = get_calendar(settings)
    return calendar.first_session.date(), calendar.last_session.date()


def test_a_revealed_date_past_the_calendars_last_session_is_refused_naming_the_bound(
    settings: Settings,
) -> None:
    _, last = _bounds(settings)
    for day in (last + dt.timedelta(days=1), last + dt.timedelta(days=400)):
        plan = plan_for(settings, day, revealed=True)
        assert plan.outcome == "refused"
        assert plan.stages == ()
        assert str(last) in plan.reason
        assert "XNYS" in plan.reason
        assert "dev_end" not in plan.reason  # not the seal's refusal: the seal is lifted


def test_the_calendars_last_session_itself_is_still_planned(settings: Settings) -> None:
    _, last = _bounds(settings)
    assert plan_for(settings, last, revealed=True).outcome == "work"


def test_a_date_before_the_calendars_first_session_is_refused_naming_the_bound(
    settings: Settings,
) -> None:
    first, _ = _bounds(settings)
    for revealed in (False, True):
        plan = plan_for(settings, first - dt.timedelta(days=1), revealed=revealed)
        assert plan.outcome == "refused"
        assert str(first) in plan.reason
        assert "XNYS" in plan.reason


def test_the_seal_is_still_refused_first_when_not_revealed(settings: Settings) -> None:
    """Past the calendar *and* sealed: the seal's reason wins, as the refusal order requires."""
    _, last = _bounds(settings)
    plan = plan_for(settings, last + dt.timedelta(days=1), revealed=False)
    assert plan.outcome == "refused"
    assert "dev_end" in plan.reason
