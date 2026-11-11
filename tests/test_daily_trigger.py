"""Nothing here installs the unattended trigger, and the document that ships it is true.

Phase 4's anti-goal A5 is *an unattended job installed by this work*: the brief is to build the
scheduler so it **can** run and leave it unrun, and a persistent trigger on the user's machine is
the user's to create. The trigger therefore ships as documentation only
(``docs/phase4-daily-trigger.md``), and this file holds the two things that keep that true.

**The scan.** A derived scan over every ``.py`` file under ``src/`` and ``tools/`` asserts that no
*code* names a LaunchAgents path, ``crontab``, ``launchctl``, ``plistlib`` or a ``.plist`` file.
The set of files is read from the tree, never listed, and the one function that globs, parses and
detects is the function both the green test and its red witness call -- so the witness exercises the
glob and the ``.py`` filter and not only the detector.

**The document.** ``ruff`` excludes ``docs/``, so nothing else checks that the plist in the
document is a plist, that the command it schedules is one the CLI accepts, or that the cron line and
the plist schedule the same verb. Those three are asserted here, by reading the document.

Nothing in this file executes the verb it reads out of the document. The CLI's verb list is read
from ``--help``, because once ``run`` exists, *calling* the documented verb would execute stages.
"""

from __future__ import annotations

import ast
import plistlib
import re
from pathlib import Path

import pytest
from test_daily import _docstring_ids, _folded

import volab.daily.__main__ as daily_cli

_REPO = Path(__file__).resolve().parents[1]
_SCANNED_ROOTS = (_REPO / "src", _REPO / "tools")
_DOCUMENT = _REPO / "docs" / "phase4-daily-trigger.md"

# What counts as installing, loading or preparing a scheduled job. The first three are the story's
# own list (a LaunchAgents path, ``crontab``, a ``.plist``); ``launchctl`` and ``plistlib`` go
# *beyond* it, because anti-goal A5 says "loaded", and loading a plist is ``launchctl``'s job while
# writing one without ever spelling ".plist" is ``plistlib``'s. They are matched case-insensitively
# as substrings of any identifier or any non-docstring string, so ``CronTab`` from the
# ``python-crontab`` package and ``"~/Library/LaunchAgents"`` are both caught.
_INSTALLER_TOKENS = ("LaunchAgents", "crontab", ".plist", "plistlib", "launchctl")

# The floor under each scanned root. A glob that matched nothing reports no offenders and reads as a
# clean bill of health (R159); a root that moved would silently halve the scan. These are floors and
# not equalities, so adding a module does not need this edited. At 29fc602 ``src/`` held
# 44 modules and ``tools/`` held 10.
_FLOORS = {"src": 20, "tools": 5}


# --- the scan ---------------------------------------------------------------------------------


