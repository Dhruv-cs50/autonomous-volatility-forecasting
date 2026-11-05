"""The daily plan: what an unattended run would do on a given date, computed as data.

**The plan is computed before anything runs, and printing it runs nothing.** That is the whole
shape of this module: an operator inspecting a job they cannot watch needs to see its decision
without causing it, and a test needs to assert a refusal without executing a stage.
``python -m volab.daily plan`` prints what ``plan_for`` returned and stops. ``run`` computes the
same plan and then, for a ``work`` plan only, executes its stages (P4-03 built the stage runner,
P4-03b the verb); for any other outcome it prints and writes nothing.

Every refusal below is decided *inside* the plan rather than at the point of execution, so the
verb that only prints and the verb that executes cannot diverge in what they refuse. There is
deliberately **no flag anywhere here that turns a guard off** -- not a ``--force``, not a
``--no-check``; which of ``plan`` and ``run`` is chosen selects whether the stages execute and
never whether the guards do. A flag that could disable a guard is the holdout-reveal
design's anti-goal #1 wearing a different hat.

**``as_of`` is a parameter, never the clock.** The README's invariant is that nothing in this
project computes a boundary from the clock, and a scheduler is the first module here that has a
clock at all. It enters at exactly one seam -- ``_now``, which returns an instant, in
``volab.daily.__main__``; ``_today`` is a pure function of it -- and everything downstream,
including every refusal, is a pure function of the date it is handed. That
is what makes "today is past ``dev_end``, so refuse" a testable statement rather than a statement
about the machine the suite happens to run on.

**The three ledger writers are deliberately absent from ``LIVE_STAGES``.** ``volab.walkforward``
and ``volab.compare`` append to ``data/trials.jsonl`` and ``data/comparisons.jsonl``, and
``volab.evaluators`` appends to both ``data/decisions.jsonl`` and the trial ledger
(``volab.evaluators.sink``). The first two are **reported numbers** whose delta the holdout reveal
reports (R139), so an unattended job that appended to them daily would move, every morning and
with nobody watching, the one count this project's honesty argument rests on. A daily run's own
record of what it forecast belongs in a separate append-only log -- the holdout-reveal design's
protection 3 -- which is ``volab.daily.forecast``, the last stage.
``tests/test_daily.py`` derives the set of ledger-writing modules from the source tree
rather than listing it (R151) and asserts no planned stage names one.

**A plan's stages are the ones that will be run, dated.** ``LIVE_STAGES`` is the undated template;
``plan_for`` appends ``--as-of DATE`` to each stage declared ``needs_as_of`` (today the forecast
stage), reading ``LIVE_STAGES`` when it is called. The date reaches the stage as an argument,
because a stage that had to find out would have to reach a clock.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Literal

from volab.config import Settings
from volab.sessions import get_calendar, is_session

Outcome = Literal["work", "refused", "no_session", "already_done"]


@dataclass(frozen=True)
class Stage:
    """One step of a daily run: the module to run and the arguments to run it with.

    ``module`` is a module name rather than a callable deliberately. P4-03 runs each stage in a
    fresh interpreter, because chaining the pipeline in one process reintroduces the cross-call
    state that ``tests/test_process_isolation.py`` exists to catch -- a memoised full-sample
    statistic filled by one build and reused by the next is invisible to truncation equivalence
    within a process (the README's E2). Holding a module name rather than an imported function
    keeps that possible and keeps this module importable without importing the pipeline.
    """

    name: str
    module: str
    argv: tuple[str, ...]
    # True for a stage that must be told which date it is for. ``plan_for`` appends
    # ``--as-of DATE`` to such a stage's argv, so the template in ``LIVE_STAGES`` carries no date
    # and a stage in a ``Plan`` always does; a planned stage has this cleared.
    needs_as_of: bool = False


@dataclass(frozen=True)
class Plan:
    """What a run on ``as_of`` would do, and why.

    ``outcome`` is one of four, and ``reason`` always says which in words an operator reading a
    log will understand:

    - ``work`` -- there is work to do, and ``stages`` is it.
    - ``refused`` -- the date is past ``dev_end``. ``stages`` is empty.
    - ``no_session`` -- the exchange was closed. ``stages`` is empty.
    - ``already_done`` -- a completed run for this date exists. ``stages`` is empty.
    """

    as_of: dt.date
    outcome: Outcome
    reason: str
    stages: tuple[Stage, ...]


# The pipeline order, which is the order the README documents. The three ledger writers are
# absent by decision (R158 Q2) and the module docstring says why. The forecast stage is last and
# writes only its own log; today it derives the labelled pseudo-champions and refuses (see
# ``volab.daily.forecast``), which fails the run rather than recording a day nothing was forecast
# on. This is the **template**: no stage here knows a date. ``plan_for`` is what dates the ones
# that need it, at call time, so a plan's stages are the ones that will actually be run.
LIVE_STAGES: tuple[Stage, ...] = (
    Stage(name="ingest", module="volab.ingest", argv=("backfill",)),
    Stage(name="features", module="volab.features", argv=("build",)),
    Stage(name="leaderboard", module="volab.leaderboard", argv=()),
    Stage(name="forecast", module="volab.daily.forecast", argv=(), needs_as_of=True),
)


def _dated(stage: Stage, as_of: dt.date) -> Stage:
    """``stage`` as it will be run on ``as_of``: the date in its argv if it needs one.

    The date is an argument all the way from the one clock seam, and a stage that needed it and
    did not get it would have to reach a clock to find out.
    """
    if not stage.needs_as_of:
        return stage
    return replace(stage, argv=(*stage.argv, "--as-of", as_of.isoformat()), needs_as_of=False)


def plan_for(
    settings: Settings,
    as_of: dt.date,
    *,
    completed: frozenset[dt.date] = frozenset(),
) -> Plan:
    """The plan for ``as_of``, computed without running or reading anything but the calendar.

    The order of the three refusals is load-bearing and is pinned by its own test. **The
    ``dev_end`` refusal comes first**, before the calendar is even asked, because a date past the
    boundary must be refused for that reason and not incidentally for being a Saturday: an
    operator who reads "the exchange was closed" where the truth is "this date is sealed" has been
    told the wrong thing, and the day that date falls on a session the refusal would have to come
    from somewhere else.

    There is no key here and no route to one. Reading past ``dev_end`` needs ``allow_holdout`` and
    ``VOLAB_HOLDOUT_UNLOCK`` together, and neither name appears anywhere under
    ``src/volab/daily/`` -- asserted by a derived scan in ``tests/test_daily.py``, because
    ``docs/phase2-completion-sprint-board.md:225`` predicted exactly this failure: "an agent asked
    to make dailies work is exactly who would reach for ``allow_holdout``". Until the holdout
    reveal has happened and a ``live`` region exists (P4-05), **every real date is past
    ``dev_end``**, so this refusal is the normal outcome rather than an edge case.
    """
    if as_of > settings.splits.dev_end:
        return Plan(
            as_of=as_of,
            outcome="refused",
            reason=(
                f"{as_of} is past dev_end {settings.splits.dev_end}; the holdout is sealed and "
                "this entry point has no key"
            ),
            stages=(),
        )
    if not is_session(get_calendar(settings), as_of):
        return Plan(
            as_of=as_of,
            outcome="no_session",
            reason=f"{as_of} is not a session on the {settings.calendar} calendar",
            stages=(),
        )
    if as_of in completed:
        return Plan(
            as_of=as_of,
            outcome="already_done",
            reason=f"a completed run for {as_of} is already in the run log",
            stages=(),
        )
    return Plan(
        as_of=as_of,
        outcome="work",
        reason=f"{as_of} is a session and has no completed run",
        stages=tuple(_dated(stage, as_of) for stage in LIVE_STAGES),
    )
