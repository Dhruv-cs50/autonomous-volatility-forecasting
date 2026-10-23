"""``python -m volab.walkforward run`` -- refit and forecast at each origin, and count the trials.

One verb. It reads the curated panel and one horizon's targets, both to ``dev_end``, rebuilds the
feature table at every forecast origin from a panel truncated at that origin, fits HAR in
levels, HAR semi-log and the random-walk benchmark, scores QLIKE and MSE, and appends one
ledger line per evaluated configuration.

**There is no ``--end`` and no unlock flag.** Every read stops at ``settings.splits.dev_end``.
Phase 6 is the only confirmatory read of the holdout, with both keys and an audit line; an
argument here accepting a later date would quietly make this the thing that opened it.

**It also prints a truncation-equivalence result**, because it gets it for free: each feature row
it builds comes from a panel stopping at that row's own session, and the persisted ``har_daily``
table was built once from the whole panel, so comparing them byte-exactly checks every origin
**the run visited**.

That last phrase is load-bearing and an earlier version of this docstring got it wrong. "Every
origin in the window" is true only at ``--every 1``; with a stride of 5 the run checks every
fifth session and says nothing about the rest, and the recorded run used 5. The dev-window-wide
result is ``VOLAB_PIT_EXHAUSTIVE=1`` on ``tests/test_truncation_equivalence.py``, which is where
the 0-of-2,245 number comes from. Claiming the broader coverage here would be the same
adjacent-truth shape as Phase 1's C1: the sentence would be true of the code and false of every
run anyone had made.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from volab.config import Settings, load_settings
from volab.errors import DatasetNotFound
from volab.features.targets import HORIZONS
from volab.identity import first_difference
from volab.panel import as_of_dates
from volab.paths import Tier
from volab.sessions import get_calendar, sessions_between
from volab.store import load_panel
from volab.walkforward import (
    LOSSES,
    MODELS,
    Evaluation,
    RunAudit,
    Trial,
    append_trials,
    count_trials,
    evaluate,
    git_state,
    load_for,
    scoreable_origins,
)

FEATURE_SET = "har_daily"

# The repository root, which is the scope the dirty flag must ask about: the default scope of
# ``store._git_state`` is the package, and a measurement showed that an edit to
# ``config/base.yaml`` -- which carries ``splits.dev_end`` and the universe -- then reads as
# clean. A provenance line that cannot see the configuration is not provenance.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _persisted(settings: Settings, symbol: str) -> pd.DataFrame | None:
    """The feature table as the CLI wrote it, or ``None`` if the store holds none of it."""
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


def _compare_against_persisted(
    built: pd.DataFrame, stored: pd.DataFrame | None
) -> tuple[int, str | None]:
    """Byte-compare the per-origin rows against the persisted table, on the origins built.

    Returns the number of origins compared and the first disagreement, if any. The comparison
    normalises the store's two documented round-trip asymmetries and nothing else: ``as_of``
    (written as ``date``, read back as ``datetime64[us]``) and ``has_distribution`` (written as
    nullable ``boolean``, read back as ``bool`` because no null ever reached disk). The second
    cast runs the narrow way, ``bool`` up to ``boolean``, so a null appearing in a fresh build is
    still a difference rather than being normalised away.
    """
    if stored is None or built.empty:
        return 0, None
    wanted = set(as_of_dates(built))
    rows = stored[[day in wanted for day in as_of_dates(stored)]].reset_index(drop=True)
    if len(rows) != len(built):
        return len(built), f"the store holds {len(rows)} of the {len(built)} origins built"

    left = built.assign(
        as_of=list(as_of_dates(built)),
        has_distribution=built["has_distribution"].astype("boolean"),
    )
    right = rows[list(built.columns)].assign(
        as_of=list(as_of_dates(rows)),
        has_distribution=rows["has_distribution"].astype("boolean"),
    )
    return len(built), first_difference(left, right)


def _flush(trials: Sequence[Trial], settings: Settings, sha: str, dirty: bool | None) -> None:
    """Last-resort append on the refusal path, so a refusal cannot erase counted trials."""
    if trials:
        append_trials(
            trials,
            root=settings.storage_root.parent,
            at=dt.datetime.now(dt.UTC),
            sha=sha,
            dirty=dirty,
        )


def _run(settings: Settings, arguments: argparse.Namespace) -> int:
    started = time.perf_counter()
    # Captured now, before a single number is produced. Reading it when the ledger is
    # written would name the tree as it is at the end of a run that may take minutes, and
    # the first headline run proved that is not pedantry: it began at one commit and the
    # working tree was committed while it ran, so its lines named a tree it had never
    # executed. The dirty flag goes on every line for the same reason -- a sha against a
    # modified tree does not identify the code that ran.
    sha, dirty = git_state(_REPO_ROOT)
    if dirty:
        print(f"git {sha[:12]} with a DIRTY tree: these trials are not reproducible from it")
    elif dirty is None:
        # ``None`` is "git could not be reached", which is not "clean". Printing clean here was a
        # false reassurance on the console while the ledger line correctly recorded null.
        print(f"git {sha}: the tree's state is UNKNOWN, so these trials cannot be located")
    else:
        print(f"git {sha[:12]}, clean")
    calendar = get_calendar(settings)
    sessions = sessions_between(calendar, arguments.start, arguments.stop)
    if not sessions:
        print(f"no {calendar.name} sessions between {arguments.start} and {arguments.stop}")
        return 1

    before = count_trials(settings.storage_root.parent)
    # Stamped at the start, with the sha, so every line of one run shares one timestamp and that
    # timestamp is when the numbers were produced rather than when they were written.
    started_at = dt.datetime.now(dt.UTC)
    everything: list[Trial] = []
    written = 0
    failures = 0

    for symbol in arguments.symbols:
        for horizon in arguments.horizons:
            print(f"{symbol} h{horizon}:")
            panel, targets = load_for(settings, symbol, horizon, report=print)
            origins = scoreable_origins(
                settings, targets, first=sessions[0], last=sessions[-1], every=arguments.every
            )
            if not origins:
                # Counted, not skipped. A configuration that was run and produced nothing is
                # exactly the result selective reporting omits.
                print("  no origin in the window has a target completed at or before dev_end")
                empty = Evaluation(
                    symbol=symbol,
                    horizon=horizon,
                    results=(),
                    features=pd.DataFrame(),
                    skipped=(),
                    requested=0,
                    every=arguments.every,
                )
                written += append_trials(
                    empty.trials(),
                    root=settings.storage_root.parent,
                    at=started_at,
                    sha=sha,
                    dirty=dirty,
                )
                continue
            print(f"  {len(origins)} origin(s) from {origins[0]} to {origins[-1]}")

            audit = RunAudit()
            evaluation = evaluate(
                panel,
                targets,
                origins,
                symbol=symbol,
                horizon=horizon,
                calendar=calendar,
                minimum_training=arguments.minimum_training,
                every=arguments.every,
                audit=audit,
            )
            violations = audit.violations()
            if violations:
                # Fail loudly. Reading past a cutoff is the one defect this whole module exists to
                # prevent, and reporting it as a warning would be the posture this project
                # refuses. Trials already produced are flushed first, below, so a refusal cannot
                # erase numbers that were printed.
                kind, at, last = violations[0]
                print(f"  REFUSED: {len(violations)} call(s) read past their cutoff")
                print(f"           first: {kind} at={at} max as_of={last}")
                _flush(everything, settings, sha, dirty)
                return 2
            print(
                f"  {len(audit.calls)} build(s) and {len(audit.training)} training set(s), "
                "every one stopping at its own origin"
            )

            compared, difference = _compare_against_persisted(
                evaluation.features, _persisted(settings, symbol)
            )
            if compared == 0:
                # Nothing to compare is not agreement. Printing "0 of 0 identical" and exiting 0
                # would be a check that passes by checking nothing, which is this project's
                # named anti-goal; it happens whenever the store holds no persisted table, so it
                # is a reachable state and not a defensive branch.
                print("  truncation equivalence: NOT COMPARED, the store holds no built table")
            elif difference is None:
                print(f"  truncation equivalence: {compared} of {compared} origins identical")
            else:
                failures += 1
                print(f"  truncation equivalence: DISAGREES at {compared} origins: {difference}")

            if evaluation.skipped:
                print(
                    f"  {len(evaluation.skipped)} origin(s) skipped, first: "
                    f"{evaluation.skipped[0][0]} {evaluation.skipped[0][1]}"
                )
            trials = evaluation.trials()
            for trial in trials:
                print(
                    f"  {trial.model:12s} {trial.loss:5s} {trial.value:+.6g} "
                    f"over {trial.origins} origin(s), {trial.dropped} dropped"
                )
            # Appended **now**, per configuration, not once at the end. A run that printed
            # SPY's results and then tripped the refusal branch on QQQ, raised, or was
            # interrupted used to reach no ledger at all -- a number seen but not counted, which
            # is the forking-paths hole this ledger exists to close.
            written += append_trials(
                trials,
                root=settings.storage_root.parent,
                at=started_at,
                sha=sha,
                dirty=dirty,
            )
            everything.extend(trials)

    after = count_trials(settings.storage_root.parent)
    print(f"trials: {written} appended, ledger now {after} line(s), was {before}")
    print(f"models counted: {len(MODELS)}, losses: {sorted(LOSSES)}")
    print(f"ran in {time.perf_counter() - started:.1f}s")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(prog="python -m volab.walkforward", description=__doc__)
    parser.add_argument("command", choices=("run",), help="what to run")
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
        "--every", type=int, default=1, help="use every Nth session as an origin (default 1)"
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

    # The seal, enforced rather than documented. ``--to`` past dev_end is clamped loudly: there
    # is no path through this entry point that reads the holdout, and a silent clamp would let a
    # caller believe they had evaluated a window they had not.
    if arguments.stop > settings.splits.dev_end:
        print(
            f"--to {arguments.stop} is past dev_end {settings.splits.dev_end}; "
            "the holdout is sealed and this entry point has no key"
        )
        return 1

    print(f"storage root {settings.storage_root}")
    print(f"reads stop at dev_end {settings.splits.dev_end}")
    return _run(settings, arguments)


if __name__ == "__main__":
    sys.exit(main())
