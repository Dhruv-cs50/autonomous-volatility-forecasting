"""Phase 2's primary point-in-time test: the table at ``t`` does not know the future is missing.

Build the feature table at cutoff ``t`` from the full panel. Build it again from a panel that
**physically stops** at ``t``. Compare byte-exactly. A trailing feature cannot tell the two
apart; a forward peek can.

This is not a proxy for point-in-time correctness. It is the definition of it, which is why it
replaces perturbation as the thing this project's claim rests on.

**Why perturbation was demoted, measured rather than argued.** Phase 1's suite rewrites the rows
after ``t`` and asserts the table is byte-identical. Two escapes survive that method as a class,
and both were built and run:

* **E1**, a band predicate on the *next* session's return, passed all 642 tests. At the cutoff a
  synthetic panel can only present ``|r_next|`` from a finite set, so a predicate thresholded
  into a gap of that set is invariant under every perturbation image. More perturbation groups
  raise the attacker's cost without closing the class. Truncation has no image to be constant
  across: the future row is gone, the forward series is null, and the predicate flips.
* **E2**, a memoised full-sample statistic, failed 0 of 100, because the only full-sample
  detector compares two tables built in one process. Truncation does not close that one either --
  see ``tests/test_process_isolation.py``, which does.

**What this file does not claim.** Detection is not uniform across cutoffs, and the honest number
is recorded rather than implied. A forward peek is visible at cutoff ``t`` only if it changes a
row at or before ``t``, and E1 changes exactly one row -- ``t``'s own -- so it is visible iff
``t``'s next return lands in its band. Measured on the real SPY panel that is **52 of 2,312
sessions, 2.2%**, so a 40-cutoff sample expects 0.9 hits and would have reported "no
disagreement" against a live forward peek about a third of the time. Three consequences, all
deliberate:

1. The real-data tier here is a **sampled ratchet** whose power is stated, not a proof.
2. The dev-window proof is this file's own exhaustive mode, ``VOLAB_PIT_EXHAUSTIVE=1``: 2,245
   cutoffs, **0 disagreements clean and 52 with E1 planted**, 6m00s, which is why it is not
   per-commit. The walk-forward adds exhaustive coverage **of its own origins** -- not of the dev
   window -- by rebuilding at every origin it evaluates from a panel truncated there. An earlier
   version of this note said the walk-forward swept every session; that is true at ``--every 1``
   and false of the recorded run, which used a stride of 5.
3. The synthetic tier is swept **exhaustively**, and its fixture deliberately carries moves
   inside E1's band. That is fixture design, not tuning the test to the plant: Phase 1's repeated
   lesson is that a leakage test can only see a leak the panel makes *expressible*, and the band
   dates on the real panel are deliberately **not** hand-fed to the sampler.

Every date here is at or before the repository's ``dev_end`` (2025-12-31); ``conftest``'s autouse
``_forbid_holdout_unlock`` fails an unmarked test that reads past it.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import pandas as pd
import pytest
from forward_scan import _forward_looks

import volab.features.build as build_module
from volab.config import Settings, load_settings
from volab.errors import DatasetNotFound
from volab.features.build import build_features
from volab.identity import first_difference
from volab.panel import as_of_dates, load_curated_panel, truncate
from volab.sessions import get_calendar, session_close_utc, sessions_between

# The longest trailing window the engine uses, so a cutoff below it compares two tables of
# nothing but nulls and proves nothing.
LONGEST_WINDOW = 66

# How many strided cutoffs the real-data tier sweeps. Chosen for wall-clock, and the cost is
# stated rather than implied: a full-vs-truncated pair on the real panel measures ~0.07s, and the
# real-data tests in this file are the bulk of the suite's growth: 644 tests in 36.8s before
# Phase 2, 707 in 89.2s after, both on an idle machine. That is the honest price of a per-commit
# ratchet that touches real data, and it is why the exhaustive mode is opt-in. Every measurement
# behind those two figures is tabulated under R38, because this number went stale three times.
SAMPLED_CUTOFFS = 60

# Opt-in exhaustive real-data sweep. ``VOLAB_`` prefixed in the manner of ``VOLAB_HOLDOUT_UNLOCK``
# -- a key, not a setting -- so ``conftest``'s ``_clean_volab_environment``, which clears only the
# names in ``_ENV_OVERRIDES``, leaves it alone. Measured at ~2.5 minutes over 2,312 cutoffs.
EXHAUSTIVE_ENV = "VOLAB_PIT_EXHAUSTIVE"

# E1's band on the next session's return, as the review that invented it wrote it. A 3-10% move
# is a credible crash-eve signal, which is what makes the escape worth closing rather than
# contrived.
BAND = (0.03, 0.10)


# ---------------------------------------------------------------------------
# The synthetic panel. Small, exhaustively swept, and present on every checkout.
# ---------------------------------------------------------------------------

SYNTHETIC_FIRST = dt.date(2024, 1, 2)
SYNTHETIC_LAST = dt.date(2024, 7, 31)

# Per-session log-ish moves. Mostly small, with four inside E1's band and two below it, because a
# fixture that cannot express a leak cannot catch one. ``test_the_fixture_carries_a_band_move``
# asserts the band really is reached rather than trusting this comment.
_SHOCKS: dict[int, float] = {17: 0.055, 38: -0.047, 61: 0.071, 84: -0.061, 29: 0.002, 70: -0.004}

VIX_FAMILY: tuple[str, ...] = ("VIX", "VIX9D", "VIX1D", "VVIX")
FRED_NAMES: tuple[str, ...] = ("BAA10Y", "DGS10", "T10Y2Y")


@dataclass(frozen=True)
class Synthetic:
    """A panel in the shape ``build_features`` is handed, plus the sessions it spans."""

    panel: pd.DataFrame
    sessions: tuple[dt.date, ...]


def _closes(count: int) -> list[float]:
    price, out = 100.0, []
    for index in range(count):
        price *= 1.0 + _SHOCKS.get(index, 0.0009 if index % 3 else -0.0012)
        out.append(round(price, 4))
    return out


def _synthetic(calendar: object) -> Synthetic:
    """SPY's bars beside every exogenous series, over seven months of 2024.

    Two fixture decisions carry weight, both inherited from escapes Phase 1 only found by
    building them:

    * **An exogenous row's ``ts`` differs from its ``as_of``.** A join that read the observation
      stamp instead of the session the value became knowable -- every release one session early --
      is otherwise byte-invisible. The lag is one session, as the real curated series carry.
    * **A session is left without a bar**, so the engine's session index has a hole in it. A panel
      with no gaps cannot tell a back-fill from an honest null.
    """
    sessions = sessions_between(calendar, SYNTHETIC_FIRST, SYNTHETIC_LAST)  # type: ignore[arg-type]
    closes = _closes(len(sessions))

    # The hole: one session carries no bar. Late enough that every cutoff swept below still has a
    # full 66-session history, and not the first or last, which the engine treats as the span.
    hole = sessions[len(sessions) // 2]
    bar_days = [day for day in sessions if day != hole]
    bar_closes = [close for day, close in zip(sessions, closes, strict=True) if day != hole]

    frames: list[pd.DataFrame] = [
        pd.DataFrame(
            {
                "symbol": ["SPY"] * len(bar_days),
                "ts": [pd.Timestamp(session_close_utc(day)) for day in bar_days],
                "as_of": list(bar_days),
                "ingested_at": [pd.Timestamp("2026-01-05T00:00:00Z")] * len(bar_days),
                "close": bar_closes,
                # Alternating rather than constant: a pass-through flag hosted a forward peek in
                # Phase 1 and a column of one value cannot show it moving.
                "has_distribution": [index % 11 == 0 for index in range(len(bar_days))],
            }
        )
    ]
    for offset, name in enumerate(VIX_FAMILY):
        frames.append(_series(name, sessions, "close", 10.0 + 10.0 * offset))
    for offset, name in enumerate(FRED_NAMES):
        frames.append(_series(name, sessions, "value", 1.5 + offset))
    return Synthetic(panel=pd.concat(frames, ignore_index=True), sessions=tuple(sessions))


def _series(name: str, sessions: Sequence[dt.date], column: str, base: float) -> pd.DataFrame:
    """One exogenous series, its ``ts`` one session behind the ``as_of`` it is knowable on."""
    return pd.DataFrame(
        {
            "symbol": [name] * len(sessions),
            "ts": [pd.Timestamp(session_close_utc(day)) - pd.Timedelta(days=1) for day in sessions],
            "as_of": list(sessions),
            "ingested_at": [pd.Timestamp("2026-01-05T00:00:00Z")] * len(sessions),
            column: [round(base + 0.37 * index % 7, 4) for index in range(len(sessions))],
        }
    )


# ---------------------------------------------------------------------------
# The sweep itself.
# ---------------------------------------------------------------------------


def sweep(
    panel: pd.DataFrame, cutoffs: Sequence[dt.date], calendar: object
) -> list[tuple[dt.date, str]]:
    """Every cutoff at which the full-panel table and the truncated-panel table disagree.

    Returns the disagreements rather than asserting, so a caller can report "k of N" -- which is
    the number this file exists to publish -- instead of stopping at the first one.
    """
    found: list[tuple[dt.date, str]] = []
    for cutoff in cutoffs:
        full = build_features(panel, through=cutoff, calendar=calendar)  # type: ignore[arg-type]
        cut = build_features(truncate(panel, cutoff), through=cutoff, calendar=calendar)  # type: ignore[arg-type]
        difference = first_difference(full, cut)
        if difference is not None:
            found.append((cutoff, difference))
    return found


def informative_cutoffs(panel: pd.DataFrame, sessions: Sequence[dt.date]) -> list[dt.date]:
    """The sessions it is worth cutting at: enough history behind, and something ahead to remove.

    A cutoff at the panel's last session removes nothing, so the two tables are the same build
    and the comparison is vacuous -- the shape of failure this project keeps finding. A cutoff
    below the longest window compares two tables of nulls. Both are excluded here rather than
    silently passing, and ``test_every_swept_cutoff_removes_at_least_one_row`` pins it.
    """
    last = max(as_of_dates(panel))
    return [day for index, day in enumerate(sessions) if index >= LONGEST_WINDOW and day < last]


def strided(cutoffs: Sequence[dt.date], count: int) -> list[dt.date]:
    """``count`` cutoffs spread evenly across ``cutoffs``, deterministically.

    A stride rather than a seeded random sample: the set is a function of the calendar alone, so
    a failure reproduces exactly and nobody has to reason about a seed. It also means the set
    cannot drift towards the dates a particular plant happens to be visible at.
    """
    if len(cutoffs) <= count:
        return list(cutoffs)
    step = len(cutoffs) / count
    return [cutoffs[int(index * step)] for index in range(count)]


# ---------------------------------------------------------------------------
# The plants. Both spelled so the AST scan cannot see them, which is the point.
# ---------------------------------------------------------------------------


def _forward_series(returns: pd.Series) -> pd.Series:
    """``r[t+1]`` down the series, spelled with no ``shift``, no slice and no ``center``.

    ``drop`` / ``set_axis`` / ``reindex``, and the axis is built with ``Index.delete`` rather than
    ``index[:-1]``. That last detail is not cosmetic: the first version of this plant used the
    negative slice and the AST scan caught it, which would have understated the escape. Removing
    the slice made it scan-silent, and only then did the entire 642-test suite pass a genuine
    forward peek. ``test_the_plants_are_invisible_to_the_forward_scan`` keeps that honest.
    """
    size = len(returns)
    ahead = returns.drop(returns.index[0])
    return ahead.set_axis(returns.index.delete(size - 1)).reindex(returns.index)


def _e1_semivariance(returns: pd.Series, *, window: int, sign: str) -> pd.Series:
    """E1: ``semivariance_proxy`` with a band on the next return OR'd into the sign mask.

    The escape that passed all 642 tests. It reads one row ahead and only at magnitudes the
    perturbation images never present, which is why no amount of extra perturbation groups
    closes it and why truncation does.
    """
    later = _forward_series(returns)
    squared = returns**2
    wrong_sign = (returns >= 0) if sign == "neg" else (returns <= 0)
    band = ((later > BAND[0]) & (later < BAND[1])) | ((later < -BAND[0]) & (later > -BAND[1]))
    return squared.mask(wrong_sign | band, 0.0).rolling(window, min_periods=window).sum()


def _unconditional_semivariance(returns: pd.Series, *, window: int, sign: str) -> pd.Series:
    """A forward peek with no band: the mask reads the next return's sign at every row.

    E1's detectability depends on the data, so this is the plant the standing red case uses: it
    moves a row at essentially every cutoff, which makes the red case deterministic rather than
    probabilistic. Still scan-silent, so it is not the easy case either.
    """
    later = _forward_series(returns)
    squared = returns**2
    wrong_sign = (later >= 0) if sign == "neg" else (later <= 0)
    return squared.mask(wrong_sign, 0.0).rolling(window, min_periods=window).sum()


@pytest.fixture
def plant(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No-op by default; the tests below install a peek over ``build``'s own binding.

    Patched at ``volab.features.build.semivariance_proxy``, which is the name ``build_features``
    actually looks up -- ``build.py`` imports the function rather than the module, so patching
    ``volab.features.realized`` would leave the live call untouched and the red case would pass
    for the wrong reason.
    """
    yield
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# The synthetic tier: present on every checkout, swept exhaustively.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def synthetic() -> Synthetic:
    return _synthetic(get_calendar(load_settings()))


