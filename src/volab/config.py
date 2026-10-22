"""Typed configuration and the validation that defines the holdout boundary.

``splits.dev_end`` is the last date development code may observe and ``holdout_start`` is the
first date of the sealed window. The guarded read path compares a caller's requested ``end``
against ``dev_end``, so an inconsistent split here would make that guard meaningless. A bad
configuration is never repaired or clipped.

``load_settings`` reads YAML, applies the explicit environment overrides in ``_ENV_OVERRIDES``
(any other ``VOLAB_*`` variable is ignored), then validates. Almost every failure is a
``ConfigError``. The one exception is ``HoldoutViolation``: an environment that moves
``splits.dev_end`` *later* than the file's own literal is not a mistyped setting, it is the
seal being moved from outside the file, and it is refused here because here is where the value
is accepted. See ``_refuse_a_later_dev_end``.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import exchange_calendars as xcals
import yaml
from exchange_calendars.errors import CalendarError, DateOutOfBounds

import volab
from volab.errors import ConfigError, HoldoutViolation
from volab.paths import validate_segment

_CONFIG_RELATIVE = Path("config") / "base.yaml"


def _find_default_config() -> Path:
    """Walk up from the installed package to the directory holding ``config/base.yaml``.

    Never cwd-relative: the same call must work from a test run, a scheduled job, or anywhere.
    If no such directory exists the conventional location is returned, so that the failure
    surfaces as a ``ConfigError`` from ``load_settings`` rather than as an import-time crash.
    """
    start = Path(volab.__file__).resolve().parent
    for directory in (start, *start.parents):
        if (directory / _CONFIG_RELATIVE).is_file():
            return directory / _CONFIG_RELATIVE
    return start.parent.parent / _CONFIG_RELATIVE


DEFAULT_CONFIG_PATH: Path = _find_default_config()
_REPO_ROOT: Path = DEFAULT_CONFIG_PATH.parent.parent

# Dotted setting name -> environment variable. Explicit on purpose: no introspection.
_ENV_OVERRIDES: dict[str, str] = {
    "universe": "VOLAB_UNIVERSE",
    "exogenous": "VOLAB_EXOGENOUS",
    "calendar": "VOLAB_CALENDAR",
    "splits.dev_end": "VOLAB_SPLITS_DEV_END",
    "splits.holdout_start": "VOLAB_SPLITS_HOLDOUT_START",
    "storage.root": "VOLAB_STORAGE_ROOT",
}
# The second of the two keys ``store.load_panel`` requires to read the sealed window. Defined
# here rather than in ``store`` because this module's own refusal has to name it, and ``store``
# imports ``config`` and not the other way round: one definition, so the two cannot drift.
HOLDOUT_UNLOCK_ENV: str = "VOLAB_HOLDOUT_UNLOCK"

_SECTIONS = frozenset({"splits", "storage"})
# Settings that are comma-separated lists in the environment and symbol lists in the file.
_SYMBOL_LISTS = frozenset({"universe", "exogenous"})


@dataclass(frozen=True)
class Splits:
    dev_end: dt.date
    holdout_start: dt.date


@dataclass(frozen=True)
class Settings:
    universe: tuple[str, ...]
    exogenous: tuple[str, ...]
    calendar: str
    splits: Splits
    storage_root: Path


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of letting the last one win."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            try:
                is_duplicate = key in seen
            except TypeError:  # an unhashable key, e.g. '? [a, b]'; the base loader rejects it too
                raise yaml.constructor.ConstructorError(
                    None, None, f"found unhashable key {key!r}", key_node.start_mark
                ) from None
            if is_duplicate:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _read_settings_file(path: Path) -> dict[str, Any]:
    """Read the YAML file into a flat map of dotted setting name to raw value."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    try:
        raw = yaml.load(text, Loader=_UniqueKeyLoader)  # a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config {path} must be a mapping at the top level")

    flat: dict[str, Any] = {}
    for key, value in raw.items():
        if "." in str(key):
            # A dotted top-level key would alias a section entry and silently override it.
            raise ConfigError(f"config key {str(key)!r} must not contain '.'; use a section")
        if key in _SECTIONS:
            if not isinstance(value, dict):
                raise ConfigError(f"config section {key!r} must be a mapping")
            flat.update({f"{key}.{sub}": inner for sub, inner in value.items()})
        else:
            flat[str(key)] = value
    unknown = sorted(set(flat) - set(_ENV_OVERRIDES))
    if unknown:
        raise ConfigError(f"unknown config key {unknown[0]!r} in {path}")
    return flat


