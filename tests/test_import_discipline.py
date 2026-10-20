import ast
from pathlib import Path

import pytest

import volab

SRC = Path(volab.__file__).parent
# Paths relative to SRC: only the top-level store.py is exempt, never a store.py in a subpackage.
ALLOWED = {"store.py"}
BANNED_CALLS = {"read_parquet", "scan_parquet", "read_table", "ParquetFile", "ParquetDataset"}
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
            if module == "volab.store":
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
