"""The checkpointer port (``volab.agent.memory``) and its two implementations.

One conformance suite, run over both ``FileCheckpointer`` and ``SqliteCheckpointer``: the suite is
the witness that the port is a port. Everything writes under ``tmp_path`` or an in-memory
database, never the repository's ``data/``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from volab.agent.memory import Checkpointer, FileCheckpointer, SprintState, SqliteCheckpointer

Factory = Callable[[Path], Checkpointer]


def _file(tmp_path: Path) -> Checkpointer:
    return FileCheckpointer(tmp_path / "checkpoints")


def _sqlite(tmp_path: Path) -> Checkpointer:
    return SqliteCheckpointer(sqlite3.connect(":memory:"))


FACTORIES = [pytest.param(_file, id="file"), pytest.param(_sqlite, id="sqlite")]

NESTED: SprintState = {
    "plan": {"horizons": [1, 5, 22], "notes": None, "inner": {"deep": [{"a": 0.1}, None, True]}},
    "score": 0.1 + 0.2,  # a float that is not exactly representable in a short decimal
    "tiny": 1e-300,
    "empty": [],
    "text": 'snowman ☃ and a "quote"',
}


@pytest.fixture(params=FACTORIES)
def checkpointer(request: pytest.FixtureRequest, tmp_path: Path) -> Checkpointer:
    factory: Factory = request.param
    return factory(tmp_path)


def test_a_sprint_with_no_state_returns_none_and_an_empty_history(
    checkpointer: Checkpointer,
) -> None:
    assert checkpointer.get("never-seen") is None
    assert checkpointer.history("never-seen") == ()
    assert checkpointer.sprints() == ()


def test_put_then_get_round_trips_the_step_and_its_state(checkpointer: Checkpointer) -> None:
    checkpointer.put("s1", "propose", {"n": 1})
    assert checkpointer.get("s1") == ("propose", {"n": 1})


def test_get_returns_the_last_step_put_and_history_lists_every_step_in_order(
    checkpointer: Checkpointer,
) -> None:
    checkpointer.put("s1", "a", {"i": 1})
    checkpointer.put("s1", "b", {"i": 2})
    checkpointer.put("s1", "c", {"i": 3})
    assert checkpointer.get("s1") == ("c", {"i": 3})
    assert checkpointer.history("s1") == (("a", {"i": 1}), ("b", {"i": 2}), ("c", {"i": 3}))


def test_putting_one_step_twice_makes_the_later_state_current_and_history_keeps_both(
    checkpointer: Checkpointer,
) -> None:
    checkpointer.put("s1", "a", {"try": 1})
    checkpointer.put("s1", "a", {"try": 2})
    assert checkpointer.get("s1") == ("a", {"try": 2})
    assert checkpointer.history("s1") == (("a", {"try": 1}), ("a", {"try": 2}))


def test_sprints_are_ordered_most_recently_written_first(checkpointer: Checkpointer) -> None:
    checkpointer.put("old", "a", {})
    checkpointer.put("mid", "a", {})
    checkpointer.put("new", "a", {})
    assert checkpointer.sprints() == ("new", "mid", "old")


def test_writing_to_an_old_sprint_moves_it_to_the_front(checkpointer: Checkpointer) -> None:
    checkpointer.put("old", "a", {})
    checkpointer.put("new", "a", {})
    checkpointer.put("old", "b", {})
    assert checkpointer.sprints() == ("old", "new")


def test_sprints_do_not_see_one_anothers_state(checkpointer: Checkpointer) -> None:
    checkpointer.put("s1", "a", {"who": 1})
    checkpointer.put("s2", "a", {"who": 2})
    assert checkpointer.get("s1") == ("a", {"who": 1})
    assert checkpointer.history("s2") == (("a", {"who": 2}),)


def test_nested_dicts_lists_none_bools_and_floats_survive_the_round_trip_unchanged(
    checkpointer: Checkpointer,
) -> None:
    checkpointer.put("s1", "a", NESTED)
    got = checkpointer.get("s1")
    assert got is not None
    assert got[1] == NESTED
    assert got[1]["score"] == 0.1 + 0.2
    assert got[1]["plan"]["notes"] is None
    assert got[1]["plan"]["inner"]["deep"][2] is True


def test_a_sprint_id_with_path_and_sql_metacharacters_is_stored_and_listed_verbatim(
    checkpointer: Checkpointer,
) -> None:
    weird = "../x/y'; DROP TABLE checkpoints; --"
    checkpointer.put(weird, "a", {"ok": True})
    assert checkpointer.get(weird) == ("a", {"ok": True})
    assert checkpointer.sprints() == (weird,)


def test_mutating_a_returned_state_does_not_change_what_is_stored(
    checkpointer: Checkpointer,
) -> None:
    checkpointer.put("s1", "a", {"list": [1]})
    got = checkpointer.get("s1")
    assert got is not None
    got[1]["list"].append(2)
    assert checkpointer.get("s1") == ("a", {"list": [1]})


def test_mutating_a_state_after_putting_it_does_not_change_what_is_stored(
    checkpointer: Checkpointer,
) -> None:
    state: SprintState = {"list": [1]}
    checkpointer.put("s1", "a", state)
    state["list"].append(2)
    assert checkpointer.get("s1") == ("a", {"list": [1]})


def test_a_state_that_is_not_json_is_refused_and_leaves_nothing_written(
    checkpointer: Checkpointer,
) -> None:
    with pytest.raises(ValueError, match="JSON"):
        checkpointer.put("s1", "a", {"nan": float("nan")})
    with pytest.raises(ValueError, match="JSON"):
        checkpointer.put("s1", "a", {"obj": object()})
    assert checkpointer.get("s1") is None
    assert checkpointer.sprints() == ()


def test_the_file_checkpointer_survives_being_reopened_on_the_same_root(tmp_path: Path) -> None:
    FileCheckpointer(tmp_path).put("s1", "a", {"k": 1})
    reopened = FileCheckpointer(tmp_path)
    assert reopened.get("s1") == ("a", {"k": 1})
    reopened.put("s2", "a", {})
    assert reopened.sprints() == ("s2", "s1")


def test_the_sqlite_checkpointer_survives_being_reopened_on_the_same_database(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cp.db"
    first = sqlite3.connect(path)
    SqliteCheckpointer(first).put("s1", "a", {"k": 1})
    first.close()
    second = sqlite3.connect(path)
    reopened = SqliteCheckpointer(second)
    assert reopened.get("s1") == ("a", {"k": 1})
    reopened.put("s2", "a", {})
    assert reopened.sprints() == ("s2", "s1")
    second.close()


def test_the_file_checkpointer_writes_only_under_its_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    FileCheckpointer(root).put("../escape", "a", {})
    assert not (tmp_path / "escape").exists()
    assert all(path.is_relative_to(root) for path in root.rglob("*"))
    assert [p.name for p in tmp_path.iterdir()] == ["root"]


def test_a_truncated_final_line_in_a_file_store_is_refused_on_read_not_skipped(
    tmp_path: Path,
) -> None:
    store = FileCheckpointer(tmp_path)
    store.put("s1", "a", {"k": 1})
    path = next(tmp_path.glob("*.jsonl"))
    path.write_text(path.read_text("utf-8") + '{"seq": 2, "sprint": "s1", "ste', "utf-8")
    with pytest.raises(ValueError):
        store.get("s1")
