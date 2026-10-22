from __future__ import annotations

import datetime as dt
import gzip
import json
from pathlib import Path

import pytest
from fixture_transport import FIXTURES, fixture_transport

from volab.errors import ParseRefused, RawOverwrite
from volab.ingest import (
    ARCHIVE_MONTHLY,
    INGEST_FLOOR,
    archive_payload,
    build_raw,
    fetch_source,
)
from volab.paths import Tier
from volab.store import load_panel

JAN = dt.datetime(2026, 1, 5, 12, 0, tzinfo=dt.UTC)
JAN_LATER = dt.datetime(2026, 1, 27, 12, 0, tzinfo=dt.UTC)
FEB = dt.datetime(2026, 2, 3, 12, 0, tzinfo=dt.UTC)


def _archive(store, body: bytes, *, source: str, symbol: str, at: dt.datetime) -> Path | None:
    return archive_payload(
        body,
        source=source,
        symbol=symbol,
        fetched_at=at,
        extension="json",
        source_url=f"https://example.invalid/{source}/{symbol}",
        settings=store.settings,
    )


def test_constants_are_what_the_spec_pins() -> None:
    assert dt.date(2016, 7, 1) == INGEST_FLOOR
    assert frozenset({"cboe_vix", "fred"}) == ARCHIVE_MONTHLY


def test_archive_writes_the_body_untouched(synthetic_store) -> None:
    body = b'{"data": "exactly these bytes"}'

    path = _archive(synthetic_store, body, source="nasdaq_bars", symbol="SPY", at=JAN)

    assert path is not None
    assert gzip.decompress(path.read_bytes()) == body


def test_archive_records_provenance_in_a_sidecar(synthetic_store) -> None:
    path = _archive(synthetic_store, b"{}", source="nasdaq_bars", symbol="SPY", at=JAN)

    assert path is not None
    sidecar = path.with_suffix("").with_suffix(".meta.json")
    meta = json.loads(sidecar.read_text())
    assert meta["source"] == "nasdaq_bars"
    assert meta["symbol"] == "SPY"
    assert meta["fetched_at"] == JAN.isoformat()
    # Recorded from the caller, not rebuilt here: a bars URL carries the date range it asked
    # for, which cannot be recovered afterwards.
    assert meta["source_url"] == "https://example.invalid/nasdaq_bars/SPY"


def test_archive_is_not_month_limited_for_a_trailing_window_source(synthetic_store) -> None:
    """Twice in one month is fine for Nasdaq, as long as the window actually moved.

    Each payload of a trailing-window source holds sessions later ones will have lost, so the
    monthly rule must not apply here. Identical bytes are skipped separately -- see
    test_archive_skips_a_body_identical_to_the_newest_payload.
    """
    first = _archive(synthetic_store, b'{"window": 1}', source="nasdaq_bars", symbol="SPY", at=JAN)
    second = _archive(
        synthetic_store, b'{"window": 2}', source="nasdaq_bars", symbol="SPY", at=JAN_LATER
    )

    assert first is not None and second is not None
    assert first != second


def test_archive_skips_a_full_history_source_twice_in_one_month(synthetic_store) -> None:
    first = _archive(synthetic_store, b"a", source="cboe_vix", symbol="VIX", at=JAN)
    second = _archive(synthetic_store, b"b", source="cboe_vix", symbol="VIX", at=JAN_LATER)
    third = _archive(synthetic_store, b"c", source="cboe_vix", symbol="VIX", at=FEB)

    assert first is not None
    assert second is None, "a second payload in the same month subsumes nothing new"
    assert third is not None, "a new month gets its own vintage"


def test_archive_never_rewrites_an_existing_payload(synthetic_store) -> None:
    _archive(synthetic_store, b"first", source="nasdaq_bars", symbol="SPY", at=JAN)

    with pytest.raises(RawOverwrite, match="already exists"):
        _archive(synthetic_store, b"second", source="nasdaq_bars", symbol="SPY", at=JAN)


def _seed(store, fixture: str, *, source: str, symbol: str, at: dt.datetime = JAN) -> None:
    archive_payload(
        (FIXTURES / fixture).read_bytes(),
        source=source,
        symbol=symbol,
        fetched_at=at,
        extension="json" if fixture.endswith("json") else "csv",
        source_url=f"https://example.invalid/{source}/{symbol}",
        settings=store.settings,
    )


def test_build_raw_replays_an_archived_payload_with_no_network(synthetic_store) -> None:
    _seed(synthetic_store, "nasdaq_bars_spy.json", source="nasdaq_bars", symbol="SPY")

    written = build_raw(
        "nasdaq_bars",
        symbols=["SPY"],
        settings=synthetic_store.settings,
    )

    assert written == 3, "one partition per session, per the write path's contract"
    frame = load_panel(
        "nasdaq_bars",
        symbols=["SPY"],
        end=dt.date(2023, 6, 20),
        tier=Tier.RAW,
        settings=synthetic_store.settings,
    )
    assert len(frame) == 3


def test_build_raw_writes_one_partition_per_as_of(synthetic_store) -> None:
    _seed(synthetic_store, "nasdaq_bars_spy.json", source="nasdaq_bars", symbol="SPY")

    build_raw(
        "nasdaq_bars",
        symbols=["SPY"],
        settings=synthetic_store.settings,
    )

    root = synthetic_store.settings.storage_root / "raw" / "nasdaq_bars" / "SPY"
    assert sorted(p.name for p in root.iterdir()) == [
        "date=2023-06-15",
        "date=2023-06-16",
        "date=2023-06-20",
    ]


