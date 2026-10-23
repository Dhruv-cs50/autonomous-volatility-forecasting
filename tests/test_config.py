import dataclasses
import datetime as dt
from pathlib import Path

import pytest

import volab
from volab.config import DEFAULT_CONFIG_PATH, Settings, Splits, load_settings
from volab.errors import ConfigError, DatasetNotFound, HoldoutViolation, UnknownSymbol
from volab.store import load_panel

VALID = """
universe: [SPY, QQQ, IWM]
exogenous: [VIX]
calendar: XNYS
splits:
  dev_end: "2023-12-31"
  holdout_start: "2024-01-02"
storage:
  root: "data"
"""

# The VOLAB_* overrides are cleared for every test by ``_clean_volab_environment`` in
# conftest, which takes the names from ``_ENV_OVERRIDES`` itself. The hand-copied tuple and
# local fixture that used to live here were exactly the duplicate that fixture exists to avoid.


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
    assert settings.splits.dev_end == dt.date(2025, 12, 31)
    assert settings.splits.holdout_start == dt.date(2026, 1, 2)
    assert isinstance(settings, Settings)


def test_repo_default_exogenous() -> None:
    assert load_settings().exogenous == (
        "VIX",
        "VIX9D",
        "VIX1D",
        "VVIX",
        "BAA10Y",
        "DGS10",
        "T10Y2Y",
    )


def test_exogenous_may_not_overlap_universe(tmp_path: Path) -> None:
    text = VALID.replace("exogenous: [VIX]", "exogenous: [SPY]")
    with pytest.raises(ConfigError, match="both universe and exogenous"):
        load_settings(_write(tmp_path, text))


@pytest.mark.parametrize("value", ["[]", "[VIX, VIX]", '[" VIX"]', "[1]", "VIX"])
def test_exogenous_is_validated_like_universe(tmp_path: Path, value: str) -> None:
    text = VALID.replace("exogenous: [VIX]", f"exogenous: {value}")
    with pytest.raises(ConfigError, match="exogenous"):
        load_settings(_write(tmp_path, text))


def test_exogenous_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLAB_EXOGENOUS", "VIX, VVIX")
    assert load_settings(_write(tmp_path, VALID)).exogenous == ("VIX", "VVIX")


