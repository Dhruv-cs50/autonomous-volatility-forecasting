"""Shared fixtures: a synthetic Parquet store, and the policing of holdout unlocks.

``_forbid_holdout_unlock`` is what keeps the suite honest. The holdout is sealed, and a test
that unlocks it without saying so would let every later test inherit that access unnoticed, so
an unmarked test that unlocks it is failed rather than trusted. It fails an unmarked test that
reads past the repository's own ``dev_end`` too, however it got there: reaching for the keys is
one way past the seal, and handing ``load_panel`` a ``Settings`` with a later boundary was
another.

It finds ``load_panel`` by identity, wherever a module holds it and whatever it is called there:
an alias and a reference captured before the suite started are the same function, and matching on
the name ``load_panel`` policed neither. The wrapper is installed once per session, since one
sweep of ``sys.modules`` costs 3.6ms and the answer does not change between tests; each test only
arms it with the boundary to enforce.

``pytest_fixture_setup`` below closes the one gap those leave: the police are function-scoped, so
a fixture of a wider scope is set up before them and its store read goes unwatched. It is refused.

``_clean_volab_environment`` is the other half of that: the police asks ``load_settings`` where
the boundary is, so the boundary must not be settable from outside the suite.
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from volab.config import Settings, Splits
from volab.paths import MANDATORY_COLUMNS, Tier, curated_path, features_path, raw_path

# Fixed so written files are reproducible. Later than any date a test is likely to write.
DEFAULT_INGESTED_AT = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)


@dataclass
class SyntheticStore:
    """A small Parquet store under a temporary root, in the layout ``volab.paths`` defines."""

    root: Path
    settings: Settings

    def write(
        self,
        dataset: str,
        symbol: str,
        dates: Iterable[dt.date],
        *,
        tier: Tier = Tier.CURATED,
        as_of: Callable[[dt.date], dt.date | None] | None = None,
        drop_columns: Iterable[str] = (),
        ts_naive: bool = False,
        ingested_at: dt.datetime | None = None,
        ts: dt.datetime | None = None,
        repeat: int = 1,
    ) -> list[Path]:
        """Write one Parquet file per date and return the paths written.

        ``dataset`` is the source name for ``Tier.RAW`` and the feature set for
        ``Tier.FEATURES``. ``as_of`` maps each partition date to the row's ``as_of`` (identity by
        default; returning ``None`` writes a null). ``drop_columns`` omits mandatory columns and
        ``ts_naive`` writes ``ts`` without a timezone, both to provoke schema errors.

        ``ts`` pins every row's ``ts`` instead of deriving it from the partition date, so that
        two vintages of the same observation can be written into different ``date=`` partitions.
        ``repeat`` writes the same row ``repeat`` times into the one file, which is the only way
        a store can hold rows tying on all of ``(symbol, ts, as_of, ingested_at)``.
        """
        dropped = tuple(drop_columns)
        unknown = sorted(set(dropped) - set(MANDATORY_COLUMNS))
        if unknown:
            raise ValueError(f"drop_columns must be mandatory columns, got {unknown}")
        stamp = DEFAULT_INGESTED_AT if ingested_at is None else ingested_at
        if stamp.tzinfo is None:
            raise ValueError(f"ingested_at must be timezone-aware, got {stamp!r}")
        choose_as_of = (lambda d: d) if as_of is None else as_of
        schema = pa.schema(
            [
                ("symbol", pa.string()),
                ("ts", pa.timestamp("us") if ts_naive else pa.timestamp("us", tz="UTC")),
                ("as_of", pa.date32()),
                ("ingested_at", pa.timestamp("us", tz="UTC")),
            ]
        )

        written: list[Path] = []
        if repeat < 1:
            raise ValueError(f"repeat must be at least 1, got {repeat}")

        for day in dates:
            midnight = dt.datetime(day.year, day.month, day.day)
            default_ts = midnight if ts_naive else midnight.replace(tzinfo=dt.UTC)
            point = default_ts if ts is None else ts
            frame = pd.DataFrame(
                {
                    "symbol": [symbol] * repeat,
                    "ts": [point] * repeat,
                    "as_of": [choose_as_of(day)] * repeat,
                    "ingested_at": [stamp] * repeat,
                }
            )
            table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
            table = table.select([c for c in MANDATORY_COLUMNS if c not in dropped])
            path = self._path(tier, dataset, symbol, day, stamp)
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, path)
            written.append(path)
        return written

    def _path(
        self, tier: Tier, dataset: str, symbol: str, day: dt.date, ingested_at: dt.datetime
    ) -> Path:
        if tier is Tier.RAW:
            return raw_path(self.root, dataset, symbol, day, ingested_at)
        if tier is Tier.FEATURES:
            return features_path(self.root, dataset, symbol, day)
        return curated_path(self.root, dataset, symbol, day)


@pytest.fixture
def synthetic_store(tmp_path: Path) -> SyntheticStore:
    # Built directly rather than via load_settings: that insists the storage root lies inside
    # the repository, which a temporary directory by design does not.
    settings = Settings(
        universe=("SPY", "QQQ", "IWM"),
        exogenous=("VIX", "VIX9D", "VIX1D", "VVIX", "BAA10Y", "DGS10", "T10Y2Y"),
        calendar="XNYS",
        splits=Splits(dev_end=dt.date(2023, 12, 31), holdout_start=dt.date(2024, 1, 2)),
        storage_root=tmp_path,
    )
    return SyntheticStore(root=settings.storage_root, settings=settings)


@pytest.fixture(autouse=True)
def _clean_volab_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every ``VOLAB_*`` configuration override, for every test.

    ``VOLAB_SPLITS_DEV_END`` is the one that matters. The fixture below asks ``load_settings``
    for the repository's own boundary, so a variable that moved it would move the police with
    it, and every assertion the suite makes about the seal would be made against a boundary
    chosen from outside the suite. ``volab.config`` refuses a *later* one now. An earlier one
    is legitimate configuration and is cleared anyway, but it is the harmless direction: it
    *widens* what counts as sealed, so the police becomes over-strict and fails closed. Noisy,
    never silent.

    The others are cleared for the ordinary reason: a test that reads the configuration must
    see the committed file, not a developer's shell. The names come from ``_ENV_OVERRIDES``
    rather than being copied, so a setting added there cannot be forgotten here.

    ``VOLAB_HOLDOUT_UNLOCK`` is deliberately not in that map -- it is a key, not a setting --
    and ``_forbid_holdout_unlock`` deals with it per test instead.
    """
    from volab.config import _ENV_OVERRIDES

    for variable in _ENV_OVERRIDES.values():
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture(autouse=True)
def _forbid_holdout_unlock(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    _clean_volab_environment: None,
) -> Iterator[None]:
    """Fail any unmarked test that unlocks the holdout, or that reads past it regardless.

    Function-scoped, and it must be: it depends on ``monkeypatch``, which is function-scoped.

    Enforcing the *boundary*, though, is not function-scoped work. The wrapper that enforces it
    is installed once per session by ``_install_holdout_police`` and then left alone; this
    fixture only arms it, by pushing the boundary it is to enforce onto ``_ACTIVE_DEV_END``.
    Finding every binding of ``load_panel`` by identity costs one sweep of ``sys.modules``,
    measured at 3.6ms -- about 6s over this suite if paid per test, for an answer that does not
    change between tests.
    """
    if request.node.get_closest_marker("holdout") is not None:
        # A marked test may read the holdout, so there is nothing here to police. The wrapper is
        # *removed* for its duration rather than merely left unarmed: ``store._caller_frame``
        # names the first frame outside ``volab`` in the audit line, so a wrapper still sitting
        # in the call chain would become the "caller" that ``test_store_guard`` asserts on. Two
        # sweeps, for the handful of marked tests in the suite, is the whole price of the
        # once-per-session install.
        exempt = bool(_POLICED_ORIGINAL)
        if exempt:
            _uninstall_holdout_police()
        try:
            yield
        finally:
            if exempt:
                _install_holdout_police()
        return

    # Lazy, so importing this conftest does not import the data layer; never guarded, because a
    # policing fixture that fails open on an import error is worse than no policing at all.
    # ``_UNLOCK_ENV`` is read from the module rather than copied, so the two cannot drift.
    import volab.store as store
    from volab.config import load_settings

    monkeypatch.delenv(store._UNLOCK_ENV, raising=False)

    original_unlock = store._unlock_granted

    def refuse_unmarked_unlock(allow_holdout: bool) -> bool:
        if original_unlock(allow_holdout):
            pytest.fail("test unlocked the holdout without @pytest.mark.holdout")
        return False

    monkeypatch.setattr(store, "_unlock_granted", refuse_unmarked_unlock)

    # Wrapping ``_unlock_granted`` polices whoever reaches for the keys. It is blind to a test
    # that moves the boundary instead -- passing a hand-built ``Settings`` whose ``dev_end`` is
    # later than the repository's -- because no key is ever asked for. ``volab.store`` refuses
    # that itself now; this is the suite-side half of the same fix.
    if not _POLICED_ORIGINAL:
        _install_holdout_police()
    elif store.load_panel is not _police_holdout_read:
        # Installing once per session buys the speed and costs this check. The per-test wrapping
        # this replaced re-captured whatever ``store.load_panel`` was for every test, so a
        # rebinding that escaped its test healed itself on the next one; nothing re-captures now.
        # The reachable route is a *marked* test that monkeypatches ``store.load_panel``: this
        # fixture requests ``monkeypatch``, so it is finalised first, its re-install captures the
        # test's stand-in as the original, and monkeypatch then restores the real function out
        # from under the police. One identity comparison per test rather than a sweep, and it
        # fails closed: a test does not run on police that cannot be vouched for.
        pytest.fail(
            "volab.store.load_panel is no longer the holdout police: something rebound it and "
            "the rebinding outlived the test that made it, so the holdout seal is unpoliced"
        )

    # ``load_settings`` is asked per test rather than once at install, so that the boundary the
    # police enforces is read the same way, and at the same moment, as it always was.
    _ACTIVE_DEV_END.append(load_settings().splits.dev_end)
    try:
        yield
    finally:
        _ACTIVE_DEV_END.pop()


