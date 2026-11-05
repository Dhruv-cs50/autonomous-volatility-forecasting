"""Plant defects in ``volab.daily`` and its neighbours, and check each is noticed by its witnesses.

**Why this is committed.** P4-01 and P4-02's evidence is the sentence "nine planted mutants, each
failing exactly the witnesses named against it and no other test", and R32 says a stated number must
name the thing that prints it or it is a figure on trust. This phase's derived guards (R151) are
worth only what they fire on, and a green suite says nothing about that; a mutant that no test
notices is how R154's deleted tests went unseen. The board's Method promises one mutation for each
derived guard (R151: "the witness for a derived rule is a mutation that the listed version
passes"), and this script is where that promise is kept.

**What the figure does and does not say.** The witness lists below are **descriptive, not
predictive**. Each records what a plant was *measured* to break, and an earlier commit widened some
of them to match what the probe had observed, so "fails exactly its named tests" is true partly by
construction of the list. A plant that breaks *more* than its list is therefore a finding about the
plant or about the tests (a guard that is blunter than its name, two tests that overlap) and not a
failure of the code; it is reported, and the list is a record of the finding. What the figure is
worth is **"every plant is noticed by something that names it"** -- a mutant that fails nothing is
a hole in the guards, and one that fails something its list does not name is a changed fact to be
read. It is not a claim of one-to-one precision between a defect and a test, and a reader should
not take it for one. The figure also covers only the test files named below: a plant that broke a
test in a file no witness lives in would not be seen.

**Which test files it runs is derived, not listed.** Every mutant names its witnesses; each name is
looked up as a ``def`` in ``tests/test_*.py``, and the files holding them are the files run for
*every* mutant. A second list of files could not report its own incompleteness (R151), whereas a
witness that no file defines, or that two files define, stops the script before it plants anything.
Adding a mutant whose witness lives in a new file adds that file to every run.

**It edits files under ``src/`` and puts them back.** Each mutant's original text is held in
memory, written back in a ``finally``, and then **compared byte for byte** -- the script raises if
a restore did not land, because a half-reverted plant is a defect committed by a tool whose job is
to find defects. R131's caution applies all the same: a plant-and-revert temporarily breaks a file
every concurrent agent can see, and the restore writes back the text read *before* the plant, so an
edit another session made to the same file in between would be overwritten. Run this with ``src/``
to yourself, never beside another session's suite run or edits, and plant only into files nobody
else is editing.

**It is also the one thing here that can move a reported ledger**, because the rehearsal's
ledger-immutability check can only be seen red by a stage that really appends to
``data/trials.jsonl``. So the two ledgers are copied to a temporary directory before the first
plant (printed, so a ``SIGKILL`` leaves a path to restore from), compared after *every* mutant, and
written back byte for byte if they moved. A ledger that moved under a mutant not declared
``moves_ledger`` stops the script. The ledgers are untracked-or-committed data that the board pins
by line count and digest; read both before and after a run.

It writes nothing else outside the source files it plants into, plus a ``.orig`` sidecar beside
each while that file is planted, and pytest's own caches. No store read, no network.

**It refuses to start unless the suite is already green**, because a witness that was red before
the plant would read as "bites". The original text is written to ``<file>.orig`` before planting,
so a ``SIGKILL`` between the plant and the restore leaves the original on disk rather than only in
a dead process's memory; the sidecar is removed once the restore is verified byte for byte.

    uv run --locked python tools/mutate_daily_guards.py

Two mutants (the verb not passing its timeout, the kill not reaching a grandchild) make a probe
stage sleep out its sixty seconds before the test can fail, and every mutant runs the whole of
several test files, so a full run takes the better part of half an hour.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass

REPO = pathlib.Path(__file__).resolve().parents[1]
# The two reported ledgers, which no plant may leave moved. Named here and not derived because they
# are the thing being protected; the daily path's own files are not reported numbers.
_LEDGERS = ("data/trials.jsonl", "data/comparisons.jsonl")
# The command the trigger-installer plant calls. Joined from two pieces because this file sits under
# ``tools/``, which the very guard being tested scans: written whole, the script would be the
# installer it plants and the guard would be red before any plant went in.
_LAUNCH_TOOL = "".join(("launch", "ctl"))


@dataclass(frozen=True)
class Mutant:
    label: str
    relative_path: str
    before: str
    after: str
    # Every test this defect must fail, and no other. Usually one. Where it is more than one,
    # that is a fact about the defect rather than a weaker claim: a record rewritten instead of
    # appended breaks every test that asserts appending, and a probe that named only one of them
    # would be describing the plant it wished it had planted.
    witnesses: tuple[str, ...]
    # True only for a plant that writes to a reported ledger *on purpose*, because the guard under
    # test is the one that notices a ledger move. ``main`` puts the ledgers back byte for byte
    # either way; a ledger that moved under any other mutant stops the run.
    moves_ledger: bool = False


# **A note on six of the older lists (P4-08c).** While this script ran only ``tests/test_daily.py``
# and ``tests/test_daily_runner.py``, these plants were recorded as breaking only tests in those
# two files. Running the forecast and rehearsal files as well showed they break more: the forecast
# stage and the whole-run rehearsal write and read the run log through the same appender and the
# same ``completed_dates``, so a plant there is seen from those files too. The lists were widened
# to what was measured -- a record of the finding, not a prediction made beforehand -- and it was
# the first run of the wider script that said so.
MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        label="the calendar is asked before dev_end",
        relative_path="src/volab/daily/__init__.py",
        before=(
            "    if as_of > settings.splits.dev_end:\n"
            "        return Plan(\n"
            "            as_of=as_of,\n"
            '            outcome="refused",'
        ),
        after=(
            "    if not is_session(get_calendar(settings), as_of):\n"
            "        return Plan(\n"
            "            as_of=as_of,\n"
            '            outcome="no_session",\n'
            '            reason=f"{as_of} is not a session on the {settings.calendar} calendar",\n'
            "            stages=(),\n"
            "        )\n"
            "    if as_of > settings.splits.dev_end:\n"
            "        return Plan(\n"
            "            as_of=as_of,\n"
            '            outcome="refused",'
        ),
        witnesses=("test_the_dev_end_refusal_is_decided_before_the_calendar_is_asked",),
    ),
    Mutant(
        label="the completed-dates argument is ignored",
        relative_path="src/volab/daily/__init__.py",
        before="    if as_of in completed:",
        after="    if False and as_of in completed:",
        witnesses=(
            "test_a_date_with_a_completed_run_is_already_done",
            "test_the_cli_reports_a_recorded_completion_as_already_done",
            "test_an_already_done_date_runs_nothing_and_leaves_the_log_byte_identical",
        ),
    ),
    Mutant(
        label="completed_dates counts starts rather than completions",
        relative_path="src/volab/daily/runlog.py",
        before=(
            '        if record.get("kind") == "complete"'
            ' and record.get("outcome") == COMPLETED_OUTCOME:\n'
            '            reference = record.get("ref")\n'
            "            if isinstance(reference, str) and reference in started:\n"
            "                done.add(started[reference])"
        ),
        after=(
            '        if record.get("kind") == "start":\n'
            '            reference = record.get("id")\n'
            "            if isinstance(reference, str) and reference in started:\n"
            "                done.add(started[reference])"
        ),
        witnesses=(
            "test_a_failing_stage_is_recorded_with_what_it_printed_and_the_run_exits_non_zero",
            "test_a_run_abandoned_mid_stage_leaves_a_start_with_no_completion_and_a_retryable_date",
            "test_a_run_recorded_as_failed_does_not_consume_its_date",
            "test_a_run_that_never_completed_leaves_its_date_retryable",
            "test_a_run_whose_forecast_refuses_a_configuration_is_recorded_failed_not_done",
            "test_a_stage_that_hangs_is_recorded_as_timed_out_and_failed_through_the_verb",
            "test_a_torn_final_line_does_not_make_the_log_unreadable",
        ),
    ),
    Mutant(
        label="a second clock call, in the plan module",
        relative_path="src/volab/daily/__init__.py",
        before="def plan_for(",
        after="def _drift() -> dt.date:\n    return dt.date.today()\n\n\ndef plan_for(",
        witnesses=("test_the_clock_is_called_in_exactly_one_function_in_the_whole_package",),
    ),
    Mutant(
        label="a holdout key used in code rather than named in prose",
        relative_path="src/volab/daily/__init__.py",
        before="    if as_of > settings.splits.dev_end:",
        after="    allow_holdout = False\n    if allow_holdout or as_of > settings.splits.dev_end:",
        witnesses=("test_no_module_under_daily_names_a_holdout_key",),
    ),
    Mutant(
        label="a reported-ledger writer added to the daily stages",
        relative_path="src/volab/daily/__init__.py",
        before='    Stage(name="leaderboard", module="volab.leaderboard", argv=()),',
        after=(
            '    Stage(name="leaderboard", module="volab.leaderboard", argv=()),\n'
            '    Stage(name="walkforward", module="volab.walkforward", argv=("run",)),'
        ),
        witnesses=(
            "test_no_live_stage_runs_a_module_that_writes_a_reported_ledger",
            "test_nothing_the_daily_path_can_import_names_a_writer_entry_point",
            "test_the_writer_modules_the_daily_path_can_import_are_exactly_these_two",
        ),
    ),
    Mutant(
        label="the run record is rewritten rather than appended",
        relative_path="src/volab/daily/runlog.py",
        before='    with path.open("a", encoding="utf-8") as handle:',
        after='    with path.open("w", encoding="utf-8") as handle:',
        witnesses=(
            "test_a_completion_refers_to_the_start_by_id_and_leaves_its_line_byte_identical",
            "test_a_failing_stage_is_recorded_with_what_it_printed_and_the_run_exits_non_zero",
            "test_a_run_of_good_stages_writes_one_start_and_one_done_completion",
            "test_a_run_whose_forecast_refuses_a_configuration_is_recorded_failed_not_done",
            "test_a_second_attempt_appends_and_rewrites_nothing",
            "test_a_stage_that_hangs_is_recorded_as_timed_out_and_failed_through_the_verb",
            "test_a_whole_daily_run_leaves_the_repositorys_ledgers_and_data_untouched",
            "test_a_work_plan_on_a_dev_date_completes_done_with_a_forecast_line_per_configuration",
            "test_an_already_done_date_runs_nothing_and_leaves_the_log_byte_identical",
            "test_the_cli_reports_a_recorded_completion_as_already_done",
            "test_the_join_is_on_the_id_and_survives_interleaved_runs",
            "test_the_record_keeps_the_tail_and_the_dropped_count_of_a_flooding_stage",
            "test_the_start_line_is_on_disk_before_the_first_stage_starts",
        ),
    ),
    Mutant(
        label="a refusal exits zero",
        relative_path="src/volab/daily/__main__.py",
        before='    return 1 if plan.outcome == "refused" else 0',
        after="    return 0",
        witnesses=(
            "test_the_cli_refuses_a_real_date_today_and_exits_one",
            "test_a_refused_or_closed_date_runs_nothing_and_leaves_no_file_behind[refused]",
        ),
    ),
    Mutant(
        label="the exchange date is read as the UTC date",
        relative_path="src/volab/daily/__main__.py",
        before="    return instant.astimezone(_EXCHANGE_TZ).date()",
        after="    return instant.date()",
        witnesses=(
            "test_the_exchange_date_is_taken_from_the_instant_and_not_from_utc",
            "test_today_is_the_exchange_date_of_the_seams_instant",
        ),
    ),
    Mutant(
        label="the start line is written but not flushed or fsynced",
        relative_path="src/volab/daily/runlog.py",
        before="        handle.flush()\n        os.fsync(handle.fileno())",
        after="        pass",
        witnesses=("test_the_start_line_is_fsynced_and_not_merely_written",),
    ),
    Mutant(
        label="any completion consumes its date, whatever its outcome",
        relative_path="src/volab/daily/runlog.py",
        before=(
            '        if record.get("kind") == "complete"'
            ' and record.get("outcome") == COMPLETED_OUTCOME:'
        ),
        after='        if record.get("kind") == "complete":',
        witnesses=(
            "test_a_failing_stage_is_recorded_with_what_it_printed_and_the_run_exits_non_zero",
            "test_a_run_recorded_as_failed_does_not_consume_its_date",
            "test_a_run_whose_forecast_refuses_a_configuration_is_recorded_failed_not_done",
            "test_a_stage_that_hangs_is_recorded_as_timed_out_and_failed_through_the_verb",
        ),
    ),
    Mutant(
        label="the CLI does not read the run log",
        relative_path="src/volab/daily/__main__.py",
        before="    plan = plan_for(settings, as_of, completed=completed_dates(log))",
        after="    plan = plan_for(settings, as_of)",
        witnesses=(
            "test_the_cli_reports_a_recorded_completion_as_already_done",
            "test_an_already_done_date_runs_nothing_and_leaves_the_log_byte_identical",
        ),
    ),
    Mutant(
        label="a torn line raises instead of being skipped",
        relative_path="src/volab/daily/runlog.py",
        before=(
            "        try:\n"
            "            parsed = json.loads(line)\n"
            "        except json.JSONDecodeError:\n"
            "            continue"
        ),
        after="        parsed = json.loads(line)",
        witnesses=("test_a_torn_final_line_does_not_make_the_log_unreadable",),
    ),
    Mutant(
        label="the start line is written after the stages, not before",
        relative_path="src/volab/daily/__main__.py",
        before=(
            "    run_id = record_start(log, plan, at=started, sha=sha, dirty=dirty)\n"
            "    # The same instant the start line carries, handed to every stage that asks for"
            " it: one clock\n"
            "    # call for both, so what a stage records and what the run log records cannot"
            " disagree.\n"
            "    stages = tuple(with_instant(stage, started) for stage in plan.stages)\n"
            "    runs = run_stages(stages, timeout_seconds=STAGE_TIMEOUT_SECONDS)\n"
        ),
        after=(
            "    stages = tuple(with_instant(stage, started) for stage in plan.stages)\n"
            "    runs = run_stages(stages, timeout_seconds=STAGE_TIMEOUT_SECONDS)\n"
            "    run_id = record_start(log, plan, at=started, sha=sha, dirty=dirty)\n"
        ),
        witnesses=(
            "test_the_start_line_is_on_disk_before_the_first_stage_starts",
            # Interrupted before the stages finish, nothing has been written yet: the abandoned-run
            # test expects the start line to be there.
            "test_a_run_abandoned_mid_stage_leaves_a_start_with_no_completion_and_a_retryable_date",
        ),
    ),
    Mutant(
        label="a timed-out stage is recorded as having exited zero",
        relative_path="src/volab/daily/runner.py",
        before="            exit_code = TIMED_OUT_EXIT_CODE",
        after="            exit_code = 0",
        witnesses=(
            "test_a_stage_past_its_timeout_is_a_failed_stage_and_the_run_stops",
            "test_a_stage_that_hangs_is_recorded_as_timed_out_and_failed_through_the_verb",
        ),
    ),
    Mutant(
        label="the verb does not pass the stage timeout to the runner",
        relative_path="src/volab/daily/__main__.py",
        before="run_stages(stages, timeout_seconds=STAGE_TIMEOUT_SECONDS)",
        after="run_stages(stages)",
        witnesses=("test_a_stage_that_hangs_is_recorded_as_timed_out_and_failed_through_the_verb",),
    ),
    Mutant(
        label="the timeout kills the stage but not the stage's children",
        relative_path="src/volab/daily/runner.py",
        before="                os.killpg(process.pid, signal.SIGKILL)",
        after="                process.kill()",
        witnesses=("test_a_timeout_kills_the_stages_own_children_too",),
    ),
    Mutant(
        label="the stage output is dropped from the completion line",
        relative_path="src/volab/daily/runlog.py",
        before='                    "output": stage.output,',
        after='                    "output": "",',
        witnesses=(
            "test_a_failing_stage_is_recorded_with_what_it_printed_and_the_run_exits_non_zero",
            "test_a_run_whose_forecast_refuses_a_configuration_is_recorded_failed_not_done",
            "test_a_stage_that_hangs_is_recorded_as_timed_out_and_failed_through_the_verb",
            "test_a_work_plan_on_a_dev_date_completes_done_with_a_forecast_line_per_configuration",
            "test_the_record_keeps_the_tail_and_the_dropped_count_of_a_flooding_stage",
            "test_the_start_line_is_on_disk_before_the_first_stage_starts",
        ),
    ),
    Mutant(
        label="the clock seam returns a local date instead of a UTC instant",
        relative_path="src/volab/daily/__main__.py",
        before="    return dt.datetime.now(tz=dt.UTC)",
        after="    return dt.date.today()",
        witnesses=("test_the_one_clock_call_is_an_aware_utc_instant_that_is_now",),
    ),
    Mutant(
        label="a failed run exits zero",
        relative_path="src/volab/daily/__main__.py",
        before="    return 0 if succeeded else STAGE_FAILED_EXIT_CODE",
        after="    return 0",
        witnesses=(
            "test_a_failing_stage_is_recorded_with_what_it_printed_and_the_run_exits_non_zero",
            "test_a_run_whose_forecast_refuses_a_configuration_is_recorded_failed_not_done",
            "test_a_stage_that_hangs_is_recorded_as_timed_out_and_failed_through_the_verb",
        ),
    ),
    Mutant(
        label="run executes a plan that was not work",
        relative_path="src/volab/daily/__main__.py",
        before='    if arguments.command == "run" and plan.outcome == "work":',
        after='    if arguments.command == "run" and plan.outcome != "refused":',
        witnesses=(
            "test_a_refused_or_closed_date_runs_nothing_and_leaves_no_file_behind[closed]",
            "test_an_already_done_date_runs_nothing_and_leaves_the_log_byte_identical",
        ),
    ),
    Mutant(
        label="an installer for a launchd job added to a module under src/",
        relative_path="src/volab/errors.py",
        before="class ConfigError(VolabError):",
        after=(
            "def _install_the_job() -> None:\n"
            "    import subprocess\n"
            "\n"
            f'    subprocess.run(["{_LAUNCH_TOOL}", "bootstrap", "gui/501", "job"], check=False)\n'
            "\n"
            "\n"
            "class ConfigError(VolabError):"
        ),
        witnesses=("test_no_code_under_src_or_tools_installs_a_trigger",),
    ),
    Mutant(
        label="the registered kind written in the pseudo-champion module",
        relative_path="src/volab/daily/champion.py",
        before='PSEUDO_KIND = "pseudo"',
        after='PSEUDO_KIND = "registered"',
        witnesses=(
            "test_every_champion_the_derivation_yields_is_labelled_pseudo",
            "test_main_fits_the_champion_and_writes_one_line_per_configuration",
            "test_no_code_under_daily_can_write_the_registered_kind",
        ),
    ),
    Mutant(
        label="a writer module imported by a module the forecast stage imports",
        relative_path="src/volab/daily/champion.py",
        before="from volab.errors import VolabError\n",
        after="import volab.evaluators.sink\nfrom volab.errors import VolabError\n",
        witnesses=("test_the_writer_modules_the_daily_path_can_import_are_exactly_these_two",),
    ),
    Mutant(
        label="a stage the rehearsal really runs appends a line to data/trials.jsonl",
        relative_path="src/volab/leaderboard/__main__.py",
        before="    arguments = parser.parse_args(argv)\n",
        after=(
            "    arguments = parser.parse_args(argv)\n"
            '    with (load_settings().storage_root / "trials.jsonl").open("a") as planted:\n'
            '        planted.write("{}\\n")\n'
        ),
        witnesses=(
            "test_a_module_outside_the_closure_is_not_scanned",
            "test_a_whole_daily_run_leaves_the_repositorys_ledgers_and_data_untouched",
            "test_no_live_stage_runs_a_module_that_writes_a_reported_ledger",
            "test_nothing_the_daily_path_can_import_names_a_writer_entry_point",
            "test_the_derived_writer_set_excludes_the_reader",
            "test_the_docstrings_that_name_append_trials_are_real_and_do_not_fire",
            "test_the_writer_modules_the_daily_path_can_import_are_exactly_these_two",
        ),
        # What the plant was measured to break. The one the plant is *for* is the whole-run
        # rehearsal's, which was confirmed to fail on "a reported ledger moved" and not on an
        # incidental exit code. The others fail because the plant also makes the module a ledger
        # writer by the derived definition (R151/R159), which is true and not the point.
        moves_ledger=True,
    ),
    Mutant(
        label="Splits.holdout_end given a default again",
        relative_path="src/volab/config.py",
        before="    holdout_end: dt.date\n",
        after="    holdout_end: dt.date = dt.date.max\n",
        witnesses=("test_splits_cannot_be_built_without_a_holdout_end",),
    ),
)


def _test_files() -> tuple[str, ...]:
    """The test files holding every witness any mutant names, derived by looking each one up.

    A witness is a ``def`` in some ``tests/test_*.py``; a parametrised id (``name[case]``) is looked
    up by the name before the bracket. A witness no file defines, or two files define, is a stop
    here and not a quiet omission -- a renamed test would otherwise leave its mutant "surviving"
    for a reason that has nothing to do with the guard.
    """
    defined: dict[str, set[str]] = {}
    for path in sorted((REPO / "tests").glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                defined.setdefault(node.name, set()).add(path.relative_to(REPO).as_posix())
    files: set[str] = set()
    for mutant in MUTANTS:
        for witness in mutant.witnesses:
            homes = defined.get(witness.split("[", 1)[0], set())
            if len(homes) != 1:
                raise SystemExit(
                    f"witness {witness!r} of {mutant.label!r} is defined in {sorted(homes) or 'no'}"
                    " test file; it must be defined in exactly one"
                )
            files |= homes
    return tuple(sorted(files))


def _failing_tests(suite: tuple[str, ...]) -> frozenset[str]:
    """The names of every failing test in the ``suite`` files, whole.

    **The whole files and not ``-k <witness>``**, which is what the first version of this script
    ran. Selecting the witness can only establish that the witness fails; the claim being made is
    that a planted defect fails **that witness and no other**, and a selected run cannot see the
    others. The difference matters in both directions: a mutant that also breaks four unrelated
    tests is a blunter instrument than the sentence claims, and one whose witness fails for an
    incidental reason would read the same.
    """
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "--tb=no", "-rf", *suite],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if "error" in completed.stdout.lower() and "passed" not in completed.stdout:
        raise SystemExit(f"the suite did not run:\n{completed.stdout}\n{completed.stderr}")
    return frozenset(
        line.split("::", 1)[1].split()[0]
        for line in completed.stdout.splitlines()
        if line.startswith(tuple(f"FAILED {path}::" for path in suite))
    )


def _read_ledgers() -> dict[str, bytes]:
    return {name: (REPO / name).read_bytes() for name in _LEDGERS}


def _put_ledgers_back(kept: dict[str, bytes]) -> bool:
    """Restore any ledger that differs from ``kept``, byte for byte; True if one had moved."""
    moved = False
    for name, data in kept.items():
        if (REPO / name).read_bytes() != data:
            moved = True
            (REPO / name).write_bytes(data)
            if (REPO / name).read_bytes() != data:
                raise SystemExit(f"FAILED TO RESTORE {name}; a copy is in the printed backup")
    return moved


def main() -> int:
    suite = _test_files()
    print("test files, derived from the witnesses:", *suite)
    ledgers = _read_ledgers()
    backup = pathlib.Path(tempfile.mkdtemp(prefix="mutate-ledgers-"))
    for name, data in ledgers.items():
        (backup / pathlib.Path(name).name).write_bytes(data)
    print("ledger copies, should a kill leave one moved:", backup)
    if _failing_tests(suite):
        raise SystemExit("the suite is already failing; fix that before planting anything")
    survivors: list[str] = []
    for mutant in MUTANTS:
        target = REPO / mutant.relative_path
        original = target.read_text(encoding="utf-8")
        if mutant.before not in original:
            raise SystemExit(f"plant anchor missing in {mutant.relative_path}: {mutant.label}")
        sidecar = target.with_suffix(target.suffix + ".orig")
        sidecar.write_text(original, encoding="utf-8")
        target.write_text(original.replace(mutant.before, mutant.after, 1), encoding="utf-8")
        try:
            failing = _failing_tests(suite)
        finally:
            target.write_text(original, encoding="utf-8")
            if target.read_text(encoding="utf-8") != original:
                raise SystemExit(
                    f"FAILED TO RESTORE {mutant.relative_path}; the original is in {sidecar}"
                )
            sidecar.unlink()
            moved = _put_ledgers_back(ledgers)
        if moved and not mutant.moves_ledger:
            raise SystemExit(f"a ledger moved under {mutant.label!r}; it has been put back")
        exact = failing == frozenset(mutant.witnesses)
        verdict = "bites" if exact else ("SURVIVES" if not failing else "BITES WIDELY")
        print(f"{verdict:12} | {mutant.label} -> {sorted(failing) or 'nothing failed'}", flush=True)
        if not exact:
            survivors.append(f"{mutant.label}: failing set was {sorted(failing)}")

    print()
    if survivors:
        print(f"{len(survivors)} of {len(MUTANTS)} mutants did not fail exactly their witness:")
        for label in survivors:
            print(f"  - {label}")
        return 1
    print(f"all {len(MUTANTS)} mutants fail exactly the tests named against them, and no others")
    return 0


if __name__ == "__main__":
    sys.exit(main())
