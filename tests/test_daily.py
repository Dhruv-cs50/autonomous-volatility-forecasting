"""The daily plan, its refusals, and the two-line run record.

Three of the tests here are **derived guards** rather than lists (R151): the set of modules that
write a reported ledger, the names that unlock the holdout, and the places a clock may be called
are each computed from the tree instead of enumerated, and each ships with a red witness showing
the detector fires on a planted violation. A list cannot report its own incompleteness, and a
scheduler is precisely the module a later agent will extend.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
from pathlib import Path

import pytest

import volab.daily.__main__ as daily_cli
import volab.daily.runlog as runlog
from volab.config import HOLDOUT_UNLOCK_ENV, Settings, Splits, load_settings
from volab.daily import LIVE_STAGES, Stage, plan_for
from volab.daily.runlog import (
    StageResult,
    completed_dates,
    incomplete_run_ids,
    record_completion,
    record_start,
    run_log_path,
)

# Dates chosen against the XNYS calendar and asserted to be what they claim below, rather than
# assumed: 2025-12-30 is a session inside dev, 2025-12-25 is Christmas, 2026-11-08 is a Sunday
# past dev_end.
A_SESSION_IN_DEV = dt.date(2025, 12, 30)
A_HOLIDAY_IN_DEV = dt.date(2025, 12, 25)
A_SUNDAY_PAST_DEV_END = dt.date(2026, 11, 8)
A_SESSION_PAST_DEV_END = dt.date(2026, 11, 4)

_DAILY_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "volab" / "daily"
_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "volab"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Built directly rather than via load_settings, which insists the storage root lies inside the
    # repository. The split is the repository's own, because the refusals under test are about
    # that boundary.
    return Settings(
        universe=("SPY",),
        exogenous=(),
        calendar="XNYS",
        splits=Splits(dev_end=dt.date(2025, 12, 31), holdout_start=dt.date(2026, 1, 2)),
        storage_root=tmp_path,
    )


# --- the plan's three refusals ---------------------------------------------------------------


def test_a_date_past_dev_end_is_refused_and_the_reason_names_the_boundary(
    settings: Settings,
) -> None:
    plan = plan_for(settings, A_SESSION_PAST_DEV_END)

    assert plan.outcome == "refused"
    assert plan.stages == ()
    assert "2025-12-31" in plan.reason
    assert "no key" in plan.reason


def test_the_dev_end_refusal_is_decided_before_the_calendar_is_asked(settings: Settings) -> None:
    """A sealed date that is also a closed day is refused **for being sealed**.

    The ordering is the whole point. A plan that asked the calendar first would tell an operator
    "the exchange was closed" about a date whose real problem is that it is inside the sealed
    window -- and on the next weekday the refusal would have to come from somewhere else. Moving
    the ``dev_end`` branch below the session branch makes this test fail and no other, which
    ``tools/mutate_daily_guards.py`` checks by running the whole file under the plant rather than
    only this test.
    """
    plan = plan_for(settings, A_SUNDAY_PAST_DEV_END)

    assert plan.outcome == "refused"
    assert "dev_end" in plan.reason


def test_a_closed_exchange_inside_dev_is_a_no_op_that_names_the_calendar(
    settings: Settings,
) -> None:
    plan = plan_for(settings, A_HOLIDAY_IN_DEV)

    assert plan.outcome == "no_session"
    assert plan.stages == ()
    assert "XNYS" in plan.reason


def test_a_session_inside_dev_plans_every_live_stage(settings: Settings) -> None:
    plan = plan_for(settings, A_SESSION_IN_DEV)

    assert plan.outcome == "work"
    assert tuple(stage.name for stage in plan.stages) == tuple(stage.name for stage in LIVE_STAGES)


def test_a_date_with_a_completed_run_is_already_done(settings: Settings) -> None:
    plan = plan_for(settings, A_SESSION_IN_DEV, completed=frozenset({A_SESSION_IN_DEV}))

    assert plan.outcome == "already_done"
    assert plan.stages == ()


# --- derived guard 1: no planned stage writes a reported ledger -------------------------------

# The two files whose line counts are reported numbers, plus the evaluators' own sink. These are
# the filenames the write-up quotes, so they are what a guard must key on.
_LEDGER_FILENAMES = ("trials.jsonl", "comparisons.jsonl", "decisions.jsonl")


def _appends_to_a_file(source: str) -> bool:
    """Whether ``source`` opens anything for appending, by AST.

    **Naming a ledger is not writing one**, and the first version of this guard got that wrong: it
    flagged ``volab.leaderboard``, whose entire design is that it "computes nothing of its own" and
    only reads the two ledgers. So the derivation is the conjunction of two facts about a package --
    it names a ledger file **and** it opens something for appending.

    Four routes count, and the first was all the first version looked for:

    - ``open(path, "a")`` or ``path.open(mode="a")``, matched on a string that is a **file mode**
      (``^[rwxabt+]+$`` containing ``a``) rather than on any string starting with "a", which flagged
      ``open("archive.csv")``;
    - a mode that is **not a literal** -- ``open(path, MODE)``. Treated as an append, fail-closed,
      because a guard that can be bypassed by naming a variable is not a guard;
    - ``os.open(..., os.O_APPEND)``, and ``logging.FileHandler``, whose default mode is ``"a"``;
    - a pandas writer with a ``mode`` keyword (``to_csv``, ``to_json``).

    **Stated limit:** this reads one file's AST. An append performed by a helper in another module,
    or through a C extension, is invisible to it, and so is a write that does not go through any of
    the four routes above. The guard it feeds is a floor on carelessness, not a proof.
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = (
            function.attr
            if isinstance(function, ast.Attribute)
            else function.id
            if isinstance(function, ast.Name)
            else None
        )
        if name == "FileHandler":
            return True
        if name == "open":
            # ``path.open(mode)`` takes the mode first; ``open(path, mode)`` takes it second.
            positional = node.args[0:] if isinstance(function, ast.Attribute) else node.args[1:]
            mode_arguments = [
                *positional,
                *(keyword.value for keyword in node.keywords if keyword.arg == "mode"),
            ]
            for argument in mode_arguments:
                if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                    return True  # a mode this cannot read is treated as an append
                if _is_append_mode(argument.value):
                    return True
            continue
        if name in {"to_csv", "to_json", "to_parquet"} and any(
            keyword.arg == "mode" for keyword in node.keywords
        ):
            return True
        if name == "open" or (isinstance(function, ast.Attribute) and function.attr == "open"):
            continue
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            if isinstance(argument, ast.Attribute) and argument.attr == "O_APPEND":
                return True
    return False