# Names of the non-function-scoped fixtures being set up right now, outermost first. Fixtures
# nest (a module fixture pulls in the session fixtures it needs inside its own setup).
_WIDER_SCOPE_SETUPS: list[str] = []
# What ``load_panel`` was when the refusal went in, held in a list so that this module's own
# namespace never holds the function and the sweeps below cannot find it here.
_REFUSED_ORIGINAL: list[object] = []


def _refuse_store_read(*args: object, **kwargs: object) -> object:
    pytest.fail(
        f"fixture {_WIDER_SCOPE_SETUPS[-1]} read the store during setup, before the autouse "
        "holdout police are active; read from inside the test body (see tests/real_panel.py)"
    )


def _names_bound_to(function: object) -> list[tuple[ModuleType, str]]:
    """Every module-level name, in every imported module, whose value *is* ``function``.

    Matched on identity and not on the name ``load_panel``: an ``import ... as aliased`` and a
    reference taken before the sweep (``captured = store.load_panel``) are the same function under
    another name. This module's own namespace is skipped, since it holds the refusal and the
    police themselves.

    One sweep is 3.6ms, so neither caller pays it per test: the fixture-scope refusal sweeps only
    around a wider-scoped setup, and the holdout police sweeps once per session at install and
    again around each ``@pytest.mark.holdout`` test.
    """
    found: list[tuple[ModuleType, str]] = []
    for module in list(sys.modules.values()):
        try:
            namespace = vars(module)
            items = list(namespace.items())
        except Exception:  # a module object that will not show its namespace has nothing to patch
            continue
        if namespace is globals():
            continue
        found.extend((module, name) for name, value in items if value is function)
    return found


