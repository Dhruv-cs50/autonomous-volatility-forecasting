"""Behaviour of the single sanctioned read path, ``load_panel``.

The tests are grouped by the check order in ``volab.store.load_panel``, because which error
surfaces first is part of the contract: a mislabelled partition must not be reported as missing
data, and the holdout boundary must be refused before anything is read off disk.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from volab import store
from volab.config import Splits
from volab.errors import DatasetNotFound, HoldoutViolation, SchemaError, UnknownSymbol
from volab.paths import MANDATORY_COLUMNS, Tier, curated_path
from volab.store import AUDIT_FILENAME, load_panel

DEV_DAY = dt.date(2023, 6, 15)
HOLDOUT_DAY = dt.date(2024, 3, 1)
DAY = dt.timedelta(days=1)
UNLOCK_ENV = "VOLAB_HOLDOUT_UNLOCK"


@pytest.mark.parametrize("tier", list(Tier), ids=lambda t: t.value)
def test_dev_window_read_returns_rows(synthetic_store, tier: Tier) -> None:
    """Every tier is readable, not just the curated default.

    Each raw part file carries its own ``ingested_at`` stamp in its name; the two dates land in
    separate ``date=`` partitions, so the default stamp is enough for both.
    """
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY - DAY, DEV_DAY], tier=tier)

    frame = load_panel("bars_daily", end=DEV_DAY, tier=tier, settings=synthetic_store.settings)

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


# A list, a tuple, and two pandas sequences whose truthiness is ambiguous -- which is why
# ``load_panel`` judges ``columns`` by ``len`` and not by ``bool``. numpy would be the
# obvious fourth, but it is only a transitive dependency of pandas here and this suite does
# not import it; ``pd.Series`` raises on ``bool()`` just as an ndarray does.
@pytest.mark.parametrize("empty", [[], (), pd.Index([]), pd.Series([], dtype=str)], ids=type)
def test_empty_columns_raises_value_error(synthetic_store, empty) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    with pytest.raises(ValueError, match="columns must not be empty"):
        load_panel("bars_daily", end=DEV_DAY, columns=empty, settings=synthetic_store.settings)


def test_bare_string_columns_raises_value_error(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    with pytest.raises(ValueError, match="not a bare string"):
        load_panel("bars_daily", end=DEV_DAY, columns="symbol", settings=synthetic_store.settings)


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
    assert record["outcome"] == "success"
    assert set(record) == {
        "ts",
        "outcome",
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


def test_the_audit_stamp_never_takes_gits_index_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every git call in the audit stamp passes ``--no-optional-locks``.

    Without it, ``git status`` takes ``index.lock`` to refresh the index, and the stamp's own
    timeout can kill git mid-refresh and leave a zero-byte lock that blocks later commits. A
    suite run would then be a source of the locks it has to work around, which is how this was
    found. Asserted on the argv rather than on behaviour, because reproducing the contention
    needs a concurrent committer.
    """
    seen: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = "deadbeef"

    def fake_run(argv: list[str], **kwargs: object) -> Completed:
        seen.append(argv)
        return Completed()

    monkeypatch.setattr(store.subprocess, "run", fake_run)
    store._git_state()

    assert seen, "the stamp made no git call at all"
    for argv in seen:
        assert argv[0] == "git"
        assert argv[1] == "--no-optional-locks", f"{argv} can take index.lock"


def test_dev_window_read_writes_no_audit_line(synthetic_store) -> None:
    # Deliberately unmarked: this read must stay locked, so the policing fixture should police it.
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)

    assert not (synthetic_store.settings.storage_root / AUDIT_FILENAME).exists()


def test_dedup_keeps_the_latest_as_of_vintage(synthetic_store) -> None:
    """``as_of`` is the vintage axis, so the latest vintage at or before ``end`` must win.

    The superseded vintage is ingested *later* than the revision here, which is what a backfill
    looks like: ordering on ``ingested_at`` alone would keep the wrong row.
    """
    revision = DEV_DAY
    superseded = DEV_DAY - DAY
    point = dt.datetime(2023, 6, 1, tzinfo=dt.UTC)
    synthetic_store.write(
        "vendor_bars",
        "SPY",
        [superseded],
        tier=Tier.RAW,
        ts=point,
        ingested_at=dt.datetime(2025, 1, 2, tzinfo=dt.UTC),
    )
    synthetic_store.write(
        "vendor_bars",
        "SPY",
        [revision],
        tier=Tier.RAW,
        ts=point,
        ingested_at=dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
    )

    frame = load_panel("vendor_bars", end=DEV_DAY, tier=Tier.RAW, settings=synthetic_store.settings)

    assert len(frame) == 1
    assert frame["as_of"].iloc[0].date() == revision


def test_rows_tying_on_every_key_raise_schema_error(synthetic_store) -> None:
    """A store that cannot say which of two identical-keyed rows is current is corrupt.

    Keeping one silently would be truncation for a dataset whose true grain is finer than
    ``(symbol, ts)``, which is exactly the silently-wrong number this layer exists to refuse.
    """
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY], repeat=2)

    with pytest.raises(SchemaError, match="SPY"):
        load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)


