"""Shared fixtures: a synthetic Parquet store, and the policing of holdout unlocks.

``_forbid_holdout_unlock`` is what keeps the suite honest. The holdout is sealed, and a test
that unlocks it without saying so would let every later test inherit that access unnoticed, so
an unmarked test that unlocks it is failed rather than trusted.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from volab.config import Settings, Splits
from volab.paths import MANDATORY_COLUMNS, Tier, curated_path, features_path, raw_path

# Fixed so written files are reproducible. Later than any date a test is likely to write.
DEFAULT_INGESTED_AT = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)

_UNLOCK_ENV = "VOLAB_HOLDOUT_UNLOCK"


@dataclass
class SyntheticStore:
    """A small Parquet store under a temporary root, in the layout ``volab.paths`` defines."""

    root: Path
    settings: Settings

    def write(
        self,
        dataset: str,
        symbol: str,
        dates: Iterable[dt.date],
        *,
        tier: Tier = Tier.CURATED,
        as_of: Callable[[dt.date], dt.date | None] | None = None,
        drop_columns: Iterable[str] = (),
        ts_naive: bool = False,
        ingested_at: dt.datetime | None = None,
    ) -> list[Path]:
        """Write one single-row Parquet file per date and return the paths written.

        ``dataset`` is the source name for ``Tier.RAW`` and the feature set for
        ``Tier.FEATURES``. ``as_of`` maps each partition date to the row's ``as_of`` (identity by
        default; returning ``None`` writes a null). ``drop_columns`` omits mandatory columns and
        ``ts_naive`` writes ``ts`` without a timezone, both to provoke schema errors.
        """
        dropped = tuple(drop_columns)
        unknown = sorted(set(dropped) - set(MANDATORY_COLUMNS))
        if unknown:
            raise ValueError(f"drop_columns must be mandatory columns, got {unknown}")
        stamp = DEFAULT_INGESTED_AT if ingested_at is None else ingested_at
        if stamp.tzinfo is None:
            raise ValueError(f"ingested_at must be timezone-aware, got {stamp!r}")
        choose_as_of = (lambda d: d) if as_of is None else as_of
        schema = pa.schema(
            [
                ("symbol", pa.string()),
                ("ts", pa.timestamp("us") if ts_naive else pa.timestamp("us", tz="UTC")),
                ("as_of", pa.date32()),
                ("ingested_at", pa.timestamp("us", tz="UTC")),
            ]
        )

        written: list[Path] = []
        for day in dates:
            midnight = dt.datetime(day.year, day.month, day.day)
            frame = pd.DataFrame(
                {
                    "symbol": [symbol],
                    "ts": [midnight if ts_naive else midnight.replace(tzinfo=dt.UTC)],
                    "as_of": [choose_as_of(day)],
                    "ingested_at": [stamp],
                }
            )
            table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
            table = table.select([c for c in MANDATORY_COLUMNS if c not in dropped])
            path = self._path(tier, dataset, symbol, day, stamp)
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, path)
            written.append(path)
        return written

    def _path(
        self, tier: Tier, dataset: str, symbol: str, day: dt.date, ingested_at: dt.datetime
    ) -> Path:
        if tier is Tier.RAW:
            return raw_path(self.root, dataset, symbol, day, ingested_at)
        if tier is Tier.FEATURES:
            return features_path(self.root, dataset, symbol, day)
        return curated_path(self.root, dataset, symbol, day)


@pytest.fixture
def synthetic_store(tmp_path: Path) -> SyntheticStore:
    # Built directly rather than via load_settings: that insists the storage root lies inside
    # the repository, which a temporary directory by design does not.
    settings = Settings(
        universe=("SPY", "QQQ", "IWM"),
        calendar="XNYS",
        splits=Splits(dev_end=dt.date(2023, 12, 31), holdout_start=dt.date(2024, 1, 2)),
        storage_root=tmp_path,
    )
    return SyntheticStore(root=settings.storage_root, settings=settings)


@pytest.fixture(autouse=True)
def _forbid_holdout_unlock(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Fail any test that unlocks the holdout without carrying ``@pytest.mark.holdout``."""
    if request.node.get_closest_marker("holdout") is not None:
        yield
        return

    monkeypatch.delenv(_UNLOCK_ENV, raising=False)
    # Lazy, so importing this conftest does not import the data layer; never guarded, because a
    # policing fixture that fails open on an import error is worse than no policing at all.
    import volab.store as store

    original = store._unlock_granted

    def refuse_unmarked_unlock(allow_holdout: bool) -> bool:
        if original(allow_holdout):
            pytest.fail("test unlocked the holdout without @pytest.mark.holdout")
        return False

    monkeypatch.setattr(store, "_unlock_granted", refuse_unmarked_unlock)
    yield
