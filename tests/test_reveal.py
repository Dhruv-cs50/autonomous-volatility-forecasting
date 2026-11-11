"""The reveal's machinery (HR-4): witnesses W1-W21 and W25-W26 of the reveal design.

Nothing here holds a key. No ``@pytest.mark.holdout``, no ``VOLAB_HOLDOUT_UNLOCK``, and no
``allow_holdout=True`` against ``data/``: every read that needs the carved window opened goes
through ``tests/carved.py``'s pseudo-holdout (2024-04-01..2024-04-12, far below the repository's
``dev_end``) and the stub below, **which forwards its argument**.

``lambda allow_holdout: allow_holdout`` and not ``lambda _: True``. A stub that discards the key
grants it whatever the caller passed, so a stage function that forgot to thread ``allow_holdout``
would read happily in every witness here and be refused in production. This stub simulates an
exported variable while keeping the argument real, so an unthreaded read fails the test.
"""

from __future__ import annotations

import ast
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import carved
import pytest

import volab
from volab import store
from volab.errors import HoldoutViolation
from volab.features.build import CLOSE_COLUMN
from volab.ingest.curate import (
    VIX_DATASET,
    _distribution_dates,
    curate_bars,
    curate_fred,
    curate_vix,
)
from volab.panel import load_bars, load_exogenous
from volab.walkforward import load_targets

SRC = Path(volab.__file__).resolve().parent
UNLOCK_ENV = "VOLAB_HOLDOUT_UNLOCK"
HOLDOUT_END = carved.SPLITS.holdout_end


def hold_the_carved_window_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both keys, simulated, without holding either: see the module docstring."""
    monkeypatch.setattr(store, "_unlock_granted", lambda allow_holdout: allow_holdout)
    assert os.environ.get(UNLOCK_ENV) is None


# ---- the key is threaded: instance witnesses --------------------------------------------------

_SEVEN: dict[str, Callable[[Any, bool], object]] = {
    "curate_bars": lambda s, k: curate_bars("SPY", end=HOLDOUT_END, settings=s, allow_holdout=k),
    "curate_vix": lambda s, k: curate_vix("VIX", end=HOLDOUT_END, settings=s, allow_holdout=k),
    "curate_fred": lambda s, k: curate_fred("DGS10", end=HOLDOUT_END, settings=s, allow_holdout=k),
    "_distribution_dates": lambda s, k: _distribution_dates(
        "QQQ", end=HOLDOUT_END, settings=s, allow_holdout=k
    ),
    "load_bars": lambda s, k: load_bars(s, "SPY", end=HOLDOUT_END, allow_holdout=k),
    "load_exogenous": lambda s, k: load_exogenous(
        s, VIX_DATASET, ("VIX",), CLOSE_COLUMN, end=HOLDOUT_END, allow_holdout=k
    ),
    "load_targets": lambda s, k: load_targets(s, "SPY", 1, end=HOLDOUT_END, allow_holdout=k),
}


def _derive_curated_holdout(settings: Any) -> None:
    """Make the curated tier reach ``holdout_end`` so the three curated-tier readers have rows."""
    for symbol in settings.universe:
        curate_bars(symbol, end=HOLDOUT_END, settings=settings, allow_holdout=True)
    curate_vix("VIX", end=HOLDOUT_END, settings=settings, allow_holdout=True)
    curate_fred("DGS10", end=HOLDOUT_END, settings=settings, allow_holdout=True)


def test_each_threaded_reader_is_refused_without_the_key_and_reads_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seven functions, two arms each. The refused arm is what makes the read arm mean something:
    with the stub forwarding its argument, the only difference between them is what the function
    passed down to ``load_panel``."""
    settings = carved.carved_store(tmp_path)
    hold_the_carved_window_open(monkeypatch)
    _derive_curated_holdout(settings)

    for name, call in _SEVEN.items():
        if name == "_distribution_dates":
            # QQQ has no dividends in the carved store, so its refusal is DatasetNotFound-shaped
            # (an empty set) only when the key was honoured; without it the guard speaks first.
            with pytest.raises(HoldoutViolation):
                call(settings, False)
            assert call(settings, True) == set()
            continue
        with pytest.raises(HoldoutViolation):
            call(settings, False)
        assert call(settings, True) is not None, name
    assert os.environ.get(UNLOCK_ENV) is None


# ---- W25: only the reveal ever passes the key --------------------------------------------------

_THREADED: dict[str, tuple[str, ...]] = {
    "ingest/curate.py": ("curate_bars", "curate_vix", "curate_fred", "_distribution_dates"),
    "panel.py": ("load_bars", "load_exogenous"),
    "walkforward/__init__.py": ("load_targets",),
}
_RESEARCH_ENTRY_POINTS: tuple[str, ...] = (
    "compare/__main__.py",
    "evaluators/__main__.py",
    "walkforward/__main__.py",
)


def _mentions_key(node: ast.AST) -> bool:
    return (
        (isinstance(node, ast.Name) and node.id == "allow_holdout")
        or (isinstance(node, ast.Attribute) and node.attr == "allow_holdout")
        or (isinstance(node, ast.keyword) and node.arg == "allow_holdout")
        or (isinstance(node, ast.arg) and node.arg == "allow_holdout")
    )


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(found) == 1, name
    return found[0]


def _callee(call: ast.Call) -> str:
    target = call.func
    return target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")


