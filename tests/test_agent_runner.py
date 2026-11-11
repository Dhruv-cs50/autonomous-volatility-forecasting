"""P5-D3: ``volab.agent._runner.submit`` -- one fresh interpreter per evaluation.

Almost nothing here launches the real ``volab.candidate``. A real evaluation reads the panel and
takes about 45 s, and the child refuses a storage root outside the repository, so a synthetic
store in ``tmp_path`` cannot be handed to it. The tests use a **stub child** instead: a script in
``tmp_path`` reached through a one-line shell wrapper that is passed as ``python=``. The wrapper
drops the ``-m volab.candidate`` that ``submit`` always puts after the interpreter and ``exec``s
the real interpreter on the stub, so the stub receives exactly ``--proposal <path> --result
<path>`` and keeps the wrapper's pid. One test launches the real module, on a proposal it refuses
before it opens any path.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tracemalloc
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from volab.agent import _runner
from volab.agent._runner import OUTPUT_BOUND_BYTES, ChildRun, submit
from volab.agent.vocabulary import Proposal
from volab.daily import runner as daily_runner

_STUBS = {
    # Writes its pid to the result file, as the real child does, and echoes what it was handed.
    "stub_ok": (
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "proposal = json.loads(open(args[1]).read())\n"
        "json.dump({'pid': os.getpid(), 'argv': args, 'proposal': proposal,\n"
        "           'storage_root': os.environ.get('VOLAB_STORAGE_ROOT'),\n"
        "           'environ_keys': sorted(os.environ)}, open(args[3], 'w'))\n"
        "print('candidate pid:', os.getpid())\n"
    ),
    "stub_exit": (
        "import json, os, sys\n"
        "code = int(os.environ['STUB_EXIT'])\n"
        "if os.environ.get('STUB_WRITE') == '1':\n"
        "    json.dump({'pid': os.getpid(), 'status': 'x'}, open(sys.argv[4], 'w'))\n"
        "print('out')\n"
        "print('err', file=sys.stderr)\n"
        "sys.exit(code)\n"
    ),
    "stub_flood": (
        "import sys\n"
        "piece = int(sys.argv[5])\n"
        "sys.stdout.write('x' * piece)\n"
        "print('\\nTHE-LAST-LINE', file=sys.stderr, flush=True)\n"
    ),
    "stub_bytes": "import sys\nsys.stdout.buffer.write(b'\\xff\\xfe before \\x80 after\\n')\n",
    "stub_garbage": "import sys\nopen(sys.argv[4], 'w').write('not json {')\n",
    "stub_list": "import sys\nopen(sys.argv[4], 'w').write('[1, 2]')\n",
}


def _wrapper(tmp_path: Path, stub: str, *extra: str) -> str:
    """A ``python`` argument that runs ``stub`` in place of ``volab.candidate``."""
    script = tmp_path / f"{stub}.py"
    script.write_text(_STUBS[stub], encoding="utf-8")
    wrapper = tmp_path / f"run_{stub}.sh"
    tail = " ".join(extra)
    wrapper.write_text(
        f'#!/bin/sh\nshift 2\nexec "{sys.executable}" "{script}" "$@" {tail}\n', encoding="utf-8"
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return str(wrapper)


def _proposal(index: int = 1) -> Proposal:
    return Proposal(
        proposal_id=f"2026-11-10-{index:02d}",
        family="harx",
        extra_regressors=("vvix",),
        symbol="SPY",
        horizon=1,
        reference=("har_levels", "naive"),
    )


def _go(tmp_path: Path, stub: str, proposal: Proposal | None = None) -> ChildRun:
    return submit(proposal or _proposal(), root=tmp_path / "data", python=_wrapper(tmp_path, stub))


# --- the fresh interpreter witness ---------------------------------------------------------


def test_two_submissions_run_in_two_different_interpreters(tmp_path: Path) -> None:
    first = _go(tmp_path, "stub_ok", _proposal(1))
    second = _go(tmp_path, "stub_ok", _proposal(2))

    assert first.pid is not None and second.pid is not None
    assert first.pid != second.pid
    # Not vacuous: neither is this process, and the pid is the one the child printed too.
    assert os.getpid() not in (first.pid, second.pid)
    assert f"candidate pid: {first.pid}" in first.output_tail


def test_the_pid_is_read_from_the_result_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_EXIT", "0")
    monkeypatch.setenv("STUB_WRITE", "1")

    run = _go(tmp_path, "stub_exit")

    assert run.result is not None and run.pid == run.result["pid"]


# --- how the child is launched -------------------------------------------------------------


def test_the_proposal_travels_as_a_file_of_the_five_fields_plus_its_id(tmp_path: Path) -> None:
    run = _go(tmp_path, "stub_ok")

    assert run.result is not None
    assert run.result["proposal"] == json.loads(json.dumps(asdict(_proposal())))
    assert set(run.result["proposal"]) == {
        "proposal_id",
        "family",
        "extra_regressors",
        "symbol",
        "horizon",
        "reference",
    }


def test_the_childs_argv_is_the_two_paths_and_no_proposal_fields(tmp_path: Path) -> None:
    run = _go(tmp_path, "stub_ok")

    assert run.result is not None
    argv = run.result["argv"]
    assert [argv[0], argv[2]] == ["--proposal", "--result"]
    assert len(argv) == 4
    assert "SPY" not in " ".join(argv)


def test_the_storage_root_is_handed_to_the_child_and_nothing_else_is_scrubbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AN_UNRELATED_VARIABLE", "kept")
    run = _go(tmp_path, "stub_ok")

    assert run.result is not None
    assert run.result["storage_root"] == str(tmp_path / "data")
    assert "AN_UNRELATED_VARIABLE" in run.result["environ_keys"]


def test_the_child_is_started_unbuffered_so_a_traceback_stays_in_order(tmp_path: Path) -> None:
    run = _go(tmp_path, "stub_ok")

    assert run.result is not None
    assert "PYTHONUNBUFFERED" in run.result["environ_keys"]


def test_the_runner_launches_the_candidate_by_name_and_never_imports_it() -> None:
    source = Path(_runner.__file__).read_text(encoding="utf-8")

    assert '"volab.candidate"' in source
    assert "import volab.candidate" not in source and "from volab.candidate" not in source


# --- the result file -----------------------------------------------------------------------


def test_a_child_that_writes_no_result_gives_none_and_does_not_raise(tmp_path: Path) -> None:
    run = _go(tmp_path, "stub_bytes")

    assert run.result is None
    assert run.pid is None
    assert run.exit_code == 0


@pytest.mark.parametrize("stub", ["stub_garbage", "stub_list"])
def test_a_result_that_is_not_a_json_object_is_no_result(tmp_path: Path, stub: str) -> None:
    run = _go(tmp_path, stub)

    assert run.result is None
    assert run.pid is None


def test_the_proposal_id_is_carried_on_the_run(tmp_path: Path) -> None:
    assert _go(tmp_path, "stub_ok", _proposal(7)).proposal_id == "2026-11-10-07"


# --- exit codes ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "written", "verdict"),
    [
        (0, True, "evaluated"),
        (1, True, "disagrees"),
        (1, False, "crashed"),
        (2, False, "usage_error"),
        (3, True, "coverage_refused"),
        (4, True, "proposal_refused"),
        (5, True, "audit_refused"),
        (9, False, "unrecognised"),
    ],
)
def test_each_exit_codes_meaning_survives_the_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, written: bool, verdict: str
) -> None:
    monkeypatch.setenv("STUB_EXIT", str(code))
    monkeypatch.setenv("STUB_WRITE", "1" if written else "0")

    run = _go(tmp_path, "stub_exit")

    assert run.exit_code == code
    assert run.verdict == verdict
    assert (run.result is not None) is written


def test_the_exit_constants_equal_the_childs_own() -> None:
    from volab.candidate import __main__ as child

    assert (
        _runner.EXIT_OK,
        _runner.EXIT_FAILED,
        _runner.EXIT_USAGE,
        _runner.EXIT_COVERAGE_REFUSED,
        _runner.EXIT_PROPOSAL_REFUSED,
        _runner.EXIT_AUDIT_REFUSED,
    ) == (
        child.EXIT_OK,
        child.EXIT_FAILED,
        child.EXIT_USAGE,
        child.EXIT_COVERAGE_REFUSED,
        child.EXIT_PROPOSAL_REFUSED,
        child.EXIT_AUDIT_REFUSED,
    )


def test_a_negative_exit_from_a_signal_is_unrecognised(tmp_path: Path) -> None:
    run = replace(_go(tmp_path, "stub_bytes"), exit_code=-9)

    assert run.verdict == "unrecognised"


# --- the output bound ----------------------------------------------------------------------


def test_the_bound_equals_the_daily_runners_bound() -> None:
    """R195: the two copies of the loop share no code, so the one number they share is tied here."""
    assert OUTPUT_BOUND_BYTES == daily_runner.OUTPUT_BOUND_BYTES == 16 * 1024


def _flood(tmp_path: Path, total: int) -> ChildRun:
    return submit(
        _proposal(),
        root=tmp_path / "data",
        python=_wrapper(tmp_path, "stub_flood", str(total)),
    )


def test_output_over_the_bound_is_cut_to_the_tail_with_a_marker(tmp_path: Path) -> None:
    run = _flood(tmp_path, 3 * OUTPUT_BOUND_BYTES)

    marker, _, kept = run.output_tail.partition("\n")
    assert "bytes of earlier output dropped" in marker
    assert len(kept.encode()) == OUTPUT_BOUND_BYTES
    assert kept.endswith("THE-LAST-LINE\n")


def test_output_of_exactly_the_bound_is_not_truncated(tmp_path: Path) -> None:
    run = _flood(tmp_path, OUTPUT_BOUND_BYTES - len("\nTHE-LAST-LINE\n"))

    assert len(run.output_tail.encode()) == OUTPUT_BOUND_BYTES
    assert "dropped" not in run.output_tail


def test_output_one_byte_over_the_bound_drops_exactly_one_byte(tmp_path: Path) -> None:
    run = _flood(tmp_path, OUTPUT_BOUND_BYTES + 1 - len("\nTHE-LAST-LINE\n"))

    assert run.output_tail.startswith("[1 bytes of earlier output dropped]\n")


def test_output_that_is_not_utf8_does_not_become_an_exception(tmp_path: Path) -> None:
    run = _go(tmp_path, "stub_bytes")

    assert "before" in run.output_tail and "�" in run.output_tail


def test_a_child_that_prints_megabytes_is_not_held_in_memory(tmp_path: Path) -> None:
    tracemalloc.start()
    try:
        run = _flood(tmp_path, 20 * 1024 * 1024)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(run.output_tail.partition("\n")[2].encode()) == OUTPUT_BOUND_BYTES
    assert peak < 2 * 1024 * 1024, peak


# --- one launch of the real child ----------------------------------------------------------


def test_the_real_candidate_refuses_a_non_canonical_id_and_the_result_round_trips() -> None:
    """The real ``volab.candidate``, on a proposal it refuses before it opens any path.

    ``Proposal`` does not validate on construction, so an id that is not the canonical one gets as
    far as the child, whose boundary re-validation refuses it with exit 4 and still writes a
    result and prints its pid. Nothing is read from the store and nothing is appended.
    """
    # The child loads its settings first and refuses a storage root outside the repository, so
    # this one root is the repository's own; the refusal path never opens it.
    root = Path(__file__).resolve().parents[1] / "data"
    run = submit(replace(_proposal(), proposal_id="not-canonical"), root=root)

    assert run.exit_code == _runner.EXIT_PROPOSAL_REFUSED, run.output_tail
    assert run.verdict == "proposal_refused"
    assert run.result is not None
    assert run.result["status"] == "refused" and run.result["refusal_kind"] == "proposal"
    assert run.pid == run.result["pid"] and run.pid != os.getpid()
    assert f"candidate pid: {run.pid}" in run.output_tail
