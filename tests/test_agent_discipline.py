"""The Phase 5 fences, built before any code of the phase (R192, after R140's precedent).

This repository's founding negative result is that an LLM reviewer approved a feature built from
future intraday bars. So the agent must be *unable* to reach the data layer, not discouraged from
it, and the only defences this project accepts are ones a test can fail. Four scans cover the
three new packages -- ``volab.agent``, ``volab.candidate`` and ``volab.promote`` -- and each is
parametrised per module found on disk, so a module added later is covered without anyone editing
a list. Each has a red case that proves it bites and a non-vacuity test, because a parametrised
test over an empty list passes by collecting nothing (R151).

**What none of these can see.** They read syntax and an import graph. An import built at runtime
(``importlib.import_module(name)``), one reached through ``getattr``, a key assembled from
fragments, or a subprocess launched with ``-m`` is invisible to every scan here. The process
boundary and the harness's fixed window carry the rest; this file only closes the part a test
can close.

**The fence is on the import graph and never on ``sys.modules``.** Measured: pandas 3.0 imports
pyarrow unconditionally, so ``"pyarrow" in sys.modules`` is true in every process that touched a
frame and proves nothing at all.

The helpers are imported from ``tests/test_import_discipline.py`` and ``tests/holdout_scan.py``
rather than copied, so a repair to either follows here. Nothing in the three packages reads
Parquet, so ``ALLOWED`` there is untouched.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest
from holdout_scan import _docstring_constants, holdout_keys_in
from test_import_discipline import (
    SRC,
    STORE_READS,
    _ancestors,
    _first_party_imports,
    _graph,
    _module_name,
    _packages,
    _store_reaches,
)

from volab.daily import LIVE_STAGES, Stage

REPO = SRC.parent.parent
TESTS = REPO / "tests"

PHASE5_PACKAGES = ("agent", "candidate", "promote")
# ``candidate`` is the evaluation child. It must read the panel through ``load_panel`` -- that is
# its whole job -- so it is exempt from the store-reach fence and from nothing else (R192). The
# exemption is by directory and is pinned by its own test, so it cannot quietly grow.
STORE_FENCE_EXEMPT = {"candidate"}
STORE_MODULE = "volab.store"


def _phase5_modules() -> list[Path]:
    found: list[Path] = []
    for package in PHASE5_PACKAGES:
        found.extend((SRC / package).rglob("*.py"))
    return sorted(found)


def _fenced_modules() -> list[Path]:
    return [
        path
        for path in _phase5_modules()
        if path.relative_to(SRC).parts[0] not in STORE_FENCE_EXEMPT
    ]


def _ident(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def test_the_scanned_set_is_not_empty_and_holds_the_three_package_inits() -> None:
    """Non-vacuity for all four scans: they parametrise over this set, so empty means no tests."""
    found = {_ident(path) for path in _phase5_modules()}
    assert {"agent/__init__.py", "candidate/__init__.py", "promote/__init__.py"} <= found, found
    assert {_ident(p) for p in _fenced_modules()} >= {"agent/__init__.py", "promote/__init__.py"}


def test_the_store_fence_exempts_exactly_the_evaluation_child() -> None:
    """The same shape as the probe directory's ``sorted(ALLOWED) == ["store.py"]`` pin.

    ``volab.candidate`` is the only package here that may reach the panel, and the exemption is
    asserted whole rather than argued, so adding a second directory fails this test rather than
    passing quietly.
    """
    assert {"candidate"} == STORE_FENCE_EXEMPT
    assert not {_ident(p) for p in _fenced_modules()} & {
        _ident(p) for p in (SRC / "candidate").rglob("*.py")
    }


# ---------------------------------------------------------------------------
# 1. The transitive store-reach fence.
# ---------------------------------------------------------------------------


def _store_chain(
    start: str, graph: dict[str, str], packages: dict[str, str] | None = None
) -> list[str]:
    """The import chain from ``start`` to ``volab.store``, or ``[]`` if there is none.

    Breadth-first, so the chain is a shortest one and reads as an explanation. Each module's own
    parent packages are edges as well, because importing ``volab.agent.x`` executes
    ``volab.agent.__init__``. A name that is not in ``graph`` is a symbol imported from a module
    and is skipped rather than treated as a missing edge.
    """
    queue: list[list[str]] = [[start]]
    seen = {start}
    while queue:
        chain = queue.pop(0)
        node = chain[-1]
        if node == STORE_MODULE and len(chain) > 1:
            return chain
        source = graph.get(node)
        if source is None:
            continue
        edges = _first_party_imports(source, (packages or {}).get(node, "volab"))
        edges |= _ancestors(node) - {node}
        for target in sorted(edges):
            if target not in seen:
                seen.add(target)
                queue.append([*chain, target])
    return []


@pytest.mark.parametrize("module", _fenced_modules(), ids=_ident)
def test_the_agent_side_reaches_no_read_path_even_indirectly(module: Path) -> None:
    """R192. Walks the import graph from ``module`` and fails with the chain it found.

    ``volab.features.targets`` does ``from volab.store import write_features``, so a one-line
    ``from volab.features.targets import HORIZONS`` pulls ``volab.store``, duckdb and pyarrow in
    two hops while a per-file scan passes it -- the hole R129 found in the numeric scan, one
    subsystem over. The module's own source is also checked for naming any of ``STORE_READS``.
    """
    name = _module_name(module)
    chain = _store_chain(name, _graph(), _packages())
    assert not chain, f"{name} reaches the store through {' -> '.join(chain)} (R192)"
    named = _store_reaches(module.read_text(encoding="utf-8"))
    assert not named, (
        f"{name} names a read path: {named} (R192; STORE_READS is {sorted(STORE_READS)})"
    )


def test_a_two_hop_import_through_features_targets_is_caught_with_its_chain() -> None:
    """The realistic mistake, planted in a synthetic graph rather than in the tree.

    Chosen over a ``src/volab/agent/_plant.py`` so nothing another story is editing is touched and
    nothing needs deleting afterwards. The graph is the real one plus one module, so the walk is
    the walk the parametrised test runs. Two hops: the planted module, ``volab.features.targets``,
    then ``volab.store``.
    """
    plant = "volab.agent._plant"
    graph = {**_graph(), plant: "from volab.features.targets import HORIZONS\n"}
    packages = {**_packages(), plant: "volab.agent"}
    chain = _store_chain(plant, graph, packages)
    assert chain == [plant, "volab.features.targets", STORE_MODULE], chain


def test_a_relative_import_and_a_parent_package_init_are_also_followed() -> None:
    """The walk follows relative imports and ``__init__`` edges, not just absolute names."""
    graph = {
        "volab.agent.a": "from .b import x\n",
        "volab.agent.b": "from volab.store import write_features\n",
        "volab.agent.c": "x = 1\n",
        "volab.agent": "x = 1\n",
        "volab": "",
        "volab.store": "",
    }
    packages = {"volab.agent.a": "volab.agent", "volab.agent.b": "volab.agent"}
    assert _store_chain("volab.agent.a", graph, packages) == [
        "volab.agent.a",
        "volab.agent.b",
        STORE_MODULE,
    ]
    # ``volab.agent.c`` imports nothing, but the ``volab`` package it lives under does.
    graph["volab"] = "from volab.store import write_features\n"
    assert _store_chain("volab.agent.c", graph, packages) == [
        "volab.agent.c",
        "volab",
        STORE_MODULE,
    ]


def test_a_clean_module_has_no_store_chain() -> None:
    graph = {"volab.agent.a": "import json\nfrom volab.errors import ProposalRefused\n"}
    graph |= {"volab.errors": "import json\n", "volab": "", "volab.agent": ""}
    assert _store_chain("volab.agent.a", graph) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from volab.store import write_features\n", "imports from volab.store"),
        ("from volab.panel import load_curated_panel\n", "imports load_curated_panel from"),
        ("frame = load_panel(['SPY'])\n", "names load_panel"),
        ("import volab.store\n", "imports volab.store"),
    ],
)
def test_naming_a_read_path_is_caught_per_file(source: str, expected: str) -> None:
    assert any(expected in found for found in _store_reaches(source))


# ---------------------------------------------------------------------------
# 2. The unlock-key scan.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", _phase5_modules(), ids=_ident)
def test_no_phase5_module_names_a_key_that_opens_the_holdout(module: Path) -> None:
    """R187. Every module of all three packages, ``candidate`` included: it reads dev data only.

    The names come from ``tests/holdout_scan.py``, which derives both -- the parameter from
    ``store.py``'s ``load_panel`` and the variable from ``volab.config`` -- so a rename is
    followed. Docstrings are exempt and comments are not in the AST. The child inherits its
    environment unchanged and scrubs nothing (R193), because a scrub would have to name the
    variable.
    """
    found = holdout_keys_in(module.read_text(encoding="utf-8"))
    assert not found, f"{_ident(module)} names an unlock key: {found} (R187)"


def test_the_unlock_scan_is_not_vacuous() -> None:
    """``store.py`` legitimately names the keys; if the scan cannot find them, it finds nothing."""
    assert holdout_keys_in((SRC / "store.py").read_text(encoding="utf-8")), (
        "the scan reads no key out of store.py, so it would read none out of anything"
    )


@pytest.mark.parametrize(
    "source",
    [
        "load_panel(['SPY'], allow_holdout=True)\n",
        "import os\nos.environ['VOLAB_HOLDOUT_UNLOCK'] = '1'\n",
        "from volab.config import HOLDOUT_UNLOCK_ENV\n",
    ],
)
def test_the_unlock_scan_bites_on_each_spelling_the_phase_must_never_use(source: str) -> None:
    assert holdout_keys_in(source)


# ---------------------------------------------------------------------------
# 3. No test of this phase carries the holdout marker.
# ---------------------------------------------------------------------------


def _registered_marker_names() -> list[str]:
    """The marker names ``pyproject.toml`` registers, read as ``test_conftest_policy`` reads it."""
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    markers = config["tool"]["pytest"]["ini_options"]["markers"]
    return [marker.split(":")[0].strip() for marker in markers]


def _holdout_marker() -> str:
    names = _registered_marker_names()
    assert len(names) == 1, f"expected one registered marker, found {names}; rewrite this scan"
    return names[0]


def _carries_marker(source: str, marker: str) -> bool:
    """Whether ``source`` applies ``marker``: ``pytest.mark.<marker>`` or the string in a call.

    The attribute form covers a decorator, ``pytestmark`` and ``pytest.param(marks=...)``; the
    string form covers ``request.applymarker("<marker>")``. Docstrings are exempt.
    """
    tree = ast.parse(source)
    exempt = _docstring_constants(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == marker:
            return True
        if isinstance(node, ast.Constant) and node.value == marker and node not in exempt:
            return True
    return False


def _belongs_to_phase5(source: str) -> bool:
    """A test module belongs to this phase if it imports any of the three packages (R151).

    Derived from the imports rather than enumerated by filename, so a test added under any name is
    caught. A test that only launches ``python -m volab.candidate`` in a subprocess imports
    nothing and is not seen -- the limit of reading syntax.
    """
    targets = _first_party_imports(source)
    return any(
        name == f"volab.{package}" or name.startswith(f"volab.{package}.")
        for name in targets
        for package in PHASE5_PACKAGES
    )


def _test_modules() -> list[Path]:
    return sorted(TESTS.glob("test_*.py"))


@pytest.mark.parametrize("module", _test_modules(), ids=lambda p: p.name)
def test_no_test_of_this_phase_is_marked_as_unlocking_the_holdout(module: Path) -> None:
    source = module.read_text(encoding="utf-8")
    assert not (_belongs_to_phase5(source) and _carries_marker(source, _holdout_marker())), (
        f"{module.name} imports a Phase 5 package and carries the holdout marker; "
        "this phase never unlocks the window (R187)"
    )


def test_the_marker_scan_is_not_vacuous() -> None:
    marker = _holdout_marker()
    assert _test_modules(), "no test modules found"
    assert any(_belongs_to_phase5(p.read_text(encoding="utf-8")) for p in _test_modules()), (
        "no test module imports a Phase 5 package, so the scan above checks nothing"
    )
    assert _carries_marker(f"import pytest\n@pytest.mark.{marker}\ndef test_x(): ...\n", marker)
    assert _carries_marker(f"import pytest\npytestmark = pytest.mark.{marker}\n", marker)
    assert _carries_marker(f"def test_x(request):\n    request.applymarker('{marker}')\n", marker)
    assert not _carries_marker(f'"""Mentions {marker} in prose."""\n', marker)
    for statement in (
        "import volab.agent",
        "from volab.agent import vocabulary",
        "from volab import candidate",
        "from volab.promote.gate import approve",
    ):
        assert _belongs_to_phase5(statement + "\n"), statement
    assert not _belongs_to_phase5("from volab.store import load_panel\n")


# ---------------------------------------------------------------------------
# 4. No live daily stage runs any of the three packages.
# ---------------------------------------------------------------------------


def _stage_offences(stages: tuple[Stage, ...]) -> list[str]:
    """Stage names whose module is, or sits under, one of the three packages.

    Prefix, not equality: ``volab.candidate.__main__`` is a runnable module name an equality test
    would wave through.
    """
    roots = [f"volab.{package}" for package in PHASE5_PACKAGES]
    return [
        stage.name
        for stage in stages
        if any(stage.module == root or stage.module.startswith(root + ".") for root in roots)
    ]


@pytest.mark.parametrize("stage", LIVE_STAGES, ids=lambda s: s.name)
def test_no_live_daily_stage_runs_a_phase5_package(stage: Stage) -> None:
    """The daily run is the production path and the agent is research: they must not meet.

    Asserted here and not in ``tests/test_daily.py``, which another story is editing (R187).
    """
    assert not _stage_offences((stage,)), f"live stage {stage.name} runs {stage.module}"


def test_the_stage_scan_is_not_vacuous_and_matches_by_prefix() -> None:
    assert LIVE_STAGES, "no live stages, so the parametrised test above collected nothing"
    for module in ("volab.agent", "volab.candidate.__main__", "volab.promote.gate"):
        assert _stage_offences((Stage(name="x", module=module, argv=()),)) == ["x"], module
    # A sibling whose name merely starts with the same letters is not under the package.
    assert _stage_offences((Stage(name="x", module="volab.agentic", argv=()),)) == []
