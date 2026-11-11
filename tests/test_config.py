import dataclasses
import datetime as dt
from pathlib import Path

import pytest

import volab
from volab.config import DEFAULT_CONFIG_PATH, Region, Settings, Splits, load_settings
from volab.errors import ConfigError, DatasetNotFound, HoldoutViolation, UnknownSymbol
from volab.store import load_panel

VALID = """
universe: [SPY, QQQ, IWM]
exogenous: [VIX]
calendar: XNYS
splits:
  dev_end: "2023-12-31"
  holdout_start: "2024-01-02"
  holdout_end: "2024-12-31"
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
    assert settings.splits.holdout_end == dt.date(2026, 10, 30)
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
        (
            VALID.replace('  holdout_end: "2024-12-31"\n', ""),
            "missing required setting 'splits.holdout_end'",
            "missing holdout_end",
        ),
        (
            VALID.replace('"2024-12-31"', '"2024-01-02"'),
            "holdout_end must be after holdout_start",
            "holdout_end equals holdout_start",
        ),
        (
            VALID.replace('"2024-12-31"', '"2024-01-01"'),
            "holdout_end must be after holdout_start",
            "holdout_end before holdout_start",
        ),
        (
            VALID.replace('"2024-12-31"', '"not-a-date"'),
            "splits.holdout_end",
            "unparseable holdout_end",
        ),
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
    monkeypatch.setenv("VOLAB_SPLITS_HOLDOUT_END", "2025-06-30")
    monkeypatch.setenv("VOLAB_STORAGE_ROOT", "scratch")
    settings = load_settings(_write(tmp_path, VALID))
    assert settings.universe == ("SPY", "DIA")
    assert settings.splits.dev_end == dt.date(2023, 12, 29)
    assert settings.splits.holdout_start == dt.date(2024, 1, 2)
    assert settings.splits.holdout_end == dt.date(2025, 6, 30)
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
        ("VOLAB_SPLITS_HOLDOUT_END", "garbage", "splits.holdout_end"),
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
  holdout_end: "2024-12-31"
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
        splits=Splits(
            dev_end=dt.date(2026, 6, 30),
            holdout_start=dt.date(2026, 7, 1),
            holdout_end=dt.date(2026, 12, 31),
        ),
    )

    with pytest.raises(HoldoutViolation, match="later than the 2025-12-31 pinned in"):
        load_panel("bars_daily", symbols=["SPY"], end=dt.date(2026, 6, 30), settings=moved)


# --- The seal's far end moves the other way ------------------------------------------------
#
# ``holdout_end`` is the last sealed date; everything after it is ``live``. Moving it *earlier*
# turns days that were sealed into days that are ``live``, which the reveal will hand out freely,
# so earlier is the refused direction -- the reverse of ``dev_end``, where later is. A rule copied
# from ``dev_end`` without turning the comparison round would refuse the harmless override and
# wave the dangerous one through, which is why every case below states the direction it covers.
#
# The refusal cases leave ``VOLAB_SPLITS_HOLDOUT_START`` alone: moving ``holdout_end`` earlier
# than the file's does not need a second variable to be consistent, and the only thing under test
# is the direction.

NO_HOLDOUT_END = VALID.replace('  holdout_end: "2024-12-31"\n', "")


@pytest.mark.parametrize(
    "earlier",
    [
        "2024-12-30",  # one day earlier
        "2024-01-03",  # still after holdout_start: a shorter sealed window, not an invalid one
        "2023-01-01",  # not even after holdout_start: still a violation, not a ConfigError
    ],
)
def test_an_env_holdout_end_earlier_than_the_file_literal_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, earlier: str
) -> None:
    monkeypatch.setenv("VOLAB_SPLITS_HOLDOUT_END", earlier)

    with pytest.raises(HoldoutViolation, match="earlier than the 2024-12-31 pinned in"):
        load_settings(_write(tmp_path, VALID))


def test_an_env_holdout_end_earlier_than_the_repository_literal_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same refusal for the configuration every unqualified ``load_settings()`` reads."""
    monkeypatch.setenv("VOLAB_SPLITS_HOLDOUT_END", "2026-10-29")

    with pytest.raises(HoldoutViolation, match="earlier than the 2026-10-30 pinned in"):
        load_settings()


