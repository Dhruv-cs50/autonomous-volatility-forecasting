"""The evaluation child (P5-C1): one proposal, one fresh interpreter, two ledgers of its own.

What these pin, each against the ruling it comes from:

* **R184** -- the child's command line has no date option and no stride. The witness walks the
  parser's actions and asks each one's ``type``; it does not name an option, so an option added
  next year is covered by the same sentence.
* **R183** -- an evaluation appends to ``data/agent_trials.jsonl`` and
  ``data/agent_comparisons.jsonl`` and to nothing else. Compared by content, before and after,
  with the reported ledgers both present and absent: a count of lines cannot see an edit.
* **R97** -- the agent ledger's lines are *byte-identical* to the ones ``append_trials`` writes,
  checked by writing the same trial through both and comparing the files.
* **The reserved cohort namespace** -- parametrised over the real registry in
  ``volab.compare.cohorts`` plus the baseline's name, so a cohort registered later is refused the
  day it is added.
* **Coverage before fitting** -- a proposal naming a column too sparse for the fixed window is
  refused with the reason ``coverage_refusal`` gives, no trial line is written and no model is
  fitted.
* **Re-validation at the boundary** -- the gate's verdict does not travel with the proposal file,
  so a file that was never gated is refused by the child.

Nothing here reads the real store or writes the real ``data/``: the panel and targets are
synthetic, ``load_for`` and ``_persisted`` are replaced by attribute, and the storage root is a
``tmp_path``. The one subprocess test launches the child on a file it refuses *before* it opens
any path, so it has no root to be handed and nothing to write.

The window constants are patched down in the tests that need a toy panel (a seven-month panel has
no 250 sessions of training). That is the tests' doing: the shipped constants are pinned by
``test_the_child_fixes_the_window_it_mirrors_from_the_walk_forward_cli``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd
import pytest
from test_walkforward_cli import _settings_with_root, _toy_panel_and_targets_by_horizon

import volab.candidate as candidate
import volab.candidate.__main__ as cli
import volab.walkforward as wf
from volab.agent.vocabulary import (
    AGENT_COHORT_PATTERN,
    REFERENCE_CONTESTANTS,
    Proposal,
    agent_cohort_name,
)
from volab.compare.cohorts import COHORTS
from volab.config import Settings, load_settings
from volab.features.build import build_features
from volab.models.base import Cohort
from volab.sessions import get_calendar
from volab.walkforward import (
    BASELINE_COHORT_NAME,
    LOSSES,
    Trial,
    append_trials,
    coverage_refusal,
    scoreable_origins,
)

REPO = Path(__file__).resolve().parents[1]
SPRINT = "2026-11-10-testsprint"
DEV_END = load_settings().splits.dev_end
# Late enough that the toy panel carries more than the 20 training rows the tests ask for.
TOY_FIRST = dt.date(2024, 4, 15)
THREE = ("har_levels", "har_semilog", "random_walk")


def _proposal(
    extra: tuple[str, ...] = ("vvix",),
    reference: tuple[str, ...] = THREE,
    horizon: int = 1,
    index: int = 1,
) -> Proposal:
    return Proposal(
        proposal_id=f"{SPRINT}-{index:02d}",
        family="harx",
        extra_regressors=extra,
        symbol="SPY",
        horizon=horizon,
        reference=reference,
    )


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _file_for(path: Path, proposal: Proposal) -> Path:
    """The proposal file the sprint runner is specified to write: the fields and ``proposal_id``."""
    return _write(path, asdict(proposal))


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _lines(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


class Wired:
    """A synthetic panel in place of the store, and the settings that point at ``tmp_path``."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = tmp_path
        self.settings: Settings = _settings_with_root(load_settings(), tmp_path)
        self.panel, self.targets = _toy_panel_and_targets_by_horizon()
        self.stored: pd.DataFrame | None = None
        self.fits = 0
        monkeypatch.setattr(candidate, "load_for", self._load_for)
        monkeypatch.setattr(candidate, "_persisted", lambda *_: self.stored)
        monkeypatch.setattr(cli, "load_settings", lambda: self.settings)
        # A seven-month toy panel cannot carry 250 sessions of training, so the window is moved
        # onto it. The shipped values are pinned by their own test.
        monkeypatch.setattr(candidate, "FIRST_ORIGIN", TOY_FIRST)
        monkeypatch.setattr(candidate, "MINIMUM_TRAINING", 20)
        monkeypatch.setattr(candidate, "STRIDE", 2)

    def _load_for(
        self, settings: Settings, symbol: str, horizon: int, *, report: Callable[[str], None]
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        return self.panel, self.targets[horizon]

    def persist_with_sparse(self, column: str, until: dt.date) -> None:
        """A stored feature table whose ``column`` is null on every session up to ``until``."""
        calendar = get_calendar(self.settings)
        last = max(self.panel["as_of"])
        table = build_features(self.panel, through=last, calendar=calendar)
        days = [pd.Timestamp(day).date() for day in table["as_of"]]
        table[column] = [
            float("nan") if day <= until else value
            for day, value in zip(days, table[column], strict=True)
        ]
        self.stored = table

    def ledger(self, name: str) -> Path:
        return self.root / "data" / name


@pytest.fixture
def wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Wired:
    return Wired(tmp_path, monkeypatch)


AT = dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.UTC)
SHA = "0123456789abcdef0123456789abcdef01234567"