def _threading_problems(source: str, functions: tuple[str, ...]) -> list[str]:
    """What is wrong with how ``functions`` in ``source`` take and forward the key."""
    tree = ast.parse(source)
    problems: list[str] = []
    for name in functions:
        function = _function(tree, name)
        names = [a.arg for a in function.args.kwonlyargs]
        declared = dict(zip(names, function.args.kw_defaults, strict=True))
        default = declared.get("allow_holdout")
        if not (isinstance(default, ast.Constant) and default.value is False):
            problems.append(f"{name}: allow_holdout is not keyword-only with default False")
        forwarded = 0
        for call in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
            if _callee(call) not in {"load_panel", "_distribution_dates"}:
                continue
            forwarded += 1
            values = [k.value for k in call.keywords if k.arg == "allow_holdout"]
            if not (len(values) == 1 and isinstance(values[0], ast.Name)):
                problems.append(f"{name}: {_callee(call)} does not forward allow_holdout by name")
            elif values[0].id != "allow_holdout":
                problems.append(f"{name}: forwards {values[0].id}, not allow_holdout")
        if forwarded != 1 + (name == "curate_bars"):
            problems.append(f"{name}: {forwarded} forwarding call(s)")
    return problems


@pytest.mark.parametrize("relative", sorted(_THREADED))
def test_the_seven_threaded_functions_take_the_key_and_forward_it_unchanged(
    relative: str,
) -> None:
    source = (SRC / relative).read_text(encoding="utf-8")
    assert _threading_problems(source, _THREADED[relative]) == []


@pytest.mark.parametrize(
    "mutation",
    [
        ("settings=resolved, allow_holdout=allow_holdout", "settings=resolved"),
        (
            "        allow_holdout=allow_holdout,\n        settings=resolved,\n    )\n    _reject",
            "        settings=resolved,\n    )\n    _reject",
        ),
    ],
    ids=["distribution forward dropped", "bars forward dropped"],
)
def test_the_threading_scan_is_seen_failing(mutation: tuple[str, str]) -> None:
    source = (SRC / "ingest/curate.py").read_text(encoding="utf-8")
    old, new = mutation
    assert old in source
    broken = source.replace(old, new, 1)
    assert _threading_problems(broken, _THREADED["ingest/curate.py"])


def _key_problems(path: Path, source: str) -> list[str]:
    relative = path.relative_to(SRC).as_posix()
    if relative == "store.py" or relative.startswith("reveal/"):
        return []
    problems = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.keyword)
            and node.arg == "allow_holdout"
            and not isinstance(node.value, ast.Name)
        ):
            problems.append(f"{relative}: passes allow_holdout= a non-name")
    return problems


def test_only_the_reveal_ever_passes_the_key() -> None:
    """Outside ``reveal/`` and ``store.py`` the key is only ever *forwarded*, by name; a constant
    ``True`` appears nowhere. The three research entry points and ``load_for`` do not mention it."""
    for path in sorted(SRC.rglob("*.py")):
        assert _key_problems(path, path.read_text(encoding="utf-8")) == [], path
    for relative in _RESEARCH_ENTRY_POINTS:
        tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
        assert not [n for n in ast.walk(tree) if _mentions_key(n)], relative
    walkforward = ast.parse((SRC / "walkforward/__init__.py").read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(_function(walkforward, "load_for")) if _mentions_key(n)]


def test_a_key_planted_in_load_for_is_seen_failing() -> None:
    source = (SRC / "walkforward/__init__.py").read_text(encoding="utf-8")
    planted = source.replace(
        "targets = load_targets(settings, symbol, horizon, end=end)",
        "targets = load_targets(settings, symbol, horizon, end=end, allow_holdout=True)",
        1,
    )
    assert planted != source
    walkforward = ast.parse(planted)
    assert [n for n in ast.walk(_function(walkforward, "load_for")) if _mentions_key(n)]
    problems = _key_problems(SRC / "walkforward/__init__.py", planted)
    assert problems


# ---- W13: the tally counts looks and attempts separately --------------------------------------

A = "a" * 32
B = "b" * 32
C = "c" * 32


def _log(*records: Any) -> str:
    return "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in records)


def test_the_tally_counts_looks_and_attempts_separately(tmp_path: Path) -> None:
    settings = carved.settings_at(tmp_path)
    dates = {
        "dev_end": settings.splits.dev_end.isoformat(),
        "holdout_start": settings.splits.holdout_start.isoformat(),
        "holdout_end": settings.splits.holdout_end.isoformat(),
    }
    path = settings.storage_root / store.AUDIT_FILENAME
    path.parent.mkdir(parents=True)
    path.write_text(
        _log(
            "not json",
            "[1, 2]",
            {"schema": 1, "kind": "look_open", "look_id": "x"},  # unmatched
            {"schema": 1, "kind": "look_open", "look_id": "y"},
            {"schema": 1, "kind": "look_close", "look_id": "y"},
            {"schema": 7, "kind": "look_open", "look_id": "z"},  # unknown schema
            {"schema": 1, "kind": "reveal_open", "reveal_id": A, **dates},
            {"schema": 1, "kind": "reveal_open", "reveal_id": B, **dates},
            {"schema": 1, "kind": "reveal_complete", "reveal_id": B},
            {"schema": 1, "kind": "reveal_complete", "reveal_id": C},  # never opened
            {"schema": 1, "kind": "reveal_open", "reveal_id": "short", **dates},
        ),
        encoding="utf-8",
    )

    tally = store.audit_tally(settings)

    assert (tally.looks, tally.looks_closed) == (2, 1)
    assert tally.attempts == (A, B)
    assert tally.completed == frozenset({B})


def test_the_tally_of_a_store_with_no_log_is_empty(tmp_path: Path) -> None:
    tally = store.audit_tally(carved.settings_at(tmp_path))

    assert (tally.looks, tally.looks_closed, tally.attempts, tally.completed) == (
        0,
        0,
        (),
        frozenset(),
    )
    assert not (tmp_path / "data" / store.AUDIT_FILENAME).exists()