def test_build_raw_drops_rows_before_the_ingest_floor(synthetic_store) -> None:
    _seed(synthetic_store, "nasdaq_bars_before_floor.json", source="nasdaq_bars", symbol="SPY")

    written = build_raw(
        "nasdaq_bars",
        symbols=["SPY"],
        settings=synthetic_store.settings,
    )

    assert written == 1, "2016-06-30 is before the floor; 2016-07-01 is not"
    root = synthetic_store.settings.storage_root / "raw" / "nasdaq_bars" / "SPY"
    assert [p.name for p in root.iterdir()] == ["date=2016-07-01"]


def test_build_raw_skips_partitions_it_already_wrote(synthetic_store) -> None:
    """Resumability. write_raw refuses to overwrite, so a second pass must skip, not raise."""
    _seed(synthetic_store, "nasdaq_bars_spy.json", source="nasdaq_bars", symbol="SPY")
    first = build_raw(
        "nasdaq_bars",
        symbols=["SPY"],
        settings=synthetic_store.settings,
    )

    second = build_raw(
        "nasdaq_bars",
        symbols=["SPY"],
        settings=synthetic_store.settings,
    )

    assert first == 3
    assert second == 0, "every partition already existed"


def test_build_raw_without_an_archive_writes_nothing(synthetic_store) -> None:
    assert (
        build_raw(
            "nasdaq_bars",
            symbols=["SPY"],
            settings=synthetic_store.settings,
        )
        == 0
    )


def test_archive_rejects_a_naive_fetched_at(synthetic_store) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        archive_payload(
            b"{}",
            source="nasdaq_bars",
            symbol="SPY",
            fetched_at=dt.datetime(2026, 1, 5, 12, 0),
            extension="json",
            source_url="https://example.invalid/x",
            settings=synthetic_store.settings,
        )


def test_fetch_source_archives_exactly_the_transport_bytes(synthetic_store) -> None:
    body = (FIXTURES / "nasdaq_bars_spy.json").read_bytes()

    path = fetch_source(
        "nasdaq_bars",
        "SPY",
        transport=fixture_transport("nasdaq_bars_spy.json"),
        fetched_at=JAN,
        settings=synthetic_store.settings,
    )

    assert path is not None
    assert gzip.decompress(path.read_bytes()) == body


def test_fetch_source_refuses_to_archive_an_unparseable_body(synthetic_store) -> None:
    """A bad response must never reach the append-only archive.

    build_raw replays every payload for a symbol, so one unparseable body archived here would
    abort that replay permanently, taking every later valid payload down with it.
    """

    def error_page(url: str) -> bytes:
        return b"<html><body>403 Forbidden</body></html>"

    with pytest.raises(ParseRefused):
        fetch_source(
            "nasdaq_bars",
            "SPY",
            transport=error_page,
            fetched_at=JAN,
            settings=synthetic_store.settings,
        )

    root = synthetic_store.settings.storage_root / "vendor_payloads"
    assert list(root.rglob("*.gz")) == [], "nothing may be archived when parsing failed"


def test_fetch_source_skips_a_taken_month_without_fetching(synthetic_store) -> None:
    _seed(synthetic_store, "cboe_vix.csv", source="cboe_vix", symbol="VIX", at=JAN)

    def must_not_be_called(url: str) -> bytes:
        raise AssertionError("fetched a month that was already archived")

    assert (
        fetch_source(
            "cboe_vix",
            "VIX",
            transport=must_not_be_called,
            fetched_at=JAN_LATER,
            settings=synthetic_store.settings,
        )
        is None
    )


def test_build_raw_later_vintage_adds_only_sessions_the_earlier_one_lacked(
    synthetic_store,
) -> None:
    """Oldest payload wins each partition; a later one contributes only what is new."""
    _seed(
        synthetic_store, "nasdaq_bars_before_floor.json", source="nasdaq_bars", symbol="SPY", at=JAN
    )
    first = build_raw("nasdaq_bars", symbols=["SPY"], settings=synthetic_store.settings)

    _seed(synthetic_store, "nasdaq_bars_spy.json", source="nasdaq_bars", symbol="SPY", at=FEB)
    second = build_raw("nasdaq_bars", symbols=["SPY"], settings=synthetic_store.settings)

    assert first == 1, "only 2016-07-01 survives the floor"
    assert second == 3, "the 2023 sessions are new; nothing is rewritten"
    root = synthetic_store.settings.storage_root / "raw" / "nasdaq_bars" / "SPY"
    assert sorted(p.name for p in root.iterdir()) == [
        "date=2016-07-01",
        "date=2023-06-15",
        "date=2023-06-16",
        "date=2023-06-20",
    ]


def test_archive_skips_a_body_identical_to_the_newest_payload(synthetic_store) -> None:
    """Two fetches of a trailing window on one day see the same bytes; store one.

    The archive is committed, so a redundant copy costs git history forever and buys nothing.
    """
    first = _archive(synthetic_store, b"same bytes", source="nasdaq_bars", symbol="SPY", at=JAN)
    second = _archive(
        synthetic_store, b"same bytes", source="nasdaq_bars", symbol="SPY", at=JAN_LATER
    )
    third = _archive(
        synthetic_store, b"different bytes", source="nasdaq_bars", symbol="SPY", at=FEB
    )

    assert first is not None
    assert second is None, "identical content adds nothing"
    assert third is not None, "a genuinely new window has different bytes"
