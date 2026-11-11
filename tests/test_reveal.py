"""The reveal's machinery (HR-4): witnesses W1-W21 and W25-W26 of the reveal design.

Nothing here holds a key. No ``@pytest.mark.holdout``, no ``VOLAB_HOLDOUT_UNLOCK``, and no
``allow_holdout=True`` against ``data/``: every read that needs the carved window opened goes
through ``tests/carved.py``'s pseudo-holdout (2024-04-01..2024-04-12, far below the repository's
``dev_end``) and the stub below, **which forwards its argument**.

``lambda allow_holdout: allow_holdout`` and not ``lambda _: True``. A stub that discards the key
grants it whatever the caller passed, so a stage function that forgot to thread ``allow_holdout``
would read happily in every witness here and be refused in production. This stub simulates an
exported variable while keeping the argument real, so an unthreaded read fails the test.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import carved
import pytest
from test_import_discipline import (
    _docstring_constants,
    _first_party_imports,
    _graph,
    _module_name,
    _packages,
    _unlocks,
)

import volab
import volab.reveal as reveal
from volab import store
from volab.compare import NO_MEASUREMENT
from volab.compare.cohorts import MULTISTEP, SHOOTOUT
from volab.compare.dm import overlap_lag
from volab.compare.multiple import SIZES, McsResult, effective_observations
from volab.compare.sink import Comparison
from volab.config import Settings
from volab.errors import HoldoutViolation
from volab.features.build import CLOSE_COLUMN, FRED_SERIES, IV_SERIES, VVIX_SERIES
from volab.features.targets import HORIZONS
from volab.ingest.curate import (
    VIX_DATASET,
    _distribution_dates,
    curate_bars,
    curate_fred,
    curate_vix,
)
from volab.leaderboard import TrialLine, ranked_trials
from volab.panel import FRED_SERIES_NAMES, VIX_SERIES, load_bars, load_exogenous
from volab.reveal import ChampionStatus, Gate, gates
from volab.walkforward import LOSSES, Trial, load_targets

SRC = Path(volab.__file__).resolve().parent
UNLOCK_ENV = "VOLAB_HOLDOUT_UNLOCK"
HOLDOUT_END = carved.SPLITS.holdout_end


def hold_the_carved_window_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both keys, simulated, without holding either: see the module docstring."""
    monkeypatch.setattr(store, "_unlock_granted", lambda allow_holdout: allow_holdout)
    assert os.environ.get(UNLOCK_ENV) is None


# ---- the key is threaded: instance witnesses --------------------------------------------------

_SEVEN: dict[str, Callable[[Any, bool], object]] = {
    "curate_bars": lambda s, k: curate_bars("SPY", end=HOLDOUT_END, settings=s, allow_holdout=k),
    "curate_vix": lambda s, k: curate_vix("VIX", end=HOLDOUT_END, settings=s, allow_holdout=k),
    "curate_fred": lambda s, k: curate_fred("DGS10", end=HOLDOUT_END, settings=s, allow_holdout=k),
    "_distribution_dates": lambda s, k: _distribution_dates(
        "QQQ", end=HOLDOUT_END, settings=s, allow_holdout=k
    ),
    "load_bars": lambda s, k: load_bars(s, "SPY", end=HOLDOUT_END, allow_holdout=k),
    "load_exogenous": lambda s, k: load_exogenous(
        s, VIX_DATASET, ("VIX",), CLOSE_COLUMN, end=HOLDOUT_END, allow_holdout=k
    ),
    "load_targets": lambda s, k: load_targets(s, "SPY", 1, end=HOLDOUT_END, allow_holdout=k),
}


def _derive_curated_holdout(settings: Any) -> None:
    """Make the curated tier reach ``holdout_end`` so the three curated-tier readers have rows."""
    for symbol in settings.universe:
        curate_bars(symbol, end=HOLDOUT_END, settings=settings, allow_holdout=True)
    curate_vix("VIX", end=HOLDOUT_END, settings=settings, allow_holdout=True)
    curate_fred("DGS10", end=HOLDOUT_END, settings=settings, allow_holdout=True)


