"""The stage runner: each stage a fresh interpreter, its exit code its verdict, its output bounded.

**No test here runs the pipeline.** ``volab.ingest`` writes tracked vendor payloads and reaches the
network, and three of the six stages append to a reported ledger, so every stage below is a probe
module this file writes into ``tmp_path`` and puts on the child's ``PYTHONPATH``. A probe is a real
``python -m <module>`` -- the runner can express nothing else -- which is why it is a file and not
a ``-c`` string. ``runner`` itself has no path to a run record; the ``run`` verb tests at the end
of the file write one, always under ``tmp_path`` and always with ``LIVE_STAGES`` replaced by probes.
"""

from __future__ import annotations

import _thread
import contextlib
import datetime as dt
import fcntl
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path

import pytest

import volab.daily as daily_package
import volab.daily.__main__ as daily_cli
import volab.daily.runner as runner
from volab.daily import Stage
from volab.daily.runlog import (
    StageResult,
    completed_dates,
    incomplete_run_ids,
    run_log_path,
)
from volab.daily.runner import OUTPUT_BOUND_BYTES, StageRun, run_stages

_PROBES = {
    # Prints the child's pid, so two stages can be shown to be two processes.
    "probe_pid": "import os\nprint(os.getpid())\n",
    # Prints what the child was started as: the interpreter and the arguments it received.
    "probe_argv": "import sys\nprint(sys.executable)\nprint(sys.argv[1:])\n",
    # Exits with the status it is given and says so on both streams.
    "probe_exit": (
        "import sys\nprint('out')\nprint('err', file=sys.stderr)\nsys.exit(int(sys.argv[1]))\n"
    ),
    # Proves it ran by creating the file named in argv.
    "probe_marker": "import pathlib, sys\npathlib.Path(sys.argv[1]).write_text('ran')\n",
    # Writes argv[1] bytes of 'x' in 64 KiB pieces, then one line to stderr, flushed in order.
    "probe_flood": (
        "import sys\n"
        "left = int(sys.argv[1])\n"
        "while left:\n"
        "    piece = min(left, 65536)\n"
        "    sys.stdout.write('x' * piece)\n"
        "    left -= piece\n"
        "sys.stdout.flush()\n"
        "print('\\nTHE-LAST-LINE', file=sys.stderr, flush=True)\n"
    ),
    # Output that is not UTF-8 at all.
    "probe_bytes": "import sys\nsys.stdout.buffer.write(b'\\xff\\xfe before \\x80 after\\n')\n",
    # Output that is exactly the bound, no more.
    "probe_exact": f"import sys\nsys.stdout.write('y' * {OUTPUT_BOUND_BYTES})\n",
    # Writes its pid to argv[1], says so, then sleeps far past any timeout a test passes.
    "probe_sleep": (
        "import os, pathlib, sys, time\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "print('before sleep', flush=True)\n"
        "time.sleep(60)\n"
    ),
    # As probe_sleep but silent, so closing its pipe (which would give a printing stage a
    # BrokenPipeError and end it early) cannot make it look as if it had been stopped.
    "probe_sleep_silent": (
        "import os, pathlib, sys, time\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    ),
    # As probe_sleep, but through a grandchild that also sleeps; argv[1] gets both pids.
    "probe_sleep_tree": (
        "import os, pathlib, subprocess, sys, time\n"
        "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()} {grandchild.pid}')\n"
        "time.sleep(60)\n"
    ),
    # Leaves a plain same-group sleeper running (no ``start_new_session``): writes its pid to
    # argv[1], then exits 0 without waiting for it.
    "probe_leaves_a_child": (
        "import pathlib, subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
        "                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        "print('leaving a child behind', flush=True)\n"
    ),
    # Tries to take the run lock named in argv[1], and exits 0 only if it could not.
    "probe_lock": (
        "import fcntl, os, sys\n"
        "descriptor = os.open(sys.argv[1], os.O_RDWR)\n"
        "try:\n"
        "    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except BlockingIOError:\n"
        "    print('lock refused')\n"
        "    sys.exit(0)\n"
        "print('lock taken')\n"
        "sys.exit(7)\n"
    ),
    # The ordering witness. argv[1] is the run-log path. Exits 0 only if that file already holds
    # exactly one line and it is a start line: not absent, not empty, not already completed.
    "probe_needs_start": (
        "import json, pathlib, sys\n"
        "path = pathlib.Path(sys.argv[1])\n"
        "if not path.exists():\n"
        "    print('no run log yet')\n"
        "    sys.exit(3)\n"
        "kinds = [json.loads(line)['kind'] for line in path.read_text().splitlines()]\n"
        "print('kinds in the log:', kinds)\n"
        "sys.exit(0 if kinds == ['start'] else 4)\n"
    ),
}


@pytest.fixture(autouse=True)
def probes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "probes"
    directory.mkdir()
    for name, source in _PROBES.items():
        (directory / f"{name}.py").write_text(source, encoding="utf-8")
    inherited = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH", str(directory) if not inherited else f"{directory}{os.pathsep}{inherited}"
    )
    return directory