def test_the_fixture_carries_every_input_group_the_engine_reads(synthetic: Synthetic) -> None:
    """Anti-vacuity: a panel missing an input cannot catch a peek hosted in that input."""
    symbols = set(synthetic.panel["symbol"])
    assert symbols == {"SPY", *VIX_FAMILY, *FRED_NAMES}
    assert set(synthetic.panel.columns) >= {"close", "value", "has_distribution"}
    bars = synthetic.panel[synthetic.panel["symbol"] == "SPY"]
    assert len(bars) == len(synthetic.sessions) - 1, "the deliberate gap session is missing"
    assert set(bars["has_distribution"]) == {True, False}, "the flag never moves"
    exogenous = synthetic.panel[synthetic.panel["symbol"] == "VIX"]
    stamps = pd.to_datetime(exogenous["ts"]).dt.date
    assert not (stamps == as_of_dates(exogenous)).any(), "ts must differ from as_of"


def test_the_fixture_carries_a_band_move(synthetic: Synthetic) -> None:
    """Anti-vacuity for E1: the panel must present a next-return inside its band.

    Without this the E1 red case below would pass on a panel where E1 is simply unreachable --
    which is the "incidental catch" failure mode this phase hit six times.
    """
    bars = synthetic.panel[synthetic.panel["symbol"] == "SPY"]
    close = pd.Series(
        bars["close"].astype("float64").to_list(), index=list(as_of_dates(bars))
    ).reindex(list(synthetic.sessions))
    returns = build_module.log_returns(close)
    inside = ((returns.abs() > BAND[0]) & (returns.abs() < BAND[1])).sum()
    assert int(inside) >= 3, f"only {int(inside)} return(s) in {BAND}; E1 would be unreachable"


