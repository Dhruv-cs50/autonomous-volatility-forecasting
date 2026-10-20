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


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (ROOT / "curated/bars_daily/SPY/date=2023-06-15/part.parquet/extra", "expected 5 path"),
        (ROOT / "curated/bars_daily/date=2023-06-15/part.parquet", "expected 5 path"),
        (ROOT / "curated/bars_daily/SPY/date=20230615/part.parquet", "non-canonical date"),
        (ROOT / "curated/bars_daily/SPY/2023-06-15/part.parquet", "date=<YYYY-MM-DD>"),
        (ROOT / "curated/bars_daily/SPY/date=2023-13-45/part.parquet", "invalid date"),
        (ROOT / "curated/bars_daily/SPY/date=2023-06-15/garbage.txt", "filename must be"),
        (ROOT / "curated/bars_daily/SPY/date=2023-06-15/part-1.parquet", "filename must be"),
        (ROOT / "features/har_lags/SPY/date=2023-06-15/part.parquet.tmp", "filename must be"),
        (ROOT / "raw/massive/SPY/date=2023-06-15/part.parquet", "raw filename must be"),
        (ROOT / "raw/massive/SPY/date=2023-06-15/part-2026101T143000Z.parquet", "raw filename"),
        (
            ROOT / "raw/massive/SPY/date=2023-06-15/part-20261319T143000Z.parquet",
            "invalid timestamp",
        ),
        (ROOT / "curated/bars_daily/SP*/date=2023-06-15/part.parquet", "glob metacharacters"),
        # DuckDB's globber expands braces and Python's glob does not, so a brace segment would
        # have the two helpers asking the same question in different languages.
        (ROOT / "curated/bars_daily/{SPY,QQQ}/date=2023-06-15/part.parquet", "glob metacharac"),
        (ROOT / "../store2/curated/bars_daily/SPY/date=2023-06-15/part.parquet", "not under"),
        (ROOT / "nosuchtier/bars_daily/SPY/date=2023-06-15/part.parquet", "unknown tier"),
    ],
)
def test_parse_path_rejection_reasons(bad: Path, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_path(bad, ROOT)


def test_parse_path_collapses_dotdot_inside_root() -> None:
    inside = ROOT / "curated/other/../bars_daily/SPY/date=2023-06-15/part.parquet"
    assert parse_path(inside, ROOT).dataset == "bars_daily"


def test_raw_path_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        raw_path(ROOT, "massive", "SPY", DAY, dt.datetime(2026, 10, 19, 14, 30))


def test_raw_path_converts_to_utc() -> None:
    est = dt.timezone(dt.timedelta(hours=-5))
    stamp = dt.datetime(2026, 10, 19, 9, 30, tzinfo=est)
    assert raw_path(ROOT, "massive", "SPY", DAY, stamp).name == "part-20261019T143000Z.parquet"


BAD_SEGMENTS = ["", ".", "..", "a/b", "SP*", "SP?", "SP[", "SP]"]


@pytest.mark.parametrize("bad", BAD_SEGMENTS)
def test_builders_reject_bad_symbol(bad: str) -> None:
    with pytest.raises(ValueError, match="symbol must"):
        raw_path(ROOT, "massive", bad, DAY, STAMP)
    with pytest.raises(ValueError, match="symbol must"):
        curated_path(ROOT, "bars_daily", bad, DAY)
    with pytest.raises(ValueError, match="symbol must"):
        features_path(ROOT, "har_lags", bad, DAY)


@pytest.mark.parametrize("bad", BAD_SEGMENTS)
def test_builders_reject_bad_dataset(bad: str) -> None:
    with pytest.raises(ValueError, match="source must"):
        raw_path(ROOT, bad, "SPY", DAY, STAMP)
    with pytest.raises(ValueError, match="dataset must"):
        curated_path(ROOT, bad, "SPY", DAY)
    with pytest.raises(ValueError, match="feature_set must"):
        features_path(ROOT, bad, "SPY", DAY)


@pytest.mark.parametrize("bad", BAD_SEGMENTS)
def test_dataset_glob_rejects_bad_segments(bad: str) -> None:
    with pytest.raises(ValueError, match="symbol must"):
        dataset_glob(ROOT, Tier.CURATED, "bars_daily", ["SPY", bad])
    with pytest.raises(ValueError, match="dataset must"):
        dataset_glob(ROOT, Tier.CURATED, bad)


def test_dataset_glob_rejects_bare_string_symbols() -> None:
    with pytest.raises(ValueError, match="bare string"):
        dataset_glob(ROOT, Tier.CURATED, "bars_daily", "SPY")


def test_dataset_glob_rejects_empty_symbols() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        dataset_glob(ROOT, Tier.CURATED, "bars_daily", [])