def test_each_threaded_reader_is_refused_without_the_key_and_reads_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seven functions, two arms each. The refused arm is what makes the read arm mean something:
    with the stub forwarding its argument, the only difference between them is what the function
    passed down to ``load_panel``."""
    settings = carved.carved_store(tmp_path)
    hold_the_carved_window_open(monkeypatch)
    _derive_curated_holdout(settings)

    for name, call in _SEVEN.items():
        if name == "_distribution_dates":
            # QQQ has no dividends in the carved store, so its refusal is DatasetNotFound-shaped
            # (an empty set) only when the key was honoured; without it the guard speaks first.
            with pytest.raises(HoldoutViolation):
                call(settings, False)
            assert call(settings, True) == set()
            continue
        with pytest.raises(HoldoutViolation):
            call(settings, False)
        assert call(settings, True) is not None, name
    assert os.environ.get(UNLOCK_ENV) is None


# ---- W25: only the reveal ever passes the key --------------------------------------------------

_THREADED: dict[str, tuple[str, ...]] = {
    "ingest/curate.py": ("curate_bars", "curate_vix", "curate_fred", "_distribution_dates"),
    "panel.py": ("load_bars", "load_exogenous"),
    "walkforward/__init__.py": ("load_targets",),
}
_RESEARCH_ENTRY_POINTS: tuple[str, ...] = (
    "compare/__main__.py",
    "evaluators/__main__.py",
    "walkforward/__main__.py",
)


def _mentions_key(node: ast.AST) -> bool:
    return (
        (isinstance(node, ast.Name) and node.id == "allow_holdout")
        or (isinstance(node, ast.Attribute) and node.attr == "allow_holdout")
        or (isinstance(node, ast.keyword) and node.arg == "allow_holdout")
        or (isinstance(node, ast.arg) and node.arg == "allow_holdout")
    )


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(found) == 1, name
    return found[0]


def _callee(call: ast.Call) -> str:
    target = call.func
    return target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")


def _threading_problems(source: str, functions: tuple[str, ...]) -> list[str]:
    """What is wrong with how ``functions`` in ``source`` take and forward the key."""
    tree = ast.parse(source)
    problems: list[str] = []
    for name in functions:
        function = _function(tree, name)
        names = [a.arg for a in function.args.kwonlyargs]
        declared = dict(zip(names, function.args.kw_defaults, strict=True))
        default = declared.get("allow_holdout")
        if not (isinstance(default, ast.Constant) and default.value is False):
            problems.append(f"{name}: allow_holdout is not keyword-only with default False")
        forwarded = 0
        for call in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
            if _callee(call) not in {"load_panel", "_distribution_dates"}:
                continue
            forwarded += 1
            values = [k.value for k in call.keywords if k.arg == "allow_holdout"]
            if not (len(values) == 1 and isinstance(values[0], ast.Name)):
                problems.append(f"{name}: {_callee(call)} does not forward allow_holdout by name")
            elif values[0].id != "allow_holdout":
                problems.append(f"{name}: forwards {values[0].id}, not allow_holdout")
        if forwarded != 1 + (name == "curate_bars"):
            problems.append(f"{name}: {forwarded} forwarding call(s)")
    return problems


@pytest.mark.parametrize("relative", sorted(_THREADED))
def test_the_seven_threaded_functions_take_the_key_and_forward_it_unchanged(
    relative: str,
) -> None:
    source = (SRC / relative).read_text(encoding="utf-8")
    assert _threading_problems(source, _THREADED[relative]) == []


@pytest.mark.parametrize(
    "mutation",
    [
        ("settings=resolved, allow_holdout=allow_holdout", "settings=resolved"),
        (
            "        allow_holdout=allow_holdout,\n        settings=resolved,\n    )\n    _reject",
            "        settings=resolved,\n    )\n    _reject",
        ),
    ],
    ids=["distribution forward dropped", "bars forward dropped"],
)
def test_the_threading_scan_is_seen_failing(mutation: tuple[str, str]) -> None:
    source = (SRC / "ingest/curate.py").read_text(encoding="utf-8")
    old, new = mutation
    assert old in source
    broken = source.replace(old, new, 1)
    assert _threading_problems(broken, _THREADED["ingest/curate.py"])


def _key_problems(path: Path, source: str) -> list[str]:
    relative = path.relative_to(SRC).as_posix()
    if relative == "store.py" or relative.startswith("reveal/"):
        return []
    problems = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.keyword)
            and node.arg == "allow_holdout"
            and not isinstance(node.value, ast.Name)
        ):
            problems.append(f"{relative}: passes allow_holdout= a non-name")
    return problems


def test_only_the_reveal_ever_passes_the_key() -> None:
    """Outside ``reveal/`` and ``store.py`` the key is only ever *forwarded*, by name; a constant
    ``True`` appears nowhere. The three research entry points and ``load_for`` do not mention it."""
    for path in sorted(SRC.rglob("*.py")):
        assert _key_problems(path, path.read_text(encoding="utf-8")) == [], path
    for relative in _RESEARCH_ENTRY_POINTS:
        tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
        assert not [n for n in ast.walk(tree) if _mentions_key(n)], relative
    walkforward = ast.parse((SRC / "walkforward/__init__.py").read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(_function(walkforward, "load_for")) if _mentions_key(n)]


def test_a_key_planted_in_load_for_is_seen_failing() -> None:
    source = (SRC / "walkforward/__init__.py").read_text(encoding="utf-8")
    planted = source.replace(
        "targets = load_targets(settings, symbol, horizon, end=end)",
        "targets = load_targets(settings, symbol, horizon, end=end, allow_holdout=True)",
        1,
    )
    assert planted != source
    walkforward = ast.parse(planted)
    assert [n for n in ast.walk(_function(walkforward, "load_for")) if _mentions_key(n)]
    problems = _key_problems(SRC / "walkforward/__init__.py", planted)
    assert problems


# ---- W13: the tally counts looks and attempts separately --------------------------------------

A = "a" * 32
B = "b" * 32
C = "c" * 32


def _log(*records: Any) -> str:
    return "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in records)


def test_the_tally_counts_looks_and_attempts_separately(tmp_path: Path) -> None:
    settings = carved.settings_at(tmp_path)
    dates = {
        "dev_end": settings.splits.dev_end.isoformat(),
        "holdout_start": settings.splits.holdout_start.isoformat(),
        "holdout_end": settings.splits.holdout_end.isoformat(),
    }
    path = settings.storage_root / store.AUDIT_FILENAME
    path.parent.mkdir(parents=True)
    path.write_text(
        _log(
            "not json",
            "[1, 2]",
            {"schema": 1, "kind": "look_open", "look_id": "x"},  # unmatched
            {"schema": 1, "kind": "look_open", "look_id": "y"},
            {"schema": 1, "kind": "look_close", "look_id": "y"},
            {"schema": 7, "kind": "look_open", "look_id": "z"},  # unknown schema
            {"schema": 1, "kind": "reveal_open", "reveal_id": A, **dates},
            {"schema": 1, "kind": "reveal_open", "reveal_id": B, **dates},
            {"schema": 1, "kind": "reveal_complete", "reveal_id": B},
            {"schema": 1, "kind": "reveal_complete", "reveal_id": C},  # never opened
            {"schema": 1, "kind": "reveal_open", "reveal_id": "short", **dates},
        ),
        encoding="utf-8",
    )

    tally = store.audit_tally(settings)

    assert (tally.looks, tally.looks_closed) == (2, 1)
    assert tally.attempts == (A, B)
    assert tally.completed == frozenset({B})


def test_the_tally_of_a_store_with_no_log_is_empty(tmp_path: Path) -> None:
    tally = store.audit_tally(carved.settings_at(tmp_path))

    assert (tally.looks, tally.looks_closed, tally.attempts, tally.completed) == (
        0,
        0,
        (),
        frozenset(),
    )
    assert not (tmp_path / "data" / store.AUDIT_FILENAME).exists()


# =============================================================================================
# From here down: the reveal package itself (volab.reveal). Pure unless a test says otherwise.
# =============================================================================================

REVEAL_DIR = SRC.joinpath("reveal")
REPO = SRC.parents[1]
REAL = Settings(
    universe=("SPY", "QQQ", "IWM"),
    exogenous=(),
    calendar="XNYS",
    splits=dataclasses.replace(
        carved.SPLITS,
        dev_end=dt.date(2025, 12, 31),
        holdout_start=dt.date(2026, 1, 2),
        holdout_end=dt.date(2026, 10, 30),
    ),
    storage_root=Path("."),
)
BEFORE = dt.date(2026, 10, 30)
AFTER = dt.date(2026, 10, 31)


def _facts(**over: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "today": AFTER,
        "repo_root": Path("."),
        "git_sha": "a" * 40,
        "git_dirty": False,
        "preregistration_sha256": reveal.REGISTERED_SHA256,
        "preregistration_blob": reveal.REGISTERED_BLOB,
        "already_revealed": False,
    }
    facts.update(over)
    return facts


# ---- W1: every gate is a row, and the order is fixed ----------------------------------------

_ROWS = [
    ("all clear", {}, []),
    ("dirty", {"git_dirty": True}, [("dirty_tree", 3)]),
    ("sha unknown", {"git_sha": "unknown", "git_dirty": None}, [("git_unknown", 4)]),
    ("flag unknown, sha known", {"git_dirty": None}, [("git_unknown", 4)]),
    ("absent", {"preregistration_sha256": None}, [("preregistration_absent", 5)]),
    ("content", {"preregistration_sha256": "0" * 64}, [("preregistration_content", 6)]),
    ("blob", {"preregistration_blob": "1" * 40}, [("preregistration_blob", 7)]),
    ("blob unnameable", {"preregistration_blob": None}, [("preregistration_blob", 7)]),
    ("window open: the last day itself", {"today": BEFORE}, [("window_open", 8)]),
    ("window open: well before", {"today": dt.date(2026, 10, 1)}, [("window_open", 8)]),
    ("window closed: the day after", {"today": AFTER}, []),
    ("already revealed", {"already_revealed": True}, [("already_revealed", 9)]),
]


@pytest.mark.parametrize(("label", "over", "expected"), _ROWS, ids=[r[0] for r in _ROWS])
def test_every_gate_is_a_table_and_the_order_is_fixed(
    label: str, over: dict[str, Any], expected: list[tuple[str, int]]
) -> None:
    found = gates(REAL, **_facts(**over))

    assert [(g.name, g.code) for g in found] == expected


def test_when_every_gate_fails_they_come_back_in_the_one_fixed_order() -> None:
    everything = _facts(
        git_dirty=True,
        preregistration_sha256="0" * 64,
        preregistration_blob=None,
        today=BEFORE,
        already_revealed=True,
    )

    names = [g.name for g in gates(REAL, **everything)]

    assert names == [
        "dirty_tree",
        "preregistration_content",
        "preregistration_blob",
        "window_open",
        "already_revealed",
    ]
    assert gates(
        REAL,
        **everything,
    ) == gates(REAL, **everything)
    # A dirty tree is reported before a hash mismatch: the hash of a dirty file is not the hash
    # of a commit, so an operator holding both should fix the tree first.
    assert names.index("dirty_tree") < names.index("preregistration_content")
    both_absent = gates(
        REAL, **_facts(git_sha="unknown", git_dirty=None, preregistration_sha256=None)
    )
    assert [g.name for g in both_absent][:2] == ["git_unknown", "preregistration_absent"]


def test_the_gate_codes_are_distinct_and_never_reserved() -> None:
    samples = [
        *gates(REAL, **_facts(git_dirty=True)),
        *gates(REAL, **_facts(git_sha="unknown", git_dirty=None)),
        *gates(REAL, **_facts(preregistration_sha256=None)),
        *gates(REAL, **_facts(preregistration_sha256="0" * 64)),
        *gates(REAL, **_facts(preregistration_blob=None)),
        *gates(REAL, **_facts(today=BEFORE)),
        *gates(REAL, **_facts(already_revealed=True)),
        reveal.keys_not_held(),
        reveal.window_not_ingested({"SPY": BEFORE}, AFTER),
        reveal.canary_disagrees(["x"]),
        reveal.truncation_disagreed(["x"]),
    ]
    ambiguous = reveal.champion_from(_comparison(("a", "b")), _trials({"a": 1.0, "b": 1.0}))
    samples.append(reveal.champion_ambiguous(ambiguous))

    codes = [g.code for g in samples]
    assert len(set(codes)) == len(codes) == 12
    assert sorted(codes) == list(range(3, 15))
    assert not {0, 1, reveal.USAGE} & set(codes)
    assert all(isinstance(g, Gate) and g.message for g in samples)
    # The seal's state is said once, by the record (seal_state), never by a gate's own words.
    assert not [g.name for g in samples if "seal" in g.message.lower()]


# ---- W2: the registered refusal table is what the code produces -----------------------------

_REGISTERED_TABLE = {
    # horizon: (origins, n_eff, 10% refused, 25% refused, DM significance withheld)
    1: (208, 208.0, False, False, False),
    5: (204, 40.8, False, False, False),
    22: (187, 8.5, True, False, True),
    66: (143, 2.2, True, True, True),
}


@pytest.mark.parametrize("horizon", sorted(_REGISTERED_TABLE))
def test_the_registered_refusal_table_is_what_the_code_produces(horizon: int) -> None:
    origins, n_eff, refused_10, refused_25, withheld = _REGISTERED_TABLE[horizon]
    assert reveal.REGISTERED_ORIGINS[horizon] == origins

    q = overlap_lag(horizon, reveal.STRIDE)
    got = effective_observations(origins, q)

    assert got == pytest.approx(n_eff, abs=0.05)
    assert (got < 2 / 0.10, got < 2 / 0.25) == (refused_10, refused_25)
    assert tuple(SIZES) == (0.10, 0.25)
    assert (got < 2 / min(SIZES)) is withheld
    comparison = dataclasses.replace(_comparison(("a", "b")), n_eff=got)
    assert comparison.claims_withheld is withheld


# ---- W3, W26: the champion rule ---------------------------------------------------------------


def _mcs(members: dict[float, tuple[str, ...]], refusals: dict[float, str]) -> McsResult:
    return McsResult(
        n=100,
        q=0,
        n_eff=100.0,
        block=None,
        reps=1,
        seed=1,
        bootstrap="stationary",
        pvalues=None,
        members=members,
        refusals=refusals,
    )


def _comparison(
    members: tuple[str, ...],
    *,
    unclaimed: tuple[str, ...] = (),
    refusal: str | None = None,
    everyone: tuple[str, ...] | None = None,
) -> Comparison:
    names = everyone or (*members, "outsider")
    return Comparison(
        symbol="SPY",
        horizon=1,
        loss="qlike",
        cohort="phase2-shootout",
        stride=1,
        origins=100,
        contestants=names,
        q=0,
        bartlett_lag=2,
        n_eff=100.0,
        block=None,
        reps=1,
        seed=1,
        bootstrap="stationary",
        method="R",
        pairs=(),
        dm_tests=0,
        mcs=_mcs(
            {} if refusal else {0.10: members, 0.25: members}, {0.10: refusal} if refusal else {}
        ),
        spa=(),
        monte_carlo_error={name: None for name in unclaimed},
        monte_carlo_source=NO_MEASUREMENT if unclaimed else None,
        unclaimed=unclaimed,
        refusal=None,
    )


def _trials(values: dict[str, float]) -> list[Trial]:
    return [
        Trial(
            model=name,
            symbol="SPY",
            horizon=1,
            loss="qlike",
            origins=100,
            value=value,
            dropped=0,
            requested=100,
            skipped=0,
            every=1,
            first_origin=dt.date(2026, 1, 2),
            last_origin=dt.date(2026, 10, 29),
            cohort="phase2-shootout",
        )
        for name, value in values.items()
    ]


def test_the_champion_rule_is_the_sentence_in_section_3() -> None:
    one = reveal.champion_from(_comparison(("only",)), _trials({"only": -8.0, "outsider": -9.0}))
    assert (one.status, one.name, one.qlike) == (ChampionStatus.NAMED, "only", -8.0)

    several = reveal.champion_from(
        _comparison(("a", "b", "c")), _trials({"a": -7.0, "b": -8.5, "c": -8.0, "outsider": -9.9})
    )
    # The outsider has the lowest QLIKE of all and is not in the set: membership comes first.
    assert (several.status, several.name) == (ChampionStatus.NAMED, "b")
    assert several.members == ("a", "b", "c")

    thin = reveal.champion_from(
        _comparison((), refusal="thin sample, sentence from the confidence set"), _trials({})
    )
    assert (thin.status, thin.name, thin.members) == (ChampionStatus.NONE, None, ())
    assert thin.refusal == "thin sample, sentence from the confidence set"

    tie = reveal.champion_from(_comparison(("a", "b")), _trials({"a": -8.0, "b": -8.0}))
    assert tie.status is ChampionStatus.AMBIGUOUS
    assert (tie.name, tie.candidates) == (None, ("a", "b"))
    assert "no way to break" in tie.statement
    assert all(c.rule == reveal.THE_RULE for c in (one, several, thin, tie))


def test_a_contestant_whose_significance_is_withheld_can_be_champion_r216() -> None:
    """R216, pinned from both sides: the withholding does not unseat the lowest QLIKE, and the
    sentence that says so travels with the name."""
    withheld = reveal.champion_from(
        _comparison(("egarch11_skewt", "har_levels"), unclaimed=("egarch11_skewt",)),
        _trials({"egarch11_skewt": -8.9, "har_levels": -8.5}),
    )

    assert withheld.status is ChampionStatus.NAMED
    assert withheld.name == "egarch11_skewt"
    assert withheld.unclaimed == ("egarch11_skewt",)
    assert reveal.WITHHELD_SENTENCE in withheld.statement
    assert withheld.statement.startswith("egarch11_skewt is the champion")

    claimed = reveal.champion_from(
        _comparison(("egarch11_skewt", "har_levels"), unclaimed=("egarch11_skewt",)),
        _trials({"egarch11_skewt": -8.0, "har_levels": -8.5}),
    )
    # The same set, but the lowest QLIKE is a claimed contestant: no withholding sentence at all.
    assert claimed.name == "har_levels"
    assert reveal.WITHHELD_SENTENCE not in claimed.statement
    # The withholding is not an ambiguity: the only thing that produces AMBIGUOUS is a tie.
    assert withheld.candidates == ()


def test_a_member_with_no_qlike_trial_is_an_error_not_a_guess() -> None:
    with pytest.raises(ValueError, match="no qlike trial"):
        reveal.champion_from(_comparison(("a", "b")), _trials({"a": -8.0}))


def _lines(values: dict[str, float]) -> list[TrialLine]:
    return [
        TrialLine("t", "s", False, "phase2-shootout", "SPY", 1, "qlike", n, v, 100, 0, 0)
        for n, v in values.items()
    ]


def test_the_champion_is_rank_one_among_the_members() -> None:
    values = {"a": -7.0, "b": -8.5, "c": -8.0, "outsider": -9.9}
    champion = reveal.champion_from(_comparison(("a", "b", "c")), _trials(values))

    ranked = [(rank, t.model) for rank, t in ranked_trials(_lines(values))]
    first_member = next(model for _, model in ranked if model in champion.members)

    assert champion.name == first_member == "b"

    tied = {"a": -8.0, "b": -8.0, "outsider": -9.0}
    ambiguous = reveal.champion_from(_comparison(("a", "b")), _trials(tied))
    members_ranked = [(r, t.model) for r, t in ranked_trials(_lines(tied)) if t.model in ("a", "b")]
    # Both candidates share one rank, and neither is named.
    assert len({rank for rank, _ in members_ranked}) == 1
    assert ambiguous.status is ChampionStatus.AMBIGUOUS
    assert set(ambiguous.candidates) == {model for _, model in members_ranked}


# ---- W4: the registered literals ---------------------------------------------------------------


def test_the_registered_literals_are_what_the_document_and_r130_say() -> None:
    document = REPO.joinpath(reveal.PREREGISTRATION)
    assert hashlib.sha256(document.read_bytes()).hexdigest() == reveal.REGISTERED_SHA256
    decisions = REPO.joinpath("docs", "superpowers", "phase2-completion-decisions.md")
    text = decisions.read_text(encoding="utf-8")
    r130 = text[text.index("## R130") :]
    r130 = r130[: r130.index("\n## ", 5)] if "\n## " in r130[5:] else r130
    for literal in (
        reveal.REGISTERED_SHA256,
        reveal.REGISTERED_BLOB,
        reveal.REGISTERING_COMMIT,
        str(reveal.REGISTERED_TRIALS),
        str(reveal.REGISTERED_COMPARISONS),
    ):
        assert literal in r130, literal
    match = re.search(
        r"`data/trials\.jsonl` line count at registration: \*\*(\d+)\.\*\* "
        r"`data/comparisons\.jsonl`: \*\*(\d+)\.\*\*",
        " ".join(document.read_text(encoding="utf-8").split()),
    )
    assert match is not None
    assert (int(match.group(1)), int(match.group(2))) == (
        reveal.REGISTERED_TRIALS,
        reveal.REGISTERED_COMPARISONS,
    )
    assert reveal.THE_RULE in " ".join(document.read_text(encoding="utf-8").split()).replace(
        "**", ""
    )


def test_the_feature_set_name_is_the_features_clis() -> None:
    from volab.features.__main__ import FEATURE_SET

    assert reveal.FEATURE_SET == FEATURE_SET
    assert reveal.CHAMPION == ("SPY", 1, "qlike", 0.10)
    assert reveal.STRIDE == 1
    assert reveal.BENCHMARKS == ()


def test_the_registered_runs_have_the_registered_footprint() -> None:
    """The real arm of W9: 150 trial lines and 24 comparison records, derived from the cohorts."""
    horizons_h1 = SHOOTOUT.horizons(HORIZONS)
    assert [(c.name, h) for c, h in reveal.RUNS] == [
        ("phase2-shootout", (1,)),
        ("phase2-shootout-multistep", (5, 22, 66)),
    ]
    assert horizons_h1 == (1,)
    symbols = len(REAL.universe)
    trials = sum(
        len(cohort.contestants) * symbols * len(h) * len(LOSSES) for cohort, h in reveal.RUNS
    )
    comparisons = sum(symbols * len(h) * len(LOSSES) for _, h in reveal.RUNS)
    assert (trials, comparisons) == (150, 24)
    assert len(MULTISTEP.contestants) == len(SHOOTOUT.contestants) - 1


# ---- W5, W6: the plan -------------------------------------------------------------------------


@pytest.mark.parametrize("settings", [REAL, carved.settings_at(Path("."))], ids=["real", "carved"])
def test_every_sealed_read_in_the_plan_is_bounded_at_holdout_end(settings: Settings) -> None:
    reads = reveal.plan(settings)

    sealed = [r for r in reads if r.sealed]
    canary = [r for r in reads if not r.sealed]
    assert sealed and canary
    assert all(r.end == settings.splits.holdout_end for r in sealed)
    assert all(r.end == settings.splits.dev_end for r in canary)
    assert len(canary) == 20
    assert {r.stage for r in canary} == {"canary-before", "canary-after"}


@pytest.mark.parametrize(
    "settings",
    [REAL, dataclasses.replace(REAL, universe=("SPY", "QQQ"))],
    ids=["three symbols", "two symbols"],
)
def test_the_plan_is_the_formula(settings: Settings) -> None:
    s, v, f, h = len(settings.universe), len(VIX_SERIES), len(FRED_SERIES_NAMES), len(HORIZONS)

    assert reveal.expected_looks(settings) == 4 * s + 2 * (v + f) + s * h
    assert (len(IV_SERIES), VVIX_SERIES, len(FRED_SERIES)) == (3, "VVIX", 3)
    if s == 3:
        assert reveal.expected_looks(settings) == 38


def test_the_plan_names_the_stages_in_order_and_the_gate_falls_at_look_16() -> None:
    sealed = [r for r in reveal.plan(REAL) if r.sealed]

    assert [r.stage for r in sealed] == ["A"] * 13 + ["B"] * 10 + ["C"] * 12 + ["D"] * 3
    # Stage A's 13 plus stage B's three bars reads: the sixteenth look is the last bars read.
    assert [r.dataset for r in sealed[13:16]] == ["bars_daily"] * 3


# ---- W8 (pure half): the canary ----------------------------------------------------------------


def test_the_canary_catches_a_changed_dev_frame() -> None:
    import pandas as pd

    frame = pd.DataFrame({"a": [1.0, 2.0], "b": ["x", "y"]})
    other = pd.DataFrame({"a": [1.0, 2.5], "b": ["x", "y"]})
    before = reveal.Snapshot({("bars_daily", "SPY"): frame, ("fred_daily", "DGS10"): frame})
    same = reveal.Snapshot({("bars_daily", "SPY"): frame.copy(), ("fred_daily", "DGS10"): frame})
    changed = reveal.Snapshot({("bars_daily", "SPY"): other, ("fred_daily", "DGS10"): frame})

    assert reveal.compare_snapshots(before, same) == ()
    differences = reveal.compare_snapshots(before, changed)
    assert len(differences) == 1
    assert differences[0].startswith("bars_daily/SPY")


# ---- the seal's state is a function of the record ---------------------------------------------


def test_the_seal_state_is_a_function_of_the_tally_and_the_commit() -> None:
    open_only = store.AuditTally(0, 0, (A,), frozenset())
    looked = store.AuditTally(16, 16, (A,), frozenset())
    both = store.AuditTally(3, 3, (A,), frozenset({A}))
    nothing = store.AuditTally(0, 0, (), frozenset())

    assert reveal.seal_state(nothing, committed=False) is reveal.SealState.UNTOUCHED
    assert reveal.seal_state(open_only, committed=False) is reveal.SealState.COUNTED
    assert reveal.seal_state(looked, committed=False) is reveal.SealState.COUNTED_WITH_LOOKS
    assert reveal.seal_state(both, committed=False) is reveal.SealState.READ_UNCOMMITTED
    assert reveal.seal_state(both, committed=True) is reveal.SealState.LIFTED
    markers = {
        reveal.SealState.UNTOUCHED: "Nothing was written",
        reveal.SealState.COUNTED: "nothing was read",
        reveal.SealState.COUNTED_WITH_LOOKS: "holdout data has been seen",
        reveal.SealState.READ_UNCOMMITTED: "HAS BEEN READ",
        reveal.SealState.LIFTED: "LIFTED",
    }
    assert set(markers) == set(reveal.SealState) == set(reveal.SEAL_SENTENCES)
    for state, sentence in reveal.SEAL_SENTENCES.items():
        # The five worlds never borrow each other's wording.
        assert markers[state] in sentence
        assert not [m for other, m in markers.items() if other is not state and m in sentence]
    assert "committed" in reveal.SEAL_SENTENCES[reveal.SealState.READ_UNCOMMITTED]


def test_a_counted_attempt_that_took_looks_says_so_and_names_how_many() -> None:
    """R220: ``reveal_open`` with look lines and no completion is not "nothing was read"."""
    looked = store.AuditTally(16, 15, (A,), frozenset())
    none = store.AuditTally(0, 0, (A,), frozenset())

    said = reveal.seal_sentence(looked, committed=False)
    assert "16 sealed looks" in said
    assert "not a first look" in said
    assert "nothing was read" not in said
    assert "INTACT" in said  # intact at HEAD, and counted
    assert "7 sealed looks" in reveal.seal_sentence(
        store.AuditTally(7, 7, (A,), frozenset()), committed=False
    )
    assert "nothing was read" in reveal.seal_sentence(none, committed=False)
    assert "sealed looks" not in reveal.seal_sentence(none, committed=False)


def test_the_fingerprint_is_json_and_taken_before_anything_is_appended() -> None:
    fp = reveal.fingerprint(
        REAL,
        sha="a" * 40,
        dirty=False,
        sha256=reveal.REGISTERED_SHA256,
        blob=reveal.REGISTERED_BLOB,
        trials_at_read=420,
        comparisons_at_read=52,
        all_ledgers_at_read=431,
    )
    record = json.loads(json.dumps(fp.record()))

    assert record["trials_registered"] == 414 and record["trials_delta"] == 6
    # The superset sits beside the registered pair and never replaces it (HR-9).
    assert record["all_ledgers_at_read"] == 431 and record["all_ledgers_delta"] == 17
    assert reveal.SUPERSET_REASON in record["all_ledgers_note"]
    assert "R139" in record["all_ledgers_note"] and "superset" in record["all_ledgers_note"]
    assert record["comparisons_delta"] == 0
    assert record["planned_looks"] == 38
    assert record["stride"] == 1 and record["benchmarks"] == []
    assert record["champion"] is None
    assert record["holdout_end"] == "2026-10-30"
    assert record["losses"] == ["qlike", "mse"]


# ---- shape witnesses over src/volab/reveal ----------------------------------------------------


def _reveal_files() -> list[Path]:
    files = sorted(REVEAL_DIR.glob("*.py"))
    assert {f.name for f in files} == {"__init__.py", "__main__.py"}
    return files


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _strings(tree: ast.AST) -> list[str]:
    """Every string constant that is not a docstring (prose about a ban is not the thing banned)."""
    exempt = _docstring_constants(tree)
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n not in exempt
    ]


def test_the_reveal_claims_only_what_section_2_registers() -> None:
    calls = [
        n
        for f in _reveal_files()
        for n in ast.walk(_tree(f))
        if isinstance(n, ast.Call) and _callee(n) == "analyse"
    ]
    assert len(calls) == 1
    value = next(k.value for k in calls[0].keywords if k.arg == "benchmarks")
    assert isinstance(value, ast.Name) and value.id == "BENCHMARKS"
    assert reveal.BENCHMARKS == ()
    for f in _reveal_files():
        for node in ast.walk(_tree(f)):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("volab.compare"):
                assert not {a.name for a in node.names} & {
                    "superior_predictive_ability",
                    "SpaResult",
                    "BENCHMARKS",
                }


_NUMERIC = {"numpy", "scipy", "arch"}
_BANNED_CALLS = {"sum", "mean", "std", "var", "median", "average", "prod", "cumsum"}
_IMPORT_ALLOW = {
    "volab.compare": {"analyse", "describe"},
    "volab.compare.cohorts": {"MULTISTEP", "SHOOTOUT", "monte_carlo_error"},
    "volab.compare.series": {"loss_series"},
    "volab.compare.sink": {"Comparison", "JsonlSink", "Sink"},
    "volab.walkforward": {
        "LOSSES",
        "Evaluation",
        "Trial",
        "load_targets",
        "run_configuration",
    },
    "volab.counts": {"agent_ledgers_line", "count_all"},
    "volab.leaderboard": {"ranked_trials", "read_comparisons", "read_trials"},
}


def _arithmetic(source: str) -> list[str]:
    """What computes a number: numeric imports, arithmetic operators, and the aggregate calls."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f"imports {a.name}" for a in node.names if a.name.split(".")[0] in _NUMERIC]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in _NUMERIC:
            found.append(f"imports from {node.module}")
        elif isinstance(node, ast.BinOp) and isinstance(
            node.op, ast.Div | ast.FloorDiv | ast.Pow | ast.Mult | ast.Mod
        ):
            found.append(f"operator {type(node.op).__name__}")
        elif isinstance(node, ast.Call) and _callee(node) in _BANNED_CALLS:
            found.append(f"calls {_callee(node)}")
        elif isinstance(node, ast.AugAssign):
            found.append("augmented assignment")
    return found


