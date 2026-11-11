"""The sprint event log: append-only lines joined by a sprint id, and the pending approval.

This mirrors ``volab.daily.runlog`` and does not reuse it. ``record_start`` there takes a
``volab.daily.Plan`` and ``completed_dates`` is keyed on a date; a sprint is neither. Three of that
module's design properties are right for a sprint as well and are copied, each for its reason:

- **The start line is written, flushed and fsynced before anything else happens.** A sprint that
  crashes, is interrupted or is killed must still leave a visible line. This is the holdout-reveal
  design's protection 5, "log the look before you take it", applied to a sprint: a record written
  afterwards is a record of the sprints that happened to finish, which is the wrong set. Every
  event is appended with the same flush-and-fsync, so a sprint's account is as durable as its start.
- **A sprint is lines joined by an id, never one mutated line.** The start and the completion are
  separate lines, and so is every event between them, so none can be rewritten to conceal another
  and an incomplete sprint is distinguishable from one that never started. Every other ledger in
  this project is append-only; a record edited in place would be the one exception.
- **A torn final line is skipped, not raised on.** A process killed mid-write leaves a partial
  line, and a reader that raised would let one truncated write destroy the whole log's
  readability. A line that cannot be parsed records nothing, so skipping it loses no evidence; the
  bytes stay in the file.

**No count from this file is a reported number.** ``data/agent_sprints.jsonl`` is gitignored, as
``data/daily_runs.jsonl`` is (R158 Q3), and the accepted cost is that ``git clean -xdf`` erases the
evidence that a sprint ran. It is *not* a trial ledger. The trial ledgers are
``data/agent_trials.jsonl`` and ``data/agent_comparisons.jsonl``, both tracked, written by
``volab.candidate``, and they are the reported numbers (R183). Nothing here writes to them.

**Named requirement: a ``proposal`` event carries the ``(cohort, at, git_sha)`` key by which its
trial lines can be found.** That is the whole reason this log is allowed to be gitignored: the
sprint log and the agent trial ledger *join* on that key, so the agent keeps no shadow count of its
own trials. ``record_event`` refuses a ``proposal`` that lacks any of the three. Other kinds that
evaluate something are the caller's to key the same way; only ``proposal`` is enforced here.

**``sha`` and ``dirty`` are the caller's**, captured at the start of its run and passed in, for the
reason ``append_trials`` states (R70): read here they would describe the tree as it is when the
writing happens, which is not the tree that ran. ``dirty=None`` means git was unreachable and is
written as JSON ``null``; nothing here renders it as clean.

**The pending approval is the sprint's half of the human gate (R188).** A sprint that reaches a
promotable candidate writes an ``awaiting_approval`` event whose fields are those of
``PendingApproval`` and exits; ``pending_approvals`` reads them back in a later, separate process.
This module only records and reads. It does not recompute the fingerprint of a stored approval --
that comparison, and the refusal on a mismatch, belong to ``volab.promote``, which imports this
module and is never imported by it. An approval counts as pending until a ``promoted`` event
carrying the same ``proposal_id`` exists; the ``promoted`` event itself is written by that package.
An ``awaiting_approval`` event that lacks a field is skipped rather than raised on, for the
torn-line reason above, so it is also not offered for approval.

This package imports nothing that reaches the read path (R192), so only the standard library is
used here.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SPRINT_LOG = Path("data") / "agent_sprints.jsonl"

# A closed set, because the log is the only account of what a sprint did and an event recorded under
# a misspelt kind is one nobody will ever look for. ``record_event`` refuses anything else.
EVENT_KINDS: tuple[str, ...] = (
    "start",
    "proposal",
    "canary",
    "refused",
    "report",
    "awaiting_approval",
    "promoted",
    "canary_accepted",
    "complete",
)

# What a ``proposal`` event must carry so that its trial lines can be found (R183).
_PROPOSAL_JOIN_KEY = ("cohort", "at", "git_sha")


@dataclass(frozen=True)
class PendingApproval:
    """A candidate awaiting a human. Written by a sprint, read by a later, separate process."""

    sprint_id: str
    proposal_id: str
    cohort: str
    proposal: dict[str, Any]  # the validated Proposal, canonical JSON (sorted keys)
    created_at: dt.datetime
    git_sha: str
    git_dirty: bool | None  # None means "git unreachable", NEVER "clean" (R70)
    evidence: tuple[str, ...]  # the agent-ledger line hashes this approval rests on
    fingerprint: str  # sha256 over canonical(proposal) + "\n".join(sorted(evidence))


def sprint_log_path(root: Path) -> Path:
    """Where the sprint log lives under ``root``, which is the repository root.

    Takes a root rather than reading the configuration, so a rehearsal writes its records somewhere
    else entirely by being handed a different root, as ``run_log_path`` does.
    """
    return root / SPRINT_LOG


def _json_default(value: object) -> str:
    """Serialise a datetime as ISO 8601 and refuse every other type that is not plain JSON."""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def _append(path: Path, line: dict[str, Any]) -> None:
    """Append one JSON line and get it onto the disk before returning.

    Serialised *before* the file is opened, so a value that cannot be written (a ``NaN``, a type
    that is not JSON) raises with nothing appended rather than leaving half a line. ``flush`` then
    ``fsync``: closing the handle would flush to the operating system, which survives this process
    dying but not the machine dying, and the claim of this log is that its record predates the work.
    """
    text = json.dumps(line, allow_nan=False, default=_json_default) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def record_start(
    path: Path,
    *,
    sprint_id: str,
    at: dt.datetime,
    sha: str,
    dirty: bool | None,
) -> None:
    """Write the start line for ``sprint_id``, before the sprint does anything else."""
    _append(
        path,
        {
            "kind": "start",
            "sprint_id": sprint_id,
            "git_sha": sha,
            "git_dirty": dirty,
            "started_at": at,
        },
    )


def record_event(path: Path, *, sprint_id: str, kind: str, **fields: Any) -> None:
    """Append one event to ``sprint_id``'s account, refusing a ``kind`` not in ``EVENT_KINDS``.

    ``fields`` are written as given, after ``kind`` and ``sprint_id``, which a field of the same
    name would otherwise overwrite, so those two are refused as field names. A ``proposal`` must
    carry ``cohort``, ``at`` and ``git_sha`` (see the module docstring).
    """
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown sprint event kind {kind!r}; expected one of {EVENT_KINDS}")
    if "kind" in fields or "sprint_id" in fields:
        raise ValueError("'kind' and 'sprint_id' are not fields")
    if kind == "proposal":
        missing = [name for name in _PROPOSAL_JOIN_KEY if name not in fields]
        if missing:
            raise ValueError(
                f"a proposal event must carry {_PROPOSAL_JOIN_KEY}, "
                f"or its trial lines cannot be found; missing {missing}"
            )
    _append(path, {"kind": kind, "sprint_id": sprint_id, **fields})


def record_completion(path: Path, *, sprint_id: str, outcome: str, at: dt.datetime) -> None:
    """Write the completion line for ``sprint_id``. The start line is left exactly as it was."""
    _append(
        path,
        {"kind": "complete", "sprint_id": sprint_id, "outcome": outcome, "ended_at": at},
    )


def _lines(path: Path) -> list[dict[str, Any]]:
    """Every well-formed JSON object in the log, skipping anything this cannot parse.

    A torn line is skipped and not raised on, for the reason in the module docstring: a process
    killed mid-write must not make the whole log unreadable. Blank lines and lines that parse to
    something other than an object are skipped for the same reason, they record nothing.
    """
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def events(path: Path, sprint_id: str | None = None) -> tuple[dict[str, Any], ...]:
    """Every event in file order, or only those of ``sprint_id`` when one is given.

    The join is on the id rather than on position, so a sprint's lines need not be adjacent: two
    sprints overlapping in time interleave theirs, and an id survives that where an assumption
    about ordering would not.
    """
    records = _lines(path)
    if sprint_id is not None:
        records = [record for record in records if record.get("sprint_id") == sprint_id]
    return tuple(records)


def sprint_ids(path: Path) -> tuple[str, ...]:
    """Ids of every sprint with a start line, most recently started first.

    Ordered by position in the file, which is append order and so start order; no timestamp is
    consulted, because a clock that stepped backwards must not reorder the record. An id with
    events but no start line is not listed, since only a start line says a sprint began.
    """
    seen: list[str] = []
    for record in _lines(path):
        sprint_id = record.get("sprint_id")
        if record.get("kind") == "start" and isinstance(sprint_id, str) and sprint_id not in seen:
            seen.append(sprint_id)
    return tuple(reversed(seen))


def _canonical(proposal: Mapping[str, Any]) -> str:
    """The proposal as JSON with sorted keys and no insignificant whitespace."""
    return json.dumps(proposal, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint_of(proposal: Mapping[str, Any], evidence: Sequence[str]) -> str:
    """sha256 over the canonical proposal followed by the newline-joined, sorted evidence.

    Canonical so that a later, separate process can recompute it from the stored approval and
    detect that the ledger moved under it: two runs over the same inputs agree byte for byte.
    Key order in ``proposal`` and order of ``evidence`` do not matter; any change to a value, to the
    set of lines, or to a key does. The evidence is sorted but not de-duplicated, so a repeated line
    is a different fingerprint.
    """
    text = _canonical(proposal) + "\n".join(sorted(evidence))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _approval_from(event: Mapping[str, Any]) -> PendingApproval | None:
    """Read one ``awaiting_approval`` event as a ``PendingApproval``, or ``None`` if malformed."""
    try:
        sprint_id = event["sprint_id"]
        proposal_id = event["proposal_id"]
        cohort = event["cohort"]
        proposal = event["proposal"]
        created_at = event["created_at"]
        git_sha = event["git_sha"]
        git_dirty = event["git_dirty"]
        evidence = event["evidence"]
        fingerprint = event["fingerprint"]
    except KeyError:
        return None
    strings = (sprint_id, proposal_id, cohort, created_at, git_sha, fingerprint)
    if not all(isinstance(value, str) for value in strings):
        return None
    if not isinstance(proposal, dict) or not isinstance(evidence, list):
        return None
    if git_dirty is not None and not isinstance(git_dirty, bool):
        return None
    if not all(isinstance(line, str) for line in evidence):
        return None
    try:
        created = dt.datetime.fromisoformat(created_at)
    except ValueError:
        return None
    return PendingApproval(
        sprint_id=sprint_id,
        proposal_id=proposal_id,
        cohort=cohort,
        proposal=proposal,
        created_at=created,
        git_sha=git_sha,
        git_dirty=git_dirty,
        evidence=tuple(evidence),
        fingerprint=fingerprint,
    )


def pending_approvals(path: Path) -> tuple[PendingApproval, ...]:
    """Approvals awaiting a human: an ``awaiting_approval`` event and no ``promoted`` one for it.

    In file order. Keyed on ``proposal_id``, which is what ``volab.promote`` is handed. This does
    not check fingerprints (see the module docstring) and does not de-duplicate: if the same
    proposal id was recorded as awaiting twice, both are returned, and which one the operator meant
    is the promoting process's to refuse on rather than this reader's to guess.
    """
    records = _lines(path)
    promoted = {
        record["proposal_id"]
        for record in records
        if record.get("kind") == "promoted" and isinstance(record.get("proposal_id"), str)
    }
    pending: list[PendingApproval] = []
    for record in records:
        if record.get("kind") != "awaiting_approval":
            continue
        approval = _approval_from(record)
        if approval is not None and approval.proposal_id not in promoted:
            pending.append(approval)
    return tuple(pending)
