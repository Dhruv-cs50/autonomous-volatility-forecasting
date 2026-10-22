"""Raw as the vendor served it becomes a typed, calendar-checked panel.

Curation is the only place strings become numbers, and the rule throughout is **fail loudly**.
A close that quietly became ``NaN`` would propagate into every realized measure built on it and
show up as a plausible-looking number much later; the reason raw is kept faithful is precisely
so that a parse bug found in a later phase is a rebuild rather than destroyed history.

Two checks beyond typing:

- **Every ``as_of`` must be an XNYS session.** A row the calendar does not recognise would sit in
  a partition nothing else aligns to, and would silently misalign every trailing window built on
  the session index.
- **Distributions are flagged, never adjusted.** ``has_distribution`` records that a session was
  ex-distribution; it does not touch the price. Nasdaq's unadjusted closes mean each distribution
  injects a spurious negative return, which flows systematically into negative semivariance --
  the one measure built to treat negative returns differently. Flagging hands Phase 2 the
  information and leaves the adjust-or-exclude decision there, with the facts in hand.

Reads go through ``load_panel`` and writes through ``store.write_curated``, so nothing here
touches Parquet. ``end`` is always the caller's and never exceeds ``dev_end``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

import pandas as pd

from volab.config import Settings, load_settings
from volab.errors import DatasetNotFound, SchemaError
from volab.ingest.bars import BARS_COLUMNS
from volab.paths import Tier
from volab.sessions import get_calendar, is_session
from volab.store import load_panel, write_curated

BARS_DATASET = "bars_daily"
VIX_DATASET = "vix_family"
FRED_DATASET = "fred_daily"
# The payload every ``vix_family`` partition carries, in the order the vendor lays it out.
_VIX_PAYLOAD: tuple[str, ...] = ("open", "high", "low", "close")


def _as_date(value: object) -> dt.date:
    """Normalise an ``as_of`` to a plain ``date``.

    The store is asymmetric here: a fetcher writes ``as_of`` as ``datetime.date`` objects, but
    ``load_panel`` hands it back as ``datetime64[us]``, so grouping a read frame yields
    ``Timestamp`` keys. Passing one to ``write_curated`` would build a partition named
    ``date=2023-06-16T00:00:00``. The write path refuses that outright, which is how this was
    caught, but the conversion belongs here rather than relying on the refusal.
    """
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    # cast because the value genuinely arrives as whatever the store handed back -- a date, a
    # Timestamp, or a numpy datetime64 -- and pandas is the thing that knows how to read all three.
    return pd.Timestamp(cast(Any, value)).date()


def _to_float(
    value: object, *, column: str, symbol: str, as_of: dt.date, strip: str = ","
) -> float:
    """Parse a vendor number, stripping only what that field legitimately contains.

    ``strip`` names the separators and symbols that field may legitimately arrive wrapped in.
    Those are removed and nothing else is guessed at: anything left that is not a number
    raises. It is narrow per field rather than a blanket "remove punctuation" because bars
    carry thousands separators in volume and no currency symbol at all, so a ``$`` in a price
    means the vendor changed format -- which must fail loudly here, not be quietly absorbed.
    """
    text = str(value).strip().translate(str.maketrans("", "", strip))
    try:
        return float(text)
    except ValueError:
        raise SchemaError(
            f"could not parse {symbol} {column} {value!r} on {as_of} as a number; refusing to "
            "coerce it, because a silent NaN here would propagate into every measure built on it"
        ) from None


def _typed(
    frame: pd.DataFrame, columns: tuple[str, ...], *, symbol: str, strip: str = ","
) -> pd.DataFrame:
    out = frame.copy()
    out["as_of"] = [_as_date(value) for value in out["as_of"]]
    for column in columns:
        out[column] = [
            _to_float(value, column=column, symbol=symbol, as_of=as_of, strip=strip)
            for value, as_of in zip(out[column], out["as_of"], strict=True)
        ]
    return out


def _reject_non_sessions(frame: pd.DataFrame, *, symbol: str, settings: Settings) -> pd.DataFrame:
    """For bars: a row the calendar does not recognise is a defect, so refuse the dataset.

    A bar dated on a non-session means the bars source disagrees with the calendar about when
    the market was open, and that disagreement would corrupt the return series itself. There is
    no safe way to absorb it.
    """
    calendar = get_calendar(settings)
    for as_of in frame["as_of"].unique():
        day = _as_date(as_of)
        if not is_session(calendar, day):
            raise SchemaError(
                f"{symbol} carries a row dated {day}, which is not an XNYS session; a bar on a "
                "non-session means the source disagrees with the calendar about when the market "
                "was open, which would corrupt the return series"
            )
    return frame


def _drop_non_sessions(frame: pd.DataFrame, *, symbol: str, settings: Settings) -> pd.DataFrame:
    """For exogenous series: drop non-session rows, and report how many.

    Cboe really does publish VIX on days the NYSE is closed -- 33 such rows since 2016-07-01, on
    Memorial Day, Independence Day, Labor Day, Thanksgiving and MLK Day, carrying genuine values;
    VIX9D, VIX1D and VVIX have none. Those values have no equity session to attach to, so they
    can never enter a session-indexed window: keeping them would put rows in the store that no
    join could ever match, while refusing the dataset outright would discard 9,298 usable VIX
    rows over 33 unusable ones.

    Dropping is safe point-in-time, because it removes rows rather than inventing them and the
    dates removed are ones no feature is evaluated on. It is reported rather than silent: a jump
    in the count would mean the vendor changed something.
    """
    calendar = get_calendar(settings)
    keep = [is_session(calendar, _as_date(value)) for value in frame["as_of"]]
    dropped = len(keep) - sum(keep)
    if dropped:
        print(f"    {symbol}: dropped {dropped} row(s) dated outside XNYS sessions")
    return frame[keep].reset_index(drop=True)


def _write_partitions(frame: pd.DataFrame, *, dataset: str, symbol: str, settings: Settings) -> int:
    """One partition per ``as_of``, because ``write_curated`` validates a single partition."""
    written = 0
    for as_of, partition in frame.groupby("as_of", sort=True):
        day: dt.date = as_of  # type: ignore[assignment]
        write_curated(
            partition.reset_index(drop=True),
            dataset=dataset,
            symbol=symbol,
            as_of=day,
            settings=settings,
        )
        written += 1
    return written


def _distribution_dates(symbol: str, *, end: dt.date, settings: Settings) -> set[dt.date]:
    """Ex-distribution sessions for ``symbol``, or an empty set when the vendor serves none.

    Verified 2026-10-19: Nasdaq's dividends endpoint returns rows for QQQ only; SPY and IWM come
    back empty under every ``assetclass``, so ``build_raw`` writes no partitions for them and
    this read legitimately finds nothing. An absent dividend source is not an error -- it means
    the flag is uniformly ``False`` and the bias it would have marked stays documented rather
    than detected.
    """
    try:
        frame = load_panel(
            "nasdaq_dividends",
            symbols=[symbol],
            end=end,
            tier=Tier.RAW,
            settings=settings,
        )
    except DatasetNotFound:
        return set()
    return {_as_date(value) for value in frame["as_of"]}


def curate_bars(symbol: str, *, end: dt.date, settings: Settings | None = None) -> int:
    """Type one symbol's daily bars and flag its distribution dates. Returns partitions written."""
    resolved = load_settings() if settings is None else settings
    raw = load_panel("nasdaq_bars", symbols=[symbol], end=end, tier=Tier.RAW, settings=resolved)
    _reject_non_sessions(raw, symbol=symbol, settings=resolved)
    frame = _typed(raw, BARS_COLUMNS, symbol=symbol)
    distributions = _distribution_dates(symbol, end=end, settings=resolved)
    frame["has_distribution"] = [as_of in distributions for as_of in frame["as_of"]]
    return _write_partitions(frame, dataset=BARS_DATASET, symbol=symbol, settings=resolved)


