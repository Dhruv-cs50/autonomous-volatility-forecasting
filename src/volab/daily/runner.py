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

**A stage that runs longer than ``timeout_seconds`` is killed and is a failed stage.** An
unattended job with no timeout is a job that can hang forever on a stalled download and never
write the completion line, so the date is neither done nor visibly failed. The deadline is
enforced by a timer that sends SIGKILL to the stage's whole process group (the child is started in
its own session for that reason: a grandchild that inherited the output pipe would otherwise keep
the read loop open after its parent died). The stage is then recorded with ``timed_out`` set and
the exit code ``TIMED_OUT_EXIT_CODE``, which is not zero, so the ordinary fail-stop applies. The
default, ``STAGE_TIMEOUT_SECONDS``, is a ceiling chosen without measuring the real stages; it is
there to bound a hang, not to time the pipeline.

**This module takes no clock and records nothing.** It does not write the run record or any
ledger and imports no sink; ``StageRun.as_result`` produces the ``StageResult`` -- verdict, bounded
output, dropped-byte count and timed-out flag -- that the caller hands to ``volab.daily.runlog``,
which writes all of it into the completion line.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass

from volab.daily import Stage
from volab.daily.runlog import StageResult

# 16 KiB: a screenful of traceback is a few hundred bytes and a progress bar's worth of ordinary
# output is a few thousand, so this keeps the reason a stage failed with room to spare while a
# daily record of six stages stays under 100 KiB however much any one of them prints.
OUTPUT_BOUND_BYTES = 16 * 1024

_CHUNK_BYTES = 64 * 1024

# Two hours per stage. Unmeasured: the real stages have not been timed unattended, and this is a
# bound on a hang rather than an estimate of a run, so it errs long -- a false timeout abandons
# a day's work, a late one costs a morning.
STAGE_TIMEOUT_SECONDS = 2 * 60 * 60

# What a killed stage is recorded as exiting with: 124, as ``timeout(1)`` reports. Not the
# child's -9, which a stage could also have earned by being OOM-killed; ``timed_out`` is what
# distinguishes the two.
TIMED_OUT_EXIT_CODE = 124


@dataclass(frozen=True)
class StageRun:
    """What one executed stage did: its verdict, and the bounded tail of what it printed.

    ``dropped_bytes`` is how much output fell off the front; zero means ``output`` is complete.
    """

    name: str
    exit_code: int
    output: str
    dropped_bytes: int
    timed_out: bool = False

    def as_result(self) -> StageResult:
        return StageResult(
            name=self.name,
            exit_code=self.exit_code,
            output=self.output,
            dropped_bytes=self.dropped_bytes,
            timed_out=self.timed_out,
        )


def _run_one(stage: Stage, timeout_seconds: float) -> StageRun:
    """Run ``stage`` in a fresh interpreter and return its verdict and output tail.

    Killed, and reported as timed out, if it is still running after ``timeout_seconds``.
    """
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
        start_new_session=True,  # its own process group, so the timeout can kill the whole tree
    ) as process:
        assert process.stdout is not None  # stdout=PIPE above; narrows the type for mypy
        expired = threading.Event()

        def kill_the_group() -> None:
            if process.poll() is not None:
                return  # already exited: a finished stage is not a timed-out one
            expired.set()
            # It may exit between the deadline and the kill; then there is nothing left to kill.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)

        timer = threading.Timer(timeout_seconds, kill_the_group)
        timer.start()
        try:
            while chunk := process.stdout.read(_CHUNK_BYTES):
                tail.append(chunk)
                kept += len(chunk)
                total += len(chunk)
                while kept - len(tail[0]) >= OUTPUT_BOUND_BYTES:
                    kept -= len(tail.popleft())
            exit_code = process.wait()
        finally:
            timer.cancel()
            timer.join()
        timed_out = expired.is_set()
        if timed_out:
            exit_code = TIMED_OUT_EXIT_CODE
    retained = b"".join(tail)[-OUTPUT_BOUND_BYTES:]
    dropped = total - len(retained)
    text = retained.decode("utf-8", errors="replace")
    if dropped:
        text = f"[{dropped} bytes of earlier output dropped]\n{text}"
    return StageRun(
        name=stage.name,
        exit_code=exit_code,
        output=text,
        dropped_bytes=dropped,
        timed_out=timed_out,
    )


def run_stages(
    stages: tuple[Stage, ...], *, timeout_seconds: float = STAGE_TIMEOUT_SECONDS
) -> tuple[StageRun, ...]:
    """Run ``stages`` in order, one fresh interpreter each, stopping after the first failure.

    A failure is a non-zero exit code, which a timed-out stage always has. ``timeout_seconds``
    applies to each stage separately, not to the run.

    Returns the stages that were *started*: all of them if every exit code was zero, otherwise up
    to and including the first that was not. A stage absent from the result did not run.
    """
    runs: list[StageRun] = []
    for stage in stages:
        run = _run_one(stage, timeout_seconds)
        runs.append(run)
        if run.exit_code != 0:
            break
    return tuple(runs)
