"""The rehearsal: what R158 Q2 now rests on, and a whole daily run off the real store.

R158 Q2 was the user's decision that the live daily path "never invokes" ``walkforward``,
``compare`` or ``evaluators`` -- so that the three reported ledgers are untouchable by construction.
P4-06c broke the letter of that (R163): the forecast stage imports ``volab.walkforward`` and lazily
``volab.compare.cohorts``, and importing the second runs ``volab.compare``'s ``__init__``, which
imports ``volab.compare.sink``. What survives is the thing the decision protected: **the daily path
appends nothing to ``data/trials.jsonl``, ``data/comparisons.jsonl`` or ``data/decisions.jsonl``.**
It is now a property of tests and not of the shape of the import graph, and this file is two of
those tests.

**Half one is a derived check on the property, not on a package list.** It finds the *writer entry
points* by reading the tree, builds the transitive import closure of everything the daily path
executes, and asserts that no module in that closure names a writer entry point in code, outside
the writers' own bodies. It is a floor on carelessness and not a proof; its limit is stated on
``_closure_violations``.

**Half two is a rehearsal.** A complete ``run`` on a dev date against a ``tmp_path`` storage root,
with the repository's own ``data/`` measured either side *inside the test*: both reported ledgers
by line count and digest, and every file and directory under ``data/`` by size and mtime. It is
the only test in the suite that mechanically shows a daily run cannot move a reported number.

Nothing here reads the store past ``dev_end``, reaches for a holdout key, or writes under the
repository's ``data/``. Every plant is in memory or under ``tmp_path``.
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from test_daily import _LEDGER_FILENAMES, _appends_to_a_file
from test_daily_forecast import (
    _END,
    _FITTED,
    _START,
    _clock,
    _fit_ledger,
    _stages_with_the_real_forecast,
)

import volab.daily as daily_package
import volab.daily.__main__ as daily_cli
from volab.daily import LIVE_STAGES, Stage
from volab.daily.runlog import completed_dates, run_log_path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _REPO_ROOT / "src" / "volab"

# ---------------------------------------------------------------------------------------------
# Half one: the transitive import check
# ---------------------------------------------------------------------------------------------


def _tree_sources() -> dict[str, str]:
    """Every module under ``src/volab``, by dotted name, a package by the name of its package."""
    sources: dict[str, str] = {}
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        parts = ("volab", *path.relative_to(_SOURCE_ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        sources[".".join(parts)] = path.read_text(encoding="utf-8")
    return sources


def _is_daily(module: str) -> bool:
    return module == "volab.daily" or module.startswith("volab.daily.")


def _functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


@dataclass(frozen=True)
class WriterEntryPoints:
    """What the tree says writes a reported ledger, and where it lives.

    ``functions`` are the names of defs that open a file for appending, found in a module that
    names one of the three ledger files. ``classes`` are the classes that own such a def (or a
    same-named one), because constructing a sink is the route to a write. ``modules`` are where
    they were found: the *writer modules*, inside whose writer defs a reference is the writer's
    own plumbing.
    """

    functions: frozenset[str]
    classes: frozenset[str]
    modules: frozenset[str]

    @property
    def names(self) -> frozenset[str]:
        return self.functions | self.classes


def _writer_entry_points(sources: Mapping[str, str]) -> WriterEntryPoints:
    """Derive the writer entry points by reading ``sources``; nothing here is a list of names.

    A def is a writer if its own text opens a file for appending (``_appends_to_a_file``, the
    detector the package-level guard in ``tests/test_daily.py`` already uses) **and** its module
    names one of the three ledger files. The conjunction is that guard's, for the same reason:
    ``volab.store`` appends to ``holdout_access.jsonl`` and is not a ledger writer, and
    ``volab.leaderboard`` names both ledgers and appends to neither.

    A delegating method, which appends nothing itself (``JsonlSink.append_trials`` calls
    ``walkforward.append_trials``), shares its name with the def it delegates to, so naming the
    entry points by *name* covers it without a second rule.
    """
    functions: set[str] = set()
    modules: set[str] = set()
    for module, source in sources.items():
        if _is_daily(module) or not any(name in source for name in _LEDGER_FILENAMES):
            continue
        for function in _functions(ast.parse(source)):
            if _appends_to_a_file(ast.unparse(function)):
                functions.add(function.name)
                modules.add(module)
    classes = {
        node.name
        for module in modules
        for node in ast.walk(ast.parse(sources[module]))
        if isinstance(node, ast.ClassDef)
        and any(function.name in functions for function in _functions(node))
    }
    return WriterEntryPoints(frozenset(functions), frozenset(classes), frozenset(modules))


def _imports_of(module: str, source: str, known: Mapping[str, str]) -> set[str]:
    """The modules ``source`` imports, anywhere in it (a function-level import counts).

    ``from a import b`` adds ``a.b`` when that is a module, because ``b`` may be a submodule and
    not a name; importing ``a.b.c`` adds ``a`` and ``a.b`` as well, because their ``__init__``
    runs. A relative import is not followed here -- ``_opaque_imports`` fails the check on it.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    reached: set[str] = set()
    for name in found:
        parts = name.split(".")
        reached.update(
            ".".join(parts[:i]) for i in range(1, len(parts) + 1) if ".".join(parts[:i]) in known
        )
    return reached


