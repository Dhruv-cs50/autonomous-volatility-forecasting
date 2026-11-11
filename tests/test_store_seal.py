"""The seal's lifecycle in ``volab.store``: the bound, the reveal record, and the shape of the code.

Witnesses for ``docs/superpowers/specs/2026-11-10-seal-lifecycle-design.md`` (W1-W21). Nothing here
holds a key: no ``@pytest.mark.holdout``, no ``VOLAB_HOLDOUT_UNLOCK``, no ``allow_holdout=True``.
Where a read past a synthetic store's ``dev_end`` is needed, ``_unlock_granted`` is replaced and
the ``end`` stays below the *repository's* ``dev_end``, so the conftest police stay armed and
would fail the test if a read of the real holdout ever returned.

``store._reveal_committed`` is replaced by the suite's policing fixture for every unmarked test,
so the one test that needs the real function (the memo) takes it from ``_REAL_REVEAL_COMMITTED``,
bound at collection time before any fixture has run.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import volab
from volab import store
from volab.config import Region, Settings, Splits, load_settings
from volab.errors import HoldoutViolation
from volab.store import AUDIT_FILENAME, REVEAL_RECORD_GIT_PATH, load_panel

_REAL_REVEAL_COMMITTED = store._reveal_committed
_STORE_SOURCE = Path(store.__file__).read_text(encoding="utf-8")
_STORE_TREE = ast.parse(_STORE_SOURCE)
_SRC = Path(volab.__file__).resolve().parent

DEV_END = dt.date(2025, 12, 31)
SPLITS = Splits(
    dev_end=DEV_END, holdout_start=dt.date(2026, 1, 2), holdout_end=dt.date(2026, 10, 30)
)
DAY = dt.timedelta(days=1)
DEV_DAY = dt.date(2023, 6, 15)
HOLDOUT_DAY = dt.date(2024, 3, 1)
UNLOCK_ENV = "VOLAB_HOLDOUT_UNLOCK"
ID = "0123456789abcdef0123456789abcdef"
OTHER_ID = "fedcba9876543210fedcba9876543210"


def _open(reveal_id: Any = ID, splits: Splits = SPLITS, **over: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema": 1,
        "kind": "reveal_open",
        "reveal_id": reveal_id,
        "dev_end": splits.dev_end.isoformat(),
        "holdout_start": splits.holdout_start.isoformat(),
        "holdout_end": splits.holdout_end.isoformat(),
    }
    record.update(over)
    return record


def _complete(reveal_id: Any = ID, **over: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"schema": 1, "kind": "reveal_complete", "reveal_id": reveal_id}
    record.update(over)
    return record


def _text(*records: Any) -> str:
    return "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in records)


def _synthetic(synthetic_store: Any, holdout_end: dt.date) -> Settings:
    return dataclasses.replace(
        synthetic_store.settings,
        splits=Splits(
            dev_end=dt.date(2023, 12, 31),
            holdout_start=dt.date(2024, 1, 2),
            holdout_end=holdout_end,
        ),
    )


def _grant_both_keys_without_holding_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "_unlock_granted", lambda _: True)
    assert os.environ.get(UNLOCK_ENV) is None


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=seal@example.invalid",
            "-c",
            "user.name=seal",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    # A scratch directory must never be able to find the real repository above it.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    _git(root, "init", "-q")
    (root / "README").write_text("x")
    _git(root, "add", "README")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _commit_record(root: Path, text: str) -> None:
    path = root / REVEAL_RECORD_GIT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(root, "add", "-f", REVEAL_RECORD_GIT_PATH)
    _git(root, "commit", "-q", "-m", "record")


# ---- W1, W2: the bound ----------------------------------------------------------------------

_REGIONS = {
    "dev": dt.date(2025, 6, 1),
    "holdout": dt.date(2026, 6, 1),
    "live": dt.date(2026, 11, 2),
}
# (revealed, unlocked) -> the regions a read may end in. Written out, not derived.
_ADMITS = {
    (False, False): {"dev": True, "holdout": False, "live": False},
    (False, True): {"dev": True, "holdout": True, "live": False},
    (True, False): {"dev": True, "holdout": True, "live": True},
    (True, True): {"dev": True, "holdout": True, "live": True},
}


@pytest.mark.parametrize("state", list(_ADMITS), ids=lambda s: f"revealed={s[0]},unlocked={s[1]}")
@pytest.mark.parametrize("region", list(_REGIONS))
def test_the_read_limit_is_the_whole_table(region: str, state: tuple[bool, bool]) -> None:
    limit = store._read_limit(SPLITS, revealed=state[0], unlocked=state[1])

    assert (_REGIONS[region] <= limit) is _ADMITS[state][region]


def test_the_limit_and_the_region_never_disagree() -> None:
    permitted = {
        (False, False): {Region.DEV},
        (False, True): {Region.DEV, Region.HOLDOUT},
        (True, False): set(Region),
        (True, True): set(Region),
    }
    probes = [dt.date.min, dt.date.max]
    for anchor in (SPLITS.dev_end, SPLITS.holdout_start, SPLITS.holdout_end):
        probes += [anchor - DAY, anchor, anchor + DAY]

    for (revealed, unlocked), regions in permitted.items():
        limit = store._read_limit(SPLITS, revealed=revealed, unlocked=unlocked)
        for day in probes:
            assert (day <= limit) is (SPLITS.region_of(day) in regions), (revealed, unlocked, day)


# ---- W3, W19, W20, W21: through load_panel ---------------------------------------------------


def test_live_is_unreachable_before_the_reveal_even_to_a_caller_holding_both_keys(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _synthetic(synthetic_store, dt.date(2024, 12, 31))
    synthetic_store.write("bars_daily", "SPY", [dt.date(2025, 1, 2)])
    _grant_both_keys_without_holding_one(monkeypatch)

    with pytest.raises(HoldoutViolation) as raised:
        load_panel("bars_daily", end=dt.date(2025, 1, 2), allow_holdout=True, settings=settings)

    assert "live" in str(raised.value)
    assert "allow_holdout" not in str(raised.value)
    assert not (settings.storage_root / AUDIT_FILENAME).exists()


def test_a_caller_cannot_widen_the_unlocked_window_by_passing_a_later_holdout_end(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _synthetic(synthetic_store, dt.date.max)
    synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY, dt.date(2026, 10, 31)])
    _grant_both_keys_without_holding_one(monkeypatch)

    with pytest.raises(HoldoutViolation, match="live"):
        load_panel("bars_daily", end=dt.date(2026, 10, 31), allow_holdout=True, settings=settings)

    # The clamp narrowed nothing it should not have: the holdout is still reachable.
    frame = load_panel("bars_daily", end=HOLDOUT_DAY, allow_holdout=True, settings=settings)
    assert len(frame) == 1


def test_a_revealed_window_is_free_and_unaudited(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _synthetic(synthetic_store, dt.date(2024, 12, 31))
    synthetic_store.write("bars_daily", "SPY", [dt.date(2025, 1, 2)])
    monkeypatch.setattr(store, "_reveal_committed", lambda splits: True)

    def no_key_is_asked(_: bool) -> bool:
        raise AssertionError("a committed reveal must not consult the keys")

    monkeypatch.setattr(store, "_unlock_granted", no_key_is_asked)

    frame = load_panel("bars_daily", end=dt.date(2025, 1, 2), settings=settings)

    assert len(frame) == 1
    assert not (settings.storage_root / AUDIT_FILENAME).exists()


def test_a_record_for_other_splits_does_not_free_a_read_that_passes_them(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the repository's own splits match the committed record; the caller's do not."""
    repository = load_settings().splits
    settings = _synthetic(synthetic_store, dt.date(2024, 12, 31))
    synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY])
    monkeypatch.setattr(store, "_reveal_committed", lambda splits: splits == repository)

    with pytest.raises(HoldoutViolation, match="past dev_end"):
        load_panel("bars_daily", end=HOLDOUT_DAY, settings=settings)