def test_the_reveal_computes_no_statistic_of_its_own() -> None:
    for f in _reveal_files():
        assert _arithmetic(f.read_text(encoding="utf-8")) == [], f.name
        for node in ast.walk(_tree(f)):
            if isinstance(node, ast.ImportFrom) and node.module in _IMPORT_ALLOW:
                assert {a.name for a in node.names} <= _IMPORT_ALLOW[node.module], (
                    f.name,
                    node.module,
                )
    # Ordering stored values is not computing a statistic: min is how the champion is found.
    assert "min(" in REVEAL_DIR.joinpath("__init__.py").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "planted",
    [
        "x = a / b\n",
        "x = a ** 2\n",
        "import numpy as np\n",
        "x = sum(values)\n",
        "x = frame.mean()\n",
        "total += 1\n",
    ],
)
def test_the_arithmetic_scan_is_seen_failing(planted: str) -> None:
    assert _arithmetic(planted)
    assert _arithmetic("x = min(values)\ny = sorted(values)\nz = len(values) - 1\n") == []


_WORDS = ("REFUSED", "no significance", "n_eff", "MCS")


def test_no_refusal_sentence_is_worded_twice() -> None:
    for f in _reveal_files():
        for text in _strings(_tree(f)):
            assert not [w for w in _WORDS if w in text], (f.name, text)
    assert _strings(ast.parse("x = 'MCS 10%: REFUSED'\n")) == ["MCS 10%: REFUSED"]
    assert _strings(ast.parse('"""MCS REFUSED"""\n')) == []


