"""The stage runner: each stage a fresh interpreter, its exit code its verdict, its output bounded.

**A stage is run as ``[sys.executable, "-m", stage.module, *stage.argv]`` and never by importing
the stage and calling its ``main``.** The reason is specific to this repository.
``tests/test_process_isolation.py`` exists because a memoised full-sample statistic, filled by one
build and reused by the next, is invisible to truncation equivalence *within* a process -- the
README's E2 defect, which the equivalence test could not see until the two builds were put in
separate interpreters. A daily run that chained the pipeline's stages in one interpreter would
reintroduce exactly that sharing, in production, where no test is watching and nothing compares
the second build against a clean one. A subprocess per stage costs one interpreter start-up each,
which is irrelevant at a daily cadence.

**The exit code is the stage's verdict, and the first non-zero one ends the run.** The stages after
it are not started, and the failure comes back as a ``StageRun`` carrying that stage's name and its
code rather than as an exception: an unattended job's failure is data for the run record, and a
traceback nobody reads is not a record. A module that does not exist is the same thing -- the
interpreter exits non-zero and says why, which lands in the output like any other failure.

**Captured output is bounded by ``OUTPUT_BOUND_BYTES``, and it is the tail that is kept.** A stage
printing megabytes must not make the run record unreadable, and the end of a stage's output is
where it says why it stopped. stderr is merged into stdout, and the child is started with
``PYTHONUNBUFFERED=1``, so a traceback stays in order with what preceded it: without that, a
child's stdout is block-buffered on a pipe while its stderr is not, and the traceback would arrive
*before* output the stage printed earlier. The child's output is read in chunks and only the
tail is retained, so the bound holds the process's memory as well as the result's. When bytes were
dropped, ``output`` begins with a marker line stating exactly how many, and ``dropped_bytes``
carries the same count.
Output is decoded after truncation with ``errors="replace"``, so a stage emitting bytes that are
not UTF-8 -- or a cut that lands inside a multi-byte character -- yields a replacement character
and not an exception.

**This module takes no clock and records nothing.** It does not write the run record or any
ledger and imports no sink; ``StageRun.as_result`` produces the ``StageResult`` the caller hands to
``volab.daily.runlog``. The record's stage entries carry only ``name`` and ``exit_code``, so the
bounded ``output`` is available to the caller here and is **not** part of that record today.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import deque
from dataclasses import dataclass

from volab.daily import Stage
from volab.daily.runlog import StageResult

# 16 KiB: a screenful of traceback is a few hundred bytes and a progress bar's worth of ordinary
# output is a few thousand, so this keeps the reason a stage failed with room to spare while a
# daily record of six stages stays under 100 KiB however much any one of them prints.
OUTPUT_BOUND_BYTES = 16 * 1024

_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class StageRun:
    """What one executed stage did: its verdict, and the bounded tail of what it printed.

    ``dropped_bytes`` is how much output fell off the front; zero means ``output`` is complete.
    """

    name: str
    exit_code: int
    output: str
    dropped_bytes: int

    def as_result(self) -> StageResult:
        return StageResult(name=self.name, exit_code=self.exit_code)


def _run_one(stage: Stage) -> StageRun:
    """Run ``stage`` to completion in a fresh interpreter and return its verdict and output tail."""
    tail: deque[bytes] = deque()
    kept = 0
    total = 0
    command = [sys.executable, "-m", stage.module, *stage.argv]
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
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
    return StageRun(name=stage.name, exit_code=exit_code, output=text, dropped_bytes=dropped)


def run_stages(stages: tuple[Stage, ...]) -> tuple[StageRun, ...]:
    """Run ``stages`` in order, one fresh interpreter each, stopping after the first failure.

    Returns the stages that were *started*: all of them if every exit code was zero, otherwise up
    to and including the first that was not. A stage absent from the result did not run.
    """
    runs: list[StageRun] = []
    for stage in stages:
        run = _run_one(stage)
        runs.append(run)
        if run.exit_code != 0:
            break
    return tuple(runs)
