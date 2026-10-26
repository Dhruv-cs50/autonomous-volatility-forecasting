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

NUMERIC_ROOTS = {"numpy", "scipy"}


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