# ---- W17: the key never reaches the environment ---------------------------------------------


def _environment_assignments(source: str) -> list[str]:
    """Every way ``source`` puts something in the process environment or names the unlock."""
    tree = ast.parse(source)
    found = list(_unlocks(source))
    found = [f for f in found if not f.startswith("passes") and not f.startswith("names allow")]
    found = [f for f in found if "allow_holdout" not in f]
    for node in ast.walk(tree):
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AugAssign | ast.AnnAssign)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Subscript) and "environ" in ast.unparse(target.value):
                found.append("assigns into environ")
        if isinstance(node, ast.Call):
            callee = ast.unparse(node.func)
            if callee.split(".")[-1] in {"putenv", "setenv", "unsetenv"}:
                found.append(f"calls {callee}")
            if "environ" in callee and callee.split(".")[-1] in {"setdefault", "update", "pop"}:
                found.append(f"calls {callee}")
    return found


_RED_ARMS = {
    "subscript assignment": 'import os\nos.environ["VOLAB_HOLDOUT_UNLOCK"] = "1"\n',
    "setdefault": 'import os\nKEY = "x"\nos.environ.setdefault(KEY, "1")\n',
    "putenv": 'import os\nos.putenv("VOLAB_HOLDOUT_UNLOCK", "1")\n',
    "the name as a constant": 'KEY = "VOLAB_HOLDOUT_UNLOCK"\n',
}
_GREEN_ARMS = {
    "docstring": '"""Never sets VOLAB_HOLDOUT_UNLOCK."""\n',
    "function docstring": 'def f() -> None:\n    """Never sets VOLAB_HOLDOUT_UNLOCK."""\n',
    "comment": "# VOLAB_HOLDOUT_UNLOCK is exported by a human\nx = 1\n",
    "the argument": "load_panel('x', end=e, allow_holdout=True)\n",
}


