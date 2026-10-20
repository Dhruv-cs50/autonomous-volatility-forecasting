import ast
from pathlib import Path

import pytest

import volab

SRC = Path(volab.__file__).parent
ALLOWED = {"store.py"}
BANNED_CALLS = {"read_parquet", "read_table", "ParquetFile", "ParquetDataset"}
# Top-level packages, so a submodule nobody anticipated (pyarrow.dataset) cannot slip through.
BANNED_ROOTS = {"duckdb", "pyarrow"}


def _modules() -> list[Path]:
    return sorted(p for p in SRC.glob("*.py") if p.name not in ALLOWED)


def _violations(source: str) -> list[str]:
    """Every way ``source`` reaches Parquet other than through ``volab.store``."""
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
    return found


@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.name)
def test_module_does_not_read_parquet_directly(module: Path) -> None:
    found = _violations(module.read_text())
    assert not found, f"{module.name}: {found}; reads belong in store.py"


@pytest.mark.parametrize(
    "source",
    [
        "import pandas as pd\npd.read_parquet('x')\n",
        "from pandas import read_parquet\nread_parquet('x')\n",
        "from pandas import read_parquet\n",
        "frame = read_parquet('x')\n",
        "import duckdb\n",
        "import duckdb as db\n",
        "import pyarrow\n",
        "import pyarrow.parquet as pq\n",
        "import pyarrow.dataset\n",
        "from pyarrow import dataset\n",
        "from pyarrow.parquet import read_table\n",
        "from duckdb import connect\n",
        "import pyarrow.parquet as pq\npq.ParquetFile('x')\n",
    ],
    ids=lambda s: s.strip().replace("\n", " ; "),
)
def test_scan_detects_each_bypass(source: str, tmp_path: Path) -> None:
    # Throwaway module on disk, scanned exactly as a real one is: proves the detection bites.
    module = tmp_path / "offender.py"
    module.write_text(source)
    assert _violations(module.read_text())


def test_scan_accepts_ordinary_code(tmp_path: Path) -> None:
    module = tmp_path / "innocent.py"
    module.write_text("import pandas as pd\nfrom volab.store import load_panel\n")
    assert _violations(module.read_text()) == []


def test_store_is_actually_exempt() -> None:
    # Guards against the allowlist silently covering a renamed module.
    assert (SRC / "store.py").exists()
    assert "read_parquet" in (SRC / "store.py").read_text()
