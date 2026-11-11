"""The combined ledger count and the read-only agent ledger reader (P5-C3, R183, R186, R192).

``volab.counts`` must not import ``volab.candidate`` (it would put ``arch`` into the leaderboard's
import graph, which ``volab.leaderboard`` was engineered to avoid) and ``volab.agent.ledger`` must
not either (``volab.candidate`` reads the panel, so importing it from inside the agent package would
cross the R192 store fence). Both therefore repeat the ledgers' paths as literals, and the
two-sided tests here are what stop a repeated literal drifting from the constant it copies.
Every ledger these tests touch is under ``tmp_path``; the repository's ``data/`` is never written.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
from pathlib import Path

import pytest
from test_agent_discipline import _store_chain
from test_import_discipline import _graph, _packages

import volab.candidate as candidate
import volab.counts as counts
from volab.agent import ledger
from volab.walkforward import TRIAL_LEDGER, count_trials

REPO = Path(__file__).resolve().parents[1]
DEV_END = dt.date(2025, 12, 31)


def _write(root: Path, relative: Path, rows: list[dict]) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _row(last_origin: str | None = "2025-12-24", **extra) -> dict:
    row: dict = {"model": "m", "loss": "qlike", "value": 1.0, **extra}
    if last_origin is not None:
        row["last_origin"] = last_origin
    return row


# ---------------------------------------------------------------------------
# count_all
# ---------------------------------------------------------------------------


def test_the_counted_agent_paths_are_the_candidate_modules_constants() -> None:
    assert counts.AGENT_TRIAL_LEDGER == candidate.AGENT_TRIAL_LEDGER
    assert counts.AGENT_COMPARISON_LEDGER == candidate.AGENT_COMPARISON_LEDGER


def test_the_reader_path_is_the_candidate_modules_and_the_trial_ledgers() -> None:
    assert ledger.AGENT_TRIAL_LEDGER == candidate.AGENT_TRIAL_LEDGER
    assert ledger.TRIAL_LEDGER == TRIAL_LEDGER


def test_counts_is_store_free_by_import_graph() -> None:
    """R212. ``volab.counts`` must not reach ``volab.store``, so the agent CLI can import it.

    Walks the import graph with the R192 fence's own walker, so the fence and this proof cannot
    disagree about what "reaches the store" means.
    """
    chain = _store_chain("volab.counts", _graph(), _packages())
    assert chain == [], f"volab.counts reaches the store: {' -> '.join(chain)}"


def test_the_store_free_walk_is_not_vacuous() -> None:
    """The walker finds the chain from a module known to reach the store."""
    assert _store_chain("volab.walkforward", _graph(), _packages())


def test_the_counted_trial_path_is_the_writers_constant() -> None:
    assert counts.TRIAL_LEDGER == TRIAL_LEDGER


@pytest.mark.parametrize(
    "content",
    [None, "", "\n\n", '{"a": 1}\n\n{"a": 2}\n', '{"a": 1}\n   \n\t\n{"a": 2}', '{"a": 1}'],
    ids=["absent", "empty", "blank-only", "blank-interleaved", "no-final-newline", "one-line"],
)
def test_count_all_trials_agrees_with_count_trials_on_the_same_file(tmp_path, content) -> None:
    """The copy of the line count and the writers' own function, on the cases that drift."""
    if content is not None:
        (tmp_path / TRIAL_LEDGER).parent.mkdir(parents=True)
        (tmp_path / TRIAL_LEDGER).write_text(content, encoding="utf-8")
    assert counts.count_all(tmp_path).trials == count_trials(tmp_path)
    assert (tmp_path / TRIAL_LEDGER).exists() is (content is not None)


def test_counts_absent_ledgers_are_zeros_and_nothing_is_created(tmp_path) -> None:
    result = counts.count_all(tmp_path)
    assert result == counts.LedgerCounts(trials=0, agent_trials=0, agent_comparisons=0, total=0)
    assert list(tmp_path.rglob("*")) == []


def test_each_count_is_its_own_ledgers_non_blank_lines(tmp_path) -> None:
    _write(tmp_path, TRIAL_LEDGER, [_row()] * 3)
    _write(tmp_path, candidate.AGENT_TRIAL_LEDGER, [_row()] * 5)
    path = tmp_path / candidate.AGENT_COMPARISON_LEDGER
    path.write_text('{"a": 1}\n\n{"a": 2}\n   \n', encoding="utf-8")
    result = counts.count_all(tmp_path)
    assert (result.trials, result.agent_trials, result.agent_comparisons) == (3, 5, 2)


def test_total_is_trials_plus_agent_trials_and_not_agent_comparisons(tmp_path) -> None:
    _write(tmp_path, TRIAL_LEDGER, [_row()] * 3)
    _write(tmp_path, candidate.AGENT_TRIAL_LEDGER, [_row()] * 5)
    _write(tmp_path, candidate.AGENT_COMPARISON_LEDGER, [{"a": 1}] * 7)
    result = counts.count_all(tmp_path)
    assert result.total == 8
    assert result.total != 3 + 5 + 7


def test_count_all_agrees_with_the_writers_own_counters(tmp_path) -> None:
    _write(tmp_path, TRIAL_LEDGER, [_row()] * 4)
    _write(tmp_path, candidate.AGENT_TRIAL_LEDGER, [_row()] * 2)
    result = counts.count_all(tmp_path)
    assert result.trials == count_trials(tmp_path)
    assert result.agent_trials == candidate.count_agent_trials(tmp_path)


