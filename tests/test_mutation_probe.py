"""The mutation probe reads pytest's own summary, and this is what it reads.

``tools/mutate_daily_guards.py`` decides whether a planted defect was noticed by parsing the
short summary of a pytest run. That parse is the whole of its evidence, so a verdict is worth
exactly what the parse sees -- and what it saw was failures only. A guard that reports a
violation from a fixture's teardown is counted by pytest as an *error* against the teardown
while the test itself passes, so such a guard was invisible to the probe twice over: ``-rf``
prints no ``ERROR`` line at all, and the parse matched ``FAILED`` alone. The file whose autouse
guard on ``data/forecasts.jsonl`` reports that way is ``tests/test_daily_forecast.py``.

Both halves are pinned below, and so are both directions the drop corrupted a verdict: a mutant
caught only in teardown read ``SURVIVES``, and a mutant that failed its witness *and* errored
some other test read as an exact ``bites`` when it bit wider than its list.

A third reading is pinned here too, because it meets the other two in the same comparison: pytest
prints a parametrised test as ``name[id]``, so a witness spelled as a bare name could never match
the red set, while a witness spelled with an id must keep matching that id alone -- two mutants
name two different ids of the one test on purpose.

**Nothing here imports ``volab``.** The probe plants defects into files under ``src/`` while it
runs, so a test of the probe that imported the code under plant could go red for a reason that
has nothing to do with the probe. What this file loads is ``tools/mutate_daily_guards.py``
itself, which runs ``main`` only under ``__main__`` and so plants nothing on import. (The
insulation is not total: ``tests/conftest.py`` imports ``volab.config`` and ``volab.paths`` at
module level and its autouse fixtures import ``volab.store``, none of which this file names.)

The captured pytest text below is pasted as pytest printed it, from throwaway files run for the
purpose; the command that produced each is recorded beside it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

PROBE = Path(__file__).resolve().parents[1] / "tools" / "mutate_daily_guards.py"

# Produced by writing a two-test file whose autouse fixture raises in teardown and running, from
# the directory holding it:
#
#     python -B -m pytest -q --tb=no -rfE -p no:cacheprovider test_teardown_probe.py
#
# pytest counted ``1 failed, 1 passed, 2 errors`` and printed the same test under both verbs: a
# failing body still has a teardown, and that teardown raised too. The counts line is kept
# because under ``-rf`` it was the *only* trace of the two errors -- no ``ERROR`` line was
# printed at all, so no parser, matching whatever verbs it liked, could have seen them.
_TEARDOWN_SUMMARY = """\
=========================== short test summary info ============================
FAILED test_teardown_probe.py::test_plain_failure - AssertionError: planted f...
ERROR test_teardown_probe.py::test_passes_but_errors_in_teardown - AssertionE...
ERROR test_teardown_probe.py::test_plain_failure - AssertionError: planted te...
1 failed, 1 passed, 2 errors in 0.01s
"""

# Produced the same way, from a file holding ``def test_x() -> None`` with the colon missing:
#
#     python -B -m pytest -q --tb=no -rfE -p no:cacheprovider test_broken.py
#
# The file is named with no ``::`` and no test after it, because no test was collected; pytest
# then interrupted the run and exited 2. Requiring ``::`` would have dropped this line in
# silence, and a dropped line reads as "nothing failed".
_COLLECTION_SUMMARY = """\
=========================== short test summary info ============================
ERROR test_broken.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.11s
"""

_SUITE = ("test_teardown_probe.py",)

# Witnesses copied out of ``MUTANTS``, with the parametrisation ids pytest was measured to
# generate for them -- ``--collect-only`` over the two files that define them. The exactness tests
# below use these rather than invented names, because the spellings in that list are the thing the
# rule has to get right.
_TORN_FRAGMENT_WITNESS = (
    "test_a_torn_fragment_is_still_in_the_file_and_still_skipped_after_an_append"
)
_TORN_LINE_WITNESS = (
    "test_an_append_after_a_torn_final_line_in_the_run_log_is_well_formed_and_loses_nothing"
)
_LIVE_DATE_WITNESS = "test_a_live_date_is_refused_as_live_and_not_as_the_holdout"
# Not a witness of any mutant, but a test in one of the files every mutant runs, and the one whose
# ids carry spaces: ``ids=["in the seam after dev_end", "mid holdout", "the last holdout day"]``,
# confirmed by collecting the file.
_SPACED_ID_TEST = "test_a_holdout_date_is_refused_as_sealed"
_REFUSED_OR_CLOSED_WITNESS = "test_a_refused_or_closed_date_runs_nothing_and_leaves_no_file_behind"

# A file whose body passes and whose autouse guard reports in teardown: the shape of
# ``tests/test_daily_forecast.py``'s forecast-log guard, reduced to what makes it invisible.
_GUARD_THAT_REPORTS_IN_TEARDOWN = '''\
"""A guard that reports from teardown, which pytest counts as an error and not a failure."""

import pytest


@pytest.fixture(autouse=True)
def _report_in_teardown():
    yield
    raise AssertionError("the repository's own log was written")


def test_the_body_passes_and_the_guard_reports() -> None:
    assert True
'''


def _probe(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The probe, loaded from ``tools/`` (not a package); it plants nothing on import.

    ``main`` runs only under ``if __name__ == "__main__"``, so executing the module defines its
    functions and its ``MUTANTS`` and touches no file.

    Registered in ``sys.modules`` before it executes, which ``tests/test_garch.py``'s loader of
    ``tools/measure_garch_forecast_error.py`` does not need to do: ``@dataclass`` resolves this
    module's postponed annotations by looking its own module up there, and on a module built by
    ``module_from_spec`` and not registered it finds ``None`` and raises. Through ``monkeypatch``,
    so the entry is removed when the test ends rather than left for the rest of the session.
    """
    spec = importlib.util.spec_from_file_location("mutate_daily_guards", PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


# --- what the summary is read for ---------------------------------------------------------------


def test_a_teardown_error_in_the_short_summary_is_collected_beside_the_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half the defect: the parse matched ``FAILED`` only, so an ``ERROR`` line was dropped.

    Both verbs name the tests that were red, and the test printed under both is one test.
    """
    failed, errored = _probe(monkeypatch)._reported_tests(_TEARDOWN_SUMMARY, _SUITE)

    assert failed == frozenset({"test_plain_failure"})
    assert errored == frozenset({"test_passes_but_errors_in_teardown", "test_plain_failure"})


def test_the_pytest_command_asks_pytest_for_errors_as_well_as_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half, and the half no parse could have covered.

    ``-r`` *selects* what the short summary reports rather than adding to pytest's default, so
    under ``-rf`` pytest prints no ``ERROR`` line whatsoever -- a parser matching both verbs
    would still have had nothing to match. The flag has to ask for the errors first.
    """
    command = _probe(monkeypatch)._pytest_command(_SUITE)

    assert command[:4] == [sys.executable, "-B", "-m", "pytest"]
    assert "-rfE" in command
    assert "-rf" not in command
    assert tuple(command[-len(_SUITE) :]) == _SUITE


def test_a_failed_line_in_the_short_summary_is_still_collected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pin, green before the change and after: reading errors must not cost the failures.

    This is the only thing the probe ever read, and every measured witness list rests on it.
    """
    failed, _ = _probe(monkeypatch)._reported_tests(
        "FAILED test_teardown_probe.py::test_plain_failure - AssertionError: planted f...\n",
        _SUITE,
    )

    assert failed == frozenset({"test_plain_failure"})


def test_a_parametrised_id_survives_the_reading_of_the_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bracketed id stays attached to the name, with the pytest reason cut off behind it.

    Two mutants name a witness by its bracketed id, and the ``- reason`` pytest prints when the
    traceback is not suppressed must not become part of the name.
    """
    failed, errored = _probe(monkeypatch)._reported_tests(
        "FAILED test_teardown_probe.py::test_x[case-1] - AssertionError: planted\n"
        "ERROR test_teardown_probe.py::test_y[closed] - AssertionError: planted\n",
        _SUITE,
    )

    assert failed == frozenset({"test_x[case-1]"})
    assert errored == frozenset({"test_y[closed]"})


def test_an_id_with_spaces_in_it_comes_back_whole_and_without_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cut at ``" - "`` and not at the first space, because a readable id contains spaces.

    ``test_a_holdout_date_is_refused_as_sealed`` is parametrised with ids written as prose, and
    cutting at the first whitespace printed ``...sealed[in``, ``...sealed[mid`` and
    ``...sealed[the`` -- three ids that do not exist, one per parametrisation, so the count was
    right and only the spelling was wrong. The reason pytest appends after ``" - "`` must still
    go, which the second line here checks.
    """
    failed, _ = _probe(monkeypatch)._reported_tests(
        f"FAILED test_teardown_probe.py::{_SPACED_ID_TEST}[mid holdout]\n"
        f"FAILED test_teardown_probe.py::{_SPACED_ID_TEST}[the last holdout day] - Assertion\n",
        _SUITE,
    )

    assert failed == frozenset(
        {f"{_SPACED_ID_TEST}[mid holdout]", f"{_SPACED_ID_TEST}[the last holdout day]"}
    )


def test_a_summary_line_naming_a_file_outside_the_suite_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The path prefix is the point: the suite is the files run, and only those are read.

    What ties a summary line to this run is the path it names, so text that merely has the shape
    of one -- a nested pytest run's output reaching the same stream, say -- is not a result of it,
    and a name read out of such a line would be charged to the plant.
    """
    failed, errored = _probe(monkeypatch)._reported_tests(
        "FAILED test_somewhere_else.py::test_plain_failure - AssertionError: planted\n"
        "ERROR test_somewhere_else.py::test_passes_but_errors_in_teardown - AssertionE...\n",
        _SUITE,
    )

    assert failed == frozenset()
    assert errored == frozenset()


def test_a_file_that_did_not_collect_stops_the_run_rather_than_reading_as_nothing_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A collection error is a stop and names the file, because its witnesses never ran.

    ``ERROR <path>`` with nothing after it is not a test to record and not a line to drop: every
    claim about a witness in a file that did not import is vacuous, and an empty red set is how
    ``SURVIVES`` is printed.
    """
    probe = _probe(monkeypatch)

    with pytest.raises(SystemExit, match=r"test_broken\.py did not collect"):
        probe._reported_tests(_COLLECTION_SUMMARY, ("test_broken.py",))


# --- what the two sets are read into ------------------------------------------------------------


def test_a_mutant_whose_witness_failed_while_another_test_errored_is_not_an_exact_bite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dangerous direction: a dropped ``ERROR`` line made a wide bite look clean.

    Reading failures alone, this captured run says the witness failed and nothing else did, which
    is "bites" -- the verdict nothing invites a reader to question. It errored a second test, so
    the honest word is the one that says the plant is blunter than its list.
    """
    probe = _probe(monkeypatch)
    failed, errored = probe._reported_tests(_TEARDOWN_SUMMARY, _SUITE)

    assert probe._verdict(failed, errored, ("test_plain_failure",)) == "BITES WIDELY"


def test_a_mutant_caught_only_by_a_guard_that_reports_in_teardown_is_a_bite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction: an errored witness and no failed one must not read as surviving.

    This is the case the forecast-log guard creates. ``SURVIVES`` means "no test noticed", and a
    guard that noticed in teardown noticed.
    """
    probe = _probe(monkeypatch)

    verdict = probe._verdict(
        frozenset(), frozenset({"test_plain_failure"}), ("test_plain_failure",)
    )

    assert verdict == "bites"


def test_a_mutant_that_failed_exactly_its_witnesses_bites(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ordinary classification, a pin and never red: failures alone always read this way."""
    probe = _probe(monkeypatch)

    verdict = probe._verdict(
        frozenset({"test_plain_failure"}), frozenset(), ("test_plain_failure",)
    )

    assert verdict == "bites"


def test_a_mutant_that_nothing_went_red_under_is_the_one_this_script_hunts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third classification, also a pin: both sets empty is a guard that noticed nothing.

    It is spelled out because the other two tests say what ``SURVIVES`` is *not*, and the word
    has to go on meaning the hole.
    """
    probe = _probe(monkeypatch)

    verdict = probe._verdict(frozenset(), frozenset(), ("test_plain_failure",))

    assert verdict == "SURVIVES"


def test_a_witness_spelled_without_an_id_is_satisfied_by_one_parametrisation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare witness name means "at least one parametrisation of this went red".

    pytest prints ``name[id]``, so compared raw a bare name can never appear in the red set and
    the mutant read wide while the guard it names had fired. The rule is a choice and not a
    derivation: the alternative reading of a bare name -- *every* parametrisation -- would make a
    witness list depend on a parametrisation count nobody writing one looks up. So a bare witness
    is a legitimate spelling and not a defective one, with one thing it cannot do: it cannot
    demand a *particular* parametrisation, which is why the bracketed form stays compared exactly
    and is not made redundant by this.

    The red set here is the one a run in flight measured for the torn-line mutant. One bracketed
    id and not four is right: of the four endings that test is parametrised over, only the first
    is a torn fragment, so the plant cannot reach the other three.
    """
    probe = _probe(monkeypatch)

    verdict = probe._verdict(
        frozenset({_TORN_FRAGMENT_WITNESS, f"{_TORN_LINE_WITNESS}[fragment-no-newline]"}),
        frozenset(),
        (_TORN_FRAGMENT_WITNESS, _TORN_LINE_WITNESS),
    )

    assert verdict == "bites"


def test_a_bare_witness_is_satisfied_by_several_parametrisations_of_its_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The many-ids case: "at least one" must not become "exactly one".

    ``test_a_live_date_is_refused_as_live_and_not_as_the_holdout`` is parametrised over three
    live dates under ``ids=str``, so pytest generates ``[2026-11-02]``, ``[2026-11-04]`` and
    ``[2027-01-04]`` -- confirmed by collecting that file. All three going red is still exactly
    this witness, so the half of the rule that refuses a red test from outside the list must not
    trip on ids sharing the one base.
    """
    probe = _probe(monkeypatch)
    every_id = frozenset(
        f"{_LIVE_DATE_WITNESS}[{day}]" for day in ("2026-11-02", "2026-11-04", "2027-01-04")
    )

    verdict = probe._verdict(every_id, frozenset(), (_LIVE_DATE_WITNESS,))

    assert verdict == "bites"


def test_a_witness_naming_one_id_is_not_satisfied_by_a_different_id_of_the_same_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Green before and after the rule, and measured red against the fold the rule rejects.

    It was seen red with the comparison replaced by ``{base of each red} == {base of each
    witness}``, which is the tempting simplification; that is the regression it is here to catch,
    rather than anything the old comparison did. Nothing in the probe folds the *bracketed* side,
    and nothing may start to. Two mutants plant
    in ``src/volab/daily/__main__.py`` and name two different ids of the one test -- ``[refused]``
    and ``[closed]``, the two the test is parametrised over -- precisely because the id says which
    code path broke. Fold both sides to base names and a plant that broke ``[closed]`` would
    satisfy the mutant expecting ``[refused]``: a "bites" where the named guard never fired, which
    is the favourable-direction corruption all over again.
    """
    probe = _probe(monkeypatch)

    verdict = probe._verdict(
        frozenset({f"{_REFUSED_OR_CLOSED_WITNESS}[closed]"}),
        frozenset(),
        (f"{_REFUSED_OR_CLOSED_WITNESS}[refused]",),
    )

    assert verdict == "BITES WIDELY"


def test_a_parametrisation_beyond_the_one_a_witness_names_is_wide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bracketed witness is satisfied exactly, so a second id of its test is still a finding.

    Green before and after the rule, and red against the same folding variant as the test above.
    A plant that broke both parametrisations under a mutant naming one of them is a fact about
    that mutant's list, and the bare spelling is the way to say "any of them" on purpose.
    """
    probe = _probe(monkeypatch)
    both = frozenset(
        {f"{_REFUSED_OR_CLOSED_WITNESS}[refused]", f"{_REFUSED_OR_CLOSED_WITNESS}[closed]"}
    )

    verdict = probe._verdict(both, frozenset(), (f"{_REFUSED_OR_CLOSED_WITNESS}[refused]",))

    assert verdict == "BITES WIDELY"


def test_a_parametrised_id_that_errored_is_matched_to_its_witness_like_one_that_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two changes meet here: the id rule is applied to the union, so both verbs get it.

    A parametrised test whose fixture reports in teardown is printed ``ERROR name[id]``, and it
    has to reach a bare witness exactly as a parametrised failure does. A rule applied to the
    failures before the two sets were joined would have given one verb what it denied the other.
    """
    probe = _probe(monkeypatch)

    verdict = probe._verdict(
        frozenset(),
        frozenset({f"{_TORN_LINE_WITNESS}[fragment-no-newline]"}),
        (_TORN_LINE_WITNESS,),
    )

    assert verdict == "bites"


def test_a_run_that_found_a_guard_hole_does_not_exit_as_a_run_that_only_bit_widely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two findings had one exit code, so exit 1 read as "a guard failed" when none had.

    A hole in the guards and a stale witness list are both to be fixed and are not the same
    thing. Neither is success, because the script's own claim is "that witness and no other".
    """
    probe = _probe(monkeypatch)

    assert probe._exit_code(uncaught=1, wide=0) == 1
    assert probe._exit_code(uncaught=0, wide=1) == 2
    assert probe._exit_code(uncaught=1, wide=0) != probe._exit_code(uncaught=0, wide=1)
    assert probe._exit_code(uncaught=0, wide=0) == 0


# --- the daily path's own files ------------------------------------------------------------------


def _fake_repo(probe: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """``tmp_path`` standing in for the repository, with the ``data/`` its own files live in.

    The probe addresses its own files as ``REPO / "data/..."``, so moving ``REPO`` is enough to
    keep every assertion below off the real ``data/``.
    """
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(probe, "REPO", tmp_path)
    return tmp_path


def test_a_daily_file_a_mutant_created_is_moved_into_the_backup_and_not_deleted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Absence is part of the reading, and putting it back must not destroy anything.

    ``data/daily_runs.jsonl`` is absent in a fresh checkout, so a mutant that makes the daily path
    write one leaves state the next mutant would run against. Neither own file is tracked and
    neither is copied into the backup when absent, so a file found where one was absent has no
    blob and no copy behind it -- and the baseline saying "absent" may have been read as much as
    half an hour earlier, so the writer may have been another session's daily run rather than the
    mutant. It is therefore moved aside into the backup, asserted on both sides: gone from
    ``data/``, and whole where it went. Measured red against a ``path.unlink()`` in place of the
    move, which is the simplification it exists to refuse.
    """
    probe = _probe(monkeypatch)
    repo = _fake_repo(probe, monkeypatch, tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    kept = probe._read_own_files()
    assert kept == {"data/forecasts.jsonl": None, "data/daily_runs.jsonl": None}
    (repo / "data" / "daily_runs.jsonl").write_bytes(b'{"planted": true}\n')

    moved = probe._put_own_files_back(kept, backup)

    assert moved == ("data/daily_runs.jsonl",)
    assert not (repo / "data" / "daily_runs.jsonl").exists()
    asides = list(backup.rglob("daily_runs.jsonl"))
    assert len(asides) == 1, "the created file was deleted rather than kept"
    assert asides[0].read_bytes() == b'{"planted": true}\n'


def test_a_daily_file_a_mutant_changed_is_restored_byte_for_byte(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bytes and not the existence: this rewrite keeps the length, as an appender would not."""
    probe = _probe(monkeypatch)
    repo = _fake_repo(probe, monkeypatch, tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    log = repo / "data" / "forecasts.jsonl"
    log.write_bytes(b'{"n": 1}\n')
    kept = probe._read_own_files()
    assert kept == {"data/forecasts.jsonl": b'{"n": 1}\n', "data/daily_runs.jsonl": None}
    log.write_bytes(b'{"n": 2}\n')

    moved = probe._put_own_files_back(kept, backup)

    assert moved == ("data/forecasts.jsonl",)
    assert log.read_bytes() == b'{"n": 1}\n'


def test_the_bytes_a_restore_writes_over_are_kept_in_the_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The changed case keeps what it replaces, for the reason the absent case moves aside.

    Writing the baseline back destroys whatever is there now, and "whatever is there now" is the
    same unrecoverable kind of thing the move-aside exists to protect: these two files are
    untracked, and a baseline read as much as half an hour earlier is not authority to say the
    current bytes are the plant's. So they are copied into the backup first, and the restore's
    printed line names where they went.
    """
    probe = _probe(monkeypatch)
    repo = _fake_repo(probe, monkeypatch, tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    log = repo / "data" / "forecasts.jsonl"
    log.write_bytes(b'{"n": 1}\n')
    kept = probe._read_own_files()
    log.write_bytes(b'{"n": 2}\n')

    assert probe._put_own_files_back(kept, backup) == ("data/forecasts.jsonl",)

    replaced = list(backup.rglob("forecasts.jsonl"))
    assert len(replaced) == 1, "the bytes the restore wrote over were not kept"
    assert replaced[0].read_bytes() == b'{"n": 2}\n'
    assert log.read_bytes() == b'{"n": 1}\n'


def test_a_daily_file_a_mutant_removed_is_written_back_with_nothing_to_keep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The fourth case: present in the baseline and gone now, so the baseline goes back.

    A pin, green before the keep-what-you-replace change and after it. There is nothing to copy
    aside here -- the bytes that were there are the ones being written -- which is why the branch
    is separate rather than a special case of the one above, and this says the split kept it
    working and kept the backup empty.
    """
    probe = _probe(monkeypatch)
    repo = _fake_repo(probe, monkeypatch, tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    log = repo / "data" / "forecasts.jsonl"
    log.write_bytes(b'{"n": 1}\n')
    kept = probe._read_own_files()
    log.unlink()

    assert probe._put_own_files_back(kept, backup) == ("data/forecasts.jsonl",)

    assert log.read_bytes() == b'{"n": 1}\n'
    assert list(backup.rglob("*")) == []


def test_daily_files_nothing_moved_are_reported_as_having_moved_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The quiet case, which is every well-behaved mutant: present unchanged, absent still absent.

    A restore that reported a move it had not made would print a finding under every mutant, and
    one that moved a file aside would empty ``data/`` of a file nothing had touched.
    """
    probe = _probe(monkeypatch)
    repo = _fake_repo(probe, monkeypatch, tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    log = repo / "data" / "forecasts.jsonl"
    log.write_bytes(b'{"n": 1}\n')
    kept = probe._read_own_files()

    moved = probe._put_own_files_back(kept, backup)

    assert moved == ()
    assert log.read_bytes() == b'{"n": 1}\n'
    assert not (repo / "data" / "daily_runs.jsonl").exists()
    assert list(backup.rglob("*")) == []


def test_no_restore_this_file_makes_can_reach_the_repositorys_own_forecast_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The real ``data/forecasts.jsonl``, digested either side of all three restore cases.

    Every call this file makes to ``_read_own_files`` or ``_put_own_files_back`` sits in a test
    that moved ``REPO`` to a ``tmp_path`` first -- one ``grep`` over the file shows it -- so none
    can reach the repository's own log. This is the reading that bears that out: the path comes
    from the probe's location and not from the patched global, and it is digested either side of
    the three cases the restore has, all performed under a fake repository. No unpatched call to
    either function is made anywhere here, deliberately: an unpatched restore would be exposed to
    the stale-baseline race the move-aside exists for, and could overwrite real rows or carry
    them into a ``tmp_path`` pytest later deletes.

    The digest's window is this test. Whether a whole suite run left the file alone is a reading
    taken outside pytest, not something a test in flight can claim.
    """
    probe = _probe(monkeypatch)
    real = PROBE.parents[1] / "data" / "forecasts.jsonl"
    before = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None

    repo = _fake_repo(probe, monkeypatch, tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    (repo / "data" / "forecasts.jsonl").write_bytes(b'{"n": 1}\n')
    kept = probe._read_own_files()
    (repo / "data" / "forecasts.jsonl").write_bytes(b'{"n": 2}\n')
    (repo / "data" / "daily_runs.jsonl").write_bytes(b'{"planted": true}\n')
    assert probe._put_own_files_back(kept, backup) == (
        "data/forecasts.jsonl",
        "data/daily_runs.jsonl",
    )

    after = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    assert after == before


# --- the command and the reading, together -------------------------------------------------------


def test_the_command_and_the_reading_together_catch_a_guard_that_reports_in_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One real pytest run, through the probe's own flags and its own parse.

    The two unit tests above are each half an answer: the flag has to ask for the errors and the
    parse has to read them, and neither alone catches a guard that reports in teardown. The run is
    one file holding one passing test, measured at 0.17s, and it is made under ``tmp_path`` with the
    bare filename as the suite entry and ``cwd`` there -- which is how the probe itself runs, whose
    suite entries are paths relative to the ``cwd`` it passes, so the prefix match needs nothing
    done to it.
    """
    probe = _probe(monkeypatch)
    name = "test_a_guard_that_reports_in_teardown.py"
    (tmp_path / name).write_text(_GUARD_THAT_REPORTS_IN_TEARDOWN, encoding="utf-8")

    completed = subprocess.run(
        probe._pytest_command((name,)),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    failed, errored = probe._reported_tests(completed.stdout, (name,))
    assert "1 passed" in completed.stdout, completed.stdout
    assert failed == frozenset()
    assert errored == frozenset({"test_the_body_passes_and_the_guard_reports"})
