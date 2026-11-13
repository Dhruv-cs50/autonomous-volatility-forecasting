"""A pseudo-holdout carved inside dev, for witnesses of the reveal that hold no key.

``dev_end`` here is 2024-03-28 and the "holdout" is the ten sessions 2024-04-01..2024-04-12, all
of it far below the repository's own ``dev_end``, so the conftest police have nothing to object to
and no test needs ``@pytest.mark.holdout``. The store mirrors the real one's shape: **raw runs
through ``holdout_end``; curated, features and targets stop at ``dev_end``**. That is the state
the reveal exists to move on from, and building it by the real ``curate_*`` and features build
(at ``dev_end``, which needs no key) means the canary has something to compare.

A plain module and not a fixture file: a fixture of a wider scope may not read the store
(``conftest.pytest_fixture_setup``), and building the dev tiers is a read.
"""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import numpy as np

from volab.config import Settings, Splits
from volab.features.__main__ import _build
from volab.features.build import FRED_SERIES, IV_SERIES, VVIX_SERIES
from volab.ingest._frames import build
from volab.ingest.curate import curate_bars, curate_fred, curate_vix
from volab.sessions import get_calendar, session_close_utc, sessions_between
from volab.store import write_raw

SPLITS = Splits(
    dev_end=dt.date(2024, 3, 28),
    holdout_start=dt.date(2024, 4, 1),
    holdout_end=dt.date(2024, 4, 12),
)
FIRST_SESSION = dt.date(2023, 6, 1)
UNIVERSE = ("SPY", "QQQ", "IWM")
VIX_NAMES = (*IV_SERIES.values(), VVIX_SERIES)
FRED_NAMES = tuple(FRED_SERIES.values())
FETCHED = dt.datetime(2024, 4, 20, 12, 0, tzinfo=dt.UTC)

# Built once per process under a directory pytest does not own, then copied per test: building the
# dev tiers is seconds, and a hundred tests paying it would be the suite's slowest fixture.
_TEMPLATE: list[Path] = []


def settings_at(root: Path) -> Settings:
    """Settings whose storage root is ``root/data``, so the ledgers land at ``root/data/*.jsonl``
    exactly where the repository keeps them and not in pytest's shared base directory."""
    return Settings(
        universe=UNIVERSE,
        exogenous=("VIX", "VIX9D", "VIX1D", "VVIX", "BAA10Y", "DGS10", "T10Y2Y"),
        calendar="XNYS",
        splits=SPLITS,
        storage_root=root / "data",
    )


def _raw(source: str, symbol: str, day: dt.date, payload: dict[str, str], columns: tuple[str, ...]):
    frame = build(
        [
            {
                "ts": session_close_utc(day),
                "as_of": day,
                "vendor_date_text": day.isoformat(),
                **payload,
            }
        ],
        symbol=symbol,
        fetched_at=FETCHED,
        source_url=f"https://example.invalid/{source}/{symbol}",
        payload_columns=columns,
    )
    return frame


def seed_raw(settings: Settings, *, through: dt.date) -> None:
    """Every raw dataset the reveal reads, one partition a session, ``FIRST_SESSION``..``through``.

    No dividends: Nasdaq serves them for QQQ only and the real store has none for the other two,
    so the dividend reads raise ``DatasetNotFound`` here exactly as there. (QQQ's are absent too;
    the count of looks does not depend on it.)
    """
    sessions = sessions_between(get_calendar(settings), FIRST_SESSION, through)
    rng = np.random.default_rng(20240401)
    for symbol in UNIVERSE:
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(sessions))))
        for day, price in zip(sessions, close, strict=True):
            payload = {
                "open": f"{price * 0.999:.4f}",
                "high": f"{price * 1.004:.4f}",
                "low": f"{price * 0.996:.4f}",
                "close": f"{price:.4f}",
                "volume": "1,000,000",
            }
            frame = _raw(
                "nasdaq_bars", symbol, day, payload, ("open", "high", "low", "close", "volume")
            )
            write_raw(
                frame,
                source="nasdaq_bars",
                symbol=symbol,
                as_of=day,
                ingested_at=FETCHED,
                settings=settings,
            )
    for name in VIX_NAMES:
        level = 16.0 + np.cumsum(rng.normal(0.0, 0.3, len(sessions)))
        for day, value in zip(sessions, level, strict=True):
            if name == VVIX_SERIES:
                payload, columns = {"close": f"{value + 70:.3f}"}, ("close",)
            else:
                text = f"{value:.3f}"
                payload = {"open": text, "high": text, "low": text, "close": text}
                columns = ("open", "high", "low", "close")
            frame = _raw("cboe_vix", name, day, payload, columns)
            write_raw(
                frame,
                source="cboe_vix",
                symbol=name,
                as_of=day,
                ingested_at=FETCHED,
                settings=settings,
            )
    for name in FRED_NAMES:
        level = 2.0 + np.cumsum(rng.normal(0.0, 0.02, len(sessions)))
        for day, value in zip(sessions, level, strict=True):
            frame = _raw("fred", name, day, {"value": f"{value:.3f}"}, ("value",))
            write_raw(
                frame, source="fred", symbol=name, as_of=day, ingested_at=FETCHED, settings=settings
            )


def derive_dev_tiers(settings: Settings) -> None:
    """Curate and build features and targets at ``dev_end``: the real tiers, no key needed."""
    end = settings.splits.dev_end
    for symbol in settings.universe:
        curate_bars(symbol, end=end, settings=settings)
    for name in VIX_NAMES:
        curate_vix(name, end=end, settings=settings)
    for name in FRED_NAMES:
        curate_fred(name, end=end, settings=settings)
    _build(settings)


def carved_store(root: Path) -> Settings:
    """A fresh carved store under ``root/data``: raw through ``holdout_end``, dev tiers built."""
    if not _TEMPLATE:
        import tempfile

        template = Path(tempfile.mkdtemp(prefix="volab-carved-"))
        built = settings_at(template)
        seed_raw(built, through=SPLITS.holdout_end)
        derive_dev_tiers(built)
        _TEMPLATE.append(template / "data")
    settings = settings_at(root)
    shutil.copytree(_TEMPLATE[0], settings.storage_root)
    return settings
