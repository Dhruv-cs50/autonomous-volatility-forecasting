"""One fresh interpreter per evaluation: launch ``volab.candidate`` on a proposal, read back a file.

**Each proposal is evaluated by ``[python, "-m", "volab.candidate", "--proposal", <path>,
"--result", <path>]`` and never by importing the child and calling it.** The reason is the one
``volab.daily``'s docstring records: *a memoised full-sample statistic filled by one build and
reused by the next is invisible to truncation equivalence within a process* -- the README's E2
defect, which ``tests/test_process_isolation.py`` exists to catch. A sprint evaluates several
proposals in sequence, so chaining them in one interpreter would reintroduce that sharing *across
proposals, in a loop*, with nothing comparing the second build against a clean one. A subprocess
per proposal costs one interpreter start-up each, which is small beside an evaluation.

**The proposal travels as a file path, not as argv fields.** The child's argparse then has no
per-field option that could drift from ``Proposal`` (and no date option at all, R184), and the
exact bytes the child read are a file rather than a command line. The file is a JSON object of the
five ``Proposal`` fields plus ``proposal_id`` -- ``asdict(proposal)`` -- which is what
``volab.candidate.__main__`` splits, re-validates and requires to round-trip. It lives in a
temporary directory that is removed when ``submit`` returns, so it is not kept for inspection.

**The result is a file, not stdout**, because a comparison record is about 10 KB against a 16 KiB
output tail. ``ChildRun.result`` is that file parsed; it is ``None`` when the child wrote none
(an uncaught exception, argparse's usage error, a kill) **or** wrote something that is not a JSON
object. ``pid`` comes from the same file, so it is ``None`` exactly when ``result`` is.

**Exit codes are carried raw, and ``ChildRun.verdict`` names them without collapsing them** (R203):
0 evaluated; 1 evaluated but the built rows disagree with the persisted table *or* an uncaught
exception (told apart only by whether a result file exists); 2 argparse usage error, a bug in the
runner; 3 refused for coverage, no trial line, a legitimate outcome; 4 the proposal file refused
at the boundary; 5 refused by the audit; 6 the child's configuration was refused, so no
evaluation began and no result file exists. Any other code, including a negative one from a signal,
is
``"unrecognised"``. The constants below are copies of the child's, tied to it by a test and not
imported, because importing ``volab.candidate`` is forbidden here.

**Import fence (R192).** This package must reach nothing that touches ``volab.store``, and
``volab.candidate`` reads the panel by design. It is therefore started by module-name string,
which is not an import, and ``volab.features.targets`` is not imported either.

**The environment is inherited unchanged, apart from two additions** (R193): ``PYTHONUNBUFFERED``
and the storage root. Nothing is scrubbed. The seal on the holdout is that no code path passes the
argument that asks for it, and unlocking needs *both* of its keys; a scrub would be a second
mechanism that has to name one of them, which the scan on this package forbids. The caller supplies
``root`` -- the storage root the child reads and whose ledgers it appends to -- so a test that
forgets it cannot write to the repository's tracked ``data/``.

**Captured output is bounded by ``OUTPUT_BOUND_BYTES``, the tail is kept, stderr is merged into
stdout, and the child is unbuffered**, for the reasons ``volab.daily.runner`` gives; see there.
**That loop is duplicated here on purpose (R195):** extracting it to a shared module would have
meant refactoring ``daily/runner.py`` while another story was adding to it. The price is two copies
of one loop, of which only the bound is coupled by a test and not the logic, so **a bug fixed in
one must be fixed in the other by hand**. Extraction is the follow-up.

What this does not do: it does not run more than one proposal, retry, time out, decide what a
refusal means for a sprint, or write to any ledger; the child does that.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from volab.agent.vocabulary import Proposal

# The same number as ``volab.daily.runner.OUTPUT_BOUND_BYTES``, the other copy of this loop (R195).
# ``tests/test_agent_runner.py`` asserts the two are equal; nothing else keeps them so.
OUTPUT_BOUND_BYTES: int = 16 * 1024

_CHUNK_BYTES = 64 * 1024

# Copies of ``volab.candidate.__main__``'s, which cannot be imported here (R192). A test ties them.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_COVERAGE_REFUSED = 3
EXIT_PROPOSAL_REFUSED = 4
EXIT_AUDIT_REFUSED = 5
EXIT_CONFIG_REFUSED = 6

_CHILD_MODULE = "volab.candidate"

_REFUSALS = {
    EXIT_USAGE: "usage_error",
    EXIT_COVERAGE_REFUSED: "coverage_refused",
    EXIT_PROPOSAL_REFUSED: "proposal_refused",
    EXIT_AUDIT_REFUSED: "audit_refused",
    EXIT_CONFIG_REFUSED: "config_refused",
}


@dataclass(frozen=True)
class ChildRun:
    """What one child did: its exit code, what it wrote to its result file, the tail of its output.

    ``pid`` and ``result`` are both ``None`` when no usable result file was written.
    """

    proposal_id: str
    exit_code: int
    pid: int | None
    result: dict[str, Any] | None
    output_tail: str

    @property
    def verdict(self) -> str:
        """The exit code's meaning (R203) as a name; the raw code stays on ``exit_code``.

        One of ``evaluated``, ``disagrees``, ``crashed``, ``usage_error``, ``coverage_refused``,
        ``proposal_refused``, ``audit_refused``, ``config_refused`` or ``unrecognised``. Exit 1 is
        ``disagrees`` when a result file was read and ``crashed`` when none was.
        """
        if self.exit_code == EXIT_OK:
            return "evaluated"
        if self.exit_code == EXIT_FAILED:
            return "disagrees" if self.result is not None else "crashed"
        return _REFUSALS.get(self.exit_code, "unrecognised")


def _read_result(path: Path) -> dict[str, Any] | None:
    """The result file as a mapping, or ``None`` if it is absent, unreadable or not an object."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _run_one(command: list[str], env: dict[str, str]) -> tuple[int, str]:
    """Run ``command`` to completion; return its exit code and the bounded tail of its output.

    The other copy of this loop is ``volab.daily.runner._run_one`` (R195).
    """
    tail: deque[bytes] = deque()
    kept = 0
    total = 0
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
    ) as process:
        assert process.stdout is not None  # stdout=PIPE above; narrows the type for mypy
        while chunk := process.stdout.read(_CHUNK_BYTES):
            tail.append(chunk)
            kept += len(chunk)
            total += len(chunk)
            while kept - len(tail[0]) >= OUTPUT_BOUND_BYTES:
                kept -= len(tail.popleft())
        exit_code = process.wait()
    retained = b"".join(tail)[-OUTPUT_BOUND_BYTES:]
    dropped = total - len(retained)
    text = retained.decode("utf-8", errors="replace")
    if dropped:
        text = f"[{dropped} bytes of earlier output dropped]\n{text}"
    return exit_code, text