def _pad_to_ohlc(frame: pd.DataFrame, *, series: str) -> pd.DataFrame:
    """Give a close-only series explicit null ``open``/``high``/``low``.

    Raw is faithful to the vendor and curated is normalised, and this is the normalisation.
    Cboe publishes only a close for VVIX, so its raw files rightly carry two payload columns
    where VIX, VIX9D and VIX1D carry four. Left alone that would make a curated *dataset* whose
    schema depends on which symbol a file belongs to, and a single ``load_panel`` over the family
    would hit a schema mismatch. The store's scan refuses to paper over that by unifying columns
    by name, because the same leniency would let a bars file that lost its ``volume`` column to a
    parse bug quietly unify instead of raising. So the schema is made uniform here, where the
    fact -- "the vendor publishes no open for this series" -- is actually known.

    The nulls are genuine floats, never zeros and never filled from the close: a reader must be
    able to tell "Cboe publishes no open for VVIX" from "the open was 0".

    Only the exact close-only shape is padded. A series with some but not all of the four, or
    with no close at all, has lost a column rather than never had it, and is refused.
    """
    present = tuple(name for name in _VIX_PAYLOAD if name in frame.columns)
    if "close" not in present:
        raise SchemaError(
            f"cboe_vix/{series} carries no close column (found {list(present)}); a series with "
            "no close is not a volatility index series, so curation refuses it"
        )
    if present == _VIX_PAYLOAD:
        return frame
    if present != ("close",):
        missing = [name for name in _VIX_PAYLOAD if name not in present]
        raise SchemaError(
            f"cboe_vix/{series} carries {list(present)} but not {missing}; only a close-only "
            "series is padded, because padding a series that lost a column would hide the loss"
        )
    padded = frame.copy()
    for name in _VIX_PAYLOAD:
        if name != "close":
            padded[name] = pd.Series(float("nan"), index=padded.index, dtype="float64")
    # The same column order for every series: a dataset's files must agree on position as well as
    # name, and the OHLC block sits last in the vendor's own layout.
    leading = [name for name in padded.columns if name not in _VIX_PAYLOAD]
    return padded[[*leading, *_VIX_PAYLOAD]]


