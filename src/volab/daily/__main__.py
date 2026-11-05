"""The daily CLI. ``python -m volab.daily plan [--as-of DATE]`` prints today's plan and runs it not.

**This is where the clock enters the project, and it enters once.** ``_today`` is the only function
anywhere under ``src/volab/daily/`` that asks what the time is, and it does one thing with the
answer: hands it to ``_date_in_exchange_tz``, which is pure. So "a date past ``dev_end`` is
refused" is a statement a test can make about any date rather than a statement about the machine
the suite ran on, and "today means today in New York" is a statement a test can make about a fixed
instant rather than one that only bites during the hours UTC and New York disagree. A derived scan
in ``tests/test_daily.py`` asserts that no other module here reaches a clock at all.

``plan`` writes nothing: no run record, no ledger line, no store read beyond the exchange calendar.
It does *read* the run record, so that a date already completed is reported as such by the verb and
not only by the library. ``run``, which executes the stages and writes the record, arrives with
P4-03 -- the verb list is an ``argparse`` ``choices`` tuple, as every other CLI in this project
uses, so adding it is a one-line change that cannot be mistyped at the command line.

**The exit code of a refusal is 1**, copied from ``volab.walkforward``'s refusal of a ``--to`` past
``dev_end`` rather than invented: an unattended job's wrapper reads exit codes, and a sealed date is
not a successful morning. A closed exchange and an already-completed date are 0 -- those are the job
correctly having nothing to do.

``root`` is injectable for the same reason ``volab.compare``'s ``main`` takes a ``sink``: a test
must be able to exercise the real entry point without writing into, or reading, the repository's own
``data/``. It defaults to the repository root.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from volab.config import load_settings
from volab.daily import plan_for
from volab.daily.runlog import completed_dates, run_log_path

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


def _today() -> dt.date:
    """The one clock call in this package. Everything downstream takes a date as a parameter."""
    return _date_in_exchange_tz(dt.datetime.now(tz=dt.UTC))


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m volab.daily", description=__doc__)
    parser.add_argument("command", choices=("plan",), help="what to run")
    parser.add_argument(
        "--as-of",
        dest="as_of",
        type=dt.date.fromisoformat,
        default=None,
        help="the date to plan for; defaults to today in the exchange's timezone",
    )
    arguments = parser.parse_args(argv)
    as_of = arguments.as_of if arguments.as_of is not None else _today()

    settings = load_settings()
    log = run_log_path(_REPO_ROOT if root is None else root)
    plan = plan_for(settings, as_of, completed=completed_dates(log))
    print(f"as_of {plan.as_of}")
    print(f"outcome {plan.outcome}")
    print(plan.reason)
    for stage in plan.stages:
        print(f"stage {stage.name}: python -m {stage.module} {' '.join(stage.argv)}".rstrip())
    return 1 if plan.outcome == "refused" else 0


if __name__ == "__main__":
    sys.exit(main())