def _apply_env_overrides(flat: dict[str, Any]) -> None:
    for name, variable in _ENV_OVERRIDES.items():
        value = os.environ.get(variable)
        if value is None:
            continue
        # Whitespace around commas is ordinary syntax in the env form only.
        is_symbol_list = name in _SYMBOL_LISTS
        flat[name] = [part.strip() for part in value.split(",")] if is_symbol_list else value


def _parse_date(field: str, value: object) -> dt.date:
    """Accept an ISO ``YYYY-MM-DD`` string or a YAML date; nothing looser."""
    if isinstance(value, dt.datetime):
        raise ConfigError(f"{field} must be a date, got a datetime: {value!r}")
    if isinstance(value, dt.date):
        return value
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be a YYYY-MM-DD date, got {value!r}")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise ConfigError(f"{field} must be a YYYY-MM-DD date, got {value!r}") from None
    if parsed.isoformat() != value:  # fromisoformat also accepts e.g. '20231231'
        raise ConfigError(f"{field} must be a YYYY-MM-DD date, got {value!r}")
    return parsed


def _validate_symbol_list(field: str, value: object) -> tuple[str, ...]:
    """Validate a list of symbols; ``field`` names the setting in every message."""
    if not isinstance(value, list | tuple):
        raise ConfigError(f"{field} must be a list of symbols, got {value!r}")
    if not value:
        raise ConfigError(f"{field} must not be empty")
    symbols: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ConfigError(
                f"{field} entries must be strings, got {entry!r} (quote symbols such as 'NO')"
            )
        if entry != entry.strip():
            raise ConfigError(f"{field} entry {entry!r} has surrounding whitespace")
        # Symbols become directory names, so they must satisfy the layout's own segment rule.
        try:
            symbols.append(validate_segment("symbol", entry))
        except ValueError as exc:
            raise ConfigError(f"{field}: {exc}") from None
    if len(set(symbols)) != len(symbols):
        duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
        raise ConfigError(f"{field} has duplicate symbols: {duplicates}")
    return tuple(symbols)


def _load_calendar(value: object) -> tuple[str, xcals.ExchangeCalendar]:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"calendar must be a non-empty string, got {value!r}")
    try:
        return value, xcals.get_calendar(value)
    except CalendarError as exc:
        raise ConfigError(f"calendar {value!r} is not a known exchange calendar: {exc}") from None


def _validate_splits(
    calendar: xcals.ExchangeCalendar, dev_end: dt.date, holdout_start: dt.date
) -> Splits:
    """Require a clean seam: holdout after dev, with no trading session between them.

    Neither endpoint need be a session (the default ``dev_end`` is a Sunday); what matters is
    that no session exists that belongs to neither window.
    """
    if holdout_start <= dev_end:
        raise ConfigError(
            f"splits.holdout_start must be after dev_end, got {holdout_start} <= {dev_end}"
        )
    gap_start = dev_end + dt.timedelta(days=1)
    gap_end = holdout_start - dt.timedelta(days=1)
    if gap_start <= gap_end:
        try:
            sessions = calendar.sessions_in_range(gap_start, gap_end)
        except (DateOutOfBounds, OverflowError) as exc:  # OverflowError: year-0001 dates
            raise ConfigError(
                f"cannot verify the gap {gap_start}..{gap_end} against the calendar: {exc}"
            ) from None
        if len(sessions) > 0:
            raise ConfigError(
                f"{len(sessions)} trading session(s) fall between dev_end {dev_end} and "
                f"holdout_start {holdout_start}, first {sessions[0].date()}: they would belong "
                "to neither window"
            )
    return Splits(dev_end=dev_end, holdout_start=holdout_start)


