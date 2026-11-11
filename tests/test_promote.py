"""The human gate, ``python -m volab.promote`` (P5-E1, R188, R201, R210, R192).

What these tests are for. Promotion is a decision recorded by a separate process, and what it must
guarantee is narrow, so each guarantee has its own witness: it writes one ``promoted`` event keyed
on the proposal and nothing else; it recomputes the sprint's fingerprint *with the sprint's own
functions* and refuses when the ledger moved; it refuses an unknown id and a second promotion; and
the agent package cannot reach this one. Those are import-graph and file facts. Whether the person
typing the command is a person is not something any test here can show.

Every test writes under ``tmp_path``; none touches the repository's ``data/``.
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from test_import_discipline import SRC, _ancestors, _first_party_imports, _graph, _packages

import volab.agent as agent
import volab.agent.sprintlog as sprintlog
import volab.promote as promote_package
from volab.agent.sprintlog import (
    fingerprint_of,
    pending_approvals,
    record_event,
    record_start,
    sprint_log_path,
)
from volab.config import load_settings
from volab.promote import main

_DEV_END = load_settings().splits.dev_end
_INSIDE = _DEV_END.isoformat()
_PAST = (_DEV_END + dt.timedelta(days=1)).isoformat()
_NOW = dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.UTC)
_PROPOSAL: dict[str, Any] = {"name": "x", "reference": ["a", "b"]}


def _trial(cohort: str, n: int, origin: str = _INSIDE) -> dict[str, Any]:
    return {"cohort": cohort, "last_origin": origin, "n": n, "score": 0.5 + n}


def _write_ledger(root: Path, lines: list[dict[str, Any]]) -> None:
    path = root / "data" / "agent_trials.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def _awaiting(
    root: Path,
    *,
    sprint_id: str = "s1",
    proposal_id: str = "p1",
    cohort: str = "agent-x",
    ledger: list[dict[str, Any]],
) -> None:
    """Write the approval the way the sprint does: evidence_hash over the cohort's kept lines."""
    log = sprint_log_path(root)
    if not log.exists():
        record_start(log, sprint_id=sprint_id, at=_NOW, sha="abc", dirty=False)
    evidence = [agent.evidence_hash(x) for x in ledger if x["cohort"] == cohort]
    record_event(
        log,
        sprint_id=sprint_id,
        kind="awaiting_approval",
        proposal_id=proposal_id,
        cohort=cohort,
        proposal=_PROPOSAL,
        created_at=_NOW.isoformat(),
        git_sha="abc",
        git_dirty=False,
        evidence=evidence,
        fingerprint=fingerprint_of(_PROPOSAL, evidence),
    )


def _run(root: Path, proposal_id: str) -> int:
    return main(["--proposal-id", proposal_id, "--root", str(root)], now=_NOW)


def _log_lines(root: Path) -> list[dict[str, Any]]:
    text = sprint_log_path(root).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_promotion_writes_one_promoted_event_carrying_the_proposal_id_and_nothing_else(
    tmp_path: Path,
) -> None:
    ledger = [_trial("agent-x", 1), _trial("agent-x", 2)]
    _write_ledger(tmp_path, ledger)
    _awaiting(tmp_path, ledger=ledger)
    before = _files(tmp_path)
    before_lines = _log_lines(tmp_path)

    assert _run(tmp_path, "p1") == 0

    after = _files(tmp_path)
    assert set(after) == set(before), "promotion created or removed a file"
    changed = {name for name in after if after[name] != before[name]}
    assert changed == {"data/agent_sprints.jsonl"}
    new = _log_lines(tmp_path)[len(before_lines) :]
    assert len(new) == 1
    assert new[0]["kind"] == "promoted"
    assert new[0]["proposal_id"] == "p1"
    assert new[0]["sprint_id"] == "s1"


def test_promoting_one_candidate_leaves_another_from_the_same_sprint_pending(
    tmp_path: Path,
) -> None:
    """R201: a write keyed on the sprint would mark both promoted, the quiet approval."""
    ledger = [_trial("agent-x", 1), _trial("agent-y", 2)]
    _write_ledger(tmp_path, ledger)
    _awaiting(tmp_path, proposal_id="p1", cohort="agent-x", ledger=ledger)
    _awaiting(tmp_path, proposal_id="p2", cohort="agent-y", ledger=ledger)
    assert {a.proposal_id for a in pending_approvals(sprint_log_path(tmp_path))} == {"p1", "p2"}

    assert _run(tmp_path, "p1") == 0

    still = {a.proposal_id for a in pending_approvals(sprint_log_path(tmp_path))}
    assert still == {"p2"}