def _installer_tokens_in(source: str) -> tuple[str, ...]:
    """Every installer token that the **code** of ``source`` uses, sorted.

    "Code" means: identifiers (names, attributes, parameters, keyword arguments, import aliases,
    imported module names, and the names of functions and classes it defines) and string literals
    that are not docstrings, with a ``"Launch" + "Agents"`` concatenation, a ``"".join(("launch",
    "ctl"))`` of literals and an f-string of literals folded into the string they make (the folding
    is ``tests/test_daily.py::_folded``, shared with the holdout scan, whose reach it states). Two
    things are excluded and each is pinned by its own test below:
    **docstrings** (a module that explains it never writes a plist must be allowed to say so) and
    **comments**, which an AST never contains.

    **A string that is not a docstring counts even when it only talks about the path** -- a refusal
    message that says "install it under ~/Library/LaunchAgents yourself" fires. That is deliberate:
    this scan cannot tell a message from a path handed to ``open``, and the cost of rewording one
    message is smaller than the cost of a guard that reads intent.

    **Stated limits.** It reads one file at a time and only the syntax: a path assembled from
    something other than literals (``"".join(parts)`` over a variable, ``str.format``, ``%``, a
    value read from a file or the environment) is invisible, and so is a write performed by
    a program the code only names indirectly. ``tests/`` is not scanned, and neither are
    ``config/`` or ``.github/``. It is a
    floor on carelessness, not a proof that nothing can install a trigger.
    """
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
    texts: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            texts.add(node.id)
        elif isinstance(node, ast.Attribute):
            texts.add(node.attr)
        elif isinstance(node, ast.arg | ast.keyword) and node.arg is not None:
            texts.add(node.arg)
        elif isinstance(node, ast.alias):
            texts.add(node.name)
            if node.asname is not None:
                texts.add(node.asname)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            texts.add(node.module)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            texts.add(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                texts.add(node.value)
        elif isinstance(node, ast.BinOp | ast.Call | ast.JoinedStr):
            folded = _folded(node)
            if folded is not None:
                texts.add(folded)
    lowered = [text.lower() for text in texts]
    return tuple(
        sorted(
            token for token in _INSTALLER_TOKENS if any(token.lower() in text for text in lowered)
        )
    )


def _python_files(root: Path) -> list[Path]:
    """Every ``.py`` file under ``root``, derived from the tree.

    Only ``.py``: ``tools/README.md`` and ``tools/e1_plant.patch`` are prose and a diff, which no
    interpreter runs, so a file that merely *mentions* an installer is not a code path. Pinned by
    ``test_a_non_python_file_is_not_scanned_though_the_same_text_in_python_is``.
    """
    return sorted(root.rglob("*.py"))


def _trigger_installers(roots: tuple[Path, ...]) -> dict[str, tuple[str, ...]]:
    """``{<root name>/<path under it>: tokens}`` for every file under ``roots`` that uses one.

    The one function that globs, parses and detects. The green test calls it on the repository's
    ``src/`` and ``tools/``; the red witness calls it on a planted tree.
    """
    offenders: dict[str, tuple[str, ...]] = {}
    for root in roots:
        for path in _python_files(root):
            found = _installer_tokens_in(path.read_text(encoding="utf-8"))
            if found:
                offenders[f"{root.name}/{path.relative_to(root).as_posix()}"] = found
    return offenders


def _roots_below_floor(roots: tuple[Path, ...], floors: dict[str, int]) -> list[str]:
    """The names of roots holding fewer ``.py`` files than their floor."""
    return [root.name for root in roots if len(_python_files(root)) < floors[root.name]]


def test_no_code_under_src_or_tools_installs_a_trigger() -> None:
    assert _roots_below_floor(_SCANNED_ROOTS, _FLOORS) == []

    assert _trigger_installers(_SCANNED_ROOTS) == {}


def _plant(root: Path, files: dict[str, str]) -> tuple[Path, Path]:
    """A ``src/`` and a ``tools/`` under ``root`` holding ``files`` (keys are ``<root>/<path>``)."""
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "tools").mkdir(exist_ok=True)
    return root / "src", root / "tools"


def test_the_trigger_install_guard_can_fail(tmp_path: Path) -> None:
    """The red witness, through the same function the green test calls and not only the detector.

    A violation planted in ``src/`` and another in ``tools/`` are both reported, under the path the
    glob derived for them, and a clean file next to them is not -- so the scan neither misses a
    root nor flags everything.
    """
    roots = _plant(
        tmp_path,
        {
            "src/volab/install.py": (
                "from pathlib import Path\n"
                "def install(text):\n"
                '    target = Path.home() / "Library" / "LaunchAgents" / "x.plist"\n'
                "    target.write_text(text)\n"
            ),
            "src/volab/clean.py": "def f():\n    return 1\n",
            "tools/schedule.py": (
                'import subprocess\nsubprocess.run(["crontab", "-l"], check=False)\n'
            ),
        },
    )

    assert _trigger_installers(roots) == {
        "src/volab/install.py": (".plist", "LaunchAgents"),
        "tools/schedule.py": ("crontab",),
    }


def test_the_floor_names_a_root_that_matched_nothing(tmp_path: Path) -> None:
    """A moved ``tools/`` must fail the floor and not read as a clean scan."""
    roots = _plant(
        tmp_path,
        {f"src/m{index}.py": "x = 1\n" for index in range(_FLOORS["src"])},
    )

    assert _roots_below_floor(roots, _FLOORS) == ["tools"]


# --- what the detector catches ----------------------------------------------------------------
# Each route is its own test, because a detector that caught one of them was all the first version
# of every guard in tests/test_daily.py was.


def test_a_launchagents_path_used_in_code_fires() -> None:
    planted = 'target = Path.home() / "Library" / "LaunchAgents"\n'

    assert _installer_tokens_in(planted) == ("LaunchAgents",)


def test_a_path_in_a_message_that_is_not_a_docstring_fires_by_design() -> None:
    planted = 'print("copy it into ~/Library/LaunchAgents yourself")\n'

    assert _installer_tokens_in(planted) == ("LaunchAgents",)


