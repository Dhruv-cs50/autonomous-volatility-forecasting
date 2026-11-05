"""The run record: two append-only lines per daily run, the second referring to the first by id.

**The start line is written, flushed and fsynced before any stage runs**, and the completion line
is a *separate* line that refers to it by id. Both halves of that are deliberate and neither is
free:

- Writing first means a run that crashes, is interrupted, or is killed still leaves a visible
  line. This is the holdout-reveal design's protection 5 ("log the look before you take it")
  applied to a scheduler: a record written afterwards is a record of the runs that happened to
  finish, which is the wrong set.
- Two lines rather than one mutated line means neither can be rewritten to conceal the other, and
  an incomplete run is distinguishable from one that never started. The reveal design's call #3
  settled on the same shape for the same reason, and every other ledger in this project is
  append-only, so a record that was edited in place would be the one exception.

**An incomplete record does not block a retry.** ``completed_dates`` reports only dates whose
*completion* line exists, so a start with no completion leaves the date available. That is the
reveal design's call #1 -- attempts are counted, not forbidden -- and here the argument is
stronger rather than weaker: a transient failure that permanently consumed a date would mean an
unattended job could brick a day's record by crashing once.

**No count from this file is a reported number.** ``data/daily_runs.jsonl`` is gitignored by
decision (R158 Q3): the tree does not go dirty every morning, and the accepted cost is that
``git clean -xdf`` erases the evidence that the job ran. The reported counts remain
``data/trials.jsonl`` and ``data/comparisons.jsonl``, which nothing here writes to.

``sha`` and ``dirty`` are the caller's, captured at the start of its run and passed in, for the
reason ``append_trials`` states: read here they would describe the tree as it is when the writing
happens, which is not the tree that ran (R70).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from volab.daily import Plan

RUN_LOG = Path("data") / "daily_runs.jsonl"

# The outcome that means the date is finished with. Anything else -- a stage that exited non-zero,
# a refusal, an abandoned attempt -- leaves the date available, because a run that failed has not
# done the day's work and a scheduler that treated failure as completion would silently skip it.
# ``record_completion`` accepts any string so that a caller can record something this module did
# not anticipate; only this one value consumes a date.
COMPLETED_OUTCOME = "done"


@dataclass(frozen=True)
class StageResult:
    """What one stage did. ``exit_code`` is the stage's own verdict, not an interpretation.

    ``output`` is the bounded tail of what the stage printed and ``dropped_bytes`` how much fell
    off its front (zero means ``output`` is whole); ``timed_out`` says the exit code is the
    runner's, because the stage was killed, and not the stage's own. They are in the record
    because an unattended job's log is the only place a failing stage's reason survives. The
    defaults let a caller with only a verdict -- a test, a rehearsal -- still build one.
    """

    name: str
    exit_code: int
    output: str = ""
    dropped_bytes: int = 0
    timed_out: bool = False


def run_log_path(root: Path) -> Path:
    """Where the run log lives under ``root``, which is the repository root.

    Takes a root rather than reading the configuration, so a rehearsal writes its records
    somewhere else entirely by being handed a different root -- the mechanism the reveal design
    chose for its dress rehearsal (its call #6), rather than a mode flag that disables recording.
    """
    return root / RUN_LOG


def append_text(path: Path, text: str) -> None:
    """Append ``text`` (whole newline-terminated lines) and get it onto the disk before returning.

    Shared by this log and ``volab.daily.forecast``'s, which had separate copies of the same
    write and so the same defect.

    **If the file does not end in a newline, a newline is written first**, in the same write as
    the new text. A process killed between the ``write`` and the ``fsync`` can leave a final line
    with no terminator, and appending straight after it glues the new record onto the fragment:
    one unparseable line that swallows the record, after which a start line never joins to its
    completion and the date never reads as done. The separator puts the fragment on a line of its
    own, where the readers already skip it, and the new record on a line of its own. The file is
    only ever appended to: nothing is truncated or rewritten, so the torn bytes stay visible to
    anyone reading the file. An empty file, or one already ending in a newline (the ordinary
    case), gets no separator and so no blank line.

    ``flush`` then ``fsync``: closing the handle would flush it to the operating system, which
    survives this process dying but not the machine dying. A daily job's whole claim is that its
    record predates the work, and the cheapest way for that claim to be false is a line that was
    still in a buffer. Once a day, the cost does not matter.

    **Not guaranteed:** a torn line is still a line nothing can parse, and its record is lost; the
    fix keeps it from taking the *next* record with it. The tail check is not atomic with the
    write, so two writers racing on one file are not made safe (nothing here runs two). And
    ``fsync`` is a request to the operating system, not a promise about the hardware: durability
    against a power cut is not claimed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Binary, so the last byte is read as a byte and "\n" is not translated on the way out.
    with path.open("ab+") as handle:
        end = handle.seek(0, os.SEEK_END)
        separator = b""
        if end > 0:
            handle.seek(end - 1)
            if handle.read(1) != b"\n":
                separator = b"\n"
        handle.write(separator + text.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def _append(path: Path, line: dict[str, Any]) -> None:
    """Append one JSON line; see ``append_text`` for how a torn final line is handled."""
    append_text(path, json.dumps(line, allow_nan=False) + "\n")


def record_start(
    path: Path,
    plan: Plan,
    *,
    at: dt.datetime,
    sha: str,
    dirty: bool | None,
) -> str:
    """Write the start line and return the run id the completion line must refer to."""
    run_id = str(uuid.uuid4())
    _append(
        path,
        {
            "kind": "start",
            "id": run_id,
            "as_of": plan.as_of.isoformat(),
            "outcome_planned": plan.outcome,
            "reason": plan.reason,
            "plan": [stage.name for stage in plan.stages],
            "git_sha": sha,
            "git_dirty": dirty,
            "started_at": at.isoformat(),
        },
    )
    return run_id


def record_completion(
    path: Path,
    run_id: str,
    *,
    outcome: str,
    stages: tuple[StageResult, ...],
    at: dt.datetime,
) -> None:
    """Write the completion line for ``run_id``. The start line is left exactly as it was.

    Each stage is written with its bounded output, so the line is as large as the stages made it:
    at most ``OUTPUT_BOUND_BYTES`` of output per stage, which ``volab.daily.runner`` enforces.
    """
    _append(
        path,
        {
            "kind": "complete",
            "ref": run_id,
            "outcome": outcome,
            "stages": [
                {
                    "name": stage.name,
                    "exit_code": stage.exit_code,
                    "output": stage.output,
                    "dropped_bytes": stage.dropped_bytes,
                    "timed_out": stage.timed_out,
                }
                for stage in stages
            ],
            "ended_at": at.isoformat(),
        },
    )


def _lines(path: Path) -> list[dict[str, Any]]:
    """Every well-formed JSON object in the log, skipping anything this cannot parse.

    **A torn line is skipped and not raised on**, which is the opposite of this project's usual
    fail-closed instinct and is deliberate here. The file's whole purpose is to survive a job
    being killed, and a process killed between the ``write`` and the ``fsync`` can leave a partial
    final line. If reading the log then raised, one crash would make every later run unable to ask
    "what has already completed" -- the exact "a crash bricks the day" outcome the two-line design
    exists to prevent. A line that cannot be parsed is a line that records nothing, so skipping it
    loses no evidence; the bytes stay in the file for a reader. ``append_text`` keeps the *next*
    record off that line, so the skipped line is only ever the torn one.
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


def completed_dates(path: Path) -> frozenset[dt.date]:
    """The dates whose runs **completed**, derived by joining completion lines back to starts.

    A start with no completion is not here, which is what makes a crashed run retryable. The join
    is on the id rather than on position, so the two lines of a run need not be adjacent -- two
    runs overlapping in time would interleave their lines, and an id survives that where an
    assumption about ordering would not.

    **Only a completion whose outcome is ``COMPLETED_OUTCOME`` counts.** A completion recorded as
    failed or refused leaves its date available, because the work was not done; treating any
    completion line as "finished" would mean a stage that exited non-zero this morning is never
    retried, which is the same bricked-day failure as a crash, arrived at politely.
    """
    records = _lines(path)
    started: dict[str, dt.date] = {}
    for record in records:
        if record.get("kind") == "start":
            run_id = record.get("id")
            as_of = record.get("as_of")
            if isinstance(run_id, str) and isinstance(as_of, str):
                try:
                    started[run_id] = dt.date.fromisoformat(as_of)
                except ValueError:
                    continue
    done: set[dt.date] = set()
    for record in records:
        if record.get("kind") == "complete" and record.get("outcome") == COMPLETED_OUTCOME:
            reference = record.get("ref")
            if isinstance(reference, str) and reference in started:
                done.add(started[reference])
    return frozenset(done)


def incomplete_run_ids(path: Path) -> tuple[str, ...]:
    """Run ids with a start line and no completion line, in the order they started.

    Exposed because "how many attempts has this date had" is a question an operator will ask, and
    the reveal design's own answer to the same question is that attempts are reported rather than
    hidden: ``k`` logged looks, the ``k``-th completed.
    """
    records = _lines(path)
    starts = [
        record["id"]
        for record in records
        if record.get("kind") == "start" and isinstance(record.get("id"), str)
    ]
    completed = {
        record["ref"]
        for record in records
        if record.get("kind") == "complete" and isinstance(record.get("ref"), str)
    }
    # Any completion closes an attempt, whatever its outcome: an attempt that ended in failure is
    # finished as an attempt. Whether its *date* is finished is ``completed_dates``' question, and
    # the two answers are deliberately different.
    return tuple(run_id for run_id in starts if run_id not in completed)
