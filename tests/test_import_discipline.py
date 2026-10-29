import ast
from pathlib import Path

import pytest

import volab

SRC = Path(volab.__file__).parent
# Paths relative to SRC: only the top-level store.py is exempt, never a store.py in a subpackage.
ALLOWED = {"store.py"}
# Includes store's private read helpers: they read Parquet with none of load_panel's guard.
# For ``_scan`` and ``_matching_files`` -- named here -- every spelling is caught (absolute or
# relative import, attribute, bare name). A future private helper not in this set is caught only
# where the rule below catches it by shape: imported by name from the store module.
BANNED_CALLS = {
    "read_parquet",
    "scan_parquet",
    "read_table",
    "ParquetFile",
    "ParquetDataset",
    "_scan",
    "_matching_files",
}
# Top-level packages, so a submodule nobody anticipated (pyarrow.dataset) cannot slip through.
BANNED_ROOTS = {"duckdb", "pyarrow"}


def _modules(root: Path = SRC) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py") if path.relative_to(root).as_posix() not in ALLOWED
    )


def _violations(source: str) -> list[str]:
    """Every way ``source`` reaches Parquet other than through ``load_panel``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in BANNED_CALLS:
            found.append(f"uses {node.attr}")
        elif isinstance(node, ast.Name) and node.id in BANNED_CALLS:
            found.append(f"uses {node.id}")
        elif isinstance(node, ast.Import):
            found.extend(
                f"imports {alias.name}"
                for alias in node.names
                if alias.name.split(".")[0] in BANNED_ROOTS
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in BANNED_ROOTS:
                found.append(f"imports from {module}")
            found.extend(
                f"imports {alias.name} from {module}"
                for alias in node.names
                if alias.name in BANNED_CALLS
            )
            if module == "volab.store" or (node.level > 0 and module == "store"):
                # A private store helper reads Parquet with none of load_panel's guard.
                found.extend(
                    f"imports private {alias.name} from {module}"
                    for alias in node.names
                    if alias.name.startswith("_")
                )
    return found


@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.relative_to(SRC).as_posix())
def test_module_does_not_read_parquet_directly(module: Path) -> None:
    found = _violations(module.read_text())
    assert not found, f"{module.relative_to(SRC)}: {found}; reads belong in store.py"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import pandas as pd\npd.read_parquet('x')\n", "uses read_parquet"),
        ("from pandas import read_parquet\n", "imports read_parquet from pandas"),
        ("frame = read_parquet('x')\n", "uses read_parquet"),
        ("import polars as pl\npl.scan_parquet('x')\n", "uses scan_parquet"),
        ("import duckdb\n", "imports duckdb"),
        ("import duckdb as db\n", "imports duckdb"),
        ("import pyarrow\n", "imports pyarrow"),
        ("import pyarrow.parquet as pq\n", "imports pyarrow.parquet"),
        ("import pyarrow.dataset\n", "imports pyarrow.dataset"),
        ("from pyarrow import dataset\n", "imports from pyarrow"),
        ("from pyarrow.parquet import read_table\n", "imports from pyarrow.parquet"),
        ("from duckdb import connect\n", "imports from duckdb"),
        ("x = something.ParquetFile('x')\n", "uses ParquetFile"),
        ("from volab.store import _scan\n", "imports private _scan from volab.store"),
        ("from volab.store import _scan\n", "imports _scan from volab.store"),
        ("from ..store import _scan\n", "imports _scan from store"),
        ("from .store import _private\n", "imports private _private from store"),
        ("from volab import store\nstore._scan([])\n", "uses _scan"),
        ("import volab.store as st\nst._matching_files([])\n", "uses _matching_files"),
        ("frame = _scan([])\n", "uses _scan"),
        (
            "from volab.store import load_panel, _matching_files\n",
            "imports private _matching_files from volab.store",
        ),
    ],
    ids=lambda value: value.strip().replace("\n", " ; "),
)
def test_scan_detects_each_bypass(source: str, expected: str) -> None:
    assert expected in _violations(source)


@pytest.mark.parametrize(
    "source",
    [
        "import pandas as pd\nfrom volab.store import load_panel\n",
        "from volab.store import AUDIT_FILENAME, load_panel\n",
        "from other import _private\n",
    ],
)
def test_scan_accepts_ordinary_code(source: str) -> None:
    assert _violations(source) == []


def test_scan_recurses_and_exempts_only_the_top_level_store(tmp_path: Path) -> None:
    for name in ("store.py", "config.py", "agent/store.py", "agent/tools.py"):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("")

    scanned = {path.relative_to(tmp_path).as_posix() for path in _modules(tmp_path)}

    assert scanned == {"config.py", "agent/store.py", "agent/tools.py"}


def test_store_is_actually_exempt() -> None:
    # Guards against the allowlist silently covering a renamed module.
    assert (SRC / "store.py").exists()
    assert "read_parquet" in (SRC / "store.py").read_text()


# ---------------------------------------------------------------------------
# The probe directory. Added because R79 claimed three things about ``tools/`` and a delivery
# audit pointed out that none of them had a witness -- which is R32 turned on the ruling that
# cites R32. The scan above deliberately covers ``src/volab`` only, so these are separate tests
# over a separate root, and ``ALLOWED`` is untouched.
# ---------------------------------------------------------------------------

TOOLS = SRC.parent.parent / "tools"

# The keyword that opens the sealed window. Spelled once, here, so the test and the thing it
# forbids cannot drift apart.
UNLOCK_ARGUMENT = "allow_holdout"
UNLOCK_ENVIRONMENT = "VOLAB_HOLDOUT_UNLOCK"


def _tools_modules() -> list[Path]:
    return sorted(TOOLS.glob("*.py")) if TOOLS.is_dir() else []


def _docstring_constants(tree: ast.AST) -> set[ast.Constant]:
    """Every ``Constant`` that is a docstring, so prose about a ban is not read as the ban.

    ``tools/_harness.py`` and ``tools/README.md`` both *say* the probes never unlock the holdout,
    and a text search would trip over the sentence that promises it. Comments are not in the AST
    at all, so they need no exemption.
    """
    found: set[ast.Constant] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if node.body and isinstance(node.body[0], ast.Expr):
            value = node.body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.add(value)
    return found


def test_the_probe_directory_is_outside_the_scanned_package() -> None:
    """Why ``tools/`` cannot widen this ratchet, asserted rather than argued (R79)."""
    assert TOOLS.is_dir(), "tools/ is gone, and with it every figure the decisions log cites"
    assert not TOOLS.resolve().is_relative_to(SRC.resolve()), "tools/ is inside the scanned package"
    assert not (TOOLS / "__init__.py").exists(), "tools/ is not a package and must not become one"
    assert sorted(ALLOWED) == ["store.py"], "the probe directory must not have bought an exemption"


@pytest.mark.parametrize("module", _tools_modules(), ids=lambda p: f"tools/{p.name}")
def test_probe_does_not_read_parquet_directly(module: Path) -> None:
    """A probe reads through ``load_panel`` like every other caller (R79)."""
    found = _violations(module.read_text())
    assert not found, f"tools/{module.name}: {found}; reads belong in store.py"


def _unlocks(source: str) -> list[str]:
    """Every way ``source`` reaches the sealed window, ignoring prose that merely names it."""
    tree = ast.parse(source)
    exempt = _docstring_constants(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == UNLOCK_ARGUMENT:
            found.append(f"passes {UNLOCK_ARGUMENT}=")
        elif isinstance(node, ast.Name) and node.id == UNLOCK_ARGUMENT:
            found.append(f"names {UNLOCK_ARGUMENT}")
        elif isinstance(node, ast.Attribute) and node.attr == UNLOCK_ARGUMENT:
            found.append(f"reads .{UNLOCK_ARGUMENT}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and UNLOCK_ENVIRONMENT in node.value
            and node not in exempt
        ):
            found.append(f"names {UNLOCK_ENVIRONMENT} outside a docstring")
    return found


@pytest.mark.parametrize("module", _tools_modules(), ids=lambda p: f"tools/{p.name}")
def test_probe_does_not_unlock_the_holdout(module: Path) -> None:
    """No probe passes ``allow_holdout`` or reads the unlock key (R79)."""
    found = _unlocks(module.read_text())
    assert not found, f"tools/{module.name}: {found}; the holdout is Phase 6's single read"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("load_panel('x', end=e, allow_holdout=True)\n", "passes allow_holdout="),
        ("if settings.allow_holdout:\n    pass\n", "reads .allow_holdout"),
        ("allow_holdout = True\n", "names allow_holdout"),
        (
            "import os\nos.environ['VOLAB_HOLDOUT_UNLOCK']\n",
            "names VOLAB_HOLDOUT_UNLOCK outside a docstring",
        ),
        (
            "KEY = 'VOLAB_HOLDOUT_UNLOCK'\n",
            "names VOLAB_HOLDOUT_UNLOCK outside a docstring",
        ),
    ],
    ids=lambda value: value.strip().replace("\n", " ; "),
)
def test_the_unlock_scan_detects_each_spelling(source: str, expected: str) -> None:
    """The red case. Without it this is a ban nobody has watched fail."""
    assert expected in _unlocks(source)


@pytest.mark.parametrize(
    "source",
    [
        '"""No probe here sets allow_holdout or reads VOLAB_HOLDOUT_UNLOCK."""\n',
        'def f() -> None:\n    """Never reads VOLAB_HOLDOUT_UNLOCK."""\n',
        "# a comment naming VOLAB_HOLDOUT_UNLOCK and allow_holdout\nx = 1\n",
        "from volab.store import load_panel\nload_panel('x', end=e)\n",
    ],
    ids=("module docstring", "function docstring", "comment", "an ordinary read"),
)
def test_the_unlock_scan_ignores_prose_that_only_names_it(source: str) -> None:
    """Both ``tools/README.md`` and ``_harness.py`` promise this ban in words.

    A text search would trip over the promise itself, which is why the scan reads the AST and
    exempts docstrings. Comments never reach the AST, so they need no exemption -- asserted here
    rather than assumed, because "comments are not in the AST" is exactly the kind of sentence
    this project has been wrong about before.
    """
    assert _unlocks(source) == []


# ---------------------------------------------------------------------------
# The numeric ban, where it is still aimed. R82 admitted numpy and scipy for the model zoo and
# the comparison layer and said the ban "keeps a witness rather than becoming a convention" --
# and then shipped without one, which an architect review caught. R32 turned on the ruling that
# cites R32, for the second time in this project. This is the witness.
# ---------------------------------------------------------------------------

# The point-in-time path: the modules whose output the byte-exact comparator and truncation
# equivalence compare. Nothing here may reach for numpy or scipy, whatever is installed.
SEALED: tuple[str, ...] = (
    "panel.py",
    "store.py",
    "walkforward/__init__.py",
    "models/base.py",
)
# Every module under this subpackage is sealed too, named by directory so a new feature module
# is covered the day it is written rather than the day someone remembers to list it.
SEALED_PACKAGES: tuple[str, ...] = ("features",)

# ``arch`` belongs here although the test names below say "numpy or scipy": R82 admitted it for
# the model zoo exactly as it admitted the other two, it pulls scipy in behind it, and a sealed
# module importing it is the same violation. The names predate its admission and are left alone
# rather than renamed, because a renamed test is a citation this project has to chase (R129).
NUMERIC_ROOTS = {"numpy", "scipy", "arch"}


def _sealed_modules() -> list[Path]:
    found = [SRC / name for name in SEALED if (SRC / name).is_file()]
    for package in SEALED_PACKAGES:
        found.extend(sorted((SRC / package).rglob("*.py")))
    return found


def _numeric_imports(source: str) -> list[str]:
    """Every direct import of numpy or scipy in ``source``.

    Imports only, deliberately. ``Series.to_numpy()`` is pandas' own API and is used in this
    repository; it reaches no numpy *name*. And the check cannot be done on ``sys.modules``,
    because pandas imports numpy itself -- it would fire on every module in the project.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(
                f"imports {alias.name}"
                for alias in node.names
                if alias.name.split(".")[0] in NUMERIC_ROOTS
            )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in NUMERIC_ROOTS:
                found.append(f"imports from {node.module}")
    return found