@pytest.mark.parametrize("arm", sorted(_RED_ARMS))
def test_the_environment_scan_is_seen_failing_on_each_red_arm(arm: str) -> None:
    assert _environment_assignments(_RED_ARMS[arm]), arm


@pytest.mark.parametrize("arm", sorted(_GREEN_ARMS))
def test_the_environment_scan_passes_prose_and_the_argument(arm: str) -> None:
    assert _environment_assignments(_GREEN_ARMS[arm]) == [], arm


_STAGE_FUNCTIONS = {"derive_curated", "read_bars", "derive_and_read"}


def test_the_reveal_never_puts_the_key_in_the_environment() -> None:
    for f in _reveal_files():
        assert _environment_assignments(f.read_text(encoding="utf-8")) == [], f.name
        names = {n.id for n in ast.walk(_tree(f)) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(_tree(f)) if isinstance(n, ast.Attribute)}
        assert not {"environ", "getenv", "putenv"} & names, f.name
    # The argument is permitted here and only here, but by call-site set, not by count: every
    # allow_holdout keyword is the constant True, inside exactly the named stage functions.
    holders: set[str] = set()
    for f in _reveal_files():
        tree = _tree(f)
        for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            for node in ast.walk(function):
                if isinstance(node, ast.keyword) and node.arg == "allow_holdout":
                    assert isinstance(node.value, ast.Constant) and node.value.value is True
                    holders.add(function.name)
        assert not [
            n for n in ast.walk(tree) if isinstance(n, ast.arg) and n.arg == "allow_holdout"
        ]
    assert holders == _STAGE_FUNCTIONS