def test_an_env_holdout_end_later_than_the_file_literal_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A longer sealed window only withholds more, so this direction stays open."""
    monkeypatch.setenv("VOLAB_SPLITS_HOLDOUT_END", "2025-06-30")

    assert load_settings(_write(tmp_path, VALID)).splits.holdout_end == dt.date(2025, 6, 30)


def test_an_env_holdout_end_later_than_the_repository_literal_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOLAB_SPLITS_HOLDOUT_END", "2026-12-31")

    assert load_settings().splits.holdout_end == dt.date(2026, 12, 31)


def test_an_env_holdout_end_equal_to_the_file_literal_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOLAB_SPLITS_HOLDOUT_END", "2024-12-31")

    assert load_settings(_write(tmp_path, VALID)).splits.holdout_end == dt.date(2024, 12, 31)


def test_the_two_override_directions_are_opposite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one test that fails if either rule is copied from the other, whichever way round.

    Each variable is moved earlier and later in turn against the same file. ``dev_end`` is
    refused later and ``holdout_end`` earlier: the two moves that carry a boundary into the
    committed sealed window, one from each side.
    """
    config = _write(tmp_path, VALID)
    outcomes: dict[tuple[str, str], bool] = {}
    cases = [
        # (variable, direction, value, extra variable that keeps the pair consistent)
        ("VOLAB_SPLITS_DEV_END", "earlier", "2023-12-29", None),
        (
            "VOLAB_SPLITS_DEV_END",
            "later",
            "2024-06-28",
            ("VOLAB_SPLITS_HOLDOUT_START", "2024-07-01"),
        ),
        ("VOLAB_SPLITS_HOLDOUT_END", "earlier", "2024-12-30", None),
        ("VOLAB_SPLITS_HOLDOUT_END", "later", "2025-01-02", None),
    ]
    for variable, direction, value, extra in cases:
        with monkeypatch.context() as scoped:
            scoped.setenv(variable, value)
            if extra is not None:
                scoped.setenv(*extra)
            try:
                load_settings(config)
            except HoldoutViolation:
                outcomes[(variable, direction)] = True
            else:
                outcomes[(variable, direction)] = False

    assert outcomes == {
        ("VOLAB_SPLITS_DEV_END", "earlier"): False,
        ("VOLAB_SPLITS_DEV_END", "later"): True,
        ("VOLAB_SPLITS_HOLDOUT_END", "earlier"): True,
        ("VOLAB_SPLITS_HOLDOUT_END", "later"): False,
    }