# ---------------------------------------------------------------------------
# R184: no date, no stride.
# ---------------------------------------------------------------------------


def _date_typed(parser: argparse.ArgumentParser) -> list[str]:
    """Options whose ``type`` is the date parser. ``==``, not ``is``: ``dt.date.fromisoformat`` is
    a classmethod, so each attribute access builds a new bound-method object and an ``is``
    comparison is false for the very parser it is meant to catch (the test below proves it)."""
    return [
        "/".join(action.option_strings) or action.dest
        for action in parser._actions
        if action.type == dt.date.fromisoformat
    ]


def test_the_childs_command_line_has_no_date_typed_option() -> None:
    """R184, derived: no action's ``type`` is ``dt.date.fromisoformat``, whatever it is called.

    This is the walk-forward CLI's own date parser, which is how ``--from`` and ``--to`` are
    typed there. It would miss an option that took a date as a string, which is why the options
    are also pinned to exactly the two the contract names.
    """
    parser = cli.build_parser()
    assert _date_typed(parser) == []
    options = sorted(opt for action in parser._actions for opt in action.option_strings)
    assert options == ["--help", "--proposal", "--result", "-h"]


def test_the_date_witness_bites_on_a_parser_that_takes_a_date() -> None:
    """Non-vacuity: the walk-forward CLI's own ``--from`` shape is found by the same scan."""
    planted = argparse.ArgumentParser()
    planted.add_argument("--from", type=dt.date.fromisoformat)
    assert _date_typed(planted) == ["--from"]


def test_proposal_and_result_are_both_required() -> None:
    parser = cli.build_parser()
    required = {action.dest for action in parser._actions if action.required}
    assert required == {"proposal", "result"}
    for argv in ([], ["--proposal", "p.json"], ["--result", "r.json"]):
        with pytest.raises(SystemExit) as raised:
            parser.parse_args(argv)
        assert raised.value.code == cli.EXIT_USAGE


def test_the_child_fixes_the_window_it_mirrors_from_the_walk_forward_cli() -> None:
    """R184's three numbers as shipped; the fourth, the last origin, is ``dev_end``.

    The tests above move them onto a toy panel; this one does not.
    """
    assert dt.date(2021, 1, 4) == candidate.FIRST_ORIGIN
    assert candidate.STRIDE == 5
    assert candidate.MINIMUM_TRAINING == 250


def test_the_child_hands_the_driver_the_window_it_fixes_and_dev_end(
    wired: Wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, object]] = []
    real = candidate.run_configuration

    def spy(*args: object, **kwargs: object) -> wf.ConfigurationOutcome:
        seen.append(dict(kwargs))
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(candidate, "run_configuration", spy)
    candidate.evaluate_proposal(_proposal(), settings=wired.settings, at=AT, sha=SHA, dirty=False)
    (call,) = seen
    assert call["first"] == TOY_FIRST  # the patched FIRST_ORIGIN, nothing else
    assert call["last"] == wired.settings.splits.dev_end
    assert call["every"] == 2
    assert call["minimum_training"] == 20


# ---------------------------------------------------------------------------
# The ledgers: R183 and R97.
# ---------------------------------------------------------------------------