def test_the_reveal_does_not_reach_the_decision_evaluators() -> None:
    graph, packages = _graph(), _packages()
    reached: set[str] = set()
    for f in _reveal_files():
        queue = [_module_name(f)]
        while queue:
            name = queue.pop()
            if name in reached or name not in graph:
                continue
            reached.add(name)
            queue += sorted(_first_party_imports(graph[name], packages.get(name, "volab")))
    assert reached
    assert not [m for m in reached if m.startswith("volab.evaluators")]
    assert not [m for m in reached if m.startswith("volab.daily")]


def test_the_reveal_names_no_second_cutoff() -> None:
    allowed = {"settings.splits.holdout_end", "settings.splits.dev_end", "end"}
    for f in _reveal_files():
        tree = _tree(f)
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "end":
                assert ast.unparse(node.value) in allowed, (f.name, ast.unparse(node.value))
            if isinstance(node, ast.Call) and ast.unparse(node.func) in {"dt.date", "date"}:
                raise AssertionError(f"{f.name} builds a date literal")
        for text in _strings(tree):
            assert not re.search(r"\b20\d\d-\d\d-\d\d\b", text), (f.name, text)


def test_the_reveal_opens_no_path_and_names_no_log() -> None:
    for f in _reveal_files():
        tree = _tree(f)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                assert _callee(node) not in {"open", "write_text", "write_bytes", "unlink"}, f.name
            if isinstance(node, ast.Name | ast.Attribute):
                name = node.id if isinstance(node, ast.Name) else node.attr
                assert name != "AUDIT_FILENAME", f.name
        assert "holdout_access.jsonl" not in "".join(_strings(tree)).replace(
            "data/holdout_access.jsonl", ""
        )