def test_exogenous_is_required(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing required setting 'exogenous'"):
        load_settings(_write(tmp_path, VALID.replace("exogenous: [VIX]\n", "")))


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
        # The layout refuses metacharacters in every dataset and symbol segment so a pattern can
        # never match more than what was asked for; the root is interpolated into the very same
        # pattern, so it is held to the very same rule.
        (VALID.replace('root: "data"', 'root: "data*"'), "glob metacharacters", "root wildcard"),
        (
            VALID.replace('root: "data"', 'root: "store/{a,b}"'),
            "glob metacharacters",
            "root brace expansion",
        ),
        (VALID.replace('root: "data"', 'root: "sub/da?a"'), "glob metacharacters", "root in depth"),
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


def test_load_panel_accepts_an_exogenous_symbol(synthetic_store) -> None:
    synthetic_store.write("vix_family", "VIX", [dt.date(2023, 6, 15)])
    frame = load_panel(
        "vix_family",
        symbols=["VIX"],
        end=dt.date(2023, 6, 15),
        settings=synthetic_store.settings,
    )
    assert list(frame["symbol"]) == ["VIX"]


def test_load_panel_none_symbols_still_scopes_to_universe(synthetic_store) -> None:
    synthetic_store.write("vix_family", "VIX", [dt.date(2023, 6, 15)])
    with pytest.raises(DatasetNotFound):
        load_panel("vix_family", end=dt.date(2023, 6, 15), settings=synthetic_store.settings)


def test_load_panel_still_rejects_an_unknown_symbol(synthetic_store) -> None:
    with pytest.raises(UnknownSymbol, match="TLT"):
        load_panel(
            "bars_daily",
            symbols=["TLT"],
            end=dt.date(2023, 6, 15),
            settings=synthetic_store.settings,
        )


# --- The seal cannot be moved later from outside the committed file ------------------------
#
# ``VOLAB_SPLITS_DEV_END`` exists so a developer can work to an *earlier* boundary than the
# repository's. Moving it the other way would open the sealed window with no key and no audit
# line, and ``load_panel``'s own check cannot see it: that compares a caller's ``Settings``
# against ``load_settings()``, which applies the same variable, so both sides move together.
# The refusal therefore has to live where the value is accepted.
#
# The refusal cases set ``VOLAB_SPLITS_HOLDOUT_START`` as well, so the pair stays internally
# consistent and the only thing under test is the direction the boundary moved. It is belt and
# braces rather than load-bearing: ``_refuse_a_later_dev_end`` runs before ``_validate_splits``,
# so a lone later ``dev_end`` raises ``HoldoutViolation`` either way.


def test_an_env_dev_end_later_than_the_file_literal_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOLAB_SPLITS_DEV_END", "2024-06-28")
    monkeypatch.setenv("VOLAB_SPLITS_HOLDOUT_START", "2024-07-01")

    with pytest.raises(HoldoutViolation, match="later than the 2023-12-31 pinned in"):
        load_settings(_write(tmp_path, VALID))


def test_an_env_dev_end_later_than_the_repository_literal_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same refusal for the configuration every unqualified ``load_settings()`` reads."""
    monkeypatch.setenv("VOLAB_SPLITS_DEV_END", "2026-06-30")
    monkeypatch.setenv("VOLAB_SPLITS_HOLDOUT_START", "2026-07-01")

    with pytest.raises(HoldoutViolation, match="later than the 2025-12-31 pinned in"):
        load_settings()


def test_an_env_dev_end_earlier_than_the_file_literal_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conservative direction stays open; it is the whole point of the override."""
    monkeypatch.setenv("VOLAB_SPLITS_DEV_END", "2023-12-29")

    assert load_settings(_write(tmp_path, VALID)).splits.dev_end == dt.date(2023, 12, 29)


def test_an_env_dev_end_equal_to_the_file_literal_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOLAB_SPLITS_DEV_END", "2023-12-31")

    assert load_settings(_write(tmp_path, VALID)).splits.dev_end == dt.date(2023, 12, 31)


NO_DEV_END = """
universe: [SPY, QQQ, IWM]
exogenous: [VIX]
calendar: XNYS
splits:
  holdout_start: "2024-01-02"
storage:
  root: "data"
"""


def test_an_env_dev_end_is_refused_when_the_file_pins_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file with no literal has no boundary to be more conservative than.

    Without this the refusal above would be sidestepped by deleting the key, and the invariant
    would be "no later than the literal, when there is one" rather than a total one.
    """
    monkeypatch.setenv("VOLAB_SPLITS_DEV_END", "2023-12-31")

    with pytest.raises(HoldoutViolation, match="pins no splits.dev_end"):
        load_settings(_write(tmp_path, NO_DEV_END))


def test_the_env_cannot_move_the_boundary_to_read_a_sealed_row(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-to-end shape of the hole: a curated row inside the sealed window, no key.

    ``load_panel``'s step 0 is vacuous here -- the ``Settings`` handed in and the configuration
    it is compared against carry the same moved ``dev_end``, because the variable moves both.
    The read must still be refused, and it is refused by ``load_settings`` before step 0 can
    compare anything.
    """
    sealed = dt.date(2026, 3, 2)  # inside the repository's sealed window
    synthetic_store.write("bars_daily", "SPY", [sealed])
    monkeypatch.setenv("VOLAB_SPLITS_DEV_END", "2026-06-30")
    monkeypatch.setenv("VOLAB_SPLITS_HOLDOUT_START", "2026-07-01")
    moved = dataclasses.replace(
        synthetic_store.settings,
        splits=Splits(dev_end=dt.date(2026, 6, 30), holdout_start=dt.date(2026, 7, 1)),
    )

    with pytest.raises(HoldoutViolation, match="later than the 2025-12-31 pinned in"):
        load_panel("bars_daily", symbols=["SPY"], end=dt.date(2026, 6, 30), settings=moved)
