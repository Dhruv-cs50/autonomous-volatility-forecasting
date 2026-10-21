"""Forward realized-variance targets, stamped with the session they become knowable.

For a base session ``t`` and horizon ``h`` the target is the sum of the next ``h`` squared daily
returns. It is forward-looking by definition, so it cannot live beside the features: it is kept
in its own dataset and carries

* ``ts``    -- ``t``, the session the forecast is *for*;
* ``as_of`` -- the ``h``-th XNYS session after ``t``, the session its last return is known.

``load_panel(..., end=E)`` filters on ``as_of <= E``, so it can only return a target whose
horizon has fully realised by ``E``. No caller can obtain an unrealised target and no model code
has to remember to exclude one -- the store prevents it structurally. A feature row and a target
row for the same ``t`` carry different ``as_of`` values, which is why they cannot share a table.

The daily-return analogue of realized variance is a *proxy* (see the spec, section 9), so the
target column says so: ``target_rv_proxy_h{h}``.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from volab.config import Settings
from volab.errors import SchemaError
from volab.sessions import nth_session_after, sessions_between
from volab.store import write_features

# The horizons the framework scores throughout, in sessions.
HORIZONS: tuple[int, ...] = (1, 5, 22, 66)

# The column of daily log returns ``build_targets`` reads from its panel.
RETURN_COLUMN = "log_return"

_PANEL_COLUMNS: tuple[str, ...] = ("symbol", "ts", "as_of", "ingested_at", RETURN_COLUMN)


def targets_dataset(horizon: int) -> str:
    """The feature-set name the horizon's targets are stored under."""
    return f"targets_rv_h{horizon}"


def target_column(horizon: int) -> str:
    """The name of the target column for ``horizon``."""
    return f"target_rv_proxy_h{horizon}"


def _as_date(value: object) -> dt.date:
    """Normalise an ``as_of`` to a plain ``date``.

    The store is asymmetric here: a fetcher writes ``as_of`` as ``datetime.date`` objects, but
    ``load_panel`` hands it back as ``datetime64[us]``, so grouping a read frame yields
    ``Timestamp`` keys. Passing one to ``write_features`` would build a partition named
    ``date=2023-06-16T00:00:00``. The write path refuses that outright, but the conversion
    belongs here rather than relying on the refusal.
    """
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    raise SchemaError(f"as_of must be a date, got {value!r} of type {type(value).__name__}")


# This is the ONE legitimate forward offset in the feature package: a target looks ahead by
# definition. The AST scan over ``src/volab/features/`` (Task 9, ``test_no_feature_module_looks_
# forward``) fails on a negative ``shift``, ``center=True`` and forward slicing, so this function
# is carried there as an explicit allow-list entry. Do not copy the pattern into any other
# function; a forward offset anywhere else is a leak.
def forward_rv_proxy(returns: pd.Series, *, horizon: int) -> pd.Series:
    """At position ``i``, the sum of ``returns[i+1] ** 2`` through ``returns[i+horizon] ** 2``.

    ``NaN`` wherever the forward window is incomplete -- the last ``horizon`` positions, and any
    position whose window holds a ``NaN`` return. Never a partial sum.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be at least 1, got {horizon}")
    squared = returns**2
    # Reversed, a trailing window becomes a forward one: after the second reversal position i
    # holds the sum over i..i+horizon-1, and the shift moves it to i+1..i+horizon.
    forward = squared[::-1].rolling(horizon).sum()[::-1]
    return forward.shift(-1)


def build_targets(
    panel: pd.DataFrame, *, horizon: int, calendar: xcals.ExchangeCalendar
) -> pd.DataFrame:
    """The forward targets for one symbol's daily-return panel.

    ``panel`` holds one row per session with ``symbol``, ``ts``, ``as_of`` (the session's date,
    as for bars), ``ingested_at`` and ``log_return``. A base session whose horizon is incomplete
    is omitted: the final ``horizon`` sessions, and any session with a missing bar or return
    inside its window. The window is counted in sessions, not rows, so a session absent from the
    panel makes the windows containing it incomplete rather than quietly reaching a session
    further.

    The result's ``as_of`` is a plain ``date`` -- the ``horizon``-th session after ``ts`` -- and
    its ``ingested_at`` is that of the row which completed the target, since the target was not
    knowable before that row was ingested.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be at least 1, got {horizon}")
    missing = [name for name in _PANEL_COLUMNS if name not in panel.columns]
    if missing:
        raise SchemaError(f"cannot build targets: the panel lacks column(s) {missing}")
    symbols = panel["symbol"].unique()
    if len(symbols) > 1:
        raise SchemaError(
            f"cannot build targets: the panel must hold one symbol, got {sorted(symbols)}"
        )

    name = target_column(horizon)
    columns = ["symbol", "ts", "as_of", "ingested_at", name]
    if panel.empty:
        return pd.DataFrame({column: [] for column in columns})

    days = [_as_date(value) for value in panel["as_of"]]
    if len(set(days)) != len(days):
        raise SchemaError("cannot build targets: a session appears in more than one row")

    returns = pd.Series(panel[RETURN_COLUMN].to_numpy(), index=days)
    # Reindexed onto every session in the span so that the windows count sessions. A session with
    # no row becomes an explicit NaN return and poisons the windows containing it.
    span = sessions_between(calendar, min(days), max(days))
    forward = forward_rv_proxy(returns.reindex(span), horizon=horizon)

    rows = panel.copy()
    rows["_day"] = days
    rows[name] = [forward.get(day) for day in days]
    rows = rows[rows[name].notna()].sort_values("_day")
    realised = [nth_session_after(calendar, day, horizon) for day in rows["_day"]]
    ingested = dict(zip(days, panel["ingested_at"], strict=True))

    out = pd.DataFrame(
        {
            "symbol": rows["symbol"].to_numpy(),
            "ts": rows["ts"].to_numpy(),
            "as_of": realised,
            "ingested_at": [ingested[day] for day in realised],
            name: rows[name].to_numpy(),
        }
    )
    return out.reset_index(drop=True)


def write_targets(
    targets: pd.DataFrame, *, horizon: int, symbol: str, settings: Settings | None = None
) -> list[Path]:
    """Write ``targets`` to the horizon's dataset, one partition per ``as_of``.

    ``write_features`` insists every row of a call carries the single ``as_of`` it is given, and
    a target frame spans one ``as_of`` per row, so this groups by it. Returns the paths written.
    """
    if targets.empty:
        raise SchemaError(f"cannot write targets_rv_h{horizon}/{symbol}: the frame has no rows")
    keyed = targets.assign(as_of=[_as_date(value) for value in targets["as_of"]])
    return [
        write_features(
            part,
            feature_set=targets_dataset(horizon),
            symbol=symbol,
            as_of=_as_date(day),
            settings=settings,
        )
        for day, part in keyed.groupby("as_of", sort=True)
    ]