def _closure(sources: Mapping[str, str], seeds: set[str]) -> frozenset[str]:
    """Every module reachable from ``seeds`` by imports, parents included."""
    seen: set[str] = set()
    todo = list(seeds)
    while todo:
        module = todo.pop()
        if module in seen or module not in sources:
            continue
        seen.add(module)
        todo.extend(_imports_of(module, sources[module], sources))
    return frozenset(seen)


def _seeds(sources: Mapping[str, str], stages: tuple[Stage, ...]) -> set[str]:
    """What the daily path executes: every ``volab.daily`` module, and every planned stage.

    A stage is run as ``python -m <module>``, which executes ``<module>.__main__`` when that is a
    package -- a file nothing imports, so an import graph seeded only from ``volab.daily`` never
    reaches it. The stage roots are derived from the stage tuple, not listed.
    """
    seeds = {module for module in sources if _is_daily(module)}
    for stage in stages:
        seeds.add(stage.module)
        seeds.add(f"{stage.module}.__main__")
    return seeds & sources.keys()


def _opaque_imports(source: str) -> list[str]:
    """The edges an import walk cannot follow: relative imports and dynamic import machinery.

    The check fails closed on these rather than treating them as no edge.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            found.append(f"relative import at line {node.lineno}")
        elif isinstance(node, ast.Name) and node.id in {"__import__", "importlib"}:
            found.append(f"{node.id} at line {node.lineno}")
        elif isinstance(node, ast.Attribute) and node.attr in {"import_module", "__import__"}:
            found.append(f"{node.attr} at line {node.lineno}")
        elif isinstance(node, ast.Import) and any(a.name == "importlib" for a in node.names):
            found.append(f"import importlib at line {node.lineno}")
    return found


def _is_string_statement(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _node_hit(node: ast.AST, names: frozenset[str]) -> tuple[str, str, int] | None:
    """Whether ``node`` itself, not its children, names one of ``names`` in code.

    Returns ``(kind, name, line)``.

    A name is matched as a variable, an attribute, the *original* name in an import (so ``as x``
    does not hide it), or a string constant containing the name as a whole word (so
    ``getattr(module, "append_trials")`` and ``"volab.walkforward:append_trials"`` do not hide it;
    prose is kept out by the bare-string-statement exclusion, not by matching exactly). A
    def's own name is none of these, which is what makes a writer's definition of itself
    invisible to the scan.
    """
    if isinstance(node, ast.Name) and node.id in names:
        return ("name", node.id, node.lineno)
    if isinstance(node, ast.Attribute) and node.attr in names:
        return ("attribute", node.attr, node.lineno)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        for name in sorted(names):
            if re.search(rf"\b{re.escape(name)}\b", node.value):
                return ("string", name, node.lineno)
    return None


def _module_hits(
    module: str, source: str, entries: WriterEntryPoints
) -> tuple[list[tuple[str, str, int]], dict[str, int]]:
    """The hits in ``module`` outside a writer's own body, and how many each name has inside one.

    Code, not prose: a bare string statement (a docstring, or any string left standing alone) is
    skipped whole.

    The exclusion is by **location**: the body of a def, in a writer module, whose name is a
    writer entry point -- ``append_trials`` itself, and ``JsonlSink.append_trials`` delegating to
    it. Not by module: ``volab.walkforward`` is a writer module and also holds ``evaluate``, which
    the daily path calls, and a future ``evaluate`` that appended would have to fire.
    """
    inside: dict[str, int] = {}
    outside: list[tuple[str, str, int]] = []

    def visit(node: ast.AST, in_writer: bool) -> None:
        if _is_string_statement(node):
            return
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and module in entries.modules
            and node.name in entries.functions
        ):
            in_writer = True
        if isinstance(node, ast.ImportFrom):
            # The imported name's original spelling, wherever it is bound to.
            hits = [("import", a.name, node.lineno) for a in node.names if a.name in entries.names]
        else:
            hit = _node_hit(node, entries.names)
            hits = [hit] if hit else []
        for kind, name, line in hits:
            if in_writer:
                inside[name] = inside.get(name, 0) + 1
            else:
                outside.append((kind, name, line))
        for child in ast.iter_child_nodes(node):
            visit(child, in_writer)

    visit(ast.parse(source), False)
    return outside, inside


def _closure_violations(
    sources: Mapping[str, str],
    closure: frozenset[str],
    entries: WriterEntryPoints,
) -> list[str]:
    """Every place a module in ``closure`` names a writer entry point in code.

    Three exclusions, each pinned by a test of its own: prose (a docstring naming
    ``append_trials`` is how three modules here explain why they do not call it), a writer
    module's own **definitions** of the entry points, and a reference inside the body of one --
    plus the module-level import that serves such a body and nothing else.

    **The limit, stated because a guard whose reach is not stated invites trust it has not
    earned (R159).** This reads what the closure *names*, not what runs. It sees an import, a
    call, a bare reference, an attribute and a string equal to a writer's name. It cannot see a
    call assembled at run time (``getattr(m, prefix + "trials")``, a name built from parts), a
    writer reached through a helper that does not itself bear one of the derived names (a new
    function in a writer module that appends *without* the shape ``_appends_to_a_file`` knows), a
    callback handed in from outside the closure (``run_configuration`` takes ``append`` as a
    parameter and a caller can pass anything), or a write by a stage subprocess of a module this
    file did not seed. Relative imports and dynamic-import machinery are refused outright rather
    than skipped. A module in the closure that is **imported** but never **called** is not a
    violation, and cannot be told from one by static means: that is the demotion R163 records, and
    why ``test_the_writer_modules_the_daily_path_can_import_are_exactly_these_two`` pins the
    modules whose import is the residual risk.
    """
    found: list[str] = []
    for module in sorted(closure):
        outside, inside = _module_hits(module, sources[module], entries)
        for kind, name, line in outside:
            # An import that exists only to serve a writer's own body is plumbing, not a use.
            if (
                kind == "import"
                and module in entries.modules
                and inside.get(name, 0) > 0
                and not any(k != "import" and n == name for k, n, _ in outside)
            ):
                continue
            found.append(f"{module}:{line} names {name} ({kind})")
        found.extend(f"{module}: {problem}" for problem in _opaque_imports(sources[module]))
    return found


# --- the real tree -----------------------------------------------------------------------------


def test_the_writer_entry_points_are_derived_and_hold_the_three_known_writers() -> None:
    """The floor: a derivation that found nothing would let every check below pass vacuously.

    A floor and not an equality, so a fourth writer needs no edit here.
    """
    entries = _writer_entry_points(_tree_sources())

    assert {"append_trials", "append_comparisons", "append_decisions"} <= entries.functions
    assert {"JsonlSink"} <= entries.classes
    assert {"volab.walkforward", "volab.compare.sink", "volab.evaluators.sink"} <= entries.modules


def test_a_module_that_appends_to_a_file_other_than_a_ledger_is_not_a_writer_entry_point() -> None:
    """``volab.store`` appends to ``holdout_access.jsonl``, which is none of the three."""
    sources = _tree_sources()
    assert _appends_to_a_file(sources["volab.store"])  # the premise: it does append somewhere
    assert not any(name in sources["volab.store"] for name in _LEDGER_FILENAMES)

    entries = _writer_entry_points(sources)

    assert "volab.store" not in entries.modules
    assert "volab.leaderboard" not in entries.modules


def test_the_closure_holds_what_the_forecast_stage_made_reachable() -> None:
    """The floor under the closure, and the answer to whether R158 Q2's letter is recoverable.

    A closure of ``volab.daily`` alone, or an empty one, must fail here. ``volab.compare.sink`` is
    in it by way of ``volab.compare.cohorts`` -> ``volab.compare`` -> ``volab.compare.sink``; it is
    not named by any ``volab.daily`` module.
    """
    sources = _tree_sources()

    closure = _closure(sources, _seeds(sources, LIVE_STAGES))

    assert {
        "volab.daily.forecast",
        "volab.daily.champion",
        "volab.walkforward",
        "volab.compare",
        "volab.compare.cohorts",
        "volab.compare.sink",
        "volab.leaderboard.__main__",
        "volab.ingest.__main__",
        "volab.features.__main__",
    } <= closure
    assert len(closure) > 20


def test_the_writer_modules_the_daily_path_can_import_are_exactly_these_two() -> None:
    """The residual risk, pinned. ``walkforward`` is imported for ``evaluate`` and ``load_targets``;
    ``compare.sink`` comes along with ``compare.cohorts``. Neither is called to write.

    Adding a third -- ``volab.evaluators.sink`` above all, whose ledger is the decisions file -- is
    a decision, and this test makes it one: it fails and says which module arrived.
    """
    sources = _tree_sources()
    entries = _writer_entry_points(sources)

    closure = _closure(sources, _seeds(sources, LIVE_STAGES))

    assert closure & entries.modules == {"volab.walkforward", "volab.compare.sink"}
    assert "volab.walkforward.__main__" not in closure
    assert "volab.compare.__main__" not in closure
    assert not any(module.startswith("volab.evaluators") for module in closure)


def test_nothing_the_daily_path_can_import_names_a_writer_entry_point() -> None:
    sources = _tree_sources()
    entries = _writer_entry_points(sources)
    closure = _closure(sources, _seeds(sources, LIVE_STAGES))

    assert _closure_violations(sources, closure, entries) == []


def test_the_docstrings_that_name_append_trials_are_real_and_do_not_fire() -> None:
    """The prose exclusion, grounded on the two real files that rely on it."""
    sources = _tree_sources()
    entries = _writer_entry_points(sources)

    for module in ("volab.daily.forecast", "volab.daily.runlog"):
        assert "append_trials" in sources[module], module
        assert _closure_violations(sources, frozenset({module}), entries) == [], module


# --- the red witnesses: every plant is in memory -----------------------------------------------


def _planted(
    extra: Mapping[str, str] | None = None, *, append_to: Mapping[str, str] | None = None
) -> tuple[dict[str, str], WriterEntryPoints, frozenset[str]]:
    """The real tree with modules added or source appended, and what the check makes of it."""
    sources = _tree_sources()
    for module, text in (append_to or {}).items():
        sources[module] += "\n" + text
    sources.update(extra or {})
    entries = _writer_entry_points(sources)
    return sources, entries, _closure(sources, _seeds(sources, LIVE_STAGES))


def _new_violations(
    sources: Mapping[str, str], closure: frozenset[str], entries: WriterEntryPoints
) -> list[str]:
    """What a plant added, so an exclusion test is about the plant and not the tree's own state."""
    real = _tree_sources()
    baseline = set(
        _closure_violations(
            real, _closure(real, _seeds(real, LIVE_STAGES)), _writer_entry_points(real)
        )
    )
    return [v for v in _closure_violations(sources, closure, entries) if v not in baseline]


