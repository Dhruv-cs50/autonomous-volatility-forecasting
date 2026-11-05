"""The forecast log: one line per day per configuration, append-only, each carrying a fingerprint.

Every test that writes writes under ``tmp_path``. The repository's own ``data/forecasts.jsonl``
must not exist as a consequence of running this file, and one test pins that.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import functools
import hashlib
import json
import math
import os
import pickle
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

import volab.daily as daily_package
import volab.daily.__main__ as daily_cli
import volab.daily.forecast as forecast
import volab.walkforward as walkforward
from volab.config import Settings, load_settings
from volab.daily import LIVE_STAGES, Stage, plan_for, with_instant
from volab.daily.champion import (
    PSEUDO_KIND,
    NoChampionError,
    PseudoChampion,
    champion_model,
    definition_sources,
    pseudo_champion,
)
from volab.daily.forecast import (
    FORECAST_LOG,
    Fingerprint,
    ForecastLine,
    ForecastLogError,
    append_forecasts,
    definition_hash,
    forecast_log_path,
    pseudo_fingerprint,
    take_fingerprint,
)
from volab.daily.runlog import completed_dates, run_log_path
from volab.errors import DatasetNotFound, ModelSkip
from volab.features.targets import HORIZONS, build_targets
from volab.models.base import Domain, Forecast
from volab.panel import truncate
from volab.sessions import get_calendar

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AT = dt.datetime(2026, 11, 4, 21, 0, tzinfo=dt.UTC)
_DAY = dt.date(2026, 11, 4)

_FINGERPRINT = Fingerprint(
    model="har_semilog",
    kind="pseudo",
    git_sha="a" * 40,
    git_dirty=False,
    definition_hash="b" * 64,
)


def _line(
    *, symbol: str = "SPY", horizon: int = 5, day: dt.date = _DAY, value: float = 1.5e-4
) -> ForecastLine:
    return ForecastLine(
        as_of=day,
        symbol=symbol,
        horizon=horizon,
        value=value,
        observations=900,
        champion=_FINGERPRINT,
    )


def _read(root: Path) -> list[dict[str, object]]:
    text = forecast_log_path(root).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


# --- where it lives ---------------------------------------------------------------------------


def test_the_log_lives_under_whatever_root_it_is_handed(tmp_path: Path) -> None:
    assert forecast_log_path(tmp_path) == tmp_path / "data" / "forecasts.jsonl"
    assert Path("data") / "forecasts.jsonl" == FORECAST_LOG


def test_the_log_is_gitignored_and_no_exception_was_added_for_it() -> None:
    ignore = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "data/**" in ignore.splitlines()
    assert "forecasts" not in ignore


# --- one line per day per configuration -------------------------------------------------------


def test_n_configurations_write_exactly_n_lines_and_the_writer_says_so(tmp_path: Path) -> None:
    lines = [_line(horizon=1), _line(horizon=5), _line(symbol="QQQ", horizon=5)]

    written = append_forecasts(lines, root=tmp_path, at=_AT)

    assert written == 3
    assert len(_read(tmp_path)) == 3


def test_a_line_carries_exactly_these_fields(tmp_path: Path) -> None:
    append_forecasts([_line()], root=tmp_path, at=_AT)

    assert _read(tmp_path) == [
        {
            "at": "2026-11-04T21:00:00+00:00",
            "as_of": "2026-11-04",
            "symbol": "SPY",
            "horizon": 5,
            "value": 1.5e-4,
            "observations": 900,
            "champion": {
                "model": "har_semilog",
                "kind": "pseudo",
                "git_sha": "a" * 40,
                "git_dirty": False,
                "definition_hash": "b" * 64,
            },
        }
    ]


def test_a_second_batch_appends_and_leaves_the_first_byte_identical(tmp_path: Path) -> None:
    append_forecasts([_line()], root=tmp_path, at=_AT)
    before = forecast_log_path(tmp_path).read_bytes()

    append_forecasts([_line(day=dt.date(2026, 11, 5))], root=tmp_path, at=_AT)

    after = forecast_log_path(tmp_path).read_bytes()
    assert after.startswith(before)
    assert len(after.splitlines()) == 2


def test_the_same_configuration_twice_in_one_batch_is_refused_and_writes_nothing(
    tmp_path: Path,
) -> None:
    with pytest.raises(ForecastLogError, match="twice in this batch"):
        append_forecasts([_line(), _line(value=2e-4)], root=tmp_path, at=_AT)

    assert not forecast_log_path(tmp_path).exists()


def test_a_day_and_configuration_already_in_the_log_is_refused_and_the_file_is_untouched(
    tmp_path: Path,
) -> None:
    append_forecasts([_line()], root=tmp_path, at=_AT)
    before = forecast_log_path(tmp_path).read_bytes()

    with pytest.raises(ForecastLogError, match="already in the log"):
        append_forecasts([_line(horizon=22), _line(value=9e-4)], root=tmp_path, at=_AT)

    assert forecast_log_path(tmp_path).read_bytes() == before


def test_the_same_configuration_on_another_day_is_not_a_duplicate(tmp_path: Path) -> None:
    append_forecasts([_line()], root=tmp_path, at=_AT)

    assert append_forecasts([_line(day=dt.date(2026, 11, 5))], root=tmp_path, at=_AT) == 1


def test_a_torn_final_line_does_not_make_the_log_unwritable(tmp_path: Path) -> None:
    path = forecast_log_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"as_of": "2026-11-0', encoding="utf-8")

    assert append_forecasts([_line()], root=tmp_path, at=_AT) == 1


def test_a_non_finite_forecast_is_refused_rather_than_written_as_one() -> None:
    with pytest.raises(ValueError, match="finite"):
        _line(value=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        _line(value=float("inf"))


def test_a_champion_kind_other_than_the_two_is_refused() -> None:
    with pytest.raises(ValueError, match="kind"):
        dataclasses.replace(_FINGERPRINT, kind="winner")


def test_a_fingerprint_without_a_hash_is_refused() -> None:
    with pytest.raises(ValueError, match="definition_hash"):
        dataclasses.replace(_FINGERPRINT, definition_hash="")


# --- it writes nothing else -------------------------------------------------------------------


def test_the_writer_creates_no_file_but_its_own(tmp_path: Path) -> None:
    append_forecasts([_line()], root=tmp_path, at=_AT)

    assert sorted(
        p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()
    ) == ["data/forecasts.jsonl"]


def test_the_module_names_none_of_the_three_ledgers_in_code() -> None:
    """Docstrings may discuss them (the guard in ``test_daily`` tolerates that); code may not."""
    tree = ast.parse(Path(forecast.__file__).read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]

    for ledger in ("trials", "comparisons", "decisions"):
        assert not [s for s in strings if ledger in s]


def test_the_module_docstring_says_no_count_from_the_file_is_a_reported_number() -> None:
    assert forecast.__doc__ is not None
    assert "No count from this file is a reported number" in forecast.__doc__


# --- the clock --------------------------------------------------------------------------------


def test_the_timestamp_is_the_one_injected(tmp_path: Path) -> None:
    other = dt.datetime(2020, 1, 2, 3, 4, 5, tzinfo=dt.UTC)

    append_forecasts([_line()], root=tmp_path, at=other)

    assert _read(tmp_path)[0]["at"] == other.isoformat()


# --- the fingerprint --------------------------------------------------------------------------


def _source(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_the_hash_is_stable_and_is_a_sha256_hex_digest(tmp_path: Path) -> None:
    source = _source(tmp_path, "m.py", "x = 1\n")

    first = definition_hash({"model": "m"}, [source], root=tmp_path)

    assert first == definition_hash({"model": "m"}, [source], root=tmp_path)
    assert len(first) == 64
    int(first, 16)


def test_editing_a_source_file_changes_the_hash_which_is_what_a_dirty_tree_needs(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path, "m.py", "x = 1\n")
    before = definition_hash({"model": "m"}, [source], root=tmp_path)

    source.write_text("x = 2\n", encoding="utf-8")

    assert definition_hash({"model": "m"}, [source], root=tmp_path) != before


def test_changing_the_configuration_changes_the_hash(tmp_path: Path) -> None:
    source = _source(tmp_path, "m.py", "x = 1\n")

    assert definition_hash({"lags": 5}, [source], root=tmp_path) != definition_hash(
        {"lags": 6}, [source], root=tmp_path
    )


def test_the_hash_does_not_depend_on_key_order_or_source_order(tmp_path: Path) -> None:
    a = _source(tmp_path, "a.py", "a\n")
    b = _source(tmp_path, "b.py", "b\n")

    assert definition_hash({"x": 1, "y": 2}, [a, b], root=tmp_path) == definition_hash(
        {"y": 2, "x": 1}, [b, a], root=tmp_path
    )


def test_the_hash_cannot_confuse_a_moved_byte_between_two_files(tmp_path: Path) -> None:
    """Concatenating contents would give ``ab`` + ``c`` the same digest as ``a`` + ``bc``."""
    one = tmp_path / "one"
    two = tmp_path / "two"
    first = definition_hash({}, [_source(one, "a.py", "ab"), _source(one, "b.py", "c")], root=one)
    second = definition_hash({}, [_source(two, "a.py", "a"), _source(two, "b.py", "bc")], root=two)

    assert first != second


def test_a_missing_source_file_raises_rather_than_hashing_its_absence(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        definition_hash({}, [tmp_path / "gone.py"], root=tmp_path)


def test_a_source_outside_the_root_is_refused(tmp_path: Path) -> None:
    outside = _source(tmp_path / "elsewhere", "m.py", "x\n")

    with pytest.raises(ValueError, match="outside the root"):
        definition_hash({}, [outside], root=tmp_path / "repo")


def test_a_configuration_that_is_not_json_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        definition_hash({"when": dt.date(2026, 1, 1)}, [], root=tmp_path)


def test_the_fingerprint_asks_git_about_the_repository_root_and_records_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[Path | None] = []

    def fake(scope: Path | None = None) -> tuple[str, bool | None]:
        asked.append(scope)
        return "c" * 40, True

    monkeypatch.setattr(forecast, "git_state", fake)
    source = _source(tmp_path, "m.py", "x\n")

    taken = take_fingerprint(
        model="m", kind="pseudo", configuration={}, sources=[source], repo_root=tmp_path
    )

    assert asked == [tmp_path]
    assert (taken.git_sha, taken.git_dirty) == ("c" * 40, True)
    assert taken.definition_hash == definition_hash({}, [source], root=tmp_path)
    assert (taken.model, taken.kind) == ("m", "pseudo")


def test_unknown_git_state_is_recorded_as_unknown_and_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(forecast, "git_state", lambda scope=None: ("unknown", None))

    taken = take_fingerprint(
        model="m", kind="pseudo", configuration={}, sources=[], repo_root=tmp_path
    )
    append_forecasts([dataclasses.replace(_line(), champion=taken)], root=tmp_path, at=_AT)

    champion = _read(tmp_path)[0]["champion"]
    assert isinstance(champion, dict)
    assert (champion["git_sha"], champion["git_dirty"]) == ("unknown", None)


# --- the stage, and the refusal until there is a champion -------------------------------------


def test_the_forecast_stage_is_planned_last_and_runs_this_module() -> None:
    assert LIVE_STAGES[-1].name == "forecast"
    assert LIVE_STAGES[-1].module == "volab.daily.forecast"
    assert [stage.name for stage in LIVE_STAGES].count("forecast") == 1


# --- the pseudo-champion: derived from the leaderboard's own reading of the ledger ------------


def _trial(
    model: str,
    value: float | None,
    *,
    symbol: str = "SPY",
    horizon: int = 1,
    loss: str = "qlike",
    at: str = "2026-10-28T08:00:00+00:00",
    sha: str = "f" * 40,
    cohort: str = "phase2-shootout",
) -> dict[str, object]:
    return {
        "at": at,
        "git_sha": sha,
        "git_dirty": False,
        "cohort": cohort,
        "model": model,
        "symbol": symbol,
        "horizon": horizon,
        "loss": loss,
        "origins": 245,
        "value": value,
    }


def _ledger(root: Path, rows: list[dict[str, object]]) -> None:
    path = root / "data" / "trials.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _covering_ledger(root: Path) -> None:
    """Every configured symbol at every horizon, so ``main`` has nothing uncovered to refuse."""
    rows = [
        _trial(model, value, symbol=symbol, horizon=horizon)
        for symbol in load_settings().universe
        for horizon in HORIZONS
        for model, value in (("har_levels", -8.0), ("random_walk", -7.0))
    ]
    _ledger(root, rows)


def test_the_champion_is_the_lowest_qlike_of_the_run(tmp_path: Path) -> None:
    _ledger(tmp_path, [_trial("a", -7.0), _trial("b", -8.5), _trial("c", -8.0)])

    champion = pseudo_champion(tmp_path, "SPY", 1)

    assert (champion.model, champion.value, champion.loss) == ("b", -8.5, "qlike")
    assert (champion.symbol, champion.horizon) == ("SPY", 1)


def test_the_champion_is_ranked_on_qlike_and_not_on_a_lower_looking_mse(tmp_path: Path) -> None:
    rows = [_trial("a", -7.0), _trial("b", -8.5), _trial("a", 0.001, loss="mse")]
    rows.append(_trial("b", 0.009, loss="mse"))
    _ledger(tmp_path, rows)

    assert pseudo_champion(tmp_path, "SPY", 1).model == "b"


def test_only_the_latest_run_is_ranked_and_never_against_an_older_one(tmp_path: Path) -> None:
    old = "2026-10-23T04:00:00+00:00"
    _ledger(
        tmp_path,
        [
            _trial("old_best", -9.9, at=old, sha="1" * 40, cohort="phase2-baseline"),
            _trial("old_other", -7.0, at=old, sha="1" * 40, cohort="phase2-baseline"),
            _trial("new_best", -8.0),
            _trial("new_other", -7.0),
        ],
    )

    champion = pseudo_champion(tmp_path, "SPY", 1)

    assert champion.model == "new_best"
    assert (champion.run_at, champion.cohort) == ("2026-10-28T08:00:00+00:00", "phase2-shootout")


def test_two_runs_at_the_same_instant_are_refused_rather_than_one_picked(tmp_path: Path) -> None:
    _ledger(tmp_path, [_trial("a", -8.0), _trial("b", -7.0, cohort="phase2-shootout-multistep")])

    with pytest.raises(NoChampionError, match="two runs share the latest instant"):
        pseudo_champion(tmp_path, "SPY", 1)


def test_a_tie_for_first_is_refused_with_a_reason_and_names_the_tied_models(
    tmp_path: Path,
) -> None:
    _ledger(tmp_path, [_trial("a", -8.0), _trial("b", -8.0), _trial("c", -7.0)])

    with pytest.raises(NoChampionError, match=r"tie for first.*\['a', 'b'\]"):
        pseudo_champion(tmp_path, "SPY", 1)


def test_a_missing_ledger_is_refused_as_empty_and_creates_nothing(tmp_path: Path) -> None:
    with pytest.raises(NoChampionError, match="the trial ledger is empty"):
        pseudo_champion(tmp_path, "SPY", 1)

    assert list(tmp_path.rglob("*")) == []


def test_a_ledger_with_no_lines_is_refused_as_empty(tmp_path: Path) -> None:
    _ledger(tmp_path, [])

    with pytest.raises(NoChampionError, match="the trial ledger is empty"):
        pseudo_champion(tmp_path, "SPY", 1)


@pytest.mark.parametrize(
    ("symbol", "horizon"),
    [("QQQ", 1), ("SPY", 5)],
    ids=["a symbol the ledger does not hold", "a horizon the ledger does not hold"],
)
def test_a_configuration_the_ledger_does_not_cover_is_refused(
    tmp_path: Path, symbol: str, horizon: int
) -> None:
    _ledger(tmp_path, [_trial("a", -8.0), _trial("b", -7.0)])

    with pytest.raises(NoChampionError, match="the ledger does not cover"):
        pseudo_champion(tmp_path, symbol, horizon)


def test_a_ledger_covering_the_pair_only_under_mse_does_not_cover_it(tmp_path: Path) -> None:
    _ledger(tmp_path, [_trial("a", 0.1, loss="mse")])

    with pytest.raises(NoChampionError, match="no qlike trial"):
        pseudo_champion(tmp_path, "SPY", 1)


def test_a_run_with_no_defined_loss_has_no_champion(tmp_path: Path) -> None:
    _ledger(tmp_path, [_trial("a", None), _trial("b", None)])

    with pytest.raises(NoChampionError, match="no model has a defined qlike"):
        pseudo_champion(tmp_path, "SPY", 1)


def test_the_champion_does_not_come_from_a_literal() -> None:
    tree = ast.parse(Path(forecast.__file__).with_name("champion.py").read_text(encoding="utf-8"))
    models = {"har_levels", "har_semilog", "random_walk", "garch11_skewt", "gjr11_skewt"}
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}

    assert not literals & models


def test_the_committed_ledger_yields_the_model_with_the_lowest_stored_qlike() -> None:
    """Read-only: the repository's own trial ledger, ranked independently of the code under test."""
    rows = [
        json.loads(line)
        for line in (_REPO_ROOT / "data" / "trials.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    latest = max(r["at"] for r in rows if r["symbol"] == "SPY" and r["horizon"] == 1)
    run = [
        r
        for r in rows
        if (r["symbol"], r["horizon"], r["loss"], r["at"]) == ("SPY", 1, "qlike", latest)
    ]
    best = min(r["value"] for r in run)
    expected = [r["model"] for r in run if r["value"] == best]
    assert len(expected) == 1

    assert pseudo_champion(_REPO_ROOT, "SPY", 1).model == expected[0]


# --- the label ------------------------------------------------------------------------------


def test_every_champion_the_derivation_yields_is_labelled_pseudo(tmp_path: Path) -> None:
    _covering_ledger(tmp_path)

    for symbol in load_settings().universe:
        for horizon in HORIZONS:
            champion = pseudo_champion(tmp_path, symbol, horizon)
            taken = pseudo_fingerprint(champion, repo_root=_REPO_ROOT)

            assert taken.kind == PSEUDO_KIND == "pseudo"
            assert taken.record()["kind"] == "pseudo"


def _docstring_ids(tree: ast.AST) -> set[int]:
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def _writes_registered(source: str, *, module: str) -> list[str]:
    """How ``source`` could produce the kind ``registered``: a code literal, or indexing ``_KINDS``.

    A docstring may say the word. The one code literal allowed is the tuple that *defines* the
    vocabulary, ``_KINDS`` in ``forecast``; indexing that tuple would reach the same string
    without spelling it, so any subscript of ``_KINDS`` is a hit wherever it appears.
    """
    tree = ast.parse(source)
    skipped = _docstring_ids(tree)
    if module == "forecast":
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_KINDS" for t in node.targets
            ):
                skipped |= {id(inner) for inner in ast.walk(node.value)}
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and node.value == "registered"
            and id(node) not in skipped
        ):
            found.append("a literal")
        if isinstance(node, ast.Subscript) and "_KINDS" in ast.dump(node.value):
            found.append("a subscript of _KINDS")
    return found


