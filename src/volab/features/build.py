"""The dated feature table: one row per session, every column a trailing window or a passthrough.

``build_features`` takes an **already-loaded panel** and a separate evaluation cutoff, and that
split is the most important structural decision in the phase. The panel it is handed may extend
past ``through``, and it must still emit exactly what it would have emitted had the later rows
never existed.

The tempting alternative -- have the engine read the store itself at ``end=t`` and let the
point-in-time filter guarantee honesty -- would make the phase's exit criterion vacuous. The
store drops every row with ``as_of > end`` before any feature code runs, so a feature that
peeked forward would produce identical output whether or not future rows existed on disk, and
the perturbation test could never fail. It is also the realistic leak: Phase 2's walk-forward
loads the dev panel **once**, legitimately, and then refits and evaluates at many earlier
cutoffs. Every row it holds is one the store was right to hand over, so the store cannot help;
the only thing between that panel and a leak is whether the feature functions look forward.
Holding rows it must not use is therefore not a lapse in posture -- it is the condition under
which this function's honesty means anything. Nothing here reads the store; the CLI does.

Three things this module owns, which the pure functions in ``realized`` and ``har`` cannot:

* **Order.** Those windows are positional and never see a date, so a descending panel would
  silently yield forward-looking features -- and the AST scan over this package could not catch
  it, because the scan reads syntax and this is a property of the data. Asserted once, here,
  where the series is assembled, and refused rather than quietly sorted: a panel in an order the
  caller did not expect is a disagreement about the data, and silent repair is how a real
  disagreement goes unnoticed.
* **The session index.** Everything is computed on the calendar's sessions rather than on the
  rows that happen to be present, so ``rolling(22)`` means 22 *sessions*. A session with no bar
  becomes an explicit null row, which poisons the windows spanning it instead of letting a halt
  or an outage silently shorten every one of them.
* **The cutoff.** Applied as the very last step, so no computation can depend on where it fell.

Incomplete windows are null, never partially filled, and rows with null features are written
rather than dropped, so the table has one row per session. Nothing is forward-filled,
back-filled or imputed anywhere. An exogenous series that is missing or partial is normal:
VIX1D only begins 2022-05-13 and FRED may be absent entirely, so those columns come out null
rather than crashing the build or vanishing from the schema.
"""

from __future__ import annotations

import datetime as dt
import math

import exchange_calendars as xcals
import pandas as pd

from volab.errors import SchemaError
from volab.features.har import HAR_WINDOWS, har_components
from volab.features.realized import (
    bipower_proxy,
    rv_proxy,
    semivariance_proxy,
    signed_jump_proxy,
)

# The one convention for "when a session's value became knowable", shared with the bars this
# engine reads, so a gap row's stamp cannot drift from a real row's. It costs an import of
# ``volab.ingest``, whose package body imports the store, which is why the import-graph test
# asserts that nothing of the store is *bound* here rather than that it is never imported.
from volab.paths import MANDATORY_COLUMNS
from volab.sessions import session_close_utc, sessions_between

# The trailing windows, in sessions, every realized column is computed over.
WINDOWS: tuple[int, ...] = (1, 5, 22, 66)

# A bipower window pairs adjacent returns, so a one-session window holds no pair at all and the
# column would be entirely null. It is omitted rather than written as nulls.
_BIPOWER_MINIMUM = 2

CLOSE_COLUMN = "close"
VALUE_COLUMN = "value"
DISTRIBUTION_COLUMN = "has_distribution"

# Horizon matching follows the research: VIX1D for one session, VIX9D (9 calendar days, about 6
# sessions) for five, VIX (30 calendar days, about 21 sessions) for 22.
IV_SERIES: dict[int, str] = {1: "VIX1D", 5: "VIX9D", 22: "VIX"}
VVIX_SERIES = "VVIX"
FRED_SERIES: dict[str, str] = {"baa10y": "BAA10Y", "dgs10": "DGS10", "t10y2y": "T10Y2Y"}
# Named rather than inferred: the panel holds one symbol's bars beside these, and a typo'd
# ticker must not be able to pass itself off as an exogenous series or as the subject.
EXOGENOUS_SERIES: frozenset[str] = frozenset(
    {*IV_SERIES.values(), VVIX_SERIES, *FRED_SERIES.values()}
)
# Sessions in a year, for scaling an annualised implied vol to a horizon variance.
SESSIONS_PER_YEAR = 252