def _stage(name: str, module: str, *argv: str) -> Stage:
    return Stage(name=name, module=module, argv=tuple(argv))


# --- item 1: a fresh interpreter per stage ----------------------------------------------------


def test_each_stage_runs_in_its_own_interpreter() -> None:
    runs = run_stages((_stage("a", "probe_pid"), _stage("b", "probe_pid")))

    pids = [int(run.output.strip()) for run in runs]
    assert len(pids) == 2
    assert len({*pids, os.getpid()}) == 3, (pids, os.getpid())


def test_a_stage_is_started_as_the_current_interpreter_dash_m_with_its_argv() -> None:
    (run,) = run_stages((_stage("a", "probe_argv", "one", "--two"),))

    executable, argv = run.output.splitlines()
    assert executable == sys.executable
    assert argv == "['one', '--two']"


def test_a_stage_module_is_never_imported_into_the_calling_process() -> None:
    """A stage run by importing it would leave it in ``sys.modules``; a child process cannot."""
    run_stages((_stage("a", "probe_pid"),))

    assert "probe_pid" not in sys.modules


# --- item 2: the exit code is the verdict -----------------------------------------------------


def test_a_non_zero_exit_stops_the_run_and_is_recorded_against_that_stage(tmp_path: Path) -> None:
    marker = tmp_path / "third-stage-ran"

    runs = run_stages(
        (
            _stage("first", "probe_exit", "0"),
            _stage("second", "probe_exit", "3"),
            _stage("third", "probe_marker", str(marker)),
        )
    )

    assert [(run.name, run.exit_code) for run in runs] == [("first", 0), ("second", 3)]
    assert not marker.exists()


def test_a_run_of_zero_exits_reports_every_stage_in_order(tmp_path: Path) -> None:
    marker = tmp_path / "last-stage-ran"

    runs = run_stages((_stage("a", "probe_exit", "0"), _stage("b", "probe_marker", str(marker))))

    assert [(run.name, run.exit_code) for run in runs] == [("a", 0), ("b", 0)]
    assert marker.read_text(encoding="utf-8") == "ran"


def test_a_module_that_does_not_exist_is_a_failed_stage_and_not_an_exception() -> None:
    (run,) = run_stages((_stage("ghost", "volab_no_such_stage_module"),))

    assert run.name == "ghost"
    assert run.exit_code != 0
    assert "volab_no_such_stage_module" in run.output


def test_no_stages_is_no_runs() -> None:
    assert run_stages(()) == ()


def test_a_stage_run_converts_to_the_run_records_stage_result() -> None:
    (run,) = run_stages((_stage("only", "probe_exit", "7"),))

    assert run.as_result() == StageResult(
        name="only", exit_code=7, output="out\nerr\n", dropped_bytes=0, timed_out=False
    )


def test_the_stage_result_carries_the_dropped_byte_count_and_the_marker_with_it() -> None:
    (run,) = run_stages((_stage("flood", "probe_flood", str(2 * OUTPUT_BOUND_BYTES)),))

    result = run.as_result()
    assert result.dropped_bytes == run.dropped_bytes > 0
    assert result.output == run.output
    assert result.output.startswith(f"[{run.dropped_bytes} bytes")


def test_both_output_streams_are_captured_in_the_order_they_were_written() -> None:
    (run,) = run_stages((_stage("a", "probe_exit", "0"),))

    assert run.output.splitlines() == ["out", "err"]


def test_output_that_is_not_utf8_does_not_turn_a_stage_into_a_traceback() -> None:
    (run,) = run_stages((_stage("a", "probe_bytes"),))

    assert run.exit_code == 0
    assert "before" in run.output
    assert "after" in run.output


# --- item 3: captured output is bounded -------------------------------------------------------


def test_the_output_bound_is_a_named_constant_of_sixteen_kibibytes() -> None:
    assert runner.OUTPUT_BOUND_BYTES == 16 * 1024


def test_output_over_the_bound_is_cut_to_it_with_a_marker_stating_what_was_dropped() -> None:
    total = 3 * OUTPUT_BOUND_BYTES

    (run,) = run_stages((_stage("flood", "probe_flood", str(total)),))

    written = total + len("\nTHE-LAST-LINE\n")
    assert run.dropped_bytes == written - OUTPUT_BOUND_BYTES
    marker, _, kept = run.output.partition("\n")
    assert str(run.dropped_bytes) in marker
    assert len(kept.encode()) == OUTPUT_BOUND_BYTES
    # The tail is what survives, because that is where a failing stage says why.
    assert kept.endswith("THE-LAST-LINE\n")