def _daily_sources() -> dict[str, str]:
    root = Path(forecast.__file__).parent
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(root.glob("*.py"))}


def test_no_code_under_daily_can_write_the_registered_kind() -> None:
    sources = _daily_sources()

    assert {"forecast", "champion", "runner", "runlog"} <= sources.keys()
    for name, source in sources.items():
        assert _writes_registered(source, module=name) == [], name


@pytest.mark.parametrize(
    "planted",
    [
        'kind = "registered"\n',
        'def f():\n    return Fingerprint(kind="registered")\n',
        "k = _KINDS[1]\n",
    ],
    ids=["a literal", "a keyword argument", "indexing the vocabulary"],
)
def test_the_registered_scan_bites_on_a_planted_writer(planted: str) -> None:
    assert _writes_registered(planted, module="champion") != []


def test_the_registered_scan_lets_the_vocabulary_definition_and_prose_through() -> None:
    clean = '"""the registered champion"""\n_KINDS = ("pseudo", "registered")\n'

    assert _writes_registered(clean, module="forecast") == []
    assert _writes_registered(clean, module="champion") != []


# --- what the definition hash covers --------------------------------------------------------


def test_the_definition_sources_are_derived_from_the_tree_and_all_exist() -> None:
    sources = definition_sources(_REPO_ROOT)
    relative = {p.relative_to(_REPO_ROOT).as_posix() for p in sources}

    assert "config/base.yaml" in relative
    assert "src/volab/models/garch.py" in relative
    assert "src/volab/walkforward/__init__.py" in relative
    assert all(p.is_file() for p in sources)
    assert not [p for p in relative if "__pycache__" in p]