def test_the_sealed_set_is_not_empty_and_holds_what_it_claims() -> None:
    """Guards against the scan quietly covering nothing after a rename."""
    sealed = {path.relative_to(SRC).as_posix() for path in _sealed_modules()}
    assert {"panel.py", "store.py", "walkforward/__init__.py"} <= sealed
    assert any(name.startswith("features/") for name in sealed), "the feature package vanished"
    assert sealed, "the sealed set is empty, so this ban checks nothing"


@pytest.mark.parametrize("module", _sealed_modules(), ids=lambda p: p.relative_to(SRC).as_posix())
def test_the_point_in_time_path_imports_no_numpy_or_scipy(module: Path) -> None:
    """R82's ban, where R4 and R57 aimed it: the path the guarantee rests on stays pure pandas."""
    found = _numeric_imports(module.read_text())
    assert not found, (
        f"{module.relative_to(SRC)}: {found}. R82 admits numpy and scipy for the model zoo and "
        "the comparison layer only; this path's output is compared byte-exactly."
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import numpy\n", "imports numpy"),
        ("import numpy as np\n", "imports numpy"),
        ("import scipy.stats\n", "imports scipy.stats"),
        ("from numpy import array\n", "imports from numpy"),
        ("from scipy.optimize import minimize\n", "imports from scipy.optimize"),
        ("from scipy import stats\n", "imports from scipy"),
    ],
    ids=lambda value: value.strip().replace("\n", " ; "),
)
def test_the_numeric_scan_detects_each_spelling(source: str, expected: str) -> None:
    """The red case. Without it this is a ban nobody has watched fail."""
    assert expected in _numeric_imports(source)


