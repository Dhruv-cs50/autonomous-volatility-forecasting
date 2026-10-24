"""``python -m volab.features build`` -- the one code path in the features package on the store.

The engine is pure and tested elsewhere; what is left to prove here is the wiring, and the wiring
is where the holdout seal could be quietly defeated: a read that stops past ``dev_end``, an
``--end`` argument, a feature row written under the wrong ``as_of``. Each test below is about one
of those, or about the state the repository is actually in -- FRED has never been ingested, so
a missing exogenous dataset has to be tolerated rather than assumed away.

The store is synthetic and deliberately short (about 32 sessions before ``dev_end``), with a
handful of sessions written *after* it. That second part is the point: the CLI is handed a store
that holds the future, and everything it writes must still stop at ``dev_end``.

``RETURNS`` is ``0.01 * (k + 1)``, so every squared return is distinct and a window shifted by a
single session changes the answer; expected target values are written out by hand.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import exchange_calendars as xcals
import pandas as pd
import pytest

import volab.features.__main__ as cli
import volab.panel as panel_module
from volab.errors import DatasetNotFound
from volab.features.build import COLUMNS, FRED_SERIES
from volab.features.targets import HORIZONS, target_column, targets_dataset
from volab.paths import Tier
from volab.sessions import sessions_between
from volab.store import load_panel, write_curated

if TYPE_CHECKING:
    from conftest import SyntheticStore

# Built once: constructing XNYS is slow enough that per-test construction shows up in the suite.
CAL = xcals.get_calendar("XNYS")
INGESTED = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
FEATURE_SET = "har_daily"
UNIVERSE = ("SPY", "QQQ", "IWM")
VIX_FAMILY = ("VIX", "VIX9D", "VIX1D", "VVIX")

# dev_end of the synthetic store is 2023-12-31, so the last dev session is 2023-12-29. The five
# sessions after it are written to the store and must never reach an output.
ALL_SESSIONS = sessions_between(CAL, dt.date(2023, 11, 14), dt.date(2024, 1, 8))
DEV_END = dt.date(2023, 12, 31)
DEV_SESSIONS = [day for day in ALL_SESSIONS if day <= DEV_END]
FUTURE_SESSIONS = [day for day in ALL_SESSIONS if day > DEV_END]
RETURNS = [0.01 * (k + 1) for k in range(len(ALL_SESSIONS) - 1)]


def _closes(returns: Sequence[float]) -> list[float]:
    closes = [100.0]
    for value in returns:
        closes.append(closes[-1] * math.exp(value))
    return closes


CLOSES = _closes(RETURNS)


def _stamp(day: dt.date) -> pd.Timestamp:
    return pd.Timestamp(dt.datetime(day.year, day.month, day.day, 21, tzinfo=dt.UTC))


def _write_series(
    store: SyntheticStore,
    dataset: str,
    symbol: str,
    days: Sequence[dt.date],
    values: Sequence[float],
    *,
    column: str,
    distribution: bool = False,
    extra: Sequence[str] = (),
) -> None:
    """One curated single-row partition per session, as ingestion lays them out."""
    for day, value in zip(days, values, strict=True):
        row = {
            "symbol": [symbol],
            "ts": [_stamp(day)],
            "as_of": [day],
            "ingested_at": [pd.Timestamp(INGESTED)],
            column: [value],
        }
        if distribution:
            row["has_distribution"] = [False]
        for name in extra:
            row[name] = [value]
        write_curated(
            pd.DataFrame(row), dataset=dataset, symbol=symbol, as_of=day, settings=store.settings
        )


def _populate(store: SyntheticStore, *, skip: dict[str, dt.date] | None = None) -> None:
    """Curated bars for the universe and the VIX family, over past and future sessions alike."""
    skipped = {} if skip is None else skip
    for symbol in UNIVERSE:
        days = [day for day in ALL_SESSIONS if skipped.get(symbol) != day]
        closes = [CLOSES[ALL_SESSIONS.index(day)] for day in days]
        _write_series(store, "bars_daily", symbol, days, closes, column="close", distribution=True)
    for offset, symbol in enumerate(VIX_FAMILY):
        levels = [10.0 * (offset + 1) + k for k in range(len(ALL_SESSIONS))]
        _write_series(store, "vix_family", symbol, ALL_SESSIONS, levels, column="close")


@pytest.fixture
def store(synthetic_store: SyntheticStore, monkeypatch: pytest.MonkeyPatch) -> SyntheticStore:
    """A populated store, with the CLI pointed at it instead of at the repository's data."""
    _populate(synthetic_store)
    monkeypatch.setattr(cli, "load_settings", lambda: synthetic_store.settings)
    return synthetic_store