def _realized_columns() -> tuple[str, ...]:
    names: list[str] = []
    for window in WINDOWS:
        names.append(f"rv_proxy_d{window}")
        names.append(f"semivar_neg_proxy_d{window}")
        names.append(f"semivar_pos_proxy_d{window}")
        names.append(f"signed_jump_proxy_d{window}")
        if window >= _BIPOWER_MINIMUM:
            names.append(f"bipower_proxy_d{window}")
    return tuple(names)


# The persisted HAR columns take the ``_proxy`` suffix even though ``har_components``' dict keys
# are bare. Every daily-return estimator carries it, because a reader months later must not
# mistake one for an intraday measure -- and a persisted column is exactly where that confusion
# happens. The dict keys stay bare: they are internal to a function whose docstring says what
# they are made of.
HAR_COLUMNS: tuple[str, ...] = tuple(f"{name}_proxy" for name in HAR_WINDOWS)

# Exactly the spec's table, in order.
FEATURE_COLUMNS: tuple[str, ...] = (
    *_realized_columns(),
    *HAR_COLUMNS,
    *(f"iv_h{horizon}" for horizon in IV_SERIES),
    *(f"iv_var_h{horizon}" for horizon in IV_SERIES),
    "vvix",
    *FRED_SERIES,
    DISTRIBUTION_COLUMN,
)
COLUMNS: tuple[str, ...] = (*MANDATORY_COLUMNS, *FEATURE_COLUMNS)

# What the subject's own rows must carry beyond the mandatory four. Curated bars hold raw OHLCV
# and the distribution flag; the close is all this engine reads of the price.
_BARS_COLUMNS: tuple[str, ...] = (CLOSE_COLUMN, DISTRIBUTION_COLUMN)


def log_returns(close: pd.Series) -> pd.Series:
    """``log(C_t / C_{t-1})`` down a session-indexed close series.

    The first row is null, because no previous session exists to divide by, and so is any row
    whose own or whose predecessor's close is null -- which is how a session with no bar poisons
    the two returns spanning it rather than being bridged by a return over two sessions.

    ``shift(1)`` looks one *row* back, so the caller must have reindexed onto the calendar's
    sessions first: only then is the previous row the previous session. Unlike the pure
    functions in ``realized`` and ``har`` this one is handed a date index, so it can check that
    for itself, and does.

    Curated bars keep raw OHLCV and carry no return column, so this is the one place in the
    project a return is derived. It is public so the CLI can reuse it for the panel the targets
    are built from, rather than deriving returns a second way.
    """
    if not (close.index.is_monotonic_increasing and close.index.is_unique):
        raise SchemaError(
            "cannot derive log returns: the close series must be indexed by ascending, unique "
            "sessions, because the previous row is the previous session only when it is"
        )
    nonpositive = close[close <= 0]
    if not nonpositive.empty:
        raise SchemaError(
            f"cannot derive log returns: {len(nonpositive)} session(s) carry a close that is "
            f"not positive, first {nonpositive.iloc[0]} on {nonpositive.index[0]}; a log return "
            "is undefined there and coercing it would invent a number"
        )
    ratio = close / close.shift(1)
    # ``math.log`` row by row rather than a vectorised log: numpy is only a transitive dependency
    # of pandas and is absent from pyproject.toml, so nothing here may import it. The dtype is
    # pinned so an all-null column cannot arrive as ``object``.
    return pd.Series(
        [float("nan") if pd.isna(value) else math.log(value) for value in ratio],
        index=close.index,
        dtype="float64",
    )


def _sessions_of(rows: pd.DataFrame) -> list[dt.date]:
    """The session each row belongs to, as plain dates, read from ``as_of``.

    The store is asymmetric here: a write takes ``datetime.date`` while a read hands the column
    back as ``datetime64[us]``, so the same column arrives as dates in one caller and as
    ``Timestamp`` in another. ``pd.to_datetime`` reads all three spellings, which is how
    ``store``'s own normaliser does it -- re-derived in one expression rather than adding a
    fourth copy of the ``_as_date`` helper that ``targets`` and ``curate`` each carry.
    """
    return list(pd.to_datetime(rows["as_of"]).dt.date)


def _subject(panel: pd.DataFrame) -> str:
    """The one symbol whose bars the table is being built for.

    The panel carries that symbol's bars **and** the exogenous series, because the exogenous
    columns are joined here, on ``as_of``. The subject is therefore whatever is not one of the
    named exogenous series.
    """
    subjects = sorted({str(value) for value in panel["symbol"].unique()} - EXOGENOUS_SERIES)
    if len(subjects) != 1:
        raise SchemaError(
            "cannot build features: the panel must hold exactly one symbol's bars beside the "
            f"exogenous series {sorted(EXOGENOUS_SERIES)}, got {subjects}"
        )
    return subjects[0]