def test_output_under_the_bound_is_returned_untouched() -> None:
    (run,) = run_stages((_stage("a", "probe_exit", "0"),))

    assert run.output == "out\nerr\n"
    assert run.dropped_bytes == 0


def test_output_of_exactly_the_bound_is_not_truncated() -> None:
    (run,) = run_stages((_stage("a", "probe_exact"),))

    assert run.dropped_bytes == 0
    assert run.output == "y" * OUTPUT_BOUND_BYTES


def test_output_one_byte_over_the_bound_drops_exactly_one_byte() -> None:
    (run,) = run_stages((_stage("a", "probe_flood", str(OUTPUT_BOUND_BYTES + 1 - 15)),))

    # 15 is the length of "\nTHE-LAST-LINE\n", so the stage wrote bound + 1 bytes in all.
    assert run.dropped_bytes == 1


def test_a_stage_that_prints_megabytes_is_not_held_in_memory() -> None:
    """Twenty MiB through the runner, with the peak of Python allocations pinned well below it.

    The bound on the *result* would be met by a runner that buffered everything and sliced at the
    end; this is what distinguishes it, and it is why the runner reads in chunks.
    """
    tracemalloc.start()
    try:
        (run,) = run_stages((_stage("flood", "probe_flood", str(20 * 1024 * 1024)),))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert run.dropped_bytes > 20 * 1024 * 1024 - OUTPUT_BOUND_BYTES
    assert peak < 2 * 1024 * 1024, peak


def test_stage_run_is_what_the_runner_returns() -> None:
    (run,) = run_stages((_stage("a", "probe_exit", "0"),))

    assert isinstance(run, StageRun)


# --- item 4: a stage that never ends is a failed stage ----------------------------------------

_SHORT = 0.4  # seconds. The probe sleeps for sixty; nothing here waits for it.


def _gone(pid: int) -> bool:
    """Whether ``pid`` is gone, allowing the kernel a few tens of milliseconds to say so."""
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def test_the_default_stage_timeout_is_a_named_constant_and_is_finite() -> None:
    assert runner.STAGE_TIMEOUT_SECONDS == 2 * 60 * 60
    assert runner.TIMED_OUT_EXIT_CODE != 0


