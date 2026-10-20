import ast
from pathlib import Path

import pytest

import volab

SRC = Path(volab.__file__).parent
ALLOWED = {"store.py"}
BANNED_CALLS = {"read_parquet", "read_table", "ParquetFile", "ParquetDataset"}
BANNED_IMPORTS = {"duckdb", "pyarrow", "pyarrow.parquet"}


def _modules() -> list[Path]:
    return sorted(p for p in SRC.glob("*.py") if p.name not in ALLOWED)


@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.name)
def test_module_does_not_read_parquet_directly(module: Path) -> None:
    tree = ast.parse(module.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in BANNED_CALLS:
            pytest.fail(f"{module.name} calls {node.attr}; reads belong in store.py")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in BANNED_IMPORTS, f"{module.name} imports {alias.name}"
        if isinstance(node, ast.ImportFrom) and node.module in BANNED_IMPORTS:
            pytest.fail(f"{module.name} imports from {node.module}")


def test_store_is_actually_exempt() -> None:
    # Guards against the allowlist silently covering a renamed module.
    assert (SRC / "store.py").exists()
    assert "read_parquet" in (SRC / "store.py").read_text()
