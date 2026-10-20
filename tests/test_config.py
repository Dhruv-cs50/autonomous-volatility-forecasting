import dataclasses
import datetime as dt
from pathlib import Path

import pytest

import volab
from volab.config import DEFAULT_CONFIG_PATH, Settings, load_settings
from volab.errors import ConfigError

VALID = """
universe: [SPY, QQQ, IWM]
calendar: XNYS
splits:
  dev_end: "2023-12-31"
  holdout_start: "2024-01-02"
storage:
  root: "data"
"""

_OVERRIDE_VARS = (
    "VOLAB_UNIVERSE",
    "VOLAB_CALENDAR",
    "VOLAB_SPLITS_DEV_END",
    "VOLAB_SPLITS_HOLDOUT_START",
    "VOLAB_STORAGE_ROOT",
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own VOLAB_* variables must not change what these tests see."""
    for name in _OVERRIDE_VARS:
        monkeypatch.delenv(name, raising=False)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(text)
    return path


def _repo_root() -> Path:
    return DEFAULT_CONFIG_PATH.parent.parent


def test_repo_default_config_loads() -> None:
    settings = load_settings()
    assert settings.universe == ("SPY", "QQQ", "IWM")
    assert settings.calendar == "XNYS"
    assert settings.splits.dev_end == dt.date(2023, 12, 31)
    assert settings.splits.holdout_start == dt.date(2024, 1, 2)
    assert isinstance(settings, Settings)


def test_default_config_path_does_not_depend_on_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert DEFAULT_CONFIG_PATH.is_absolute()
    assert DEFAULT_CONFIG_PATH.is_file()
    assert Path(volab.__file__).resolve().is_relative_to(_repo_root())
    settings = load_settings()
    assert settings.storage_root == _repo_root() / "data"


def test_storage_root_is_relative_to_repo_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = load_settings(_write(tmp_path, VALID))
    assert settings.storage_root == _repo_root() / "data"


def test_storage_root_inside_repo_is_accepted_when_absolute(tmp_path: Path) -> None:
    inside = _repo_root() / "elsewhere"
    text = VALID.replace('root: "data"', f'root: "{inside}"')
    assert load_settings(_write(tmp_path, text)).storage_root == inside


def test_settings_are_frozen() -> None:
    settings = load_settings()
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.calendar = "XLON"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.splits.dev_end = dt.date(2030, 1, 1)  # type: ignore[misc]


def test_sunday_dev_end_is_accepted(tmp_path: Path) -> None:
    # 2023-12-31 is a Sunday and 2024-01-01 a holiday, so no session falls between.
    assert load_settings(_write(tmp_path, VALID)).splits.dev_end == dt.date(2023, 12, 31)


def test_adjacent_dates_are_accepted(tmp_path: Path) -> None:
    text = VALID.replace('"2024-01-02"', '"2024-01-01"')
    assert load_settings(_write(tmp_path, text)).splits.holdout_start == dt.date(2024, 1, 1)


def _with_splits(dev_end: str, holdout_start: str) -> str:
    # Replace the later date first so a new dev_end of 2024-01-02 is not rewritten again.
    return VALID.replace("2024-01-02", holdout_start).replace("2023-12-31", dev_end)


def test_session_day_dev_end_is_accepted(tmp_path: Path) -> None:
    # 2024-01-02 is a session; the next session, 2024-01-03, is the first of the holdout.
    text = _with_splits("2024-01-02", "2024-01-03")
    assert load_settings(_write(tmp_path, text)).splits.dev_end == dt.date(2024, 1, 2)


def test_session_directly_after_dev_end_is_rejected(tmp_path: Path) -> None:
    # 2024-01-03 (dev_end + 1 day) is a session lying in the gap.
    with pytest.raises(ConfigError, match="trading session"):
        load_settings(_write(tmp_path, _with_splits("2024-01-02", "2024-01-04")))


def test_session_directly_before_holdout_start_is_rejected(tmp_path: Path) -> None:
    # 2024-01-02 (holdout_start - 1 day) is a session; dev_end + 1 day is a Saturday.
    with pytest.raises(ConfigError, match="trading session"):
        load_settings(_write(tmp_path, _with_splits("2023-12-29", "2024-01-03")))


def test_unquoted_yaml_dates_are_accepted(tmp_path: Path) -> None:
    text = VALID.replace('"2023-12-31"', "2023-12-31").replace('"2024-01-02"', "2024-01-02")
    settings = load_settings(_write(tmp_path, text))
    assert settings.splits.dev_end == dt.date(2023, 12, 31)
    assert settings.splits.holdout_start == dt.date(2024, 1, 2)


@pytest.mark.parametrize(
    ("bad", "match", "reason"),
    [
        (VALID.replace("[SPY, QQQ, IWM]", "[]"), "must not be empty", "empty universe"),
        (VALID.replace("[SPY, QQQ, IWM]", "[SPY, SPY]"), "duplicate", "duplicate symbol"),
        (
            VALID.replace('"2024-01-02"', '"2023-12-30"'),
            "holdout_start must be after dev_end",
            "holdout_start before dev_end",
        ),
        (
            VALID.replace('"2024-01-02"', '"2023-12-31"'),
            "holdout_start must be after dev_end",
            "holdout_start equals dev_end",
        ),
        (
            VALID.replace('"2024-01-02"', '"2024-01-10"'),
            "trading session",
            "sessions fall in the gap",
        ),
        (VALID.replace('"2023-12-31"', '"not-a-date"'), "splits.dev_end", "unparseable date"),
        (VALID.replace('"2023-12-31"', '"20231231"'), "splits.dev_end", "non-canonical date"),
        (VALID.replace('root: "data"', 'root: "/etc"'), "storage.root", "root outside repo"),
        (VALID.replace('root: "data"', 'root: "../data"'), "storage.root", "root escapes repo"),
        (VALID.replace('root: "data"', 'root: "."'), "storage.root", "root is the repo itself"),
        (
            VALID.replace("calendar: XNYS", "calendar: NOT_A_CALENDAR"),
            "calendar",
            "unknown calendar",
        ),
        (VALID.replace("calendar: XNYS", "calendar: ''"), "calendar", "empty calendar"),
        (VALID.replace("calendar: XNYS", "calendar: 7"), "calendar", "non-string calendar"),
        (VALID.replace("[SPY, QQQ, IWM]", "SPY"), "must be a list", "universe not a list"),
        (VALID.replace("[SPY, QQQ, IWM]", "[SPY, NO]"), "universe", "YAML boolean symbol"),
        (VALID.replace("[SPY, QQQ, IWM]", "[SPY, '']"), "universe", "empty symbol"),
        (VALID.replace("[SPY, QQQ, IWM]", "[SPY, '../QQQ']"), "universe", "traversal symbol"),
        (VALID.replace("[SPY, QQQ, IWM]", "[SPY, 'A/B']"), "universe", "separator in symbol"),
        (VALID.replace("[SPY, QQQ, IWM]", "[SPY, 'Q*']"), "universe", "glob symbol"),
        (
            VALID.replace("universe: [SPY, QQQ, IWM]\n", ""),
            "missing required setting .universe.",
            "missing universe",
        ),
        (VALID.replace('  dev_end: "2023-12-31"\n', ""), "splits.dev_end", "missing dev_end"),
        (VALID.replace('storage:\n  root: "data"\n', ""), "storage", "missing storage"),
        (VALID + "extra: 1\n", "extra", "unknown top-level key"),
        (VALID.replace("  root:", "  typo: x\n  root:"), "typo", "unknown nested key"),
        (VALID + "calendar: XLON\n", "duplicate key", "duplicate top-level key"),
        (
            VALID.replace(
                '  dev_end: "2023-12-31"\n', '  dev_end: "2023-12-31"\n  dev_end: "2023-12-29"\n'
            ),
            "duplicate key",
            "duplicate nested key",
        ),
        ("? [a, b]\n: 1\n", "unhashable key", "unhashable mapping key"),
        (
            VALID.replace("[SPY, QQQ, IWM]", "[' SPY', QQQ]"),
            "surrounding whitespace",
            "leading space in YAML symbol",
        ),
        (
            VALID.replace("[SPY, QQQ, IWM]", "['SPY ', QQQ]"),
            "surrounding whitespace",
            "trailing space in YAML symbol",
        ),
        (VALID + 'splits.dev_end: "2099-01-01"\n', "must not contain", "dotted top-level key"),
        (
            VALID.replace("splits:", 'splits.dev_end: "2099-01-01"\nsplits:'),
            "must not contain",
            "dotted key before its section",
        ),
        (
            VALID.replace('"2023-12-31"', '"0001-01-01"').replace('"2024-01-02"', '"0001-01-05"'),
            "cannot verify the gap",
            "year-0001 gap",
        ),
        ("- a\n- b\n", "mapping", "not a mapping"),
        ("", "mapping", "empty file"),
        ("universe: [SPY\n", "YAML", "malformed YAML"),
    ],
)
def test_invalid_config_raises_config_error(
    tmp_path: Path, bad: str, match: str, reason: str
) -> None:
    with pytest.raises(ConfigError, match=match):
        load_settings(_write(tmp_path, bad))


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="absent.yaml"):
        load_settings(tmp_path / "absent.yaml")


def test_directory_in_place_of_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_settings(tmp_path)


def test_gap_outside_calendar_range_raises_config_error(tmp_path: Path) -> None:
    # Far beyond any calendar's last session: unverifiable, so rejected rather than assumed ok.
    text = VALID.replace('"2023-12-31"', '"2090-01-01"').replace('"2024-01-02"', '"2090-02-01"')
    with pytest.raises(ConfigError, match="calendar"):
        load_settings(_write(tmp_path, text))


def test_env_override_applies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLAB_CALENDAR", "XLON")
    assert load_settings(_write(tmp_path, VALID)).calendar == "XLON"


def test_env_overrides_apply_to_every_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOLAB_UNIVERSE", "SPY, DIA")
    monkeypatch.setenv("VOLAB_SPLITS_DEV_END", "2023-12-29")
    monkeypatch.setenv("VOLAB_SPLITS_HOLDOUT_START", "2024-01-02")
    monkeypatch.setenv("VOLAB_STORAGE_ROOT", "scratch")
    settings = load_settings(_write(tmp_path, VALID))
    assert settings.universe == ("SPY", "DIA")
    assert settings.splits.dev_end == dt.date(2023, 12, 29)
    assert settings.splits.holdout_start == dt.date(2024, 1, 2)
    assert settings.storage_root == _repo_root() / "scratch"


@pytest.mark.parametrize(
    ("name", "value", "match"),
    [
        ("VOLAB_UNIVERSE", "SPY,SPY", "duplicate"),
        ("VOLAB_UNIVERSE", "", "universe"),
        ("VOLAB_UNIVERSE", "SPY,../x", "universe"),
        ("VOLAB_CALENDAR", "NOT_A_CALENDAR", "calendar"),
        ("VOLAB_CALENDAR", "", "calendar"),
        ("VOLAB_SPLITS_DEV_END", "garbage", "splits.dev_end"),
        ("VOLAB_SPLITS_HOLDOUT_START", "2023-12-01", "holdout_start must be after dev_end"),
        ("VOLAB_STORAGE_ROOT", "/etc", "storage.root"),
    ],
)
def test_env_overrides_are_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str, match: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=match):
        load_settings(_write(tmp_path, VALID))


def test_unknown_volab_env_var_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLAB_HOLDOUT_UNLOCK", "1")
    monkeypatch.setenv("VOLAB_SPLITS_TYPO", "2099-01-01")
    settings = load_settings(_write(tmp_path, VALID))
    assert settings.calendar == "XNYS"
    assert settings.splits.dev_end == dt.date(2023, 12, 31)