def test_a_stage_past_its_timeout_is_a_failed_stage_and_the_run_stops(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    marker = tmp_path / "after-ran"

    started = time.monotonic()
    runs = run_stages(
        (
            _stage("hang", "probe_sleep", str(pid_file)),
            _stage("after", "probe_marker", str(marker)),
        ),
        timeout_seconds=_SHORT,
    )
    elapsed = time.monotonic() - started

    (run,) = runs
    assert run.name == "hang"
    assert run.timed_out
    assert run.exit_code == runner.TIMED_OUT_EXIT_CODE != 0
    assert "before sleep" in run.output  # what it printed before it was killed is kept
    assert not marker.exists()
    assert elapsed < 5, elapsed  # sixty seconds of sleeping was not waited out


def test_a_timed_out_stage_is_actually_killed_not_abandoned(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"

    run_stages((_stage("hang", "probe_sleep", str(pid_file)),), timeout_seconds=_SHORT)

    assert _gone(int(pid_file.read_text(encoding="utf-8")))


def test_a_timeout_kills_the_stages_own_children_too(tmp_path: Path) -> None:
    """A grandchild holding the output pipe would otherwise keep the read loop waiting."""
    pid_file = tmp_path / "pids"

    started = time.monotonic()
    run_stages((_stage("tree", "probe_sleep_tree", str(pid_file)),), timeout_seconds=_SHORT)
    elapsed = time.monotonic() - started

    # Without the elapsed bound this passes by waiting out the grandchild's own sixty seconds,
    # after which it is gone of natural causes: the mutation probe found exactly that.
    assert elapsed < 5, elapsed
    child, grandchild = (int(pid) for pid in pid_file.read_text(encoding="utf-8").split())
    assert _gone(child)
    assert _gone(grandchild)


def test_a_same_group_child_left_by_a_finished_stage_is_killed_with_the_group(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "pid"

    started = time.monotonic()
    try:
        (run,) = run_stages((_stage("leaver", "probe_leaves_a_child", str(pid_file)),))
        elapsed = time.monotonic() - started
        sleeper = int(pid_file.read_text(encoding="utf-8"))
        gone = _gone(sleeper)
    finally:
        if pid_file.exists():
            _kill_if_alive(int(pid_file.read_text(encoding="utf-8")))

    assert run.exit_code == 0
    assert elapsed < 1, elapsed
    assert gone


def _is_a_zombie(pid: int) -> bool:
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return state.stdout.strip().startswith("Z")


def test_an_interrupt_landing_after_the_stage_exited_but_before_it_was_reaped_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signalling a group led by an exited, unreaped stage raises ``PermissionError``.

    The interrupt handler's kill of the group must not let that replace the interrupt: the CLI
    promises ``TERMINATED_EXIT_CODE`` for a SIGTERM and not a traceback.
    """
    started: list[subprocess.Popen[bytes]] = []

    class Recording(subprocess.Popen):  # type: ignore[type-arg]
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[call-overload]
            started.append(self)

    real_select = select.select

    def select_once_the_stage_has_exited_unreaped(*args: object) -> object:
        (process,) = started
        # Nothing here reaps it, so ``ps`` reporting a zombie is the state in question: exited and
        # not waited for. (``os.waitid`` with ``WNOWAIT`` would say so too, but darwin lacks it.)
        deadline = time.monotonic() + 5
        while not _is_a_zombie(process.pid):
            assert time.monotonic() < deadline, "the stage never exited"
            time.sleep(0.01)
        raise KeyboardInterrupt

    monkeypatch.setattr(runner.subprocess, "Popen", Recording)
    monkeypatch.setattr(runner.select, "select", select_once_the_stage_has_exited_unreaped)
    began = time.monotonic()
    try:
        with pytest.raises(KeyboardInterrupt):
            run_stages((_stage("a", "probe_exit", "0"),), timeout_seconds=_SHORT)
    finally:
        monkeypatch.setattr(runner.select, "select", real_select)
        for process in started:
            process.kill()
            process.wait()
    assert time.monotonic() - began < 5


def test_a_second_interrupt_inside_the_cleanup_still_reaps_the_stage_and_closes_its_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first interrupt lands in the read loop's ``wait``; the second in the cleanup's own."""
    started: list[subprocess.Popen[bytes]] = []

    class InterruptedTwice(subprocess.Popen):  # type: ignore[type-arg]
        waits = 0

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[call-overload]
            started.append(self)

        def wait(self, timeout: float | None = None) -> int:
            self.waits += 1
            if self.waits <= 2:
                raise KeyboardInterrupt
            return super().wait(timeout)

    monkeypatch.setattr(runner.subprocess, "Popen", InterruptedTwice)
    try:
        with pytest.raises(KeyboardInterrupt):
            run_stages((_stage("a", "probe_exit", "0"),), timeout_seconds=_SHORT)
        (process,) = started
        assert process.returncode is not None  # reaped: not a zombie, and no ResourceWarning
        assert process.stdout is not None and process.stdout.closed
    finally:
        for process in started:
            process.kill()
            InterruptedTwice.waits = 99
            process.wait()


def test_the_timeout_itself_kills_the_group_not_only_the_sweep_after_the_reap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The timeout's own kill, isolated from the sweep that would otherwise stand in for it.

    ``test_a_timeout_kills_the_stages_own_children_too`` passes whether the group dies at the
    deadline or at the reap, because ``_sweep_the_group`` kills it either way -- so on its own it
    stopped witnessing the timeout's ``killpg``, and deleting that call would leave it green.
    With the sweep monkeypatched out, only the deadline's kill can reach the grandchild.

    **The ``finally`` is load-bearing, not tidiness.** The sweep is removed for the duration, so
    it is the only thing left that reaps the tree -- and the case this test exists to catch is
    exactly the one where the timeout fails to. Without it a regression leaks a sleeping
    grandchild per run. It kills the group rather than the two pids, because a descendant of the
    grandchild would not be in the file.
    """
    pid_file = tmp_path / "pids"
    monkeypatch.setattr(runner, "_sweep_the_group", lambda pid: None)
    leader = None

    try:
        started = time.monotonic()
        run_stages((_stage("tree", "probe_sleep_tree", str(pid_file)),), timeout_seconds=_SHORT)
        elapsed = time.monotonic() - started

        assert elapsed < 5, elapsed
        leader, grandchild = (int(pid) for pid in pid_file.read_text(encoding="utf-8").split())
        assert _gone(leader)
        assert _gone(grandchild)
    finally:
        if leader is None and pid_file.exists():
            leader = int(pid_file.read_text(encoding="utf-8").split()[0])
        if leader is not None:
            # The same shape as ``_sweep_the_group``: PermissionError because an exited-but-
            # unreaped group leader refuses the signal.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(-leader, signal.SIGKILL)


def test_a_stage_that_finishes_inside_its_timeout_is_not_marked_timed_out() -> None:
    (run,) = run_stages((_stage("quick", "probe_exit", "0"),), timeout_seconds=30)

    assert not run.timed_out
    assert run.exit_code == 0


def test_a_stage_that_exits_with_the_timeout_code_by_itself_is_not_marked_timed_out() -> None:
    (run,) = run_stages(
        (_stage("coincidence", "probe_exit", str(runner.TIMED_OUT_EXIT_CODE)),), timeout_seconds=30
    )

    assert run.exit_code == runner.TIMED_OUT_EXIT_CODE
    assert not run.timed_out


# --- the ``run`` verb -------------------------------------------------------------------------

A_SESSION_IN_DEV = dt.date(2025, 12, 30)
A_HOLIDAY_IN_DEV = dt.date(2025, 12, 25)
A_SESSION_PAST_DEV_END = dt.date(2026, 11, 4)
_START = dt.datetime(2026, 11, 5, 9, 30, tzinfo=dt.UTC)
_END = dt.datetime(2026, 11, 5, 9, 45, tzinfo=dt.UTC)


def _clock(*instants: dt.datetime) -> Callable[[], dt.datetime]:
    """A clock that yields exactly these instants and raises if asked for another."""
    remaining = iter(instants)
    return lambda: next(remaining)


@pytest.fixture(autouse=True)
def no_real_git_and_no_real_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LIVE_STAGES`` is the real pipeline until a test replaces it, so replace it for all.

    The first real stage is ``volab.ingest backfill``, which reaches the network and writes the
    tracked ``data/vendor_payloads/``. An empty tuple here means a test that forgets to choose its
    stages runs nothing rather than that. ``git_state`` is replaced because it shells out to git
    and its answer is not under test.
    """
    monkeypatch.setattr(daily_package, "LIVE_STAGES", ())
    monkeypatch.setattr(daily_cli, "git_state", lambda scope=None: ("abc1234", False))


def _use_stages(monkeypatch: pytest.MonkeyPatch, *stages: Stage) -> None:
    monkeypatch.setattr(daily_package, "LIVE_STAGES", stages)


def _records(root: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in run_log_path(root).read_text(encoding="utf-8").splitlines()
    ]


def _run(root: Path, date: dt.date, **kwargs: object) -> int:
    return daily_cli.main(["run", "--as-of", date.isoformat()], root=root, **kwargs)  # type: ignore[arg-type]


def test_run_is_a_verb_of_the_cli(tmp_path: Path) -> None:
    assert _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END)) == 0


def test_a_run_of_good_stages_writes_one_start_and_one_done_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_stages(monkeypatch, _stage("a", "probe_exit", "0"), _stage("b", "probe_exit", "0"))

    code = _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END))

    assert code == 0
    start, complete = _records(tmp_path)
    assert start["kind"] == "start"
    assert start["started_at"] == _START.isoformat()
    assert start["git_sha"] == "abc1234"
    assert start["git_dirty"] is False
    assert start["as_of"] == A_SESSION_IN_DEV.isoformat()
    assert complete["kind"] == "complete"
    assert complete["ref"] == start["id"]
    assert complete["outcome"] == "done"
    assert complete["ended_at"] == _END.isoformat()
    assert [stage["name"] for stage in complete["stages"]] == ["a", "b"]  # type: ignore[attr-defined]
    assert completed_dates(run_log_path(tmp_path)) == frozenset({A_SESSION_IN_DEV})


