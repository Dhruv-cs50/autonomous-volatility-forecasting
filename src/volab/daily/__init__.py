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

**A plan's stages are the ones that will be run, dated -- and the run adds the instant.**
``LIVE_STAGES`` is the undated template; ``plan_for`` appends ``--as-of DATE`` to each stage
declared ``needs_as_of`` (today the forecast stage), reading ``LIVE_STAGES`` when it is called.
The date reaches the stage as an argument, because a stage that had to find out would have to
reach a clock. A stage declared ``needs_at`` is also given the run's instant, but not by the plan:
the plan is a function of a date and holds no instant (printing it must not ask the clock), so the
``run`` verb appends ``--at INSTANT`` with ``with_instant`` once it has taken the instant for the
start line. The stage as printed -- by ``plan``, and by ``run`` before it executes anything --
therefore lacks ``--at``, and the one that runs has it; both are derived from the same ``Stage``.

**The plan hands a stage a date and an instant, and never a root.** Where a stage writes is
its own configuration's business or its own argument's: ``volab.ingest`` and
``volab.features`` write the store at ``settings.storage_root`` and accept no root at all,
while ``volab.daily.forecast`` and ``volab.leaderboard`` each take a ``--root`` that defaults
to this repository. So the ``root`` ``volab.daily.__main__`` accepts moves the run log alone,
and no rehearsal can be had by handing it one -- which that module's docstring states and
``tests/test_daily_forecast.py`` executes. Nor can the two store-writing stages be aimed
elsewhere by configuration: ``volab.config._validate_storage_root`` resolves ``storage.root``
against the repository root and refuses anything not strictly inside it, and the root it
resolves against is derived from the installed package, so neither ``load_settings(path=...)``
nor ``VOLAB_STORAGE_ROOT`` can name a temporary directory (``tests/conftest.py`` builds its
``Settings`` by hand for exactly that reason, and ``tests/test_config.py`` pins the
refusal by both routes). What follows for a rehearsal -- which of these stages it can really
run and which it must stand in for -- is stated in ``tests/test_daily_rehearsal.py``, not
claimed here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Literal

from volab.config import Region, Settings, Splits
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
    # True for a stage that must be told when the run happened. A second flag and not a
    # generalisation of the first, because the two are known at different moments: the date is a
    # parameter of the plan and is in a ``Plan``'s stages, while the instant is read from the clock
    # seam only once ``run`` knows there is work, so a plan -- which is data, computed without the
    # clock -- cannot hold it. ``with_instant`` appends ``--at INSTANT`` and clears this; until
    # then it stays set on a ``Plan``'s stage, which is how the stage says it is still waiting.
    needs_at: bool = False


@dataclass(frozen=True)
class Plan:
    """What a run on ``as_of`` would do, and why.

    ``outcome`` is one of four, and ``reason`` always says which in words an operator reading a
    log will understand:

    - ``work`` -- there is work to do, and ``stages`` is it.
    - ``refused`` -- the date is past ``dev_end``, in the holdout or the live region; the reason
      says which. ``stages`` is empty.
    - ``no_session`` -- the exchange was closed. ``stages`` is empty.
    - ``already_done`` -- a completed run for this date exists. ``stages`` is empty.
    """

    as_of: dt.date
    outcome: Outcome
    reason: str
    stages: tuple[Stage, ...]


# The pipeline order, which is the order the README documents. The three ledger writers are
# absent by decision (R158 Q2) and the module docstring says why. The forecast stage is last and
# writes only its own log: it fits the labelled pseudo-champion of each configuration and appends
# one line per configuration (see ``volab.daily.forecast``), and exits non-zero if any
# configuration was refused, so a partial day fails the run rather than being recorded as done.
# This is the **template**: no stage here knows a date or an instant. ``plan_for`` dates the ones
# that need it, at call time; ``with_instant`` stamps the ones that need the run's instant.
LIVE_STAGES: tuple[Stage, ...] = (
    Stage(name="ingest", module="volab.ingest", argv=("backfill",)),
    Stage(name="features", module="volab.features", argv=("build",)),
    Stage(name="leaderboard", module="volab.leaderboard", argv=()),
    Stage(
        name="forecast",
        module="volab.daily.forecast",
        argv=(),
        needs_as_of=True,
        needs_at=True,
    ),
)


def _dated(stage: Stage, as_of: dt.date) -> Stage:
    """``stage`` as it will be run on ``as_of``: the date in its argv if it needs one.

    The date is an argument all the way from the one clock seam, and a stage that needed it and
    did not get it would have to reach a clock to find out.
    """
    if not stage.needs_as_of:
        return stage
    return replace(stage, argv=(*stage.argv, "--as-of", as_of.isoformat()), needs_as_of=False)


def _refusal_reason(splits: Splits, as_of: dt.date) -> str:
    """Why a date past ``dev_end`` is refused, naming the region it is actually in.

    The refusal itself is decided by ``dev_end`` alone, as the read guard is; the region only
    chooses which true thing to say. (The split's end is not interpolated: nothing under this
    package may name an attribute with ``holdout`` in it, ``tests/test_daily.py`` scans for that.)
    ``Splits.region_of`` is total, so a date in the seam between
    ``dev_end`` and ``holdout_start`` is called holdout, as the guard treats it.
    """
    if splits.region_of(as_of) is Region.LIVE:
        where = (
            "it is in the live region, after the holdout window, which this entry point cannot "
            "read and has no key for"
        )
    else:
        where = "the holdout is sealed and this entry point has no key"
    return f"{as_of} is past dev_end {splits.dev_end}; {where}"


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
    to make dailies work is exactly who would reach for ``allow_holdout``". **Every
    real date is past ``dev_end``** -- in the holdout region while ``holdout_end`` lies ahead, in
    the live region once it has passed (``Splits.region_of`` says which) -- so this refusal is the
    normal outcome rather than an edge case. Its reason names the region the date is in: a live
    date is not described as sealed holdout, though both are refused alike, with the same exit code.
    """
    if as_of > settings.splits.dev_end:
        return Plan(
            as_of=as_of,
            outcome="refused",
            reason=_refusal_reason(settings.splits, as_of),
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


def with_instant(stage: Stage, at: dt.datetime) -> Stage:
    """``stage`` as it will be run at ``at``: the instant in its argv if it needs one.

    ``at`` is the instant the ``run`` verb took from the clock seam for the start line, so a stage
    that records it (the forecast log's ``at``) records the run's own start and a reader can join
    the two. Like the date, it travels as an argument: a stage that had to find it out would have
    to reach a clock, and exactly one function under ``volab.daily`` may. It must carry a UTC
    offset, because a naive instant would be written to a log nothing could place on a timeline.
    """
    if not stage.needs_at:
        return stage
    if at.tzinfo is None:
        raise ValueError(f"the instant must carry a UTC offset, got {at.isoformat()}")
    return replace(stage, argv=(*stage.argv, "--at", at.isoformat()), needs_at=False)