def test_a_call_in_a_non_writer_function_inside_the_closure_fires() -> None:
    sources, entries, closure = _planted(
        append_to={
            "volab.daily.forecast": (
                "def _sneaky() -> None:\n"
                "    append_trials([], root=None, at=None, sha='', dirty=None)\n"
            )
        }
    )

    violations = _closure_violations(sources, closure, entries)

    assert [v for v in violations if "volab.daily.forecast" in v and "append_trials" in v]


def test_a_call_added_to_a_function_inside_a_writer_module_fires() -> None:
    """The reason the exclusion is by location: ``walkforward`` is a writer module, and a
    function there that is *not* a writer must not be waved through with the module."""
    sources, entries, closure = _planted(
        append_to={
            "volab.walkforward": (
                "def evaluate_and_record(trials: object) -> int:\n"
                "    return append_trials(trials, root=None, at=None, sha='', dirty=None)\n"
            )
        }
    )

    violations = _closure_violations(sources, closure, entries)

    assert any(v.startswith("volab.walkforward:") and "append_trials" in v for v in violations)


def test_a_comparison_sink_call_fires_through_an_attribute() -> None:
    sources, entries, closure = _planted(
        append_to={"volab.daily.runner": "def _go(sink):\n    sink.append_comparisons([])\n"}
    )

    violations = _closure_violations(sources, closure, entries)

    assert any(
        "volab.daily.runner" in v and "append_comparisons (attribute)" in v for v in violations
    )


