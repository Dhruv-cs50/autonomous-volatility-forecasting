"""``real_panel``: what its accessors hand out, and the one thing it must never become.

None of these reads the store. The loaders are exercised through a fake ``load_curated_panel``
under a symbol no real run uses, so the process-wide cache the other test modules share is neither
cleared nor filled by anything here.
"""

from __future__ import annotations

import uuid

import pandas as pd
import pytest
import real_panel
from _pytest.fixtures import FixtureFunctionDefinition


def _fake_panel() -> pd.DataFrame:
    return pd.DataFrame({"close": [1.0, 2.0, 3.0], "symbol": ["X", "X", "X"]})


@pytest.fixture
def fake_symbol(monkeypatch: pytest.MonkeyPatch) -> tuple[str, list[str]]:
    """A symbol no real run uses, and the log of the symbols the fake store was asked for."""
    asked: list[str] = []

    def load(_settings: object, symbol: str, *, end: object) -> pd.DataFrame:
        asked.append(symbol)
        return _fake_panel()

    monkeypatch.setattr(real_panel, "load_curated_panel", load)
    return f"T{uuid.uuid4().hex}", asked


def test_the_expensive_read_happens_once_however_many_callers(
    fake_symbol: tuple[str, list[str]],
) -> None:
    symbol, asked = fake_symbol
    hits_before = real_panel._curated_panel.cache_info().hits
    real_panel.curated_panel(symbol)
    real_panel.curated_panel(symbol)
    real_panel.curated_panel(symbol)
    assert asked == [symbol]
    assert real_panel._curated_panel.cache_info().hits - hits_before == 2


def test_every_caller_gets_its_own_frame(fake_symbol: tuple[str, list[str]]) -> None:
    symbol, _ = fake_symbol
    first = real_panel.curated_panel(symbol)
    first.loc[0, "close"] = -999.0
    first["scribble"] = 1
    first.drop(index=[1, 2], inplace=True)

    second = real_panel.curated_panel(symbol)
    assert second is not first
    pd.testing.assert_frame_equal(second, _fake_panel())


def test_the_pair_and_the_persisted_table_are_copies_too(monkeypatch: pytest.MonkeyPatch) -> None:
    symbol = f"T{uuid.uuid4().hex}"
    monkeypatch.setattr(
        real_panel.wf, "load_for", lambda _s, _sym, _h: (_fake_panel(), _fake_panel())
    )
    monkeypatch.setattr(real_panel.compare_cli, "_persisted", lambda _s, _sym: _fake_panel())

    panel, targets = real_panel.load_for(symbol, 5)
    panel["close"] = 0.0
    targets["close"] = 0.0
    stored = real_panel.persisted(symbol)
    assert stored is not None
    stored["close"] = 0.0

    panel_again, targets_again = real_panel.load_for(symbol, 5)
    stored_again = real_panel.persisted(symbol)
    assert stored_again is not None
    for frame in (panel_again, targets_again, stored_again):
        pd.testing.assert_frame_equal(frame, _fake_panel())


def test_a_missing_feature_table_is_none_and_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(real_panel.compare_cli, "_persisted", lambda _s, _sym: None)
    assert real_panel.persisted(f"T{uuid.uuid4().hex}") is None


def test_the_module_defines_no_pytest_fixture() -> None:
    """The reason is in the module docstring: a fixture of a higher scope than the function
    would load before the autouse holdout police, and this is the check that holds the line."""
    fixtures = [
        name
        for name, value in vars(real_panel).items()
        if isinstance(value, FixtureFunctionDefinition)
    ]
    assert fixtures == []
