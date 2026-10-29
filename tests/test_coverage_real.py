"""``coverage_refusal`` on the committed store (US-3.9a): the mechanism, on the real VIX1D.

The toy tests in ``test_domain.py`` prove the refusal against a fixture built to express it. This
file closes the gap the design left optional: the measured figures it rests on -- VIX1D begins
2022-05-13 where the other IV series begin 2016-07-01, and a VIX1D-requiring contestant would cost
the shootout roughly half its origins -- came from a script, and nothing tied the code to them.

**What is asserted is the mechanism, not today's count.** The first runnable origin is derived
here from the definition (the ``minimum_training``-th session that is non-null in the HAR floor
and the column, plus the session its target completes on), computed by a cumulative scan that
shares no code with ``coverage_refusal``'s per-origin rebuild. A test pinning "119" would pin the
store and fail on the next honest rebuild, which is the failure mode R72, R78 and R99 record. The
one place the figures *are* pinned is ``test_the_designs_measured_figures_still_hold``, labelled as
exactly that: a one-time agreement between the design's script and this code, expected to be
re-measured, not re-edited, when the store changes.

Reads go through ``wf.load_for`` and the CLI's own persisted-table read, both of which stop at
``dev_end``; they run from inside test bodies so the autouse holdout fixture polices them.
"""

from __future__ import annotations

import datetime as dt
import functools
import re

import pandas as pd
import pytest
from model_conformance import Spy

import volab.compare.__main__ as compare_cli
import volab.walkforward as wf
from volab.config import Settings, load_settings
from volab.errors import DatasetNotFound
from volab.features.targets import HORIZONS
from volab.models.base import Cohort, Domain
from volab.panel import as_of_dates
from volab.sessions import get_calendar

SYMBOL = "SPY"
REQUIRED = "iv_h1"  # VIX1D, the one IV series that starts late
WINDOW_FIRST = dt.date(2021, 1, 4)  # the shootout's own default window
EVERY = 5
MINIMUM = 250