def test_a_decision_sink_call_fires() -> None:
    sources, entries, closure = _planted(
        append_to={"volab.daily.runner": "def _go(sink):\n    sink.append_decisions([])\n"}
    )

    assert any("append_decisions" in v for v in _closure_violations(sources, closure, entries))


def test_a_string_spelling_of_the_name_fires() -> None:
    sources, entries, closure = _planted(
        append_to={
            "volab.daily.runner": (
                "def _go(module):\n    return getattr(module, 'append_trials')\n"
            )
        }
    )

    violations = _closure_violations(sources, closure, entries)

    assert any("append_trials (string)" in v for v in violations)


def test_an_aliased_import_fires_at_the_import() -> None:
    sources, entries, closure = _planted(
        append_to={"volab.daily.runner": "from volab.walkforward import append_trials as w\n"}
    )

    violations = _closure_violations(sources, closure, entries)

    assert any("volab.daily.runner" in v and "append_trials (import)" in v for v in violations)


def test_constructing_the_sink_class_fires() -> None:
    sources, entries, closure = _planted(
        append_to={
            "volab.daily.runner": (
                "from volab.compare.sink import JsonlSink\n\n\n"
                "def _go(root):\n    return JsonlSink(root)\n"
            )
        }
    )

    violations = _closure_violations(sources, closure, entries)

    assert any("JsonlSink" in v and "volab.daily.runner" in v for v in violations)


