"""``python -m volab.ingest`` -- fetch, build-raw, curate, backfill.

The functions the CLI drives are tested elsewhere; what is pinned here is the CLI itself: the exit
code, the choice of what to read and what to skip, and above all the holdout seal. The CLI has no
unlock flag and no ``--end``, deliberately, and a CLI that "worked" on a manual run could still
have gained either without a single other test noticing.

Two fixtures make every test safe to run anywhere. ``_isolated`` points the CLI at a temporary
store instead of the repository's ``data/``. ``_no_network`` fails any test that reaches for a
transport or a socket. It is stricter than ``exploding_transport`` can be: that raises an
``AssertionError``, and ``_fetch`` catches every ``Exception`` and prints ``FAILED``, so a stray
network call would otherwise turn into exit code 1 and a green test.

Tests that want a fetch install their own ``cli.fetch_source``: either a recorder with canned
outcomes, or the real function fed by ``_routed_transport``, which serves committed fixtures and
one bars payload that runs *past* ``dev_end``. That payload is the point of the seal tests: the
store really does hold the future, so a read that stopped one boundary late would be seen.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
import socket
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from fixture_transport import FIXTURES

import volab.ingest.__main__ as cli
from volab.config import Settings, load_settings
from volab.errors import ParseRefused, SchemaError
from volab.ingest import SOURCES, archive_payload, build_raw, fetch_source
from volab.ingest.__main__ import EXOGENOUS_SOURCE
from volab.ingest.curate import BARS_DATASET, FRED_DATASET, VIX_DATASET
from volab.paths import Tier
from volab.store import load_panel

if TYPE_CHECKING:
    from conftest import SyntheticStore

VERBS = ("fetch", "build-raw", "curate", "backfill")

# Spelled out rather than read from the CLI, so a change to the plan is a change to this file.
UNIVERSE = ("SPY", "QQQ", "IWM")
VIX_FAMILY = ("VIX", "VIX9D", "VIX1D", "VVIX")
FRED_FAMILY = ("BAA10Y", "DGS10", "T10Y2Y")
FULL_PLAN = (
    [("nasdaq_bars", symbol) for symbol in UNIVERSE]
    + [("nasdaq_dividends", symbol) for symbol in UNIVERSE]
    + [("cboe_vix", symbol) for symbol in VIX_FAMILY]
    + [("fred", symbol) for symbol in FRED_FAMILY]
)

# The synthetic store's dev_end is 2023-12-31. Two of these sessions are inside it and two are
# after it, so a store seeded from them holds exactly the rows ingestion must capture but curation
# must never touch. 2024-01-01 is a holiday.
DEV_END = dt.date(2023, 12, 31)
DEV_BARS = [dt.date(2023, 12, 28), dt.date(2023, 12, 29)]
FUTURE_BARS = [dt.date(2024, 1, 2), dt.date(2024, 1, 3)]
VIX_SESSIONS = [dt.date(2023, 6, 15), dt.date(2023, 6, 16), dt.date(2023, 6, 20)]
FETCHED = dt.datetime(2026, 1, 5, 12, 0, tzinfo=dt.UTC)

MAINTENANCE_PAGE = b"<!DOCTYPE html><html><body><h1>Down for maintenance</h1></body></html>"
SKIPPED = "no raw data yet, skipped"


def _bars_payload() -> bytes:
    rows = [
        {
            "date": day.strftime("%m/%d/%Y"),
            "close": "440.50",
            "volume": "1,000",
            "open": "439.10",
            "high": "441.00",
            "low": "438.75",
        }
        for day in reversed(DEV_BARS + FUTURE_BARS)
    ]
    document = {
        "data": {"symbol": "SPY", "tradesTable": {"rows": rows}},
        "message": None,
        "status": {"rCode": 200},
    }
    return json.dumps(document).encode()


def _routed_transport(*, fred: bytes | None = None) -> Callable[[str], bytes]:
    """A transport that answers by vendor from committed fixtures. No socket is ever opened."""
    bars = _bars_payload()
    dividends = (FIXTURES / "nasdaq_dividends_empty.json").read_bytes()
    vix = (FIXTURES / "cboe_vix.csv").read_bytes()
    fred_body = (FIXTURES / "fred_baa10y.csv").read_bytes() if fred is None else fred

    def transport(url: str) -> bytes:
        if "/historical" in url:
            return bars
        if "/dividends" in url:
            return dividends
        if "cboe.com" in url:
            return vix
        if "fredgraph" in url:
            return fred_body
        raise AssertionError(f"no fixture routes {url}")

    return transport


@pytest.fixture(autouse=True)
def _isolated(synthetic_store: SyntheticStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the CLI at the temporary store; the repository's own data must never be touched."""
    monkeypatch.setattr(cli, "load_settings", lambda: synthetic_store.settings)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that fetches without having said how, or that opens a socket at all.

    Both guards use ``pytest.fail``, which raises a ``BaseException``: ``except Exception`` in
    ``_fetch`` cannot swallow it.
    """

    def forbidden_fetch(*args: object, **kwargs: object) -> object:
        pytest.fail(f"this test fetched without installing a fetch_source: {args}")

    def forbidden_socket(*args: object, **kwargs: object) -> object:
        pytest.fail(f"this test tried to reach the network: {args}")

    monkeypatch.setattr(cli, "fetch_source", forbidden_fetch)
    monkeypatch.setattr(socket.socket, "connect", forbidden_socket)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden_socket)


# -- helpers ------------------------------------------------------------------------------------


def _install_fetch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: Mapping[tuple[str, str], Exception] | None = None,
    result: Path | None = Path("20260105T120000Z.json.gz"),
) -> list[tuple[str, str]]:
    """Replace ``fetch_source`` with a recorder. Returns the list of (source, symbol) attempts."""
    attempts: list[tuple[str, str]] = []
    failures = fail or {}

    def fake(source: str, symbol: str, **kwargs: object) -> Path | None:
        attempts.append((source, symbol))
        if (source, symbol) in failures:
            raise failures[(source, symbol)]
        return result

    monkeypatch.setattr(cli, "fetch_source", fake)
    return attempts


def _install_real_fetch(monkeypatch: pytest.MonkeyPatch, transport: Callable[[str], bytes]) -> None:
    """The real ``fetch_source``, parsing and archiving, fed by fixtures instead of the wire."""

    def routed(source: str, symbol: str, *, settings: Settings) -> Path | None:
        return fetch_source(source, symbol, transport=transport, settings=settings)

    monkeypatch.setattr(cli, "fetch_source", routed)


def _archive(store: SyntheticStore, body: bytes, *, source: str, symbol: str, ext: str) -> None:
    archive_payload(
        body,
        source=source,
        symbol=symbol,
        fetched_at=FETCHED,
        extension=ext,
        source_url=f"https://example.invalid/{source}/{symbol}",
        settings=store.settings,
    )


def _seed_archive(store: SyntheticStore, *sources: str, only: Sequence[str] | None = None) -> None:
    """Archive fixture payloads for the named sources, as a past ``fetch`` would have.

    ``only`` narrows the seeding to those symbols, for a store that holds part of a source.
    """
    transport = _routed_transport()
    for source, symbol in FULL_PLAN:
        if source in sources and (only is None or symbol in only):
            url = {
                "nasdaq_bars": "/historical",
                "nasdaq_dividends": "/dividends",
                "cboe_vix": "cboe.com",
                "fred": "fredgraph",
            }[source]
            ext = "json" if source.startswith("nasdaq") else "csv"
            _archive(store, transport(url), source=source, symbol=symbol, ext=ext)


def _seed_raw(store: SyntheticStore, *sources: str, only: Sequence[str] | None = None) -> None:
    _seed_archive(store, *sources, only=only)
    for source in sources:
        symbols = [
            symbol
            for name, symbol in FULL_PLAN
            if name == source and (only is None or symbol in only)
        ]
        build_raw(source, symbols=symbols, settings=store.settings)


def _partition_dates(store: SyntheticStore, tier: Tier, dataset: str, symbol: str) -> list[dt.date]:
    base = Path(store.root) / tier.value / dataset / symbol
    if not base.is_dir():
        return []
    return sorted(dt.date.fromisoformat(path.name.removeprefix("date=")) for path in base.iterdir())


def _curated(store: SyntheticStore, dataset: str, symbol: str) -> list[dt.date]:
    return _partition_dates(store, Tier.CURATED, dataset, symbol)


def _lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [line.strip() for line in capsys.readouterr().out.splitlines()]


def _record_reads(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Record the keyword arguments of every ``load_panel`` call made anywhere in ``volab``.

    Wraps whatever each module holds *now*, which includes the conftest's holdout policer, so that
    stays in the chain. A call is recorded before it is delegated, so one that raises still
    counts. Every binding is replaced, not only the store's: ``curate`` holds its own reference.
    """
    calls: list[dict[str, object]] = []
    for name, module in list(sys.modules.items()):
        if not name.startswith("volab"):
            continue
        current = getattr(module, "load_panel", None)
        if current is None:
            continue

        def recording(
            *args: object, _current: Callable[..., pd.DataFrame] = current, **kwargs: object
        ) -> pd.DataFrame:
            calls.append(dict(kwargs))
            return _current(*args, **kwargs)

        monkeypatch.setattr(module, "load_panel", recording)
    return calls


