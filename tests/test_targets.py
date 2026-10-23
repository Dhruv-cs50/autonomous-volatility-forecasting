"""Forward realized-variance targets, and the ``as_of`` stamp that keeps them out of features.

A target is the one quantity in this project that is *meant* to look ahead. These tests defend
the half of that which is structural: a target row's ``as_of`` is the session its last return
becomes known, so ``load_panel(..., end=E)`` can only ever hand back a realised one.

Most expected values are written out by hand rather than recomputed by the code under test.
``RETURNS`` is ``r_k = 0.01 * (k + 1)`` over a fixed run of sessions, so every squared return is
distinct and a window shifted by a single session changes the answer.
"""

from __future__ import annotations

import datetime as dt

import exchange_calendars as xcals
import pandas as pd
import pytest

from volab.errors import SchemaError
from volab.features.targets import (
    HORIZONS,
    build_targets,
    forward_rv_proxy,
    targets_dataset,
    write_targets,
)
from volab.paths import Tier
from volab.sessions import sessions_between
from volab.store import load_panel

# Built once: constructing XNYS is slow enough that per-test construction shows up in the suite.
CAL = xcals.get_calendar("XNYS")
INGESTED = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)

# 2023-12-20 .. 2024-01-10: 14 sessions, spanning Christmas, New Year's Day and a year boundary.
#   idx: 0=12-20 1=12-21 2=12-22 3=12-26 4=12-27 5=12-28 6=12-29
#        7=01-02 8=01-03 9=01-04 10=01-05 11=01-08 12=01-09 13=01-10
SESSIONS = sessions_between(CAL, dt.date(2023, 12, 20), dt.date(2024, 1, 10))
RETURNS = [0.01 * (k + 1) for k in range(len(SESSIONS))]


def _panel(
    days: list[dt.date],
    returns: list[float],
    *,
    symbol: str = "SPY",
    ingested: list[dt.datetime] | None = None,
) -> pd.DataFrame:
    stamps = [INGESTED] * len(days) if ingested is None else ingested
    return pd.DataFrame(
        {
            "symbol": [symbol] * len(days),
            "ts": pd.to_datetime([dt.datetime(d.year, d.month, d.day) for d in days]).tz_localize(
                "UTC"
            ),
            "as_of": days,
            "ingested_at": pd.to_datetime(stamps),
            "log_return": returns,
        }
    )


def _by_day(frame: pd.DataFrame, column: str) -> dict[dt.date, object]:
    return {ts.date(): value for ts, value in zip(frame["ts"], frame[column], strict=True)}


def test_horizons_are_1_5_22_66() -> None:
    assert HORIZONS == (1, 5, 22, 66)


def test_targets_dataset_names_the_horizon() -> None:
    assert targets_dataset(22) == "targets_rv_h22"


def test_forward_rv_sums_the_following_returns() -> None:
    r = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02])
    out = forward_rv_proxy(r, horizon=2)
    # The value at i sums returns i+1..i+2 -- not i..i+1, which is the off-by-one to catch.
    assert out.iloc[0] == pytest.approx(0.02**2 + 0.03**2)
    assert out.iloc[1] == pytest.approx(0.03**2 + 0.01**2)
    assert out.iloc[2] == pytest.approx(0.01**2 + 0.02**2)


def test_forward_rv_is_nan_at_the_tail() -> None:
    r = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02])
    out = forward_rv_proxy(r, horizon=3)
    assert out.iloc[0] == pytest.approx(0.02**2 + 0.03**2 + 0.01**2)
    assert out.iloc[1] == pytest.approx(0.03**2 + 0.01**2 + 0.02**2)
    assert out.iloc[2:].isna().all()


def test_forward_rv_horizon_one_is_the_next_squared_return() -> None:
    out = forward_rv_proxy(pd.Series([0.01, -0.02, 0.03]), horizon=1)
    assert out.iloc[0] == pytest.approx(0.02**2)
    assert out.iloc[1] == pytest.approx(0.03**2)
    assert pd.isna(out.iloc[2])


def test_forward_rv_keeps_the_index() -> None:
    r = pd.Series([0.01, 0.02, 0.03, 0.04], index=[10, 20, 30, 40])
    assert list(forward_rv_proxy(r, horizon=1).index) == [10, 20, 30, 40]