def test_importing_the_evaluators_grows_the_closure_and_the_pin_and_the_scan_both_fire() -> None:
    """A daily module that imports ``volab.evaluators.sink`` is the case R158 Q2 forbade."""
    sources, entries, closure = _planted(
        append_to={"volab.daily.runner": "import volab.evaluators.sink\n"}
    )

    assert "volab.evaluators.sink" in closure
    assert "volab.evaluators.sink" in closure & entries.modules
    assert closure & entries.modules != {"volab.walkforward", "volab.compare.sink"}
    # And the scan itself: a module that names the class it constructs fires on top of the pin.
    sources, entries, closure = _planted(
        append_to={
            "volab.daily.runner": (
                "from volab.evaluators.sink import JsonlSink\n\n\n"
                "def _go(root):\n    return JsonlSink(root)\n"
            )
        }
    )
    assert any("JsonlSink" in v for v in _closure_violations(sources, closure, entries))


def test_a_package_imported_for_a_leaf_brings_its_init_into_the_closure() -> None:
    """Importing ``a.b`` runs ``a/__init__``: the edge an import-statement walk alone would miss."""
    sources, entries, closure = _planted(
        extra={"volab.zz": "import volab.evaluators.sink\n", "volab.zz.leaf": ""},
        append_to={"volab.daily.runner": "import volab.zz.leaf\n"},
    )

    assert "volab.zz" in closure
    assert "volab.evaluators.sink" in closure


def test_a_from_import_of_a_submodule_brings_the_submodule_into_the_closure() -> None:
    """``from a import b`` names no module ``a.b``, and ``b`` may be one."""
    sources, entries, closure = _planted(
        extra={"volab.zz": "", "volab.zz.leaf": "import volab.evaluators.sink\n"},
        append_to={"volab.daily.runner": "from volab.zz import leaf\n"},
    )

    assert "volab.zz.leaf" in closure
    assert "volab.evaluators.sink" in closure


def test_a_stage_that_runs_a_writer_module_brings_its_main_into_the_closure() -> None:
    """The seed is derived from the stage tuple: a ``-m`` stage runs ``__main__``, which no
    ``import`` statement reaches."""
    sources = _tree_sources()
    planted = (*LIVE_STAGES, Stage(name="planted", module="volab.walkforward", argv=("run",)))

    closure = _closure(sources, _seeds(sources, planted))
    entries = _writer_entry_points(sources)

    assert "volab.walkforward.__main__" in closure
    assert any(
        "volab.walkforward.__main__" in v and "append_trials" in v
        for v in _closure_violations(sources, closure, entries)
    )


@pytest.mark.parametrize(
    "planted",
    [
        "from . import sibling\n",
        "from .sibling import thing\n",
        "import importlib\n",
        "def _go():\n    return __import__('volab.walkforward')\n",
        "def _go(importlib):\n    return importlib.import_module('volab.walkforward')\n",
    ],
)
def test_a_dynamic_or_relative_import_in_the_closure_fails_closed(planted: str) -> None:
    sources, entries, closure = _planted(append_to={"volab.daily.runner": planted})

    violations = _closure_violations(sources, closure, entries)

    assert any("volab.daily.runner" in v for v in violations)


def test_the_real_closure_has_no_import_the_walk_cannot_follow() -> None:
    sources = _tree_sources()
    closure = _closure(sources, _seeds(sources, LIVE_STAGES))

    assert [(m, _opaque_imports(sources[m])) for m in closure if _opaque_imports(sources[m])] == []


