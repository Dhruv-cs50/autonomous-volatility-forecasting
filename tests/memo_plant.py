"""E2: a memoised full-sample statistic, the escape no in-process comparison can see.

Not a test module. It is imported by both halves of ``tests/test_process_isolation.py`` -- the
in-process half, which must show the plant *escaping*, and the child script, which must show it
caught -- because a plant written twice is two plants, and the whole point of that test is that
one plant behaves differently depending only on how many processes the comparison spans.

The escape, as the Phase 1 review that invented it recorded: a module-level cache filled on the
first ``build_features`` call and reused by the next. ``test_the_panels_extent_does_not_change_
the_table`` is the suite's only detector of the full-sample class, and it compares two tables
built in one process, so the second build reads the first build's cache and the two agree.
**Class: any cross-call state -- a global, a dict, ``functools.cache``.**

Note what is *not* forward-looking about it. There is no ``shift``, no slice, no later row: the
statistic is computed from the rows the engine was handed. It leaks because the rows it was
handed depend on the panel's extent, and then the answer is frozen and reused at a cutoff where
those rows were not yet knowable. That is why the AST scan is blind to it and why Phase 1's
second required mutation was of exactly this kind.
"""

from __future__ import annotations

import pandas as pd

# The module-level mutable state. One dict, filled once, reused forever -- which is the entire
# mechanism. Scoped to this module so installing the plant does not leave anything behind in the
# feature package.
_SCALE: dict[str, float] = {}


def reset() -> None:
    """Empty the cache, so the in-process half can choose when state is allowed to survive."""
    _SCALE.clear()


def memoised_rv_proxy(returns: pd.Series, *, window: int) -> pd.Series:
    """``rv_proxy`` normalised by a full-sample statistic that is computed once and cached.

    The first call decides the scale from whatever series it is handed. Every later call in the
    same process reuses it, whatever series *it* is handed -- so a table built from a panel
    truncated at ``t`` is normalised by a constant derived from rows after ``t``.
    """
    if "scale" not in _SCALE:
        _SCALE["scale"] = float(returns.abs().mean())
    squared = returns**2
    return squared.rolling(window, min_periods=window).sum() / _SCALE["scale"]