def test_the_agent_ledger_lines_are_byte_identical_to_what_append_trials_writes(
    tmp_path: Path,
) -> None:
    """If the user later merges the ledgers the migration is ``cat``: so the bytes must match."""
    trial = Trial(
        model="harx+vvix",
        symbol="SPY",
        horizon=5,
        loss="qlike",
        origins=40,
        value=0.25,
        dropped=1,
        requested=44,
        skipped=3,
        every=5,
        first_origin=dt.date(2021, 1, 4),
        last_origin=dt.date(2026, 11, 3),
        cohort=agent_cohort_name(SPRINT, 1),
        refused=2,
    )
    nan = replace(trial, loss="mse", value=float("nan"))
    reported = tmp_path / "reported"
    agent = tmp_path / "agent"
    assert append_trials([trial, nan], root=reported, at=AT, sha=SHA, dirty=None) == 2
    assert candidate.append_agent_trials([trial, nan], root=agent, at=AT, sha=SHA, dirty=None) == 2
    written = (agent / candidate.AGENT_TRIAL_LEDGER).read_bytes()
    assert written == (reported / wf.TRIAL_LEDGER).read_bytes()
    keys = set(json.loads(written.splitlines()[0]))
    assert keys == {"at", "git_sha", "git_dirty", *trial.record()}


def test_the_ledger_constants_are_the_two_agent_files_and_neither_reported_one() -> None:
    assert Path("data") / "agent_trials.jsonl" == candidate.AGENT_TRIAL_LEDGER
    assert Path("data") / "agent_comparisons.jsonl" == candidate.AGENT_COMPARISON_LEDGER
    reported = {wf.TRIAL_LEDGER, Path("data") / "comparisons.jsonl"}
    assert not reported & {candidate.AGENT_TRIAL_LEDGER, candidate.AGENT_COMPARISON_LEDGER}


def test_counting_an_absent_agent_ledger_is_zero_and_a_present_one_counts_its_lines(
    tmp_path: Path,
) -> None:
    assert candidate.count_agent_trials(tmp_path) == 0
    path = tmp_path / candidate.AGENT_TRIAL_LEDGER
    path.parent.mkdir(parents=True)
    path.write_text('{"a": 1}\n\n{"a": 2}\n', encoding="utf-8")
    assert candidate.count_agent_trials(tmp_path) == 2


@pytest.mark.parametrize("seeded", [False, True], ids=["reported-ledgers-absent", "present"])
def test_a_full_evaluation_writes_the_agent_ledgers_and_leaves_the_reported_ones_alone(
    wired: Wired, seeded: bool
) -> None:
    """R183, by content: the reported ledgers are compared before and after, not counted."""
    reported = [wired.ledger("trials.jsonl"), wired.ledger("comparisons.jsonl")]
    if seeded:
        wired.ledger("trials.jsonl").parent.mkdir(parents=True)
        for path in reported:
            path.write_text('{"sentinel": "' + path.name + '"}\n', encoding="utf-8")
    before = [_digest(path) for path in reported]
    repo_before = [
        _digest(REPO / "data" / "trials.jsonl"),
        _digest(REPO / "data" / "comparisons.jsonl"),
    ]

    proposal = _proposal(reference=(*THREE, "har_levels_mz"))
    outcome = candidate.evaluate_proposal(
        proposal, settings=wired.settings, at=AT, sha=SHA, dirty=False
    )

    assert [_digest(path) for path in reported] == before
    assert [
        _digest(REPO / "data" / "trials.jsonl"),
        _digest(REPO / "data" / "comparisons.jsonl"),
    ] == repo_before
    assert not outcome.refused
    assert outcome.cohort == agent_cohort_name(SPRINT, 1)
    assert re.fullmatch(AGENT_COHORT_PATTERN, outcome.cohort)

    # Four reference contestants plus the candidate, each scored on every loss.
    expected = 5 * len(LOSSES)
    assert outcome.written == expected == len(outcome.trials)
    lines = _lines(wired.ledger("agent_trials.jsonl"))
    assert len(lines) == expected == candidate.count_agent_trials(wired.root)
    assert {line["cohort"] for line in lines} == {outcome.cohort}
    assert {line["model"] for line in lines} == {*THREE, "har_levels_mz", "harx+vvix"}
    assert {line["every"] for line in lines} == {2}
    assert {line["at"] for line in lines} == {AT.isoformat()}


