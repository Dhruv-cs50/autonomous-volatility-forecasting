"""Find every place a piece of source names a key that opens the sealed holdout window (R151).

Reading past ``splits.dev_end`` needs ``allow_holdout=True`` **and** the environment variable
``VOLAB_HOLDOUT_UNLOCK=1``. The Phase 5 packages must never name either, and this module is the
detector; the tests that apply it to those packages live elsewhere. It is written fresh rather
than extracted from ``tests/test_daily.py`` (R187) so that no file another story is editing is
refactored underneath it.

**Both names are derived, never typed (R151).** The environment variable is read from
``volab.config.HOLDOUT_UNLOCK_ENV``. The parameter name is read from ``store.py``'s own
``def load_panel`` **by AST and not by** ``inspect.signature``: ``tests/conftest.py``'s holdout
police replace ``volab.store.load_panel`` with a wrapper for the whole session, so the signature
at runtime may be the police's rather than the store's (R153). Rename either key and the scan
follows.

**The matching rules are deliberately not symmetric.** Identifiers are matched loosely -- a keyword
argument, a bare name, or an attribute access -- so ``allow_holdout=False`` "for clarity" is caught
too. String constants are matched strictly, by equality with the environment variable's name, so
unrelated prose that merely contains it is not an offence. **Docstrings are exempt**, because
modules say in prose that they never unlock the holdout and a text search would trip over the
sentence that promises it. Comments are not in the AST at all and so need no exemption.

What this does not cover: a key assembled at runtime (``"VOLAB_" + "HOLDOUT_UNLOCK"``) or reached
through ``getattr``. It reads syntax, not behaviour.
"""

from __future__ import annotations

import ast
from pathlib import Path

import volab
from volab import config

_STORE = Path(volab.__file__).resolve().parent / "store.py"


def holdout_key_names() -> tuple[str, str]:
    """``(parameter_name, environment_variable)`` -- derived, never typed."""
    environment: str = config.HOLDOUT_UNLOCK_ENV
    tree = ast.parse(_STORE.read_text(encoding="utf-8"))
    parameters: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "load_panel":
            for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
                if (
                    isinstance(default, ast.Constant)
                    and default.value is False
                    and "holdout" in argument.arg
                ):
                    parameters.append(argument.arg)
    assert len(parameters) == 1, (
        f"load_panel should have exactly one keyword-only holdout flag defaulting to False, "
        f"found {parameters}; this scan needs rewriting"
    )
    return parameters[0], environment


def _docstring_constants(tree: ast.AST) -> set[ast.Constant]:
    """Every ``Constant`` that is a docstring, so prose about a ban is not read as the ban."""
    found: set[ast.Constant] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if node.body and isinstance(node.body[0], ast.Expr):
            value = node.body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.add(value)
    return found


def holdout_keys_in(source: str) -> list[str]:
    """Every way ``source`` names a key that opens the sealed window, prose excluded."""
    parameter, environment = holdout_key_names()
    tree = ast.parse(source)
    exempt = _docstring_constants(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == parameter:
            found.append(f"passes {parameter}=")
        elif isinstance(node, ast.Name) and node.id == parameter:
            found.append(f"names {parameter}")
        elif isinstance(node, ast.Attribute) and node.attr == parameter:
            found.append(f"reads .{parameter}")
        elif isinstance(node, ast.Constant) and node.value == environment and node not in exempt:
            found.append(f"names {environment} outside a docstring")
    return found
