"""HR-7: the daily's own seal lifts when, and only when, the reveal is committed.

``plan_for`` held a second copy of the seal -- ``if as_of > dev_end: refused`` -- that
``store.py`` lifting its own never reached, so after the reveal ``daily plan`` would still refuse
every real date. These witnesses pin the repair and, as important, pin that the repair is not a
lever: ``revealed=`` is a parameter that opens a door, so the only thing allowed to supply it is
``daily/__main__`` and the only thing allowed to feed it is ``store.reveal_is_committed()``.

Nothing here reads the store, sets a holdout key, or makes ``data/holdout_access.jsonl``.
"""

from __future__ import annotations

import ast
import datetime as dt
import inspect
from pathlib import Path

import pytest

import volab.daily.__main__ as daily_cli
from volab.config import Settings, Splits
from volab.daily import plan_for

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "volab"
_MAIN_PATH = _SOURCE_ROOT / "daily" / "__main__.py"
_INIT_PATH = _SOURCE_ROOT / "daily" / "__init__.py"

# One session in each region of the fixture's splits, plus a live session well past
# ``holdout_end``, which is what an upper bound on the revealed branch would refuse. (Not further
# out than the XNYS calendar reaches: past its last session the plan is refused instead, HR-8's
# fact, pinned in ``tests/test_daily_calendar_bound.py``.)
_DEV = dt.date(2025, 12, 30)
_HOLDOUT = dt.date(2026, 3, 3)
_LIVE = dt.date(2026, 11, 4)
_LIVE_FAR = dt.date(2027, 3, 2)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # All three dates: ``Splits.holdout_end`` has been required since P4-05b.
    return Settings(
        universe=("SPY",),
        exogenous=(),
        calendar="XNYS",
        splits=Splits(
            dev_end=dt.date(2025, 12, 31),
            holdout_start=dt.date(2026, 1, 2),
            holdout_end=dt.date(2026, 10, 30),
        ),
        storage_root=tmp_path,
    )


# --- the refusal's two states, as a table ----------------------------------------------------


@pytest.mark.parametrize(
    ("day", "revealed", "outcome"),
    [
        (_DEV, False, "work"),
        (_HOLDOUT, False, "refused"),
        (_LIVE, False, "refused"),
        (_LIVE_FAR, False, "refused"),
        (_DEV, True, "work"),
        (_HOLDOUT, True, "work"),
        (_LIVE, True, "work"),
        (_LIVE_FAR, True, "work"),
    ],
)
def test_the_refusal_table_region_by_revealed(
    settings: Settings, day: dt.date, revealed: bool, outcome: str
) -> None:
    """Revealed lifts the refusal in every region and has **no upper bound**.

    The ``_LIVE`` and ``_LIVE_FAR`` rows kill ``as_of > (holdout_end if revealed else dev_end)``,
    which refuses every live date after the reveal -- the bug this story fixes, one line lower.
    """
    assert plan_for(settings, day, revealed=revealed).outcome == outcome


def test_the_default_is_the_sealed_answer(settings: Settings) -> None:
    assert plan_for(settings, _LIVE).outcome == "refused"
    assert plan_for(settings, _LIVE, revealed=False) == plan_for(settings, _LIVE)


def test_a_revealed_closed_day_is_still_a_closed_day(settings: Settings) -> None:
    """Lifting the seal skips only the seal: the calendar and the run log still apply."""
    saturday = dt.date(2026, 11, 7)
    assert plan_for(settings, saturday, revealed=True).outcome == "no_session"
    assert plan_for(settings, _LIVE, revealed=True, completed=frozenset({_LIVE})).outcome == (
        "already_done"
    )


# --- revealed= is a lever, so pin that it is not one ------------------------------------------


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _revealed_keyword_calls() -> list[tuple[str, ast.Call, ast.keyword]]:
    """Every call anywhere under ``src/volab`` that passes ``revealed=``, whoever it calls."""
    found: list[tuple[str, ast.Call, ast.keyword]] = []
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Call):
                found += [
                    (path.relative_to(_SOURCE_ROOT).as_posix(), node, keyword)
                    for keyword in node.keywords
                    if keyword.arg == "revealed"
                ]
    return found