def test_every_swept_cutoff_removes_at_least_one_row(synthetic: Synthetic) -> None:
    """Anti-vacuity: a cutoff that truncates nothing compares a build against itself."""
    cutoffs = informative_cutoffs(synthetic.panel, synthetic.sessions)
    assert cutoffs, "nothing to sweep"
    for cutoff in cutoffs:
        kept = len(truncate(synthetic.panel, cutoff))
        assert kept < len(synthetic.panel), f"{cutoff} removes nothing"


def test_truncation_equivalence_holds_at_every_synthetic_cutoff(synthetic: Synthetic) -> None:
    """The clean row. It matters as much as the red ones: a check that fired on honest code
    would be useless, and this one is silent at every cutoff."""
    cutoffs = informative_cutoffs(synthetic.panel, synthetic.sessions)
    found = sweep(synthetic.panel, cutoffs, get_calendar(load_settings()))
    assert not found, f"{len(found)} of {len(cutoffs)} cutoffs disagree: {found[:3]}"


def _returns_of(synthetic: Synthetic) -> pd.Series:
    """The subject's session-indexed log returns, as the engine computes them."""
    bars = synthetic.panel[synthetic.panel["symbol"] == "SPY"]
    close = pd.Series(
        bars["close"].astype("float64").to_list(), index=list(as_of_dates(bars))
    ).reindex(list(synthetic.sessions))
    return build_module.log_returns(close)


