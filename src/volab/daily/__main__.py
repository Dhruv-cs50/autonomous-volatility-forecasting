"""The daily CLI. ``python -m volab.daily plan|run [--as-of DATE]``: ``plan`` prints, ``run`` does.

**This is where the clock enters the project, and it enters once.** ``_now`` is the only function
anywhere under ``src/volab/daily/`` that asks what the time is, and it returns an **instant**: the
run record's start and completion lines need a real ``datetime``, and a seam that returned only a
date would leave a run no legal source for one. ``_today`` is ``_date_in_exchange_tz(_now())``
and does not ask the time itself; ``_date_in_exchange_tz`` is pure. So "a date past ``dev_end`` is
refused" is a statement a test can make about any date rather than a statement about the machine
the suite ran on, and "today means today in New York" is a statement a test can make about a fixed
instant rather than one that only bites during the hours UTC and New York disagree. A derived scan
in ``tests/test_daily.py`` asserts that no other module here reaches a clock at all.

``main`` takes a ``clock`` -- a callable returning an instant -- so a test can hand ``run`` known
start and end times. It is called ``clock`` and not ``now`` because the scan above flags a bare name
called ``now``. It defaults to ``_now``.

``plan`` writes nothing: no run record, no ledger line, no store read beyond the exchange calendar.
It does *read* the run record, so that a date already completed is reported as such by the verb and
not only by the library.

``run`` computes the same plan and then does one of two things. For a plan that is not ``work`` --
a refusal, a closed exchange, a date already done -- it prints what ``plan`` prints and **writes
nothing at all**: no run record, no directory, and no call to the clock *from ``run``*. That last
is exact about where the clock is read: with ``--as-of`` the clock is not called at all; without
it -- which is how the shipped plist invokes the job -- ``main`` reads it **once**, before the plan
exists, because "today" is what the plan is for. Either way a non-work plan reads it no further, and
only a ``work`` plan reads it again (the start and completion instants). Pinned by
``tests/test_daily_forecast.py`` with a counting clock. For ``work`` it writes
the start line (through ``record_start``, which fsyncs it) **before the first stage starts**, runs
the stages through ``run_stages`` (each its own interpreter, killed past
``STAGE_TIMEOUT_SECONDS``) -- a stage that declares ``needs_at`` is handed the start line's own
instant as ``--at`` (``with_instant``), so the two share one reading of the clock -- and then writes
exactly one completion line, ``done`` if every stage exited zero and ``failed`` otherwise, carrying
each stage's bounded output. If the process is interrupted between the two writes, the start line
stands alone and the date stays retryable (``completed_dates``).
A Ctrl-C kills the running stage's process group first (``run_stages``), and so does a SIGTERM:
for the length of a ``work`` run ``_sigterm_unwinds`` turns SIGTERM into ``SystemExit``, which
unwinds through ``run_stages`` (that kills the stage's group and reaps the stage) and the lock, and
leaves the process without having written a completion line. Either way the start line stands
alone and the date stays retryable. What that does not cover is in ``volab.daily.runner``: a
``SIGKILL`` to ``run`` runs no handler at all, and a descendant that left the stage's group is not
found.

**One ``run`` at a time, by a lock that refuses and does not wait.** Before the start line, a
``work`` plan takes ``run_lock`` on ``daily_run.lock`` beside the run log (so an injected ``root``
gets its own). A second run finds it held, prints ``outcome locked`` and returns at once having
written nothing -- no start line, so it cannot look like an abandoned run. Once the lock is held
the log is read again: a date another run completed in the meantime prints
``completed_elsewhere`` and runs nothing. A plan that is not ``work`` never touches the lock.
``volab.daily.runner`` states what the lock does and does not cover.

**Exit codes.** 1 for a refusal, copied from ``volab.walkforward``'s refusal of a ``--to`` past
``dev_end`` rather than invented: an unattended job's wrapper reads exit codes, and a sealed date is
not a successful morning. 2 for a run in which a stage failed or timed out, so a wrapper can tell
"the date is sealed" from "the work broke". 3 (``RUN_LOCKED_EXIT_CODE``) when another run holds the
lock: not a sealed date and not broken work, and the locked run has written nothing, so a wrapper
may simply fire again later. It is distinct from ``completed_elsewhere``, which is 0 -- the date is
done. 143 (``TERMINATED_EXIT_CODE``, 128 + SIGTERM as a shell reports it) when a SIGTERM ended a
``work`` run while its handler was installed: the running stage's process group was killed, no
completion line was written, and the date is retryable. The handler is installed *before* the lock
is taken, so there is no stretch in which a SIGTERM arrives with the lock held and no way to
unwind. By construction, and not by a test that sends a signal into that window, a SIGTERM
arriving while the lock is still being tried would unwind through the same handler before any start
line is written. A SIGTERM before the handler is installed (the plan is read and printed first), or
to a ``plan`` or a non-``work`` ``run``, is not handled: the default disposition ends the process,
with no stage started. A closed exchange and an already-completed
date are 0 -- those are the job correctly having nothing to do. The verb list is an ``argparse``
``choices`` tuple, as every other CLI in this project uses.

``root`` is injectable for the same reason ``volab.compare``'s ``main`` takes a ``sink``, and it
moves **the run log only**: the file ``plan`` reads for completed dates and ``run`` writes. It does
**not** reach the stages. ``plan_for`` hands no stage a ``--root``, so a real ``run`` with an
injected ``root`` still has the forecast stage reading the repository's trial ledger and writing the
repository's forecast log, and ``ingest`` and ``features`` take no root at all (they write the
store, which comes from the settings). A test that wants a ``run`` kept out of the repository's
``data/`` must replace ``LIVE_STAGES`` with stages it points at its own directory, as
``tests/test_daily_rehearsal.py`` does; handing ``root`` is not enough, and this is pinned by
``tests/test_daily_forecast.py``. It defaults to the repository root. The ``git_sha`` and
``git_dirty`` of the start line are always those of the repository itself, whatever ``root`` is.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import signal
import sys
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from types import FrameType
from typing import NoReturn
from zoneinfo import ZoneInfo

from volab.config import load_settings
from volab.daily import Plan, plan_for, with_instant
from volab.daily.runlog import (
    COMPLETED_OUTCOME,
    completed_dates,
    record_completion,
    record_start,
    run_log_path,
)
from volab.daily.runner import STAGE_TIMEOUT_SECONDS, LockHeld, run_lock, run_stages
from volab.store import git_state

# What ``run`` exits with when it ran and a stage did not succeed. 1 is the refusal's.
STAGE_FAILED_EXIT_CODE = 2

# What ``run`` exits with when another run holds the lock: not 1 (a sealed date) and not 2 (the
# work broke), because "someone else is already doing it" is neither.
RUN_LOCKED_EXIT_CODE = 3

# What ``run`` exits with when a SIGTERM ended it: 128 + SIGTERM, which is how a shell reports a
# terminated process.
TERMINATED_EXIT_CODE = 128 + signal.SIGTERM

# The exchange's own timezone, because "today" for a job that asks the XNYS calendar whether it is
# a session must be the date in New York, not on the machine. ``volab.sessions`` keeps its copy
# private, and this is the only other place in the project that needs one; if a third appears, that
# is the point at which one of them should become public rather than now.
_EXCHANGE_TZ = ZoneInfo("America/New_York")

# As every other CLI in this package family spells it: ``src/volab/daily/__main__.py`` -> the
# repository root. A measurement behind ``volab.walkforward``'s copy is why the dirtiness question
# is asked about the root and not the package.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _date_in_exchange_tz(instant: dt.datetime) -> dt.date:
    """The exchange's date at ``instant``. Pure, so a test can pin it at a fixed moment."""
    return instant.astimezone(_EXCHANGE_TZ).date()