def test_the_comparison_records_carry_the_origin_dates_the_reported_file_lacks(
    wired: Wired,
) -> None:
    """R183's added fields: ISO dates, inside the dev window, and nothing else changed."""
    outcome = candidate.evaluate_proposal(
        _proposal(), settings=wired.settings, at=AT, sha=SHA, dirty=True
    )
    records = _lines(wired.ledger("agent_comparisons.jsonl"))
    assert len(records) == len(outcome.comparisons) == len(LOSSES)
    assert {record["loss"] for record in records} == set(LOSSES)
    first = TOY_FIRST
    for record in records:
        for key in ("first_origin", "last_origin"):
            assert isinstance(record[key], str)
        low = dt.date.fromisoformat(str(record["first_origin"]))
        high = dt.date.fromisoformat(str(record["last_origin"]))
        assert first <= low <= high <= wired.settings.splits.dev_end
        assert record["cohort"] == outcome.cohort
        assert record["git_dirty"] is True
        assert record["git_sha"] == SHA
        assert record["at"] == AT.isoformat()
        assert record["origins"] and record["stride"] == 2
    # Everything the reported file's records carry is here too: the schema only grew.
    reported_keys = set(outcome.comparisons[0].comparison.record())
    assert set(records[0]) == {
        "at",
        "git_sha",
        "git_dirty",
        "first_origin",
        "last_origin",
        *reported_keys,
    }


# ---------------------------------------------------------------------------
# Reference contestants.
# ---------------------------------------------------------------------------


def test_resolve_reference_returns_the_named_models_in_order() -> None:
    models = candidate.resolve_reference(REFERENCE_CONTESTANTS)
    assert tuple(model.name for model in models) == REFERENCE_CONTESTANTS
    assert candidate.resolve_reference(("random_walk", "har_levels"))[0].name == "random_walk"


@pytest.mark.parametrize(
    "names", [("har_levels", "egarch11_skewt"), ("nonesuch",), ("har_levels", "har_levels")]
)
def test_resolve_reference_refuses_an_unknown_or_repeated_contestant(
    names: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="reference"):
        candidate.resolve_reference(names)


# ---------------------------------------------------------------------------
# The reserved cohort namespace.
# ---------------------------------------------------------------------------

REGISTERED = [*COHORTS, BASELINE_COHORT_NAME]


def test_the_registry_the_namespace_test_is_parametrised_over_is_not_empty() -> None:
    """A parametrised test over an empty list passes by collecting nothing."""
    assert len(COHORTS) >= 3
    assert BASELINE_COHORT_NAME in REGISTERED


@pytest.mark.parametrize("name", REGISTERED)
def test_a_registered_cohort_name_is_refused_and_nothing_is_written(
    name: str, wired: Wired
) -> None:
    with pytest.raises(candidate.CohortNameRefused, match=re.escape(name)):
        candidate.require_agent_cohort(name)
    models = candidate.resolve_reference(THREE)
    with pytest.raises(candidate.CohortNameRefused):
        candidate.evaluate_cohort(
            _proposal(),
            Cohort(name, models),
            settings=wired.settings,
            at=AT,
            sha=SHA,
            dirty=False,
        )
    assert not wired.ledger("agent_trials.jsonl").exists()
    assert not wired.ledger("agent_comparisons.jsonl").exists()


def test_a_name_in_the_reserved_pattern_is_accepted() -> None:
    name = agent_cohort_name(SPRINT, 7)
    assert candidate.require_agent_cohort(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "agent-2026-11-10-a1b2c3d4-03/x",
        "agent-2026-11-10-a1b2c3d4-03\n",
        "xagent-2026-11-10-a1b2c3d4-03",
        "",
    ],
)
def test_the_pattern_is_matched_whole_not_as_a_prefix(name: str) -> None:
    with pytest.raises(candidate.CohortNameRefused):
        candidate.require_agent_cohort(name)


# ---------------------------------------------------------------------------
# Coverage is decided before any model is fitted.
# ---------------------------------------------------------------------------


