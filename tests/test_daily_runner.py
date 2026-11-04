"""The stage runner: each stage a fresh interpreter, its exit code its verdict, its output bounded.

**No test here runs the pipeline.** ``volab.ingest`` writes tracked vendor payloads and reaches the
network, and three of the six stages append to a reported ledger, so every stage below is a probe
module this file writes into ``tmp_path`` and puts on the child's ``PYTHONPATH``. A probe is a real
``python -m <module>`` -- the runner can express nothing else -- which is why it is a file and not
a ``-c`` string. Nothing in this file writes a run record: ``runner`` has no path to one.
"""

from __future__ import annotations

import os
import sys
import tracemalloc
from pathlib import Path

import pytest

import volab.daily.runner as runner
from volab.daily import Stage
from volab.daily.runlog import StageResult
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

    assert run.as_result() == StageResult(name="only", exit_code=7)


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