def test_the_definition_sources_follow_the_root_it_is_handed(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "base.yaml").write_text("a: 1\n", encoding="utf-8")
    models = tmp_path / "src" / "volab" / "models"
    models.mkdir(parents=True)
    (models / "m.py").write_text("x = 1\n", encoding="utf-8")
    (models / "notes.txt").write_text("not source\n", encoding="utf-8")

    found = {p.relative_to(tmp_path).as_posix() for p in definition_sources(tmp_path)}

    assert found == {"config/base.yaml", "src/volab/models/m.py"}


def test_editing_a_model_file_changes_the_champions_hash_and_so_does_its_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(forecast, "git_state", lambda scope=None: ("c" * 40, False))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "base.yaml").write_text("a: 1\n", encoding="utf-8")
    models = tmp_path / "src" / "volab" / "models"
    models.mkdir(parents=True)
    (models / "m.py").write_text("x = 1\n", encoding="utf-8")
    _ledger(tmp_path, [_trial("a", -8.0), _trial("b", -7.0)])
    champion = pseudo_champion(tmp_path, "SPY", 1)

    first = pseudo_fingerprint(champion, repo_root=tmp_path).definition_hash
    (models / "m.py").write_text("x = 2\n", encoding="utf-8")
    edited = pseudo_fingerprint(champion, repo_root=tmp_path).definition_hash
    other_run = dataclasses.replace(champion, run_at="2026-11-01T00:00:00+00:00")
    rerun = pseudo_fingerprint(other_run, repo_root=tmp_path).definition_hash

    assert len({first, edited, rerun}) == 3