def blind_cutoffs(synthetic: Synthetic, cutoffs: Sequence[dt.date]) -> set[dt.date]:
    """The cutoffs at which a one-row-ahead peek has nothing to read, in either panel.

    Exactly the cutoffs where ``r[t+1]`` is null. There the peek reads a null from the *full*
    panel as well, so it reaches the same answer as the truncated panel and there is genuinely no
    difference to find -- a blind spot of the leak, not of the method.

    Derived rather than listed, because it is a property of where the fixture's gap falls and
    would go stale the moment that moved. Measured on this fixture: 2 of 79, 2024-04-16 and
    2024-04-17, either side of the deliberate gap at 2024-04-17.

    **``r[t]`` being null does not make a cutoff blind, and assuming it did cost a wrong rule
    here.** ``Series.mask(condition, 0.0)`` replaces wherever the condition holds, nulls
    included, so a forward-dependent mask turns an unknown return into a confident zero: at
    cutoff 2024-04-18 the full panel produced ``0.0`` where the truncated one produced ``NaN``,
    and the peek was caught at the very session whose own return is missing. The shipped
    ``semivariance_proxy`` is safe from that only because its condition is a comparison on
    ``returns`` itself, and a comparison against NaN is False -- which its docstring says, and
    which is load-bearing rather than incidental.
    """
    returns = _returns_of(synthetic)
    sessions = list(synthetic.sessions)
    blind: set[dt.date] = set()
    for cutoff in cutoffs:
        position = sessions.index(cutoff)
        ahead = returns.iloc[position + 1] if position + 1 < len(sessions) else float("nan")
        if pd.isna(ahead):
            blind.add(cutoff)
    return blind


