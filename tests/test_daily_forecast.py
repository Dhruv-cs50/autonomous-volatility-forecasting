"""The forecast log: one line per day per configuration, append-only, each carrying a fingerprint.

Every test that writes writes under ``tmp_path``. The repository's own ``data/forecasts.jsonl``
must not exist as a consequence of running this file, and one test pins that.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

import volab.daily as daily_package
import volab.daily.forecast as forecast
from volab.config import load_settings
from volab.daily import LIVE_STAGES, Stage, plan_for
from volab.daily.champion import (
    PSEUDO_KIND,
    NoChampionError,
    PseudoChampion,
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
from volab.features.targets import HORIZONS

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


def test_main_refuses_a_missing_as_of_rather_than_defaulting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        forecast.main(["--root", str(tmp_path)])

    assert exit_info.value.code == 2
    assert "the following arguments are required: --as-of" in capsys.readouterr().err


def test_main_refuses_an_unparseable_as_of(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        forecast.main(["--as-of", "yesterday", "--root", str(tmp_path)])

    assert exit_info.value.code == 2
    assert "invalid" in capsys.readouterr().err


def test_main_names_every_refused_configuration_and_the_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ledger(tmp_path, [_trial("a", -8.0), _trial("b", -7.0)])

    code = forecast.main(["--as-of", "2025-12-15", "--root", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 1
    assert "refused QQQ h1: the ledger does not cover" in out
    assert "refused SPY h5: the ledger does not cover" in out
    assert not forecast_log_path(tmp_path).exists()


def test_main_with_champions_prints_them_as_dev_winners_and_still_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(forecast, "git_state", lambda scope=None: ("e" * 40, False))
    _covering_ledger(tmp_path)

    code = forecast.main(["--as-of", "2025-12-15", "--root", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 1
    assert "as_of 2025-12-15" in out
    assert "PSEUDO-CHAMPION" in out and "dev-window leaderboard winner" in out
    assert "NOT the registered champion" in out
    assert "SPY h1: har_levels" in out
    assert "kind=pseudo" in out
    assert "fitting the champions needs the store" in out
    assert not forecast_log_path(tmp_path).exists()


def test_the_stage_runs_in_a_fresh_interpreter_and_says_what_it_is(tmp_path: Path) -> None:
    _covering_ledger(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "volab.daily.forecast",
            "--as-of",
            "2025-12-15",
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "NOT the registered champion" in completed.stdout
    assert not forecast_log_path(tmp_path).exists()
    assert not (_REPO_ROOT / FORECAST_LOG).exists()


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