def test_implicit_symbols_read_only_the_configured_universe(synthetic_store) -> None:
    """``symbols=None`` means the universe, not whatever directories happen to exist.

    A typo'd or decommissioned ticker directory must not silently join the panel. Only one of
    the three universe symbols has files, which is not an error.
    """
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])
    synthetic_store.write("bars_daily", "TSLA", [DEV_DAY])

    frame = load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)

    assert set(frame["symbol"]) == {"SPY"}


def test_no_universe_symbol_on_disk_raises_dataset_not_found(synthetic_store) -> None:
    synthetic_store.write("bars_daily", "TSLA", [DEV_DAY])

    with pytest.raises(DatasetNotFound, match="no files match"):
        load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)


def test_passed_settings_may_not_move_dev_end_later(synthetic_store) -> None:
    """The guard assumes ``Settings`` came from ``load_settings``; here it enforces that.

    Deliberately unmarked: moving the boundary is not unlocking it, so no key is held and
    nothing may be read past the repository's own ``dev_end``.
    """
    synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY])
    moved = dataclasses.replace(
        synthetic_store.settings,
        splits=Splits(dev_end=dt.date(2099, 12, 31), holdout_start=dt.date(2100, 1, 2)),
    )

    with pytest.raises(HoldoutViolation, match="later than the repository"):
        load_panel("bars_daily", end=HOLDOUT_DAY, settings=moved)


def test_passed_settings_may_move_dev_end_earlier(synthetic_store) -> None:
    """An earlier ``dev_end`` is more conservative, so it is allowed."""
    earlier = dataclasses.replace(
        synthetic_store.settings,
        splits=Splits(dev_end=dt.date(2023, 6, 30), holdout_start=dt.date(2023, 7, 3)),
    )
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])

    assert len(load_panel("bars_daily", end=DEV_DAY, settings=earlier)) == 1


@pytest.mark.holdout
def test_refused_unlocked_read_still_writes_one_audit_line(
    synthetic_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed holdout read is still an attempt on the sealed window, so it is recorded.

    Without this, a key-holder could probe the existence, emptiness and schema of post-
    ``dev_end`` data without end while the log's line count -- the number the write-up must
    report -- stayed clean.
    """
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY])
    monkeypatch.setenv(UNLOCK_ENV, "1")

    with pytest.raises(DatasetNotFound, match="no rows"):
        load_panel(
            "bars_daily",
            start=HOLDOUT_DAY,
            end=HOLDOUT_DAY,
            allow_holdout=True,
            settings=synthetic_store.settings,
        )

    audit = synthetic_store.settings.storage_root / AUDIT_FILENAME
    lines = audit.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["outcome"] == "DatasetNotFound"
    assert record["end"] == HOLDOUT_DAY.isoformat()


def test_hive_partition_value_wins_over_a_same_named_file_column(synthetic_store) -> None:
    """Step 9 rests on DuckDB preferring the Hive ``date`` value to a file column of that name.

    The file claims ``date == as_of``, which would make the comparison vacuously true; its
    ``date=`` partition says otherwise. The third-party precedence rule is what makes the
    mismatch visible, so it is pinned here against future DuckDB upgrades.
    """
    path = curated_path(synthetic_store.root, "bars_daily", "SPY", DEV_DAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "symbol": pa.array(["SPY"], pa.string()),
                "ts": pa.array(
                    [dt.datetime(2024, 3, 1, tzinfo=dt.UTC)], pa.timestamp("us", tz="UTC")
                ),
                "as_of": pa.array([HOLDOUT_DAY], pa.date32()),
                "ingested_at": pa.array(
                    [dt.datetime(2025, 1, 1, tzinfo=dt.UTC)], pa.timestamp("us", tz="UTC")
                ),
                "date": pa.array([HOLDOUT_DAY], pa.date32()),
            }
        ),
        path,
    )

    with pytest.raises(HoldoutViolation, match="partition"):
        load_panel("bars_daily", end=DEV_DAY, settings=synthetic_store.settings)


def test_repeated_symbol_reads_the_same_rows_as_a_single_one(synthetic_store) -> None:
    """A symbol named twice is a caller's slip, not a corrupt store.

    ``dataset_glob`` emits one pattern per symbol, so a repeated symbol yields two identical
    patterns; were the expansion not a set, DuckDB would read every file twice and every row
    would tie on the whole key, accusing the store of a corruption that is not there.
    """
    synthetic_store.write("bars_daily", "SPY", [DEV_DAY - DAY, DEV_DAY])

    once = load_panel("bars_daily", end=DEV_DAY, symbols=["SPY"], settings=synthetic_store.settings)
    twice = load_panel(
        "bars_daily", end=DEV_DAY, symbols=["SPY", "SPY"], settings=synthetic_store.settings
    )

    assert len(twice) == 2
    pd.testing.assert_frame_equal(once, twice)