def _is_append_mode(value: str) -> bool:
    return bool(value) and set(value) <= set("rwxabt+") and "a" in value


def _ledger_writing_packages() -> frozenset[str]:
    """Every ``volab`` sub-package that names a ledger file and appends to a file.

    The **packages** are derived by reading the tree, so a fourth writer is covered without anyone
    remembering to extend a list. The three **filenames** are still written down, deliberately:
    they are the names the write-up quotes, so a fourth ledger does need a line here -- and the
    floor test below would not notice its absence. ``volab.daily`` is excluded: its docstrings
    name those files in order to say it does not write them, and a guard that fired on a comment
    about itself would be unusable.
    """
    sources: dict[str, list[str]] = {}
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        package = path.relative_to(_SOURCE_ROOT).parts[0]
        package = package.removesuffix(".py")
        if package == "daily":
            continue
        sources.setdefault(package, []).append(path.read_text(encoding="utf-8"))
    writers = {
        f"volab.{package}"
        for package, texts in sources.items()
        if any(name in text for text in texts for name in _LEDGER_FILENAMES)
        and any(_appends_to_a_file(text) for text in texts)
    }
    return frozenset(writers)


def _stages_touching_a_ledger(stages: tuple[Stage, ...]) -> tuple[str, ...]:
    """The planned stages that run a ledger-writing package, matched by prefix and not equality.

    ``volab.walkforward.__main__`` and ``volab.evaluators.sink`` are both runnable module names
    that an equality test would wave through, and ``python -m volab.walkforward.__main__`` writes
    the same ledger as ``python -m volab.walkforward``.

    **Stated limit, because a guard whose reach is not stated invites the trust this project
    withholds:** the derivation underneath is not transitive. A future package that imports
    ``append_trials`` without naming a ledger file or opening one for appending would not be in
    the writer set, and so would not be caught here. That is exactly the shape P4-06's forecast
    stage could arrive in, and closing it is P4-04's derived import check -- which asks what
    ``volab.daily`` imports rather than what a stage's name looks like.
    """
    writers = _ledger_writing_packages()
    return tuple(
        stage.module
        for stage in stages
        if any(
            stage.module == writer or stage.module.startswith(f"{writer}.") for writer in writers
        )
    )