def _read(store: SyntheticStore, dataset: str, symbol: str) -> pd.DataFrame:
    return load_panel(
        dataset,
        end=store.settings.splits.dev_end,
        symbols=[symbol],
        tier=Tier.FEATURES,
        settings=store.settings,
    )


def _partition_dates(store: SyntheticStore, dataset: str, symbol: str) -> list[dt.date]:
    base = Path(store.root) / "features" / dataset / symbol
    return sorted(dt.date.fromisoformat(path.name.removeprefix("date=")) for path in base.iterdir())


def test_build_writes_one_feature_partition_per_session_through_dev_end(
    store: SyntheticStore,
) -> None:
    assert cli.main(["build"]) == 0

    for symbol in UNIVERSE:
        assert _partition_dates(store, FEATURE_SET, symbol) == DEV_SESSIONS


def test_the_feature_table_carries_the_full_column_set(store: SyntheticStore) -> None:
    cli.main(["build"])

    table = _read(store, FEATURE_SET, "SPY")

    assert list(table.columns) == list(COLUMNS)
    assert len(table) == len(DEV_SESSIONS)
    assert set(table["symbol"]) == {"SPY"}


def test_the_feature_values_come_from_the_curated_panel(store: SyntheticStore) -> None:
    """The VIX family is joined on, with each series landing in its own column."""
    cli.main(["build"])

    last = _read(store, FEATURE_SET, "QQQ").iloc[-1]
    offset = len(DEV_SESSIONS) - 1

    assert last["iv_h22"] == pytest.approx(10.0 + offset)  # VIX
    assert last["iv_h5"] == pytest.approx(20.0 + offset)  # VIX9D
    assert last["iv_h1"] == pytest.approx(30.0 + offset)  # VIX1D
    assert last["vvix"] == pytest.approx(40.0 + offset)
    # The rv_proxy_d1 of the last session is its own squared return, written out by hand.
    assert last["rv_proxy_d1"] == pytest.approx(RETURNS[offset - 1] ** 2)


