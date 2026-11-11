"""The checkpointer port, and the two implementations that make it a port.

A sprint is a short linear sequence of steps, and it must be resumable: a run that dies after
step three picks up from step three. ``Checkpointer`` is where a step's state is kept.

**A domain-level port, deliberately not a DB-API connection.** ``put`` / ``get`` / ``history`` /
``sprints`` say what the agent needs; they do not say "execute this SQL". A port shaped as a SQL
executor makes every implementation a dialect, and a test against sqlite3 would then be evidence
about sqlite3 and nothing else (R190). Two independent implementations ship from day one --
``FileCheckpointer`` (JSON lines on disk) and ``SqliteCheckpointer`` over an injected
``sqlite3.Connection`` -- and one conformance suite in ``tests/test_agent_memory.py`` is run over
both. Two implementations that share no code path are what make this a port rather than one class
with an interface drawn around it. Both need no credential: ``sqlite3`` is stdlib.

**What is NOT written, and why (R190).** There is no Postgres-backed implementation. sqlite3 uses
``?`` (qmark) parameters, psycopg uses ``%s``, and the DDL differs, so the sqlite3 test is not
evidence about Postgres SQL. Shipping psycopg code that has never met a server and calling it
"working, blocked on a key" would be an overclaim, so it is not shipped and not described that
way. A DSN would let a third implementation be written *and run*; what running it would prove is
that the SQL dialect and DDL are right, which the sqlite3 implementation does not establish. No
``psycopg`` dependency is added (R182).

**State is JSON-shaped.** A ``SprintState`` must survive ``json.dumps(..., allow_nan=False)``:
dicts with string keys, lists, strings, ints, floats, bools and ``None``. Anything else -- a
non-finite float, an arbitrary object -- raises ``ValueError`` from ``put`` and writes nothing.
A tuple comes back as a list and a non-string dict key as a string; both implementations do the
same, because both serialise through the same function. Because state is serialised on ``put``
and parsed afresh on ``get``, a caller mutating a state it passed in, or one it got back, never
changes what is stored.

**Ordering.** Every ``put`` takes the next value of a store-wide counter (the file store counts
lines across its root; the sqlite store uses an ``AUTOINCREMENT`` key), so order is by write and
never by a wall clock that could tie or run backwards. ``sprints`` is ordered by each sprint's
*latest* write, most recent first. Putting the same step twice keeps both rows in ``history`` and
makes the later one what ``get`` returns.

**Not covered.** No locking: one writer per store is assumed, which is how a scheduled sprint
runs. ``FileCheckpointer`` writes each record as one appended line and does not ``fsync``; a
process killed mid-write could leave a truncated final line, which a read refuses loudly rather
than skipping. A sprint id long enough to exceed the filesystem's filename limit is not handled.
The module imports only the standard library, which keeps it inside the R192 import fence.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any, Protocol

SprintState = dict[str, Any]


class Checkpointer(Protocol):
    """Where a sprint's per-step state is kept, so a dead run can resume."""

    def put(self, sprint_id: str, step: str, state: SprintState) -> None:
        """Record ``state`` as the result of ``step``. Raises ``ValueError`` if not JSON."""
        ...

    def get(self, sprint_id: str) -> tuple[str, SprintState] | None:
        """The last step written and its state, or ``None`` for a sprint never written."""
        ...

    def history(self, sprint_id: str) -> tuple[tuple[str, SprintState], ...]:
        """Every ``(step, state)`` written, oldest first; empty for an unknown sprint."""
        ...

    def sprints(self) -> tuple[str, ...]:
        """Every sprint id, ordered by its latest write, most recent first."""
        ...


def _dump(state: SprintState) -> str:
    try:
        return json.dumps(state, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"sprint state is not JSON-serialisable: {exc}") from exc


class FileCheckpointer:
    """One JSON-lines file per sprint under ``root``; no credential, no database.

    The file name is the percent-encoded sprint id, so an id holding ``/`` or ``..`` cannot
    escape ``root``; the real id is also stored in every record, and ``sprints`` reads it from
    there. A record is ``{"seq", "sprint", "step", "state"}`` where ``seq`` is one more than the
    highest ``seq`` anywhere under ``root``. That scan makes ``put`` linear in the store's size,
    which is fine for a weekly sprint of seven steps and is not meant for more.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, sprint_id: str) -> Path:
        return self._root / (urllib.parse.quote(sprint_id, safe="") + ".jsonl")

    def _records(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]

    def _all_records(self) -> list[dict[str, Any]]:
        if not self._root.exists():
            return []
        found: list[dict[str, Any]] = []
        for path in sorted(self._root.glob("*.jsonl")):
            found.extend(self._records(path))
        return found

    def put(self, sprint_id: str, step: str, state: SprintState) -> None:
        encoded = _dump(state)  # refuse before touching the disk
        seq = max((r["seq"] for r in self._all_records()), default=0) + 1
        record = json.dumps(
            {"seq": seq, "sprint": sprint_id, "step": step, "state": json.loads(encoded)},
            allow_nan=False,
        )
        self._root.mkdir(parents=True, exist_ok=True)
        with self._path(sprint_id).open("a", encoding="utf-8") as handle:
            handle.write(record + "\n")

    def history(self, sprint_id: str) -> tuple[tuple[str, SprintState], ...]:
        records = self._records(self._path(sprint_id))
        return tuple((r["step"], r["state"]) for r in sorted(records, key=lambda r: r["seq"]))

    def get(self, sprint_id: str) -> tuple[str, SprintState] | None:
        steps = self.history(sprint_id)
        return steps[-1] if steps else None

    def sprints(self) -> tuple[str, ...]:
        latest: dict[str, int] = {}
        for record in self._all_records():
            latest[record["sprint"]] = max(latest.get(record["sprint"], 0), record["seq"])
        return tuple(sorted(latest, key=lambda sprint: latest[sprint], reverse=True))


class SqliteCheckpointer:
    """The same port over a ``sqlite3.Connection`` the caller owns and closes.

    The connection is injected so a test passes ``":memory:"`` and a run passes a file. The table
    is created if absent; the connection is committed after every ``put``. Parameters are
    qmark-style, which is sqlite3's dialect and *not* psycopg's -- see the module docstring on
    why that is the reason no Postgres implementation is written here.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS checkpoints ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                "sprint_id TEXT NOT NULL, step TEXT NOT NULL, state TEXT NOT NULL)"
            )

    def put(self, sprint_id: str, step: str, state: SprintState) -> None:
        encoded = _dump(state)
        with self._connection:
            self._connection.execute(
                "INSERT INTO checkpoints (sprint_id, step, state) VALUES (?, ?, ?)",
                (sprint_id, step, encoded),
            )

    def history(self, sprint_id: str) -> tuple[tuple[str, SprintState], ...]:
        rows = self._connection.execute(
            "SELECT step, state FROM checkpoints WHERE sprint_id = ? ORDER BY seq", (sprint_id,)
        ).fetchall()
        return tuple((step, json.loads(state)) for step, state in rows)

    def get(self, sprint_id: str) -> tuple[str, SprintState] | None:
        row = self._connection.execute(
            "SELECT step, state FROM checkpoints WHERE sprint_id = ? ORDER BY seq DESC LIMIT 1",
            (sprint_id,),
        ).fetchone()
        return None if row is None else (row[0], json.loads(row[1]))

    def sprints(self) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT sprint_id FROM checkpoints GROUP BY sprint_id ORDER BY MAX(seq) DESC"
        ).fetchall()
        return tuple(row[0] for row in rows)