def test_the_derived_writer_set_is_not_empty_and_holds_the_three_known_writers() -> None:
    """The guard below is vacuous if the derivation finds nothing, so pin the floor (R151).

    A floor rather than an equality: a fourth writer must not need this test edited.
    """
    writers = _ledger_writing_packages()

    assert {"volab.walkforward", "volab.compare", "volab.evaluators"} <= writers


def test_the_derived_writer_set_excludes_the_reader() -> None:
    """``volab.leaderboard`` reads both ledgers and writes neither, and is a planned stage.

    This is the case the first version of this derivation got wrong, and it is the reason the
    rule is a conjunction rather than a filename match.
    """
    assert "volab.leaderboard" not in _ledger_writing_packages()


def test_no_live_stage_runs_a_module_that_writes_a_reported_ledger() -> None:
    assert _stages_touching_a_ledger(LIVE_STAGES) == ()


def test_the_ledger_guard_can_fail() -> None:
    """The red witness: a planted stage is caught. Without this the test above proves nothing.

    The planted tuple is built from scratch rather than from ``LIVE_STAGES``, so that a defect
    *in* ``LIVE_STAGES`` fails the green test above and this one not at all. The first version
    spliced the plant into ``LIVE_STAGES`` and so failed alongside it, which
    ``tools/mutate_daily_guards.py`` reported as one mutant breaking two tests.
    """
    planted = (Stage(name="planted", module="volab.walkforward", argv=("run",)),)

    assert _stages_touching_a_ledger(planted) == ("volab.walkforward",)


def test_the_ledger_guard_catches_a_submodule_spelling() -> None:
    """``python -m volab.walkforward.__main__`` writes the same ledger as its package.

    An equality test passes this, which is what the first version of the guard did.
    """
    planted = (
        Stage(name="planted", module="volab.walkforward.__main__", argv=("run",)),
        Stage(name="also", module="volab.evaluators.sink", argv=()),
    )

    assert _stages_touching_a_ledger(planted) == (
        "volab.walkforward.__main__",
        "volab.evaluators.sink",
    )


def test_the_append_detector_can_fail() -> None:
    """And the detector underneath it: a read-only module must not read as a writer."""
    assert _appends_to_a_file('path.open("a", encoding="utf-8")')
    assert _appends_to_a_file('open(path, mode="a")')
    assert not _appends_to_a_file('path.open("r", encoding="utf-8")')
    assert not _appends_to_a_file('path.read_text(encoding="utf-8")')


# --- derived guard 2: no holdout key anywhere under volab/daily -------------------------------


def _holdout_key_names() -> frozenset[str]:
    """The two keys a sealed read needs, derived rather than typed.

    The environment variable comes from ``volab.config.HOLDOUT_UNLOCK_ENV``, which exists so that
    one definition cannot drift from the refusal that names it. The parameter name is read from
    ``store.py``'s own ``def load_panel`` by AST and **not** from ``inspect.signature`` of the
    imported function: ``conftest``'s holdout police replace that object with a
    ``(*args, **kwargs)`` wrapper for the whole session (R153), so the signature at runtime is the
    police's and not the store's. Rename either key and this guard follows; type either as a
    string literal here and it would not.
    """
    source = (_SOURCE_ROOT / "store.py").read_text(encoding="utf-8")
    parameters: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "load_panel":
            arguments = node.args
            for argument in [*arguments.args, *arguments.kwonlyargs, *arguments.posonlyargs]:
                if "holdout" in argument.arg:
                    parameters.add(argument.arg)
    assert parameters, "load_panel no longer has a holdout parameter; this guard needs rewriting"
    return frozenset(parameters) | {HOLDOUT_UNLOCK_ENV}


def _code_names_and_strings(source: str) -> tuple[frozenset[str], frozenset[str]]:
    """The identifiers the *code* of ``source`` uses, and separately its non-docstring strings.

    **Docstrings and comments are excluded deliberately, and the exclusion is pinned by its own
    witness below.** A module that explains in prose that it never unlocks the holdout would
    otherwise fail the guard for saying so, which would push the explanation out of the code -- the
    opposite of what this project wants.

    The two kinds are returned separately because the rule that applies to them differs, and
    conflating them is what the first version got wrong twice: an identifier is judged loosely
    (anything containing "holdout" or "unlock", so a renamed or imported constant is caught) and a
    string strictly (the key's own spelling, so a refusal *message* that says "the holdout is
    sealed" is not an offence).
    """
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    names: set[str] = set()
    strings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.keyword | ast.arg) and node.arg is not None:
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            strings.add(node.value)
    return frozenset(names), frozenset(strings)