def test_a_failing_stage_is_recorded_with_what_it_printed_and_the_run_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "third-ran"
    _use_stages(
        monkeypatch,
        _stage("first", "probe_exit", "0"),
        _stage("second", "probe_exit", "3"),
        _stage("third", "probe_marker", str(marker)),
    )

    code = _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END))

    assert code not in (0, 1)  # not a success, and not the code that means "refused"
    _, complete = _records(tmp_path)
    assert complete["outcome"] == "failed"
    first, second = complete["stages"]  # type: ignore[misc]
    assert (first["exit_code"], second["exit_code"]) == (0, 3)
    assert second["output"] == "out\nerr\n"
    assert second["dropped_bytes"] == 0
    assert second["timed_out"] is False
    assert not marker.exists()
    # A failed run has not done the day's work: the date is still available.
    assert completed_dates(run_log_path(tmp_path)) == frozenset()
    assert incomplete_run_ids(run_log_path(tmp_path)) == ()


def test_the_record_keeps_the_tail_and_the_dropped_count_of_a_flooding_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_stages(monkeypatch, _stage("flood", "probe_flood", str(3 * OUTPUT_BOUND_BYTES)))

    _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END))

    (stage,) = _records(tmp_path)[1]["stages"]  # type: ignore[misc]
    assert stage["dropped_bytes"] > 0
    assert stage["output"].startswith(f"[{stage['dropped_bytes']} bytes")
    assert stage["output"].endswith("THE-LAST-LINE\n")