def test_the_fingerprint_names_the_model_and_the_repositorys_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(forecast, "git_state", lambda scope=None: ("d" * 40, True))
    _ledger(tmp_path, [_trial("a", -8.0), _trial("b", -7.0)])

    taken = pseudo_fingerprint(pseudo_champion(tmp_path, "SPY", 1), repo_root=_REPO_ROOT)

    assert (taken.model, taken.git_sha, taken.git_dirty) == ("a", "d" * 40, True)


# --- the date reaches the stage through the plan --------------------------------------------

_SESSION = dt.date(2025, 12, 15)


def _forecast_stage(plan: daily_package.Plan) -> Stage:
    (stage,) = [s for s in plan.stages if s.name == "forecast"]
    return stage


def test_the_planned_forecast_stage_carries_the_plans_own_date() -> None:
    plan = plan_for(load_settings(), _SESSION)

    assert plan.outcome == "work"
    assert _forecast_stage(plan).argv == ("--as-of", "2025-12-15")
    other = plan_for(load_settings(), dt.date(2025, 12, 16))
    assert _forecast_stage(other).argv == ("--as-of", "2025-12-16")


def test_a_stage_that_does_not_need_the_date_is_not_handed_one() -> None:
    plan = plan_for(load_settings(), _SESSION)
    template = {stage.name: stage for stage in LIVE_STAGES}

    for stage in plan.stages:
        if stage.name != "forecast":
            assert stage.argv == template[stage.name].argv


def test_live_stages_stays_the_undated_template_and_names_match_the_plan() -> None:
    assert _forecast_stage(plan_for(load_settings(), _SESSION)).argv != ()
    assert next(s for s in LIVE_STAGES if s.name == "forecast").argv == ()
    assert [s.name for s in plan_for(load_settings(), _SESSION).stages] == [
        s.name for s in LIVE_STAGES
    ]


def test_a_planned_stage_has_its_date_exactly_once_so_it_is_not_dated_again() -> None:
    plan = plan_for(load_settings(), _SESSION)

    assert all(not stage.needs_as_of for stage in plan.stages)
    assert sum(stage.argv.count("--as-of") for stage in plan.stages) == 1


def test_the_plan_dates_whatever_live_stages_is_at_the_time_of_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = Stage(name="probe", module="probe_argv", argv=("x",), needs_as_of=True)
    monkeypatch.setattr(daily_package, "LIVE_STAGES", (probe,))

    (stage,) = plan_for(load_settings(), _SESSION).stages

    assert stage.argv == ("x", "--as-of", "2025-12-15")


# --- the stage: --as-of is required, and the refusal is the narrowed one ---------------------


# --- the instant reaches the stage the way the date does: in argv, from the one seam -----------


def test_the_forecast_stage_declares_it_needs_the_instant_and_the_template_carries_none() -> None:
    template = {stage.name: stage for stage in LIVE_STAGES}

    assert template["forecast"].needs_at
    assert "--at" not in template["forecast"].argv
    assert [name for name, stage in template.items() if stage.needs_at] == ["forecast"]


def test_a_plan_holds_no_instant_so_it_leaves_the_stage_waiting_for_one() -> None:
    stage = _forecast_stage(plan_for(load_settings(), _SESSION))

    assert stage.needs_at
    assert "--at" not in stage.argv
    assert not stage.needs_as_of


def test_with_instant_appends_it_once_and_clears_the_flag() -> None:
    wanting = Stage(name="f", module="m", argv=("--as-of", "2023-06-30"), needs_at=True)

    stamped = with_instant(wanting, _AT)

    assert stamped.argv == ("--as-of", "2023-06-30", "--at", _AT.isoformat())
    assert not stamped.needs_at
    assert with_instant(stamped, _AT) == stamped, "a stamped stage is not stamped a second time"


