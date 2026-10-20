"""Behaviour of the single sanctioned read path, ``load_panel``.

The tests are grouped by the check order in ``volab.store.load_panel``, because which error
surfaces first is part of the contract: a mislabelled partition must not be reported as missing
data, and the holdout boundary must be refused before anything is read off disk.
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from volab.errors import DatasetNotFound, HoldoutViolation, SchemaError, UnknownSymbol
from volab.paths import MANDATORY_COLUMNS, Tier
from volab.store import AUDIT_FILENAME, load_panel

DEV_DAY = dt.date(2023, 6, 15)
HOLDOUT_DAY = dt.date(2024, 3, 1)
DAY = dt.timedelta(days=1)
UNLOCK_ENV = "VOLAB_HOLDOUT_UNLOCK"


def test_dev_window_read_returns_rows(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY - DAY, DEV_DAY])

    frame = load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)

    assert len(frame) == 2
    assert set(frame["symbol"]) == {"SPY"}


def test_returns_only_rows_at_or_before_end(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY - DAY, DEV_DAY, DEV_DAY + DAY])

    frame = load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)

    assert len(frame) == 2
    assert frame["as_of"].max().date() == DEV_DAY


def test_start_filters_lower_bound(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY - DAY, DEV_DAY, DEV_DAY + DAY])

    frame = load_panel(
        "bars_daily", start=DEV_DAY, end=DEV_DAY + DAY, settings=synthetic_store.settings
    )

    assert len(frame) == 2
    assert frame["as_of"].min().date() == DEV_DAY
    assert frame["as_of"].max().date() == DEV_DAY + DAY


def test_columns_subsets_the_frame(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    frame = load_panel(
        "bars_daily",
        end=DEV_DAY,
        columns=["symbol", "as_of"],
        settings=synthetic_store.settings,
    )

    assert list(frame.columns) == ["symbol", "as_of"]


def test_columns_none_returns_the_contract_columns_without_the_partition_artefact(
    synthetic_store,
) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    frame = load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)

    assert set(frame.columns) == set(MANDATORY_COLUMNS)
    assert "date" not in frame.columns


def test_empty_columns_raises_value_error(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    with pytest.raises(ValueError, match="columns must not be empty"):
        load_panel("bars_daily", end=DEV_DAY, columns=[], settings=synthetic_store.settings)


def test_symbol_outside_universe_raises_unknown_symbol(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    with pytest.raises(UnknownSymbol, match="TSLA"):
        load_panel("bars_daily", end=DEV_DAY, symbols=["TSLA"], settings=synthetic_store.settings)


def test_symbol_in_universe_without_files_raises_dataset_not_found(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    with pytest.raises(DatasetNotFound, match="no files match"):
        load_panel("bars_daily", end=DEV_DAY, symbols=["QQQ"], settings=synthetic_store.settings)


def test_end_after_dev_end_raises_without_keys(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY])

    with pytest.raises(HoldoutViolation, match="past dev_end"):
        load_panel("bars_daily", end=HOLDOUT_DAY, settings=synthetic_store.settings)


def test_allow_holdout_alone_raises(synthetic_store, monkeypatch: pytest.MonkeyPatch) -> None:
    synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY])
    monkeypatch.delenv(UNLOCK_ENV, raising=False)

    with pytest.raises(HoldoutViolation, match="past dev_end"):
        load_panel(
            "bars_daily",
            end=HOLDOUT_DAY,
            allow_holdout=True,
            settings=synthetic_store.settings,
        )


def test_env_var_alone_raises(synthetic_store, monkeypatch: pytest.MonkeyPatch) -> None:
    synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY])
    monkeypatch.setenv(UNLOCK_ENV, "1")

    with pytest.raises(HoldoutViolation, match="past dev_end"):
        load_panel(
            "bars_daily",
            end=HOLDOUT_DAY,
            allow_holdout=False,
            settings=synthetic_store.settings,
        )


def test_holdout_guard_raises_before_touching_filesystem(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary check precedes every read: both I/O helpers are booby-trapped."""

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("load_panel touched the filesystem before the holdout check")

    synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY])
    monkeypatch.setattr("volab.store._matching_files", explode)
    monkeypatch.setattr("volab.store._scan", explode)

    with pytest.raises(HoldoutViolation, match="past dev_end"):
        load_panel("bars_daily", end=HOLDOUT_DAY, settings=synthetic_store.settings)