# --- the exclusions, one test each -------------------------------------------------------------


def test_a_docstring_naming_a_writer_entry_point_does_not_fire() -> None:
    sources, entries, closure = _planted(
        append_to={
            "volab.daily.runner": (
                'def _doc() -> None:\n    """Does not call append_trials or JsonlSink."""\n\n\n'
                '"""A stray string statement mentioning append_comparisons."""\n'
            )
        }
    )

    assert _new_violations(sources, closure, entries) == []


def test_a_comment_naming_a_writer_entry_point_does_not_fire() -> None:
    sources, entries, closure = _planted(
        append_to={"volab.daily.runner": "# never calls append_trials\n"}
    )

    assert _new_violations(sources, closure, entries) == []


def test_a_writer_modules_own_definition_of_an_entry_point_does_not_fire() -> None:
    """The defs and their delegating twins are what the entry points *are*.

    Asked of the exclusion itself and not of the whole module, so a violation elsewhere in a
    writer module cannot make this test fail for a reason it is not about.
    """
    sources = _tree_sources()
    entries = _writer_entry_points(sources)
    inside_a_writer = 0

    for module in sorted(entries.modules):
        tree = ast.parse(sources[module])
        spans = [
            range(function.lineno, (function.end_lineno or function.lineno) + 1)
            for function in _functions(tree)
            if function.name in entries.functions
        ]
        outside, inside = _module_hits(module, sources[module], entries)
        assert not [hit for hit in outside if any(hit[2] in span for span in spans)], module
        inside_a_writer += sum(inside.values())

    # The premise: there *were* references inside writer bodies for the exclusion to exclude.
    assert inside_a_writer > 0


def test_the_import_that_serves_a_writers_body_does_not_fire_once_the_name_is_used_elsewhere() -> (
    None
):
    """The module-level import is excused only while every use of it is inside a writer's body."""
    sources = _tree_sources()
    entries = _writer_entry_points(sources)
    assert "append_trials" in sources["volab.compare.sink"]
    assert _closure_violations(sources, frozenset({"volab.compare.sink"}), entries) == []

    planted = dict(sources)
    planted["volab.compare.sink"] += (
        "\n\ndef _outside() -> None:\n"
        "    append_trials([], root=None, at=None, sha='', dirty=None)\n"
    )
    violations = _closure_violations(planted, frozenset({"volab.compare.sink"}), entries)

    assert any("append_trials (name)" in v for v in violations)
    assert any("append_trials (import)" in v for v in violations)


def test_an_import_of_an_entry_point_in_a_module_that_is_not_a_writer_module_always_fires() -> None:
    sources, entries, closure = _planted(
        append_to={"volab.daily.runner": "from volab.walkforward import append_trials\n"}
    )

    assert any(
        "volab.daily.runner" in v and "append_trials (import)" in v
        for v in _closure_violations(sources, closure, entries)
    )


def test_a_module_outside_the_closure_is_not_scanned() -> None:
    """``volab.walkforward.__main__`` calls ``append_trials`` legitimately; the scan is of the
    closure it is given, so the same module is clean outside it and fires inside it."""
    sources = _tree_sources()
    entries = _writer_entry_points(sources)
    closure = _closure(sources, _seeds(sources, LIVE_STAGES))
    main = "volab.walkforward.__main__"

    assert "append_trials" in sources[main]
    assert _closure_violations(sources, closure - {main}, entries) == []
    assert _closure_violations(sources, closure | {main}, entries)


# ---------------------------------------------------------------------------------------------
# Half two: the rehearsal
# ---------------------------------------------------------------------------------------------

_LEDGERS = ("trials.jsonl", "comparisons.jsonl")
# Files the daily path itself owns. Neither exists in a fresh checkout and neither may appear in
# the repository as a consequence of a rehearsal.
_OWN_FILES = ("daily_runs.jsonl", "forecasts.jsonl")


@dataclass(frozen=True)
class Measurement:
    """The repository's ``data/`` at one moment: the two ledgers, and everything else by stat."""

    ledgers: dict[str, tuple[int, str]]
    tree: dict[str, tuple[bool, int, int]]


def _ledger_reading(path: Path) -> tuple[int, str]:
    data = path.read_bytes()
    return data.count(b"\n"), hashlib.md5(data).hexdigest()  # noqa: S324 -- a fingerprint


