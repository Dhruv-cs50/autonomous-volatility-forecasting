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

**A stage that runs longer than ``timeout_seconds`` is killed and is a failed stage -- within a
stated bound.** An unattended job with no timeout is a job that can hang forever on a stalled
download and never write the completion line, so the date is neither done nor visibly failed. The
deadline is enforced by a ``threading.Timer`` that sends SIGKILL to the stage's whole process
group (the child is started in its own session for that reason). The stage is then recorded with
``timed_out`` set and the exit code ``TIMED_OUT_EXIT_CODE``, which is not zero, so the ordinary
fail-stop applies. The default, ``STAGE_TIMEOUT_SECONDS``, is a ceiling chosen without measuring
the real stages; it is there to bound a hang, not to time the pipeline.

**The bound, stated rather than implied.** It has two halves, and they are not the same claim.

*Everything still in the stage's process group is killed, on every path that is not interrupted
again while it cleans up.* On a timeout the ``threading.Timer`` sends SIGKILL to the group while the
stage runs. After an ordinary exit, a failure, a timeout or a first interrupt, the cleanup
(``_close_reap_and_sweep``) closes the pipe, reaps the stage and then has ``_sweep_the_group`` send
SIGKILL to the group, so a same-group descendant the stage forgot to wait for (a plain
``subprocess.Popen`` it left running) does not outlive the stage. SIGKILL, so nothing gets to clean
up. The three steps are each attempted even if an earlier one was interrupted, so a second
interrupt that lands in the reap still gets the sweep and a second reap. What is not covered: a
third interrupt landing in that second reap, and a SIGKILL to ``run``, run nothing at all. The sweep
runs after the read loop, so it cannot truncate captured output, and after the reap, so on the
uninterrupted paths it cannot race a live stage. Its own residuals, both small: POSIX does not
reuse a process-group id while the group still has a member, so the only window is the moment
after the group has emptied completely, when the id could in principle be reused before the
sweep's signal lands; and the timeout's ``kill_the_group`` can fire in the gap between
``process.wait()`` returning and ``timer.cancel()``, which is safe only because it re-checks
``poll()`` first and does nothing for a stage that has already been reaped.

*A descendant that left the group is not killed.* (``setsid``, ``start_new_session=True``, a
double fork into a daemon.) It is orphaned, keeps running, and keeps the pipe open. This is a
decision and not an oversight, and nothing here claims otherwise:

- darwin has no cgroups and no ``PR_SET_CHILD_SUBREAPER``, so no kernel object holds "everything
  the stage started";
- a reparented process has lost its ancestry: ``ps`` shows a pid and a pgid but nothing links it
  to a stage that has exited, so any sweep would have to match on a command line or an executable
  name and could kill a process that merely looks similar -- worse than a descendant that keeps
  running;
- a whole-run timeout does not reach it either, since it left the run's session too;
- a process that deliberately double-forks has asked to outlive its parent, and overriding that is
  a policy this module has no standing to make.

``grep -rn "start_new_session\\|setsid\\|os.fork\\|daemon" src/`` matches only this file, so no
stage in this repository escapes its group today; the bound is stated for the stage someone adds
later. It is pinned as stated by three tests in ``tests/test_daily_forecast.py``:
``test_a_timeout_returns_promptly_even_when_a_descendant_left_the_group_holding_the_pipe``,
``test_a_stage_that_exits_leaving_the_pipe_held_is_not_waited_for_past_its_own_exit`` and
``test_an_escaped_descendant_is_left_running_and_that_is_the_stated_bound``.

What the runner does about the escaped descendant is stop *waiting* for it: once the stage's own
process has exited and the pipe has been idle for ``_POLL_SECONDS``, reading ends and the stage is
returned. That applies after a timeout and after an ordinary exit alike. The cost is that output
such an orphan writes after that point is not captured (a same-group descendant is swept, so its
output stops too). Before this, such a descendant held the read open until it exited -- 6.05 s for
a 0.4 s timeout in a probe, and without bound for a stage that exited by itself, where no timeout
applied at all.

Two more things the same bound applies to:

- an **interrupt** (``KeyboardInterrupt``) or any other ``BaseException`` while a stage runs --
  from the moment the child exists, not from the moment the read loop starts -- kills the stage's
  group, reaps the stage, and then propagates. The stage is in its own session, which is why the
  terminal's Ctrl-C does not reach it and why this has to be done here. A **SIGTERM** to ``run``
  is such an exception only because ``volab.daily.__main__`` converts it to ``SystemExit`` for the
  length of a run (``_sigterm_unwinds``); this module installs no handler, and a caller that does
  not will see the default disposition end the process with the stage still running. A
  ``SIGKILL`` to ``run`` cannot be handled by anything. Only the process group is killed, so the
  escaped-descendant bound above applies on this path too.
- **a run lock keeps a second ``run`` out, and a killed run cannot wedge it** (``run_lock``). It is
  ``flock(LOCK_EX | LOCK_NB)`` on a file that is never unlinked, and the second run **refuses and
  does not wait**: ``LockHeld`` is raised at once, and the CLI turns it into a distinct exit code
  without having written a start line, because a start with no completion would claim an
  abandoned run for a run that never started a stage. The kernel drops a ``flock`` when the last
  descriptor on the open file description closes, which happens when the holder dies however it
  dies -- SIGKILL included -- so the file left behind is inert: the next run opens it and takes the
  lock. There is no staleness test, no timeout and no pid check. The pid written into the file is
  for a person looking at a held lock and **decides nothing**. The descriptor is opened with
  ``os.open``, which is non-inheritable, so a stage never holds the run's lock open. What the lock
  does not cover: a ``SIGKILL``ed ``run`` releases it while its stage, in its own session, may
  still be running, so the next run can start a stage beside that orphan.

**This module reads no wall clock and records nothing.** It never asks what the date or time is
(nothing it returns depends on either); the only time it uses is a *duration*, as a
``threading.Timer`` and a ``select`` poll interval. It does not write the run record or any
ledger and imports no sink; ``StageRun.as_result`` produces the ``StageResult`` -- verdict, bounded
output, dropped-byte count and timed-out flag -- that the caller hands to ``volab.daily.runlog``,
which writes all of it into the completion line.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import select
import signal
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from volab.daily import Stage
from volab.daily.runlog import StageResult

# 16 KiB: a screenful of traceback is a few hundred bytes and a progress bar's worth of ordinary
# output is a few thousand, so this keeps the reason a stage failed with room to spare while a
# daily record of six stages stays under 100 KiB however much any one of them prints.
OUTPUT_BOUND_BYTES = 16 * 1024

_CHUNK_BYTES = 64 * 1024

# How long the pipe must be idle, after the stage's own process has exited, before reading stops
# and the stage is returned although something still holds the pipe open. See the module docstring.
_POLL_SECONDS = 0.1

# Two hours per stage. Unmeasured: the real stages have not been timed unattended, and this is a
# bound on a hang rather than an estimate of a run, so it errs long -- a false timeout abandons
# a day's work, a late one costs a morning.
STAGE_TIMEOUT_SECONDS = 2 * 60 * 60

# What a killed stage is recorded as exiting with: 124, as ``timeout(1)`` reports. Not the
# child's -9, which a stage could also have earned by being OOM-killed; ``timed_out`` is what
# distinguishes the two.
TIMED_OUT_EXIT_CODE = 124


class LockHeld(Exception):
    """Another run holds the lock. Carries the lock path."""


@contextlib.contextmanager
def run_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive ``flock`` on ``path`` for the ``with`` body; raise ``LockHeld`` if held.

    Never waits. The file is created if absent and **never unlinked**: unlinking would let two
    processes lock two inodes under one name. ``flock`` and not ``lockf``, because POSIX record
    locks are dropped by *any* close of *any* descriptor to the file in the process. The kernel
    releases the lock when the descriptor closes -- here, or when the process dies -- so a file
    left behind by a killed run is inert and the next call takes it. The pid written into it is
    for a person to read and is **not consulted** by anything.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)  # non-inheritable: stages lack it
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockHeld(path) from None
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode())
        yield
    finally:
        os.close(descriptor)


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