def test_forward_rv_nan_return_poisons_every_window_holding_it() -> None:
    r = pd.Series([0.01, 0.02, float("nan"), 0.04, 0.05, 0.06])
    out = forward_rv_proxy(r, horizon=2)
    # r[2] is inside the windows of i=0 (1..2) and i=1 (2..3); never a partial sum.
    assert out.iloc[0:2].isna().all()
    assert out.iloc[2] == pytest.approx(0.04**2 + 0.05**2)
    assert out.iloc[3] == pytest.approx(0.05**2 + 0.06**2)


@pytest.mark.parametrize("horizon", [0, -1])
def test_forward_rv_rejects_a_horizon_below_one(horizon: int) -> None:
    with pytest.raises(ValueError, match="horizon"):
        forward_rv_proxy(pd.Series([0.01, 0.02]), horizon=horizon)


def test_target_value_is_the_hand_computed_forward_sum() -> None:
    out = build_targets(_panel(SESSIONS, RETURNS), horizon=2, calendar=CAL)
    values = _by_day(out, "target_rv_proxy_h2")
    # Base 2023-12-29 is idx 6, so its window is idx 7 and 8: r = 0.08 and 0.09.
    assert values[dt.date(2023, 12, 29)] == pytest.approx(0.08**2 + 0.09**2)
    # Base 2023-12-20 is idx 0: r = 0.02 and 0.03.
    assert values[dt.date(2023, 12, 20)] == pytest.approx(0.02**2 + 0.03**2)


def test_target_column_is_named_for_the_horizon_and_the_proxy() -> None:
    out = build_targets(_panel(SESSIONS, RETURNS), horizon=5, calendar=CAL)
    assert list(out.columns) == ["symbol", "ts", "as_of", "ingested_at", "target_rv_proxy_h5"]


@pytest.mark.parametrize(
    ("horizon", "expected"),
    [
        (1, dt.date(2024, 1, 2)),  # across the New Year's Day closure and the weekend
        (2, dt.date(2024, 1, 3)),
        (5, dt.date(2024, 1, 8)),  # 01-02, 03, 04, 05, 08 -- the weekend is not a session
    ],
)
def test_target_as_of_is_the_nth_session_after_ts(horizon: int, expected: dt.date) -> None:
    out = build_targets(_panel(SESSIONS, RETURNS), horizon=horizon, calendar=CAL)
    assert _by_day(out, "as_of")[dt.date(2023, 12, 29)] == expected


@pytest.mark.parametrize("horizon", HORIZONS)
def test_target_as_of_is_the_nth_session_after_ts_at_every_horizon(horizon: int) -> None:
    days = sessions_between(CAL, dt.date(2023, 1, 3), dt.date(2024, 6, 28))
    out = build_targets(_panel(days, [0.01] * len(days)), horizon=horizon, calendar=CAL)
    # Counted off the session list directly, independently of nth_session_after.
    expected = {days[i]: days[i + horizon] for i in range(len(days) - horizon)}
    assert _by_day(out, "as_of") == expected


def test_target_as_of_differs_from_ts() -> None:
    # The property that keeps targets out of the feature table: a feature row for the same
    # session carries as_of == ts's date, so the two cannot share a row without one lying.
    for horizon in HORIZONS[:3]:
        out = build_targets(_panel(SESSIONS, RETURNS), horizon=horizon, calendar=CAL)
        assert all(ts.date() < as_of for ts, as_of in zip(out["ts"], out["as_of"], strict=True))


def test_incomplete_horizon_rows_are_omitted() -> None:
    out = build_targets(_panel(SESSIONS, RETURNS), horizon=5, calendar=CAL)
    assert len(out) == len(SESSIONS) - 5
    # 2024-01-02 (idx 7) is the last base with five sessions after it inside the panel.
    assert out["ts"].iloc[-1].date() == SESSIONS[len(SESSIONS) - 6]
    assert out["target_rv_proxy_h5"].notna().all()


def test_a_panel_shorter_than_the_horizon_yields_no_rows() -> None:
    out = build_targets(_panel(SESSIONS[:3], RETURNS[:3]), horizon=5, calendar=CAL)
    assert out.empty
    assert list(out.columns) == ["symbol", "ts", "as_of", "ingested_at", "target_rv_proxy_h5"]