def test_the_import_ratchet_is_untouched() -> None:
    import test_import_discipline as ratchet

    assert {"store.py"} == ratchet.ALLOWED
    assert not [p for p in ratchet._sealed_modules() if "reveal" in p.as_posix()]
    assert "reveal" not in ratchet.SEALED_PACKAGES
    assert not [s for s in ratchet.SEALED if "reveal" in s]


# ---- HR-9: SEAL_SENTENCES is a template, so only seal_sentence may touch it (R221) -----


def _seal_sentences_uses(sources: dict[Path, str]) -> list[str]:
    """Every ``path:line`` that names ``SEAL_SENTENCES`` in code, except inside ``seal_sentence``.

    One entry holds a ``{looks}`` placeholder, so anything printing the dict emits it literally.
    The definition itself (a store) and the one function that fills the placeholder are exempt;
    prose in a docstring or comment is not a use.
    """
    found: list[str] = []
    for path, source in sorted(sources.items()):
        tree = ast.parse(source)
        exempt = {
            id(inner)
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "seal_sentence"
            for inner in ast.walk(node)
        }
        for node in ast.walk(tree):
            if id(node) in exempt:
                continue
            named = (
                (isinstance(node, ast.Name) and node.id == "SEAL_SENTENCES")
                or (isinstance(node, ast.Attribute) and node.attr == "SEAL_SENTENCES")
                or (
                    isinstance(node, ast.ImportFrom)
                    and any(a.name == "SEAL_SENTENCES" for a in node.names)
                )
            )
            if named and not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)):
                found.append(f"{path.as_posix()}:{node.lineno}")
    return found