def _holdout_keys_in(source: str) -> tuple[str, ...]:
    """Every holdout-ish name the **code** of ``source`` uses.

    Identifiers are judged loosely and strings strictly, and both halves exist because a reviewer
    walked through the earlier versions:

    - an **identifier, attribute, keyword argument or import alias** is flagged if its lowercase
      contains ``holdout`` or ``unlock``. A deliberate widening past the two derived keys, because
      the obvious evasion is not either key: importing ``HOLDOUT_UNLOCK_ENV`` from ``volab.config``
      and indexing ``os.environ`` with it uses the constant's *name* and never its value.
    - a **string literal that is not a docstring** is flagged if it contains one of the derived
      keys or the token ``HOLDOUT_UNLOCK``, case-sensitively. That catches
      ``"VOLAB_" + "HOLDOUT_UNLOCK"`` and ``subprocess.run(["env", "VOLAB_HOLDOUT_UNLOCK=1", …])``
      while leaving alone the refusal message this package prints, which says in English that the
      holdout is sealed. A guard that forbade *saying* so would push the explanation out of the
      code.

    **Stated limits**, because a guard whose reach is not stated invites the trust this project
    withholds. It reads this package's source only: a flag passed positionally
    (``load_panel(settings, end, True)``) names nothing, and an unlock exported by the shell that
    launched the job is in no file here. Both still need the *other* key, and ``conftest``'s
    ``_forbid_holdout_unlock`` fails any unmarked test that reads past ``dev_end`` however it got
    there -- that fixture, not this scan, is the backstop.
    """
    names, strings = _code_names_and_strings(source)
    keys = _holdout_key_names()
    flagged = {name for name in names if "holdout" in name.lower() or "unlock" in name.lower()}
    flagged |= keys & names
    tokens = (*keys, "HOLDOUT_UNLOCK")
    flagged |= {text for text in strings if any(token in text for token in tokens)}
    return tuple(sorted(flagged))


def test_no_module_under_daily_names_a_holdout_key() -> None:
    scanned = sorted(path.name for path in _DAILY_PACKAGE.rglob("*.py"))
    # The floor that stops this passing vacuously: a glob that matched nothing would otherwise
    # report an empty set of offenders and read as a clean bill of health (R159).
    assert len(scanned) >= 3, scanned

    offenders = {
        path.name: _holdout_keys_in(path.read_text(encoding="utf-8"))
        for path in sorted(_DAILY_PACKAGE.rglob("*.py"))
        if _holdout_keys_in(path.read_text(encoding="utf-8"))
    }

    assert offenders == {}


def test_the_holdout_key_guard_can_fail() -> None:
    planted = "def unlock():\n    os.environ[VOLAB_HOLDOUT_UNLOCK] = '1'\n"

    assert _holdout_keys_in(planted) == (HOLDOUT_UNLOCK_ENV,)


def test_the_holdout_key_guard_catches_the_parameter_too() -> None:
    planted = "load_panel(settings, allow_holdout=True)\n"

    assert "allow_holdout" in _holdout_keys_in(planted)


def test_the_holdout_key_guard_catches_the_variable_spelled_as_a_string() -> None:
    """The route a determined author would take: the key as a literal, not as an identifier."""
    planted = "os.environ['VOLAB_HOLDOUT_UNLOCK'] = '1'\n"

    assert _holdout_keys_in(planted) == (HOLDOUT_UNLOCK_ENV,)


def test_the_holdout_key_guard_catches_the_constant_rather_than_its_value() -> None:
    """The evasion a reviewer found: import the name the guard itself imports, and use that.

    ``HOLDOUT_UNLOCK_ENV`` is not either key; it is the name of one. The exact match misses it.
    """
    planted = "from volab.config import HOLDOUT_UNLOCK_ENV\nos.environ[HOLDOUT_UNLOCK_ENV] = '1'\n"

    assert _holdout_keys_in(planted) == ("HOLDOUT_UNLOCK_ENV",)


