"""Typed configuration and the validation that defines the holdout boundary.

``splits.dev_end`` is the last date development code may observe and ``holdout_start`` is the
first date of the sealed window. The guarded read path compares a caller's requested ``end``
against ``dev_end``, so an inconsistent split here would make that guard meaningless. Every
failure is a ``ConfigError``; a bad configuration is never repaired or clipped.

``load_settings`` reads YAML, applies the explicit environment overrides in ``_ENV_OVERRIDES``
(any other ``VOLAB_*`` variable is ignored), then validates.
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
from volab.errors import ConfigError
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
    "calendar": "VOLAB_CALENDAR",
    "splits.dev_end": "VOLAB_SPLITS_DEV_END",
    "splits.holdout_start": "VOLAB_SPLITS_HOLDOUT_START",
    "storage.root": "VOLAB_STORAGE_ROOT",
}
_SECTIONS = frozenset({"splits", "storage"})


@dataclass(frozen=True)
class Splits:
    dev_end: dt.date
    holdout_start: dt.date


@dataclass(frozen=True)
class Settings:
    universe: tuple[str, ...]
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
        flat[name] = [part.strip() for part in value.split(",")] if name == "universe" else value


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


def _validate_universe(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ConfigError(f"universe must be a list of symbols, got {value!r}")
    if not value:
        raise ConfigError("universe must not be empty")
    symbols: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ConfigError(
                f"universe entries must be strings, got {entry!r} (quote symbols such as 'NO')"
            )
        if entry != entry.strip():
            raise ConfigError(f"universe entry {entry!r} has surrounding whitespace")
        # Symbols become directory names, so they must satisfy the layout's own segment rule.
        try:
            symbols.append(validate_segment("symbol", entry))
        except ValueError as exc:
            raise ConfigError(f"universe: {exc}") from None
    if len(set(symbols)) != len(symbols):
        duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
        raise ConfigError(f"universe has duplicate symbols: {duplicates}")
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
    _apply_env_overrides(flat)
    for name in _ENV_OVERRIDES:
        if name not in flat:
            raise ConfigError(f"missing required setting {name!r} in {config_path}")

    universe = _validate_universe(flat["universe"])
    calendar_name, calendar = _load_calendar(flat["calendar"])
    dev_end = _parse_date("splits.dev_end", flat["splits.dev_end"])
    holdout_start = _parse_date("splits.holdout_start", flat["splits.holdout_start"])
    splits = _validate_splits(calendar, dev_end, holdout_start)
    storage_root = _validate_storage_root(flat["storage.root"])
    return Settings(
        universe=universe, calendar=calendar_name, splits=splits, storage_root=storage_root
    )