# The ``dev_end`` the holdout police is enforcing right now. Empty whenever no unmarked test is
# running -- during collection, between tests, and for the duration of a marked one -- and the
# wrapper then calls straight through. A stack, not a scalar, so that pushing and popping is all
# an unmarked test has to do and no state can be left armed by a test that raised.
_ACTIVE_DEV_END: list[dt.date] = []
# What ``load_panel`` was before the police wrapped it, held in a list for the same reason as
# ``_REFUSED_ORIGINAL`` above: this module's own namespace must never bind the function, or the
# sweeps would find it here.
_POLICED_ORIGINAL: list[object] = []


def _police_holdout_read(*args: object, **kwargs: object) -> object:
    """``load_panel``, failing the test if it read past the boundary being enforced right now.

    Judged on what came back rather than on the argument, so the tests whose whole purpose is to
    prove a holdout read is *refused* stay expressible: those raise, and never reach the check.
    """
    original = _POLICED_ORIGINAL[-1]
    frame = original(*args, **kwargs)  # type: ignore[operator]
    if not _ACTIVE_DEV_END:
        return frame
    boundary = _ACTIVE_DEV_END[-1]
    end = kwargs.get("end")
    if isinstance(end, dt.date) and end > boundary:
        pytest.fail(
            f"test read past the repository's dev_end {boundary} with end={end} "
            "and no @pytest.mark.holdout"
        )
    return frame


