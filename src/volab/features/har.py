"""HAR regressors: trailing means of squared daily returns over a day, a week and a month.

The heterogeneous autoregressive model of realized variance regresses on the latest day's
variance and on its averages over the last week (5 sessions) and month (22). They are *means*,
whereas ``rv_proxy`` is a *sum*; that asymmetry is the standard HAR specification and is
deliberate, not an inconsistency to tidy. A mean over ``n`` sessions is the ``rv_proxy`` sum
over the same window divided by ``n``, and it is built from exactly that, so the two cannot
drift apart.

Like everything in this package the inputs are daily returns, so these are variance *proxies*,
trailing-only, and null wherever the window is incomplete.

**Caller contract: ascending time order.** The windows are positional, so "trailing" means the
preceding rows of the series as handed in, and these functions never see a date. Callers must
pass the series in ascending time order; a descending one would silently produce forward-looking
components that no syntactic scan can detect. The functions do not verify the order, deliberately:
they are hot and pure, and monotonicity is asserted once, where the series is assembled.
"""

from __future__ import annotations

import pandas as pd

from volab.features.realized import rv_proxy

# Sessions in the day, week and month windows of the HAR specification.
HAR_WINDOWS: dict[str, int] = {"har_d": 1, "har_w": 5, "har_m": 22}


def har_components(returns: pd.Series) -> dict[str, pd.Series]:
    """The HAR day, week and month components of a daily-return series.

    ``har_d`` is the squared return itself, ``har_w`` the mean of the last 5 squared returns and
    ``har_m`` the mean of the last 22. Each is a same-length series that is null until its window
    is full and for any window containing a null return.
    """
    return {name: rv_proxy(returns, window=window) / window for name, window in HAR_WINDOWS.items()}