@pytest.mark.parametrize("value", ["2024-12-31", "2099-12-31", "2000-01-01"])
def test_an_env_holdout_end_is_refused_when_the_file_pins_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A file with no literal has no end to be later than, whatever date the variable carries.

    Without this the refusal would be sidestepped by deleting the key. The later and equal
    values are the ones that would pass if the rule were "no earlier than the literal, when
    there is one".
    """
    monkeypatch.setenv("VOLAB_SPLITS_HOLDOUT_END", value)

    with pytest.raises(HoldoutViolation, match="pins no splits.holdout_end"):
        load_settings(_write(tmp_path, NO_HOLDOUT_END))


def test_a_file_pinning_no_holdout_end_is_a_config_error_when_the_env_is_unset(
    tmp_path: Path,
) -> None:
    """The other route to the same file: no variable, so the missing-key rule answers instead."""
    with pytest.raises(ConfigError, match="missing required setting 'splits.holdout_end'"):
        load_settings(_write(tmp_path, NO_HOLDOUT_END))


def test_the_holdout_end_variable_is_a_registered_override() -> None:
    """The variable has the name the design gives it, in the map the loader reads.

    ``conftest._clean_volab_environment`` clears whatever is in that map; that it does so for
    this variable was checked by exporting it around a run, not by this test.
    """
    from volab.config import _ENV_OVERRIDES

    assert _ENV_OVERRIDES["splits.holdout_end"] == "VOLAB_SPLITS_HOLDOUT_END"


# --- The three regions ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (dt.date.min, Region.DEV),
        (dt.date(2025, 12, 30), Region.DEV),
        (dt.date(2025, 12, 31), Region.DEV),  # dev_end itself
        (dt.date(2026, 1, 1), Region.HOLDOUT),  # between dev_end and holdout_start
        (dt.date(2026, 1, 2), Region.HOLDOUT),  # holdout_start itself
        (dt.date(2026, 6, 15), Region.HOLDOUT),
        (dt.date(2026, 10, 30), Region.HOLDOUT),  # holdout_end itself
        (dt.date(2026, 10, 31), Region.LIVE),  # holdout_end + 1 day
        (dt.date(2026, 11, 2), Region.LIVE),
        (dt.date.max, Region.LIVE),
    ],
)
def test_the_repository_dates_fall_in_the_three_regions(day: dt.date, expected: Region) -> None:
    assert load_settings().splits.region_of(day) is expected


def test_a_date_in_the_gap_between_dev_and_holdout_is_holdout() -> None:
    """2026-01-01 is a holiday, so the seam check lets it through, and it belongs to neither
    ``dev_end`` nor ``holdout_start``. It is ``holdout``: ``dev`` means "at or before ``dev_end``",
    which is the test ``store.load_panel`` applies, so calling the gap ``dev`` would name as free a
    date the guard refuses."""
    splits = load_settings().splits
    gap = splits.dev_end + dt.timedelta(days=1)

    assert gap < splits.holdout_start
    assert splits.region_of(gap) is Region.HOLDOUT


def test_every_date_has_exactly_one_region_and_they_follow_in_order() -> None:
    splits = load_settings().splits
    first = splits.dev_end - dt.timedelta(days=3)
    last = splits.holdout_end + dt.timedelta(days=3)
    days = [first + dt.timedelta(days=n) for n in range((last - first).days + 1)]

    order = [Region.DEV, Region.HOLDOUT, Region.LIVE]
    ranks = [order.index(splits.region_of(day)) for day in days]

    assert ranks == sorted(ranks)  # never back from a later region to an earlier one
    assert set(ranks) == {0, 1, 2}  # and the window shown really crosses all three


def test_splits_cannot_be_built_without_a_holdout_end() -> None:
    """Omitting ``holdout_end`` is a ``TypeError``: no default silently means "never ends"."""
    with pytest.raises(TypeError, match="holdout_end"):
        Splits(dev_end=dt.date(2023, 12, 31), holdout_start=dt.date(2024, 1, 2))  # type: ignore[call-arg]


def test_a_hand_built_splits_with_a_far_future_end_has_no_live_region() -> None:
    """A holdout with no practical end is now said at the call site, as an explicit date."""
    splits = Splits(
        dev_end=dt.date(2023, 12, 31),
        holdout_start=dt.date(2024, 1, 2),
        holdout_end=dt.date.max,  # the holdout never ends here, so there is no live region
    )

    assert splits.region_of(dt.date(2099, 1, 1)) is Region.HOLDOUT
    assert splits.region_of(dt.date.max) is Region.HOLDOUT


def test_a_live_date_is_refused_by_the_existing_guard_exactly_as_a_holdout_date_is(
    synthetic_store,
) -> None:
    """``live`` has a name and no path: no read past ``holdout_end`` is any more open than before.

    The settings are the store's, given the repository's own splits so that ``dev_end`` is the
    real boundary. Each read is refused at step 3, before any filesystem access, with the
    message the guard has always used, which names ``dev_end`` and not the region. That the two
    messages differ only in the date is the point: the guard's answer does not depend on the
    region.
    """
    splits = load_settings().splits
    settings = dataclasses.replace(synthetic_store.settings, splits=splits)
    holdout_day = dt.date(2026, 6, 15)
    live_day = dt.date(2026, 11, 2)
    assert splits.region_of(holdout_day) is Region.HOLDOUT
    assert splits.region_of(live_day) is Region.LIVE

    messages = []
    for day in (holdout_day, live_day):
        with pytest.raises(HoldoutViolation) as raised:
            load_panel("bars_daily", symbols=["SPY"], end=day, settings=settings)
        assert f"end {day} is past dev_end 2025-12-31; reading the holdout window" in str(
            raised.value
        )
        assert "allow_holdout=True and VOLAB_HOLDOUT_UNLOCK=1" in str(raised.value)
        messages.append(str(raised.value).replace(str(day), "<end>"))

    assert messages[0] == messages[1]