def test_plan_for_is_called_with_revealed_from_exactly_one_place_in_the_daily_cli() -> None:
    plan_for_calls = [
        (name, call, keyword)
        for name, call, keyword in _revealed_keyword_calls()
        if isinstance(call.func, ast.Name) and call.func.id == "plan_for"
    ]
    assert [name for name, _, _ in plan_for_calls] == ["daily/__main__.py"]
    _, _, keyword = plan_for_calls[0]
    # The argument is a call to ``reveal_is_committed`` taking nothing: not a name a caller could
    # have bound to anything, not an attribute of the arguments, not a literal.
    value = keyword.value
    assert isinstance(value, ast.Call)
    assert isinstance(value.func, ast.Name) and value.func.id == "reveal_is_committed"
    assert not value.args and not value.keywords


def test_no_other_caller_of_plan_for_exists_in_src_to_forget_the_keyword() -> None:
    callers = sorted(
        path.relative_to(_SOURCE_ROOT).as_posix()
        for path in _SOURCE_ROOT.rglob("*.py")
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "plan_for"
    )
    assert callers == ["daily/__main__.py"]


def test_reveal_is_committed_is_asked_in_the_function_that_enters_the_clock_seam() -> None:
    tree = _tree(_MAIN_PATH)

    def callers_of(name: str) -> set[str]:
        return {
            function.name
            for function in ast.walk(tree)
            if isinstance(function, ast.FunctionDef)
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        }

    assert callers_of("reveal_is_committed") == {"main"}
    assert callers_of("_today") == {"main"}


def test_nothing_the_operator_can_type_feeds_revealed() -> None:
    """No CLI argument, environment variable or config key reaches ``revealed``."""
    tree = _tree(_MAIN_PATH)
    dests = {
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        for keyword in node.keywords
        if keyword.arg == "dest" and isinstance(keyword.value, ast.Constant)
    }
    flags = {
        argument.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        for argument in node.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    }
    assert flags == {"command", "--as-of"}
    assert dests == {"as_of"}

    for path in (_MAIN_PATH, _INIT_PATH):
        mentions = {
            node.attr if isinstance(node, ast.Attribute) else node.id
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Attribute | ast.Name)
        }
        assert not mentions & {"environ", "getenv", "putenv"}, path.name

    # The one place ``revealed`` is bound in the CLI is the keyword of that call: no assignment
    # to a name or attribute called ``revealed``, no ``arguments.revealed``.
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id != "revealed"
        if isinstance(node, ast.Attribute):
            assert node.attr != "revealed"

    # And no config key: neither ``Settings`` nor ``Splits`` has a field that could carry it.
    for model in (Settings, Splits):
        assert not [name for name in model.__dataclass_fields__ if "reveal" in name]


def test_revealed_is_a_keyword_only_parameter_defaulting_to_sealed() -> None:
    parameter = inspect.signature(plan_for).parameters["revealed"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is False


def test_plan_for_stays_pure() -> None:
    """No clock, no git, no settings lookup: ``revealed`` arrives as a value."""
    function = next(
        node
        for node in ast.walk(_tree(_INIT_PATH))
        if isinstance(node, ast.FunctionDef) and node.name == "plan_for"
    )
    names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
    assert not names & {"reveal_is_committed", "git_state", "load_settings", "_now", "_today"}


def test_the_refusal_is_written_with_no_upper_bound_once_revealed() -> None:
    function = next(
        node
        for node in ast.walk(_tree(_INIT_PATH))
        if isinstance(node, ast.FunctionDef) and node.name == "plan_for"
    )
    first = next(node for node in function.body if isinstance(node, ast.If))
    assert ast.unparse(first.test) == "not revealed and as_of > settings.splits.dev_end"


# --- the CLI supplies it from the seam --------------------------------------------------------


def test_the_cli_passes_what_reveal_is_committed_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both answers, so a CLI that hardwired either constant fails one of the two."""
    monkeypatch.setattr(daily_cli, "reveal_is_committed", lambda: False)
    assert daily_cli.main(["plan", "--as-of", _LIVE.isoformat()], root=tmp_path) != 0
    assert "outcome refused" in capsys.readouterr().out

    monkeypatch.setattr(daily_cli, "reveal_is_committed", lambda: True)
    assert daily_cli.main(["plan", "--as-of", _LIVE.isoformat()], root=tmp_path) == 0
    assert "outcome work" in capsys.readouterr().out