def _stat_tree(root: Path) -> dict[str, tuple[bool, int, int]]:
    """Every file and directory under ``root`` as ``(is_dir, size, mtime_ns)``, not following links.

    A directory's mtime moves when an entry is added to or removed from it, so a file created
    anywhere under ``data/`` shows here even where its parent was not otherwise looked at; a file
    edited in place moves its own size or mtime.
    """
    found: dict[str, tuple[bool, int, int]] = {}
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                is_dir = entry.is_dir(follow_symlinks=False)
                found[os.path.relpath(entry.path, root)] = (is_dir, info.st_size, info.st_mtime_ns)
                if is_dir:
                    stack.append(Path(entry.path))
    found["."] = (True, 0, root.stat().st_mtime_ns)
    return found


def _measure(repo: Path) -> Measurement:
    """Both reported ledgers (which must exist and be non-empty) and the whole of ``data/``.

    A ledger that is absent or empty is a failure here, not a reading: ``None == None`` would let
    a run that deleted both pass the comparison.
    """
    ledgers: dict[str, tuple[int, str]] = {}
    for name in _LEDGERS:
        path = repo / "data" / name
        assert path.is_file(), f"{path} must exist for the rehearsal to mean anything"
        lines, digest = _ledger_reading(path)
        assert lines > 0, f"{path} is empty"
        ledgers[name] = (lines, digest)
    return Measurement(ledgers=ledgers, tree=_stat_tree(repo / "data"))


def _assert_unmoved(before: Measurement, after: Measurement) -> None:
    assert after.ledgers == before.ledgers, "a reported ledger moved"
    created = sorted(set(after.tree) - set(before.tree))
    removed = sorted(set(before.tree) - set(after.tree))
    changed = sorted(
        p for p in before.tree.keys() & after.tree.keys() if before.tree[p] != after.tree[p]
    )
    assert (created, removed, changed) == ([], [], []), "data/ was touched"


def _fake_repo(root: Path) -> Path:
    """A stand-in for the repository: two non-empty ledgers and a file elsewhere under ``data/``."""
    (root / "data" / "raw").mkdir(parents=True)
    for name in _LEDGERS:
        (root / "data" / name).write_text('{"n": 1}\n{"n": 2}\n', encoding="utf-8")
    (root / "data" / "raw" / "bars.parquet").write_bytes(b"not really parquet")
    return root