def test_an_id_with_no_awaiting_approval_event_is_refused_and_nothing_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = [_trial("agent-x", 1)]
    _write_ledger(tmp_path, ledger)
    _awaiting(tmp_path, ledger=ledger)
    before = _files(tmp_path)

    assert _run(tmp_path, "no-such-id") == 1

    assert "no awaiting_approval event" in capsys.readouterr().err
    assert _files(tmp_path) == before


def test_a_moved_ledger_is_refused_with_both_fingerprints_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = [_trial("agent-x", 1)]
    _write_ledger(tmp_path, ledger)
    _awaiting(tmp_path, ledger=ledger)
    stored = pending_approvals(sprint_log_path(tmp_path))[0].fingerprint
    moved = [*ledger, _trial("agent-x", 2)]
    _write_ledger(tmp_path, moved)
    recomputed = fingerprint_of(_PROPOSAL, [agent.evidence_hash(x) for x in moved])
    before = _files(tmp_path)

    assert _run(tmp_path, "p1") == 1

    err = capsys.readouterr().err
    assert stored in err
    assert recomputed in err
    assert stored != recomputed
    assert _files(tmp_path) == before


def test_a_proposal_already_promoted_is_refused_and_only_one_event_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = [_trial("agent-x", 1)]
    _write_ledger(tmp_path, ledger)
    _awaiting(tmp_path, ledger=ledger)
    assert _run(tmp_path, "p1") == 0
    capsys.readouterr()

    assert _run(tmp_path, "p1") == 1

    assert "already promoted" in capsys.readouterr().err
    assert [e["kind"] for e in _log_lines(tmp_path)].count("promoted") == 1


def test_duplicate_ledger_lines_of_the_cohort_are_part_of_the_evidence(tmp_path: Path) -> None:
    """R210: a crash-window resubmission doubles the lines; the sprint fingerprinted them all."""
    line = _trial("agent-x", 1)
    ledger = [line, line]
    _write_ledger(tmp_path, ledger)
    _awaiting(tmp_path, ledger=ledger)
    assert len(pending_approvals(sprint_log_path(tmp_path))[0].evidence) == 2

    assert _run(tmp_path, "p1") == 0


def test_an_approval_built_without_the_duplicate_is_refused(tmp_path: Path) -> None:
    """The other half of R210: a de-duplicating recompute would be a different fingerprint."""
    line = _trial("agent-x", 1)
    _write_ledger(tmp_path, [line, line])
    _awaiting(tmp_path, ledger=[line])

    assert _run(tmp_path, "p1") == 1


def test_lines_of_other_cohorts_are_not_in_the_evidence(tmp_path: Path) -> None:
    kept = _trial("agent-x", 1)
    ledger = [kept, _trial("agent-y", 2)]
    _write_ledger(tmp_path, ledger)
    _awaiting(tmp_path, ledger=[kept])

    assert _run(tmp_path, "p1") == 0


@pytest.mark.parametrize("cohort", ["agent-x", "agent-y"])
def test_a_line_past_dev_end_is_an_integrity_anomaly_and_nothing_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], cohort: str
) -> None:
    """R208: ``research`` fails a sprint on such a line; silently dropping it here would let a
    ledger with an impossible line be approved, whichever cohort the line belongs to."""
    kept = _trial("agent-x", 1)
    _write_ledger(tmp_path, [kept, _trial(cohort, 3, origin=_PAST)])
    _awaiting(tmp_path, ledger=[kept])
    before = _files(tmp_path)

    assert _run(tmp_path, "p1") == 1

    err = capsys.readouterr().err
    assert "integrity" in err and "dev_end" in err
    assert _files(tmp_path) == before, "a refused promotion wrote something"
    assert "promoted" not in [e["kind"] for e in _log_lines(tmp_path)]