def test_the_holdout_key_guard_catches_a_split_or_embedded_spelling() -> None:
    assert _holdout_keys_in("key = 'VOLAB_' + 'HOLDOUT_UNLOCK'\n") == ("HOLDOUT_UNLOCK",)
    assert _holdout_keys_in("subprocess.run(['env', 'VOLAB_HOLDOUT_UNLOCK=1', 'python'])\n") == (
        "VOLAB_HOLDOUT_UNLOCK=1",
    )


def test_the_holdout_key_guard_does_not_fire_on_a_docstring_that_names_the_keys() -> None:
    """Because ``volab.daily``'s own docstrings name them in order to say it never uses them.

    This is the case that forced the AST filter: the first version of the guard was a substring
    match and failed on the module docstring explaining the guard.
    """
    planted = '"""Reading past dev_end needs allow_holdout and VOLAB_HOLDOUT_UNLOCK."""\n'

    assert _holdout_keys_in(planted) == ()


# --- derived guard 3: the clock is called in exactly one place --------------------------------

# Clock-ish names, as attributes or bare calls. ``time`` is deliberately **absent**: it would
# flag ``dt.time(9, 30)``, a constant time of day, and a guard with a false positive teaches people
# to route around it. The ``time`` *module* is covered instead, by forbidding the import outright.
_CLOCK_NAMES = frozenset(
    {"today", "now", "utcnow", "fromtimestamp", "time_ns", "perf_counter", "monotonic", "localtime"}
)
_CLOCK_MODULES = frozenset({"time", "calendar"})


def _clock_call_sites(source: str) -> frozenset[str]:
    """Where ``source`` reaches a clock, as ``<enclosing function>`` names, by AST not by text.

    Four routes, and only the first was in the version a reviewer read:

    - a **call** of a clock name (``dt.datetime.now()``, ``datetime.datetime.now()``), by attribute
      name, so it does not matter how the module was imported -- the same reason R153 made the
      holdout police match by identity rather than by the name they expected;
    - a **reference** to one without calling it (``clock = dt.datetime.now``), which is how a
      second seam would most naturally be introduced;
    - ``getattr(x, "now")`` with a literal attribute;
    - an **import of a clock module** (``time``, ``calendar``), reported against ``<module>``.

    **Stated limit:** an indirect call assembled at runtime -- a name looked up in a dict, an
    attribute built by string concatenation and fetched by a non-literal ``getattr`` -- is not
    visible to any AST scan. What this catches is a seam written in the ordinary way, which is what
    a second seam would be.
    """
    tree = ast.parse(source)
    enclosing: dict[ast.AST, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(node):
                enclosing.setdefault(child, node.name)
    sites: set[str] = set()

    def flag(node: ast.AST) -> None:
        sites.add(enclosing.get(node, "<module>"))

    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr in _CLOCK_NAMES) or (
            isinstance(node, ast.Name) and node.id in _CLOCK_NAMES
        ):
            flag(node)
        elif isinstance(node, ast.Call):
            function = node.func
            is_getattr = isinstance(function, ast.Name) and function.id == "getattr"
            if is_getattr and len(node.args) >= 2:
                attribute = node.args[1]
                if (
                    isinstance(attribute, ast.Constant)
                    and isinstance(attribute.value, str)
                    and attribute.value in _CLOCK_NAMES
                ):
                    flag(node)
        elif isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] in _CLOCK_MODULES for alias in node.names):
                flag(node)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in (
            _CLOCK_MODULES
        ):
            flag(node)
    return frozenset(sites)


def _functions_named(source: str, name: str) -> int:
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    )


def test_the_clock_is_called_in_exactly_one_function_in_the_whole_package() -> None:
    """One seam, named, so every refusal downstream is a pure function of a date.

    Asserted as an exact map rather than a count: a second call inside ``_today`` would be
    harmless, and a call anywhere else is the defect, so the thing to pin is *where*.
    """
    sources = {
        path.name: path.read_text(encoding="utf-8") for path in sorted(_DAILY_PACKAGE.rglob("*.py"))
    }
    assert len(sources) >= 3, sorted(sources)  # the floor: a glob matching nothing is not evidence

    sites = {name: _clock_call_sites(text) for name, text in sources.items()}
    assert {name: sorted(found) for name, found in sites.items() if found} == {
        "__main__.py": ["_today"]
    }
    # And exactly one function is called ``_today``, so the map above cannot be satisfied by a
    # second seam of the same name -- which is how a reviewer got past the first version.
    assert _functions_named(sources["__main__.py"], "_today") == 1


