import datetime as dt
import os
from pathlib import Path

import pytest

import volab
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


def test_unmarked_test_that_unlocks_holdout_fails(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The nested run gets its own rootdir, so this repo's ``pythonpath = ["src"]`` does not
    # reach it; hand it the directory the volab under test was imported from.
    src_dir = str(Path(volab.__file__).resolve().parents[1])
    existing = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([src_dir, existing]) if existing else src_dir)
    pytester.makeconftest((Path(__file__).parent / "conftest.py").read_text())
    pytester.makepyfile(
        """
        import volab.store as store

        def test_sneaks_past_the_guard(monkeypatch):
            monkeypatch.setenv("VOLAB_HOLDOUT_UNLOCK", "1")
            assert store._unlock_granted(allow_holdout=True)
        """
    )
    # A subprocess, not runpytest(): in-process the outer autouse fixture has already wrapped
    # _unlock_granted, which would mask a defect confined to the copied conftest. The pattern
    # anchors on the failure itself, not on the pytest.fail(...) source line a traceback echoes.
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Failed: test unlocked the holdout*"])


@pytest.mark.holdout
def test_marked_test_may_unlock(monkeypatch: pytest.MonkeyPatch) -> None:
    import volab.store as store

    monkeypatch.setenv("VOLAB_HOLDOUT_UNLOCK", "1")
    assert store._unlock_granted(allow_holdout=True) is True


def test_unmarked_test_that_moves_the_boundary_fails(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture must also catch a test that moves ``dev_end`` instead of unlocking it.

    ``volab.store`` refuses a passed ``dev_end`` later than the repository's, so the nested test
    neutralises that check -- it stands in for a future refactor that loses it -- and the
    fixture is what is left. The nested test holds no key, so ``_unlock_granted`` is never
    called and the existing wrapping cannot see it.
    """
    src_dir = str(Path(volab.__file__).resolve().parents[1])
    existing = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([src_dir, existing]) if existing else src_dir)
    pytester.makeconftest((Path(__file__).parent / "conftest.py").read_text())
    pytester.makepyfile(
        """
        import dataclasses
        import datetime as dt

        import volab.store as store
        from volab.config import Splits
        from volab.store import load_panel

        HOLDOUT_DAY = dt.date(2024, 3, 1)

        def test_moves_the_boundary_instead_of_unlocking_it(synthetic_store, monkeypatch):
            synthetic_store.write("bars_daily", "SPY", [HOLDOUT_DAY])
            moved = dataclasses.replace(
                synthetic_store.settings,
                splits=Splits(dev_end=dt.date(2099, 12, 31), holdout_start=dt.date(2100, 1, 2)),
            )
            monkeypatch.setattr(store, "load_settings", lambda *a, **k: moved)

            frame = load_panel("bars_daily", end=HOLDOUT_DAY, settings=moved)

            assert len(frame) == 1
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Failed: test read past the repository's dev_end*"])