@pytest.mark.parametrize(
    "source",
    [
        "import pandas as pd\nvalues = frame['x'].to_numpy()\n",
        "# numpy is deliberately not imported here\nimport pandas as pd\n",
        '"""This module imports no numpy."""\nimport pandas as pd\n',
        "from volab.store import load_panel\n",
    ],
    ids=("to_numpy is pandas' own API", "a comment", "a docstring", "an ordinary import"),
)
def test_the_numeric_scan_ignores_prose_and_pandas_own_api(source: str) -> None:
    """``to_numpy()`` reaches no numpy name, and prose naming the ban is not the ban."""
    assert _numeric_imports(source) == []


# ---------------------------------------------------------------------------
# The model zoo's own boundary. A contestant is handed everything it may see in its
# ``TrainingView``: the knowable training rows, the origin's feature row, and the return series
# ending at the origin. It therefore needs no read path of its own -- and nothing enforced that
# until this scan. ``load_panel`` requires an explicit ``end`` and refuses to cross ``dev_end``,
# so a contestant calling it could not reach the holdout; it could, however, read every session
# up to ``dev_end``, which at a 2021 origin is four years of future. That is a forward peek with
# the store's own blessing, and the point-in-time tests would not see it: both panels in a
# truncation comparison would be bypassed identically.
# ---------------------------------------------------------------------------