def test_the_clock_guard_can_fail() -> None:
    planted = "def plan_for(settings):\n    return dt.date.today()\n"

    assert _clock_call_sites(planted) == frozenset({"plan_for"})


def test_the_clock_guard_catches_the_indirect_spellings_a_reviewer_found() -> None:
    """A reference without a call, a literal ``getattr``, and an import of the ``time`` module."""
    assert _clock_call_sites("def f():\n    clock = dt.datetime.now\n    return clock()\n") == (
        frozenset({"f"})
    )
    assert _clock_call_sites('def f():\n    return getattr(dt.datetime, "now")()\n') == frozenset(
        {"f"}
    )
    assert _clock_call_sites("import time\n") == frozenset({"<module>"})
    assert _clock_call_sites("from time import time as t\n") == frozenset({"<module>"})


def test_the_clock_guard_does_not_fire_on_a_constant_time_of_day() -> None:
    """``dt.time(9, 30)`` is not a clock, and a guard with a false positive gets routed around."""
    assert _clock_call_sites("CLOSE = dt.time(16, 0)\n") == frozenset()


# --- the run record ---------------------------------------------------------------------------

_AT = dt.datetime(2026, 11, 4, 9, 30, tzinfo=dt.UTC)
_LATER = dt.datetime(2026, 11, 4, 9, 45, tzinfo=dt.UTC)


def test_the_start_line_exists_on_its_own_before_any_completion(
    settings: Settings, tmp_path: Path
) -> None:
    path = run_log_path(tmp_path)
    plan = plan_for(settings, A_SESSION_IN_DEV)

    run_id = record_start(path, plan, at=_AT, sha="abc1234", dirty=False)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "start"
    assert record["id"] == run_id
    assert record["as_of"] == A_SESSION_IN_DEV.isoformat()
    assert record["git_sha"] == "abc1234"
    assert record["git_dirty"] is False
    assert record["plan"] == [stage.name for stage in LIVE_STAGES]


def test_a_completion_refers_to_the_start_by_id_and_leaves_its_line_byte_identical(
    settings: Settings, tmp_path: Path
) -> None:
    path = run_log_path(tmp_path)
    plan = plan_for(settings, A_SESSION_IN_DEV)
    run_id = record_start(path, plan, at=_AT, sha="abc1234", dirty=False)
    first_line = path.read_bytes()

    record_completion(
        path,
        run_id,
        outcome="done",
        stages=(StageResult(name="ingest", exit_code=0),),
        at=_LATER,
    )

    body = path.read_bytes()
    assert body.startswith(first_line)
    lines = body.decode("utf-8").splitlines()
    assert len(lines) == 2
    completion = json.loads(lines[1])
    assert completion == {
        "kind": "complete",
        "ref": run_id,
        "outcome": "done",
        "stages": [{"name": "ingest", "exit_code": 0}],
        "ended_at": _LATER.isoformat(),
    }
    assert completed_dates(path) == frozenset({A_SESSION_IN_DEV})


def test_a_run_that_never_completed_leaves_its_date_retryable(
    settings: Settings, tmp_path: Path
) -> None:
    """A crash must not consume the day. Attempts are counted, not forbidden."""
    path = run_log_path(tmp_path)
    plan = plan_for(settings, A_SESSION_IN_DEV)
    run_id = record_start(path, plan, at=_AT, sha="abc1234", dirty=None)

    assert completed_dates(path) == frozenset()
    assert incomplete_run_ids(path) == (run_id,)
    assert plan_for(settings, A_SESSION_IN_DEV, completed=completed_dates(path)).outcome == "work"


def test_the_join_is_on_the_id_and_survives_interleaved_runs(
    settings: Settings, tmp_path: Path
) -> None:
    """Two runs overlapping in time interleave their lines; position would mis-join them."""
    path = run_log_path(tmp_path)
    first = record_start(
        path, plan_for(settings, A_SESSION_IN_DEV), at=_AT, sha="abc1234", dirty=False
    )
    other_day = dt.date(2025, 12, 29)
    second = record_start(path, plan_for(settings, other_day), at=_AT, sha="abc1234", dirty=False)

    record_completion(path, second, outcome="done", stages=(), at=_LATER)
    record_completion(path, first, outcome="done", stages=(), at=_LATER)

    assert completed_dates(path) == frozenset({A_SESSION_IN_DEV, other_day})
    assert incomplete_run_ids(path) == ()