def _sweep_the_group(pid: int) -> None:
    """SIGKILL whatever is still in the process group ``pid`` led, after that leader is reaped.

    The kernel links a same-group descendant to the stage; it cannot link one that left the group,
    which this does not reach (see the module docstring).

    **The suppressed set here and in ``_run_one``'s ``except BaseException`` must stay in step.**
    This one suppressed ``PermissionError`` from the start -- a group whose only member is a stage
    that has exited and not yet been reaped refuses the signal -- while the handler three lines
    above that ``raise`` suppressed only ``ProcessLookupError``, so an interrupt arriving in that
    window came out as a ``PermissionError`` and the CLI exited 1 with a traceback instead of
    ``TERMINATED_EXIT_CODE``. Same knowledge, two functions, one written later than the other:
    R151's duplication-drifts ruling, showing up in error handling rather than in an enumerated
    set. A third signalling site belongs in here, not beside the others.
    """
    group = -pid  # ``kill`` with a negative pid signals a group; spelt apart from the timeout's
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(group, signal.SIGKILL)


def _close_reap_and_sweep(process: subprocess.Popen[bytes]) -> None:
    """Close the stage's pipe, reap it, then sweep its group, each attempted whatever the last did.

    Nested ``finally`` blocks, so an interrupt raised by one step is propagated only after the
    later steps have run. If the reap itself is the step interrupted, the sweep that follows kills
    the group (the leader too, if it is still alive) and one further ``wait`` then reaps it; an
    interrupt that lands in *that* ``wait`` is not defended against.
    """
    try:
        if process.stdout is not None:
            process.stdout.close()
    finally:
        try:
            process.wait()
        finally:
            _sweep_the_group(process.pid)
            if process.returncode is None:  # the first ``wait`` was interrupted
                process.wait()


def _run_one(stage: Stage, timeout_seconds: float) -> StageRun:
    """Run ``stage`` in a fresh interpreter and return its verdict and output tail.

    Killed, and reported as timed out, if it is still running after ``timeout_seconds``.
    """
    tail: deque[bytes] = deque()
    kept = 0
    total = 0
    command = [sys.executable, "-m", stage.module, *stage.argv]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        start_new_session=True,  # its own process group, so the timeout can kill the whole tree
    )
    # Not ``with Popen(...)``: its ``__exit__`` waits for the child with no timeout for anything but
    # a ``KeyboardInterrupt``, so a ``SystemExit`` landing before the read loop began would block
    # until the stage finished. This ``try`` starts at the first line after the child exists.
    try:
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
        try:
            timer.start()
            descriptor = process.stdout.fileno()
            while True:
                ready, _, _ = select.select([descriptor], [], [], _POLL_SECONDS)
                if not ready:
                    if process.poll() is not None:
                        break  # the stage has exited; something else holds the pipe
                    continue
                chunk = os.read(descriptor, _CHUNK_BYTES)
                if not chunk:
                    break
                tail.append(chunk)
                kept += len(chunk)
                total += len(chunk)
                while kept - len(tail[0]) >= OUTPUT_BOUND_BYTES:
                    kept -= len(tail.popleft())
            exit_code = process.wait()
        finally:
            # Why this ``try`` scopes the read loop rather than the whole function: ``expired`` is
            # read just below, and a timer still able to fire could set it between the loop ending
            # and that read -- marking a stage timed out that in fact exited on its own. Cancelling
            # and joining here makes the flag quiescent before anything reads it, and keeps the
            # timeout's SIGKILL from racing the kill in the ``except`` below.
            timer.cancel()
            if timer.is_alive():  # not yet started if the ``start`` itself was interrupted
                timer.join()
        timed_out = expired.is_set()
        if timed_out:
            exit_code = TIMED_OUT_EXIT_CODE
    except BaseException:
        # An interrupt or a SIGTERM-turned-``SystemExit`` reaches this process and not the stage,
        # which is in its own session. Kill its group and reap it before unwinding, or the stage
        # outlives the run.
        # ``PermissionError`` as well: a group whose only member is a stage that has exited and
        # not yet been reaped refuses the signal, and it must not replace the interrupt.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(-process.pid, signal.SIGKILL)
        raise
    finally:
        _close_reap_and_sweep(process)
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