MODEL_PACKAGE = "models"

# ``volab.store`` is banned by *module*: it exists to read Parquet and has no other purpose, so
# importing it at all is a path the view does not control.
STORE_MODULES = {"volab.store", "store"}
# ``volab.panel`` is NOT banned by module, and the first version of this scan got that wrong.
# It is a *mixed* module: alongside ``load_curated_panel`` and friends it holds ``as_of_dates``,
# ``truncate`` and ``collapse_superseded_observations``, which are pure frame helpers that open
# nothing -- and ``models/recalibrated.py`` legitimately imports ``as_of_dates``. A module-wide
# ban flagged it, which is a false positive on a side-effect-free helper. So the read *functions*
# are banned by name instead, from whichever module they are reached through.
STORE_READS = {"load_panel", "load_curated_panel", "load_bars", "load_exogenous", "load_targets"}


def _model_modules() -> list[Path]:
    package = SRC / MODEL_PACKAGE
    return sorted(package.rglob("*.py")) if package.is_dir() else []


def _store_reaches(source: str) -> list[str]:
    """Every way ``source`` could read the store instead of using the view it was handed."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(
                f"imports {alias.name}" for alias in node.names if alias.name in STORE_MODULES
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in STORE_MODULES:
                found.append(f"imports from {module}")
            found.extend(
                f"imports {alias.name} from {module}"
                for alias in node.names
                if alias.name in STORE_READS
            )
        elif isinstance(node, ast.Attribute) and node.attr in STORE_READS:
            found.append(f"calls {node.attr}")
        elif isinstance(node, ast.Name) and node.id in STORE_READS:
            found.append(f"names {node.id}")
    return found


def test_the_model_package_exists_so_this_scan_is_not_vacuous() -> None:
    assert (SRC / MODEL_PACKAGE).is_dir(), "the model zoo is gone and this ban checks nothing"
    assert _model_modules(), "no contestant modules found to scan"


@pytest.mark.parametrize("module", _model_modules(), ids=lambda p: p.relative_to(SRC).as_posix())
def test_a_contestant_cannot_reach_the_store(module: Path) -> None:
    """A contestant takes what the view gives it and has no read path of its own.

    Not a style rule. A model that read the panel itself would see every session to ``dev_end``
    regardless of its origin, and **truncation equivalence could not catch it**: the comparison
    perturbs the panel handed to the engine, and a model bypassing that panel produces the same
    answer on both sides. This is the same shape as R78 one level over -- the check watches the
    input, and the leak does not come through the input.
    """
    found = _store_reaches(module.read_text())
    assert not found, (
        f"{module.relative_to(SRC)}: {found}. A contestant receives its data in the "
        "TrainingView; reading the store directly would bypass the origin entirely."
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from volab.store import load_panel\n", "imports load_panel from volab.store"),
        (
            "from volab.panel import load_curated_panel\n",
            "imports load_curated_panel from volab.panel",
        ),
        ("import volab.store\n", "imports volab.store"),
        (
            "from volab.walkforward import load_targets\n",
            "imports load_targets from volab.walkforward",
        ),
        ("frame = store.load_panel('x', end=e)\n", "calls load_panel"),
        ("frame = load_panel('x', end=e)\n", "names load_panel"),
    ],
    ids=lambda value: value.strip().replace("\n", " ; "),
)
def test_the_store_reach_scan_detects_each_spelling(source: str, expected: str) -> None:
    """The red case. Without it this is a ban nobody has watched fail."""
    assert expected in _store_reaches(source)


@pytest.mark.parametrize(
    "source",
    [
        "import pandas as pd\nfrom volab.models.base import TrainingView\n",
        "from volab.errors import ModelSkip\n",
        '"""A contestant never calls load_panel; it uses the view."""\nimport pandas as pd\n',
        "# no load_panel here\nfrom volab.walkforward import fit_ols\n",
        # The case that caught the first version of this ban being too broad.
        "from volab.panel import as_of_dates, truncate\n",
    ],
    ids=(
        "the interface",
        "an error type",
        "a docstring",
        "a comment plus a legitimate import",
        "volab.panel's pure frame helpers",
    ),
)
def test_the_store_reach_scan_accepts_what_a_contestant_legitimately_needs(source: str) -> None:
    assert _store_reaches(source) == []


# ---------------------------------------------------------------------------
# The numeric ban, made transitive. The scan above reads one file's AST, so it catches a sealed
# module that imports numpy and misses a sealed module that imports a first-party module that
# imports numpy. The 2026-10-28 GJR design leaned on that gap deliberately -- it put the new
# scipy code in ``models/garch.py`` rather than a new ``models/skewt.py`` partly *because* a new
# scipy-importing module would open a surface this scan could not see -- and specified this
# hardening in the same breath. The design shipped and the hardening did not, which is R82's
# failure exactly: a ruling that keeps a convention rather than a witness. This is the witness.
#
# It passes today for a real reason, not an empty one: only ``models/garch.py``, ``compare/dm.py``
# and ``compare/multiple.py`` import numeric libraries anywhere in ``src``, and nothing sealed
# reaches any of them -- the sealed path imports ``models.base``, never ``models.garch``. So this
# is a ratchet over a property the tree already has, which is the only kind worth adding before
# someone breaks it. ``test_a_planted_first_party_edge_to_numpy_is_caught`` is why it cannot pass
# vacuously.
# ---------------------------------------------------------------------------


def _module_name(path: Path) -> str:
    """``SRC/models/base.py`` -> ``volab.models.base``; a package ``__init__`` -> the package."""
    parts = path.relative_to(SRC).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("volab", *parts))


def _package_of(path: Path) -> str:
    """The package a relative import inside ``path`` resolves against.

    For ``features/build.py`` that is ``volab.features``; for ``features/__init__.py`` it is also
    ``volab.features``, because a package's ``__init__`` resolves ``from . import x`` against the
    package itself. Dropping the last component gives both, since ``__init__`` *is* the package.
    """
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(("volab", *parts[:-1]))


def _ancestors(dotted: str) -> set[str]:
    """``volab.models.base`` -> itself plus ``volab.models`` and ``volab``.

    **This is the hop the first version of this scan missed**, and its docstring claimed it had.
    Python executes `volab/__init__.py` and `volab/models/__init__.py` when anything imports
    `volab.models.base`, so each is an edge whatever the import's spelling -- but only
    ``from volab.models import base`` names the parent, and the scan was reading the names rather
    than the packages they imply. The result was that ``volab.models`` and ``volab.ingest`` were
    absent from the reachable set altogether, so a numeric import placed in either package's
    ``__init__`` was invisible. Found by a delivery audit (R129), not by me.
    """
    parts = dotted.split(".")
    return {".".join(parts[: i + 1]) for i in range(len(parts))}


def _first_party_imports(source: str, package: str = "volab") -> set[str]:
    """Every ``volab`` module ``source`` imports, by dotted name, with every ancestor package.

    ``package`` is what a *relative* import resolves against -- :func:`_package_of` of the file
    being read. Relative imports were skipped entirely at first, excused by "grep finds no
    relative import under ``src``": true at the time, enforced by nothing, and ``ruff`` selects
    neither ``TID252`` nor ``ban-relative-imports``, so no gate would have noticed one appearing.

    An entry may be a *symbol* rather than a module (``from volab.paths import Tier``). That is a
    deliberate over-approximation: the walk skips a name that is not a module in the graph, so a
    spurious entry costs nothing while a missing one hides an edge.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "volab":
                    found |= _ancestors(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                # ``level`` 1 is the package itself, 2 its parent, and so on.
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                prefix = ".".join(base)
                if not prefix:
                    continue
                module = f"{prefix}.{node.module}" if node.module else prefix
            else:
                module = node.module or ""
            if module.split(".")[0] != "volab":
                continue
            found |= _ancestors(module)
            found |= {name for a in node.names for name in _ancestors(f"{module}.{a.name}")}
    return found


def _graph() -> dict[str, str]:
    """Every first-party module's dotted name to its source. ``store.py`` included: the numeric
    ban never exempted it -- ``ALLOWED`` is the *Parquet* exemption, and conflating the two would
    silently drop the one module most likely to reach for an array library."""
    return {_module_name(path): path.read_text(encoding="utf-8") for path in SRC.rglob("*.py")}


def _packages() -> dict[str, str]:
    """Each module's own package, so a relative import in it resolves to the right target."""
    return {_module_name(path): _package_of(path) for path in SRC.rglob("*.py")}


def _numeric_reach(
    start: str, graph: dict[str, str], packages: dict[str, str] | None = None
) -> list[str]:
    """The import chain from ``start`` to a direct numeric import, or ``[]`` if there is none.

    Breadth-first, so the chain reported is a shortest one and reads as an explanation rather
    than a wander. A name that is not a module in ``graph`` is a symbol imported *from* one
    (``from volab.paths import Tier``), so it is skipped rather than treated as a missing edge.
    """
    queue: list[list[str]] = [[start]]
    seen = {start}
    while queue:
        chain = queue.pop(0)
        source = graph.get(chain[-1])
        if source is None:
            continue
        if _numeric_imports(source) and len(chain) > 1:
            return chain
        package = (packages or {}).get(chain[-1], "volab")
        for target in sorted(_first_party_imports(source, package)):
            if target not in seen:
                seen.add(target)
                queue.append([*chain, target])
    return []


@pytest.mark.parametrize("module", _sealed_modules(), ids=lambda p: p.relative_to(SRC).as_posix())
def test_the_point_in_time_path_reaches_no_numpy_or_scipy_even_indirectly(module: Path) -> None:
    """R82's ban with the hop the per-file scan cannot see (the 2026-10-28 design, Decision 3)."""
    graph = _graph()
    chain = _numeric_reach(_module_name(module), graph, _packages())
    assert not chain, (
        f"{module.relative_to(SRC)} reaches a numeric library through "
        f"{' -> '.join(chain)}: {_numeric_imports(graph[chain[-1]])}. R82 admits numpy and scipy "
        "for the model zoo and the comparison layer only, and an indirect import is still an "
        "import -- this path's output is compared byte-exactly."
    )


def test_the_transitive_scan_walks_more_than_one_hop() -> None:
    """Guards against the walk degenerating into the per-file scan it exists to extend.

    If it never followed an edge, the test above would pass by doing nothing. The sealed set must
    actually reach modules outside itself -- it does: ``config``, ``errors``, ``paths``,
    ``sessions``, ``identity`` and ``ingest.curate`` are all on the path and none is sealed.
    """
    graph, packages = _graph(), _packages()
    reached = set()
    for module in _sealed_modules():
        start = _module_name(module)
        queue, seen = [start], {start}
        while queue:
            name = queue.pop(0)
            source = graph.get(name)
            if source is None:
                continue
            for target in sorted(_first_party_imports(source, packages.get(name, "volab"))):
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
        reached |= seen
    sealed_names = {_module_name(path) for path in _sealed_modules()}
    assert reached - sealed_names, "the walk followed no edge out of the sealed set"
    assert "volab.config" in reached and "volab.sessions" in reached, sorted(reached)
    # The parent packages, which the first version of this scan never reached (R129). A numeric
    # import in either ``__init__`` was invisible, so asserting their presence is the witness
    # that the ancestor edges exist at all.
    assert {"volab", "volab.models", "volab.ingest", "volab.features"} <= reached, sorted(reached)


def test_a_planted_first_party_edge_to_numpy_is_caught() -> None:
    """The mutation, run against a synthetic graph rather than by editing the tree.

    Two hops, so it fails only if the walk is genuinely transitive: ``models/base.py`` is clean,
    the module it imports is clean, and only the third imports numpy. This is the exact shape the
    design was worried about -- a ``models/skewt.py`` that the per-file scan would never see.
    """
    graph = {
        "volab.models.base": "from volab.models.skewt import kappa\n",
        "volab.models.skewt": "from volab.models.quadrature import simpson\n",
        "volab.models.quadrature": "import numpy as np\n",
    }
    assert _numeric_reach("volab.models.base", graph) == [
        "volab.models.base",
        "volab.models.skewt",
        "volab.models.quadrature",
    ]
    assert _numeric_reach("volab.models.skewt", graph) == [
        "volab.models.skewt",
        "volab.models.quadrature",
    ]
    # And a clean graph of the same shape is not reported, so the finding is the numpy import and
    # not merely the presence of edges.
    assert not _numeric_reach(
        "volab.models.base",
        {**graph, "volab.models.quadrature": "import math\n"},
    )


def test_a_numeric_import_in_a_parent_package_init_is_caught() -> None:
    """R129's first gap: the ``__init__`` that runs on the way to a submodule.

    ``from volab.models.garch import SCALE`` executes ``volab/models/__init__.py``, so numpy
    placed there is reached by anything importing *any* module in the package. The first version
    of this scan added the parent edge only for ``from volab.models import base`` -- it read the
    names written down rather than the packages they imply -- so ``volab.models`` and
    ``volab.ingest`` were absent from the reachable set and this was invisible.
    """
    graph = {
        "volab.models.base": "from volab.models.garch import SCALE\n",
        "volab.models": "import numpy as np\n",
        "volab.models.garch": "import math\n",
    }
    assert _numeric_reach("volab.models.base", graph) == ["volab.models.base", "volab.models"]
    # And by the other spelling, which runs the same __init__.
    assert _numeric_reach(
        "volab.models.base", {**graph, "volab.models.base": "import volab.models.garch\n"}
    ) == [
        "volab.models.base",
        "volab.models",
    ]
    # Clean parent, numeric grandchild: still found, by the longer chain.
    assert _numeric_reach(
        "volab.models.base",
        {**graph, "volab.models": "import math\n", "volab.models.garch": "import scipy.special\n"},
    ) == ["volab.models.base", "volab.models.garch"]


def test_a_relative_import_reaching_numpy_is_caught() -> None:
    """R129's second gap: ``node.level`` was skipped outright.

    The excuse was that ``grep`` finds no relative import under ``src``. True when written, and
    enforced by nothing -- ``ruff`` selects neither ``TID252`` nor ``ban-relative-imports``, so a
    relative import appearing would have passed every gate while silently emptying this scan.
    """
    graph = {
        "volab.features.build": "from . import helper\n",
        "volab.features.helper": "import numpy as np\n",
    }
    packages = {"volab.features.build": "volab.features"}
    assert _numeric_reach("volab.features.build", graph, packages) == [
        "volab.features.build",
        "volab.features.helper",
    ]
    # ``from .helper import f`` resolves the same way.
    assert _numeric_reach(
        "volab.features.build",
        {**graph, "volab.features.build": "from .helper import f\n"},
        packages,
    ) == ["volab.features.build", "volab.features.helper"]
    # And a level-2 import climbs out of the package.
    assert _numeric_reach(
        "volab.features.build",
        {"volab.features.build": "from .. import identity\n", "volab.identity": "import scipy\n"},
        packages,
    ) == ["volab.features.build", "volab.identity"]


def test_arch_counts_as_a_numeric_root() -> None:
    """R82 admitted ``arch`` for the model zoo exactly as it admitted numpy and scipy, and it
    pulls scipy in behind it, so a sealed module importing it is the same violation. It was
    missing from ``NUMERIC_ROOTS`` while the per-file test's name said only "numpy or scipy"
    (R129); the names are left as they are, because a renamed test is a citation to chase."""
    assert "arch" in NUMERIC_ROOTS
    assert _numeric_imports("import arch\n") == ["imports arch"]
    assert _numeric_imports("from arch.univariate import SkewStudent\n") == [
        "imports from arch.univariate"
    ]
    graph = {
        "volab.panel": "from volab.models.garch import SCALE\n",
        "volab.models.garch": "import arch\n",
    }
    assert _numeric_reach("volab.panel", graph) == ["volab.panel", "volab.models.garch"]
