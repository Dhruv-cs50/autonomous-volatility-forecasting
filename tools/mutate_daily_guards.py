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
worth is **"every plant is noticed by something that names it"** -- a mutant nothing goes red
under is a hole in the guards, and one that reddens something its list does not name is a changed
fact to be read. It is not a claim of one-to-one precision between a defect and a test, and a
reader should not take it for one. The figure also covers only the test files named below: a plant
that broke a test in a file no witness lives in would not be seen.

**What "noticed" is read out of.** A mutant's run is read from pytest's short summary: the tests
it names ``FAILED`` and the tests it names ``ERROR``, which are two counts and not one -- a guard
that reports a violation from a fixture's teardown leaves the test itself passed and its teardown
errored. Both sets are printed for every mutant, and the comparison against the witness list is
over the two together, so a guard that only ever reports in teardown is a bite and not a hole.

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
``moves_ledger`` stops the script. Both ledgers are tracked, and the board pins each by line count
and digest; read both before and after a run.

**The daily path's own two files are kept too**, by a different route because their failure mode
is a different one. ``data/forecasts.jsonl`` and ``data/daily_runs.jsonl`` are not reported
numbers, so a mutant that moves one is printed and nothing more: it neither stops the run nor
changes that mutant's verdict. They are read before the first plant and put back after every
mutant all the same, because a file one mutant wrote is state every later mutant then runs
against, and a checkout left with a changed log costs whoever owns that log. Either may
legitimately be absent, so absence is part of the reading; a file found where one was absent is
**moved aside into the backup directory and never deleted**, since neither file is tracked and the
reading that called it absent may have been taken as much as half an hour earlier.

**The bound on that, said plainly: it holds for the duration of a run of this script and nowhere
else.** Outside a run these two files have no restore at all. What watches ``data/forecasts.jsonl``
is ``tests/test_daily_forecast.py``'s autouse guard, and that *reports* a change from teardown
without putting anything back -- so an ordinary suite run that wrote the repository's own log would
be reported and the file left as written, with no blob to recover from. Tracking them instead was
never the alternative, and ``volab.daily.forecast``'s own docstring says why: until the reveal, the
champion on every forecast line is a *pseudo*-champion (the field is fixed to ``PSEUDO_KIND`` with
no parameter to say otherwise), so a tracked file would put unregistered numbers into git.

Outside the source files it plants into, it writes a ``.orig`` sidecar beside each while that file
is planted; the ledger and own-file copies, and any moved-aside file, under the printed backup
directory; the two ledgers and the daily path's own two files, when a plant moved one; and
pytest's own caches. No store read, no network.

**It refuses to start unless the suite is already green**, because a witness that was red before
the plant would read as "bites". The original text is written to ``<file>.orig`` before planting,
so a ``SIGKILL`` between the plant and the restore leaves the original on disk rather than only in
a dead process's memory; the sidecar is removed once the restore is verified byte for byte.

    uv run --locked python tools/mutate_daily_guards.py

It exits 0 only if every mutant was exact, 1 if any mutant was noticed by nothing, and 2 if none
was missed but some were noticed more widely than the list named against them. The three are
counted and printed separately, because a hole in the guards and a stale witness list are
different findings.

**Code 1 is shared, and the sentence above is not a complete account of it.** Every refusal in
this file is ``raise SystemExit("<text>")``, which prints the text and exits 1; there are fourteen
such sites, among them a missing plant anchor, a ledger that moved, a suite already red, a file
that did not collect, a witness naming an id that does not exist, and every restore failure. So a
1 means *either* some mutant was noticed by nothing *or* the run stopped before it reached a
verdict, and the code cannot tell a reader which. **Read the verdict off the "noticed by nothing"
block at the end of the run, never off ``$?``** -- a halt prints no such block, and a run that
reached the end prints one. Giving the halts a code of their own would be the better fix and is
not done here: ``SystemExit`` carrying a string is what prints it, and an integer code would have
to be printed by hand at fourteen sites, five of which are the move-aside and restore paths this
script's safety rests on. A docstring is the cheap half, and this is it.