def test_a_dev_read_never_asks_the_predicate_or_the_keys(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``and`` short-circuit: git stays off the path every shootout read takes."""

    def must_not_be_asked(*_: object) -> bool:
        raise AssertionError("a dev read asked about the seal")

    monkeypatch.setattr(store, "_reveal_committed", must_not_be_asked)
    monkeypatch.setattr(store, "_unlock_granted", must_not_be_asked)
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    assert len(load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)) == 1


# ---- W5 - W9: the record and the predicate ---------------------------------------------------

_OTHER_SPLITS = Splits(
    dev_end=dt.date(2023, 12, 31),
    holdout_start=dt.date(2024, 1, 2),
    holdout_end=dt.date(2026, 10, 30),
)
_NEST = "[" * 100000 + "]" * 100000

_NOT_A_REVEAL: list[tuple[str, str]] = [
    ("open only", _text(_open())),
    ("complete only", _text(_complete())),
    ("complete before open", _text(_complete(), _open())),
    ("different ids", _text(_open(ID), _complete(OTHER_ID))),
    ("hand-typed id", _text(_open("abc"), _complete("abc"))),
    ("uppercase id", _text(_open(ID.upper()), _complete(ID.upper()))),
    ("trailing newline in id", _text(_open(ID + "\n"), _complete(ID + "\n"))),
    ("integer id", _text(_open(7), _complete(7))),
    ("truncated json", _text(_open(), '{"schema": 1, "kind": "reveal_complete", "reve')),
    ("no schema", _text({**_open(), "schema": None}, _complete())),
    ("schema 2", _text(_open(schema=2), _complete(schema=2))),
    ("schema true", _text(_open(schema=True), _complete(schema=True))),
    ("schema 1.0", _text(_open(schema=1.0), _complete(schema=1.0))),
    ("unknown kind", _text(_open(kind="reveal_opened"), _complete())),
    ("complete has no schema", _text(_open(), {"kind": "reveal_complete", "reveal_id": ID})),
    ("other dev_end", _text(_open(splits=_OTHER_SPLITS), _complete())),
    ("other holdout_end", _text(_open(holdout_end="2026-10-29"), _complete())),
    ("missing date", _text({k: v for k, v in _open().items() if k != "dev_end"}, _complete())),
    ("compact date", _text(_open(dev_end="20251231"), _complete())),
    ("date as integer", _text(_open(dev_end=20251231), _complete())),
    ("not an object", _text("[1, 2]", '"reveal_open"', "7", "null")),
    ("binary", "\x00\x01\x02 \udcff\n"),
    ("deeply nested", _NEST + "\n"),
    ("empty", ""),
]


@pytest.mark.parametrize(("label", "text"), _NOT_A_REVEAL, ids=[row[0] for row in _NOT_A_REVEAL])
def test_a_partial_or_forged_log_is_not_a_completed_reveal(label: str, text: str) -> None:
    assert store._completed_reveal_in(text, SPLITS) is False


def test_a_well_formed_pair_is_a_completed_reveal() -> None:
    assert store._completed_reveal_in(_text(_open(), _complete()), SPLITS) is True


def test_a_pair_surrounded_by_junk_and_look_lines_is_still_a_completed_reveal() -> None:
    look = {"schema": 1, "kind": "look_open", "look_id": ID, "reveal_id": None}
    text = _text(
        "garbage", "", look, _open(OTHER_ID, dev_end="1999-01-01"), _open(), "{", _complete()
    )

    assert store._completed_reveal_in(text, SPLITS) is True


def test_a_pair_for_other_splits_is_a_reveal_only_of_those(repo: Path) -> None:
    _commit_record(repo, _text(_open(), _complete()))

    assert store._reveal_committed_in(repo, SPLITS) is True
    assert store._reveal_committed_in(repo, _OTHER_SPLITS) is False


def test_the_predicate_asks_git_and_not_the_filesystem(repo: Path) -> None:
    path = repo / REVEAL_RECORD_GIT_PATH
    path.parent.mkdir(parents=True)
    path.write_text(_text(_open(), _complete()), encoding="utf-8")

    assert store._reveal_committed_in(repo, SPLITS) is False  # in the working tree only

    _git(repo, "add", "-f", REVEAL_RECORD_GIT_PATH)
    assert store._reveal_committed_in(repo, SPLITS) is False  # staged only

    _git(repo, "commit", "-q", "-m", "record")
    assert store._reveal_committed_in(repo, SPLITS) is True


def test_a_record_under_another_storage_root_does_not_lift_the_seal(
    repo: Path, tmp_path: Path
) -> None:
    elsewhere = Settings(
        universe=("SPY",),
        exogenous=(),
        calendar="XNYS",
        splits=SPLITS,
        storage_root=tmp_path / "rehearsal",
    )
    reveal_id = store.record_reveal_opened(elsewhere, details={})
    store.record_reveal_completed(elsewhere, reveal_id)
    assert store._completed_reveal_in(
        (elsewhere.storage_root / AUDIT_FILENAME).read_text(encoding="utf-8"), SPLITS
    )

    assert store._reveal_committed_in(repo, SPLITS) is False


def test_every_git_failure_leaves_the_window_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Completed:
        def __init__(self, returncode: int, stdout: Any) -> None:
            self.returncode = returncode
            self.stdout = stdout

    good = _text(_open(), _complete()).encode()
    failures: list[Callable[..., Any]] = []

    def raising(exc: BaseException) -> Callable[..., Any]:
        def run(*args: object, **kwargs: object) -> Any:
            raise exc

        return run

    failures += [
        raising(subprocess.TimeoutExpired("git", 5)),
        raising(OSError("no git")),
        raising(subprocess.SubprocessError("odd")),
        lambda *a, **k: Completed(128, b""),
        lambda *a, **k: Completed(0, b"\xff\xfe\x00 not utf-8 \xff"),
        lambda *a, **k: Completed(0, "a str, not the bytes asked for"),
    ]
    for run in failures:
        monkeypatch.setattr(store.subprocess, "run", run)
        assert store._reveal_committed_in(tmp_path, SPLITS) is False

    # The control: the same stub shape with a good blob does lift it.
    monkeypatch.setattr(store.subprocess, "run", lambda *a, **k: Completed(0, good))
    assert store._reveal_committed_in(tmp_path, SPLITS) is True

    monkeypatch.undo()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert store._reveal_committed_in(tmp_path, SPLITS) is False  # not a repository


# ---- the memo --------------------------------------------------------------------------------


def test_the_predicate_is_memoised_per_splits_and_only_reset_forces_a_re_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[Splits] = []
    answer = {"value": False}

    def fake(root: Path, splits: Splits) -> bool:
        assert root == store._REPO_ROOT
        asked.append(splits)
        return answer["value"]

    monkeypatch.setattr(store, "_reveal_committed_in", fake)
    monkeypatch.setattr(store, "_REVEAL_CACHE", {})

    assert _REAL_REVEAL_COMMITTED(SPLITS) is False
    assert _REAL_REVEAL_COMMITTED(SPLITS) is False
    assert len(asked) == 1
    assert _REAL_REVEAL_COMMITTED(_OTHER_SPLITS) is False  # other splits, own entry
    assert len(asked) == 2

    answer["value"] = True
    assert _REAL_REVEAL_COMMITTED(SPLITS) is False  # a cached False is not re-asked
    store._reset_reveal_cache()
    assert _REAL_REVEAL_COMMITTED(SPLITS) is True
    answer["value"] = False
    assert _REAL_REVEAL_COMMITTED(SPLITS) is True  # monotone within a process


# ---- the log: ordering, crash, and failure ---------------------------------------------------


def test_the_open_line_survives_a_sigkill_inside_the_read(tmp_path: Path) -> None:
    """The mutant this discriminates is the order: log-after-read leaves no line at all."""
    root = tmp_path / "store"
    script = textwrap.dedent(
        """
        import datetime as dt, os, signal, sys
        from pathlib import Path
        import pyarrow as pa, pyarrow.parquet as pq
        from volab import store
        from volab.config import Settings, Splits
        from volab.paths import curated_path

        root = Path(sys.argv[1])
        day = dt.date(2024, 3, 1)
        path = curated_path(root, "bars_daily", "SPY", day)
        path.parent.mkdir(parents=True)
        pq.write_table(pa.table({"symbol": ["SPY"]}), path)
        settings = Settings(
            universe=("SPY",), exogenous=(), calendar="XNYS",
            splits=Splits(
                dev_end=dt.date(2023, 12, 31),
                holdout_start=dt.date(2024, 1, 2),
                holdout_end=dt.date(2024, 12, 31),
            ),
            storage_root=root,
        )
        # No conftest here, so both halves of the suite's policing are stubbed by hand.
        store._unlock_granted = lambda _: True
        store._reveal_committed = lambda splits: False
        store._scan = lambda patterns: os.kill(os.getpid(), signal.SIGKILL)
        store.load_panel("bars_daily", end=day, allow_holdout=True, settings=settings)
        """
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("VOLAB_")}

    child = subprocess.run(
        [sys.executable, "-c", script, str(root)], capture_output=True, env=env, check=False
    )

    assert child.returncode == -9, child.stderr.decode()
    lines = (root / AUDIT_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["kind"] == "look_open"


def test_a_look_that_cannot_be_logged_does_not_happen(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _synthetic(synthetic_store, dt.date(2026, 10, 30))
    synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY])
    _grant_both_keys_without_holding_one(monkeypatch)
    touched: list[str] = []

    def unwritable(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "_append_audit", unwritable)
    monkeypatch.setattr(store, "_matching_files", lambda p: touched.append("matching") or [])
    monkeypatch.setattr(store, "_scan", lambda p: touched.append("scan"))

    with pytest.raises(OSError, match="disk full"):
        load_panel("bars_daily", end=HOLDOUT_DAY, allow_holdout=True, settings=settings)

    assert touched == []


def test_an_unwritable_close_line_never_displaces_a_refusal_but_does_surface_on_success(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _synthetic(synthetic_store, dt.date(2026, 10, 30))
    synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY])
    _grant_both_keys_without_holding_one(monkeypatch)

    def no_close(*args: object, **kwargs: object) -> None:
        raise OSError("close failed")

    monkeypatch.setattr(store, "_close_look", no_close)

    from volab.errors import DatasetNotFound

    with pytest.raises(DatasetNotFound):
        load_panel(
            "bars_daily",
            start=HOLDOUT_DAY + DAY,
            end=HOLDOUT_DAY + 2 * DAY,
            allow_holdout=True,
            settings=settings,
        )
    with pytest.raises(OSError, match="close failed"):
        load_panel("bars_daily", end=HOLDOUT_DAY, allow_holdout=True, settings=settings)


def test_a_step_three_refusal_is_not_a_look(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY])

    with pytest.raises(HoldoutViolation):
        load_panel("bars_daily", end=HOLDOUT_DAY, settings=synthetic_store.settings)

    assert not (synthetic_store.root / AUDIT_FILENAME).exists()


def test_a_reveal_scope_labels_the_looks_it_encloses(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _synthetic(synthetic_store, dt.date(2026, 10, 30))
    synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY])
    _grant_both_keys_without_holding_one(monkeypatch)

    with store.reveal_scope(ID):
        load_panel("bars_daily", end=HOLDOUT_DAY, allow_holdout=True, settings=settings)
        with store.reveal_scope(ID):  # the same id is the same event
            pass
        with pytest.raises(RuntimeError), store.reveal_scope(OTHER_ID):
            pass
    load_panel("bars_daily", end=HOLDOUT_DAY, allow_holdout=True, settings=settings)

    records = [
        json.loads(line)
        for line in (settings.storage_root / AUDIT_FILENAME).read_text().splitlines()
    ]
    assert [r["reveal_id"] for r in records] == [ID, ID, None, None]
    assert store._REVEAL_SCOPE.get() is None


def test_the_reveal_writers_pin_the_splits_and_write_what_the_guard_accepts(
    tmp_path: Path,
) -> None:
    settings = Settings(
        universe=("SPY",), exogenous=(), calendar="XNYS", splits=SPLITS, storage_root=tmp_path
    )

    reveal_id = store.record_reveal_opened(settings, details={"evidence": [1, 2]})
    text = (tmp_path / AUDIT_FILENAME).read_text(encoding="utf-8")
    assert store._completed_reveal_in(text, SPLITS) is False  # open only
    store.record_reveal_completed(settings, reveal_id, details={"ok": True})

    text = (tmp_path / AUDIT_FILENAME).read_text(encoding="utf-8")
    opened, completed = (json.loads(line) for line in text.splitlines())
    assert (opened["kind"], completed["kind"]) == ("reveal_open", "reveal_complete")
    assert opened["reveal_id"] == completed["reveal_id"] == reveal_id
    assert opened["details"] == {"evidence": [1, 2]}
    assert store._completed_reveal_in(text, SPLITS) is True
    assert store._completed_reveal_in(text, _OTHER_SPLITS) is False


# ---- shape witnesses: there is no second route -----------------------------------------------


def _function(name: str) -> ast.FunctionDef:
    found = [n for n in ast.walk(_STORE_TREE) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(found) == 1, name
    return found[0]


def _calls_of(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]


def _names(tree: ast.AST, name: str) -> list[ast.Name]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == name]


def _functions_mentioning(name: str) -> set[str]:
    return {
        node.name
        for node in _STORE_TREE.body
        if isinstance(node, ast.FunctionDef) and _names(node, name)
    }


def test_the_seal_is_one_comparison_against_one_bound() -> None:
    load = _function("load_panel")
    gates = [
        n
        for n in ast.walk(load)
        if isinstance(n, ast.Compare)
        and isinstance(n.left, ast.Name)
        and n.left.id == "end"
        and isinstance(n.ops[0], ast.Gt)
    ]
    # The bound, and ``past_dev_end = end > effective.dev_end`` which feeds the predicate's
    # short-circuit and the audit flag. Nothing else may compare ``end`` upward.
    assert sorted(ast.unparse(g.comparators[0]) for g in gates) == ["effective.dev_end", "limit"]
    assert sum(1 for g in gates if ast.unparse(g.comparators[0]) == "limit") == 1

    assert len(_calls_of(_STORE_TREE, "_read_limit")) == 1
    assert len(_calls_of(_STORE_TREE, "_unlock_granted")) == 1
    # Only the guard and the read-only accessor ask; and only by calling, never by aliasing.
    assert _functions_mentioning("_reveal_committed") == {"load_panel", "reveal_is_committed"}
    assert len(_names(_STORE_TREE, "_reveal_committed")) == len(
        _calls_of(_STORE_TREE, "_reveal_committed")
    )
    # The predicate is asked about the caller's clamped splits, never the repository's.
    (ask,) = _calls_of(load, "_reveal_committed")
    assert isinstance(ask.args[0], ast.Name) and ask.args[0].id == "effective"


def test_nothing_but_git_and_the_splits_decides_the_seal() -> None:
    for name in (
        "_read_limit",
        "_completed_reveal_in",
        "_reveal_committed_in",
        "_reveal_committed",
    ):
        function = _function(name)
        for node in ast.walk(function):
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"environ", "getenv", "storage_root"}, name
            if isinstance(node, ast.Name):
                assert node.id not in {
                    "storage_root",
                    "AUDIT_FILENAME",
                    "Settings",
                    "_UNLOCK_ENV",
                    "HOLDOUT_UNLOCK_ENV",
                    "load_settings",
                }, name
    # And the one that takes a root takes it for testability: load_panel never names it.
    assert not _names(_function("load_panel"), "_reveal_committed_in")
    assert not _names(_function("load_panel"), "_REPO_ROOT")


def test_only_one_function_opens_the_audit_log() -> None:
    owners = {
        node.name
        for node in ast.walk(_STORE_TREE)
        if isinstance(node, ast.FunctionDef) and _names(node, "AUDIT_FILENAME")
    }
    assert owners == {"_append_audit"}

    for path in sorted(_SRC.rglob("*.py")):
        if path.name == "store.py" and path.parent == _SRC:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            named = (
                (isinstance(node, ast.Name) and node.id == "AUDIT_FILENAME")
                or (isinstance(node, ast.Attribute) and node.attr == "AUDIT_FILENAME")
                or (isinstance(node, ast.alias) and node.name == "AUDIT_FILENAME")
                or (isinstance(node, ast.Constant) and node.value == "holdout_access.jsonl")
            )
            assert not named, f"{path} names the audit log"


_PUBLIC = frozenset(
    {
        "load_panel",
        "git_state",
        "reveal_is_committed",
        "reveal_scope",
        "record_reveal_opened",
        "record_reveal_completed",
        "write_curated",
        "write_features",
        "write_raw",
    }
)


def test_no_public_name_can_change_the_seals_state() -> None:
    own = {
        name
        for name, value in vars(store).items()
        if not name.startswith("_")
        and callable(value)
        and getattr(value, "__module__", "") == "volab.store"
    }
    # ``load_panel`` is the conftest's policing wrapper while a test runs, so its ``__module__``
    # is not ours; it is asserted to exist rather than counted.
    assert callable(vars(store)["load_panel"])
    assert own == _PUBLIC - {"load_panel"}


def test_the_cache_can_be_filled_only_from_git_and_emptied_only_by_the_reset() -> None:
    assert _functions_mentioning("_REVEAL_CACHE") == {"_reveal_committed", "_reset_reveal_cache"}
    (store_site,) = [
        n
        for n in ast.walk(_function("_reveal_committed"))
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Subscript)
    ]
    assert isinstance(store_site.value, ast.Call)
    assert isinstance(store_site.value.func, ast.Name)
    assert store_site.value.func.id == "_reveal_committed_in"
    reset = _function("_reset_reveal_cache")
    assert [n.attr for n in ast.walk(reset) if isinstance(n, ast.Attribute)] == ["clear"]


def test_the_predicates_path_is_what_git_actually_tracks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gitignore = (store._REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert f"!{REVEAL_RECORD_GIT_PATH}" in gitignore
    assert (
        load_settings().storage_root / AUDIT_FILENAME == store._REPO_ROOT / REVEAL_RECORD_GIT_PATH
    )

    seen: list[list[str]] = []

    class Completed:
        returncode = 128
        stdout = b""

    def fake(argv: list[str], **kwargs: object) -> Completed:
        seen.append(argv)
        return Completed()

    monkeypatch.setattr(store.subprocess, "run", fake)
    store._reveal_committed_in(tmp_path, SPLITS)

    assert len(seen) == 1
    (argv,) = seen
    assert "--no-optional-locks" in argv and "cat-file" in argv
    assert f"HEAD:{REVEAL_RECORD_GIT_PATH}" in argv
    assert not {"rev-list", "log", "--all", "--follow"} & set(argv)


def test_every_audit_append_is_fsynced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[int] = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    settings = Settings(
        universe=("SPY",), exogenous=(), calendar="XNYS", splits=SPLITS, storage_root=tmp_path
    )

    store.record_reveal_opened(settings, details={})

    assert len(synced) == 1


def test_the_suite_is_sealed_whatever_head_says(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "_reveal_committed_in", lambda root, splits: True)
    assert store._reveal_committed(SPLITS) is False
    assert store.reveal_is_committed() is False

    settings = dataclasses.replace(synthetic_store.settings, splits=load_settings().splits)
    with pytest.raises(HoldoutViolation):
        load_panel("bars_daily", symbols=["SPY"], end=dt.date(2026, 6, 15), settings=settings)