@functools.cache
def real(horizon: int) -> tuple[Settings, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Settings, curated panel, targets and the persisted feature table, read once per horizon.

    Cached here and not in a module-scoped fixture, which would run before the autouse holdout
    police. Callers must not mutate the frames.
    """
    settings = load_settings()
    try:
        panel, targets = wf.load_for(settings, SYMBOL, horizon)
        stored = compare_cli._persisted(settings, SYMBOL)
    except DatasetNotFound as missing:
        pytest.fail(
            f"the curated store is incomplete ({missing}); rebuild it with "
            "`uv run --locked python -m volab.ingest build-raw`, then `curate`, then "
            "`python -m volab.features build`"
        )
    if stored is None:
        pytest.fail("the store holds no built feature table; run `python -m volab.features build`")
    return settings, panel, targets, stored


def origins_for(horizon: int, first: dt.date = WINDOW_FIRST) -> list[dt.date]:
    settings, _, targets, _ = real(horizon)
    return wf.scoreable_origins(
        settings, targets, first=first, last=settings.splits.dev_end, every=EVERY
    )


def first_runnable_origin(horizon: int, origins: list[dt.date]) -> dt.date | None:
    """From the definition, by a cumulative scan: the first origin at which ``MINIMUM`` rows are
    non-null in the HAR floor and ``REQUIRED`` **and** have a target completed by the origin."""
    _, _, targets, stored = real(horizon)
    done = dict(
        zip(
            (stamp.date() for stamp in pd.to_datetime(targets["ts"], utc=True)),
            as_of_dates(targets),
            strict=True,
        )
    )
    complete = stored[[*wf.HAR_REGRESSORS, REQUIRED]].notna().all(axis=1).tolist()
    completions = sorted(
        done[day]
        for day, ok in zip(as_of_dates(stored), complete, strict=True)
        if ok and day in done
    )
    if len(completions) < MINIMUM:
        return None
    boundary = completions[MINIMUM - 1]
    return next((origin for origin in origins if origin >= boundary), None)


def refusal(horizon: int, origins: list[dt.date], *, required: str = REQUIRED) -> str | None:
    _, _, targets, stored = real(horizon)
    return wf.coverage_refusal(
        stored, targets, (required,), origins, horizon=horizon, minimum_training=MINIMUM
    )


def first_session(panel: pd.DataFrame, name: str) -> dt.date:
    """The first ``as_of`` of one series in the curated panel, as a ``date``."""
    return min(as_of_dates(panel[panel["symbol"] == name]))


def named(text: str) -> tuple[dt.date, int, int]:
    found = re.search(
        r"first that qualifies is (\d{4}-\d{2}-\d{2}), and (\d+) of the (\d+) requested", text
    )
    assert found is not None, text
    return dt.date.fromisoformat(found.group(1)), int(found.group(2)), int(found.group(3))


def test_the_column_starts_where_its_series_starts_and_the_others_do_not() -> None:
    """``iv_h1`` is VIX1D, which begins long after the series the other horizons read. Read from
    the curated panel and the persisted table, so this holds whatever the dates are."""
    _, panel, _, stored = real(1)
    first = {name: first_session(panel, name) for name in ("VIX1D", "VIX9D", "VIX", "VVIX")}
    assert first["VIX1D"] > max(first["VIX9D"], first["VIX"], first["VVIX"])
    assert first["VIX1D"] > dt.date(2022, 1, 1), "a late series, not a trimmed one"
    stored_days = list(as_of_dates(stored))
    column_start = next(
        day for day, ok in zip(stored_days, stored[REQUIRED].notna(), strict=True) if ok
    )
    assert column_start == first["VIX1D"], "the feature column starts with its series"
    assert stored["iv_h5"].notna().sum() > stored[REQUIRED].notna().sum()


@pytest.mark.parametrize("horizon", HORIZONS)
def test_the_refusal_names_the_origin_the_definition_says_is_first_runnable(horizon: int) -> None:
    """On the real panel, at every horizon: refused, and the named origin is the one a cumulative
    scan from the definition gives -- so it moves with the target lag, ``horizon`` sessions after
    the ``MINIMUM``-th non-null session, and not with the column's first session."""
    origins = origins_for(horizon)
    expected = first_runnable_origin(horizon, origins)
    assert expected is not None, "no origin in the window can run a VIX1D contestant"
    reason = refusal(horizon, origins)
    assert reason is not None, "the window begins years before VIX1D does"
    origin, before, total = named(reason)
    assert origin == expected
    assert total == len(origins) and before == origins.index(expected)
    assert REQUIRED in reason

    # Moving --from to the named origin is accepted, so the remedy the message gives works.
    assert refusal(horizon, origins_for(horizon, first=expected)) is None


@pytest.mark.parametrize("horizon", [1, 22, 66])
def test_a_window_starting_at_the_columns_first_session_is_still_refused(horizon: int) -> None:
    """The hole a first-session rule leaves: the contestant would decline on thin training for a
    year while ``Evaluation.comparable`` intersected those origins out of everyone's loss. The
    boundary sits ``MINIMUM`` rows and the target lag past the start."""
    _, panel, _, _ = real(horizon)
    start = first_session(panel, "VIX1D")
    origins = origins_for(horizon, first=start)
    reason = refusal(horizon, origins)
    assert reason is not None
    origin, before, _ = named(reason)
    assert origin == first_runnable_origin(horizon, origins)
    assert before > 0
    # At least the training sufficiency alone: MINIMUM sessions of the column come first.
    assert (origin - start).days >= MINIMUM, (origin, start)


@pytest.mark.parametrize("horizon", [1, 22])
def test_the_driver_refuses_the_real_window_before_any_fit(horizon: int) -> None:
    """End to end through ``run_configuration`` on the real panel: refused, nothing appended, and
    the contestant is never handed a view -- the refusal precedes ``evaluate``."""
    settings, panel, targets, stored = real(horizon)
    contestant = Spy("needs_vix1d", domain=Domain(requires=(REQUIRED,)))
    written: list[object] = []
    reports: list[str] = []
    outcome = wf.run_configuration(
        settings,
        SYMBOL,
        horizon,
        panel,
        targets,
        calendar=get_calendar(settings),
        first=WINDOW_FIRST,
        last=settings.splits.dev_end,
        every=EVERY,
        minimum_training=MINIMUM,
        cohort=Cohort("vix1d-probe", (Spy("plain"), contestant)),
        persisted=lambda: stored,
        append=lambda trials: written.extend(trials) or len(trials),
        report=reports.append,
    )
    assert outcome.refused and outcome.evaluation is None and outcome.written == 0
    assert written == [] and contestant.views == []
    assert any("REFUSED" in line and REQUIRED in line for line in reports)


def test_a_floor_only_cohort_is_never_refused_on_the_real_window() -> None:
    """The control: a cohort requiring nothing -- every shipped one -- is not the refusal's to
    make, over the same window that refuses the VIX1D contestant."""
    _, _, targets, stored = real(1)
    origins = origins_for(1)
    assert (
        wf.coverage_refusal(stored, targets, (), origins, horizon=1, minimum_training=MINIMUM)
        is None
    )
    assert refusal(1, origins) is not None


# The design's measured figures (docs/superpowers/specs/2026-10-28-contestant-capability-design.md,
# "What a VIX1D contestant would cost every other contestant"), over the shootout window at stride
# 5 with the target lag: horizon -> (scoreable origins, origins lost).
DESIGN_FIGURES = {1: (251, 119), 5: (250, 120), 22: (247, 123), 66: (238, 132)}


@pytest.mark.parametrize("horizon", HORIZONS)
def test_the_designs_measured_figures_still_hold(horizon: int) -> None:
    """**This pins the store, deliberately and only here.** It records that the code reproduces
    the numbers the design was reasoned from, which came from a separate script. When the store is
    rebuilt or the vendor backfills, this is expected to fail and to be *re-measured*, not edited
    to pass; the tests above are the behaviour and do not move."""
    origins = origins_for(horizon)
    reason = refusal(horizon, origins)
    assert reason is not None
    _, lost, total = named(reason)
    assert (total, lost) == DESIGN_FIGURES[horizon]


MATCHED = {1: "iv_var_h1", 5: "iv_var_h5", 22: "iv_var_h22"}


@pytest.mark.parametrize("horizon", [1, 5, 22])
def test_the_horizon_matched_column_refuses_only_at_h1(horizon: int) -> None:
    """The figure spec section 4 reasons from. **A pinned measurement in the sense of R133** (its
    third category of figure): expected to fail and be **re-measured, not edited**, when the
    store is rebuilt. Both branches discriminate: the 119 is exact, and a refusal at h=5 or h=22
    fails the ``reason is None`` branch."""
    origins = origins_for(horizon)
    reason = refusal(horizon, origins, required=MATCHED[horizon])
    if horizon == 1:
        assert reason is not None and named(reason)[1] == 119
    else:
        assert reason is None