def test_a_second_attempt_appends_and_rewrites_nothing(settings: Settings, tmp_path: Path) -> None:
    path = run_log_path(tmp_path)
    plan = plan_for(settings, A_SESSION_IN_DEV)
    first = record_start(path, plan, at=_AT, sha="abc1234", dirty=False)
    before = path.read_bytes()

    second = record_start(path, plan, at=_LATER, sha="abc1234", dirty=False)

    assert second != first
    assert path.read_bytes().startswith(before)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_a_completion_referring_to_no_start_counts_as_nothing(tmp_path: Path) -> None:
    """A line that joins to nothing is not evidence of a run, and must not invent one."""
    path = run_log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": "complete", "ref": "not-a-run"}) + "\n", encoding="utf-8")

    assert completed_dates(path) == frozenset()


# --- the CLI ----------------------------------------------------------------------------------


def test_the_cli_refuses_a_real_date_today_and_exits_one(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Against the repository's own configuration, which is the state the project is actually in.

    Every real date is past ``dev_end`` until the reveal happens and a ``live`` region exists
    (P4-05), so this is the normal outcome of the daily job today and not an edge case.
    """
    code = daily_cli.main(["plan", "--as-of", A_SESSION_PAST_DEV_END.isoformat()], root=tmp_path)

    assert code == 1
    printed = capsys.readouterr().out
    assert "refused" in printed
    assert "no key" in printed
    # Design section 4's witness A1, behavioural half: the refusal leaves **nothing** behind --
    # no audit line, no run record, no file of any kind under the root it was given.
    assert sorted(tmp_path.rglob("*")) == []


def test_the_cli_plans_a_session_inside_dev_and_exits_zero(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = daily_cli.main(["plan", "--as-of", A_SESSION_IN_DEV.isoformat()], root=tmp_path)

    assert code == 0
    printed = capsys.readouterr().out
    for stage in LIVE_STAGES:
        assert f"stage {stage.name}" in printed


def test_the_cli_exits_zero_on_a_closed_exchange(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = daily_cli.main(["plan", "--as-of", A_HOLIDAY_IN_DEV.isoformat()], root=tmp_path)

    assert code == 0
    assert "no_session" in capsys.readouterr().out


def test_the_exchange_date_is_taken_from_the_instant_and_not_from_utc() -> None:
    """Pinned at two fixed instants, so it bites at any hour the suite happens to run.

    02:00 UTC on 5 November is still 4 November in New York, and 15:00 UTC is the same day. An
    implementation that returned ``instant.date()`` -- the UTC date -- passes the second and fails
    the first. The previous version of this test compared ``_today()`` against a freshly taken New
    York date, which agreed with a UTC implementation for most of the day; a reviewer pointed out
    that it therefore bit only in the evening.
    """
    assert daily_cli._date_in_exchange_tz(dt.datetime(2026, 11, 5, 2, 0, tzinfo=dt.UTC)) == dt.date(
        2026, 11, 4
    )
    assert daily_cli._date_in_exchange_tz(
        dt.datetime(2026, 11, 5, 15, 0, tzinfo=dt.UTC)
    ) == dt.date(2026, 11, 5)


def test_the_one_clock_call_goes_through_that_helper() -> None:
    assert daily_cli._today() == daily_cli._date_in_exchange_tz(dt.datetime.now(tz=dt.UTC))


def test_the_cli_takes_its_default_date_from_the_clock_seam(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """With no ``--as-of``, the date printed is the one ``_today`` returned.

    Pins the plumbing rather than the clock: replacing ``_today()`` in ``main`` with a literal date
    would pass every other test in this file.
    """
    monkeypatch.setattr(daily_cli, "_today", lambda: A_SESSION_IN_DEV)

    code = daily_cli.main(["plan"], root=tmp_path)

    assert code == 0
    assert f"as_of {A_SESSION_IN_DEV}" in capsys.readouterr().out


def test_the_start_line_is_fsynced_and_not_merely_written(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The board claims the start line reaches the disk, so the claim gets a witness.

    Every other test here reads the file after the handle has closed, which flushes it anyway, so
    deleting the ``flush``/``fsync`` pair left all of them green -- a reviewer measured that. This
    counts the ``fsync`` calls instead, which is the only part of "on the disk" a unit test can
    observe. Durability past a power cut is not tested by anything and is not claimed.
    """
    synced: list[int] = []
    real_fsync = runlog.os.fsync
    monkeypatch.setattr(
        runlog.os,
        "fsync",
        lambda descriptor: (synced.append(descriptor), real_fsync(descriptor))[1],
    )
    path = run_log_path(tmp_path)

    run_id = record_start(path, plan_for(settings, A_SESSION_IN_DEV), at=_AT, sha="s", dirty=False)
    record_completion(path, run_id, outcome="done", stages=(), at=_LATER)

    assert len(synced) == 2


def test_a_run_recorded_as_failed_does_not_consume_its_date(
    settings: Settings, tmp_path: Path
) -> None:
    """A stage that exited non-zero has not done the day's work.

    Treating any completion line as "finished" would skip the day politely instead of skipping it
    by crashing, which is the same bricked day. The attempt is closed -- it is not in
    ``incomplete_run_ids`` -- and the date is still available.
    """
    path = run_log_path(tmp_path)
    run_id = record_start(path, plan_for(settings, A_SESSION_IN_DEV), at=_AT, sha="s", dirty=False)

    record_completion(
        path, run_id, outcome="failed", stages=(StageResult(name="ingest", exit_code=2),), at=_LATER
    )

    assert completed_dates(path) == frozenset()
    assert incomplete_run_ids(path) == ()
    assert plan_for(settings, A_SESSION_IN_DEV, completed=completed_dates(path)).outcome == "work"


def test_a_torn_final_line_does_not_make_the_log_unreadable(
    settings: Settings, tmp_path: Path
) -> None:
    """A job killed between the write and the fsync leaves a partial line.

    If reading the log then raised, one crash would stop every later run asking what had already
    completed -- the bricked day the two-line design exists to prevent. The torn bytes stay in the
    file; they simply record nothing.
    """
    path = run_log_path(tmp_path)
    run_id = record_start(path, plan_for(settings, A_SESSION_IN_DEV), at=_AT, sha="s", dirty=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "complete", "ref": "' + run_id)

    assert completed_dates(path) == frozenset()
    assert incomplete_run_ids(path) == (run_id,)


def test_a_start_line_with_an_unreadable_date_is_skipped_rather_than_raised(tmp_path: Path) -> None:
    path = run_log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"kind": "start", "id": "x", "as_of": "not-a-date"}) + "\n", encoding="utf-8"
    )

    assert completed_dates(path) == frozenset()