def submit(
    proposal: Proposal,
    *,
    root: Path,
    python: str = sys.executable,
    git_sha: str | None = None,
    git_dirty: bool | None = None,
) -> ChildRun:
    """Evaluate ``proposal`` in a fresh interpreter against the storage root ``root``.

    ``git_sha`` and ``git_dirty`` are the **sprint's** stamps, captured once at its start (R70) and
    handed to the child as ``--git-sha`` / ``--git-dirty`` so that it does not read the tree after
    an earlier child has appended to a tracked ledger. They go together: with ``git_sha=None``
    neither is passed and the child captures its own. ``git_dirty=None`` with a sha is "could not
    be read" and is passed as ``unknown``, which the child records as null and never as clean.
    They are not part of the proposal file, whose keys ``validate`` fixes (R184).

    ``python`` is the interpreter and is only ever followed by ``-m volab.candidate``; it is a
    parameter so a test can substitute a stub. Never raises for what the child did: every outcome,
    a crash included, comes back as a :class:`ChildRun`.
    """
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "VOLAB_STORAGE_ROOT": str(root)}
    with tempfile.TemporaryDirectory(prefix="volab-candidate-") as scratch:
        proposal_path = Path(scratch) / "proposal.json"
        result_path = Path(scratch) / "result.json"
        proposal_path.write_text(json.dumps(asdict(proposal)), encoding="utf-8")
        command = [
            python,
            "-m",
            _CHILD_MODULE,
            "--proposal",
            str(proposal_path),
            "--result",
            str(result_path),
        ]
        if git_sha is not None:
            dirty_flag = "unknown" if git_dirty is None else str(git_dirty).lower()
            command += ["--git-sha", git_sha, "--git-dirty", dirty_flag]
        exit_code, output = _run_one(command, env)
        result = _read_result(result_path)
    pid = result.get("pid") if result is not None else None
    return ChildRun(
        proposal_id=proposal.proposal_id,
        exit_code=exit_code,
        pid=pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        result=result,
        output_tail=output,
    )
