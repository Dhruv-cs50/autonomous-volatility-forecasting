"""Path layout algebra for the Parquet store.

Pure functions that say where a Parquet file lives and what a path means. Nothing here touches
the filesystem or reads Parquet; the guarded read path builds on these.

Layout under ``root``::

    raw/<source>/<symbol>/date=<YYYY-MM-DD>/part-<%Y%m%dT%H%M%SZ>.parquet
    curated/<dataset>/<symbol>/date=<YYYY-MM-DD>/part.parquet
    features/<feature_set>/<symbol>/date=<YYYY-MM-DD>/part.parquet

The ``date`` partition value is the ``as_of`` date of the rows in the file, which is what
point-in-time filtering keys on (never ``ts``).
"""

from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# Every Parquet file, in every tier, carries these columns.
MANDATORY_COLUMNS: tuple[str, ...] = ("symbol", "ts", "as_of", "ingested_at")

_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_RAW_NAME = re.compile(r"part-([0-9]{8}T[0-9]{6}Z)\.parquet")
_DATE_PREFIX = "date="
_SINGLE_PART_NAME = "part.parquet"
_SEGMENTS = 5  # tier / dataset / symbol / date=... / filename
_GLOB_METACHARACTERS = frozenset("*?[]")


class Tier(StrEnum):
    RAW = "raw"
    CURATED = "curated"
    FEATURES = "features"


@dataclass(frozen=True)
class PathInfo:
    """What a store path means. For the raw tier, ``dataset`` is the source segment."""

    tier: Tier
    dataset: str
    symbol: str
    date: dt.date
    ingested_at: dt.datetime | None


def _segment(kind: str, value: str) -> str:
    """Validate a dataset or symbol path segment.

    A segment must be a single, literal directory name. Anything else can escape the tier tree,
    change the segment count, or (via glob metacharacters) widen a scan to other symbols.
    """
    if not value or value in {".", ".."}:
        raise ValueError(f"{kind} must be a non-empty name, got {value!r}")
    if "/" in value or os.sep in value:
        raise ValueError(f"{kind} must not contain a path separator, got {value!r}")
    if _GLOB_METACHARACTERS.intersection(value):
        raise ValueError(f"{kind} must not contain glob metacharacters, got {value!r}")
    return value


def _partition(date: dt.date) -> str:
    return f"{_DATE_PREFIX}{date.isoformat()}"


def raw_path(root: Path, source: str, symbol: str, date: dt.date, ingested_at: dt.datetime) -> Path:
    if ingested_at.tzinfo is None:
        raise ValueError("ingested_at must be timezone-aware")
    stamp = ingested_at.astimezone(dt.UTC).strftime(_STAMP_FORMAT)
    return (
        root
        / Tier.RAW
        / _segment("source", source)
        / _segment("symbol", symbol)
        / _partition(date)
        / f"part-{stamp}.parquet"
    )


def curated_path(root: Path, dataset: str, symbol: str, date: dt.date) -> Path:
    return (
        root
        / Tier.CURATED
        / _segment("dataset", dataset)
        / _segment("symbol", symbol)
        / _partition(date)
        / _SINGLE_PART_NAME
    )


def features_path(root: Path, feature_set: str, symbol: str, date: dt.date) -> Path:
    return (
        root
        / Tier.FEATURES
        / _segment("feature_set", feature_set)
        / _segment("symbol", symbol)
        / _partition(date)
        / _SINGLE_PART_NAME
    )


def _parse_date_segment(segment: str) -> dt.date:
    if not segment.startswith(_DATE_PREFIX):
        raise ValueError(f"expected a 'date=<YYYY-MM-DD>' segment, got {segment!r}")
    text = segment[len(_DATE_PREFIX) :]
    try:
        parsed = dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"invalid date in partition segment {segment!r}") from None
    # fromisoformat also accepts e.g. '20230615'; only the canonical form is a valid partition.
    if parsed.isoformat() != text:
        raise ValueError(f"non-canonical date in partition segment {segment!r}")
    return parsed


def parse_path(path: Path, root: Path) -> PathInfo:
    """Invert the ``*_path`` builders. Raises ``ValueError`` on any path they could not build.

    Paths are normalised lexically (no filesystem access, symlinks are not followed).
    """
    try:
        relative = Path(os.path.normpath(path)).relative_to(os.path.normpath(root))
    except ValueError:
        raise ValueError(f"{path} is not under store root {root}") from None

    parts = relative.parts
    if len(parts) != _SEGMENTS:
        raise ValueError(
            f"expected {_SEGMENTS} path segments under {root}, got {len(parts)}: {path}"
        )
    tier_name, dataset, symbol, date_segment, filename = parts

    try:
        tier = Tier(tier_name)
    except ValueError:
        raise ValueError(f"unknown tier {tier_name!r} in {path}") from None

    _segment("dataset", dataset)
    _segment("symbol", symbol)
    date = _parse_date_segment(date_segment)

    if tier is not Tier.RAW:
        if filename != _SINGLE_PART_NAME:
            raise ValueError(
                f"{tier.value} filename must be {_SINGLE_PART_NAME!r}, got {filename!r}"
            )
        return PathInfo(tier=tier, dataset=dataset, symbol=symbol, date=date, ingested_at=None)

    match = _RAW_NAME.fullmatch(filename)
    if match is None:
        raise ValueError(f"raw filename must be 'part-<{_STAMP_FORMAT}>.parquet', got {filename!r}")
    try:
        ingested_at = dt.datetime.strptime(match.group(1), _STAMP_FORMAT).replace(tzinfo=dt.UTC)
    except ValueError:
        raise ValueError(f"invalid timestamp in raw filename {filename!r}") from None
    return PathInfo(tier=tier, dataset=dataset, symbol=symbol, date=date, ingested_at=ingested_at)


def dataset_glob(
    root: Path, tier: Tier, dataset: str, symbols: Sequence[str] | None = None
) -> list[str]:
    """One DuckDB scan pattern per symbol, or a single wildcard-symbol pattern when ``None``.

    Symbols and the dataset are validated as literal names, so a pattern can never match more
    than the symbols asked for. A bare ``str`` or an empty sequence is rejected rather than
    iterated into per-character patterns or an empty scan.
    """
    dataset = _segment("dataset", dataset)
    if symbols is None:
        names = ["*"]
    elif isinstance(symbols, str):
        raise ValueError(f"symbols must be a sequence of names, not a bare string: {symbols!r}")
    elif not symbols:
        raise ValueError("symbols must not be empty; pass None to scan every symbol")
    else:
        names = [_segment("symbol", symbol) for symbol in symbols]
    return [f"{root}/{tier.value}/{dataset}/{name}/{_DATE_PREFIX}*/*.parquet" for name in names]
