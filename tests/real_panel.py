"""The real curated store, read once per process and handed out as copies.

**Plain functions, called from inside test bodies -- never a pytest fixture, of any scope.** The
autouse ``_forbid_holdout_unlock`` fixture in ``conftest.py`` is function-scoped, and it is what
fails an unmarked test that reads past ``dev_end``. A fixture of a wider scope than the function
is set up before it, so a load made there happens before the police are watching and goes
unpoliced. Called from a test body, the first read in the process is made while they are. That is
the whole of what is policed: later calls are cache lookups and reach the store not at all, so
the guarantee covers the one read, which is the only one that touches the store.
``pytest_fixture_setup`` in ``conftest.py`` is what enforces the rule, for fixtures defined
anywhere: it refuses a store read during the setup of a non-function-scoped fixture. A fixture
that wants a frame from here must therefore be function-scoped. ``test_real_panel.py`` also
asserts that this module defines no fixture of its own.

**Why the accessors copy.** Each loader is cached, so every caller in the process would otherwise
hold the same frame object. The modules this replaced each did that behind a comment saying
callers must not mutate it, which nothing enforced; sharing the same objects across modules
would turn that within-file convention into a coupling between files. Each accessor returns
``copy(deep=True)`` instead, so a caller that assigns into a returned frame, adds a column or
edits it in place changes its own copy and no one else's. That is all the copy protects against.
It duplicates the frame's arrays and index, not the Python objects an object-dtype cell might
refer to, so mutating such an object in place would still be shared. Nothing here checks that the
cached frames themselves stay untouched: only this module holds them. That the copies isolate
callers from one another is tested; that they need to be *deep* rather than shallow is not (under
pandas copy-on-write a shallow copy isolates assignment as well). Deep is correct by inspection.

``settings`` is the exception: ``Settings`` is a frozen dataclass of immutable fields, so the one
cached instance is returned as it is.

A loader that raises caches nothing, so ``DatasetNotFound`` reaches the caller on every call and
each test module decides for itself whether an absent store is a skip or a failure.
"""

from __future__ import annotations

import functools

import pandas as pd

import volab.compare.__main__ as compare_cli
import volab.walkforward as wf
from volab.config import Settings, load_settings
from volab.panel import load_curated_panel


@functools.cache
def _settings() -> Settings:
    return load_settings()


@functools.cache
def _curated_panel(symbol: str) -> pd.DataFrame:
    config = _settings()
    return load_curated_panel(config, symbol, end=config.splits.dev_end)


@functools.cache
def _load_for(symbol: str, horizon: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    return wf.load_for(_settings(), symbol, horizon)


@functools.cache
def _persisted(symbol: str) -> pd.DataFrame | None:
    return compare_cli._persisted(_settings(), symbol)


def settings() -> Settings:
    """The repository's settings, loaded once; shared by reference because it is immutable."""
    return _settings()


def curated_panel(symbol: str) -> pd.DataFrame:
    """The curated panel for ``symbol`` read to ``dev_end``, as the caller's own copy."""
    return _curated_panel(symbol).copy(deep=True)


def load_for(symbol: str, horizon: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``wf.load_for``'s panel and targets for ``symbol`` and ``horizon``, as owned copies."""
    panel, targets = _load_for(symbol, horizon)
    return panel.copy(deep=True), targets.copy(deep=True)


def persisted(symbol: str) -> pd.DataFrame | None:
    """The feature table the walk-forward CLI would read, or ``None`` if the store holds none.

    ``None`` is a real answer and is cached like a frame; the caller decides what it means.
    """
    stored = _persisted(symbol)
    return None if stored is None else stored.copy(deep=True)
