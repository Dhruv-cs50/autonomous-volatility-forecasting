"""``python -m volab.agent {sprint,show}``: where a sprint's edge resources are built (P5-E2).

``volab.agent`` itself takes every door to the outside world as a parameter (R182): the model, the
checkpointer, the log path, the evaluation, the clock and the git state. This module is the one
caller that turns those parameters into real things, so "what did a live sprint touch?" has one
answer, and it is here.

**``sprint``** runs one sprint. It reads the credential once, here, by calling
:func:`volab.agent._llm.completer_from_environment`, never at import. It builds a
``FileCheckpointer`` under ``<root>/data/agent_checkpoints``, points the sprint log at
``<root>/data/agent_sprints.jsonl`` (:func:`volab.agent.sprintlog.sprint_log_path`), and wraps
:func:`volab.agent._runner.submit` so the evaluation child is aimed at ``settings.storage_root``.
``at``, ``sha`` and ``dirty`` are captured **once, before anything runs** (R70): read at the end,
they would describe the tree at write time and not the tree that ran. The sprint id defaults to
the start time as ``YYYY-MM-DDTHHMMSSZ``; ``--sprint-id`` names one explicitly, which is also how
an interrupted sprint is resumed from its checkpoint. An id that does not begin with an ISO date
is refused up front, by the same rule (:func:`volab.agent.vocabulary.agent_cohort_name`) that
would otherwise refuse it half way through, after a start line had been written.

**With no key, ``sprint`` refuses and says why.** ``completer_from_environment`` raises
``ConfigError`` naming the variable; that is printed to stderr and the exit status is
:data:`EXIT_NO_KEY`. No sprint-log line, no checkpoint and no directory is created by that
refusal, because the completer is built before anything else is. This refusal is the one piece of
evidence the repository can hold about the keyed path without a key; **no test here, or anywhere,
exercises a live model**, and that is not covered by anything in this module.

**``show``** prints one sprint's events (the latest sprint unless ``--sprint-id`` is given) and the
ledger counts from :func:`volab.counts.count_all`. It is read-only: it creates nothing, so a
mistyped ``--root`` does not conjure a ``data/`` directory.

**There is no ``promote`` verb and there must never be one (R188).** Promotion is a separate
package so that a sprint cannot promote in its own process; a verb here would make that a
convention again. ``tests/test_agent_cli.py`` asserts the parser's verbs are exactly ``sprint`` and
``show``. When a sprint ends ``awaiting_approval`` this module prints the ``volab.promote``
command line as text and runs nothing.

**Paths (R211).** ``--root`` names the directory that *holds* ``data/``; it defaults to
``settings.storage_root.parent``, the convention ``volab.promote`` and ``volab.leaderboard`` use.
``settings.storage_root`` is the ``data`` directory itself, so a ledger path (which already begins
``data/``) is joined to the root, never to the storage root. An explicit ``--root`` implies
``storage_root = <root>/data``. Note the evaluation child (R206) refuses a storage root that is
not under the repository, so a ``--root`` elsewhere can drive ``show`` but a live ``sprint``
against it ends with every child refused.

**Exit codes.**

* ``0`` -- ``sprint`` finished with any outcome except ``failed``; ``show`` printed.
* ``1`` -- ``sprint`` finished ``failed``; or ``show`` was asked for a sprint the log does not hold.
* ``2`` -- usage: argparse's own status, and a sprint id that does not begin with an ISO date.
* ``3`` -- no provider key (:data:`EXIT_NO_KEY`).
* ``4`` -- the sprint id already has a ``complete`` line; ids are used once.
* ``5`` -- **the gate accepted a canary, so the sprint was voided** (R189). Distinct from ``1``
  on purpose: an accepted canary means the guardrail stopped refusing illegal proposals, the
  one outcome a shell caller or a CI job must never read as success. Every outcome other than
  ``failed`` and this one used to exit 0, so this was invisible outside the sprint log.

**Ledger counts.** ``volab.counts.count_all`` is the repository's counter, but it imports
``volab.walkforward.count_trials`` and so reaches ``volab.store`` (the fence reports
``volab.agent.__main__ -> volab.counts -> volab.walkforward -> volab.store``). :func:`_count_all`
repeats it over the three ledger paths, and ``tests/test_agent_cli.py`` pins it to ``count_all``
on the same files so the two cannot drift silently.

**Git state.** ``volab.store.git_state`` is the repository's helper, but ``volab.store`` is the
panel's read path and this module is inside the store-reach fence (R192), so it cannot be imported
here or through ``volab.walkforward``. :func:`_git_state` repeats the two measured fixes that
helper carries (``--no-optional-locks``, and a pathspec that skips the raw data) and is the price
of that fence: a fix to one must be made to the other by hand.

What this does not cover: it does not retry, schedule, or run two sprints; it does not decide what
an outcome means; and it does not promote.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from volab.agent import SprintAlreadyComplete, SprintResult, run_sprint
from volab.agent._llm import Completer, completer_from_environment
from volab.agent._runner import ChildRun, submit
from volab.agent.memory import FileCheckpointer
from volab.agent.sprintlog import events, sprint_ids, sprint_log_path
from volab.agent.vocabulary import Proposal, agent_cohort_name
from volab.config import load_settings
from volab.errors import ConfigError

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_KEY = 3
EXIT_ALREADY_COMPLETE = 4
EXIT_VOIDED_BY_CANARY = 5

CHECKPOINTS = Path("data") / "agent_checkpoints"
VERBS = ("sprint", "show")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GIT_TIMEOUT_SECONDS = 5.0

SubmitFn = Callable[..., ChildRun]
GitStateFn = Callable[[Path], tuple[str, bool | None]]


def _git_state(scope: Path) -> tuple[str, bool | None]:
    """The commit and whether the tree under ``scope`` is dirty, or ``("unknown", None)``.

    A reimplementation of ``volab.store.git_state`` for the reason in the module docstring.
    ``--no-optional-locks`` keeps ``git status`` from taking ``index.lock``, and the pathspec
    excludes the raw data directories, which are the whole cost of the untracked walk.
    """

    def run(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", "--no-optional-locks", *args],
                cwd=scope,
                capture_output=True,
                text=True,
                check=False,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return None if done.returncode != 0 else done.stdout.strip()

    sha = run("rev-parse", "HEAD")
    if sha is None:
        return "unknown", None
    status = run(
        "status",
        "--porcelain",
        "--",
        ".",
        ":(exclude)data/raw",
        ":(exclude)data/curated",
        ":(exclude)data/features",
    )
    return sha, None if status is None else bool(status)


_LEDGERS = {
    "trials": Path("data") / "trials.jsonl",
    "agent_trials": Path("data") / "agent_trials.jsonl",
    "agent_comparisons": Path("data") / "agent_comparisons.jsonl",
}


def _count_all(root: Path) -> dict[str, int]:
    """Non-blank lines per ledger under ``root``; an absent file is ``0`` and is not created."""
    counts = {}
    for name, relative in _LEDGERS.items():
        path = root / relative
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        counts[name] = sum(1 for line in lines if line.strip())
    counts["total"] = counts["trials"] + counts["agent_trials"]
    return counts


def build_parser() -> argparse.ArgumentParser:
    """The argument parser. Exactly the verbs in :data:`VERBS`; see the module docstring."""
    parser = argparse.ArgumentParser(prog="python -m volab.agent", description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="the directory holding data/ (default: the parent of the configured storage root)",
    )
    verbs = parser.add_subparsers(dest="verb", required=True)
    run = verbs.add_parser("sprint", help="run one research sprint (needs VOLAB_LLM_API_KEY)")
    run.add_argument(
        "--sprint-id",
        default=None,
        help="must begin with an ISO date; default is the start time. Reuse an id to resume it.",
    )
    show = verbs.add_parser("show", help="print a sprint's events and the ledger counts")
    show.add_argument("--sprint-id", default=None, help="default: the most recently started")
    return parser


def _sprint(
    root: Path,
    storage_root: Path,
    sprint_id: str | None,
    *,
    completer_factory: Callable[[], Completer],
    submit_fn: SubmitFn,
    now: dt.datetime,
    git_state: GitStateFn,
) -> int:
    try:
        completer = completer_factory()
    except ConfigError as missing:
        print(f"refused: {missing}", file=sys.stderr)
        return EXIT_NO_KEY
    at = now
    sha, dirty = git_state(_REPO_ROOT)
    chosen = sprint_id or at.strftime("%Y-%m-%dT%H%M%SZ")
    try:
        agent_cohort_name(chosen, 0)
    except ValueError as bad:
        print(f"refused: {bad}", file=sys.stderr)
        return EXIT_USAGE
    settings = dataclasses.replace(load_settings(), storage_root=storage_root)

    def evaluate(proposal: Proposal) -> ChildRun:
        # The sprint's own stamps (R70), so no child reads a tree an earlier child appended to.
        return submit_fn(proposal, root=storage_root, git_sha=sha, git_dirty=dirty)

    try:
        result: SprintResult = run_sprint(
            sprint_id=chosen,
            settings=settings,
            completer=completer,
            checkpointer=FileCheckpointer(root / CHECKPOINTS),
            log_path=sprint_log_path(root),
            submit=evaluate,
            at=at,
            sha=sha,
            dirty=dirty,
        )
    except SprintAlreadyComplete as done:
        print(f"refused: {done}", file=sys.stderr)
        return EXIT_ALREADY_COMPLETE
    print(
        f"sprint {result.sprint_id}: {result.outcome} -- {result.reason}\n"
        f"  proposals {result.proposals}, accepted {result.accepted}, refused {result.refused}, "
        f"canary refused {result.canary_refused}"
    )
    if result.pending is not None:
        print(
            f"  awaiting approval: {result.pending.proposal_id}\n"
            f"  to promote, in a separate process: "
            f"python -m volab.promote --proposal-id {result.pending.proposal_id}"
        )
    if result.outcome == "voided_by_canary":
        return EXIT_VOIDED_BY_CANARY
    return EXIT_FAILED if result.outcome == "failed" else EXIT_OK


def _show(root: Path, sprint_id: str | None) -> int:
    log = sprint_log_path(root)
    known = sprint_ids(log)
    chosen = sprint_id or (known[0] if known else None)
    if sprint_id is not None and sprint_id not in known:
        print(f"no sprint {sprint_id!r} in {log}", file=sys.stderr)
        return EXIT_FAILED
    if chosen is None:
        print(f"no sprints in {log}")
    else:
        print(f"sprint {chosen}")
        for event in events(log, chosen):
            print(f"  {event}")
    for name, count in _count_all(root).items():
        print(f"{name}: {count}")
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    completer_factory: Callable[[], Completer] = completer_from_environment,
    submit_fn: SubmitFn = submit,
    now: dt.datetime | None = None,
    git_state: GitStateFn = _git_state,
) -> int:
    """Parse ``argv`` and run the verb. The keyword-only seams exist for tests; callers pass none.

    ``completer_factory`` defaults to the live one, ``submit_fn`` to the real child launcher,
    ``now`` to the wall clock and ``git_state`` to :func:`_git_state`. ``show`` uses none of them.
    """
    arguments = build_parser().parse_args(argv)
    settings = load_settings()
    if arguments.root is None:
        storage_root = settings.storage_root
        root = storage_root.parent
    else:
        root = arguments.root
        storage_root = root / "data"
    if arguments.verb == "show":
        return _show(root, arguments.sprint_id)
    return _sprint(
        root,
        storage_root,
        arguments.sprint_id,
        completer_factory=completer_factory,
        submit_fn=submit_fn,
        now=now or dt.datetime.now(dt.UTC),
        git_state=git_state,
    )


if __name__ == "__main__":
    sys.exit(main())