def test_a_missing_session_omits_the_windows_it_falls_in() -> None:
    # Drop 2023-12-27 (idx 4). With h=2 the bases at idx 2 and 3 have it inside their windows;
    # the base at idx 4 no longer exists. A window computed over surviving rows would instead
    # reach one session further and report a value for them.
    keep = [i for i in range(len(SESSIONS)) if i != 4]
    panel = _panel([SESSIONS[i] for i in keep], [RETURNS[i] for i in keep])
    out = build_targets(panel, horizon=2, calendar=CAL)
    present = set(_by_day(out, "target_rv_proxy_h2"))
    assert dt.date(2023, 12, 22) not in present
    assert dt.date(2023, 12, 26) not in present
    assert dt.date(2023, 12, 27) not in present
    assert dt.date(2023, 12, 21) in present
    assert dt.date(2023, 12, 28) in present
    # 2023-12-21 (idx 1) still sums idx 2 and 3: r = 0.03 and 0.04.
    assert _by_day(out, "target_rv_proxy_h2")[dt.date(2023, 12, 21)] == pytest.approx(
        0.03**2 + 0.04**2
    )


def test_ingested_at_is_that_of_the_row_that_completed_the_target() -> None:
    stamps = [dt.datetime(2025, 1, 1 + i, tzinfo=dt.UTC) for i in range(len(SESSIONS))]
    out = build_targets(_panel(SESSIONS, RETURNS, ingested=stamps), horizon=2, calendar=CAL)
    stamped = _by_day(out, "ingested_at")
    # Base idx 6 is realised by idx 8, which was ingested on 2025-01-09.
    assert stamped[dt.date(2023, 12, 29)] == pd.Timestamp(stamps[8])
    assert stamped[dt.date(2023, 12, 20)] == pd.Timestamp(stamps[2])


def test_as_of_read_back_as_timestamps_gives_the_same_targets() -> None:
    # load_panel returns as_of as datetime64, so a panel read from the store arrives this way.
    panel = _panel(SESSIONS, RETURNS)
    stamped = panel.assign(as_of=pd.to_datetime(panel["as_of"]))
    expected = build_targets(panel, horizon=2, calendar=CAL)
    got = build_targets(stamped, horizon=2, calendar=CAL)
    assert list(got["as_of"]) == list(expected["as_of"])
    assert all(type(value) is dt.date for value in got["as_of"])


def test_a_panel_of_two_symbols_is_refused() -> None:
    both = pd.concat([_panel(SESSIONS, RETURNS), _panel(SESSIONS, RETURNS, symbol="QQQ")])
    with pytest.raises(SchemaError, match="one symbol"):
        build_targets(both, horizon=1, calendar=CAL)


def test_a_duplicated_session_is_refused() -> None:
    doubled = pd.concat([_panel(SESSIONS, RETURNS), _panel(SESSIONS[:1], RETURNS[:1])])
    with pytest.raises(SchemaError, match="more than one row"):
        build_targets(doubled, horizon=1, calendar=CAL)


def test_a_missing_return_column_is_refused() -> None:
    with pytest.raises(SchemaError, match="log_return"):
        build_targets(_panel(SESSIONS, RETURNS).drop(columns="log_return"), horizon=1, calendar=CAL)


def test_build_targets_rejects_a_horizon_below_one() -> None:
    with pytest.raises(ValueError, match="horizon"):
        build_targets(_panel(SESSIONS, RETURNS), horizon=0, calendar=CAL)


def test_write_targets_writes_one_partition_per_as_of_and_reads_back(synthetic_store) -> None:
    days = sessions_between(CAL, dt.date(2023, 12, 18), dt.date(2023, 12, 29))
    returns = [0.01 * (k + 1) for k in range(len(days))]
    targets = build_targets(_panel(days, returns), horizon=1, calendar=CAL)

    paths = write_targets(targets, horizon=1, symbol="SPY", settings=synthetic_store.settings)

    assert len(paths) == len(targets) == len(days) - 1
    assert all("targets_rv_h1" in str(path) for path in paths)
    back = load_panel(
        "targets_rv_h1",
        symbols=["SPY"],
        end=dt.date(2023, 12, 29),
        tier=Tier.FEATURES,
        settings=synthetic_store.settings,
    ).sort_values("ts")
    assert len(back) == len(targets)
    assert list(back["target_rv_proxy_h1"]) == pytest.approx(list(targets["target_rv_proxy_h1"]))
    # Each row sits in the partition named by its own as_of, one session after its ts.
    for ts, as_of in zip(back["ts"], back["as_of"], strict=True):
        assert as_of.date() == days[days.index(ts.tz_convert("UTC").date()) + 1]