def _install_holdout_police() -> None:
    """Put the boundary check in front of every binding of ``load_panel``, for the session.

    Matching the *name* ``load_panel`` is what this replaced, and it policed nothing bound under
    another one: ``from volab.store import load_panel as aliased``, and a module-level
    ``captured = store.load_panel`` taken at collection time, both read the store unwatched.
    ``_names_bound_to`` matches on identity instead, so the spelling stops mattering.

    Installed once and left in place, because after this ``volab.store.load_panel`` *is* the
    wrapper: a module imported from here on binds the wrapper by whatever name it chooses, with
    no second sweep needed, and what changes per test is ``_ACTIVE_DEV_END`` alone.

    What it still does not see is what ``pytest_fixture_setup`` below does not see either: a test
    that reads Parquet itself without calling any ``volab`` code, and a reference to the function
    held somewhere other than a module-level name -- inside a closure, a default argument or an
    object's attribute -- and taken before this ran.
    """
    # Lazy and unguarded, as in ``_forbid_holdout_unlock``: policing that fails open on an import
    # error is worse than none.
    import volab.store as store

    if _WIDER_SCOPE_SETUPS:
        # Unreachable as the fixtures stand, since this installs from a function-scoped fixture
        # that requests nothing wider, and checked anyway because the failure would be silent:
        # ``store.load_panel`` is ``_refuse_store_read`` while a wider-scoped fixture is being set
        # up, and wrapping *that* as the original would leave the police guarding the refusal and
        # the real function unguarded for the rest of the session.
        pytest.fail(
            "the holdout police cannot install while the fixture-scope refusal is in place "
            f"(setting up {_WIDER_SCOPE_SETUPS[-1]})"
        )
    original = store.load_panel
    _POLICED_ORIGINAL.append(original)
    for module, name in _names_bound_to(original):
        module.__dict__[name] = _police_holdout_read


def _uninstall_holdout_police() -> None:
    # Swept for, not replayed from a record, for the reason ``_remove_store_refusal`` gives: a
    # module first imported while the police were installed bound the wrapper, and was never
    # recorded. There is no uninstall at session end: every nested run in this suite is a
    # subprocess (``runpytest_subprocess``), so nothing in this process looks at ``load_panel``
    # after the last test, and an in-process nested run would be wrong for its own reasons.
    original = _POLICED_ORIGINAL.pop()
    for module, name in _names_bound_to(_police_holdout_read):
        module.__dict__[name] = original


def _install_store_refusal() -> None:
    # Lazy and unguarded, as in ``_forbid_holdout_unlock``: a guard that fails open on an import
    # error is worse than none.
    import volab.store as store

    # Whatever ``load_panel`` is right now: the holdout police's wrapper once that has installed,
    # which is the common case, and the original before the first test and for the duration of a
    # ``@pytest.mark.holdout`` test.
    current = store.load_panel
    _REFUSED_ORIGINAL.append(current)
    for module, name in _names_bound_to(current):
        module.__dict__[name] = _refuse_store_read


def _remove_store_refusal() -> None:
    # Swept for, not replayed from a record: a module first imported while the refusal was in
    # place bound the refusal itself, and was never recorded.
    original = _REFUSED_ORIGINAL.pop()
    for module, name in _names_bound_to(_refuse_store_read):
        module.__dict__[name] = original


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(
    fixturedef: pytest.FixtureDef[object], request: pytest.FixtureRequest
) -> Generator[None, object, object]:
    """Refuse any ``volab.store.load_panel`` call made while a non-function fixture is set up.

    Higher-scoped fixtures are set up before the function-scoped autouse ``_forbid_holdout_unlock``,
    so a read made there is never checked against ``dev_end``. This was the case for the ``real``
    fixtures in ``test_walkforward.py`` and ``test_truncation_equivalence.py`` until
    ``tests/real_panel.py`` was written, and nothing noticed.

    The rule asks pytest for the scope and finds ``load_panel`` by identity, so it names no fixture,
    no import spelling and keeps no list of exceptions: a call to the function is refused whatever
    it is called in the module that makes it, and however many helpers sit between the fixture and
    the call. Synthetic-store reads are refused too; nothing needs one at a wider scope, and an
    exemption is the thing that would rot.

    ``load_panel`` is a complete chokepoint for ``volab``: ``test_import_discipline.py`` fails any
    module under ``src`` other than ``volab.store`` that reads Parquet by another route.

    What it does not see: a test or fixture that reads files itself without calling any ``volab``
    code, and a reference to the function held somewhere other than a module-level name (inside a
    closure, a default argument or an object's attribute) and taken before the fixture ran.

    The refusal is installed only while such a fixture is being set up, and it raises instead of
    calling through. It is therefore never a frame in a successful read, which matters:
    ``store._caller_frame`` names the first frame outside ``volab`` in the audit line, and a
    permanent wrapper here would become the "caller" ``test_store_guard`` asserts on.
    """
    if fixturedef.scope == "function":
        return (yield)
    label = f"{fixturedef.argname!r} (scope {fixturedef.scope})"
    if not _WIDER_SCOPE_SETUPS:
        _install_store_refusal()
    _WIDER_SCOPE_SETUPS.append(label)
    try:
        return (yield)
    finally:
        _WIDER_SCOPE_SETUPS.pop()
        if not _WIDER_SCOPE_SETUPS:
            _remove_store_refusal()