def test_counts_imports_neither_candidate_nor_compare() -> None:
    tree = ast.parse((REPO / "src" / "volab" / "counts.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    for name in names:
        assert not name.startswith(("volab.candidate", "volab.compare")), name


# ---------------------------------------------------------------------------
# read_trials / read_agent_trials
# ---------------------------------------------------------------------------


def test_a_line_past_dev_end_is_excluded_and_counted_not_silently_dropped(tmp_path) -> None:
    _write(
        tmp_path,
        TRIAL_LEDGER,
        [_row("2025-12-24"), _row("2025-12-31"), _row("2026-01-02", model="late")],
    )
    view = ledger.read_trials(tmp_path, dev_end=DEV_END)
    assert [line["last_origin"] for line in view.lines] == ["2025-12-24", "2025-12-31"]
    assert all(line["model"] != "late" for line in view.lines)
    assert view.excluded == 1
    assert view.source == TRIAL_LEDGER


def test_a_line_with_no_last_origin_is_excluded_and_counted(tmp_path) -> None:
    _write(tmp_path, TRIAL_LEDGER, [_row(None), _row("2025-12-24")])
    view = ledger.read_trials(tmp_path, dev_end=DEV_END)
    assert len(view.lines) == 1
    assert view.excluded == 1


def test_a_last_origin_that_is_not_a_date_raises_rather_than_being_guessed(tmp_path) -> None:
    _write(tmp_path, TRIAL_LEDGER, [_row("not-a-date")])
    with pytest.raises(ledger.LedgerReadError, match="line 1"):
        ledger.read_trials(tmp_path, dev_end=DEV_END)


def test_a_malformed_line_raises_rather_than_shrinking_the_view(tmp_path) -> None:
    path = tmp_path / TRIAL_LEDGER
    path.parent.mkdir(parents=True)
    path.write_text('{"last_origin": "2025-01-01"}\nnot json\n', encoding="utf-8")
    with pytest.raises(ledger.LedgerReadError, match="line 2"):
        ledger.read_trials(tmp_path, dev_end=DEV_END)


def test_an_absent_ledger_is_an_empty_view_and_nothing_is_created(tmp_path) -> None:
    view = ledger.read_agent_trials(tmp_path, dev_end=DEV_END)
    assert (view.lines, view.excluded, view.source) == ((), 0, candidate.AGENT_TRIAL_LEDGER)
    assert list(tmp_path.rglob("*")) == []


def test_the_agent_reader_reads_the_agent_ledger_and_excludes_the_same_way(tmp_path) -> None:
    _write(tmp_path, TRIAL_LEDGER, [_row("2025-01-01")] * 4)
    _write(tmp_path, candidate.AGENT_TRIAL_LEDGER, [_row("2025-01-01"), _row("2026-06-01")])
    view = ledger.read_agent_trials(tmp_path, dev_end=DEV_END)
    assert len(view.lines) == 1
    assert view.excluded == 1


def test_the_reader_does_not_modify_the_ledger(tmp_path) -> None:
    _write(tmp_path, TRIAL_LEDGER, [_row("2026-06-01")])
    before = (tmp_path / TRIAL_LEDGER).read_bytes()
    ledger.read_trials(tmp_path, dev_end=DEV_END)
    assert (tmp_path / TRIAL_LEDGER).read_bytes() == before


# --- the import ban (R186): ``count_trials`` is reachable only from its definition ---

_COUNT_TRIALS_HOMES = frozenset({Path("walkforward/__init__.py")})


def _count_trials_users(sources: dict[Path, str]) -> list[str]:
    """Every ``path:line`` that imports or names ``count_trials`` in code.

    Walked by ``ast``, so a docstring or comment that merely mentions the function is not a use
    (``volab.agent.__main__`` keeps such a mention, R212). Modules in ``_COUNT_TRIALS_HOMES`` are
    exempt: the definition. ``count_all`` no longer calls it (R212).
    """
    found: list[str] = []
    for path, source in sorted(sources.items()):
        if path in _COUNT_TRIALS_HOMES:
            continue
        for node in ast.walk(ast.parse(source)):
            used = (
                (
                    isinstance(node, ast.ImportFrom)
                    and any(a.name == "count_trials" for a in node.names)
                )
                or (isinstance(node, ast.Name) and node.id == "count_trials")
                or (isinstance(node, ast.Attribute) and node.attr == "count_trials")
            )
            if used:
                found.append(f"{path.as_posix()}:{node.lineno}")
    return found


def _src_sources(root: Path) -> dict[Path, str]:
    return {p.relative_to(root): p.read_text(encoding="utf-8") for p in root.rglob("*.py")}


def test_no_module_but_the_definition_uses_count_trials() -> None:
    users = _count_trials_users(_src_sources(REPO / "src" / "volab"))
    assert users == [], (
        "these modules reach count_trials directly and could print the first ledger's count "
        f"without the agent ledgers' (R186); use volab.counts.count_all: {users}"
    )


def test_the_ban_catches_a_planted_import_and_a_planted_attribute_use(tmp_path) -> None:
    (tmp_path / "walkforward").mkdir()
    (tmp_path / "walkforward" / "__init__.py").write_text("def count_trials(root): ...\n")
    (tmp_path / "counts.py").write_text("from volab.walkforward import count_trials\n")
    (tmp_path / "mentions.py").write_text('"""Mentions count_trials in prose only."""\n')
    (tmp_path / "planted.py").write_text("from volab.walkforward import count_trials\n")
    (tmp_path / "attr.py").write_text("import volab.walkforward as w\nn = w.count_trials(1)\n")
    # R212: counts.py is no longer exempt, so it is flagged like any other module.
    users = _count_trials_users(_src_sources(tmp_path))
    assert users == ["attr.py:2", "counts.py:1", "planted.py:1"]
