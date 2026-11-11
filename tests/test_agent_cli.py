"""``python -m volab.agent`` (P5-E2, R182, R188, R192, R193, R211).

What these tests are for. The CLI is the one place a sprint's edge resources are built, so what it
must guarantee is narrow and each guarantee has a witness: with no key it refuses intelligibly and
leaves nothing behind (the only evidence the repository can hold about the keyed path without a
key); with a fake completer a whole sprint runs through ``main`` and records a log; ``show`` reads
and writes nothing; and the parser has no ``promote`` verb.

**The fixture uses the repository's real shape: ``storage_root = <tmp>/data``.** Under
``storage_root = tmp_path`` a read from the wrong root and a write from the right one coincide,
which is how R211 hid for a whole story. ``test_show_reads_the_ledger_where_the_evaluation_child_
writes_it`` writes where ``volab.candidate`` writes and asks ``show`` to find it.

No test here reaches a live model or launches the real evaluation child. Every test writes under
``tmp_path``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from test_agent_sprint import SHA, StubSubmit, script

import volab.agent as agent
import volab.agent.__main__ as cli
from volab.agent._llm import API_KEY_VARIABLE, FakeCompleter
from volab.agent._runner import ChildRun
from volab.agent.sprintlog import events
from volab.agent.vocabulary import Proposal
from volab.config import Settings, load_settings
from volab.counts import AGENT_TRIAL_LEDGER, count_all

AT = dt.datetime(2026, 11, 10, 6, 0, tzinfo=dt.UTC)
SPRINT_ID = "2026-11-10-cli"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # The repository's real shape: ``storage.root`` is the ``data`` directory, ledgers join to its
    # parent. Never ``storage_root=tmp_path`` -- see the module docstring (R211).
    return dataclasses.replace(load_settings(), storage_root=tmp_path / "data")


@pytest.fixture(autouse=True)
def default_settings(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``main``'s default root land under ``tmp_path`` rather than the repository."""
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


def tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def git(_: Path) -> tuple[str, bool | None]:
    return SHA, False


def run_cli(*argv: str, **seams: Any) -> int:
    seams.setdefault("now", AT)
    seams.setdefault("git_state", git)
    return cli.main(list(argv), **seams)


class RecordingSubmit:
    """``submit``'s signature, delegating to a ``StubSubmit`` and recording its ``root``."""

    def __init__(self, stub: StubSubmit) -> None:
        self.stub = stub
        self.roots: list[Path] = []

    def __call__(self, proposal: Proposal, *, root: Path) -> ChildRun:
        self.roots.append(root)
        return self.stub(proposal)


def test_the_parser_has_exactly_the_sprint_and_show_verbs_and_never_a_promote_verb() -> None:
    parser = cli.build_parser()
    actions = [a for a in parser._actions if a.dest == "verb"]
    assert len(actions) == 1
    assert set(actions[0].choices or ()) == {"sprint", "show"}
    assert set(cli.VERBS) == {"sprint", "show"}


def test_with_no_key_sprint_refuses_naming_the_variable_and_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(API_KEY_VARIABLE, raising=False)
    before = tree(tmp_path)
    status = cli.main(["--root", str(tmp_path), "sprint"], now=AT, git_state=git)
    captured = capsys.readouterr()
    assert status == cli.EXIT_NO_KEY == 3
    assert API_KEY_VARIABLE in captured.err
    assert "Traceback" not in captured.err
    assert tree(tmp_path) == before


def test_a_blank_key_is_refused_like_an_absent_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(API_KEY_VARIABLE, "   ")
    assert cli.main(["--root", str(tmp_path), "sprint"], now=AT, git_state=git) == cli.EXIT_NO_KEY
    assert API_KEY_VARIABLE in capsys.readouterr().err


def test_the_refusal_never_prints_a_key_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VOLAB_LLM_MODEL", "a-model-name-that-must-not-matter")
    monkeypatch.delenv(API_KEY_VARIABLE, raising=False)
    cli.main(["--root", str(tmp_path), "sprint"], now=AT, git_state=git)
    assert "a-model-name-that-must-not-matter" not in capsys.readouterr().err


def test_the_live_completer_is_the_default_factory_and_import_reads_no_key() -> None:
    import inspect

    from volab.agent._llm import completer_from_environment

    defaults = inspect.signature(cli.main).parameters
    assert defaults["completer_factory"].default is completer_from_environment
    assert defaults["submit_fn"].default is cli.submit
    assert defaults["completer_factory"].kind is inspect.Parameter.KEYWORD_ONLY


def test_a_sprint_id_without_an_iso_date_is_refused_before_anything_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = run_cli(
        "--root",
        str(tmp_path),
        "sprint",
        "--sprint-id",
        "weekly-one",
        completer_factory=lambda: FakeCompleter([]),
    )
    assert status == cli.EXIT_USAGE
    assert "ISO date" in capsys.readouterr().err
    assert tree(tmp_path) == []