def test_mislabelled_partition_raises_even_inside_dev_window(synthetic_store) -> None:
    """A file parked under the wrong ``date=`` must fail loudly, not be filtered away.

    The row claims an ``as_of`` in the holdout while sitting in a dev-window partition. Were
    the window filter to run first, the row would vanish and the caller would be told the
    dataset was empty -- a plausible answer derived from corrupt storage.
    """
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY], as_of=lambda d: HOLDOUT_DAY)

    with pytest.raises(HoldoutViolation, match="partition"):
        load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)


def test_missing_mandatory_column_raises_schema_error(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY], drop_columns=("as_of",))

    with pytest.raises(SchemaError, match="missing mandatory column"):
        load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)


def test_null_as_of_raises_holdout_violation(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY], as_of=lambda d: None)

    with pytest.raises(HoldoutViolation, match="null as_of"):
        load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)


def test_naive_ts_raises_schema_error(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY], ts_naive=True)

    with pytest.raises(SchemaError, match="timezone-aware"):
        load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)


def test_unknown_requested_column_raises_schema_error(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    with pytest.raises(SchemaError, match="nope"):
        load_panel("bars_daily", end=DEV_DAY, columns=["nope"], settings=synthetic_store.settings)


def test_reversed_window_raises_value_error(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    with pytest.raises(ValueError, match="precedes start"):
        load_panel(
            "bars_daily",
            start=DEV_DAY,
            end=DEV_DAY - 10 * DAY,
            settings=synthetic_store.settings,
        )


def test_empty_result_raises_dataset_not_found(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    with pytest.raises(DatasetNotFound, match="no rows"):
        load_panel("bars_daily", end=DEV_DAY - 30 * DAY, settings=synthetic_store.settings)


def test_duplicate_rows_are_deduplicated_keeping_latest_ingest(synthetic_store) -> None:
    earlier = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    later = dt.datetime(2025, 1, 2, tzinfo=dt.UTC)
    # Only the raw tier holds several part files per partition, so only it can hold duplicates.
    synthetic_store.write("vendor_bars", "SPY", [DEV_DAY], tier=Tier.RAW, ingested_at=earlier)
    synthetic_store.write("vendor_bars", "SPY", [DEV_DAY], tier=Tier.RAW, ingested_at=later)

    frame = load_panel("vendor_bars", end=DEV_DAY, tier=Tier.RAW, settings=synthetic_store.settings)

    assert len(frame) == 1
    assert frame["ingested_at"].iloc[0] == pd.Timestamp(later)


def test_empty_dataset_directory_raises_dataset_not_found(synthetic_store) -> None:
    (synthetic_store.root / Tier.CURATED / "bars_daily").mkdir(parents=True)

    with pytest.raises(DatasetNotFound, match="no files match"):
        load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)


@pytest.mark.holdout
def test_both_keys_return_holdout_rows_and_write_one_audit_line(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY])
    monkeypatch.setenv(UNLOCK_ENV, "1")

    frame = load_panel(
        "bars_daily",
        end=HOLDOUT_DAY,
        allow_holdout=True,
        settings=synthetic_store.settings,
    )

    assert len(frame) == 1
    audit = synthetic_store.settings.storage_root / AUDIT_FILENAME
    lines = audit.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["dataset"] == "bars_daily"
    assert record["end"] == HOLDOUT_DAY.isoformat()
    assert record["start"] is None
    assert record["tier"] == Tier.CURATED.value
    assert record["caller_module"].endswith("test_store_guard")
    assert set(record) == {
        "ts",
        "dataset",
        "tier",
        "end",
        "start",
        "symbols",
        "caller_module",
        "caller_lineno",
        "git_sha",
        "git_dirty",
    }


def test_dev_window_read_writes_no_audit_line(synthetic_store) -> None:
    # Deliberately unmarked: this read must stay locked, so the policing fixture should police it.
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)

    assert not (synthetic_store.settings.storage_root / AUDIT_FILENAME).exists()
