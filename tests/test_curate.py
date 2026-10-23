"""Curation: raw as the vendor served it becomes a typed panel.

This is where every string becomes a number, and the rule is that it fails loudly rather than
coercing. A silently NaN close would propagate into every realized measure built on it, and the
whole point of keeping raw faithful is that a parse bug here is a rebuild rather than lost data.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from fixture_transport import FIXTURES

from volab.errors import DatasetNotFound, SchemaError
from volab.ingest import archive_payload, build_raw
from volab.ingest.curate import curate_bars, curate_fred, curate_vix
from volab.paths import Tier
from volab.store import load_panel, write_raw

FETCHED = dt.datetime(2026, 1, 5, 12, 0, tzinfo=dt.UTC)
END = dt.date(2023, 6, 20)
SESSIONS = [dt.date(2023, 6, 15), dt.date(2023, 6, 16), dt.date(2023, 6, 20)]


def _seed(store, fixture: str, *, source: str, symbol: str) -> None:
    archive_payload(
        (FIXTURES / fixture).read_bytes(),
        source=source,
        symbol=symbol,
        fetched_at=FETCHED,
        extension="json" if fixture.endswith("json") else "csv",
        source_url=f"https://example.invalid/{source}/{symbol}",
        settings=store.settings,
    )
    build_raw(source, symbols=[symbol], settings=store.settings)


def _curated(store, dataset: str, symbol: str) -> pd.DataFrame:
    return load_panel(
        dataset, symbols=[symbol], end=END, tier=Tier.CURATED, settings=store.settings
    ).sort_values("as_of")


def test_curate_bars_types_prices_and_strips_volume_separators(synthetic_store) -> None:
    _seed(synthetic_store, "nasdaq_bars_spy.json", source="nasdaq_bars", symbol="SPY")

    written = curate_bars("SPY", end=END, settings=synthetic_store.settings)
    frame = _curated(synthetic_store, "bars_daily", "SPY")

    assert written == 3
    assert frame["close"].iloc[0] == pytest.approx(437.21)
    assert frame["close"].dtype.kind == "f"
    # "1,234,567" as served -> an integer-valued float, separators gone.
    assert frame["volume"].iloc[0] == pytest.approx(1_234_567)


@pytest.mark.parametrize("bad", ["N/A", "", "--", "1.2.3", "$4.00"])
def test_curate_bars_fails_loudly_on_an_unparseable_price(synthetic_store, bad: str) -> None:
    """Never coerce to NaN and continue: a silent NaN close poisons every measure built on it."""
    frame = pd.DataFrame(
        {
            "symbol": ["SPY"],
            "ts": [pd.Timestamp("2023-06-15 20:00", tz="UTC")],
            "as_of": [dt.date(2023, 6, 15)],
            "ingested_at": [pd.Timestamp(FETCHED)],
            "source_url": ["x"],
            "vendor_date_text": ["06/15/2023"],
            "open": ["1.0"],
            "high": ["1.0"],
            "low": ["1.0"],
            "close": [bad],
            "volume": ["1"],
        }
    )
    write_raw(
        frame,
        source="nasdaq_bars",
        symbol="SPY",
        as_of=dt.date(2023, 6, 15),
        ingested_at=FETCHED,
        settings=synthetic_store.settings,
    )

    with pytest.raises(SchemaError, match="could not parse"):
        curate_bars("SPY", end=END, settings=synthetic_store.settings)


def test_curate_bars_rejects_a_non_session_date(synthetic_store) -> None:
    """A row the calendar does not recognise would misalign every window built on sessions."""
    saturday = dt.date(2023, 6, 17)
    frame = pd.DataFrame(
        {
            "symbol": ["SPY"],
            "ts": [pd.Timestamp("2023-06-17 20:00", tz="UTC")],
            "as_of": [saturday],
            "ingested_at": [pd.Timestamp(FETCHED)],
            "source_url": ["x"],
            "vendor_date_text": ["06/17/2023"],
            "open": ["1.0"],
            "high": ["1.0"],
            "low": ["1.0"],
            "close": ["1.0"],
            "volume": ["1"],
        }
    )
    write_raw(
        frame,
        source="nasdaq_bars",
        symbol="SPY",
        as_of=saturday,
        ingested_at=FETCHED,
        settings=synthetic_store.settings,
    )

    with pytest.raises(SchemaError, match="not an XNYS session"):
        curate_bars("SPY", end=END, settings=synthetic_store.settings)


def test_curate_bars_flags_a_distribution_for_qqq(synthetic_store) -> None:
    _seed(synthetic_store, "nasdaq_bars_spy.json", source="nasdaq_bars", symbol="QQQ")
    _seed(synthetic_store, "nasdaq_dividends_qqq.json", source="nasdaq_dividends", symbol="QQQ")

    curate_bars("QQQ", end=END, settings=synthetic_store.settings)
    frame = _curated(synthetic_store, "bars_daily", "QQQ")

    assert frame["has_distribution"].dtype == bool
    # The fixture's ex-date is 2023-06-16, the middle session.
    assert list(frame["has_distribution"]) == [False, True, False]


def test_curate_bars_has_distribution_is_false_without_a_dividend_source(
    synthetic_store,
) -> None:
    """SPY and IWM return no dividend rows from the vendor at all, so the column is uniformly
    False and the bias it would have flagged is documented rather than detected."""
    _seed(synthetic_store, "nasdaq_bars_spy.json", source="nasdaq_bars", symbol="SPY")

    curate_bars("SPY", end=END, settings=synthetic_store.settings)
    frame = _curated(synthetic_store, "bars_daily", "SPY")

    assert frame["has_distribution"].dtype == bool
    assert not frame["has_distribution"].any()


def test_curate_bars_never_reads_past_the_end_it_was_given(synthetic_store, monkeypatch) -> None:
    _seed(synthetic_store, "nasdaq_bars_spy.json", source="nasdaq_bars", symbol="SPY")
    seen: list[dt.date] = []
    import volab.ingest.curate as curate_module

    original = curate_module.load_panel

    def spy(*args: object, **kwargs: object) -> pd.DataFrame:
        end = kwargs.get("end")
        if isinstance(end, dt.date):
            seen.append(end)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(curate_module, "load_panel", spy)
    curate_bars("SPY", end=END, settings=synthetic_store.settings)

    assert seen, "curate must read through load_panel"
    assert max(seen) <= END


def test_curate_bars_is_idempotent(synthetic_store) -> None:
    _seed(synthetic_store, "nasdaq_bars_spy.json", source="nasdaq_bars", symbol="SPY")

    first = curate_bars("SPY", end=END, settings=synthetic_store.settings)
    second = curate_bars("SPY", end=END, settings=synthetic_store.settings)

    assert first == second == 3
    frame = _curated(synthetic_store, "bars_daily", "SPY")
    assert len(frame) == 3


def test_curate_vix_types_the_closes(synthetic_store) -> None:
    _seed(synthetic_store, "cboe_vix.csv", source="cboe_vix", symbol="VIX")

    written = curate_vix("VIX", end=END, settings=synthetic_store.settings)
    frame = _curated(synthetic_store, "vix_family", "VIX")

    assert written == 3
    assert frame["close"].iloc[0] == pytest.approx(14.8)
    assert frame["open"].iloc[0] == pytest.approx(14.5)


def test_curate_vix_pads_a_close_only_series_with_null_ohlc(synthetic_store) -> None:
    """VVIX is close-only at the vendor; curated gives it the family's four columns, as nulls.

    Raw stays faithful to Cboe and curated is normalised, so the padding is a curation fact. The
    nulls are genuine and float-typed -- not zeros, which would read as "the open was 0" -- so a
    reader can tell "Cboe publishes no open for VVIX" from a real value.
    """
    _seed(synthetic_store, "cboe_vvix.csv", source="cboe_vix", symbol="VVIX")

    curate_vix("VVIX", end=END, settings=synthetic_store.settings)
    frame = _curated(synthetic_store, "vix_family", "VVIX")

    assert frame["close"].iloc[0] == pytest.approx(78.12)
    assert frame["close"].dtype.kind == "f"
    for name in ("open", "high", "low"):
        assert name in frame.columns
        assert frame[name].dtype.kind == "f", f"{name} must be float, not object"
        assert frame[name].isna().all(), f"{name} must be null, never zero or filled"
        assert not (frame[name] == 0).any()


def test_one_load_panel_spans_series_of_different_vendor_shapes(synthetic_store) -> None:
    """The acceptance criterion: VIX (OHLC) and VVIX (close-only) read back in a single call.

    Before curation normalised the schema this raised a DuckDB schema mismatch across the
    dataset's files, because the curated schema depended on which symbol a file belonged to.
    """
    _seed(synthetic_store, "cboe_vix.csv", source="cboe_vix", symbol="VIX")
    _seed(synthetic_store, "cboe_vvix.csv", source="cboe_vix", symbol="VVIX")
    curate_vix("VIX", end=END, settings=synthetic_store.settings)
    curate_vix("VVIX", end=END, settings=synthetic_store.settings)

    panel = load_panel(
        "vix_family",
        symbols=["VIX", "VVIX"],
        end=END,
        tier=Tier.CURATED,
        settings=synthetic_store.settings,
    )

    assert sorted(panel["symbol"].unique()) == ["VIX", "VVIX"]
    assert len(panel) == 6
    vix = panel[panel["symbol"] == "VIX"]
    vvix = panel[panel["symbol"] == "VVIX"]
    assert vix["open"].notna().all()
    assert vvix["open"].isna().all()
    assert vvix["close"].notna().all()


def test_the_raw_tier_keeps_a_close_only_series_close_only(synthetic_store) -> None:
    """Padding is a curated-tier concern: raw must stay exactly what Cboe served."""
    _seed(synthetic_store, "cboe_vvix.csv", source="cboe_vix", symbol="VVIX")

    curate_vix("VVIX", end=END, settings=synthetic_store.settings)
    raw = load_panel(
        "cboe_vix",
        symbols=["VVIX"],
        end=END,
        tier=Tier.RAW,
        settings=synthetic_store.settings,
    )

    assert {"open", "high", "low", "close"} & set(raw.columns) == {"close"}


def _write_raw_vix(store, columns: dict[str, list[str]]) -> None:
    day = dt.date(2023, 6, 15)
    frame = pd.DataFrame(
        {
            "symbol": ["VIX"],
            "ts": [pd.Timestamp(f"{day} 20:00", tz="UTC")],
            "as_of": [day],
            "ingested_at": [pd.Timestamp(FETCHED)],
            "source_url": ["x"],
            "vendor_date_text": ["06/15/2023"],
            **columns,
        }
    )
    write_raw(
        frame,
        source="cboe_vix",
        symbol="VIX",
        as_of=day,
        ingested_at=FETCHED,
        settings=store.settings,
    )


def test_curate_vix_refuses_to_pad_a_series_that_lost_a_column(synthetic_store) -> None:
    """Only the genuinely close-only shape is padded.

    A VIX file that lost ``high`` to a parse bug must raise, not be unified with the rest: padding
    it would be the silent repair the store's strict schema check exists to refuse.
    """
    _write_raw_vix(synthetic_store, {"open": ["1.0"], "low": ["1.0"], "close": ["14.0"]})

    with pytest.raises(SchemaError, match="high"):
        curate_vix("VIX", end=END, settings=synthetic_store.settings)


def test_curate_vix_refuses_a_series_with_no_close(synthetic_store) -> None:
    _write_raw_vix(synthetic_store, {"open": ["1.0"], "high": ["1.0"], "low": ["1.0"]})

    with pytest.raises(SchemaError, match="close"):
        curate_vix("VIX", end=END, settings=synthetic_store.settings)


def test_curate_fred_types_the_value(synthetic_store) -> None:
    _seed(synthetic_store, "fred_baa10y.csv", source="fred", symbol="BAA10Y")

    written = curate_fred("BAA10Y", end=END, settings=synthetic_store.settings)
    frame = _curated(synthetic_store, "fred_daily", "BAA10Y")

    assert written >= 2
    assert frame["value"].iloc[0] == pytest.approx(1.78)
    assert frame["value"].dtype.kind == "f"


def test_curate_raises_when_the_raw_dataset_is_missing(synthetic_store) -> None:
    with pytest.raises(DatasetNotFound):
        curate_bars("SPY", end=END, settings=synthetic_store.settings)


def test_curate_vix_drops_a_row_dated_outside_a_session(synthetic_store, capsys) -> None:
    """Cboe publishes VIX on NYSE holidays -- 33 such rows since 2016-07-01, with real values.

    They have no equity session to attach to, so they can never enter a session-indexed window.
    Dropping them is reported, not silent, so a jump in the count surfaces a vendor change.
    """
    memorial_day = dt.date(2023, 5, 29)
    for day in (dt.date(2023, 5, 26), memorial_day):
        write_raw(
            pd.DataFrame(
                {
                    "symbol": ["VIX"],
                    "ts": [pd.Timestamp(f"{day} 20:00", tz="UTC")],
                    "as_of": [day],
                    "ingested_at": [pd.Timestamp(FETCHED)],
                    "source_url": ["x"],
                    "vendor_date_text": [day.strftime("%m/%d/%Y")],
                    "open": ["1.0"],
                    "high": ["1.0"],
                    "low": ["1.0"],
                    "close": ["14.0"],
                }
            ),
            source="cboe_vix",
            symbol="VIX",
            as_of=day,
            ingested_at=FETCHED,
            settings=synthetic_store.settings,
        )

    written = curate_vix("VIX", end=dt.date(2023, 5, 30), settings=synthetic_store.settings)

    assert written == 1, "the holiday row is dropped, the session row kept"
    assert "dropped 1 row" in capsys.readouterr().out


def test_curate_bars_rejects_rather_than_drops_a_non_session_row(synthetic_store) -> None:
    """Bars get the stricter rule: a bar on a non-session means the source is wrong about the
    calendar, which would corrupt the return series rather than merely add an unusable row."""
    saturday = dt.date(2023, 6, 17)
    write_raw(
        pd.DataFrame(
            {
                "symbol": ["SPY"],
                "ts": [pd.Timestamp("2023-06-17 20:00", tz="UTC")],
                "as_of": [saturday],
                "ingested_at": [pd.Timestamp(FETCHED)],
                "source_url": ["x"],
                "vendor_date_text": ["06/17/2023"],
                "open": ["1.0"],
                "high": ["1.0"],
                "low": ["1.0"],
                "close": ["1.0"],
                "volume": ["1"],
            }
        ),
        source="nasdaq_bars",
        symbol="SPY",
        as_of=saturday,
        ingested_at=FETCHED,
        settings=synthetic_store.settings,
    )

    with pytest.raises(SchemaError, match="corrupt the return series"):
        curate_bars("SPY", end=END, settings=synthetic_store.settings)