def test_a_supervisor_stopped_sprint_runs_end_to_end_through_main_with_a_fake_completer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(agent, "list_models", lambda: ())
    completer = FakeCompleter(script())
    status = run_cli(
        "--root",
        str(tmp_path),
        "sprint",
        "--sprint-id",
        SPRINT_ID,
        completer_factory=lambda: completer,
        submit_fn=RecordingSubmit(StubSubmit(tmp_path)),
    )
    assert status == cli.EXIT_OK
    assert "no_candidate" in capsys.readouterr().out
    log = events(tmp_path / "data" / "agent_sprints.jsonl")
    assert [e["kind"] for e in log][0] == "start"
    assert log[0]["git_sha"] == SHA
    assert log[0]["git_dirty"] is False
    assert log[0]["started_at"] == AT.isoformat()
    assert log[-1]["kind"] == "complete"
    assert completer.prompts == ()


def test_a_full_sprint_through_main_submits_aimed_at_the_storage_root_and_checkpoints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    submit = RecordingSubmit(StubSubmit(tmp_path))
    status = run_cli(
        "--root",
        str(tmp_path),
        "sprint",
        "--sprint-id",
        SPRINT_ID,
        completer_factory=lambda: FakeCompleter(script()),
        submit_fn=submit,
    )
    out = capsys.readouterr().out
    assert "sprint 2026-11-10-cli:" in out
    assert status == (cli.EXIT_FAILED if ": failed" in out else cli.EXIT_OK)
    assert submit.roots, "no proposal reached the evaluation seam"
    assert set(submit.roots) == {tmp_path / "data"}
    assert list((tmp_path / "data" / "agent_checkpoints").glob("*.jsonl"))
    assert events(tmp_path / "data" / "agent_sprints.jsonl", SPRINT_ID)[-1]["kind"] == "complete"


def test_the_default_root_is_the_parent_of_the_storage_root_not_the_storage_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "list_models", lambda: ())
    status = run_cli(
        "sprint",
        "--sprint-id",
        SPRINT_ID,
        completer_factory=lambda: FakeCompleter([]),
        submit_fn=RecordingSubmit(StubSubmit(tmp_path)),
    )
    assert status == cli.EXIT_OK
    assert (tmp_path / "data" / "agent_sprints.jsonl").exists()
    assert not (tmp_path / "data" / "data").exists()


def test_a_finished_sprint_id_is_not_run_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(agent, "list_models", lambda: ())
    args = ("--root", str(tmp_path), "sprint", "--sprint-id", SPRINT_ID)
    seams: dict[str, Any] = {"completer_factory": lambda: FakeCompleter([])}
    assert run_cli(*args, **seams) == cli.EXIT_OK
    capsys.readouterr()
    assert run_cli(*args, **seams) == cli.EXIT_ALREADY_COMPLETE
    assert "already" in capsys.readouterr().err


def test_show_prints_a_sprints_events_and_the_ledger_counts_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(agent, "list_models", lambda: ())
    run_cli(
        "--root",
        str(tmp_path),
        "sprint",
        "--sprint-id",
        SPRINT_ID,
        completer_factory=lambda: FakeCompleter([]),
    )
    capsys.readouterr()
    snapshot = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    before = tree(tmp_path)
    assert cli.main(["--root", str(tmp_path), "show"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"sprint {SPRINT_ID}" in out
    assert "'kind': 'complete'" in out
    assert "agent_trials: 0" in out
    assert tree(tmp_path) == before
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == snapshot


def test_show_on_an_empty_root_reports_no_sprints_and_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "empty"
    assert cli.main(["--root", str(root), "show"]) == cli.EXIT_OK
    assert "no sprints" in capsys.readouterr().out
    assert not root.exists()


def test_show_of_an_unknown_sprint_id_exits_one_and_names_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--root", str(tmp_path), "show", "--sprint-id", "2026-01-01-x"]) == 1
    assert "2026-01-01-x" in capsys.readouterr().err


def test_show_reads_the_ledger_where_the_evaluation_child_writes_it(
    tmp_path: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """R211, as a test: write where ``volab.candidate`` writes and ask ``show`` to find it."""
    written = settings.storage_root.parent / AGENT_TRIAL_LEDGER
    written.parent.mkdir(parents=True)
    written.write_text(json.dumps({"cohort": "agent-x", "last_origin": "2025-01-02"}) + "\n")
    assert written == tmp_path / "data" / "agent_trials.jsonl"
    assert cli.main(["show"]) == cli.EXIT_OK
    assert "agent_trials: 1" in capsys.readouterr().out


def test_the_local_ledger_counter_agrees_with_the_repositorys_count_all(tmp_path: Path) -> None:
    """``__main__`` cannot import ``volab.counts`` (it reaches the store); this pins the copy."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "trials.jsonl").write_text('{"a": 1}\n\n{"a": 2}\n')
    (data / "agent_trials.jsonl").write_text('{"b": 1}\n   \n')
    (data / "agent_comparisons.jsonl").write_text('{"c": 1}\n{"c": 2}\n{"c": 3}\n')
    counted = count_all(tmp_path)
    assert cli._count_all(tmp_path) == {
        "trials": counted.trials,
        "agent_trials": counted.agent_trials,
        "agent_comparisons": counted.agent_comparisons,
        "total": counted.total,
    }
    assert cli._count_all(tmp_path / "absent") == dict.fromkeys(cli._LEDGERS, 0) | {"total": 0}
    assert not (tmp_path / "absent").exists()