def _now() -> dt.datetime:
    """The one clock call in this package. Everything downstream takes an instant or a date."""
    return dt.datetime.now(tz=dt.UTC)


def _today() -> dt.date:
    """Today's date in the exchange's timezone, from the seam and not from a clock of its own."""
    return _date_in_exchange_tz(_now())


def _lock_path(log: Path) -> Path:
    """Beside the run log, so an injected ``root`` gets its own lock and ``data/**`` ignores it."""
    return log.with_name("daily_run.lock")


@contextlib.contextmanager
def _sigterm_unwinds() -> Iterator[None]:
    """For the ``with`` body, make a SIGTERM raise ``SystemExit(TERMINATED_EXIT_CODE)``.

    Python's default SIGTERM disposition ends the process without unwinding, so the runner's
    cleanup of a running stage never runs. ``SystemExit`` is a ``BaseException``, so it takes the
    same path a ``KeyboardInterrupt`` does. The handler then ignores further SIGTERMs, so a second
    one cannot interrupt the unwinding the first started. The previous disposition is restored on
    the way out. A ``SIGKILL`` cannot be caught and is not covered.

    ``signal.signal`` works only on the main thread and raises ``ValueError`` elsewhere. That is
    deliberately not caught: ``run`` is a process entry point and is on the main thread, and a
    caller that invokes ``main`` from a thread should get the error and not a run that is silently
    unprotected.
    """

    def terminate(number: int, frame: FrameType | None) -> NoReturn:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise SystemExit(TERMINATED_EXIT_CODE)

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _execute(plan: Plan, log: Path, clock: Callable[[], dt.datetime]) -> int:
    """Run a ``work`` plan's stages between a start line and a completion line."""
    sha, dirty = git_state(_REPO_ROOT)
    started = clock()
    run_id = record_start(log, plan, at=started, sha=sha, dirty=dirty)
    # The same instant the start line carries, handed to every stage that asks for it: one clock
    # call for both, so what a stage records and what the run log records cannot disagree.
    stages = tuple(with_instant(stage, started) for stage in plan.stages)
    runs = run_stages(stages, timeout_seconds=STAGE_TIMEOUT_SECONDS)
    succeeded = len(runs) == len(plan.stages) and all(run.exit_code == 0 for run in runs)
    record_completion(
        log,
        run_id,
        outcome=COMPLETED_OUTCOME if succeeded else "failed",
        stages=tuple(run.as_result() for run in runs),
        at=clock(),
    )
    for run in runs:
        verdict = "timed out" if run.timed_out else f"exit {run.exit_code}"
        print(f"ran {run.name}: {verdict}")
    return 0 if succeeded else STAGE_FAILED_EXIT_CODE


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path | None = None,
    clock: Callable[[], dt.datetime] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m volab.daily", description=__doc__)
    parser.add_argument("command", choices=("plan", "run"), help="what to do")
    parser.add_argument(
        "--as-of",
        dest="as_of",
        type=dt.date.fromisoformat,
        default=None,
        help="the date to plan for; defaults to today in the exchange's timezone",
    )
    arguments = parser.parse_args(argv)
    if arguments.as_of is not None:
        as_of = arguments.as_of
    elif clock is not None:
        as_of = _date_in_exchange_tz(clock())
    else:
        as_of = _today()

    settings = load_settings()
    log = run_log_path(_REPO_ROOT if root is None else root)
    plan = plan_for(settings, as_of, completed=completed_dates(log))
    print(f"as_of {plan.as_of}")
    print(f"outcome {plan.outcome}")
    print(plan.reason)
    for stage in plan.stages:
        print(f"stage {stage.name}: python -m {stage.module} {' '.join(stage.argv)}".rstrip())
    if arguments.command == "run" and plan.outcome == "work":
        try:
            with _sigterm_unwinds(), run_lock(_lock_path(log)):
                if plan.as_of in completed_dates(log):
                    print("outcome completed_elsewhere")
                    print(
                        "another run completed this date after this one read the run log "
                        "and before it took the lock"
                    )
                    return 0
                return _execute(plan, log, _now if clock is None else clock)
        except LockHeld:
            print("outcome locked")
            print(f"another daily run holds {_lock_path(log)}; this run has written nothing")
            return RUN_LOCKED_EXIT_CODE
    return 1 if plan.outcome == "refused" else 0


if __name__ == "__main__":
    sys.exit(main())