def test_the_cli_reports_a_recorded_completion_as_already_done(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end through the shipped verb, not through a hand-built set.

    ``test_a_date_with_a_completed_run_is_already_done`` passes a ``frozenset`` to ``plan_for``,
    which says nothing about whether the CLI ever reads the log. A reviewer found that the first
    version of ``main`` did not, so ``plan`` printed ``work`` for a date the log said was finished.
    """
    path = run_log_path(tmp_path)
    run_id = record_start(path, plan_for(settings, A_SESSION_IN_DEV), at=_AT, sha="s", dirty=False)
    record_completion(path, run_id, outcome="done", stages=(), at=_LATER)

    code = daily_cli.main(["plan", "--as-of", A_SESSION_IN_DEV.isoformat()], root=tmp_path)

    assert code == 0
    assert "already_done" in capsys.readouterr().out


def test_the_fixture_split_has_not_drifted_from_the_repositorys_own(settings: Settings) -> None:
    """The fixture hard-codes the boundary; this is what makes the comment above it true."""
    assert settings.splits.dev_end == load_settings().splits.dev_end
    assert settings.splits.holdout_start == load_settings().splits.holdout_start


def test_the_dates_this_module_asserts_about_are_what_it_says_they_are(settings: Settings) -> None:
    """The fixtures above are claims about a calendar; derive them rather than trust them."""
    from volab.sessions import get_calendar, is_session

    calendar = get_calendar(settings)
    assert is_session(calendar, A_SESSION_IN_DEV)
    assert not is_session(calendar, A_HOLIDAY_IN_DEV)
    assert not is_session(calendar, A_SUNDAY_PAST_DEV_END)
    assert A_SUNDAY_PAST_DEV_END.weekday() == 6
    assert is_session(calendar, A_SESSION_PAST_DEV_END)