def test_the_start_line_is_on_disk_before_the_first_stage_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The board's claim, witnessed by a stage that fails unless the claim is true.

    The probe is handed the run-log path in its argv and exits non-zero unless the file already
    holds exactly one line and it is a start line. A run that wrote the start after the stages
    leaves the probe seeing no file; one that wrote start and completion together leaves it seeing
    two lines. Either way the stage fails, the run is recorded ``failed`` and this test does too.
    """
    log = run_log_path(tmp_path)
    _use_stages(monkeypatch, _stage("witness", "probe_needs_start", str(log)))

    code = _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END))

    (stage,) = _records(tmp_path)[1]["stages"]  # type: ignore[misc]
    assert stage["output"] == "kinds in the log: ['start']\n"
    assert stage["exit_code"] == 0
    assert code == 0


def _pid_when_written(pid_file: Path, deadline_seconds: float = 10) -> int:
    """The pid a probe wrote to ``pid_file``, waiting for the whole number and not a half-write."""
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        with contextlib.suppress(FileNotFoundError, ValueError):
            return int(pid_file.read_text())
        time.sleep(0.02)
    raise AssertionError(f"{pid_file} was never written")


def _kill_if_alive(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def test_a_run_abandoned_mid_stage_leaves_a_start_with_no_completion_and_a_retryable_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pid_file = tmp_path / "pid"
    _use_stages(monkeypatch, _stage("a", "probe_sleep", str(pid_file)))

    def interrupt_once_the_stage_is_running() -> None:
        with contextlib.suppress(AssertionError):
            _pid_when_written(pid_file)
            _thread.interrupt_main()  # a genuine KeyboardInterrupt, with a genuine stage in flight

    interrupter = threading.Thread(target=interrupt_once_the_stage_is_running)
    began = time.monotonic()
    interrupter.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END))
        elapsed = time.monotonic() - began
    finally:
        interrupter.join()
        if pid_file.exists():
            _kill_if_alive(int(pid_file.read_text()))

    assert elapsed < 5, elapsed  # the stage sleeps sixty seconds; the unwind did not wait for it
    assert _gone(int(pid_file.read_text()))
    (start,) = _records(tmp_path)
    assert start["kind"] == "start"
    log = run_log_path(tmp_path)
    assert incomplete_run_ids(log) == (start["id"],)
    assert completed_dates(log) == frozenset()
    retry = daily_package.plan_for(
        daily_cli.load_settings(), A_SESSION_IN_DEV, completed=completed_dates(log)
    )
    assert retry.outcome == "work"


def test_a_stage_that_hangs_is_recorded_as_timed_out_and_failed_through_the_verb(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pid_file = tmp_path / "pid"
    _use_stages(monkeypatch, _stage("hang", "probe_sleep", str(pid_file)))
    monkeypatch.setattr(daily_cli, "STAGE_TIMEOUT_SECONDS", _SHORT)

    code = _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END))

    assert code not in (0, 1)
    _, complete = _records(tmp_path)
    assert complete["outcome"] == "failed"
    (stage,) = complete["stages"]  # type: ignore[misc]
    assert stage["timed_out"] is True
    assert stage["exit_code"] == runner.TIMED_OUT_EXIT_CODE
    assert "before sleep" in stage["output"]
    assert completed_dates(run_log_path(tmp_path)) == frozenset()


@pytest.mark.parametrize(
    ("date", "code", "word"),
    [
        (A_SESSION_PAST_DEV_END, 1, "refused"),
        (A_HOLIDAY_IN_DEV, 0, "no_session"),
    ],
    ids=["refused", "closed"],
)
def test_a_refused_or_closed_date_runs_nothing_and_leaves_no_file_behind(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    date: dt.date,
    code: int,
    word: str,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    marker = tmp_path / "ran"
    _use_stages(monkeypatch, _stage("a", "probe_marker", str(marker)))

    # An empty clock: a verb that asked the time for a plan it will not run would raise.
    assert _run(root, date, clock=_clock()) == code

    assert word in capsys.readouterr().out
    assert sorted(root.rglob("*")) == []
    assert not marker.exists()


def test_an_already_done_date_runs_nothing_and_leaves_the_log_byte_identical(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    marker = tmp_path / "ran"
    _use_stages(monkeypatch, _stage("a", "probe_marker", str(marker)))
    assert _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END)) == 0
    assert marker.exists()
    marker.unlink()
    log = run_log_path(tmp_path)
    before = log.read_bytes()

    assert _run(tmp_path, A_SESSION_IN_DEV, clock=_clock()) == 0

    assert "already_done" in capsys.readouterr().out
    assert log.read_bytes() == before
    assert not marker.exists()


# --- the run lock -----------------------------------------------------------------------------

_HOLDER = (
    "import fcntl, os, pathlib, sys, time\n"
    "descriptor = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
    "fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "pathlib.Path(sys.argv[2]).write_text('held')\n"
    "time.sleep(60)\n"
)


def _hold_the_lock(root: Path, marker: Path) -> subprocess.Popen[bytes]:
    """A real second process that holds ``root``'s run lock until it is killed.

    Returns only once the holder's marker file exists, which it writes after its ``flock``
    succeeded; if it never does, the test fails here rather than passing for the wrong reason.
    """
    lock = daily_cli._lock_path(run_log_path(root))
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen([sys.executable, "-c", _HOLDER, str(lock), str(marker)])
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert holder.poll() is None, "the holder exited before taking the lock"
            assert time.monotonic() < deadline, "the holder never took the lock"
            time.sleep(0.02)
    except BaseException:
        holder.kill()
        holder.wait()
        raise
    return holder


def _kill_and_reap(holder: subprocess.Popen[bytes]) -> None:
    holder.kill()
    holder.wait()


def test_a_second_run_refuses_while_another_holds_the_lock_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    marker = tmp_path / "ran"
    _use_stages(monkeypatch, _stage("a", "probe_marker", str(marker)))
    holder = _hold_the_lock(tmp_path, tmp_path / "held")
    try:
        code = _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END))
    finally:
        _kill_and_reap(holder)

    assert code == daily_cli.RUN_LOCKED_EXIT_CODE
    assert "locked" in capsys.readouterr().out
    assert not run_log_path(tmp_path).exists()
    assert not marker.exists()


def test_a_lock_left_behind_by_a_killed_run_does_not_block_the_next_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_stages(monkeypatch, _stage("a", "probe_exit", "0"))
    holder = _hold_the_lock(tmp_path, tmp_path / "held")
    holder.send_signal(signal.SIGKILL)
    holder.wait()
    assert daily_cli._lock_path(run_log_path(tmp_path)).exists()

    code = _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END))

    assert code == 0
    start, complete = _records(tmp_path)
    assert (start["kind"], complete["kind"], complete["outcome"]) == ("start", "complete", "done")


def test_the_lock_is_released_so_the_next_run_can_take_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_stages(monkeypatch, _stage("a", "probe_exit", "0"))
    other_day = dt.date(2025, 12, 31)

    assert _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END)) == 0
    assert _run(tmp_path, other_day, clock=_clock(_START, _END)) == 0


def test_a_stage_cannot_take_the_run_lock_while_the_run_holds_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock = daily_cli._lock_path(run_log_path(tmp_path))
    _use_stages(monkeypatch, _stage("probe", "probe_lock", str(lock)))

    code = _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END))

    (stage,) = _records(tmp_path)[1]["stages"]  # type: ignore[misc]
    assert stage["output"] == "lock refused\n"
    assert (stage["exit_code"], code) == (0, 0)


def test_a_run_that_takes_the_lock_leaves_the_date_done_and_the_lock_file_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_stages(monkeypatch, _stage("a", "probe_exit", "0"))
    lock = daily_cli._lock_path(run_log_path(tmp_path))

    assert _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END)) == 0

    assert completed_dates(run_log_path(tmp_path)) == frozenset({A_SESSION_IN_DEV})
    # Never unlinked: unlinking lets two processes hold locks on two inodes under one name.
    assert lock.exists()
    descriptor = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)  # and it is free again
    finally:
        os.close(descriptor)


# --- SIGTERM ----------------------------------------------------------------------------------

# Run in a child interpreter, because a SIGTERM with no handler kills the process it is sent to.
# argv: root, pid file, date. It refuses to call ``main`` unless every planned stage is a probe: a
# mis-patch here would otherwise run the real pipeline against this checkout.
_DRIVER = """\
import datetime as dt
import sys
from pathlib import Path

