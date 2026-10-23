"""Shared fixtures: a synthetic Parquet store, and the policing of holdout unlocks.

``_forbid_holdout_unlock`` is what keeps the suite honest. The holdout is sealed, and a test
that unlocks it without saying so would let every later test inherit that access unnoticed, so
an unmarked test that unlocks it is failed rather than trusted. It fails an unmarked test that
reads past the repository's own ``dev_end`` too, however it got there: reaching for the keys is
one way past the seal, and handing ``load_panel`` a ``Settings`` with a later boundary was
another.

``_clean_volab_environment`` is the other half of that: the police asks ``load_settings`` where
the boundary is, so the boundary must not be settable from outside the suite.
"""

from __future__ import annotations

import datetime as dt
import sys
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
        ts: dt.datetime | None = None,
        repeat: int = 1,
    ) -> list[Path]:
        """Write one Parquet file per date and return the paths written.

        ``dataset`` is the source name for ``Tier.RAW`` and the feature set for
        ``Tier.FEATURES``. ``as_of`` maps each partition date to the row's ``as_of`` (identity by
        default; returning ``None`` writes a null). ``drop_columns`` omits mandatory columns and
        ``ts_naive`` writes ``ts`` without a timezone, both to provoke schema errors.

        ``ts`` pins every row's ``ts`` instead of deriving it from the partition date, so that
        two vintages of the same observation can be written into different ``date=`` partitions.
        ``repeat`` writes the same row ``repeat`` times into the one file, which is the only way
        a store can hold rows tying on all of ``(symbol, ts, as_of, ingested_at)``.
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
        if repeat < 1:
            raise ValueError(f"repeat must be at least 1, got {repeat}")

        for day in dates:
            midnight = dt.datetime(day.year, day.month, day.day)
            default_ts = midnight if ts_naive else midnight.replace(tzinfo=dt.UTC)
            point = default_ts if ts is None else ts
            frame = pd.DataFrame(
                {
                    "symbol": [symbol] * repeat,
                    "ts": [point] * repeat,
                    "as_of": [choose_as_of(day)] * repeat,
                    "ingested_at": [stamp] * repeat,
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
        exogenous=("VIX", "VIX9D", "VIX1D", "VVIX", "BAA10Y", "DGS10", "T10Y2Y"),
        calendar="XNYS",
        splits=Splits(dev_end=dt.date(2023, 12, 31), holdout_start=dt.date(2024, 1, 2)),
        storage_root=tmp_path,
    )
    return SyntheticStore(root=settings.storage_root, settings=settings)


@pytest.fixture(autouse=True)
def _clean_volab_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every ``VOLAB_*`` configuration override, for every test.

    ``VOLAB_SPLITS_DEV_END`` is the one that matters. The fixture below asks ``load_settings``
    for the repository's own boundary, so a variable that moved it would move the police with
    it, and every assertion the suite makes about the seal would be made against a boundary
    chosen from outside the suite. ``volab.config`` refuses a *later* one now. An earlier one
    is legitimate configuration and is cleared anyway, but it is the harmless direction: it
    *widens* what counts as sealed, so the police becomes over-strict and fails closed. Noisy,
    never silent.

    The others are cleared for the ordinary reason: a test that reads the configuration must
    see the committed file, not a developer's shell. The names come from ``_ENV_OVERRIDES``
    rather than being copied, so a setting added there cannot be forgotten here.

    ``VOLAB_HOLDOUT_UNLOCK`` is deliberately not in that map -- it is a key, not a setting --
    and ``_forbid_holdout_unlock`` deals with it per test instead.
    """
    from volab.config import _ENV_OVERRIDES

    for variable in _ENV_OVERRIDES.values():
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture(autouse=True)
def _forbid_holdout_unlock(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    _clean_volab_environment: None,
) -> Iterator[None]:
    """Fail any unmarked test that unlocks the holdout, or that reads past it regardless.

    Function-scoped, and it must be: it depends on ``monkeypatch``, which is function-scoped.
    """
    if request.node.get_closest_marker("holdout") is not None:
        yield
        return

    # Lazy, so importing this conftest does not import the data layer; never guarded, because a
    # policing fixture that fails open on an import error is worse than no policing at all.
    # ``_UNLOCK_ENV`` is read from the module rather than copied, so the two cannot drift.
    import volab.store as store
    from volab.config import load_settings

    monkeypatch.delenv(store._UNLOCK_ENV, raising=False)

    original_unlock = store._unlock_granted

    def refuse_unmarked_unlock(allow_holdout: bool) -> bool:
        if original_unlock(allow_holdout):
            pytest.fail("test unlocked the holdout without @pytest.mark.holdout")
        return False

    monkeypatch.setattr(store, "_unlock_granted", refuse_unmarked_unlock)

    # Wrapping ``_unlock_granted`` polices whoever reaches for the keys. It is blind to a test
    # that moves the boundary instead -- passing a hand-built ``Settings`` whose ``dev_end`` is
    # later than the repository's -- because no key is ever asked for. ``volab.store`` refuses
    # that itself now; this is the suite-side half of the same fix. Judged on what came back
    # rather than on the argument, so the tests whose whole purpose is to prove a holdout read
    # is *refused* stay expressible: those raise, and never reach the check.
    repository_dev_end = load_settings().splits.dev_end
    original_load_panel = store.load_panel

    def police_holdout_read(*args: object, **kwargs: object) -> object:
        frame = original_load_panel(*args, **kwargs)  # type: ignore[arg-type]
        end = kwargs.get("end")
        if isinstance(end, dt.date) and end > repository_dev_end:
            pytest.fail(
                f"test read past the repository's dev_end {repository_dev_end} with end={end} "
                "and no @pytest.mark.holdout"
            )
        return frame

    # Every binding of the function, not only ``volab.store``'s: a test module that did
    # ``from volab.store import load_panel`` holds its own reference, taken at collection time,
    # which an attribute patch on the module alone would not reach. The probe is guarded because
    # a module-level ``__getattr__`` (PEP 562) may raise anything it likes, and one unlucky
    # third-party module would otherwise break every test at fixture setup.
    for module in list(sys.modules.values()):
        try:
            bound = getattr(module, "load_panel", None)
        except Exception:
            continue
        if bound is original_load_panel:
            monkeypatch.setattr(module, "load_panel", police_holdout_read)
    yield