def test_every_read_stops_at_dev_end(
    store: SyntheticStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever the CLI reads, it reads with ``end=dev_end`` -- the store holds the future.

    Patched on ``volab.panel``, which is where the reads are since the assembly was shared with
    the walk-forward and the truncation-equivalence tests. That is the one binding every read on
    the CLI's behalf goes through, so recording it there also covers the ones it delegates.
    """
    calls: list[dict[str, object]] = []
    original = panel_module.load_panel

    def recording(*args: object, **kwargs: object) -> pd.DataFrame:
        calls.append(dict(kwargs))
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(panel_module, "load_panel", recording)

    cli.main(["build"])

    assert calls, "the CLI never read the store"
    assert {call["end"] for call in calls} == {store.settings.splits.dev_end}
    assert all(not call.get("allow_holdout") for call in calls)


def test_nothing_past_dev_end_is_written(store: SyntheticStore) -> None:
    """Features and targets alike: the store held five later sessions, none may reach an output."""
    cli.main(["build"])

    for symbol in UNIVERSE:
        datasets = [FEATURE_SET, *(targets_dataset(horizon) for horizon in (1, 5, 22))]
        for dataset in datasets:
            dates = _partition_dates(store, dataset, symbol)
            assert dates, f"{dataset}/{symbol} wrote nothing"
            assert max(dates) <= DEV_END, f"{dataset}/{symbol} reached {max(dates)}"
    assert not set(FUTURE_SESSIONS) & set(_partition_dates(store, FEATURE_SET, "SPY"))


@pytest.mark.parametrize(
    "argument",
    [
        ["build", "--end", "2024-01-05"],
        ["build", "--allow-holdout"],
        ["build", "--unlock"],
        ["build", "--through", "2024-01-05"],
    ],
)
def test_there_is_no_way_to_ask_for_a_later_end(
    argument: list[str], store: SyntheticStore, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(argument)

    assert raised.value.code == 2
    assert not (Path(store.root) / "features").exists()


def test_the_only_verb_is_build(store: SyntheticStore) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["backfill"])

    assert raised.value.code == 2


def test_a_missing_fred_dataset_is_skipped_reported_and_leaves_null_columns(
    store: SyntheticStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """The state the repository is actually in: ``curate_fred`` has never run on real data."""
    with pytest.raises(DatasetNotFound):
        load_panel("fred_daily", end=DEV_END, settings=store.settings)

    assert cli.main(["build"]) == 0

    output = capsys.readouterr().out
    assert "fred_daily" in output
    assert "skipped" in output
    table = _read(store, FEATURE_SET, "SPY")
    for column in FRED_SERIES:
        assert column in table.columns
        assert table[column].isna().all(), f"{column} should be null without FRED"
    # The rest of the table is unaffected by the absence.
    assert table["iv_h22"].notna().all()


def test_present_fred_series_are_joined_onto_the_table(store: SyntheticStore) -> None:
    """The other half of the pin: when FRED exists the CLI reads it and the levels arrive."""
    levels = {"BAA10Y": 1.5, "DGS10": 4.0, "T10Y2Y": -0.5}
    for series, level in levels.items():
        _write_series(
            store,
            "fred_daily",
            series,
            ALL_SESSIONS,
            [level] * len(ALL_SESSIONS),
            column="value",
        )

    cli.main(["build"])

    table = _read(store, FEATURE_SET, "SPY")
    assert table["baa10y"].eq(1.5).all()
    assert table["dgs10"].eq(4.0).all()
    assert table["t10y2y"].eq(-0.5).all()


def test_targets_are_written_for_every_symbol_and_horizon_with_a_complete_window(
    store: SyntheticStore,
) -> None:
    cli.main(["build"])

    for symbol in UNIVERSE:
        for horizon in (1, 5, 22):
            table = _read(store, targets_dataset(horizon), symbol)
            # The last ``horizon`` base sessions have no complete window, so they have no row.
            assert len(table) == len(DEV_SESSIONS) - horizon
            assert target_column(horizon) in table.columns


def test_target_values_are_the_forward_sums_of_squared_returns(store: SyntheticStore) -> None:
    cli.main(["build"])

    one = _read(store, targets_dataset(1), "SPY")
    five = _read(store, targets_dataset(5), "SPY")

    # Base session index 1 (the first with a return behind it): h=1 is the next squared return.
    first_one = one.iloc[1]
    assert first_one["target_rv_proxy_h1"] == pytest.approx(RETURNS[1] ** 2)
    assert first_one["as_of"] == pd.Timestamp(DEV_SESSIONS[2])
    # h=5 from the same base is the next five squared returns.
    first_five = five.iloc[1]
    expected = sum(RETURNS[k] ** 2 for k in range(1, 6))
    assert first_five["target_rv_proxy_h5"] == pytest.approx(expected)
    assert first_five["as_of"] == pd.Timestamp(DEV_SESSIONS[6])


def test_a_horizon_with_no_complete_window_is_reported_not_crashed(
    store: SyntheticStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """32 sessions hold no 66-session window, and ``write_targets`` refuses an empty frame."""
    assert len(DEV_SESSIONS) <= max(HORIZONS)

    assert cli.main(["build"]) == 0

    assert "targets_rv_h66" in capsys.readouterr().out
    assert not (Path(store.root) / "features" / "targets_rv_h66").exists()


def test_a_missing_bar_is_not_bridged_into_a_two_session_return(
    store: SyntheticStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returns are derived on the calendar's sessions, so a gap poisons the two returns it spans.

    Judged on the panel the CLI hands ``build_targets``, because the output cannot tell the two
    apart: the one window a bridged return would enter starts on the gap session itself, which
    has no row and so no target either way. What the contract requires is that the session after
    the gap carries no return, rather than one over two sessions.
    """
    gap = DEV_SESSIONS[10]
    for symbol in UNIVERSE:
        for path in (Path(store.root) / "curated" / "bars_daily" / symbol).glob(
            f"date={gap.isoformat()}/*"
        ):
            path.unlink()
    panels: list[pd.DataFrame] = []
    original = cli.build_targets

    def recording(panel: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
        panels.append(panel)
        return original(panel, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "build_targets", recording)

    cli.main(["build"])

    panel = panels[0]
    returns = dict(zip(pd.to_datetime(panel["as_of"]).dt.date, panel["log_return"], strict=True))
    assert gap not in returns
    assert math.isnan(returns[DEV_SESSIONS[11]]), "the return after a gap was bridged"
    assert returns[DEV_SESSIONS[12]] == pytest.approx(RETURNS[11])
    one = _read(store, targets_dataset(1), "SPY")
    bases = {stamp.date() for stamp in one["ts"]}
    assert DEV_SESSIONS[9] not in bases  # its window would hold the gap's missing return
    assert DEV_SESSIONS[11] in bases


def test_exogenous_series_with_different_schemas_are_each_read_on_their_own(
    store: SyntheticStore,
) -> None:
    """The real curated VIX family is not one schema: VIX carries OHLC, VVIX only a close.

    One ``load_panel`` over both fails inside DuckDB with a schema mismatch, so the CLI reads
    each series separately. The synthetic store gives VIX extra columns to reproduce that.
    """
    _write_series(
        store,
        "vix_family",
        "VIX",
        ALL_SESSIONS,
        [10.0 + k for k in range(len(ALL_SESSIONS))],
        column="close",
        extra=("open", "high", "low"),
    )

    assert cli.main(["build"]) == 0

    table = _read(store, FEATURE_SET, "SPY")
    assert table["iv_h22"].notna().all()
    assert table["vvix"].notna().all()


def test_the_run_says_what_it_wrote(
    store: SyntheticStore, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["build"])

    output = capsys.readouterr().out
    assert f"reads stop at dev_end {DEV_END}" in output
    for symbol in UNIVERSE:
        assert symbol in output
    assert FEATURE_SET in output
    assert str(len(DEV_SESSIONS)) in output


def test_exogenous_collapses_two_observations_sharing_a_session(synthetic_store, capsys) -> None:
    """FRED's calendar and XNYS differ, so two observations can share one knowable session.

    Observed once in real BAA10Y data: the NYSE shut on 2025-01-09 for the national day of
    mourning while the bond market did not, so 2025-01-08 and 2025-01-09 both became knowable on
    Friday the 10th. The latest observation wins -- standing on Friday, Thursday's value is the
    newest one known -- and `build_features` still refuses a panel carrying both.
    """
    knowable = dt.date(2023, 6, 20)
    # Both observations share one partition, which is the production shape: the curated writer
    # groups by as_of, so two rows that became knowable on the same session land in one file.
    write_curated(
        pd.DataFrame(
            {
                "symbol": ["BAA10Y", "BAA10Y"],
                "ts": [
                    pd.Timestamp(dt.date(2023, 6, 15), tz="UTC"),
                    pd.Timestamp(dt.date(2023, 6, 16), tz="UTC"),
                ],
                "as_of": [knowable, knowable],
                "ingested_at": [pd.Timestamp("2026-01-05", tz="UTC")] * 2,
                "value": ["1.70", "1.80"],
            }
        ),
        dataset="fred_daily",
        symbol="BAA10Y",
        as_of=knowable,
        settings=synthetic_store.settings,
    )

    frame = cli._exogenous(synthetic_store.settings, "fred_daily", ("BAA10Y",), "value")

    assert frame is not None
    assert len(frame) == 1, "one row per knowable session"
    assert frame["value"].iloc[0] == "1.80", "the later observation supersedes the earlier"
    assert "collapsed 1 superseded observation" in capsys.readouterr().out