def test_a_ledger_that_gained_a_line_is_not_unmoved(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path / "repo")
    before = _measure(repo)

    with (repo / "data" / "trials.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"n": 3}\n')

    with pytest.raises(AssertionError, match="a reported ledger moved"):
        _assert_unmoved(before, _measure(repo))


def test_a_ledger_rewritten_to_the_same_length_is_not_unmoved(tmp_path: Path) -> None:
    """The digest and not only the line count: an in-place edit keeps the count."""
    repo = _fake_repo(tmp_path / "repo")
    before = _measure(repo)

    (repo / "data" / "comparisons.jsonl").write_text('{"n": 9}\n{"n": 2}\n', encoding="utf-8")

    with pytest.raises(AssertionError, match="a reported ledger moved"):
        _assert_unmoved(before, _measure(repo))


def test_a_new_file_anywhere_under_data_is_not_unmoved(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path / "repo")
    before = _measure(repo)

    (repo / "data" / "raw" / "forecasts.jsonl").write_text("{}\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="data/ was touched"):
        _assert_unmoved(before, _measure(repo))


def test_a_file_edited_in_place_under_data_is_not_unmoved(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path / "repo")
    before = _measure(repo)

    (repo / "data" / "raw" / "bars.parquet").write_bytes(b"not really parquet, edited")

    with pytest.raises(AssertionError, match="data/ was touched"):
        _assert_unmoved(before, _measure(repo))


def test_a_measurement_of_an_untouched_tree_is_unmoved(tmp_path: Path) -> None:
    """The comparison is not trivially red: two readings of nothing happening agree."""
    repo = _fake_repo(tmp_path / "repo")

    _assert_unmoved(_measure(repo), _measure(repo))


def test_a_repository_without_its_ledgers_cannot_be_measured(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()

    with pytest.raises(AssertionError, match="must exist"):
        _measure(tmp_path)


_APPENDER = """\
import sys

with open(sys.argv[1], "a", encoding="utf-8") as handle:
    handle.write('{"planted": true}\\n')
"""


def _rehearse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    repo: Path,
    plant_into: Path | None = None,
) -> tuple[int, Measurement, Measurement]:
    """Run the whole ``run`` verb on a dev date with ``tmp_path`` as the storage root.

    The stages are three child interpreters like the real ones, and not the real ``LIVE_STAGES``:
    ``ingest backfill`` reaches the network and writes ``data/vendor_payloads/`` in the repository,
    ``features build`` takes no root, and ``plan_for`` appends no ``--root`` to any stage, so
    handing ``main`` a ``root`` moves only the run record. What stands in is a probe, the shipped
    ``volab.leaderboard`` stage module pointed at ``tmp_path``, and the real forecast ``main``
    behind the shim ``tests/test_daily_forecast.py`` already uses (fake loaders, so no store is
    read). ``plant_into`` adds a stage that appends a line to that file -- the witness that the
    measurement bites when a stage does what the rehearsal exists to rule out.
    """
    _fit_ledger(tmp_path)
    _stages_with_the_real_forecast(monkeypatch, tmp_path)
    probe, forecast = daily_package.LIVE_STAGES
    stages = [
        probe,
        Stage(name="leaderboard", module="volab.leaderboard", argv=("--root", str(tmp_path))),
        forecast,
    ]
    if plant_into is not None:
        (tmp_path / "probes" / "ledger_appender.py").write_text(_APPENDER, encoding="utf-8")
        stages.insert(2, Stage(name="appender", module="ledger_appender", argv=(str(plant_into),)))
    monkeypatch.setattr(daily_package, "LIVE_STAGES", tuple(stages))

    before = _measure(repo)
    code = daily_cli.main(["run", "--as-of", _FITTED], root=tmp_path, clock=_clock(_START, _END))
    return code, before, _measure(repo)


def test_a_whole_daily_run_leaves_the_repositorys_ledgers_and_data_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the story. Read the two ledgers, run the day, read them again.

    The readings are taken from the files and compared with each other, not with constants: the
    property is that a run does not move them, and a pinned digest would fail the day the reveal
    legitimately appends. (At the time of writing they are 414 lines /
    ``090e37b08f03d0548f63d1515ec8e35c`` and 52 / ``b9f41c2651f10f3f9288d6cb427e2c9a``, which
    the report for this story records.) ``_measure`` refuses an absent or empty ledger, so the
    comparison cannot be ``None == None``.
    """
    owned_before = {name: (_REPO_ROOT / "data" / name).exists() for name in _OWN_FILES}

    code, before, after = _rehearse(tmp_path, monkeypatch, repo=_REPO_ROOT)

    assert code == 0
    _assert_unmoved(before, after)
    # The daily path's own files exist where the run was rooted, and are not newly in the repo.
    assert (tmp_path / "data" / "daily_runs.jsonl").is_file()
    assert (tmp_path / "data" / "forecasts.jsonl").is_file()
    assert {name: (_REPO_ROOT / "data" / name).exists() for name in _OWN_FILES} == owned_before
    # And what the run recorded: a start line, a completion line, a result for every stage.
    log = run_log_path(tmp_path)
    start, complete = (json.loads(text) for text in log.read_text(encoding="utf-8").splitlines())
    assert (start["kind"], complete["kind"]) == ("start", "complete")
    assert complete["ref"] == start["id"]
    assert complete["outcome"] == "done"
    assert [stage["name"] for stage in complete["stages"]] == ["probe", "leaderboard", "forecast"]
    assert [stage["exit_code"] for stage in complete["stages"]] == [0, 0, 0]
    assert completed_dates(log) == {dt.date.fromisoformat(_FITTED)}


def test_the_rehearsal_measurement_bites_when_a_stage_appends_to_a_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same run, with one more stage that appends to a stand-in repository's trial ledger.

    The stand-in is under ``tmp_path``; the real ledgers are not touched, which is the claim this
    whole file protects.
    """
    repo = _fake_repo(tmp_path / "repo")

    code, before, after = _rehearse(
        tmp_path, monkeypatch, repo=repo, plant_into=repo / "data" / "trials.jsonl"
    )

    assert code == 0
    with pytest.raises(AssertionError, match="a reported ledger moved"):
        _assert_unmoved(before, after)


def test_the_fixture_ledgers_the_real_rehearsal_depends_on_exist() -> None:
    """Fail by name, with the cause, if a checkout has no ledgers rather than a bare KeyError."""
    for name in _LEDGERS:
        assert (_REPO_ROOT / "data" / name).is_file(), name
    assert re.fullmatch(r"[0-9a-f]{32}", _ledger_reading(_REPO_ROOT / "data" / "trials.jsonl")[1])
