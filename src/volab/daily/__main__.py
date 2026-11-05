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
nothing at all**: no run record, no directory, not even a call to the clock. For ``work`` it writes
the start line (through ``record_start``, which fsyncs it) **before the first stage starts**, runs
the stages through ``run_stages`` -- each its own interpreter, killed past ``STAGE_TIMEOUT_SECONDS``
-- and then writes exactly one completion line, ``done`` if every stage exited zero and ``failed``
otherwise, carrying each stage's bounded output. If the process is interrupted between the two
writes, the start line stands alone and the date stays retryable (``completed_dates``).

**Exit codes.** 1 for a refusal, copied from ``volab.walkforward``'s refusal of a ``--to`` past
``dev_end`` rather than invented: an unattended job's wrapper reads exit codes, and a sealed date is
not a successful morning. 2 for a run in which a stage failed or timed out, so a wrapper can tell
"the date is sealed" from "the work broke". A closed exchange and an already-completed date are 0 --
those are the job correctly having nothing to do. The verb list is an ``argparse`` ``choices``
tuple, as every other CLI in this project uses.

``root`` is injectable for the same reason ``volab.compare``'s ``main`` takes a ``sink``: a test
must be able to exercise the real entry point without writing into, or reading, the repository's own
``data/``. It defaults to the repository root. The ``git_sha`` and ``git_dirty`` of the start line
are always those of the repository itself, whatever ``root`` is.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from volab.config import load_settings
from volab.daily import Plan, plan_for
from volab.daily.runlog import (
    COMPLETED_OUTCOME,
    completed_dates,
    record_completion,
    record_start,
    run_log_path,
)
from volab.daily.runner import STAGE_TIMEOUT_SECONDS, run_stages
from volab.store import git_state

# What ``run`` exits with when it ran and a stage did not succeed. 1 is the refusal's.
STAGE_FAILED_EXIT_CODE = 2

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


def _execute(plan: Plan, log: Path, clock: Callable[[], dt.datetime]) -> int:
    """Run a ``work`` plan's stages between a start line and a completion line."""
    sha, dirty = git_state(_REPO_ROOT)
    run_id = record_start(log, plan, at=clock(), sha=sha, dirty=dirty)
    runs = run_stages(plan.stages, timeout_seconds=STAGE_TIMEOUT_SECONDS)
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
        return _execute(plan, log, _now if clock is None else clock)
    return 1 if plan.outcome == "refused" else 0


if __name__ == "__main__":
    sys.exit(main())
