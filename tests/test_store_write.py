"""The write path: what may reach the store, and what a half-written file must never look like.

``write_*`` exists so that ``load_panel``'s step 9 -- the assertion that a row's ``as_of`` agrees
with its ``date=`` partition -- is unreachable from data this package wrote. Every test here that
names ``contradicts`` is defending that property from the writing side.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from volab.errors import RawOverwrite, SchemaError
from volab.paths import Tier
from volab.store import load_panel, write_curated, write_features, write_raw

DAY = dt.date(2023, 6, 15)
INGESTED = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)


def _frame(
    *,
    symbol: str = "SPY",
    as_of: dt.date | None = DAY,
    ts: dt.datetime | None = None,
    ingested_at: dt.datetime = INGESTED,
    rows: int = 1,
) -> pd.DataFrame:
    point = dt.datetime(DAY.year, DAY.month, DAY.day, tzinfo=dt.UTC) if ts is None else ts
    return pd.DataFrame(
        {
            "symbol": [symbol] * rows,
            "ts": [point] * rows,
            "as_of": [as_of] * rows,
            "ingested_at": [ingested_at] * rows,
            "close": [123.45] * rows,
        }
    )


def test_write_curated_roundtrips_through_load_panel(synthetic_store) -> None:
    path = write_curated(
        _frame(), dataset="bars_daily", symbol="SPY", as_of=DAY, settings=synthetic_store.settings
    )

    assert path.exists()
    frame = load_panel("bars_daily", symbols=["SPY"], end=DAY, settings=synthetic_store.settings)
    assert list(frame["symbol"]) == ["SPY"]
    assert frame["close"].iloc[0] == pytest.approx(123.45)


def test_write_features_roundtrips(synthetic_store) -> None:
    write_features(
        _frame(),
        feature_set="har_daily",
        symbol="SPY",
        as_of=DAY,
        settings=synthetic_store.settings,
    )

    frame = load_panel(
        "har_daily",
        symbols=["SPY"],
        end=DAY,
        tier=Tier.FEATURES,
        settings=synthetic_store.settings,
    )
    assert frame["close"].iloc[0] == pytest.approx(123.45)


def test_write_raw_filename_carries_the_ingested_at_stamp(synthetic_store) -> None:
    path = write_raw(
        _frame(),
        source="nasdaq_bars",
        symbol="SPY",
        as_of=DAY,
        ingested_at=INGESTED,
        settings=synthetic_store.settings,
    )

    assert path.name == "part-20250101T000000Z.parquet"
    assert path.parent.name == "date=2023-06-15"


def test_write_raw_roundtrips_through_load_panel(synthetic_store) -> None:
    write_raw(
        _frame(),
        source="nasdaq_bars",
        symbol="SPY",
        as_of=DAY,
        ingested_at=INGESTED,
        settings=synthetic_store.settings,
    )

    frame = load_panel(
        "nasdaq_bars", symbols=["SPY"], end=DAY, tier=Tier.RAW, settings=synthetic_store.settings
    )
    assert len(frame) == 1


@pytest.mark.parametrize("missing", ["symbol", "ts", "as_of", "ingested_at"])
def test_write_requires_every_mandatory_column(synthetic_store, missing: str) -> None:
    frame = _frame().drop(columns=[missing])

    with pytest.raises(SchemaError, match=missing):
        write_curated(
            frame, dataset="bars_daily", symbol="SPY", as_of=DAY, settings=synthetic_store.settings
        )


def test_write_rejects_a_naive_ts(synthetic_store) -> None:
    frame = _frame(ts=dt.datetime(2023, 6, 15))

    with pytest.raises(SchemaError, match="timezone-aware"):
        write_curated(
            frame, dataset="bars_daily", symbol="SPY", as_of=DAY, settings=synthetic_store.settings
        )


def test_write_rejects_an_empty_frame(synthetic_store) -> None:
    with pytest.raises(SchemaError, match="no rows"):
        write_curated(
            _frame(rows=0),
            dataset="bars_daily",
            symbol="SPY",
            as_of=DAY,
            settings=synthetic_store.settings,
        )


def test_write_rejects_a_null_as_of(synthetic_store) -> None:
    with pytest.raises(SchemaError, match="null as_of"):
        write_curated(
            _frame(as_of=None),
            dataset="bars_daily",
            symbol="SPY",
            as_of=DAY,
            settings=synthetic_store.settings,
        )


def test_write_rejects_a_row_whose_as_of_differs_from_the_partition(synthetic_store) -> None:
    frame = _frame(as_of=dt.date(2023, 6, 14))

    with pytest.raises(SchemaError, match="contradicts"):
        write_curated(
            frame, dataset="bars_daily", symbol="SPY", as_of=DAY, settings=synthetic_store.settings
        )


def test_write_rejects_a_frame_mixing_two_as_of_values(synthetic_store) -> None:
    frame = pd.concat([_frame(), _frame(as_of=dt.date(2023, 6, 14))], ignore_index=True)

    with pytest.raises(SchemaError, match="contradicts"):
        write_curated(
            frame, dataset="bars_daily", symbol="SPY", as_of=DAY, settings=synthetic_store.settings
        )


def test_write_raw_refuses_to_overwrite_an_existing_file(synthetic_store) -> None:
    kwargs = {
        "source": "nasdaq_bars",
        "symbol": "SPY",
        "as_of": DAY,
        "ingested_at": INGESTED,
        "settings": synthetic_store.settings,
    }
    first = write_raw(_frame(), **kwargs)

    with pytest.raises(RawOverwrite, match="already exists"):
        write_raw(_frame(), **kwargs)

    assert first.exists()


def test_write_curated_overwrites_idempotently(synthetic_store) -> None:
    kwargs = {
        "dataset": "bars_daily",
        "symbol": "SPY",
        "as_of": DAY,
        "settings": synthetic_store.settings,
    }
    first = write_curated(_frame(), **kwargs)
    second = write_curated(_frame(), **kwargs)

    assert first == second
    assert list(first.parent.iterdir()) == [first]


def test_write_leaves_no_temp_file_behind(synthetic_store) -> None:
    path = write_curated(
        _frame(), dataset="bars_daily", symbol="SPY", as_of=DAY, settings=synthetic_store.settings
    )

    assert list(path.parent.iterdir()) == [path]


def test_a_failed_write_leaves_no_partial_file(synthetic_store, monkeypatch) -> None:
    import volab.store as store

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store.pq, "write_table", boom)
    root: Path = synthetic_store.settings.storage_root

    with pytest.raises(OSError, match="disk full"):
        write_curated(
            _frame(),
            dataset="bars_daily",
            symbol="SPY",
            as_of=DAY,
            settings=synthetic_store.settings,
        )

    assert list(root.rglob("*.parquet")) == []
    assert list(root.rglob("*.tmp")) == []


def test_write_rejects_a_datetime_as_of(synthetic_store) -> None:
    """``datetime`` passes mypy as a ``date`` subclass but builds ``date=...T00:00:00``."""
    with pytest.raises(SchemaError, match="not a datetime"):
        write_curated(
            _frame(),
            dataset="bars_daily",
            symbol="SPY",
            as_of=dt.datetime(2023, 6, 15, 12, 0),  # type: ignore[arg-type]
            settings=synthetic_store.settings,
        )


def test_load_panel_returns_timestamps_in_utc_whatever_the_host_timezone(
    synthetic_store,
) -> None:
    """DuckDB renders TIMESTAMP WITH TIME ZONE in the host's timezone, so without normalising,
    this laptop and CI would return identical instants under different dtypes. The instants were
    always right; the dtype was not reproducible, which in this project is the same defect."""
    write_curated(
        _frame(), dataset="bars_daily", symbol="SPY", as_of=DAY, settings=synthetic_store.settings
    )

    frame = load_panel("bars_daily", symbols=["SPY"], end=DAY, settings=synthetic_store.settings)

    assert str(frame["ts"].dtype) == "datetime64[us, UTC]"
    assert str(frame["ingested_at"].dtype) == "datetime64[us, UTC]"
    # The instant is unchanged by the conversion, only its rendering.
    assert frame["ts"].iloc[0] == pd.Timestamp("2023-06-15 00:00", tz="UTC")
