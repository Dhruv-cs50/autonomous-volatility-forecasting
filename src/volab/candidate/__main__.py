"""``python -m volab.candidate --proposal <path> --result <path>`` -- evaluate one proposal.

One verb, two required options and an optional pair of stamps, **and no others**. In particular
there is no date and no stride:
the window is fixed by :mod:`volab.candidate` (R184), because choosing it is selection and a
window the caller can choose is one it can choose badly. There is no unlock flag and the
environment is inherited unchanged (R193): the seal is that no code path asks for the sealed
window, not that something was removed from the child's environment.

**The stamps are the caller's, when it gives them (R70).** ``--git-sha`` and ``--git-dirty
{true,false,unknown}`` come as a pair: a sprint captures them once, at its start, and passes them
in, because a child that captures its own reads the tree *after* an earlier child appended to the
tracked ``data/agent_trials.jsonl`` and so stamps ``git_dirty=true`` on a tree whose code did not
change. ``unknown`` is ``None`` and is never rendered as clean. With neither given, the child
captures its own, so standalone use is still stamped. They are options and not proposal fields:
``validate`` refuses unknown keys and that refusal is load-bearing (R184). Neither is date-typed.

**The proposal file is re-validated here**, with ``volab.agent.vocabulary.validate``, because the
gate's verdict does not travel with the file and a tampered or hand-edited temp file must be
refused at the boundary that reads it. The file is a JSON object holding the five fields of a
``Proposal`` and its ``proposal_id``; the id is split into the sprint id and index that
``validate`` takes, and the proposal ``validate`` returns must carry the same id.

**The result is a file and not stdout** because a comparison record is about 10 KB against a
16 KiB output tail. It is written on every outcome that reaches a verdict (``status`` is ``ok``
or ``refused``,
including a refused proposal file), and holds the pid, the cohort, the reason, the trial count and
the records. A run that exits 1 because of the truncation comparison has still written its result;
an uncaught exception writes none and exits with Python's own 1.

**The pid is printed first**, on a line of its own, so that a caller can witness that each
evaluation really was a fresh interpreter: ``candidate pid: <n>``.

Exit codes. ``2`` is argparse's own usage error and is left alone:

* ``0`` -- evaluated, the trials and comparisons are on the agent ledgers.
* ``1`` -- evaluated, but the built rows disagree with the persisted feature table (the walk-forward
  CLI's meaning), or an unexpected failure. Trials were already appended when it is the former.
* ``3`` -- **refused for coverage**: a column the proposal names cannot support the fixed window.
  No model was fitted and no trial line was written; the result file carries the reason verbatim.
  A routine, data-driven outcome, not a defect.
* ``4`` -- **the proposal file was refused at this boundary**: unreadable, not JSON, or rejected by
  ``validate``. Nothing was read from the store and nothing was written to a ledger.
* ``5`` -- **refused by the audit**: a build read past its cutoff (R63). Models were fitted and
  nothing was appended. This is an integrity defect, not a property of the proposal.
* ``6`` -- **the configuration was refused**, so no evaluation was possible. The expected cause is
  a ``storage.root`` outside the repository: ``volab.config._validate_storage_root`` resolves it
  against the repository root *derived from the installed package*, so neither
  ``load_settings(path=...)`` nor ``VOLAB_STORAGE_ROOT`` can aim this child at a temporary
  directory (R206; ``volab.daily.__init__`` states the same constraint and
  ``tests/test_daily_rehearsal.py`` pins both routes). Settings must be loaded **before** the
  proposal can be judged, because ``validate`` needs ``settings.universe``, so this cannot be
  fixed by reordering. What is fixed is the *refusal*: a ``ConfigError`` here escaped as an
  uncaught traceback, which told a caller nothing. No result file is written, because no
  evaluation began. The runner names this ``config_refused``.

What this does not do: it does not run more than one proposal, retry, or decide what a refusal
means for a sprint. It never appends to ``data/trials.jsonl`` or ``data/comparisons.jsonl``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from volab.agent.vocabulary import Proposal, agent_cohort_name, validate
from volab.candidate import EvaluationOutcome, evaluate_proposal
from volab.config import load_settings
from volab.errors import ConfigError, ProposalRefused
from volab.walkforward import git_state

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2  # argparse's, never returned by this module's own code
EXIT_COVERAGE_REFUSED = 3
EXIT_PROPOSAL_REFUSED = 4
EXIT_AUDIT_REFUSED = 5
# No result file is written for this one: the evaluation never began.
EXIT_CONFIG_REFUSED = 6

# The repository root, the scope the dirty flag must ask about (R70): the default scope is the
# package, and an edit to ``config/base.yaml`` -- which carries ``dev_end`` -- then reads clean.
_REPO_ROOT = Path(__file__).resolve().parents[3]


# ``unknown`` is ``None``: a tree whose state could not be read must not render as clean (R70).
_DIRTY_FLAGS: dict[str, bool | None] = {"true": True, "false": False, "unknown": None}


class ProposalFileRefused(Exception):
    """The file is unreadable or malformed before ``validate`` can judge it.

    Distinct from ``ProposalRefused``, which carries a ``RefusalCode`` that names the branch of
    ``validate`` that refused: these refusals are about the file, and no such branch exists.
    """


def build_parser() -> argparse.ArgumentParser:
    """The command line: ``--proposal`` and ``--result``, both required, both paths as strings.

    A function so a test can walk ``parser._actions`` and assert that no action is date-typed.
    """
    parser = argparse.ArgumentParser(prog="python -m volab.candidate", description=__doc__)
    parser.add_argument("--proposal", required=True, help="the proposal JSON file to evaluate")
    parser.add_argument("--result", required=True, help="where to write the result JSON")
    parser.add_argument(
        "--git-sha",
        default=None,
        help="the caller's commit, captured once at its start (R70); with --git-dirty",
    )
    parser.add_argument(
        "--git-dirty",
        choices=sorted(_DIRTY_FLAGS),
        default=None,
        help="the caller's dirty flag; 'unknown' is recorded as null, never as clean",
    )
    return parser


def _read_proposal(path: Path, universe: tuple[str, ...]) -> Proposal:
    """The proposal in ``path``, validated, or :class:`ProposalFileRefused` / ``ProposalRefused``.

    Every way the file can be bad is one of those two refusals, since this is the boundary: an
    unreadable file, one that is not JSON, an id that is not ``<sprint id>-<ASCII digits>`` with a
    sprint id that begins with a date (so ``cohort_name_for`` cannot raise later), and whatever
    ``validate`` refuses. Nothing here is expected to escape as a traceback.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as unreadable:
        raise ProposalFileRefused(
            f"proposal file {path} is unreadable: {unreadable}"
        ) from unreadable
    if not isinstance(raw, dict):
        # ``validate`` words this itself, but it needs the id first, so say it here.
        raise ProposalFileRefused(
            f"not_a_mapping: a proposal is a mapping, got {type(raw).__name__}"
        )
    fields = dict(raw)
    proposal_id = fields.pop("proposal_id", None)
    sprint, _, index = proposal_id.rpartition("-") if isinstance(proposal_id, str) else ("", "", "")
    # ``isascii`` as well: ``str.isdigit`` accepts "\u00b2", which ``int`` then rejects.
    if not sprint or not (index.isascii() and index.isdigit()):
        raise ProposalFileRefused(
            f"proposal_id {proposal_id!r} is not '<sprint id>-<two-digit index>'"
        )
    try:
        # The cohort name is derived from the id downstream, so an id with no cohort name (a
        # sprint that does not begin with a date) is refused here and not at that later call.
        agent_cohort_name(sprint, int(index))
    except ValueError as nameless:
        raise ProposalFileRefused(
            f"proposal_id {proposal_id!r} has no cohort: {nameless}"
        ) from nameless
    proposal = validate(fields, sprint_id=sprint, index=int(index), universe=universe)
    if proposal.proposal_id != proposal_id:
        raise ProposalFileRefused(
            f"proposal_id {proposal_id!r} is not the canonical {proposal.proposal_id!r}"
        )
    return proposal