**A 1 on a tree carrying a plant that has gone inert is not news either.** ``_exit_code`` returns
1 whenever anything is uncaught, unconditionally, so such a tree exits 1 however many mutants were
exact. What warrants stopping is a *second* uncaught mutant. Both of these were found by review
seats reading this file rather than by its author, who predicted a 2 for the run of 2026-11-10 and
had forgotten that the uncaught branch takes precedence.

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
# are the thing being protected. Both are tracked, so the copy this script takes is the second of
# two routes back; the daily path's own files, below, are neither reported numbers nor tracked.
_LEDGERS = ("data/trials.jsonl", "data/comparisons.jsonl")
# The daily path's own two files. Not reported numbers -- so a plant that moves one is printed
# rather than a stop, and does not change its verdict -- and kept all the same, because a file one
# mutant wrote is state every later mutant then runs against, and a checkout left with a changed
# log costs whoever owns that log. Neither is tracked (``.gitignore`` un-ignores the two ledgers and
# not these), so for whichever of them is present, the copy this script takes is the only route
# back: narrowing this list again would leave real data with nothing behind it.
_OWN_FILES = ("data/forecasts.jsonl", "data/daily_runs.jsonl")
# The three verdicts a mutant can be given, which are three different findings.
_EXACT = "bites"
_WIDE = "BITES WIDELY"
_UNCAUGHT = "SURVIVES"
# The command the trigger-installer plant calls. Joined from two pieces because this file sits under
# ``tools/``, which the very guard being tested scans: written whole, the script would be the
# installer it plants and the guard would be red before any plant went in.
# The scheduler tool this script plants a call to, spelled so that **this file** does not read as
# an installer to ``tests/test_daily_trigger.py``'s scan while the text it *plants* does. The scan
# folds a concatenation or a ``join`` of literals (P4-08b1 closed that hole, and closing it made it
# fire on this very line), but it does not fold a name bound earlier -- a limit its own docstring
# states. So the pieces go through a name. Keep it that way: the plant must carry the real tool
# name into the file it mutates, and the probe must not itself look like the thing it hunts.
_LAUNCH_PREFIX = "launch"
_LAUNCH_TOOL = _LAUNCH_PREFIX + "ctl"


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
            "test_a_torn_fragment_is_still_in_the_file_and_still_skipped_after_an_append",
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
        before='    with path.open("ab+") as handle:',
        after='    with path.open("wb") as handle:',
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
        witnesses=(
            "test_a_torn_fragment_is_still_in_the_file_and_still_skipped_after_an_append",
            "test_an_append_after_a_torn_final_line_in_the_run_log_is_well_formed_and_loses_nothing",
        ),
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
    # **The one list this script's own history proves can go stale under a passing suite.** This
    # plant named ``test_a_timeout_kills_the_stages_own_children_too``, and from ``28d81b3`` it
    # stopped reaching it: ``_sweep_the_group`` SIGKILLs the stage's group from ``_run_one``'s
    # outermost ``finally``, so the grandchild dies whether the deadline's ``killpg`` ran or not and
    # that test passes under the plant. The probe called the result ``SURVIVES`` -- the word for a
    # hole in the guards -- said of a plant that had gone inert. ``89e0c88`` closed it by adding the
    # test named below, which monkeypatches the sweep out so only the deadline's kill can reach the
    # grandchild. **Measured, not reasoned:** planted into a ``git archive`` copy of ``59a3590``
    # with ``PYTHONPATH`` pointed at that copy, the six files above give ``1 failed, 491 passed``
    # and the one failure is this test alone.
    Mutant(
        label="the timeout kills the stage but not the stage's children",
        relative_path="src/volab/daily/runner.py",
        before="                os.killpg(process.pid, signal.SIGKILL)",
        after="                process.kill()",
        witnesses=("test_the_timeout_itself_kills_the_group_not_only_the_sweep_after_the_reap",),
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
        label="the live region is reported as the sealed holdout",
        relative_path="src/volab/daily/__init__.py",
        before="    if splits.region_of(as_of) is Region.LIVE:",
        after="    if False and splits.region_of(as_of) is Region.LIVE:",
        witnesses=(
            "test_a_live_date_is_refused_as_live_and_not_as_the_holdout",
            "test_the_wording_follows_the_configured_holdout_end_and_not_a_hardcoded_date",
        ),
    ),
    Mutant(
        label="the read loop keeps waiting after the stage's own process has exited",
        relative_path="src/volab/daily/runner.py",
        before=(
            "                        break  # the stage has exited; something else holds the pipe"
        ),
        after=(
            "                        pass  # the stage has exited; something else holds the pipe"
        ),
        witnesses=(
            "test_a_stage_that_exits_leaving_the_pipe_held_is_not_waited_for_past_its_own_exit",
            "test_a_timeout_returns_promptly_even_when_a_descendant_left_the_group_holding_the_pipe",
        ),
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


def _base_name(identifier: str) -> str:
    """A pytest id with any parametrisation dropped: ``name[case]`` -> ``name``."""
    return identifier.split("[", 1)[0]


def _test_name(line: str) -> str:
    """The test named on a pytest line, out of one spelling for every reading here.

    Everything after the first ``::``, up to the ``" - "`` pytest puts before a failure reason,
    stripped. One function because two readings that disagree about how an id is spelled is the
    defect this whole file was built to stop being possible: the short-summary parse once cut at
    the first space, and a collection parse that cut differently would make the start-up check
    below reject exactly the ids the verdict accepts.
    """
    return line.split("::", 1)[1].split(" - ")[0].strip()


def _pytest_command(suite: tuple[str, ...]) -> list[str]:
    """The command run for every mutant: the whole of each ``suite`` file, quiet, summary only.

    ``-rfE`` and not ``-rf``, because ``-r`` *selects* what the short summary reports rather than
    adding to pytest's default. A test that passes while a fixture raises in its teardown is
    counted an error and not a failure, and under ``-rf`` pytest printed no ``ERROR`` line at all
    -- so no reading below, matching whatever verbs it liked, could have seen one. The guard that
    reports that way is ``tests/test_daily_forecast.py``'s autouse guard on
    ``data/forecasts.jsonl``: a mutant that made production code write that file is caught at
    runtime, and under the old flag it was read here as noticed by nothing.
    """
    return [sys.executable, "-B", "-m", "pytest", "-q", "--tb=no", "-rfE", *suite]


def _reported_tests(stdout: str, suite: tuple[str, ...]) -> tuple[frozenset[str], frozenset[str]]:
    """The tests the short summary names failed, and the tests it names errored, by name.

    Two sets and not one, because pytest counts them separately and a reader needs to see which
    is which: a test whose body fails is ``FAILED``, and one that passes while its fixture raises
    in teardown is ``ERROR``. The same test can be printed under both -- a failing body still has
    a teardown -- so a name can appear in each.

    Only lines naming one of the ``suite`` files count, which is what the prefix match is for: it
    ties a summary line to a file this run was asked for, so text that merely looks like one -- a
    nested pytest run's own output reaching the same stream, say -- is not read as a result of this
    run.

    The name is everything after ``::`` up to the ``" - "`` pytest puts before the reason, and
    **not** up to the first space, because an id written to be read contains spaces.
    ``test_a_holdout_date_is_refused_as_sealed`` is parametrised over prose ids, and cutting at the
    first space printed ``...sealed[in``, ``...sealed[mid`` and ``...sealed[the`` -- one per
    parametrisation, so the count was right while none of the three ids existed. The bound accepted
    with that: an id containing ``" - "`` itself would still be cut, and none in this repository
    does. Taking the whole id is also what a later check needs -- "reject a bracketed witness that
    is not among the ids its test generates" would reject a correctly spelled ``[mid holdout]``
    while the parse cannot produce one.

    **A file that did not collect is a stop, not a line to drop.** pytest prints that as
    ``ERROR <path>`` with no test after it, and every claim about a witness in a file that never
    imported is vacuous -- while a dropped line would leave both sets empty, which is how "noticed
    by nothing" is printed.
    """
    failed: set[str] = set()
    errored: set[str] = set()
    for line in stdout.splitlines():
        for path in suite:
            if line.startswith(f"FAILED {path}::"):
                failed.add(_test_name(line))
            elif line.startswith(f"ERROR {path}::"):
                errored.add(_test_name(line))
            elif line == f"ERROR {path}" or line.startswith(f"ERROR {path} - "):
                raise SystemExit(f"{path} did not collect; pytest reported {line!r}")
    return frozenset(failed), frozenset(errored)


def _verdict(failed: frozenset[str], errored: frozenset[str], witnesses: tuple[str, ...]) -> str:
    """The word printed for a mutant: one of the three above, read over both sets together.

    The union and not the failures alone, which corrupted the verdict in both directions. A plant
    whose only catcher reports from a fixture's teardown fails nothing and errors whatever test that
    fixture was watching, and reading failures alone called that ``SURVIVES`` -- the word for a hole
    in the guards, said of a guard that had done its job. A plant that fails its witness *and*
    errors some unrelated test read as ``bites``, the verdict nothing invites a reader to question,
    when it had bitten wider than its list. ``witnesses`` keeps the meaning it always had: the set a
    mutant must leave red, whichever verb each was printed under.

    **A witness may be spelled with or without a parametrisation id, and the two mean different
    things.** pytest prints ``name[id]``, so a bare ``name`` compared raw could never appear in the
    red set at all and its mutant read wide while the guard it names had fired. A bare witness is
    therefore read as "at least one parametrisation of this went red", which is a choice and not a
    derivation: the other reading, *every* parametrisation, would tie a witness list to a
    parametrisation count nobody writing one looks up. So the bare spelling is legitimate rather
    than something to be corrected -- it says "any one of them" -- and the one thing it cannot do is
    demand a *particular* parametrisation. That is the bracketed form's job, so a bracketed witness
    is still compared exactly and the bracketed side is never folded: two mutants name two different
    ids of the one test because the id says which code path broke, and folding would let a plant
    that broke the other one satisfy them. The rule is this simple because no two mutants' witness
    sets collide once the bare names are allowed their ids, so the hazard is inside a mutant's own
    list and not between two of them.
    """
    red = failed | errored
    wanted_ids = {witness for witness in witnesses if "[" in witness}
    wanted_bases = {witness for witness in witnesses if "[" not in witness}
    bases_red = {_base_name(identifier) for identifier in red}
    exact = (
        wanted_ids <= red
        and wanted_bases <= bases_red
        and all(
            identifier in wanted_ids or _base_name(identifier) in wanted_bases for identifier in red
        )
    )
    if exact:
        return _EXACT
    return _UNCAUGHT if not red else _WIDE


def _exit_code(uncaught: int, wide: int) -> int:
    """0 if every mutant was exact, 1 if any was noticed by nothing, 2 if some only bit wide.

    The 1 is not this function's alone: every halt in this file raises ``SystemExit`` with a string
    and so exits 1 too, which the module docstring states and no code here distinguishes. The
    uncaught branch also takes unconditional precedence, so a run with one uncaught mutant and
    twenty-seven wide ones exits 1 and the wide count is invisible in ``$?``.

    Three codes and not two. One code made the two findings indistinguishable, and they are not
    the same thing: a mutant nothing noticed is a hole in the guards, which is what this script
    exists to find, while one noticed more widely than its list is a fact about the list or the
    plant. A wide run does not exit 0 either -- the sentence this script's figure is cited for is
    "each failing exactly the witnesses named against it and no other", and a wide result is that
    sentence not holding.
    """
    if uncaught:
        return 1
    return 2 if wide else 0


def _failing_tests(suite: tuple[str, ...]) -> tuple[frozenset[str], frozenset[str]]:
    """Every test in the ``suite`` files that went red, from a run of the whole of each file.

    **The whole files and not ``-k <witness>``**, which is what the first version of this script
    ran. Selecting the witness can only establish that the witness fails; the claim being made is
    that a planted defect fails **that witness and no other**, and a selected run cannot see the
    others. The difference matters in both directions: a mutant that also breaks four unrelated
    tests is a blunter instrument than the sentence claims, and one whose witness fails for an
    incidental reason would read the same.

    Two sets come back, the failed and the errored, as ``_reported_tests`` reads them out of the
    short summary; "failing" here means red under either verb.
    """
    completed = subprocess.run(
        _pytest_command(suite),
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    # The reading first, so that a file which did not collect is named by the stop that knows
    # which file it was. The check below is the one it cannot make: it catches a run that
    # produced no passes at all, whatever went wrong, including ways that print no summary.
    reported = _reported_tests(completed.stdout, suite)
    if "error" in completed.stdout.lower() and "passed" not in completed.stdout:
        raise SystemExit(f"the suite did not run:\n{completed.stdout}\n{completed.stderr}")
    return reported


def _collect_command(suite: tuple[str, ...]) -> list[str]:
    """``--collect-only`` over the same files every mutant runs: one node id a line, then a count.

    No plant is in place when this runs and none is needed: collection imports the modules and
    evaluates the ``parametrize`` decorators, which is where the ids come from, without executing
    a single test body.
    """
    return [sys.executable, "-B", "-m", "pytest", "-q", "--collect-only", *suite]


def _collected_tests(stdout: str, suite: tuple[str, ...]) -> frozenset[str]:
    """Every test id ``--collect-only`` listed for the ``suite`` files, by name.

    The same prefix match the short-summary reading makes, and for the same reason: a line naming
    a file this run did not ask for says nothing about the ids of a test in the files every mutant
    runs. The name comes out of ``_test_name``, so this reading and the verdict's cannot disagree
    about how an id is spelled -- which is the only reason the check below can be trusted to
    accept ``[mid holdout]`` and reject ``[mid``.
    """
    return frozenset(
        _test_name(line)
        for line in stdout.splitlines()
        for path in suite
        if line.startswith(f"{path}::")
    )


def _unknown_ids(
    witnesses: tuple[str, ...], collected: frozenset[str]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Each bracketed witness no parametrisation generates, with the ids its test does generate.

    **Bracketed witnesses only.** A bare name is resolved by ``_test_files``, which refuses one not
    defined in exactly one test file, and ``_verdict`` reads it as "any one parametrisation of this
    went red" -- so comparing a bare name against collected *ids* would reject all but two of the
    witnesses in ``MUTANTS``. The reach of this check is therefore narrow and worth stating: it
    establishes that a bracketed id exists in collection, nothing about whether it can go red, and
    nothing at all about the plant that is supposed to make it go red.
    """
    unknown = []
    for witness in witnesses:
        if "[" not in witness or witness in collected:
            continue
        base = _base_name(witness)
        unknown.append(
            (witness, tuple(sorted(i for i in collected if _base_name(i) == base and "[" in i)))
        )
    return tuple(unknown)


def _check_bracketed_witnesses(suite: tuple[str, ...], collected: frozenset[str]) -> None:
    """Stop before any plant if a mutant names a parametrisation that does not exist.

    A witness id with a typo in it can never appear in the red set, so the mutant carrying it can
    never read exact. What it reads instead is ``BITES WIDELY``: the plant still reddens the
    correctly spelled id, so the red set is not empty, and only a plant that reddened nothing at
    all would print ``SURVIVES``. The corruption therefore runs the *unfavourable* way -- a guard
    that fired is reported as having bitten past its list, and a list that has a typo in it looks
    like a list that is wrong about the plant. Cheap to catch, so caught here: ask pytest what ids
    it generates.

    An empty collection is the stop and not the finding: read as data it says no id exists, which
    would name every bracketed witness a typo at once.
    """
    if not collected:
        raise SystemExit(f"{' '.join(suite)} collected no test at all; nothing can be checked")
    for mutant in MUTANTS:
        for witness, generated in _unknown_ids(mutant.witnesses, collected):
            raise SystemExit(
                f"witness {witness!r} of {mutant.label!r} names a parametrisation that does not"
                f" exist; {_base_name(witness)} generates {list(generated) or 'no ids'}"
            )


def _collected(suite: tuple[str, ...]) -> frozenset[str]:
    """Run the collection and read it, or stop naming what pytest printed."""
    completed = subprocess.run(
        _collect_command(suite),
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"collection failed:\n{completed.stdout}\n{completed.stderr}")
    return _collected_tests(completed.stdout, suite)


def _read_own_files() -> dict[str, bytes | None]:
    """The bytes each of the daily path's own files holds, ``None`` for one that is absent."""
    return {
        name: ((REPO / name).read_bytes() if (REPO / name).exists() else None)
        for name in _OWN_FILES
    }


def _put_own_files_back(kept: dict[str, bytes | None], backup: pathlib.Path) -> tuple[str, ...]:
    """Put each of the daily path's own files back as ``kept`` found it; the names that had moved.

    Four cases, because absence is part of the reading on both sides. **Unchanged** does nothing.
    **Present and changed** has the bytes it is about to write over copied into ``backup`` first,
    then the baseline written back, and both verified. **Absent before and present now** is *moved
    aside* into ``backup`` and never deleted. **Present before and gone now** is simply written
    back: the bytes that were there are the ones going back, so there is nothing to keep.

    The second and third cases are the same argument. Neither of these files is tracked, so bytes
    lost here have no blob and no copy behind them, and the reading they are judged against was
    taken before a run that takes the better part of half an hour -- another session's daily job, or
    a person, writes the same paths, so what is found is not certainly the plant's. Keeping it costs
    a copy and cannot lose anything. ``_put_ledgers_back`` deliberately does not do this, and the
    asymmetry is the point: the two ledgers are tracked, so ``git restore --source=<sha>`` recovers
    them whatever this script does. Same operation, different consequence.

    Raises if any of that did not land, naming the printed backup directory, for the reason
    ``_put_ledgers_back`` raises: a tool whose job is to find defects must not commit one.
    """
    moved: list[str] = []
    for name, data in kept.items():
        path = REPO / name
        current = path.read_bytes() if path.exists() else None
        if current == data:
            continue
        moved.append(name)
        if data is None:
            # A directory per file kept, so two mutants writing the same path cannot collide.
            aside = pathlib.Path(tempfile.mkdtemp(dir=backup, prefix="moved-aside-")) / path.name
            path.rename(aside)
            if path.exists() or aside.read_bytes() != current:
                raise SystemExit(f"FAILED TO MOVE {name} ASIDE; look in {backup}")
            print(f"moved aside rather than deleted: {name} -> {aside}", flush=True)
            continue
        if current is None:
            path.write_bytes(data)
            if path.read_bytes() != data:
                raise SystemExit(f"FAILED TO RESTORE {name}; a copy is in the printed backup")
            print(f"restored {name}, which had been removed", flush=True)
            continue
        aside = pathlib.Path(tempfile.mkdtemp(dir=backup, prefix="replaced-")) / path.name
        aside.write_bytes(current)
        path.write_bytes(data)
        if path.read_bytes() != data or aside.read_bytes() != current:
            raise SystemExit(f"FAILED TO RESTORE {name}; a copy is in the printed backup")
        print(f"restored {name}; the bytes it replaced are in {aside}", flush=True)
    return tuple(moved)


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
    own_files = _read_own_files()
    backup = pathlib.Path(tempfile.mkdtemp(prefix="mutate-ledgers-"))
    for name, data in {**ledgers, **own_files}.items():
        # A file that was absent is copied nowhere: there are no bytes to keep, and
        # ``_put_own_files_back`` puts absence back by moving whatever now exists into here.
        if data is not None:
            (backup / pathlib.Path(name).name).write_bytes(data)
    print(
        "copies of the ledgers and of whichever daily files exist, should a kill leave one moved:",
        backup,
    )
    _check_bracketed_witnesses(suite, _collected(suite))
    if any(_failing_tests(suite)):
        raise SystemExit("the suite is already red; fix that before planting anything")
    # Two lists and not one, because they are two findings: a mutant nothing noticed is a
    # statement about the guards, and one noticed more widely than its list is a statement about
    # the list or the plant.
    wide: list[str] = []
    uncaught: list[str] = []
    for mutant in MUTANTS:
        target = REPO / mutant.relative_path
        original = target.read_text(encoding="utf-8")
        if mutant.before not in original:
            raise SystemExit(f"plant anchor missing in {mutant.relative_path}: {mutant.label}")
        sidecar = target.with_suffix(target.suffix + ".orig")
        sidecar.write_text(original, encoding="utf-8")
        target.write_text(original.replace(mutant.before, mutant.after, 1), encoding="utf-8")
        try:
            failed, errored = _failing_tests(suite)
        finally:
            target.write_text(original, encoding="utf-8")
            if target.read_text(encoding="utf-8") != original:
                raise SystemExit(
                    f"FAILED TO RESTORE {mutant.relative_path}; the original is in {sidecar}"
                )
            sidecar.unlink()
            moved = _put_ledgers_back(ledgers)
            # After the ledgers, so that a restore failure here cannot skip theirs.
            own_moved = _put_own_files_back(own_files, backup)
        if moved and not mutant.moves_ledger:
            raise SystemExit(f"a ledger moved under {mutant.label!r}; it has been put back")
        if own_moved:
            # Printed and nothing more. These are not reported numbers, so a mutant that moved
            # one neither stops the run nor changes its verdict below; it is put back so that the
            # next mutant runs against the state this one found, and named so that a reader can
            # see which mutant wrote where.
            print(f"put back under {mutant.label!r}: {', '.join(own_moved)}", flush=True)
        verdict = _verdict(failed, errored, mutant.witnesses)
        # Both sets, named, and the errored one even when it is empty: a guard that reports in
        # teardown is counted errored and not failed, and a line that printed only non-empty sets
        # would leave a reader unable to tell "none errored" from "errors not looked for".
        print(
            f"{verdict:12} | {mutant.label} -> failed {sorted(failed) or 'nothing'};"
            f" errored {sorted(errored) or 'nothing'}",
            flush=True,
        )
        finding = f"{mutant.label}: failed {sorted(failed)}, errored {sorted(errored)}"
        if verdict == _UNCAUGHT:
            uncaught.append(finding)
        elif verdict == _WIDE:
            wide.append(finding)

    print()
    print(
        f"{len(MUTANTS) - len(wide) - len(uncaught)} exact, {len(wide)} wide,"
        f" {len(uncaught)} noticed by nothing, of {len(MUTANTS)} mutants"
    )
    if uncaught:
        print("noticed by nothing -- a hole in the guards, which is what this script hunts:")
        for finding in uncaught:
            print(f"  - {finding}")
    if wide:
        print("noticed more widely than their list -- a fact about the list or the plant:")
        for finding in wide:
            print(f"  - {finding}")
    if not uncaught and not wide:
        print(
            f"all {len(MUTANTS)} mutants left red exactly the tests named against them and no"
            " others -- failed or errored, a bare witness counting any one of its parametrisations"
        )
    return _exit_code(len(uncaught), len(wide))


if __name__ == "__main__":
    sys.exit(main())
