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

        # After the repository's own dev_end (2025-12-31), which is what the fixture polices.
        HOLDOUT_DAY = dt.date(2026, 3, 2)

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


def test_a_store_read_during_a_wider_scoped_fixture_is_an_error(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A module-scoped fixture is set up before the autouse police, so its read is refused.

    The read is two calls down (fixture, helper, ``load_curated_panel``), so the guard has to
    watch the read itself and not the fixture's source. ``errors=1``, not ``failed=1``: a failure
    during fixture setup is reported as an error, and ``failed`` would mean the guard fired in
    the wrong place. The quiet module-scoped fixture beside it is the control: the guard refuses
    reads, not wider scopes.
    """
    src_dir = str(Path(volab.__file__).resolve().parents[1])
    existing = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([src_dir, existing]) if existing else src_dir)
    pytester.makeconftest((Path(__file__).parent / "conftest.py").read_text())
    pytester.makepyfile(
        """
        import pytest

        from volab.config import load_settings
        from volab.panel import load_curated_panel

        def read_the_store():
            settings = load_settings()
            return load_curated_panel(settings, "SPY", end=settings.splits.dev_end)

        @pytest.fixture(scope="module")
        def early():
            return read_the_store()

        @pytest.fixture(scope="module")
        def quiet():
            return 1

        def test_depends_on_the_early_read(early):
            raise AssertionError("the fixture's read was not refused")

        def test_a_quiet_wider_fixture_is_fine(quiet):
            assert quiet == 1
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*Failed: fixture 'early' (scope module) read the store*"])


def _run_nested(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, source: str
) -> pytest.RunResult:
    """Run ``source`` as a test module under a copy of this conftest, in a subprocess."""
    src_dir = str(Path(volab.__file__).resolve().parents[1])
    existing = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([src_dir, existing]) if existing else src_dir)
    pytester.makeconftest((Path(__file__).parent / "conftest.py").read_text())
    pytester.makepyfile(source)
    return pytester.runpytest_subprocess()


def test_the_refusal_follows_the_function_and_not_the_name_it_is_bound_to(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An aliased import, and a reference captured before the fixture ran, are both refused.

    Each fixture would read the real store if the guard matched on the attribute name
    ``load_panel``; the fixtures' own names are in the expected message.
    """
    result = _run_nested(
        pytester,
        monkeypatch,
        """
        import pytest

        import volab.store as store
        from volab.config import load_settings
        from volab.store import load_panel as aliased

        captured = store.load_panel

        def read(function):
            settings = load_settings()
            return function("bars_daily", end=settings.splits.dev_end, settings=settings)

        @pytest.fixture(scope="module")
        def through_an_alias():
            return read(aliased)

        @pytest.fixture(scope="module")
        def through_a_capture():
            return read(captured)

        def test_alias(through_an_alias):
            raise AssertionError("the aliased read was not refused")

        def test_capture(through_a_capture):
            raise AssertionError("the captured reference was not refused")
        """,
    )
    result.assert_outcomes(errors=2)
    result.stdout.fnmatch_lines(
        [
            "*Failed: fixture 'through_an_alias' (scope module) read the store*",
            "*Failed: fixture 'through_a_capture' (scope module) read the store*",
        ]
    )


def test_a_module_first_imported_during_wider_scope_setup_is_not_left_refusing(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``volab.panel`` binds ``load_panel`` at import. Imported inside a module-scoped fixture, it
    binds whatever is installed then, and the refusal must not outlive the setup in that binding."""
    result = _run_nested(
        pytester,
        monkeypatch,
        """
        import sys

        import pytest

        import volab.store as store
        from volab.config import load_settings
        from volab.errors import DatasetNotFound

        @pytest.fixture(scope="module")
        def lazily_imports():
            assert "volab.panel" not in sys.modules, "the premise: not yet imported"
            import volab.panel

        def test_the_binding_is_the_genuine_function_afterwards(lazily_imports):
            import volab.panel

            assert volab.panel.load_panel is store.load_panel
            # Behaviour, not the function's name: a refusal wrapped by the police would still be
            # a refusal. A dataset that does not exist is a store error from the genuine function
            # and a pytest failure (not an ``Exception``) from the refusal.
            settings = load_settings()
            with pytest.raises(DatasetNotFound):
                volab.panel.load_panel(
                    "no_such_dataset", end=settings.splits.dev_end, settings=settings
                )
        """,
    )
    result.assert_outcomes(passed=1)