def test_with_instant_leaves_a_stage_that_does_not_need_it_alone() -> None:
    plain = Stage(name="p", module="m", argv=("x",))

    assert with_instant(plain, _AT) is plain


def test_with_instant_refuses_an_instant_with_no_offset() -> None:
    wanting = Stage(name="f", module="m", argv=(), needs_at=True)

    with pytest.raises(ValueError, match="must carry a UTC offset"):
        with_instant(wanting, dt.datetime(2026, 11, 4, 21, 0))


# --- fitting the champion: the store seams are replaced and every read is recorded -------------


@dataclasses.dataclass(frozen=True)
class _Read:
    kind: str
    symbol: str
    end: dt.date


@functools.cache
def _synthetic_store() -> tuple[pd.DataFrame, dict[int, pd.DataFrame], tuple[dt.date, ...]]:
    """SPY's bars and every exogenous series from 2021 to mid-2023, and the targets built from it.

    The shared synthetic panel, lengthened: its own seven months leave h=66 short of the 250
    training rows the walk-forward asks for. Built once; nothing here mutates it, and
    ``truncate`` hands back copies. It is in memory, so no test below reads a store.
    """
    import test_truncation_equivalence as equivalence

    calendar = get_calendar(load_settings())
    with (
        mock.patch.object(equivalence, "SYNTHETIC_FIRST", dt.date(2021, 1, 4)),
        mock.patch.object(equivalence, "SYNTHETIC_LAST", dt.date(2023, 6, 30)),
    ):
        synthetic = equivalence._synthetic(calendar)
    bars = synthetic.panel[synthetic.panel["symbol"] == "SPY"].reset_index(drop=True)
    frame = walkforward.target_panel_for(bars, calendar)
    targets = {
        horizon: build_targets(frame, horizon=horizon, calendar=calendar) for horizon in HORIZONS
    }
    return synthetic.panel, targets, synthetic.sessions


_FITTED = "2023-06-30"  # a session every configuration can be fitted at
_THIN = "2022-05-31"  # a session where h=1, 5 and 22 fit and h=66 has 247 training rows of 250


def _fake_store(
    monkeypatch: pytest.MonkeyPatch,
    *,
    universe: tuple[str, ...] = ("SPY",),
    missing: frozenset[str] = frozenset(),
    dev_end: dt.date | None = None,
) -> list[_Read]:
    """Replace the three seams ``forecast`` reaches the world through; return the reads made."""
    panel, targets, _ = _synthetic_store()
    reads: list[_Read] = []
    settings = load_settings()
    if dev_end is not None:
        settings = dataclasses.replace(
            settings, splits=dataclasses.replace(settings.splits, dev_end=dev_end)
        )
    settings = dataclasses.replace(settings, universe=universe)

    def fake_panel(_settings: Settings, symbol: str, *, end: dt.date, report: object = None):  # type: ignore[no-untyped-def]
        reads.append(_Read("panel", symbol, end))
        if symbol in missing:
            raise DatasetNotFound(f"no bars for {symbol}")
        return truncate(panel, end)

    def fake_targets(_settings: Settings, symbol: str, horizon: int, *, end: dt.date):  # type: ignore[no-untyped-def]
        reads.append(_Read("targets", symbol, end))
        return truncate(targets[horizon], end)

    monkeypatch.setattr(forecast, "load_settings", lambda: settings)
    monkeypatch.setattr(forecast, "load_curated_panel", fake_panel)
    monkeypatch.setattr(forecast, "load_targets", fake_targets)
    monkeypatch.setattr(forecast, "git_state", lambda scope=None: ("e" * 40, False))
    return reads


def _argv(root: Path, as_of: str) -> list[str]:
    return ["--as-of", as_of, "--at", _AT.isoformat(), "--root", str(root)]


def _fit_ledger(
    root: Path, symbols: tuple[str, ...] = ("SPY",), cohort: str = "phase2-baseline"
) -> None:
    """A ledger naming ``har_levels`` the winner of every configuration of ``symbols``."""
    _ledger(
        root,
        [
            _trial(model, value, symbol=symbol, horizon=horizon, cohort=cohort)
            for symbol in symbols
            for horizon in HORIZONS
            for model, value in (("har_levels", -8.0), ("random_walk", -7.0))
        ],
    )


def _digest(path: Path) -> str | None:
    return hashlib.md5(path.read_bytes()).hexdigest() if path.exists() else None


def test_main_fits_the_champion_and_writes_one_line_per_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fit_ledger(tmp_path)
    _fake_store(monkeypatch)

    code = forecast.main(_argv(tmp_path, _FITTED))

    out = capsys.readouterr().out
    lines = _read(tmp_path)
    assert code == 0
    assert [(line["symbol"], line["horizon"]) for line in lines] == [("SPY", h) for h in HORIZONS]
    for line in lines:
        assert line["at"] == _AT.isoformat()
        assert line["as_of"] == _FITTED
        value = line["value"]
        assert isinstance(value, float) and math.isfinite(value) and value > 0
        observations = line["observations"]
        assert isinstance(observations, int) and observations >= 250
        champion = line["champion"]
        assert isinstance(champion, dict)
        assert champion["model"] == "har_levels"
        assert champion["kind"] == PSEUDO_KIND
        assert champion["git_sha"] == "e" * 40
        assert len(str(champion["definition_hash"])) == 64
    assert "wrote 4 forecast line(s)" in out
    assert "PSEUDO-CHAMPION" in out and "dev-window leaderboard winner" in out
    assert "NOT the registered champion" in out
    assert out.count("kind=pseudo") == 4, "each configuration's champion is printed as a pseudo one"
    assert "as_of 2023-06-30" in out


def test_main_refuses_a_missing_as_of_rather_than_defaulting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        forecast.main(["--at", _AT.isoformat(), "--root", str(tmp_path)])

    assert exit_info.value.code == 2
    assert "the following arguments are required: --as-of" in capsys.readouterr().err


def test_main_refuses_a_missing_instant_rather_than_reading_a_clock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        forecast.main(["--as-of", _FITTED, "--root", str(tmp_path)])

    assert exit_info.value.code == 2
    assert "the following arguments are required: --at" in capsys.readouterr().err


def test_main_refuses_an_instant_with_no_offset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        forecast.main(["--as-of", _FITTED, "--at", "2026-11-04T21:00:00", "--root", str(tmp_path)])

    assert exit_info.value.code == 2
    assert "must carry a UTC offset" in capsys.readouterr().err


