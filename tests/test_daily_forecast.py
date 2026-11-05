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

import volab.daily.forecast as forecast
from volab.daily import LIVE_STAGES
from volab.daily.forecast import (
    FORECAST_LOG,
    Fingerprint,
    ForecastLine,
    ForecastLogError,
    append_forecasts,
    definition_hash,
    forecast_log_path,
    take_fingerprint,
)

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


def test_without_a_champion_main_refuses_with_a_message_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = forecast.main(["--root", str(tmp_path)])

    assert code == 1
    assert "no champion" in capsys.readouterr().out
    assert list(tmp_path.rglob("*")) == []


def test_the_refusal_holds_as_a_real_stage_in_a_fresh_interpreter(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "volab.daily.forecast", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "no champion" in completed.stdout
    assert list(tmp_path.rglob("*")) == []


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