import volab.daily as package
import volab.daily.__main__ as cli
from volab.daily import Stage

root, pid_file, date = Path(sys.argv[1]), sys.argv[2], dt.date.fromisoformat(sys.argv[3])
package.LIVE_STAGES = (Stage(name="a", module="probe_sleep", argv=(pid_file,)),)
cli.git_state = lambda scope=None: ("abc1234", False)
planned = package.plan_for(cli.load_settings(), date, completed=frozenset()).stages
if not planned or not all(stage.module.startswith("probe_") for stage in planned):
    print("refusing: a planned stage is not a probe", [s.module for s in planned], file=sys.stderr)
    sys.exit(99)
instants = iter(
    [dt.datetime(2026, 11, 5, 9, 30, tzinfo=dt.UTC), dt.datetime(2026, 11, 5, 9, 45, tzinfo=dt.UTC)]
)
sys.exit(cli.main(["run", "--as-of", date.isoformat()], root=root, clock=lambda: next(instants)))
"""


def test_a_sigterm_during_a_stage_kills_the_stage_and_leaves_a_retryable_date(
    tmp_path: Path,
) -> None:
    driver_file = tmp_path / "driver.py"
    driver_file.write_text(_DRIVER, encoding="utf-8")
    pid_file = tmp_path / "pid"
    root = tmp_path / "root"
    driver = subprocess.Popen(
        [sys.executable, str(driver_file), str(root), str(pid_file), A_SESSION_IN_DEV.isoformat()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    stage_pid: int | None = None
    try:
        stage_pid = _pid_when_written(pid_file)
        driver.send_signal(signal.SIGTERM)
        _, error = driver.communicate(timeout=10)
        assert driver.returncode == 128 + signal.SIGTERM, error.decode()  # as a shell reports it
        assert _gone(stage_pid)
        assert daily_cli.TERMINATED_EXIT_CODE == 128 + signal.SIGTERM
    finally:
        if stage_pid is not None:
            _kill_if_alive(stage_pid)
        if driver.poll() is None:
            driver.kill()
            driver.communicate()

    # The promise the two-line run log exists to keep: started, never completed, so retryable.
    (start,) = _records(root)
    assert start["kind"] == "start"
    log = run_log_path(root)
    assert completed_dates(log) == frozenset()
    assert incomplete_run_ids(log) == (start["id"],)
    retry = daily_package.plan_for(
        daily_cli.load_settings(), A_SESSION_IN_DEV, completed=completed_dates(log)
    )
    assert retry.outcome == "work"


def test_the_run_lock_is_released_by_a_sigterm_unwind_so_the_next_run_proceeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """In this process, not a child: a child's exit would release the lock whatever the unwind did.

    ``flock`` conflicts between two open file descriptions even inside one process, so a
    descriptor the unwind failed to close would make the second run refuse.
    """
    pid_file = tmp_path / "pid"
    _use_stages(monkeypatch, _stage("a", "probe_sleep", str(pid_file)))

    def terminate_once_the_stage_is_running() -> None:
        with contextlib.suppress(AssertionError):
            _pid_when_written(pid_file)
            os.kill(os.getpid(), signal.SIGTERM)

    terminator = threading.Thread(target=terminate_once_the_stage_is_running)
    terminator.start()
    try:
        with pytest.raises(SystemExit) as exited:
            _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END))
    finally:
        terminator.join()
        if pid_file.exists():
            _kill_if_alive(int(pid_file.read_text()))
    assert exited.value.code == daily_cli.TERMINATED_EXIT_CODE
    assert _gone(int(pid_file.read_text()))

    _use_stages(monkeypatch, _stage("a", "probe_exit", "0"))
    assert _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END)) == 0
    assert completed_dates(run_log_path(tmp_path)) == frozenset({A_SESSION_IN_DEV})


def test_the_sigterm_handler_is_restored_after_a_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_stages(monkeypatch, _stage("a", "probe_exit", "0"))
    before = signal.getsignal(signal.SIGTERM)

    assert _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END)) == 0

    assert signal.getsignal(signal.SIGTERM) is before


def test_an_exit_landing_before_the_read_loop_does_not_wait_for_the_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pid_file = tmp_path / "pid"

    class ExitsAtOnce:
        def __init__(self, *_: object) -> None:
            raise SystemExit(143)

    monkeypatch.setattr(runner.threading, "Timer", ExitsAtOnce)
    began = time.monotonic()
    try:
        with pytest.raises(SystemExit):
            run_stages((_stage("a", "probe_sleep_silent", str(pid_file)),))
        elapsed = time.monotonic() - began
    finally:
        if pid_file.exists():
            _kill_if_alive(int(pid_file.read_text()))

    # The stage sleeps sixty seconds. ``Popen.__exit__`` would have waited for all of them.
    assert elapsed < 5, elapsed


# --- the re-check under the lock --------------------------------------------------------------


def test_a_date_completed_after_the_plan_was_read_runs_nothing_and_says_completed_elsewhere(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    marker = tmp_path / "ran"
    _use_stages(monkeypatch, _stage("a", "probe_marker", str(marker)))
    lock = daily_cli._lock_path(run_log_path(tmp_path))
    reads: list[bool] = []

    def completed_dates_that_changes_its_answer(log: Path) -> frozenset[dt.date]:
        # The plan reads the log first and says ``work``; the re-check then says done. The second
        # read must happen with the run lock held, or the re-check closes nothing.
        reads.append(True)
        if len(reads) == 1:
            return frozenset()
        descriptor = os.open(lock, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
        return frozenset({A_SESSION_IN_DEV})

    monkeypatch.setattr(daily_cli, "completed_dates", completed_dates_that_changes_its_answer)

    code = _run(tmp_path, A_SESSION_IN_DEV, clock=_clock(_START, _END))

    assert code == 0
    assert len(reads) == 2
    out = capsys.readouterr().out
    assert "completed_elsewhere" in out
    assert "already_done" not in out  # that word is the already-done verb's witness alone
    assert "waited" not in out  # the run refuses; it never waits
    assert not run_log_path(tmp_path).exists()
    assert not marker.exists()