def _volab_sources() -> dict[Path, str]:
    root = Path(volab.__file__).parent
    return {p.relative_to(root): p.read_text(encoding="utf-8") for p in root.rglob("*.py")}


def test_nothing_under_src_references_seal_sentences_outside_seal_sentence() -> None:
    assert _seal_sentences_uses(_volab_sources()) == []


def test_the_seal_sentences_scan_sees_each_spelling_and_exempts_only_its_function() -> None:
    sources = {
        Path("definition.py"): "SEAL_SENTENCES = {}\n",
        Path("annotated.py"): "SEAL_SENTENCES: dict[str, str] = {}\n",
        Path("filler.py"): "def seal_sentence(t):\n    return SEAL_SENTENCES[t].format(looks=1)\n",
        Path("prose.py"): '"""Mentions SEAL_SENTENCES in prose only."""\n# SEAL_SENTENCES\n',
        Path("name.py"): "print(SEAL_SENTENCES)\n",
        Path("attr.py"): "import volab.reveal as r\nprint(r.SEAL_SENTENCES)\n",
        Path("imported.py"): "from volab.reveal import SEAL_SENTENCES\n",
        Path("other.py"): "def seal_sentence_two(t):\n    return SEAL_SENTENCES[t]\n",
    }
    assert _seal_sentences_uses(sources) == [
        "attr.py:2",
        "imported.py:1",
        "name.py:1",
        "other.py:2",
    ]