def test_the_sweep_catches_a_scan_silent_forward_peek(
    synthetic: Synthetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The red case. Without it the suite would be a guarantee nobody has watched fail.

    Caught at **every** cutoff except the ones where the peek's own input is already null, which
    ``blind_cutoffs`` derives. Asserting a bare count instead would have hidden the interesting
    part: the two escapes here are not a hole in the method, they are cutoffs where the leaking
    expression reads the same null in both panels and so there is no difference to find.
    """
    monkeypatch.setattr(build_module, "semivariance_proxy", _unconditional_semivariance)
    cutoffs = informative_cutoffs(synthetic.panel, synthetic.sessions)
    blind = blind_cutoffs(synthetic, cutoffs)
    assert blind, "the fixture lost its gap, so this test no longer checks what it says"
    found = {cutoff for cutoff, _ in sweep(synthetic.panel, cutoffs, get_calendar(load_settings()))}
    expected = set(cutoffs) - blind
    assert found == expected, (
        f"caught {len(found)} of an expected {len(expected)}; escaped "
        f"{sorted(expected - found)}, unexpectedly caught {sorted(found - expected)}"
    )


def test_the_sweep_catches_e1_which_the_whole_phase_1_suite_passed(
    synthetic: Synthetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E1 specifically: the band predicate that passed 642 tests, caught here."""
    monkeypatch.setattr(build_module, "semivariance_proxy", _e1_semivariance)
    cutoffs = informative_cutoffs(synthetic.panel, synthetic.sessions)
    found = sweep(synthetic.panel, cutoffs, get_calendar(load_settings()))
    assert found, f"E1 escaped all {len(cutoffs)} cutoffs"


def test_the_plants_are_invisible_to_the_forward_scan() -> None:
    """Both plants read a later row, and the AST scan sees nothing. Run, not asserted in prose.

    This is the measured basis for demoting the scan: it is a ratchet against a careless
    spelling, never evidence that a table is point-in-time correct.
    """
    import inspect

    for peek in (_forward_series, _e1_semivariance, _unconditional_semivariance):
        source = inspect.getsource(peek)
        assert not _forward_looks(source), f"{peek.__name__} is not scan-silent: it would be caught"


def test_e1_is_detectable_at_exactly_the_cutoffs_where_its_band_is_reached(
    synthetic: Synthetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E1's whole detectable surface, derived and then measured against. This is the pivot.

    E1 reads one row ahead and moves a row only where that row's next return lands inside its
    band, so the cutoffs at which it is visible are **exactly** ``{t : r[t+1] in the band}``.
    This test derives that set from the fixture's own returns and asserts the sweep finds
    precisely it -- neither fewer, which would mean the sweep is weaker than the leak, nor more,
    which would mean it fires for some reason nobody has accounted for.

    That identity is the executable form of why perturbation has a ceiling here. A perturbation
    can only present the magnitudes its images contain, a finite set, and a predicate thresholded
    into a gap of that set is invariant across every one of them; further perturbation groups
    enlarge the set without closing the gap. Truncation presents no magnitude at all -- it
    removes the row -- so the band's input becomes null and the predicate moves wherever it was
    reachable at all.

    The ceiling itself is Phase 1's measurement, re-run for Phase 2 rather than taken on trust:
    E1 planted in ``src/`` passes the entire suite while this sweep fails. Both outputs are
    quoted in ``docs/superpowers/phase2-decisions.md``. It is not asserted here, because planting
    a peek into the shipped source is not something a standing test can do to itself.
    """
    monkeypatch.setattr(build_module, "semivariance_proxy", _e1_semivariance)
    returns = _returns_of(synthetic)
    sessions = list(synthetic.sessions)
    cutoffs = informative_cutoffs(synthetic.panel, synthetic.sessions)

    def reaches_the_band(cutoff: dt.date) -> bool:
        position = sessions.index(cutoff)
        if position + 1 >= len(sessions):
            return False
        ahead = returns.iloc[position + 1]
        return bool(not pd.isna(ahead) and BAND[0] < abs(ahead) < BAND[1])

    expected = {cutoff for cutoff in cutoffs if reaches_the_band(cutoff)}
    assert expected, "the fixture never reaches E1's band; the plant would be unreachable"
    found = {cutoff for cutoff, _ in sweep(synthetic.panel, cutoffs, get_calendar(load_settings()))}
    assert found == expected, (
        f"E1's detectable surface is not what it should be: expected {sorted(expected)}, "
        f"found {sorted(found)}"
    )


# ---------------------------------------------------------------------------
# The real-data tier. Skipped where the curated store is absent, which includes any fresh
# checkout: ``data/**`` is gitignored because it is derivable from the committed payloads.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Real:
    panel: pd.DataFrame
    sessions: tuple[dt.date, ...]
    settings: Settings


@pytest.fixture(scope="module")
def real() -> Real:
    settings = load_settings()
    try:
        panel = load_curated_panel(settings, "SPY", end=settings.splits.dev_end)
    except DatasetNotFound as absent:
        pytest.skip(f"the curated store holds no panel to sweep: {absent}")
    bars = panel[panel["symbol"] == "SPY"]
    sessions = sessions_between(
        get_calendar(settings), min(as_of_dates(bars)), max(as_of_dates(bars))
    )
    return Real(panel=panel, sessions=tuple(sessions), settings=settings)


def awkward_cutoffs(real: Real) -> dict[str, dt.date]:
    """Cutoffs at calendar positions where an off-by-one is most likely to hide.

    Each is derived from the calendar rather than written down, and
    ``test_the_awkward_cutoffs_are_what_they_claim_to_be`` asserts each really is what its key
    says -- a hand-listed date that stopped being a half-day would otherwise quietly become an
    ordinary one and the coverage would evaporate without a failure.
    """
    calendar = get_calendar(real.settings)
    informative = informative_cutoffs(real.panel, real.sessions)
    usable = set(informative)
    early = [day.date() for day in calendar.early_closes]

    chosen: dict[str, dt.date] = {}
    half_days = [day for day in informative if day in set(early)]
    if half_days:
        chosen["half_day"] = half_days[-1]
    eves = [day for day in _holiday_eves(real.sessions) if day in usable]
    if eves:
        chosen["holiday_eve"] = eves[-1]
    chosen["last_before_dev_end"] = informative[-1]
    chosen["first_with_a_full_history"] = informative[0]
    gaps = [day for day in _before_a_gap(real.sessions) if day in usable]
    if gaps:
        chosen["before_a_long_gap"] = gaps[-1]
    return chosen


def _holiday_eves(sessions: Sequence[dt.date]) -> list[dt.date]:
    """Sessions whose next session is not the next weekday: a weekday holiday follows."""
    out: list[dt.date] = []
    for day, following in zip(sessions, sessions[1:], strict=False):
        step = day + dt.timedelta(days=1)
        while step.weekday() >= 5:
            step += dt.timedelta(days=1)
        if following != step:
            out.append(day)
    return out


def _before_a_gap(sessions: Sequence[dt.date]) -> list[dt.date]:
    """Sessions followed by a gap of four calendar days or more."""
    return [
        day
        for day, following in zip(sessions, sessions[1:], strict=False)
        if (following - day).days >= 4
    ]


def test_the_awkward_cutoffs_are_what_they_claim_to_be(real: Real) -> None:
    chosen = awkward_cutoffs(real)
    calendar = get_calendar(real.settings)
    early = {day.date() for day in calendar.early_closes}
    assert chosen["last_before_dev_end"] < real.settings.splits.dev_end
    assert chosen["last_before_dev_end"] in set(real.sessions)
    if "half_day" in chosen:
        assert chosen["half_day"] in early
    if "holiday_eve" in chosen:
        assert chosen["holiday_eve"] in set(_holiday_eves(real.sessions))
    if "before_a_long_gap" in chosen:
        assert chosen["before_a_long_gap"] in set(_before_a_gap(real.sessions))
    assert len(chosen) >= 4, f"only {len(chosen)} awkward positions found: {chosen}"


def real_cutoffs(real: Real) -> list[dt.date]:
    """The sampled set: a stride across the dev window plus the awkward positions.

    Exhaustive when ``VOLAB_PIT_EXHAUSTIVE`` is set, which is how the recorded measurement in
    ``docs/superpowers/phase2-decisions.md`` was produced.
    """
    informative = informative_cutoffs(real.panel, real.sessions)
    if os.environ.get(EXHAUSTIVE_ENV):
        return informative
    chosen = set(strided(informative, SAMPLED_CUTOFFS)) | set(awkward_cutoffs(real).values())
    return sorted(chosen)


def test_truncation_equivalence_holds_on_the_real_curated_panel(real: Real) -> None:
    """The claim this project makes about its own feature table, against its own data.

    Sampled, and the sample's power against a band-threshold peek is 2.2% per cutoff -- stated
    in this file's docstring, and the reason the walk-forward carries the exhaustive version.
    """
    cutoffs = real_cutoffs(real)
    assert len(cutoffs) >= SAMPLED_CUTOFFS, f"only {len(cutoffs)} cutoffs to sweep"
    found = sweep(real.panel, cutoffs, get_calendar(real.settings))
    assert not found, f"{len(found)} of {len(cutoffs)} cutoffs disagree: {found[:3]}"


def test_every_real_cutoff_removes_at_least_one_row(real: Real) -> None:
    for cutoff in real_cutoffs(real):
        assert len(truncate(real.panel, cutoff)) < len(real.panel), f"{cutoff} removes nothing"


def test_the_truncated_panel_is_what_the_store_would_have_handed_over(real: Real) -> None:
    """``truncate`` is load-bearing, so it carries a witness rather than an argument.

    A post-load slice equals ``load_panel(end=cutoff)`` only while no key carries several
    vintages: the store keeps the latest vintage at or before ``end``, where a slice would drop a
    revised row instead of falling back to its earlier one. Measured true today -- no
    ``(symbol, ts)`` duplicate exists in any curated dataset -- and pinned here so that the day
    curated starts carrying revisions this fails instead of quietly becoming false.

    Compared against a *freshly read* bars panel rather than against the assembled one. The
    assembly concatenates the exogenous series, which carry no ``has_distribution``, so the
    column picks up nulls and its dtype widens from ``bool`` to ``object`` -- a difference the
    comparator reports, correctly, and one that has nothing to do with the cutoff.
    ``build_features`` casts that column itself, so the widening is harmless there, but comparing
    an assembled slice against a store read would be comparing two different questions.
    """
    from volab.panel import load_bars

    bars = real.panel[real.panel["symbol"] == "SPY"]
    assert not bars.duplicated(["symbol", "ts"]).any(), (
        "curated bars now carry more than one vintage per key; truncate() can no longer stand in "
        "for a store read and the walk-forward must re-read per cutoff instead"
    )
    whole = load_bars(real.settings, "SPY", end=real.settings.splits.dev_end)
    for cutoff in sorted(awkward_cutoffs(real).values())[-3:]:
        direct = load_bars(real.settings, "SPY", end=cutoff).reset_index(drop=True)
        sliced = truncate(whole, cutoff).reset_index(drop=True)
        assert first_difference(sliced, direct) is None, (
            f"at {cutoff} the slice is not the store's own answer"
        )


def test_the_span_fix_did_not_move_the_persisted_feature_table(real: Real) -> None:
    """Ruling R32: the claim that the span repair is a no-op on the CLI's calls gets a witness.

    ``build_features`` now spans to the later of the subject's last bar and ``through``. Every
    read the CLI makes stops at ``dev_end`` and every symbol's bars reach it, so the ``max`` is
    ``through`` either way and nothing persisted moves. That is an argument; this is the check.
    Measured: 2,312 rows fresh against 2,312 on disk, for each of SPY, QQQ and IWM.

    Two columns are normalised before comparing, and both are the store's own round-trip
    asymmetries rather than anything this change did:

    * ``as_of`` -- a write takes ``datetime.date`` and a read hands back ``datetime64[us]``.
    * ``has_distribution`` -- written as pandas' nullable ``boolean`` and read back as numpy
      ``bool``, because no null ever reached disk for it. The cast runs the *narrow* way, from
      ``bool`` up to ``boolean``, so the null mask is still compared: a null appearing in the
      fresh build would still be a difference rather than being normalised away.
    """
    from volab.panel import load_bars
    from volab.paths import Tier
    from volab.store import load_panel

    calendar = get_calendar(real.settings)

    # The premise -- that every symbol's bars reach the cutoff, which is what makes the ``max``
    # a no-op -- is checked for the whole universe, because it is cheap and it is the part that
    # could change under us when a vendor lags.
    for symbol in real.settings.universe:
        bars = load_bars(real.settings, symbol, end=real.settings.splits.dev_end)
        assert max(as_of_dates(bars)) == real.settings.splits.dev_end, (
            f"{symbol}'s bars no longer reach dev_end, so the span now reaches forward and the "
            "persisted table would move on the next build"
        )

    # The byte comparison runs for one symbol. Reading 2,312 feature partitions costs ~19s and
    # all three symbols cost ~57s; the three builds are the same code over the same shape, so the
    # extra 38s buys repetition rather than coverage. Measured when it was written, not guessed.
    for symbol in real.settings.universe[:1]:
        panel = load_curated_panel(real.settings, symbol, end=real.settings.splits.dev_end)
        fresh = build_features(panel, through=real.settings.splits.dev_end, calendar=calendar)
        stored = (
            load_panel(
                "har_daily",
                end=real.settings.splits.dev_end,
                symbols=[symbol],
                tier=Tier.FEATURES,
                settings=real.settings,
            )
            .sort_values("as_of")
            .reset_index(drop=True)[list(fresh.columns)]
        )
        fresh = fresh.assign(
            as_of=list(as_of_dates(fresh)),
            has_distribution=fresh["has_distribution"].astype("boolean"),
        )
        stored = stored.assign(
            as_of=list(as_of_dates(stored)),
            has_distribution=stored["has_distribution"].astype("boolean"),
        )
        assert first_difference(fresh, stored) is None, f"{symbol}'s persisted table moved"