def test_the_fingerprint_is_recomputed_by_the_sprints_own_functions() -> None:
    """Identity, not equality: a second implementation could agree today and drift tomorrow."""
    assert promote_package.evidence_hash is agent.evidence_hash
    assert promote_package.fingerprint_of is sprintlog.fingerprint_of
    line = {"b": 1, "a": [1, 2]}
    text = json.dumps(line, sort_keys=True, separators=(",", ":"))
    assert agent.evidence_hash(line) == hashlib.sha256(text.encode()).hexdigest()


def test_two_awaiting_events_for_one_id_are_refused_rather_than_guessed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = [_trial("agent-x", 1)]
    _write_ledger(tmp_path, ledger)
    _awaiting(tmp_path, ledger=ledger)
    _awaiting(tmp_path, sprint_id="s2", ledger=ledger)

    assert _run(tmp_path, "p1") == 1

    assert "2 awaiting_approval events" in capsys.readouterr().err
    assert "promoted" not in [e["kind"] for e in _log_lines(tmp_path)]


def test_an_unreadable_agent_ledger_is_reported_and_exits_non_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = [_trial("agent-x", 1)]
    _write_ledger(tmp_path, ledger)
    _awaiting(tmp_path, ledger=ledger)
    with (tmp_path / "data" / "agent_trials.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("not json\n")

    assert _run(tmp_path, "p1") == 1

    assert "cannot be read" in capsys.readouterr().err


def test_the_operator_must_type_the_id_and_no_flag_selects_a_candidate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for argv in ([], ["--latest"], ["--all"], ["p1"]):
        with pytest.raises(SystemExit) as exit_info:
            main(argv)
        assert exit_info.value.code == 2
    capsys.readouterr()


# ---------------------------------------------------------------------------
# The import fence, in the direction the store-reach fence does not cover.
# ---------------------------------------------------------------------------

_TARGET = "volab.promote"


def _reach(start: str, graph: dict[str, str], packages: dict[str, str]) -> list[str]:
    """The import chain from ``start`` to ``volab.promote``, or ``[]``; breadth-first.

    Each module's parent packages are edges, because importing ``volab.agent.x`` executes
    ``volab.agent.__init__``. A name not in ``graph`` is a symbol imported from a module.
    """
    queue: list[list[str]] = [[start]]
    seen = {start}
    while queue:
        chain = queue.pop(0)
        node = chain[-1]
        if node != start and (node == _TARGET or node.startswith(_TARGET + ".")):
            return chain
        source = graph.get(node)
        if source is None:
            continue
        edges = _first_party_imports(source, packages.get(node, "volab"))
        edges |= _ancestors(node) - {node}
        for target in sorted(edges):
            if target not in seen:
                seen.add(target)
                queue.append([*chain, target])
    return []


def _agent_modules() -> list[str]:
    return sorted(m for m in _graph() if m == "volab.agent" or m.startswith("volab.agent."))


def test_the_agent_package_cannot_reach_the_promote_package() -> None:
    modules = _agent_modules()
    assert len(modules) > 3, "the scan would pass by walking nothing"
    graph, packages = _graph(), _packages()
    for module in modules:
        assert _reach(module, graph, packages) == [], module


def test_a_planted_reverse_import_is_caught_with_its_chain() -> None:
    graph, packages = _graph(), _packages()
    graph["volab.agent.planted"] = "from volab.promote import main\n"
    packages["volab.agent.planted"] = "volab.agent"
    assert _reach("volab.agent.planted", graph, packages)[-1].startswith(_TARGET)

    graph["volab.agent.planted"] = "import volab.agent.hop\n"
    graph["volab.agent.hop"] = "from volab.promote.__main__ import main\n"
    packages["volab.agent.hop"] = "volab.agent"
    chain = _reach("volab.agent.planted", graph, packages)
    assert chain[0] == "volab.agent.planted"
    assert "volab.agent.hop" in chain


def test_the_promote_package_imports_only_what_it_should() -> None:
    """It may import volab.agent and volab.config; it must not import the evaluation child."""
    for path in sorted((SRC / "promote").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            n.module
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("volab")
        }
        assert not any(m.startswith("volab.candidate") for m in names), path
        assert "volab.features.targets" not in names, path
