"""The human gate: approving an agent candidate, in a process of its own.

A separate package rather than a verb on ``volab.agent`` so that the separation is an import-graph
fact instead of a convention -- a verb in the same module means a sprint *could* promote in-process
and only discipline would stop it (R188). The edge runs one way: this package imports
``volab.agent`` and never the reverse, and both directions are asserted
(``tests/test_promote.py::test_the_agent_package_cannot_reach_the_promote_package`` and the
store-reach fence in ``tests/test_agent_discipline.py``).

**Promotion here writes a ``promoted`` event and nothing else.** No champion pointer moves and
nothing a daily stage consumes is written, because the framework's own ordering puts a single logged
holdout check *before* promotion and that check is Phase 6's. So the framework's "``interrupt``
before promotion" is met by pause-and-persist, approval in a separate process, and a recorded
decision -- not by a champion changing hands. The event carries the ``proposal_id`` (R201), which
is what :func:`volab.agent.sprintlog.pending_approvals` keys on; an event keyed on the sprint alone
would mark every candidate that sprint produced as promoted.

**What this does not guarantee.** It guarantees a different *process*, not a different *agent*
(design, "What is not structural", item 4). ``python -m volab.promote`` cannot tell a person typing
it from a cron line or from a model with a shell. The gate is weaker than its name: calling it
"human approval" would claim more than a process boundary can show, so this module records a
decision made in a separate process and does not claim to know who made it.

**The operator names the candidate.** ``--proposal-id`` is required and there is no ``--latest``,
no ``--all`` and no positional shortcut. A flag that selects the candidate would mean the operator
is no longer the one choosing it.

**The fingerprint is recomputed by the sprint's own code.** :func:`volab.agent.evidence_hash` and
:func:`volab.agent.sprintlog.fingerprint_of` are imported and called; there is no second
implementation here, because two implementations of a hash can drift one byte apart and every
approval would then be refused for a reason nobody can see. The contract is the one
``evidence_hash`` states: the *parsed* lines that survive
:func:`volab.agent.ledger.read_agent_trials` (``last_origin`` inside the development window),
filtered to the approval's cohort, **duplicates included** (R210: a crash-window resubmission
doubles a cohort's lines, and the sprint fingerprinted all of them).

**Refusals** (each exits 1, writes nothing, and says why): no ``awaiting_approval`` event for the
id; a ``promoted`` event for the id already exists, since promotion happens once; the recomputed
fingerprint differs from the stored one, in which case both are printed; and, because
``pending_approvals`` leaves it to this process to refuse rather than guess, more than one
``awaiting_approval`` event for the same id. An unreadable agent ledger is also reported and
exits 1.

**What is not covered.** The stored ``evidence`` list is not compared with the recomputed one
beyond the fingerprint, so a mismatch says that the ledger moved and not which line moved. This
module reads the sprint log and the agent trial ledger only; it imports nothing that reaches the
panel (R192), and it writes no ledger line and nothing that opens the held-out window.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Sequence
from pathlib import Path

from volab.agent import evidence_hash
from volab.agent.ledger import LedgerReadError, read_agent_trials
from volab.agent.sprintlog import (
    PendingApproval,
    events,
    fingerprint_of,
    pending_approvals,
    record_event,
    sprint_log_path,
)
from volab.config import load_settings


class _Refused(Exception):
    """A reason promotion did not happen. Carries the text printed to the operator."""


def _recompute(approval: PendingApproval, root: Path, dev_end: dt.date) -> str:
    """The fingerprint the sprint would compute now, by the sprint's own functions (see above)."""
    view = read_agent_trials(root, dev_end=dev_end)
    evidence = [evidence_hash(line) for line in view.lines if line.get("cohort") == approval.cohort]
    return fingerprint_of(approval.proposal, evidence)


def _select(root: Path, proposal_id: str) -> PendingApproval:
    """The one approval for ``proposal_id``, or raise :class:`_Refused` saying why not."""
    log = sprint_log_path(root)
    recorded = events(log)
    awaiting = [
        e
        for e in recorded
        if e.get("kind") == "awaiting_approval" and e.get("proposal_id") == proposal_id
    ]
    if not awaiting:
        raise _Refused(f"no awaiting_approval event for proposal {proposal_id!r} in {log}")
    if any(e.get("kind") == "promoted" and e.get("proposal_id") == proposal_id for e in recorded):
        raise _Refused(f"proposal {proposal_id!r} is already promoted; promotion happens once")
    if len(awaiting) > 1:
        raise _Refused(
            f"{len(awaiting)} awaiting_approval events for proposal {proposal_id!r}; "
            "which one was meant is not guessed"
        )
    parsed = [a for a in pending_approvals(log) if a.proposal_id == proposal_id]
    if not parsed:
        raise _Refused(f"the awaiting_approval event for {proposal_id!r} is malformed")
    return parsed[0]


def promote(root: Path, proposal_id: str, *, dev_end: dt.date, now: dt.datetime) -> PendingApproval:
    """Record the ``promoted`` event for ``proposal_id`` under ``root``, or raise :class:`_Refused`.

    Writes exactly one sprint-log line on success and nothing on any refusal.
    """
    approval = _select(root, proposal_id)
    recomputed = _recompute(approval, root, dev_end)
    if recomputed != approval.fingerprint:
        raise _Refused(
            f"the agent ledger moved under proposal {proposal_id!r}\n"
            f"  stored fingerprint:     {approval.fingerprint}\n"
            f"  recomputed fingerprint: {recomputed}"
        )
    record_event(
        sprint_log_path(root),
        sprint_id=approval.sprint_id,
        kind="promoted",
        proposal_id=approval.proposal_id,
        cohort=approval.cohort,
        fingerprint=approval.fingerprint,
        promoted_at=now.isoformat(),
    )
    return approval


def main(argv: Sequence[str] | None = None, *, now: dt.datetime | None = None) -> int:
    """Parse the arguments, promote, and return the exit status (0 promoted, 1 refused)."""
    parser = argparse.ArgumentParser(prog="python -m volab.promote", description=__doc__)
    parser.add_argument(
        "--proposal-id", required=True, help="the proposal to promote; the operator types it"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="the directory holding data/ (default: the repository's own)",
    )
    arguments = parser.parse_args(argv)
    settings = load_settings()
    root: Path = arguments.root or settings.storage_root.parent
    when = now or dt.datetime.now(dt.UTC)
    try:
        approval = promote(root, arguments.proposal_id, dev_end=settings.splits.dev_end, now=when)
    except _Refused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 1
    except LedgerReadError as unreadable:
        print(f"refused: the agent ledger cannot be read: {unreadable}", file=sys.stderr)
        return 1
    print(
        f"promoted {approval.proposal_id} (sprint {approval.sprint_id}, cohort {approval.cohort})"
    )
    return 0