def _write_result(path: Path, result: dict[str, Any]) -> None:
    path.write_text(json.dumps(result, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _result(
    outcome: EvaluationOutcome | None, *, status: str, kind: str | None, reason: str | None
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "pid": os.getpid(),
        "status": status,
        "refusal_kind": kind,
        "reason": reason,
        "cohort": None,
        "trials_written": 0,
        "trials": [],
        "comparisons": [],
        "disagrees": False,
    }
    if outcome is not None:
        base.update(
            cohort=outcome.cohort,
            trials_written=outcome.written,
            trials=[trial.record() for trial in outcome.trials],
            comparisons=[item.record() for item in outcome.comparisons],
            disagrees=outcome.disagrees,
        )
    return base


def main(argv: Sequence[str] | None = None) -> int:
    started = time.perf_counter()
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if (arguments.git_sha is None) != (arguments.git_dirty is None):
        parser.error("--git-sha and --git-dirty are given together or not at all (R70)")
    # Before anything else, so a refusal still proves a new process ran.
    print(f"candidate pid: {os.getpid()}", flush=True)
    result_path = Path(arguments.result)
    # ``validate`` needs ``settings.universe``, so the configuration is loaded before the proposal
    # is judged and that order cannot be inverted. What can be improved, and is, is the refusal:
    # a ``ConfigError`` here used to escape as an uncaught traceback (R206).
    try:
        settings = load_settings()
    except ConfigError as refused:
        print(f"configuration refused, so no evaluation is possible: {refused}")
        return EXIT_CONFIG_REFUSED

    try:
        proposal = _read_proposal(Path(arguments.proposal), settings.universe)
    except (ProposalFileRefused, ProposalRefused) as refused:
        print(f"proposal refused: {refused}")
        _write_result(
            result_path, _result(None, status="refused", kind="proposal", reason=str(refused))
        )
        return EXIT_PROPOSAL_REFUSED

    print(f"storage root {settings.storage_root}")
    print(f"reads stop at dev_end {settings.splits.dev_end}")
    # The caller's stamps if it gave them (R70); otherwise captured now, before a single number is
    # produced, and stamped once for the run.
    if arguments.git_sha is not None:
        sha, dirty = arguments.git_sha, _DIRTY_FLAGS[arguments.git_dirty]
    else:
        sha, dirty = git_state(_REPO_ROOT)
    at = dt.datetime.now(dt.UTC)
    outcome = evaluate_proposal(proposal, settings=settings, at=at, sha=sha, dirty=dirty)

    result = _result(
        outcome,
        status="refused" if outcome.refused else "ok",
        kind=outcome.refusal_kind,
        reason=outcome.reason,
    )
    result.update(git_sha=sha, git_dirty=dirty, at=at.isoformat())
    _write_result(result_path, result)

    print(f"cohort {outcome.cohort}")
    if outcome.refused:
        print(f"REFUSED ({outcome.refusal_kind}): {outcome.reason}")
        return EXIT_COVERAGE_REFUSED if outcome.refusal_kind == "coverage" else EXIT_AUDIT_REFUSED
    print(f"trials: {outcome.written} appended, {len(outcome.comparisons)} comparison record(s)")
    print(f"ran in {time.perf_counter() - started:.1f}s")
    return EXIT_FAILED if outcome.disagrees else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