def test_shelling_out_to_crontab_fires() -> None:
    assert _installer_tokens_in('subprocess.run(["crontab", "-"], input=text)\n') == ("crontab",)
    assert _installer_tokens_in('os.system("crontab -e")\n') == ("crontab",)


def test_importing_the_python_crontab_package_fires() -> None:
    assert _installer_tokens_in("from crontab import CronTab\n") == ("crontab",)
    assert _installer_tokens_in("import crontab\n") == ("crontab",)


def test_writing_a_plist_file_fires() -> None:
    assert _installer_tokens_in('Path("job.plist").write_text(text)\n') == (".plist",)


def test_plistlib_fires_even_when_no_plist_filename_is_spelled() -> None:
    """``plistlib.dump(data, handle)`` writes a plist without the string ``.plist`` appearing."""
    assert _installer_tokens_in("import plistlib\nplistlib.dump(data, handle)\n") == ("plistlib",)


def test_launchctl_fires() -> None:
    assert _installer_tokens_in('subprocess.run(["launchctl", "bootstrap", domain, path])\n') == (
        "launchctl",
    )


def test_a_concatenation_of_literals_is_folded_before_it_is_judged() -> None:
    planted = 'target = "~/Library/Launch" + "Agents"\n'

    assert _installer_tokens_in(planted) == ("LaunchAgents",)


@pytest.mark.parametrize(
    "planted",
    [
        'tool = "".join(("launch", "ctl"))\n',
        'tool = "".join(["cron", "tab"])\n',
        'tool = "/".join(("~", "Library", "LaunchAgents"))\n',
        'tool = "launch" "ctl"\n',
        "tool = f\"{'launch'}ctl\"\n",
    ],
)
def test_a_join_or_an_fstring_of_literals_is_folded_before_it_is_judged(planted: str) -> None:
    """The eleventh guard hole: ``+`` was folded and ``str.join`` was not (found by the probe)."""
    assert _installer_tokens_in(planted) != (), planted


def test_a_function_or_class_named_for_installing_a_trigger_fires() -> None:
    assert _installer_tokens_in("def install_crontab():\n    return 1\n") == ("crontab",)
    assert _installer_tokens_in("class LaunchctlRunner:\n    pass\n") == ("launchctl",)


def test_the_match_is_case_insensitive() -> None:
    assert _installer_tokens_in('target = "~/library/launchagents"\n') == ("LaunchAgents",)


def test_a_string_that_is_a_bare_statement_but_not_a_docstring_fires() -> None:
    """Only the *first* statement of a module, class or function is a docstring."""
    planted = 'x = 1\n"~/Library/LaunchAgents"\n'

    assert _installer_tokens_in(planted) == ("LaunchAgents",)


# --- what the scan excludes, one test each ----------------------------------------------------


def test_a_module_docstring_naming_an_installer_does_not_fire() -> None:
    """Because a module must be free to say, in prose, that it never does this."""
    assert (
        _installer_tokens_in('"""Never writes ~/Library/LaunchAgents or runs crontab."""\n') == ()
    )


def test_a_function_docstring_naming_an_installer_does_not_fire() -> None:
    planted = 'def f():\n    """Does not call launchctl or write a .plist."""\n    return 1\n'

    assert _installer_tokens_in(planted) == ()


def test_a_class_docstring_naming_an_installer_does_not_fire() -> None:
    planted = 'class C:\n    """Not a plistlib wrapper."""\n    x = 1\n'

    assert _installer_tokens_in(planted) == ()


def test_a_comment_never_reaches_the_ast_so_the_scan_cannot_see_it() -> None:
    """Pins that the scan is AST-based, which is the only reason a comment is excluded.

    It cannot fail on the *scan's* exclusion logic, because there is none: a comment is not a node.
    What can fail is the premise, and that is what is asserted -- the tree of the commented source
    is the tree of the same source without it, so no scan built on ``ast`` can fire on the comment.
    The paired test below is the half that can go red: the same text in a string does fire.
    """
    comment = "# ~/Library/LaunchAgents, crontab, launchctl, a .plist, plistlib\nx = 1\n"

    assert ast.dump(ast.parse(comment)) == ast.dump(ast.parse("x = 1\n"))
    assert _installer_tokens_in(comment) == ()


def test_the_same_text_in_a_string_statement_that_is_not_a_docstring_does_fire() -> None:
    """Proves the scan does not strip ``#...`` textually: it is the AST that excludes comments."""
    planted = 'x = 1\n"# launchctl"\n'

    assert _installer_tokens_in(planted) == ("launchctl",)


