"""``python -m volab.compare run`` -- the shootout: every contestant, compared and counted.

It **calls** :func:`volab.walkforward.run_configuration` and does not reimplement it (R97), so the
audit refusal (R63), the append-per-configuration (R74), the empty-configuration count and the
truncation-equivalence report are the walk-forward CLI's own, inherited by delegation. The two
rulings R97 left in ``walkforward/__main__.py`` are kept here, in the same words:

* **R62** -- there is no ``--end`` and no unlock flag, and ``--to`` past ``dev_end`` is refused
  loudly rather than clamped. Every read stops at ``settings.splits.dev_end``.
* **R70** -- the git sha and dirty flag are captured **once, at the start**, scoped to the
  repository root, and arrive on every line of both files.

After each configuration it builds one :class:`Comparison` per loss from the evaluation the driver
returned, and hands them to the sink under the run's one timestamp -- the same ``(sha, at)`` its
trials carry, so ``data/trials.jsonl`` and ``data/comparisons.jsonl`` join without guessing (R95).
It appends per configuration, so a later refusal cannot erase what an earlier one counted, and
**it has no flush on the refusal path**: R99 removed the one that double-counted.

**The stride defaults to 5**, where the walk-forward CLI defaults to 1. The overlap order, the
effective sample and the table in R94 were all derived at stride 5, and a shootout at stride 1
would cost five times as long for the same ``n_eff``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from collections.abc import Sequence
from functools import partial
from pathlib import Path

import pandas as pd

from volab.compare import analyse, describe
from volab.compare.cohorts import (
    COHORTS,
    MONTE_CARLO_ERROR_SOURCE,
    SHOOTOUT,
    monte_carlo_error,
)
from volab.compare.series import loss_series
from volab.compare.sink import Comparison, JsonlSink, Sink
from volab.config import Settings, load_settings
from volab.errors import DatasetNotFound
from volab.features.targets import HORIZONS
from volab.paths import Tier
from volab.sessions import get_calendar, sessions_between
from volab.store import load_panel
from volab.walkforward import (
    LOSSES,
    Evaluation,
    count_trials,
    git_state,
    load_for,
    run_configuration,
)

FEATURE_SET = "har_daily"

# The repository root, which is the scope the dirty flag must ask about (R70): the default scope is
# the package, and an edit to ``config/base.yaml`` -- which carries ``dev_end`` -- then reads clean.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _persisted(settings: Settings, symbol: str) -> pd.DataFrame | None:
    """The feature table as the CLI wrote it, or ``None`` if the store holds none of it.

    The walk-forward CLI's own function, restated rather than imported: it is private to a
    ``__main__`` module, and the read it makes is bounded by ``dev_end`` exactly as it is there.
    """
    try:
        frame = load_panel(
            FEATURE_SET,
            end=settings.splits.dev_end,
            symbols=[symbol],
            tier=Tier.FEATURES,
            settings=settings,
        )
    except DatasetNotFound:
        return None
    return frame.sort_values("as_of").reset_index(drop=True)


def _report(errors: dict[str, float | None], line: str) -> None:
    """``print``, with the measured Monte Carlo error appended to a simulated contestant's line.

    ``run_configuration`` prints one line per trial that begins with the contestant's name
    (``evaluation.trials()``), and R102 puts the error beside every simulated figure.
    """
    words = line.split()
    if words and words[0] in errors:
        value = errors[words[0]]
        shown = "not measured" if value is None else f"{value:.3f}%"
        line += f"  [MC error {shown} ({MONTE_CARLO_ERROR_SOURCE})]"
    print(line)


def _run(settings: Settings, arguments: argparse.Namespace, sink: Sink) -> int:
    started = time.perf_counter()
    cohort = COHORTS[arguments.cohort]
    # Captured now, before a single number is produced (R70).
    sha, dirty = git_state(_REPO_ROOT)
    if dirty:
        print(f"git {sha[:12]} with a DIRTY tree: these trials are not reproducible from it")
    elif dirty is None:
        print(f"git {sha}: the tree's state is UNKNOWN, so these trials cannot be located")
    else:
        print(f"git {sha[:12]}, clean")
    calendar = get_calendar(settings)
    sessions = sessions_between(calendar, arguments.start, arguments.stop)
    if not sessions:
        print(f"no {calendar.name} sessions between {arguments.start} and {arguments.stop}")
        return 1

    # One timestamp for the run, stamped at the start: it is half the join key.
    started_at = dt.datetime.now(dt.UTC)
    trials_written = 0
    comparisons_written = 0
    dm_tests = 0
    failures = 0
    print(f"cohort {cohort.name}: {', '.join(cohort.contestants)}")

    for symbol in arguments.symbols:
        for horizon in arguments.horizons:
            print(f"{symbol} h{horizon}:")
            panel, targets = load_for(settings, symbol, horizon, report=print)
            errors = monte_carlo_error(cohort, horizon)
            outcome = run_configuration(
                settings,
                symbol,
                horizon,
                panel,
                targets,
                calendar=calendar,
                first=sessions[0],
                last=sessions[-1],
                every=arguments.every,
                minimum_training=arguments.minimum_training,
                cohort=cohort,
                persisted=partial(_persisted, settings, symbol),
                append=lambda trials: sink.append_trials(
                    trials, at=started_at, sha=sha, dirty=dirty
                ),
                # The driver prints each trial's mean; a simulated contestant's carries its error.
                report=partial(_report, errors),
            )
            trials_written += outcome.written
            if outcome.refused:
                # Nothing to flush (R99): earlier configurations appended as they finished.
                return 2
            failures += outcome.disagrees

            evaluation = outcome.evaluation or Evaluation(
                symbol=symbol,
                horizon=horizon,
                results=(),
                features=pd.DataFrame(),
                skipped=(),
                requested=0,
                every=arguments.every,
                contestants=cohort.contestants,
                cohort=cohort.name,
            )
            comparisons: list[Comparison] = [
                analyse(
                    loss_series(evaluation, loss, cohort=cohort.name),
                    monte_carlo_error=errors,
                    monte_carlo_source=MONTE_CARLO_ERROR_SOURCE,
                )
                for loss in LOSSES
            ]
            comparisons_written += sink.append_comparisons(
                comparisons, at=started_at, sha=sha, dirty=dirty
            )
            # The ledger's count, cumulative and not this cohort's, printed beside the ranking.
            ledger = count_trials(sink.root) if isinstance(sink, JsonlSink) else None
            for comparison in comparisons:
                dm_tests += comparison.dm_tests
                for line in describe(comparison, trials=ledger):
                    print(line)

    print(
        f"{trials_written} trial line(s) and {comparisons_written} comparison record(s) appended, "
        f"{dm_tests} pairwise DM test(s) run"
    )
    if isinstance(sink, JsonlSink):
        print(f"ledger now {count_trials(sink.root)} trial line(s)")
    print(f"ran in {time.perf_counter() - started:.1f}s")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None, *, sink: Sink | None = None) -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(prog="python -m volab.compare", description=__doc__)
    parser.add_argument("command", choices=("run",), help="what to run")
    parser.add_argument(
        "--cohort",
        choices=list(COHORTS),
        default=SHOOTOUT.name,
        help=f"the contestants to compare (default {SHOOTOUT.name})",
    )
    parser.add_argument(
        "--symbol",
        dest="symbols",
        action="append",
        choices=list(settings.universe),
        help="repeatable; defaults to the whole configured universe",
    )
    parser.add_argument(
        "--horizon",
        dest="horizons",
        action="append",
        type=int,
        choices=list(HORIZONS),
        help="repeatable; defaults to every configured horizon",
    )
    parser.add_argument(
        "--from",
        dest="start",
        type=dt.date.fromisoformat,
        default=dt.date(2021, 1, 4),
        help="first forecast origin (default 2021-01-04, leaving four years of training)",
    )
    parser.add_argument(
        "--to",
        dest="stop",
        type=dt.date.fromisoformat,
        default=settings.splits.dev_end,
        help="last forecast origin; never read past dev_end whatever is passed",
    )
    parser.add_argument(
        "--every",
        type=int,
        default=5,
        help="use every Nth session as an origin (default 5, the stride R94 was derived at)",
    )
    parser.add_argument(
        "--minimum-training",
        type=int,
        default=250,
        help="refuse to fit an origin with fewer usable training rows (default 250)",
    )
    arguments = parser.parse_args(argv)
    arguments.symbols = arguments.symbols or list(settings.universe)
    arguments.horizons = arguments.horizons or list(HORIZONS)

    # The seal, enforced rather than documented (R62).
    if arguments.stop > settings.splits.dev_end:
        print(
            f"--to {arguments.stop} is past dev_end {settings.splits.dev_end}; "
            "the holdout is sealed and this entry point has no key"
        )
        return 1

    print(f"storage root {settings.storage_root}")
    print(f"reads stop at dev_end {settings.splits.dev_end}")
    return _run(settings, arguments, sink or JsonlSink(settings.storage_root.parent))


if __name__ == "__main__":
    sys.exit(main())