def _require_ascending_sessions(panel: pd.DataFrame) -> None:
    """Refuse a panel that is not in ascending session order, one symbol at a time.

    This is the guard the pure functions deliberately do not carry. Their windows are
    positional, they never see a date, and they are hot; the one place to assert ordering is
    where their input is assembled. Per symbol, because a panel read from the store is sorted by
    ``(symbol, ts)`` and so is only ever ascending *within* a symbol.

    Both ``ts`` and ``as_of`` are checked, and ``as_of`` is the load-bearing one: it is the
    session every window and every join below is keyed on, so a panel whose ``as_of`` order
    disagrees with its ``ts`` order would otherwise be silently reordered by the reindex. A
    repeated session is refused here too, where it can say what is wrong, rather than reaching
    the reindex and surfacing as a bare pandas "axis with duplicate labels".
    """
    if not isinstance(panel["ts"].dtype, pd.DatetimeTZDtype):
        raise SchemaError(
            f"cannot build features: column 'ts' must be timezone-aware, got {panel['ts'].dtype}"
        )
    for symbol, rows in panel.groupby("symbol", sort=True):
        stamps = rows["ts"]
        if not bool(stamps.is_monotonic_increasing):
            raise SchemaError(
                f"cannot build features: {symbol}'s rows are not in ascending ts order; every "
                "trailing window here is positional, so a descending panel would silently yield "
                "forward-looking features"
            )
        if bool(stamps.duplicated().any()):
            raise SchemaError(
                f"cannot build features: {symbol} has more than one row for a ts, so no window "
                "can say which of them the session's value is"
            )
        days = _sessions_of(rows)
        if days != sorted(days):
            raise SchemaError(
                f"cannot build features: {symbol}'s rows are not in ascending as_of order; "
                "as_of is the session every window and every join is keyed on, so an ascending "
                "ts is not enough to establish the order the panel is in"
            )
        if len(set(days)) != len(days):
            raise SchemaError(
                f"cannot build features: {symbol} has more than one row for a session, so no "
                "window can say which of them the session's value is"
            )


def _on_sessions(values: pd.Series, days: list[dt.date], sessions: list[dt.date]) -> pd.Series:
    """``values``, re-keyed from the rows' own sessions onto every session in the span.

    A session with no row becomes an explicit null, which is what makes every window below count
    sessions rather than surviving rows. Nothing is filled.
    """
    return values.set_axis(days).reindex(sessions)


def _exogenous_column(
    panel: pd.DataFrame, series: str, column: str, sessions: list[dt.date]
) -> pd.Series:
    """One exogenous series, joined onto the session index on ``as_of``.

    Joining on ``as_of`` rather than on the vendor's observation date is what keeps a lagged
    series honest: ``as_of`` is the session the value first became knowable, which for FRED is
    the session after the observation.

    Absent is normal, not an error. VIX1D only begins 2022-05-13 and FRED may be missing
    entirely, so a series with no rows -- or a panel carrying no such payload column at all --
    yields a null column rather than a missing one or a failure.
    """
    rows = panel[panel["symbol"] == series]
    if rows.empty or column not in panel.columns:
        return pd.Series(float("nan"), index=sessions, dtype="float64")
    days = _sessions_of(rows)
    # Already proved by the ordering guard, which runs over every symbol. Kept because it costs
    # one comparison, and the alternative if the two ever drift is a bare pandas ValueError from
    # the reindex below instead of a sentence naming the series.
    if len(set(days)) != len(days):
        raise SchemaError(
            f"cannot build features: {series} has more than one row for a session, so the "
            "session's value is ambiguous"
        )
    return _on_sessions(rows[column].astype("float64"), days, sessions)