def test_a_non_python_file_is_not_scanned_though_the_same_text_in_python_is(
    tmp_path: Path,
) -> None:
    """``tools/`` really holds a ``README.md`` and a ``.patch``; neither names an installer."""
    text = 'target = "~/Library/LaunchAgents/x.plist"\n'
    roots = _plant(
        tmp_path,
        {"tools/notes.md": text, "tools/change.patch": text, "tools/real.py": text},
    )

    assert _trigger_installers(roots) == {"tools/real.py": (".plist", "LaunchAgents")}


# --- the document -----------------------------------------------------------------------------


def _document() -> str:
    return _DOCUMENT.read_text(encoding="utf-8")


def _fenced(language: str) -> str:
    """The body of the one fenced block in the document tagged ``language``."""
    blocks = re.findall(rf"^```{language}\n(.*?)^```$", _document(), flags=re.MULTILINE | re.DOTALL)
    assert len(blocks) == 1, f"expected exactly one ```{language} block, found {len(blocks)}"
    return blocks[0]


def _verb_of(arguments: list[str]) -> str:
    """The word after ``volab.daily`` in a command's arguments."""
    return arguments[arguments.index("volab.daily") + 1]


def _cli_verbs(capsys: pytest.CaptureFixture[str]) -> frozenset[str]:
    """The verbs ``python -m volab.daily`` accepts, read from ``--help``; none is called."""
    with pytest.raises(SystemExit) as raised:
        daily_cli.main(["--help"])
    assert raised.value.code == 0
    match = re.search(r"\{([a-z,]+)\}", capsys.readouterr().out)
    assert match is not None, "the usage line no longer lists the verbs in braces"
    return frozenset(match.group(1).split(","))


def test_the_document_exists() -> None:
    assert _DOCUMENT.is_file(), f"{_DOCUMENT} is the story's deliverable"


def test_the_plist_in_the_document_is_a_plist_that_schedules_a_verb_the_cli_accepts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    plist = plistlib.loads(_fenced("xml").encode("utf-8"))

    arguments = plist["ProgramArguments"]
    assert arguments[0].startswith("/"), (
        "launchd has no PATH worth the name; the program is absolute"
    )
    assert "--locked" in arguments, "the project forbids a bare `uv run`"
    assert _verb_of(arguments) in _cli_verbs(capsys)


def test_the_plist_runs_from_a_checkout_and_logs_outside_it() -> None:
    """A log inside the tree would dirty it, a problem the document says it does not solve."""
    plist = plistlib.loads(_fenced("xml").encode("utf-8"))

    checkout = Path(plist["WorkingDirectory"])
    assert checkout.name == "vol-lab", "the main checkout, not a worktree that is mid-change"
    for key in ("StandardOutPath", "StandardErrorPath"):
        assert checkout not in Path(plist[key]).parents


def test_the_plist_and_the_cron_line_schedule_the_same_verb_at_the_same_time() -> None:
    plist = plistlib.loads(_fenced("xml").encode("utf-8"))
    lines = [
        line for line in _fenced("cron").splitlines() if line.strip() and not line.startswith("#")
    ]
    assert len(lines) == 1, lines
    minute, hour, day_of_month, month, day_of_week, *_ = lines[0].split()

    assert _verb_of(plist["ProgramArguments"]) == _verb_of(lines[0].replace('"', "").split())
    assert "--locked" in lines[0]
    assert (day_of_month, month) == ("*", "*")
    intervals = plist["StartCalendarInterval"]
    assert {(entry["Hour"], entry["Minute"]) for entry in intervals} == {(int(hour), int(minute))}
    assert {entry["Weekday"] for entry in intervals} == set(range(1, 6))
    assert day_of_week == "1-5"


def test_the_cron_line_has_no_percent_sign() -> None:
    """In a crontab ``%`` means newline, so one in the command would silently truncate it."""
    assert "%" not in _fenced("cron")


def test_the_document_gives_install_and_uninstall_for_both_and_says_it_installs_nothing() -> None:
    text = _document()

    for needle in ("launchctl bootstrap", "launchctl bootout", "crontab -e"):
        assert needle in text, needle
    assert "installs nothing" in text.lower()


def test_the_document_names_the_dirty_flag_problem_and_where_it_is_owned() -> None:
    text = _document()

    for needle in ("vendor_payloads", "git_dirty", "P4-03", "Found while building the first slice"):
        assert needle in text, needle
