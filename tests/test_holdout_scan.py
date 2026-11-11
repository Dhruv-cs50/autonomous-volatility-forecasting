"""The derived holdout-key scan (R151, R187): its derivation, its red cases, its green cases.

``tests/holdout_scan.py`` is the helper the Phase 5 packages are scanned with. This module tests
the helper itself, because a ban nobody has watched fail is a convention rather than a guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from holdout_scan import holdout_constant_names, holdout_key_names, holdout_keys_in

import volab
from volab.config import HOLDOUT_UNLOCK_ENV

_STORE = Path(volab.__file__).resolve().parent / "store.py"


def test_the_environment_key_is_the_one_config_defines() -> None:
    """The second name follows ``volab.config.HOLDOUT_UNLOCK_ENV`` rather than a typed literal."""
    assert holdout_key_names()[1] == HOLDOUT_UNLOCK_ENV


def test_the_parameter_key_is_the_one_store_py_declares() -> None:
    """The first name is ``allow_holdout``, and ``store.py`` really declares it keyword-only.

    The second assertion reads ``store.py`` by AST (as the helper does) and finds a keyword-only
    parameter of that name defaulting to ``False``, so the value above is shown to have come from
    the real function rather than from a string that happens to agree.
    """
    parameter, _ = holdout_key_names()
    assert parameter == "allow_holdout"
    tree = ast.parse(_STORE.read_text(encoding="utf-8"))
    load_panel = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "load_panel"
    )
    defaults = {
        argument.arg: default
        for argument, default in zip(
            load_panel.args.kwonlyargs, load_panel.args.kw_defaults, strict=True
        )
    }
    assert isinstance(defaults[parameter], ast.Constant)
    assert defaults[parameter].value is False


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("load_panel(x, allow_holdout=True)\n", "passes allow_holdout="),
        ("allow_holdout\n", "names allow_holdout"),
        ("settings.allow_holdout\n", "reads .allow_holdout"),
        (
            "import os\nos.environ['VOLAB_HOLDOUT_UNLOCK']\n",
            "names VOLAB_HOLDOUT_UNLOCK outside a docstring",
        ),
        (
            "import os\nos.environ.get('VOLAB_HOLDOUT_UNLOCK')\n",
            "names VOLAB_HOLDOUT_UNLOCK outside a docstring",
        ),
        (
            "import os\nos.environ.get(HOLDOUT_UNLOCK_ENV)\n",
            "names HOLDOUT_UNLOCK_ENV",
        ),
        ("import os\nos.environ[HOLDOUT_UNLOCK_ENV]\n", "names HOLDOUT_UNLOCK_ENV"),
        ("from volab.config import HOLDOUT_UNLOCK_ENV\n", "imports HOLDOUT_UNLOCK_ENV"),
        ("import volab.config as c\nc.HOLDOUT_UNLOCK_ENV\n", "reads .HOLDOUT_UNLOCK_ENV"),
        # R193: the child inherits its environment unchanged. This "scrub" is the exact thing that
        # ruling forbids, and it is the case that must stay red.
        (
            "import os\n{k: v for k, v in os.environ.items() if k != 'VOLAB_HOLDOUT_UNLOCK'}\n",
            "names VOLAB_HOLDOUT_UNLOCK outside a docstring",
        ),
    ],
    ids=(
        "keyword argument",
        "bare name",
        "attribute",
        "environ subscript",
        "environ get",
        "environment scrub (R193)",
        "constant in environ.get",
        "constant in environ subscript",
        "import of the constant",
        "constant as attribute",
    ),
)
def test_the_scan_detects_each_spelling(source: str, expected: str) -> None:
    """Each source names a key in code, and the expected finding is among the results."""
    assert expected in holdout_keys_in(source)


@pytest.mark.parametrize(
    "source",
    [
        '"""Never passes allow_holdout and never reads VOLAB_HOLDOUT_UNLOCK."""\nx = 1\n',
        'def f() -> None:\n    """Never passes allow_holdout or reads VOLAB_HOLDOUT_UNLOCK."""\n',
        "# a comment naming VOLAB_HOLDOUT_UNLOCK and allow_holdout\nx = 1\n",
        "from volab.store import load_panel\nload_panel(ds, end=d)\n",
        (
            "from volab.errors import HoldoutViolation\n"
            "try:\n    pass\nexcept HoldoutViolation:\n    pass\n"
        ),
        "NAME = 'XVOLAB_HOLDOUT_UNLOCK_NOT'\nOTHER = 'the VOLAB_HOLDOUT_UNLOCK key is sealed'\n",
    ],
    ids=(
        "module docstring",
        "function docstring",
        "comment",
        "ordinary read",
        "catching HoldoutViolation",
        "string merely containing the name",
    ),
)
def test_the_scan_ignores_prose_and_longer_strings(source: str) -> None:
    """Docstrings, comments, ordinary calls and non-exact strings produce no findings.

    Strings are matched strictly (equality with the variable's name) so that unrelated prose is
    not an offence; identifiers are matched loosely, which the red cases above cover.
    """
    assert holdout_keys_in(source) == []


def test_the_constant_name_is_discovered_from_config_not_typed() -> None:
    """The identifier that carries the variable is found by value in ``volab.config``'s namespace.

    The scan matches that identifier loosely, so a module that imports and uses the constant is
    caught as surely as one that spells the string. Asserting against ``HOLDOUT_UNLOCK_ENV`` here
    is a witness of the discovery, not its source.
    """
    assert holdout_constant_names() == ("HOLDOUT_UNLOCK_ENV",)
    assert getattr(volab.config, holdout_constant_names()[0]) == HOLDOUT_UNLOCK_ENV


def test_the_scan_is_not_vacuous_against_the_real_store() -> None:
    """``store.py`` is the one module that legitimately names the keys; the scan must see them.

    It finds the parameter in code, and finds the environment variable through the imported
    constant (the import and the ``_UNLOCK_ENV = HOLDOUT_UNLOCK_ENV`` alias). It finds no
    environment-variable *string*, because ``store.py`` names the spelling only in a docstring;
    that absence is asserted too, so this test also records what the strict string rule skips.
    """
    findings = holdout_keys_in(_STORE.read_text(encoding="utf-8"))
    parameter, variable = holdout_key_names()
    assert f"names {parameter}" in findings
    assert "imports HOLDOUT_UNLOCK_ENV" in findings
    assert "names HOLDOUT_UNLOCK_ENV" in findings
    assert not any(variable in finding for finding in findings)