def _refuse_a_later_dev_end(literal: object, effective: dt.date, config_path: Path) -> None:
    """Refuse an environment that moves ``splits.dev_end`` later than the file's own literal.

    ``VOLAB_SPLITS_DEV_END`` exists so a developer can work to an **earlier** boundary than the
    repository's; that direction is conservative and stays open. The other direction opens the
    sealed window, and it opens it invisibly: ``store.load_panel``'s own check compares a
    caller's ``Settings`` against ``load_settings()``, and this variable moves both sides at
    once, so that check cannot see it. Nor can the suite's policing fixture, which reads the
    same configuration. The one place the two values are still distinguishable is here, between
    the file and the override, which is why the refusal lives in the configuration layer.

    Not a ``ConfigError``: a later ``dev_end`` is not a setting someone mistyped, it is the
    holdout seal being moved from outside the file, and it belongs to the same class of refusal
    as a keyless read. What is compared is the file on disk, so a local edit to ``base.yaml``
    moves the boundary; that edit is visible in a diff, and an exported variable is not.

    A file pinning no literal is refused too, so the rule is total rather than "no later than
    the literal, when there is one" -- otherwise deleting the key would sidestep it.
    """
    if os.environ.get(_ENV_OVERRIDES["splits.dev_end"]) is None:
        return
    unlock = f"allow_holdout=True and {HOLDOUT_UNLOCK_ENV}=1"
    if literal is None:
        raise HoldoutViolation(
            f"{_ENV_OVERRIDES['splits.dev_end']} sets splits.dev_end to {effective}, but "
            f"{config_path} pins no splits.dev_end of its own to be more conservative than: "
            f"the holdout boundary is a committed literal, not an environment variable. "
            f"Unlock the window with {unlock} instead."
        )
    pinned = _parse_date("splits.dev_end", literal)
    if effective > pinned:
        raise HoldoutViolation(
            f"{_ENV_OVERRIDES['splits.dev_end']} sets splits.dev_end to {effective}, later "
            f"than the {pinned} pinned in {config_path}: moving the holdout boundary is not a "
            f"way to read past it. An earlier dev_end is allowed, because it only withholds "
            f"data. Unlock the window with {unlock} instead."
        )


def _validate_storage_root(value: object) -> Path:
    """Resolve against the repository root and require a location strictly inside it.

    Lexical, like ``paths``: symlinks are not followed, so a ``data`` symlink to another disk
    still works.

    The root is interpolated straight into the scan patterns ``paths.dataset_glob`` builds, so
    its own components are held to the rule every dataset and symbol segment is held to: no glob
    metacharacters, or a pattern could match more than what was asked for. ``storage.root:
    "data*"`` would otherwise scan every sibling directory whose name starts with ``data``.
    Refused here, in the configuration layer, because that is where the value is accepted.
    """
    if not isinstance(value, str) or not value:
        raise ConfigError(f"storage.root must be a non-empty path string, got {value!r}")
    root = Path(os.path.normpath(_REPO_ROOT / value))  # an absolute value wins the join
    if root == _REPO_ROOT or not root.is_relative_to(_REPO_ROOT):
        raise ConfigError(f"storage.root {value!r} must be inside the repository {_REPO_ROOT}")
    for component in root.relative_to(_REPO_ROOT).parts:
        try:
            validate_segment("storage.root component", component)
        except ValueError as exc:
            raise ConfigError(f"storage.root {value!r}: {exc}") from None
    return root


def load_settings(path: Path | None = None) -> Settings:
    """Load and validate settings; ``path`` defaults to the repository's ``config/base.yaml``."""
    config_path = DEFAULT_CONFIG_PATH if path is None else path
    flat = _read_settings_file(config_path)
    # Read before the override, because the override is what it has to be compared against.
    literal_dev_end = flat.get("splits.dev_end")
    _apply_env_overrides(flat)
    for name in _ENV_OVERRIDES:
        if name not in flat:
            raise ConfigError(f"missing required setting {name!r} in {config_path}")

    universe = _validate_symbol_list("universe", flat["universe"])
    exogenous = _validate_symbol_list("exogenous", flat["exogenous"])
    overlap = sorted(set(universe) & set(exogenous))
    if overlap:
        raise ConfigError(
            f"symbol(s) {overlap} are in both universe and exogenous: a series is either a "
            "forecast target or a market-wide input, never both"
        )
    calendar_name, calendar = _load_calendar(flat["calendar"])
    dev_end = _parse_date("splits.dev_end", flat["splits.dev_end"])
    _refuse_a_later_dev_end(literal_dev_end, dev_end, config_path)
    holdout_start = _parse_date("splits.holdout_start", flat["splits.holdout_start"])
    splits = _validate_splits(calendar, dev_end, holdout_start)
    storage_root = _validate_storage_root(flat["storage.root"])
    return Settings(
        universe=universe,
        exogenous=exogenous,
        calendar=calendar_name,
        splits=splits,
        storage_root=storage_root,
    )