def test_write_targets_accepts_as_of_read_back_as_timestamps(synthetic_store) -> None:
    # Grouping a frame read from the store yields Timestamp keys, which write_features would
    # turn into a partition named date=2023-06-16T00:00:00. write_targets must normalise first.
    days = sessions_between(CAL, dt.date(2023, 12, 18), dt.date(2023, 12, 29))
    targets = build_targets(_panel(days, [0.01] * len(days)), horizon=1, calendar=CAL)
    read_back = targets.assign(as_of=pd.to_datetime(targets["as_of"]))

    paths = write_targets(read_back, horizon=1, symbol="SPY", settings=synthetic_store.settings)

    assert all("T00:00:00" not in str(path) for path in paths)
    assert len(paths) == len(targets)


def test_write_targets_refuses_an_empty_frame(synthetic_store) -> None:
    empty = build_targets(_panel(SESSIONS[:1], RETURNS[:1]), horizon=1, calendar=CAL)
    with pytest.raises(SchemaError, match="no rows"):
        write_targets(empty, horizon=1, symbol="SPY", settings=synthetic_store.settings)


def test_a_panel_whose_as_of_is_lagged_behind_ts_is_refused() -> None:
    # A feature-style panel: as_of one session after ts. Taken at face value the targets would
    # be shifted by a session, so the mismatch is refused rather than repaired.
    panel = _panel(SESSIONS, RETURNS)
    panel["as_of"] = SESSIONS[1:] + [dt.date(2024, 1, 11)]
    with pytest.raises(SchemaError, match="not the session of their ts"):
        build_targets(panel, horizon=1, calendar=CAL)


def test_a_panel_with_a_naive_ts_is_refused() -> None:
    panel = _panel(SESSIONS, RETURNS)
    panel["ts"] = panel["ts"].dt.tz_localize(None)
    with pytest.raises(SchemaError, match="timezone-aware"):
        build_targets(panel, horizon=1, calendar=CAL)


def test_write_targets_refuses_a_frame_of_another_horizon(synthetic_store) -> None:
    days = sessions_between(CAL, dt.date(2023, 12, 18), dt.date(2023, 12, 29))
    h5 = build_targets(_panel(days, [0.01] * len(days)), horizon=5, calendar=CAL)
    with pytest.raises(SchemaError, match="target_rv_proxy_h22"):
        write_targets(h5, horizon=22, symbol="SPY", settings=synthetic_store.settings)
    assert not (synthetic_store.root / "features" / "targets_rv_h22").exists()


def test_write_targets_refuses_a_frame_of_another_symbol(synthetic_store) -> None:
    days = sessions_between(CAL, dt.date(2023, 12, 18), dt.date(2023, 12, 29))
    qqq = build_targets(_panel(days, [0.01] * len(days), symbol="QQQ"), horizon=1, calendar=CAL)
    with pytest.raises(SchemaError, match="QQQ"):
        write_targets(qqq, horizon=1, symbol="SPY", settings=synthetic_store.settings)
    assert not (synthetic_store.root / "features" / "targets_rv_h1").exists()


def test_reading_targets_at_a_cutoff_returns_only_realised_base_sessions(synthetic_store) -> None:
    # The store filter doing the work the module docstring claims: as_of values straddle the
    # cutoff, and only base sessions at least `horizon` sessions before it come back.
    horizon, cutoff = 2, dt.date(2023, 12, 27)
    days = sessions_between(CAL, dt.date(2023, 12, 18), dt.date(2023, 12, 29))
    targets = build_targets(_panel(days, [0.01] * len(days)), horizon=horizon, calendar=CAL)
    write_targets(targets, horizon=horizon, symbol="SPY", settings=synthetic_store.settings)

    back = load_panel(
        targets_dataset(horizon),
        symbols=["SPY"],
        end=cutoff,
        tier=Tier.FEATURES,
        settings=synthetic_store.settings,
    )

    last_base = back["ts"].max().date()
    assert last_base == days[days.index(cutoff) - horizon]  # 2023-12-22: two sessions before
    assert (back["as_of"].dt.date <= cutoff).all()
    assert len(back) < len(targets)  # some targets were written but are not yet visible