def curate_vix(series: str, *, end: dt.date, settings: Settings | None = None) -> int:
    """Type one Cboe index series into the ``vix_family`` dataset.

    Every partition leaves here with the same four payload columns. A close-only series (VVIX)
    gets explicit null ``open``/``high``/``low``; see ``_pad_to_ohlc`` for why.
    """
    resolved = load_settings() if settings is None else settings
    raw = load_panel("cboe_vix", symbols=[series], end=end, tier=Tier.RAW, settings=resolved)
    raw = _drop_non_sessions(raw, symbol=series, settings=resolved)
    if raw.empty:
        raise DatasetNotFound(f"cboe_vix/{series} has no rows on an XNYS session up to {end}")
    present = tuple(name for name in _VIX_PAYLOAD if name in raw.columns)
    frame = _pad_to_ohlc(_typed(raw, present, symbol=series), series=series)
    return _write_partitions(frame, dataset=VIX_DATASET, symbol=series, settings=resolved)


def curate_fred(series: str, *, end: dt.date, settings: Settings | None = None) -> int:
    """Type one FRED series.

    The fetcher already lags ``as_of`` to the next session, so the filter should be a no-op
    here. It is applied anyway, because "should be a no-op" is exactly the assumption the VIX
    holiday rows disproved for Cboe.
    """
    resolved = load_settings() if settings is None else settings
    raw = load_panel("fred", symbols=[series], end=end, tier=Tier.RAW, settings=resolved)
    raw = _drop_non_sessions(raw, symbol=series, settings=resolved)
    if raw.empty:
        raise DatasetNotFound(f"fred/{series} has no rows on an XNYS session up to {end}")
    frame = _typed(raw, ("value",), symbol=series)
    return _write_partitions(frame, dataset=FRED_DATASET, symbol=series, settings=resolved)
