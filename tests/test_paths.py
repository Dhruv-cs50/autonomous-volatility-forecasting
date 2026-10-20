import datetime as dt
from collections.abc import Callable
from pathlib import Path

import pytest

from volab.paths import (
    PathInfo,
    Tier,
    curated_path,
    dataset_glob,
    features_path,
    parse_path,
    raw_path,
)

ROOT = Path("/tmp/store")
DAY = dt.date(2023, 6, 15)
STAMP = dt.datetime(2026, 10, 19, 14, 30, 0, tzinfo=dt.UTC)


def test_raw_path_layout() -> None:
    assert raw_path(ROOT, "massive", "SPY", DAY, STAMP) == (
        ROOT / "raw/massive/SPY/date=2023-06-15/part-20261019T143000Z.parquet"
    )


def test_curated_path_layout() -> None:
    assert curated_path(ROOT, "bars_daily", "SPY", DAY) == (
        ROOT / "curated/bars_daily/SPY/date=2023-06-15/part.parquet"
    )


def test_features_path_layout() -> None:
    assert features_path(ROOT, "har_lags", "QQQ", DAY) == (
        ROOT / "features/har_lags/QQQ/date=2023-06-15/part.parquet"
    )


def test_raw_round_trip() -> None:
    assert parse_path(raw_path(ROOT, "massive", "SPY", DAY, STAMP), ROOT) == PathInfo(
        tier=Tier.RAW, dataset="massive", symbol="SPY", date=DAY, ingested_at=STAMP
    )


@pytest.mark.parametrize(
    ("builder", "tier", "dataset"),
    [(curated_path, Tier.CURATED, "bars_daily"), (features_path, Tier.FEATURES, "har_lags")],
)
def test_single_part_round_trip(
    builder: Callable[[Path, str, str, dt.date], Path], tier: Tier, dataset: str
) -> None:
    assert parse_path(builder(ROOT, dataset, "IWM", DAY), ROOT) == PathInfo(
        tier=tier, dataset=dataset, symbol="IWM", date=DAY, ingested_at=None
    )


@pytest.mark.parametrize(
    "bad",
    [
        ROOT / "curated/bars_daily/SPY/part.parquet",  # no date partition
        ROOT / "curated/bars_daily/SPY/date=not-a-date/part.parquet",
        ROOT / "nosuchtier/bars_daily/SPY/date=2023-06-15/part.parquet",
        Path("/elsewhere/curated/bars_daily/SPY/date=2023-06-15/part.parquet"),
    ],
)
def test_malformed_path_raises(bad: Path) -> None:
    with pytest.raises(ValueError):
        parse_path(bad, ROOT)


def test_dataset_glob_one_pattern_per_symbol() -> None:
    assert dataset_glob(ROOT, Tier.CURATED, "bars_daily", ["SPY", "QQQ"]) == [
        f"{ROOT}/curated/bars_daily/SPY/date=*/*.parquet",
        f"{ROOT}/curated/bars_daily/QQQ/date=*/*.parquet",
    ]


def test_dataset_glob_wildcards_symbols_when_none() -> None:
    assert dataset_glob(ROOT, Tier.CURATED, "bars_daily") == [
        f"{ROOT}/curated/bars_daily/*/date=*/*.parquet"
    ]