def build_features(
    panel: pd.DataFrame, *, through: dt.date, calendar: xcals.ExchangeCalendar
) -> pd.DataFrame:
    """The feature table for one symbol, for every session at or before ``through``.

    ``panel`` holds that symbol's curated bars concatenated with the curated exogenous series --
    the VIX family on ``close``, the FRED levels on ``value`` -- keyed by ``symbol`` and joined
    here on ``as_of``. **It may extend past ``through``**, and the output must not move when it
    does; that is the property the phase exists to prove.

    One row per session from the subject's first to its last, up to ``through``, with null
    features wherever a window is incomplete. A session with no bar is still a row: its ``ts``
    is the calendar's close for that session, and its ``ingested_at`` is null, because nothing
    was ingested for a bar that was never served.
    """
    missing = [name for name in MANDATORY_COLUMNS if name not in panel.columns]
    if missing:
        raise SchemaError(f"cannot build features: the panel lacks mandatory column(s) {missing}")
    if panel.empty:
        return pd.DataFrame({name: [] for name in COLUMNS})

    symbol = _subject(panel)
    absent = [name for name in _BARS_COLUMNS if name not in panel.columns]
    if absent:
        raise SchemaError(
            f"cannot build features for {symbol}: the panel lacks column(s) {absent}; it must "
            "carry curated bars, which hold the raw OHLCV and the distribution flag"
        )
    _require_ascending_sessions(panel)

    bars = panel[panel["symbol"] == symbol]
    days = _sessions_of(bars)
    # Every computation runs on the calendar's sessions, before the cutoff is applied.
    sessions = sessions_between(calendar, min(days), max(days))
    stray = sorted(set(days) - set(sessions))
    if stray:
        raise SchemaError(
            f"cannot build features for {symbol}: {len(stray)} row(s) are dated outside the "
            f"{calendar.name} sessions, first {stray[0]}; reindexing onto the session index "
            "would drop them silently"
        )

    close = _on_sessions(bars[CLOSE_COLUMN].astype("float64"), days, sessions)
    returns = log_returns(close)

    # A gap session's row needs a ts of its own, and a null one will not do: the store's read
    # path dedupes on ``(symbol, ts)``, pandas treats NaT as equal to NaT, so two or more gap
    # rows would collapse into a single row and then sort after every real one -- losing both
    # "one row per session" and ascending order exactly when a vendor omits sessions, which is
    # the case the session index exists for. The fallback is the calendar's own 16:00 ET close
    # for that session: a function of the calendar, not an invented bar timestamp, and the same
    # convention the bars themselves carry. ``ingested_at`` stays null, because nothing was
    # ingested; the dedupe sorts on ``as_of``, and the full-key tie check does not collapse rows
    # that differ in it.
    canonical = pd.Series(
        pd.to_datetime([session_close_utc(session) for session in sessions], utc=True),
        index=sessions,
    )
    data: dict[str, pd.Series] = {
        "symbol": pd.Series([symbol] * len(sessions), index=sessions),
        "ts": _on_sessions(bars["ts"], days, sessions).fillna(canonical),
        "as_of": pd.Series(sessions, index=sessions),
        "ingested_at": _on_sessions(bars["ingested_at"], days, sessions),
    }
    for window in WINDOWS:
        data[f"rv_proxy_d{window}"] = rv_proxy(returns, window=window)
        data[f"semivar_neg_proxy_d{window}"] = semivariance_proxy(
            returns, window=window, sign="neg"
        )
        data[f"semivar_pos_proxy_d{window}"] = semivariance_proxy(
            returns, window=window, sign="pos"
        )
        data[f"signed_jump_proxy_d{window}"] = signed_jump_proxy(returns, window=window)
        if window >= _BIPOWER_MINIMUM:
            data[f"bipower_proxy_d{window}"] = bipower_proxy(returns, window=window)
    for name, component in har_components(returns).items():
        data[f"{name}_proxy"] = component

    for horizon, series in IV_SERIES.items():
        implied = _exogenous_column(panel, series, CLOSE_COLUMN, sessions)
        data[f"iv_h{horizon}"] = implied
        # An annualised vol in percent becomes a variance over the horizon.
        data[f"iv_var_h{horizon}"] = (implied / 100) ** 2 * (horizon / SESSIONS_PER_YEAR)
    data["vvix"] = _exogenous_column(panel, VVIX_SERIES, CLOSE_COLUMN, sessions)
    for name, series in FRED_SERIES.items():
        data[name] = _exogenous_column(panel, series, VALUE_COLUMN, sessions)

    # Nullable rather than object-with-NaN, so a gap session's unknown flag keeps the column's
    # dtype deterministic instead of turning it into a column of Python objects.
    flags = _on_sessions(bars[DISTRIBUTION_COLUMN], days, sessions)
    data[DISTRIBUTION_COLUMN] = flags.astype("boolean")

    # The cutoff, last: nothing above it can depend on where it fell.
    within = [session <= through for session in sessions]
    frame = pd.DataFrame({name: data[name] for name in COLUMNS}, index=sessions)
    return frame[within].reset_index(drop=True)