# -- 1. exit codes ------------------------------------------------------------------------------


def test_fetch_exits_zero_when_every_fetch_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    attempts = _install_fetch(monkeypatch)

    assert cli.main(["fetch"]) == 0

    assert attempts == FULL_PLAN
    assert "nasdaq_bars/SPY: archived 20260105T120000Z.json.gz" in _lines(capsys)


def test_fetch_exits_zero_and_says_so_when_there_is_nothing_new(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``None`` is the archive saying "already held", which is a success and not a failure."""
    _install_fetch(monkeypatch, result=None)

    assert cli.main(["fetch"]) == 0

    assert "nasdaq_bars/SPY: nothing new" in _lines(capsys)


@pytest.mark.parametrize(
    ("error", "label"),
    [
        (SchemaError("the payload is not what was expected"), "REFUSED SchemaError"),
        (ParseRefused("body is not JSON"), "REFUSED ParseRefused"),
        (ConnectionError("vendor is down"), "FAILED ConnectionError"),
        (RuntimeError("fetching the url failed after 3 attempts"), "FAILED RuntimeError"),
    ],
    ids=["a-rejected-payload", "an-unparseable-payload", "a-vendor-outage", "exhausted-retries"],
)
@pytest.mark.parametrize(
    "broken",
    [("nasdaq_bars", "SPY"), ("cboe_vix", "VIX9D"), ("fred", "T10Y2Y")],
    ids=["first", "middle", "last"],
)
def test_fetch_exits_one_when_one_fetch_fails_and_still_tries_the_rest(
    broken: tuple[str, str],
    error: Exception,
    label: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One bad source must neither crash the run, nor stop the others, nor be forgotten.

    Failing the first, middle and last fetch each time rules out a CLI that stops at the first
    failure, one that only remembers the last, and one that forgets entirely.
    """
    attempts = _install_fetch(monkeypatch, fail={broken: error})

    assert cli.main(["fetch"]) == 1

    assert attempts == FULL_PLAN, "a failure stopped the run"
    reported = [line for line in _lines(capsys) if label in line]
    assert len(reported) == 1
    assert reported[0].startswith(f"{broken[0]}/{broken[1]}:")


def test_fetch_exits_one_when_everything_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = {step: ConnectionError("down") for step in FULL_PLAN}
    attempts = _install_fetch(monkeypatch, fail=broken)

    assert cli.main(["fetch"]) == 1
    assert attempts == FULL_PLAN


def test_build_raw_and_curate_exit_zero_on_an_empty_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to replay and nothing to type is not a failure, and neither verb can fetch."""
    attempts = _install_fetch(monkeypatch)

    assert cli.main(["build-raw"]) == 0
    assert cli.main(["curate"]) == 0
    assert attempts == []


# -- 2. curate tolerates a missing raw dataset --------------------------------------------------


def test_curate_skips_every_series_with_no_raw_data_and_exits_zero(
    synthetic_store: SyntheticStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """The state the repository is actually in for FRED, taken to its limit: nothing at all."""
    assert cli.main(["curate"]) == 0

    skipped = [line for line in _lines(capsys) if line.endswith(SKIPPED)]
    assert skipped == (
        [f"bars/{symbol}: {SKIPPED}" for symbol in UNIVERSE]
        + [f"vix/{symbol}: {SKIPPED}" for symbol in VIX_FAMILY]
        + [f"fred/{symbol}: {SKIPPED}" for symbol in FRED_FAMILY]
    )
    assert not (Path(synthetic_store.root) / Tier.CURATED.value).exists()


def test_curate_carries_on_past_a_missing_fred_and_still_curates_the_rest(
    synthetic_store: SyntheticStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """FRED has no raw data (its host is serving a maintenance page); bars and VIX still must."""
    _seed_raw(synthetic_store, "nasdaq_bars", "cboe_vix")

    assert cli.main(["curate"]) == 0

    output = _lines(capsys)
    for symbol in FRED_FAMILY:
        assert f"fred/{symbol}: {SKIPPED}" in output
    assert not any(line.startswith("bars/") and SKIPPED in line for line in output)
    for symbol in UNIVERSE:
        assert _curated(synthetic_store, BARS_DATASET, symbol) == DEV_BARS
    for symbol in VIX_FAMILY:
        assert _curated(synthetic_store, VIX_DATASET, symbol) == VIX_SESSIONS
    assert _curated(synthetic_store, FRED_DATASET, "BAA10Y") == []


def test_curate_resumes_after_a_skip_rather_than_stopping_at_it(
    synthetic_store: SyntheticStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """Skips come first here (bars, then VIX) and FRED is processed after them, not before."""
    _seed_raw(synthetic_store, "fred", only=("BAA10Y",))

    assert cli.main(["curate"]) == 0

    output = _lines(capsys)
    assert f"bars/SPY: {SKIPPED}" in output
    assert f"vix/VVIX: {SKIPPED}" in output
    assert f"fred/DGS10: {SKIPPED}" in output, "a skip after the success must not end the run"
    assert "fred/BAA10Y: 3 partitions" in output
    assert len(_curated(synthetic_store, FRED_DATASET, "BAA10Y")) == 3


def test_curate_skips_only_a_missing_dataset_and_not_a_broken_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absence is tolerated; a raw dataset that is present and wrong must still stop the run."""

    def broken(symbol: str, **kwargs: object) -> int:
        raise SchemaError(f"bars/{symbol} could not be parsed")

    monkeypatch.setattr(cli, "curate_bars", broken)

    with pytest.raises(SchemaError, match="could not be parsed"):
        cli.main(["curate"])


def test_curate_types_raw_but_does_not_replay_the_archive(
    synthetic_store: SyntheticStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """The verbs are separable: an archived payload nobody has built is not yet curatable."""
    _seed_archive(synthetic_store, "nasdaq_bars", "cboe_vix", "fred")

    assert cli.main(["curate"]) == 0

    assert len([line for line in _lines(capsys) if line.endswith(SKIPPED)]) == 10
    assert not (Path(synthetic_store.root) / Tier.RAW.value).exists()
    assert not (Path(synthetic_store.root) / Tier.CURATED.value).exists()


# -- 3. the holdout seal ------------------------------------------------------------------------


def _prepare(verb: str, store: SyntheticStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave the store in the state ``verb`` expects to find, with the future in it."""
    _install_real_fetch(monkeypatch, _routed_transport())
    if verb == "build-raw":
        _seed_archive(store, "nasdaq_bars", "cboe_vix", "fred")
    elif verb == "curate":
        _seed_raw(store, "nasdaq_bars", "cboe_vix", "fred")


@pytest.mark.parametrize("verb", VERBS)
def test_no_verb_reads_past_dev_end(
    verb: str, synthetic_store: SyntheticStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load-bearing property. Whatever the verb reads, it reads with ``end <= dev_end``.

    The store holds two sessions after ``dev_end``, so a CLI that read one boundary late would pass
    its own end and curate them. Recorded before delegation, so a refused read still counts.
    """
    _prepare(verb, synthetic_store, monkeypatch)
    calls = _record_reads(monkeypatch)

    assert cli.main([verb]) == 0

    dev_end = synthetic_store.settings.splits.dev_end
    assert dev_end == DEV_END
    assert all(isinstance(call["end"], dt.date) and call["end"] <= dev_end for call in calls)
    assert all(not call.get("allow_holdout") for call in calls)
    if verb in ("curate", "backfill"):
        assert calls, "the verb never read the store, so this test proved nothing"
        assert {call["end"] for call in calls} == {dev_end}
    for symbol in UNIVERSE:
        assert max(_curated(synthetic_store, BARS_DATASET, symbol), default=DEV_END) <= dev_end


@pytest.mark.parametrize("verb", ["build-raw", "backfill"])
def test_ingestion_writes_past_dev_end_but_curation_does_not(
    verb: str, synthetic_store: SyntheticStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only reads are sealed. Raw must capture the holdout rows or they could never be scored."""
    _prepare(verb, synthetic_store, monkeypatch)

    assert cli.main([verb]) == 0

    for symbol in UNIVERSE:
        raw = _partition_dates(synthetic_store, Tier.RAW, "nasdaq_bars", symbol)
        assert raw == DEV_BARS + FUTURE_BARS
        if verb == "backfill":
            assert _curated(synthetic_store, BARS_DATASET, symbol) == DEV_BARS


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize(
    "argument",
    [
        ["--end", "2024-01-03"],
        ["--end=2024-01-03"],
        ["--allow-holdout"],
        ["--unlock"],
        ["--through", "2024-01-03"],
    ],
    ids=["end", "end-equals", "allow-holdout", "unlock", "through"],
)
def test_there_is_no_way_to_ask_for_a_later_end(
    verb: str,
    argument: list[str],
    synthetic_store: SyntheticStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main([verb, *argument])

    assert raised.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert list(Path(synthetic_store.root).iterdir()) == [], "a rejected argument still wrote"


def test_the_parser_defines_no_option_that_could_move_the_boundary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    options = capsys.readouterr().out.split("options:")[1]
    assert set(re.findall(r"(?<![\w-])--?[a-z][\w-]*", options)) == {"-h", "--help", "--source"}


# -- 4. --source --------------------------------------------------------------------------------


@pytest.mark.parametrize("verb", VERBS)
def test_an_unknown_source_exits_rather_than_silently_doing_nothing(
    verb: str, synthetic_store: SyntheticStore
) -> None:
    """``fetch_source`` is the forbidden one here: no verb may fetch before refusing."""
    with pytest.raises(SystemExit) as raised:
        cli.main([verb, "--source", "nasdaq_barz"])

    message = str(raised.value.code)
    assert "unknown source 'nasdaq_barz'" in message
    for source in SOURCES:
        assert source in message
    assert list(Path(synthetic_store.root).iterdir()) == []


def test_the_exogenous_series_match_the_configured_ones() -> None:
    """The plan's exogenous half is the configured list, not a copy of it.

    ``load_settings`` directly rather than the patched ``cli.load_settings``: what has to hold
    is that the repository's own configuration is fully served, which a synthetic one cannot
    show. The two source lists must partition ``exogenous`` exactly -- same set, same length,
    no overlap -- so neither a dropped series nor one fetched twice can hide here.
    """
    settings = load_settings()

    assert set(EXOGENOUS_SOURCE) == set(settings.exogenous)
    assert set(EXOGENOUS_SOURCE.values()) <= set(SOURCES)

    planned = dict(cli._plan(settings))
    exogenous = planned["cboe_vix"] + planned["fred"]
    assert set(exogenous) == set(settings.exogenous)
    assert len(exogenous) == len(settings.exogenous)
    assert not set(planned["cboe_vix"]) & set(planned["fred"])


def test_a_configured_series_no_source_can_serve_stops_the_run(
    synthetic_store: SyntheticStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure mode the map replaces: a series added to the configuration and never fetched."""
    unplaced = dataclasses.replace(
        synthetic_store.settings, exogenous=(*synthetic_store.settings.exogenous, "SOFR")
    )
    monkeypatch.setattr(cli, "load_settings", lambda: unplaced)

    with pytest.raises(SystemExit) as raised:
        cli.main(["fetch"])

    assert "SOFR" in str(raised.value.code)
    assert list(Path(synthetic_store.root).iterdir()) == []


@pytest.mark.parametrize(
    ("source", "symbols"),
    [
        ("nasdaq_bars", UNIVERSE),
        ("nasdaq_dividends", UNIVERSE),
        ("cboe_vix", VIX_FAMILY),
        ("fred", FRED_FAMILY),
    ],
)
def test_source_limits_fetch_to_that_sources_series(
    source: str, symbols: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _install_fetch(monkeypatch)

    assert cli.main(["fetch", "--source", source]) == 0

    assert attempts == [(source, symbol) for symbol in symbols]


def test_source_limits_build_raw_and_curate_to_that_source(
    synthetic_store: SyntheticStore, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_archive(synthetic_store, "nasdaq_bars", "cboe_vix")

    assert cli.main(["build-raw", "--source", "cboe_vix"]) == 0

    assert _partition_dates(synthetic_store, Tier.RAW, "cboe_vix", "VIX") == VIX_SESSIONS
    assert not (Path(synthetic_store.root) / Tier.RAW.value / "nasdaq_bars").exists()
    capsys.readouterr()

    _seed_raw(synthetic_store, "nasdaq_bars")
    assert cli.main(["curate", "--source", "cboe_vix"]) == 0

    assert [line for line in _lines(capsys) if line.startswith("bars/")] == []
    assert _curated(synthetic_store, VIX_DATASET, "VIX") == VIX_SESSIONS
    assert _curated(synthetic_store, BARS_DATASET, "SPY") == []


# -- a source that is curated elsewhere ---------------------------------------------------------

DIVIDENDS_NOTE = (
    "nasdaq_dividends: curated as part of nasdaq_bars, which reads it;"
    " it has no curated dataset of its own"
)


def test_curating_dividends_says_where_it_is_curated_and_does_no_work(
    synthetic_store: SyntheticStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dividends have no curated dataset: ``curate_bars`` reads them to set ``has_distribution``.

    The request is coherent, so it exits 0, but it must not do so in silence.
    """
    _seed_raw(synthetic_store, "nasdaq_bars", "nasdaq_dividends")
    reads = _record_reads(monkeypatch)

    assert cli.main(["curate", "--source", "nasdaq_dividends"]) == 0

    assert _lines(capsys)[2:] == [DIVIDENDS_NOTE]
    assert reads == [], "explaining must not read, let alone curate, anything"
    assert not (Path(synthetic_store.root) / Tier.CURATED.value).exists()


def test_a_full_curate_accounts_for_dividends_too(
    synthetic_store: SyntheticStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ``--source`` the note appears alongside the real work, and bars still curate."""
    _seed_raw(synthetic_store, "nasdaq_bars", "nasdaq_dividends")

    assert cli.main(["curate"]) == 0

    output = _lines(capsys)
    assert output.count(DIVIDENDS_NOTE) == 1
    assert "bars/SPY: 2 partitions" in output
    assert _curated(synthetic_store, BARS_DATASET, "SPY") == DEV_BARS


@pytest.mark.parametrize("source", SOURCES)
def test_no_source_can_be_curated_in_silence(
    source: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The class, not the instance: every valid ``--source`` says something when curated.

    On an empty store the three curated sources each print a skip, so this does not depend on
    any data. A source added to the plan with no curation rule would print nothing here.
    """
    assert cli.main(["curate", "--source", source]) == 0

    assert _lines(capsys)[2:], f"curate --source {source} exited 0 and said nothing"


def test_a_source_with_no_curation_rule_is_an_error_not_a_silent_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forgetting to teach ``_curate`` about a new source must be loud."""
    monkeypatch.setattr(cli, "_plan", lambda settings: [("mystery", ("X",))])

    with pytest.raises(RuntimeError, match="mystery"):
        cli.main(["curate"])


def test_backfill_of_dividends_fetches_and_builds_raw_and_explains_the_curate_stage(
    synthetic_store: SyntheticStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The source is valid to fetch and to build raw; only curate is 'elsewhere'."""
    _install_real_fetch(monkeypatch, _routed_transport())
    root = Path(synthetic_store.root)

    assert cli.main(["backfill", "--source", "nasdaq_dividends"]) == 0

    output = _lines(capsys)
    for symbol in UNIVERSE:
        assert any(line.startswith(f"nasdaq_dividends/{symbol}: archived") for line in output)
    assert "nasdaq_dividends: 0 partitions" in output
    assert DIVIDENDS_NOTE in output
    assert not (root / Tier.CURATED.value).exists()


# -- fetch against the real archive -------------------------------------------------------------


def test_fetch_archives_the_payload_and_writes_no_parquet(
    synthetic_store: SyntheticStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``fetch`` touches the network and the archive and nothing else."""
    _install_real_fetch(monkeypatch, _routed_transport())
    root = Path(synthetic_store.root)

    assert cli.main(["fetch", "--source", "nasdaq_bars"]) == 0

    for symbol in UNIVERSE:
        payloads = list((root / "vendor_payloads" / "nasdaq_bars" / symbol).glob("*.gz"))
        assert len(payloads) == 1
    assert not (root / Tier.RAW.value).exists()
    assert not (root / Tier.CURATED.value).exists()
    assert any("archived" in line for line in _lines(capsys))

    assert cli.main(["fetch", "--source", "nasdaq_bars"]) == 0

    assert "nasdaq_bars/SPY: nothing new" in _lines(capsys)
    assert len(list((root / "vendor_payloads" / "nasdaq_bars" / "SPY").glob("*.gz"))) == 1


def test_fetch_refuses_fred_maintenance_page_and_archives_nothing(
    synthetic_store: SyntheticStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real outage: ``fred.stlouisfed.org`` answering 200 with an HTML page."""
    _install_real_fetch(monkeypatch, _routed_transport(fred=MAINTENANCE_PAGE))

    assert cli.main(["fetch", "--source", "fred"]) == 1

    output = _lines(capsys)
    for symbol in FRED_FAMILY:
        refused = [line for line in output if line.startswith(f"fred/{symbol}:")]
        assert len(refused) == 1
        assert "REFUSED ParseRefused" in refused[0]
        assert "FAILED" not in refused[0], "the response arrived; the fetch did not fail"
    assert not (Path(synthetic_store.root) / "vendor_payloads").exists()


def test_fetch_tells_a_refused_response_from_a_fetch_that_did_not_complete(
    synthetic_store: SyntheticStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both states in one real run, through the real parse: the operator reads these lines.

    FRED answers 200 with a page that is not a CSV (arrived, rejected: REFUSED). Cboe never
    answers (the fetch did not complete: FAILED). Both fail the run; the lines must not swap.
    """
    refusing = _routed_transport(fred=MAINTENANCE_PAGE)

    def transport(url: str) -> bytes:
        if "cboe.com" in url:
            raise RuntimeError(f"fetching {url} failed after 3 attempts: timed out")
        return refusing(url)

    _install_real_fetch(monkeypatch, transport)

    assert cli.main(["fetch"]) == 1

    output = _lines(capsys)
    for symbol in FRED_FAMILY:
        assert any(line.startswith(f"fred/{symbol}: REFUSED ParseRefused") for line in output)
    for symbol in VIX_FAMILY:
        assert any(line.startswith(f"cboe_vix/{symbol}: FAILED RuntimeError") for line in output)
    assert len([line for line in output if "REFUSED" in line]) == len(FRED_FAMILY)
    assert len([line for line in output if "FAILED" in line]) == len(VIX_FAMILY)
    # The healthy sources are still fetched, and the refused one archived nothing.
    assert any(line.startswith("nasdaq_bars/SPY: archived") for line in output)
    assert not (Path(synthetic_store.root) / "vendor_payloads" / "fred").exists()


# Parsers signal a body they cannot read in whatever way the body breaks them, not only with
# ValueError: a row without a key, or a document of the wrong shape, escapes as these.
NOT_VALUE_ERRORS = [
    (
        b'{"data": {"tradesTable": {"rows": [{"close": "1.00"}]}}, "status": {"rCode": 200}}',
        "KeyError",
    ),
    (b'["not", "the", "document", "we", "asked", "for"]', "AttributeError"),
]


@pytest.mark.parametrize(("body", "original"), NOT_VALUE_ERRORS, ids=["missing-key", "wrong-shape"])
def test_fetch_refuses_a_payload_that_breaks_the_parser_without_a_value_error(
    body: bytes,
    original: str,
    synthetic_store: SyntheticStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The response arrived and cannot be read: retrying cannot help, so it is REFUSED.

    Keyed on ``ValueError`` alone, these printed ``FAILED KeyError`` and told the operator to
    retry something that can never succeed. The original exception is named in the line.
    """
    transport = _routed_transport()

    def routed(url: str) -> bytes:
        return body if "/historical" in url else transport(url)

    _install_real_fetch(monkeypatch, routed)

    assert cli.main(["fetch", "--source", "nasdaq_bars"]) == 1

    lines = [line for line in _lines(capsys) if line.startswith("nasdaq_bars/")]
    assert len(lines) == len(UNIVERSE)
    for line in lines:
        assert "REFUSED ParseRefused" in line
        assert original in line, "the original exception must not be lost"
        assert "FAILED" not in line
    assert not (Path(synthetic_store.root) / "vendor_payloads").exists()


def test_a_value_error_from_the_transport_is_a_failed_fetch_not_a_refused_payload(
    synthetic_store: SyntheticStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only the parse is wrapped. Were the transport inside it, a fault there would read REFUSED."""

    def transport(url: str) -> bytes:
        raise ValueError(f"unknown url type: {url!r}")

    _install_real_fetch(monkeypatch, transport)

    assert cli.main(["fetch", "--source", "fred"]) == 1

    lines = [line for line in _lines(capsys) if line.startswith("fred/")]
    assert len(lines) == len(FRED_FAMILY)
    assert all("FAILED ValueError" in line for line in lines)


@pytest.mark.parametrize(("body", "original"), NOT_VALUE_ERRORS, ids=["missing-key", "wrong-shape"])
def test_fetch_source_chains_the_parse_failure_and_archives_nothing(
    body: bytes, original: str, synthetic_store: SyntheticStore
) -> None:
    """The parse still happens before the archive write, and the wrapper keeps the cause."""
    with pytest.raises(ParseRefused) as raised:
        fetch_source(
            "nasdaq_bars",
            "SPY",
            transport=lambda url: body,
            fetched_at=FETCHED,
            settings=synthetic_store.settings,
        )

    assert type(raised.value.__cause__).__name__ == original
    assert not (Path(synthetic_store.root) / "vendor_payloads").exists()


def test_a_run_with_only_refusals_still_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refusal is not a lesser failure for the exit code: the run is incomplete either way."""
    _install_real_fetch(monkeypatch, _routed_transport(fred=MAINTENANCE_PAGE))

    assert cli.main(["fetch", "--source", "fred"]) == 1
    assert not any("FAILED" in line for line in _lines(capsys))


def test_the_help_explains_refused_failed_and_the_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])

    assert raised.value.code == 0
    # argparse wraps to the terminal width, so compare on collapsed whitespace.
    text = " ".join(capsys.readouterr().out.split())
    assert "REFUSED means the response arrived and was rejected" in text
    assert "FAILED means the fetch did not complete" in text
    assert "Exit status 1 if any fetch was REFUSED or FAILED" in text


# -- 5. no network ------------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["build-raw", "curate"])
def test_build_raw_and_curate_never_call_fetch_source(
    verb: str, synthetic_store: SyntheticStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay and typing work from what is already on disk. This fetch records, then succeeds."""
    _seed_raw(synthetic_store, "nasdaq_bars", "cboe_vix", "fred")
    attempts = _install_fetch(monkeypatch)

    assert cli.main([verb]) == 0

    assert attempts == []


# -- 6. backfill --------------------------------------------------------------------------------


def _install_stage_spies(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    def build(source: str, *, symbols: list[str], settings: object) -> int:
        events.append(f"build_raw:{source}:{','.join(symbols)}")
        return 0

    def curator(kind: str) -> Callable[..., int]:
        def curate(symbol: str, *, end: dt.date, settings: object) -> int:
            events.append(f"curate:{kind}:{symbol}")
            return 0

        return curate

    monkeypatch.setattr(cli, "build_raw", build)
    monkeypatch.setattr(cli, "curate_bars", curator("bars"))
    monkeypatch.setattr(cli, "curate_vix", curator("vix"))
    monkeypatch.setattr(cli, "curate_fred", curator("fred"))


def _stages(events: list[str]) -> list[str]:
    return [event.split(":")[0] for event in events]


def test_backfill_runs_fetch_then_build_raw_then_curate(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    _install_stage_spies(monkeypatch, events)

    def fetch(source: str, symbol: str, **kwargs: object) -> Path | None:
        events.append(f"fetch:{source}:{symbol}")
        return None

    monkeypatch.setattr(cli, "fetch_source", fetch)

    assert cli.main(["backfill"]) == 0

    assert _stages(events) == ["fetch"] * 13 + ["build_raw"] * 4 + ["curate"] * 10
    assert events[13] == "build_raw:nasdaq_bars:SPY,QQQ,IWM"


def test_a_fetch_failure_does_not_stop_backfills_later_stages_but_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []
    _install_stage_spies(monkeypatch, events)

    def fetch(source: str, symbol: str, **kwargs: object) -> Path | None:
        events.append(f"fetch:{source}:{symbol}")
        if (source, symbol) == ("fred", "BAA10Y"):
            raise ConnectionError("fred is serving a maintenance page")
        return None

    monkeypatch.setattr(cli, "fetch_source", fetch)

    assert cli.main(["backfill"]) == 1

    assert _stages(events) == ["fetch"] * 13 + ["build_raw"] * 4 + ["curate"] * 10
    assert "fred/BAA10Y: FAILED ConnectionError: fred is serving a maintenance page" in _lines(
        capsys
    )


@pytest.mark.parametrize(
    ("fred_body", "expected_exit", "fred_curated"),
    [(None, 0, 3), (MAINTENANCE_PAGE, 1, 0)],
    ids=["every-source-up", "fred-down"],
)
def test_backfill_end_to_end_from_an_empty_store(
    fred_body: bytes | None,
    expected_exit: int,
    fred_curated: int,
    synthetic_store: SyntheticStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fetch -> raw -> curated in one run, from nothing, with FRED up and with FRED down.

    From an empty store, curated rows can exist only if the stages ran in order: curate before
    build-raw would find no raw data and skip everything.
    """
    _install_real_fetch(monkeypatch, _routed_transport(fred=fred_body))

    assert cli.main(["backfill"]) == expected_exit

    output = _lines(capsys)
    for symbol in UNIVERSE:
        assert _curated(synthetic_store, BARS_DATASET, symbol) == DEV_BARS
    for symbol in VIX_FAMILY:
        assert _curated(synthetic_store, VIX_DATASET, symbol) == VIX_SESSIONS
    assert len(_curated(synthetic_store, FRED_DATASET, "BAA10Y")) == fred_curated
    if fred_body is None:
        assert not any(SKIPPED in line for line in output)
    else:
        assert all(f"fred/{symbol}: {SKIPPED}" in output for symbol in FRED_FAMILY)
        assert _partition_dates(synthetic_store, Tier.RAW, "fred", "BAA10Y") == []


def test_curate_output_matches_what_was_written(
    synthetic_store: SyntheticStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """The partition counts the CLI prints are the counts on disk, not a separate tally."""
    _seed_raw(synthetic_store, "nasdaq_bars")

    assert cli.main(["curate", "--source", "nasdaq_bars"]) == 0

    assert _lines(capsys)[2:] == [f"bars/{symbol}: 2 partitions" for symbol in UNIVERSE]
    frame = load_panel(
        BARS_DATASET,
        symbols=["SPY"],
        end=DEV_END,
        tier=Tier.CURATED,
        settings=synthetic_store.settings,
    )
    assert len(frame) == 2
