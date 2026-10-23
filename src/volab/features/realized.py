"""Trailing realized-variance *proxies*, computed from daily returns.

Every estimator here carries ``_proxy_`` in its name, and that is a correctness statement, not
a style choice. Realized variance, negative semivariance and bipower variation are defined on
**intraday** returns. Applied to one return per session they are different, weaker quantities,
and no free intraday source exists at this tier. Naming them as proxies keeps a later phase
that compares against the literature from mistaking one for the other.

All of them are pure functions of a return series: a ``pd.Series`` in, a same-length
``pd.Series`` out, no dates, no I/O. Two properties hold for every output row ``i``:

* **Trailing only.** Row ``i`` is computed from rows ``..i`` and nothing after. ``rolling(n)``
  is right-closed, which is exactly that, so no *forward* offset or centring appears anywhere.
  The one offset used, ``shift(1)`` in ``bipower_proxy``, looks a row back.
* **Incomplete is null.** A window that is not yet full, or that contains a null return, yields
  ``NaN`` -- never the sum of the rows that happen to be present. Nothing is filled or imputed.

**Caller contract: ascending time order.** Every window here is *positional*: "trailing" means
the preceding rows of the series as handed in. The functions never see a date, so they cannot
check that those rows are the preceding *sessions*. A series in descending date order would
silently yield forward-looking values, and no syntactic scan can catch that because it is a
property of the data. Callers must pass the series in ascending time order; these functions do
not verify it, and deliberately so -- they are hot, pure, per-symbol, and the one place to
assert monotonicity is where the series is assembled.
"""

from __future__ import annotations

import math
from typing import Literal

import pandas as pd


def _require_window(window: int, *, minimum: int = 1) -> None:
    if window < minimum:
        raise ValueError(f"window must be at least {minimum}, got {window}")


def _trailing_sum(values: pd.Series, window: int) -> pd.Series:
    # ``min_periods=window`` is what makes an incomplete window null: pandas' default for a
    # fixed window is already ``window``, but spelling it out keeps that load-bearing choice
    # from depending on a default. A NaN inside the window counts as a missing observation, so
    # it leaves the window short and the sum null.
    return values.rolling(window, min_periods=window).sum()


def rv_proxy(returns: pd.Series, *, window: int) -> pd.Series:
    """Daily-return proxy for realized variance: the trailing sum of ``window`` squared returns.

    This is a *proxy*: realized variance proper sums squared **intraday** returns. Null until
    the window is full, and null for any window containing a null return.
    """
    _require_window(window)
    return _trailing_sum(returns**2, window)


def semivariance_proxy(
    returns: pd.Series, *, window: int, sign: Literal["neg", "pos"]
) -> pd.Series:
    """Daily-return proxy for realized semivariance: squared returns of one sign, summed.

    ``sign="neg"`` keeps the squared negative returns, ``"pos"`` the positive ones; a zero
    return belongs to neither and contributes nothing. Semivariance proper is defined on
    **intraday** returns, so this is a proxy. ``pos + neg`` equals ``rv_proxy`` exactly.

    A null return stays null rather than reading as "not of this sign" and becoming a zero,
    which would turn a window the data cannot support into a confident partial sum.
    """
    _require_window(window)
    if sign not in ("neg", "pos"):
        raise ValueError(f"sign must be 'neg' or 'pos', got {sign!r}")
    squared = returns**2
    # ``mask`` replaces where the condition holds and leaves NaN alone, because a comparison
    # against NaN is False. That is the property wanted: only a *known* wrong-signed or zero
    # return is zeroed.
    wrong_sign = (returns >= 0) if sign == "neg" else (returns <= 0)
    return _trailing_sum(squared.mask(wrong_sign, 0.0), window)


def signed_jump_proxy(returns: pd.Series, *, window: int) -> pd.Series:
    """Daily-return proxy for the signed jump: positive minus negative semivariance proxy.

    Positive when upside variation dominates the window, negative when downside does. Built
    from the two semivariance *proxies*, so it inherits their caveat: the literature's signed
    jump is defined on **intraday** returns.
    """
    return semivariance_proxy(returns, window=window, sign="pos") - semivariance_proxy(
        returns, window=window, sign="neg"
    )


def bipower_proxy(returns: pd.Series, *, window: int) -> pd.Series:
    """Daily-return proxy for bipower variation over a trailing ``window`` of sessions.

    ``(pi / 2)`` times the sum of ``|r[t]| * |r[t-1]|`` over the adjacent pairs inside the
    window, which is ``window - 1`` products -- hence ``window >= 2``. Bipower variation proper
    is defined on **intraday** returns, so this is a proxy.

    The first pair needs a return before the window's first row, so the earliest defined row is
    ``window - 1`` and a window containing a null return, in either member of any pair, is null.
    """
    _require_window(window, minimum=2)
    # shift(1) looks one row back, so row t pairs (t-1, t) and never sees anything later.
    adjacent_products = (returns.abs() * returns.abs().shift(1)).astype("float64")
    pairs = window - 1
    return (math.pi / 2) * adjacent_products.rolling(pairs, min_periods=pairs).sum()
