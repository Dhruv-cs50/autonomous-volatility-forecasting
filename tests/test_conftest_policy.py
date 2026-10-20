import datetime as dt
from pathlib import Path

import pytest

from volab.paths import curated_path

pytest_plugins = ["pytester"]


def test_synthetic_store_writes_discoverable_files(synthetic_store) -> None:
    day = dt.date(2023, 6, 15)
    written = synthetic_store.write("bars_daily", "SPY", [day])
    assert written == [curated_path(synthetic_store.root, "bars_daily", "SPY", day)]
    assert written[0].exists()


def test_synthetic_store_settings_match_repo_defaults(synthetic_store) -> None:
    assert synthetic_store.settings.universe == ("SPY", "QQQ", "IWM")
    assert synthetic_store.settings.splits.dev_end == dt.date(2023, 12, 31)
    assert synthetic_store.root == synthetic_store.settings.storage_root


def test_unmarked_test_that_unlocks_holdout_fails(pytester: pytest.Pytester) -> None:
    pytester.makeconftest((Path(__file__).parent / "conftest.py").read_text())
    pytester.makepyfile(
        """
        import volab.store as store

        def test_sneaks_past_the_guard(monkeypatch):
            monkeypatch.setenv("VOLAB_HOLDOUT_UNLOCK", "1")
            assert store._unlock_granted(allow_holdout=True)
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*@pytest.mark.holdout*"])


@pytest.mark.holdout
def test_marked_test_may_unlock(monkeypatch: pytest.MonkeyPatch) -> None:
    import volab.store as store

    monkeypatch.setenv("VOLAB_HOLDOUT_UNLOCK", "1")
    assert store._unlock_granted(allow_holdout=True) is True
