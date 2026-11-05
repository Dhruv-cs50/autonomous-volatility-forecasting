"""Plant defects in ``volab.daily`` and check each one fails exactly its own witness.

**Why this is committed.** P4-01 and P4-02's evidence is the sentence "nine planted mutants, each
failing exactly the witnesses named against it and no other test", and R32 says a stated number must
name the
thing that prints it or it is a figure on trust. Three of this slice's tests are derived guards
(R151) whose whole value is that they fire on something a list would have missed; a green suite says
nothing about
that, and a mutant that no test notices is how R154's deleted tests went unseen.

**It edits files under ``src/`` and puts them back.** Each mutant's original text is held in
memory, written back in a ``finally``, and then **compared byte for byte** -- the script raises if
a restore did not land, because a half-reverted plant is a defect committed by a tool whose job is
to find defects. R131's caution applies all the same: a plant-and-revert temporarily breaks a file
every concurrent agent can see, so run this with ``src/`` to yourself, never beside another
session's suite run.

It writes nothing outside the four source files it plants into -- ``__init__.py``, ``runlog.py``,
``runner.py`` and ``__main__.py`` -- plus a ``.orig`` sidecar beside each while that file is
planted, and pytest's own caches. No ledger, no store read, no network.

**It refuses to start unless the suite is already green**, because a witness that was red before
the plant would read as "bites". And it writes the original text to ``<file>.orig`` before planting,
so a ``SIGKILL`` between the plant and the restore leaves the original on disk rather than only in
a dead process's memory; the sidecar is removed once the restore is verified byte for byte.

    uv run --locked python tools/mutate_daily_guards.py

The witnesses live in ``tests/test_daily.py`` and ``tests/test_daily_runner.py``, and both files
are run for every mutant. Two mutants (the verb not passing its timeout, the kill not reaching a
grandchild) make a probe stage sleep out its sixty seconds before the test can fail, so a full run
takes a few minutes.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from dataclasses import dataclass

REPO = pathlib.Path(__file__).resolve().parents[1]
_SUITE = ("tests/test_daily.py", "tests/test_daily_runner.py")


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
            "test_a_run_recorded_as_failed_does_not_consume_its_date",
            "test_a_run_that_never_completed_leaves_its_date_retryable",
            "test_a_torn_final_line_does_not_make_the_log_unreadable",
            # The verb tests that assert a failed or abandoned run leaves its date available.
            "test_a_failing_stage_is_recorded_with_what_it_printed_and_the_run_exits_non_zero",
            "test_a_run_abandoned_mid_stage_leaves_a_start_with_no_completion_and_a_retryable_date",
            "test_a_stage_that_hangs_is_recorded_as_timed_out_and_failed_through_the_verb",
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
        witnesses=("test_no_live_stage_runs_a_module_that_writes_a_reported_ledger",),
    ),
    Mutant(
        label="the run record is rewritten rather than appended",
        relative_path="src/volab/daily/runlog.py",
        before='    with path.open("a", encoding="utf-8") as handle:',
        after='    with path.open("w", encoding="utf-8") as handle:',
        witnesses=(
            "test_a_completion_refers_to_the_start_by_id_and_leaves_its_line_byte_identical",
            "test_a_second_attempt_appends_and_rewrites_nothing",
            "test_the_cli_reports_a_recorded_completion_as_already_done",
            "test_the_join_is_on_the_id_and_survives_interleaved_runs",
            # The verb writes through the same appender, so every verb test that reads the log
            # back for a start line followed by a completion fails with it.
            "test_a_run_of_good_stages_writes_one_start_and_one_done_completion",
            "test_a_failing_stage_is_recorded_with_what_it_printed_and_the_run_exits_non_zero",
            "test_the_record_keeps_the_tail_and_the_dropped_count_of_a_flooding_stage",
            "test_the_start_line_is_on_disk_before_the_first_stage_starts",
            "test_a_stage_that_hangs_is_recorded_as_timed_out_and_failed_through_the_verb",
            "test_an_already_done_date_runs_nothing_and_leaves_the_log_byte_identical",
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
            "test_a_run_recorded_as_failed_does_not_consume_its_date",
            "test_a_failing_stage_is_recorded_with_what_it_printed_and_the_run_exits_non_zero",
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
            "test_the_record_keeps_the_tail_and_the_dropped_count_of_a_flooding_stage",
            "test_the_start_line_is_on_disk_before_the_first_stage_starts",
            "test_a_stage_that_hangs_is_recorded_as_timed_out_and_failed_through_the_verb",
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
)


def _failing_tests() -> frozenset[str]:
    """The names of every failing test in the two daily test files, whole.

    **The whole files and not ``-k <witness>``**, which is what the first version of this script
    ran. Selecting the witness can only establish that the witness fails; the claim being made is
    that a planted defect fails **that witness and no other**, and a selected run cannot see the
    others. The difference matters in both directions: a mutant that also breaks four unrelated
    tests is a blunter instrument than the sentence claims, and one whose witness fails for an
    incidental reason would read the same.
    """
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "--tb=no", "-rf", *_SUITE],
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
        if line.startswith(tuple(f"FAILED {path}::" for path in _SUITE))
    )


def _baseline_is_green() -> bool:
    return not _failing_tests()


def main() -> int:
    if not _baseline_is_green():
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
            failing = _failing_tests()
        finally:
            target.write_text(original, encoding="utf-8")
            if target.read_text(encoding="utf-8") != original:
                raise SystemExit(
                    f"FAILED TO RESTORE {mutant.relative_path}; the original is in {sidecar}"
                )
            sidecar.unlink()
        exact = failing == frozenset(mutant.witnesses)
        verdict = "bites" if exact else ("SURVIVES" if not failing else "BITES WIDELY")
        print(f"{verdict:12} | {mutant.label} -> {sorted(failing) or 'nothing failed'}")
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