def test_a_proposal_naming_a_sparse_column_is_refused_before_fitting_with_the_reason_verbatim(
    wired: Wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3: ``iv_h1`` is non-null on 912 of 2,312 dev rows, so a window from 2021 cannot use it.

    The reason is compared with what ``coverage_refusal`` returns for the same inputs, and
    ``evaluate`` is replaced by something that fails if it is reached.
    """
    sparse = "vvix"
    wired.persist_with_sparse(sparse, until=dt.date(2024, 6, 14))

    def forbidden(*_: object, **__: object) -> object:
        raise AssertionError("a model was fitted although the window cannot support the column")

    monkeypatch.setattr(wf, "evaluate", forbidden)
    proposal = _proposal(extra=(sparse,))
    outcome = candidate.evaluate_proposal(
        proposal, settings=wired.settings, at=AT, sha=SHA, dirty=False
    )

    origins = scoreable_origins(
        wired.settings, wired.targets[1], first=TOY_FIRST, last=DEV_END, every=2
    )
    assert wired.stored is not None
    expected = coverage_refusal(
        wired.stored,
        wired.targets[1],
        ("har_d_proxy", "har_w_proxy", "har_m_proxy", sparse),
        origins,
        horizon=1,
        minimum_training=20,
    )
    assert expected is not None
    assert outcome.refused
    assert outcome.refusal_kind == "coverage"
    assert outcome.reason == expected
    assert outcome.written == 0 and outcome.trials == () and outcome.comparisons == ()
    assert not wired.ledger("agent_trials.jsonl").exists()
    assert not wired.ledger("agent_comparisons.jsonl").exists()


def test_the_same_window_runs_when_the_column_is_dense(wired: Wired) -> None:
    """The control: the refusal above is about the column, not about the toy window."""
    wired.persist_with_sparse("vvix", until=dt.date(2024, 6, 14))
    assert wired.stored is not None
    outcome = candidate.evaluate_proposal(
        _proposal(extra=("semivar_neg_proxy_d5",)),
        settings=wired.settings,
        at=AT,
        sha=SHA,
        dirty=False,
    )
    assert not outcome.refused and outcome.reason is None
    assert outcome.written == 4 * len(LOSSES)


# ---------------------------------------------------------------------------
# The command line: exit codes, the result file, the pid.
# ---------------------------------------------------------------------------


def test_exit_codes_are_distinct_and_leave_one_and_two_alone() -> None:
    codes = {
        cli.EXIT_OK,
        cli.EXIT_FAILED,
        cli.EXIT_USAGE,
        cli.EXIT_COVERAGE_REFUSED,
        cli.EXIT_PROPOSAL_REFUSED,
        cli.EXIT_AUDIT_REFUSED,
    }
    assert len(codes) == 6
    assert (cli.EXIT_OK, cli.EXIT_FAILED, cli.EXIT_USAGE) == (0, 1, 2)


def test_a_valid_proposal_exits_zero_prints_its_pid_and_writes_the_result_file(
    wired: Wired, capsys: pytest.CaptureFixture[str]
) -> None:
    import os

    proposal_file = _file_for(wired.root / "proposal.json", _proposal())
    result_file = wired.root / "result.json"
    assert cli.main(["--proposal", str(proposal_file), "--result", str(result_file)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"candidate pid: {os.getpid()}\n" in out

    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert result["pid"] == os.getpid()
    assert result["cohort"] == agent_cohort_name(SPRINT, 1)
    assert result["trials_written"] == 4 * len(LOSSES)
    assert len(result["comparisons"]) == len(LOSSES)
    assert result["reason"] is None
    assert result["git_sha"] and result["at"]
    # Every comparison in the result is filed under the cohort the run reports.
    assert [c["cohort"] for c in result["comparisons"]] == [result["cohort"]] * len(LOSSES)


def test_a_coverage_refusal_exits_with_its_own_code_and_says_why_in_the_result_file(
    wired: Wired, capsys: pytest.CaptureFixture[str]
) -> None:
    wired.persist_with_sparse("vvix", until=dt.date(2024, 6, 14))
    proposal_file = _file_for(wired.root / "proposal.json", _proposal(extra=("vvix",)))
    result_file = wired.root / "result.json"
    code = cli.main(["--proposal", str(proposal_file), "--result", str(result_file)])
    capsys.readouterr()
    assert code == cli.EXIT_COVERAGE_REFUSED
    assert code not in (cli.EXIT_OK, cli.EXIT_FAILED, cli.EXIT_USAGE)
    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert result["status"] == "refused"
    assert result["refusal_kind"] == "coverage"
    assert "vvix" in result["reason"] and "first requested origin" in result["reason"]
    assert result["trials_written"] == 0
    assert not wired.ledger("agent_trials.jsonl").exists()


def _tampered_payloads() -> dict[str, object]:
    good = asdict(_proposal())
    return {
        "out-of-vocabulary regressor": {**good, "extra_regressors": ["target_rv_proxy_h5"]},
        "unknown key (a date)": {**good, "end": "2026-06-30"},
        "unknown key (a stride)": {**good, "every": 1},
        "no proposal id": {k: v for k, v in good.items() if k != "proposal_id"},
        "id that is not a sprint and an index": {**good, "proposal_id": "no-index-here"},
        "not a mapping": ["har_levels"],
    }


@pytest.mark.parametrize("label", list(_tampered_payloads()))
def test_a_proposal_file_the_gate_never_saw_is_refused_by_the_child(
    label: str, wired: Wired, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate's verdict does not travel with the file, so the child validates what it reads."""
    proposal_file = _write(wired.root / "proposal.json", _tampered_payloads()[label])
    result_file = wired.root / "result.json"
    code = cli.main(["--proposal", str(proposal_file), "--result", str(result_file)])
    capsys.readouterr()
    assert code == cli.EXIT_PROPOSAL_REFUSED
    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert result["status"] == "refused" and result["refusal_kind"] == "proposal"
    assert result["reason"]
    assert not wired.ledger("agent_trials.jsonl").exists()


def test_an_out_of_vocabulary_regressor_is_refused_with_the_gates_own_code(
    wired: Wired, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {**asdict(_proposal()), "extra_regressors": ["target_rv_proxy_h5"]}
    result_file = wired.root / "result.json"
    cli.main(
        ["--proposal", str(_write(wired.root / "p.json", payload)), "--result", str(result_file)]
    )
    capsys.readouterr()
    assert "out_of_vocabulary" in json.loads(result_file.read_text(encoding="utf-8"))["reason"]


@pytest.mark.parametrize("content", ["{not json", ""])
def test_a_proposal_file_that_is_not_json_is_refused_not_a_traceback(
    content: str, wired: Wired, capsys: pytest.CaptureFixture[str]
) -> None:
    path = wired.root / "proposal.json"
    path.write_text(content, encoding="utf-8")
    code = cli.main(["--proposal", str(path), "--result", str(wired.root / "r.json")])
    capsys.readouterr()
    assert code == cli.EXIT_PROPOSAL_REFUSED


def test_a_missing_proposal_file_is_refused_not_a_traceback(
    wired: Wired, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        ["--proposal", str(wired.root / "absent.json"), "--result", str(wired.root / "r.json")]
    )
    capsys.readouterr()
    assert code == cli.EXIT_PROPOSAL_REFUSED


def _run_child(proposal: Path, result: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "volab.candidate",
            "--proposal",
            str(proposal),
            "--result",
            str(result),
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )


def test_each_child_is_a_fresh_interpreter_with_its_own_pid(tmp_path: Path) -> None:
    """The process-isolation witness (P5-E1 relies on it): two launches, two different pids.

    Both launches are handed a file the child refuses before it opens any path, so this needs
    no storage root and writes nothing but ``result``. The pid is printed on a labelled line
    *before* the file is read, so a refusal still proves a new process ran.
    """
    bad = _write(tmp_path / "bad.json", {**asdict(_proposal()), "extra_regressors": ["nope"]})
    pids = []
    for number in (1, 2):
        result = tmp_path / f"result-{number}.json"
        done = _run_child(bad, result)
        assert done.returncode == cli.EXIT_PROPOSAL_REFUSED, done.stdout + done.stderr
        match = re.search(r"^candidate pid: (\d+)$", done.stdout, flags=re.MULTILINE)
        assert match, done.stdout
        pids.append(int(match.group(1)))
        assert json.loads(result.read_text(encoding="utf-8"))["pid"] == pids[-1]
    assert pids[0] != pids[1]