def test_main_refuses_an_unparseable_as_of(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        forecast.main(["--as-of", "yesterday", "--at", _AT.isoformat(), "--root", str(tmp_path)])

    assert exit_info.value.code == 2
    assert "invalid" in capsys.readouterr().err


def test_main_names_every_refused_configuration_and_the_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _ledger(tmp_path, [_trial("a", -8.0), _trial("b", -7.0)])
    reads = _fake_store(monkeypatch, universe=("SPY", "QQQ"))

    code = forecast.main(_argv(tmp_path, _FITTED))

    out = capsys.readouterr().out
    assert code == 1
    assert "refused QQQ h1: the ledger does not cover" in out
    assert "refused SPY h5: the ledger does not cover" in out
    # SPY h1 is covered but its champion "a" is in no cohort the ledger could rebuild a model from.
    assert "refused SPY h1: cohort 'phase2-shootout' holds no model 'a'" in out
    assert not forecast_log_path(tmp_path).exists()
    # A refusal at the champion never touched the store: the champion is derived first.
    assert reads == []


def test_the_value_is_what_the_walk_forward_gives_that_model_at_that_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a second fit: the same model at the same origin through ``evaluate`` with the whole
    baseline cohort must give the number on the line, and the observations the fit consumed."""
    _fit_ledger(tmp_path)
    _fake_store(monkeypatch)
    panel, targets, _ = _synthetic_store()
    origin = dt.date.fromisoformat(_FITTED)
    calendar = get_calendar(load_settings())

    forecast.main(_argv(tmp_path, _FITTED))

    for line in _read(tmp_path):
        horizon = line["horizon"]
        assert isinstance(horizon, int)
        evaluation = walkforward.evaluate(
            panel,
            truncate(targets[horizon], origin),
            [origin],
            symbol="SPY",
            horizon=horizon,
            calendar=calendar,
        )
        (result,) = evaluation.results
        assert line["value"] == result.forecasts["har_levels"]
        assert line["observations"] == result.training_rows


@pytest.mark.parametrize(
    ("as_of", "expected_end"),
    [
        (_FITTED, dt.date(2023, 6, 30)),
        ("2025-12-31", dt.date(2025, 12, 31)),
        ("2026-01-02", dt.date(2025, 12, 31)),
        ("2026-11-04", dt.date(2025, 12, 31)),
    ],
)
def test_the_store_is_read_to_the_earlier_of_the_date_and_dev_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_of: str, expected_end: dt.date
) -> None:
    _fit_ledger(tmp_path)
    reads = _fake_store(monkeypatch)

    forecast.main(_argv(tmp_path, as_of))

    dev_end = load_settings().splits.dev_end
    assert reads, "the stage read nothing, so this pins nothing"
    assert {read.end for read in reads} == {expected_end}
    assert all(read.end <= dev_end for read in reads)


def test_the_bound_is_the_configured_dev_end_and_not_a_constant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fit_ledger(tmp_path)
    reads = _fake_store(monkeypatch, dev_end=dt.date(2023, 3, 31))

    forecast.main(_argv(tmp_path, _FITTED))

    assert {read.end for read in reads} == {dt.date(2023, 3, 31)}


def test_a_date_past_dev_end_is_refused_at_every_configuration_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Direct invocation past ``dev_end`` is not a way round the seal: the read stops at
    ``dev_end``, the panel therefore ends before the date, and a forecast for a day the data does
    not reach is refused rather than made from an earlier one."""
    _fit_ledger(tmp_path)
    _fake_store(monkeypatch)

    code = forecast.main(_argv(tmp_path, "2026-11-04"))

    out = capsys.readouterr().out
    assert code == 1
    assert out.count("refused SPY h") == 4
    assert "ends at 2023-06-30, before the as-of 2026-11-04" in out
    assert not forecast_log_path(tmp_path).exists()


def test_a_configuration_that_cannot_be_fitted_is_refused_and_the_others_still_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fit_ledger(tmp_path)
    _fake_store(monkeypatch)

    code = forecast.main(_argv(tmp_path, _THIN))

    out = capsys.readouterr().out
    assert code == 1, "a day with a refusal in it must not exit as a success"
    assert [line["horizon"] for line in _read(tmp_path)] == [1, 5, 22]
    assert "refused SPY h66: only 247 knowable training row(s)" in out
    assert "wrote 3 forecast line(s)" in out
    assert "refusing: 1 configuration(s)" in out


def test_a_symbol_whose_panel_cannot_be_read_is_refused_at_each_of_its_horizons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fit_ledger(tmp_path, symbols=("SPY", "QQQ"))
    _fake_store(monkeypatch, universe=("SPY", "QQQ"), missing=frozenset({"QQQ"}))

    code = forecast.main(_argv(tmp_path, _FITTED))

    out = capsys.readouterr().out
    assert code == 1
    assert {(line["symbol"], line["horizon"]) for line in _read(tmp_path)} == {
        ("SPY", h) for h in HORIZONS
    }
    assert out.count("refused QQQ h") == 4
    assert "no bars for QQQ" in out


class _Skipper:
    """A champion whose own fit declines, as a GARCH that did not converge does (R87)."""

    name = "har_levels"
    fit_depends_on_horizon = True
    domain = Domain()

    def fit(self, view: object) -> object:
        raise ModelSkip("the optimiser did not converge")


class _ShortBreathed:
    """A champion that answers h=1 only, as EGARCH does, and returns a constant when asked."""

    name = "har_levels"
    fit_depends_on_horizon = False
    domain = Domain(max_horizon=1)

    def fit(self, view: object) -> object:
        class Fitted:
            observations = 7

            def forecast(self, horizon: int) -> Forecast:
                return Forecast(2.5e-5, 7)

        return Fitted()


def test_a_champion_that_skips_its_own_fit_is_refused_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fit_ledger(tmp_path)
    _fake_store(monkeypatch)
    monkeypatch.setattr(forecast, "champion_model", lambda champion: _Skipper())

    code = forecast.main(_argv(tmp_path, _FITTED))

    out = capsys.readouterr().out
    assert code == 1
    assert out.count("refused SPY h") == 4
    assert "the optimiser did not converge" in out
    assert not forecast_log_path(tmp_path).exists()


def test_a_champion_is_not_asked_a_horizon_it_does_not_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fit_ledger(tmp_path)
    _fake_store(monkeypatch)
    monkeypatch.setattr(forecast, "champion_model", lambda champion: _ShortBreathed())

    code = forecast.main(_argv(tmp_path, _FITTED))

    out = capsys.readouterr().out
    assert code == 1
    assert [line["horizon"] for line in _read(tmp_path)] == [1]
    assert "refused SPY h5: 'har_levels' answers h<=1" in out


def test_a_non_finite_forecast_is_refused_rather_than_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fit_ledger(tmp_path)
    _fake_store(monkeypatch)
    monkeypatch.setattr(
        walkforward, "_har_levels_forecast", lambda *_: (float("nan"), 9), raising=True
    )

    code = forecast.main(_argv(tmp_path, _FITTED))

    assert code == 1
    assert "refused SPY h1: the fit gave a non-finite forecast (nan)" in capsys.readouterr().out
    assert not forecast_log_path(tmp_path).exists()


def test_a_second_run_of_a_written_day_adds_nothing_and_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fit_ledger(tmp_path)
    _fake_store(monkeypatch)
    forecast.main(_argv(tmp_path, _FITTED))
    first = _digest(forecast_log_path(tmp_path))
    capsys.readouterr()

    code = forecast.main(_argv(tmp_path, _FITTED))

    out = capsys.readouterr().out
    assert code == 0
    assert _digest(forecast_log_path(tmp_path)) == first
    assert out.count("already in the log") == 4
    assert "wrote 0 forecast line(s)" in out


def test_retrying_a_partly_refused_day_neither_duplicates_nor_rewrites_the_lines_it_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fit_ledger(tmp_path)
    _fake_store(monkeypatch)
    forecast.main(_argv(tmp_path, _THIN))
    first = _digest(forecast_log_path(tmp_path))
    capsys.readouterr()

    code = forecast.main(_argv(tmp_path, _THIN))

    out = capsys.readouterr().out
    assert code == 1, "the refused configuration is still refused, so the day is still not done"
    assert _digest(forecast_log_path(tmp_path)) == first
    assert out.count("already in the log") == 3
    assert "refused SPY h66" in out


def test_a_forecast_is_not_a_trial_so_no_ledger_is_written_or_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fit_ledger(tmp_path)
    _fake_store(monkeypatch)

    def tripwire(*args: object, **kwargs: object) -> int:
        raise AssertionError("a forecast stage appended a trial")

    monkeypatch.setattr(walkforward, "append_trials", tripwire)
    repo_trials = _REPO_ROOT / "data" / "trials.jsonl"
    repo_comparisons = _REPO_ROOT / "data" / "comparisons.jsonl"
    before = (_digest(tmp_path / "data" / "trials.jsonl"), _digest(repo_trials))
    before_repo = (_digest(repo_comparisons), _digest(_REPO_ROOT / "data" / "decisions.jsonl"))

    assert forecast.main(_argv(tmp_path, _FITTED)) == 0

    assert (_digest(tmp_path / "data" / "trials.jsonl"), _digest(repo_trials)) == before
    assert (
        _digest(repo_comparisons),
        _digest(_REPO_ROOT / "data" / "decisions.jsonl"),
    ) == before_repo
    assert sorted(p.name for p in (tmp_path / "data").iterdir()) == [
        "forecasts.jsonl",
        "trials.jsonl",
    ]


def test_a_refusing_stage_in_a_fresh_interpreter_exits_one_and_says_what_it_is(
    tmp_path: Path,
) -> None:
    """The refusal as an unattended job meets it, with no store read: the ledger is empty, so
    every configuration is refused at the champion, which is derived before any panel is read."""
    completed = subprocess.run(
        [sys.executable, "-m", "volab.daily.forecast", *_argv(tmp_path, _FITTED)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "NOT the registered champion" in completed.stdout
    assert "the trial ledger is empty" in completed.stdout
    assert "wrote 0 forecast line(s)" in completed.stdout
    assert not forecast_log_path(tmp_path).exists()
    assert not (_REPO_ROOT / FORECAST_LOG).exists()


# --- finding the champion's model: the leaderboard's cohort name is the way back ---------------


def test_the_baseline_champion_is_found_in_the_baseline_cohort() -> None:
    champion = PseudoChampion(
        symbol="SPY", horizon=1, loss="qlike", model="har_semilog", value=-8.0, origins=10,
        cohort="phase2-baseline", run_at="2026-10-28T08:00:00+00:00", run_sha="f" * 40,
    )  # fmt: skip

    assert champion_model(champion).name == "har_semilog"


def test_a_shootout_champion_is_found_in_the_registry_of_named_cohorts() -> None:
    champion = PseudoChampion(
        symbol="SPY", horizon=1, loss="qlike", model="egarch11_skewt", value=-8.0, origins=10,
        cohort="phase2-shootout", run_at="2026-10-28T08:00:00+00:00", run_sha="f" * 40,
    )  # fmt: skip

    model = champion_model(champion)

    assert model.name == "egarch11_skewt"
    assert not model.domain.answers(5), "the model found must be the one that declares h<=1"


def test_a_cohort_nobody_can_rebuild_is_refused_by_name() -> None:
    champion = PseudoChampion(
        symbol="SPY", horizon=1, loss="qlike", model="har_levels", value=-8.0, origins=10,
        cohort="a-cohort-from-nowhere", run_at="2026-10-28T08:00:00+00:00", run_sha="f" * 40,
    )  # fmt: skip

    with pytest.raises(NoChampionError, match="cohort 'a-cohort-from-nowhere' is not one"):
        champion_model(champion)


def test_a_model_the_named_cohort_does_not_hold_is_refused_by_name() -> None:
    champion = PseudoChampion(
        symbol="SPY", horizon=1, loss="qlike", model="har_fantasy", value=-8.0, origins=10,
        cohort="phase2-baseline", run_at="2026-10-28T08:00:00+00:00", run_sha="f" * 40,
    )  # fmt: skip

    with pytest.raises(NoChampionError, match="cohort 'phase2-baseline' holds no model 'har_fan"):
        champion_model(champion)


# --- no private name crosses a package boundary ------------------------------------------------


def _private_imports(source: str) -> list[str]:
    """Names this source imports with a leading underscore from a ``volab`` package other than
    ``volab.daily``. A package's own privates are its own business; another's are not."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module is not None
            and node.module.startswith("volab.")
            and node.module != "volab.daily"
            and not node.module.startswith("volab.daily.")
        ):
            found.extend(
                f"{alias.name} from {node.module}"
                for alias in node.names
                if alias.name.startswith("_")
            )
    return found


def test_nothing_under_daily_imports_a_private_name_from_another_package() -> None:
    sources = _daily_sources()

    assert {"forecast", "champion", "runner", "runlog"} <= sources.keys()
    for name, source in sources.items():
        assert _private_imports(source) == [], name


@pytest.mark.parametrize(
    "planted",
    [
        "from volab.leaderboard import _ranked\n",
        "from volab.leaderboard import TrialLine, _ranked, read_trials\n",
        "from volab.walkforward import _har_levels_forecast as h\n",
    ],
)
def test_the_private_import_scan_bites_on_a_planted_import(planted: str) -> None:
    assert _private_imports(planted) != []


def test_the_private_import_scan_lets_public_names_and_the_packages_own_through() -> None:
    clean = (
        "from volab.leaderboard import ranked_trials\n"
        "from volab.daily.forecast import _existing_keys\n"
        "from volab.daily import _dated\n"
    )

    assert _private_imports(clean) == []


# --- the run verb, end to end on a dev date ----------------------------------------------------

_SHIM = '''\
"""A stage module for tests: the real forecast ``main`` with its three seams reading a pickle.

The stage runner executes ``python -m <module>``, so a test cannot patch a function inside the
child. This module is the real ``volab.daily.forecast.main`` -- same parser, same fit, same writer
-- with ``load_settings``, ``load_curated_panel`` and ``load_targets`` answering from the frames in
the file named by ``--data``, so no real store is read. It honours ``end`` as the store does.
"""
import dataclasses
import pickle
import sys

import volab.daily.forecast as forecast
from volab.config import load_settings
from volab.panel import truncate

argv = sys.argv[1:]
where = argv.index("--data")
panel, targets = pickle.loads(open(argv[where + 1], "rb").read())
del argv[where : where + 2]
settings = dataclasses.replace(load_settings(), universe=("SPY",))

forecast.load_settings = lambda: settings
forecast.load_curated_panel = lambda _s, symbol, *, end, report=None: truncate(panel, end)
forecast.load_targets = lambda _s, symbol, horizon, *, end: truncate(targets[horizon], end)
sys.exit(forecast.main(argv))
'''

_START = dt.datetime(2026, 11, 5, 9, 30, tzinfo=dt.UTC)
_END = dt.datetime(2026, 11, 5, 9, 45, tzinfo=dt.UTC)


def _stages_with_the_real_forecast(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    panel, targets, _ = _synthetic_store()
    data = tmp_path / "store.pickle"
    data.write_bytes(pickle.dumps((panel, targets)))
    probes = tmp_path / "probes"
    probes.mkdir()
    (probes / "probe_ok.py").write_text("print('probe ran')\n", encoding="utf-8")
    (probes / "forecast_shim.py").write_text(_SHIM, encoding="utf-8")
    inherited = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH", f"{probes}{os.pathsep}{inherited}" if inherited else str(probes)
    )
    monkeypatch.setattr(
        daily_package,
        "LIVE_STAGES",
        (
            Stage(name="probe", module="probe_ok", argv=()),
            Stage(
                name="forecast",
                module="forecast_shim",
                argv=("--data", str(data), "--root", str(tmp_path)),
                needs_as_of=True,
                needs_at=True,
            ),
        ),
    )
    monkeypatch.setattr(daily_cli, "git_state", lambda scope=None: ("abc1234", False))


def _clock(*instants: dt.datetime):  # type: ignore[no-untyped-def]
    """Yields exactly these instants and raises if asked for another."""
    remaining = iter(instants)
    return lambda: next(remaining)


def test_a_work_plan_on_a_dev_date_completes_done_with_a_forecast_line_per_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fit_ledger(tmp_path)
    _stages_with_the_real_forecast(monkeypatch, tmp_path)

    code = daily_cli.main(["run", "--as-of", _FITTED], root=tmp_path, clock=_clock(_START, _END))

    records = [
        json.loads(text) for text in run_log_path(tmp_path).read_text(encoding="utf-8").splitlines()
    ]
    start, complete = records
    assert code == 0
    assert (start["kind"], complete["kind"]) == ("start", "complete")
    assert complete["outcome"] == "done"
    assert [stage["exit_code"] for stage in complete["stages"]] == [0, 0]
    assert completed_dates(run_log_path(tmp_path)) == frozenset({dt.date.fromisoformat(_FITTED)})
    lines = _read(tmp_path)
    assert [(line["symbol"], line["horizon"]) for line in lines] == [("SPY", h) for h in HORIZONS]
    # The instant on every forecast line is the run's own: the start line's, from the one seam.
    assert {line["at"] for line in lines} == {start["started_at"]} == {_START.isoformat()}
    assert "wrote 4 forecast line(s)" in complete["stages"][1]["output"]
    assert not (_REPO_ROOT / FORECAST_LOG).exists()


def test_a_run_whose_forecast_refuses_a_configuration_is_recorded_failed_not_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal cannot be mistaken for a success where it matters: the run log. The three
    configurations that could be fitted are written, and the day is still not completed."""
    _fit_ledger(tmp_path)
    _stages_with_the_real_forecast(monkeypatch, tmp_path)

    code = daily_cli.main(["run", "--as-of", _THIN], root=tmp_path, clock=_clock(_START, _END))

    _, complete = [
        json.loads(text) for text in run_log_path(tmp_path).read_text(encoding="utf-8").splitlines()
    ]
    assert code == daily_cli.STAGE_FAILED_EXIT_CODE
    assert complete["outcome"] == "failed"
    assert complete["stages"][1]["exit_code"] == 1
    assert "refused SPY h66" in complete["stages"][1]["output"]
    assert completed_dates(run_log_path(tmp_path)) == frozenset()
    assert [line["horizon"] for line in _read(tmp_path)] == [1, 5, 22]


def test_a_pseudo_champion_is_a_value_object_with_the_run_it_came_from(tmp_path: Path) -> None:
    _ledger(tmp_path, [_trial("a", -8.0), _trial("b", -7.0)])

    champion = pseudo_champion(tmp_path, "SPY", 1)

    assert isinstance(champion, PseudoChampion)
    assert champion.configuration()["model"] == "a"
    assert champion.configuration()["run_sha"] == "f" * 40


def test_nothing_here_created_the_repositorys_own_forecast_log() -> None:
    assert not (_REPO_ROOT / FORECAST_LOG).exists()


def test_the_batch_is_fsynced_and_not_merely_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[int] = []
    real_fsync = forecast.os.fsync

    def recording(descriptor: int) -> None:
        synced.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(forecast.os, "fsync", recording)

    append_forecasts([_line(), _line(horizon=1)], root=tmp_path, at=_AT)

    assert len(synced) == 1
