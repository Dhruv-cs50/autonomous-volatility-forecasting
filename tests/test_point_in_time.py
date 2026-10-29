"""Phase 1's exit criterion: a feature at ``t`` does not move when the future is rewritten.

**DEMOTED BY PHASE 2. This file is a per-commit ratchet, not the project's guarantee.** It is
kept, in full, because it is cheap and it catches a careless regression early -- and it is no
longer what the claim rests on. Two escapes survive its method as a *class*, both built and run:

* **E1**, a band predicate on the next session's return, passes **100 of 100** tests here and
  644 of 645 in the whole suite, verified by planting it in ``src/`` rather than by argument. At
  the cutoff the fixture can only present ``|r_next|`` from a finite set, so a predicate
  thresholded into a gap of that set is invariant under *every* perturbation image. Enumerating
  more groups raises the attacker's cost without closing the class.
* **E2**, a memoised full-sample statistic, fails **0 of 100**, because
  ``test_the_panels_extent_does_not_change_the_table`` -- the only full-sample detector in this
  file -- compares two tables built in one process.

The guarantee now lives in ``tests/test_truncation_equivalence.py`` (build at ``t`` from the full
panel, build again from a panel that physically stops at ``t``, compare: the definition of
point-in-time correctness rather than a proxy for it) and ``tests/test_process_isolation.py``
(each side of that comparison in its own interpreter, so no cross-call state can span them).

**The AST scan in this file is never to be cited as a defence.** Four evasions are measured:
escape B, the volume peek, a back-fill spelled invisibly, and E1.

Everything below is the original file, unchanged in substance. Read it as the ratchet it is.

----

This file is the project's founding premise made executable. A published agent framework had an
LLM reviewer approve a feature built from future intraday bars, and a deliberately leaky strategy
with a Sharpe of 35 passed both a Deflated Sharpe and a PBO check. Statistical corrections did
not catch the leakage and a reviewer did not either, so the harness refuses instead. These are
the tests that do the refusing, and the one thing they must be is **able to fail**: a suite that
asserts point-in-time honesty but cannot detect a violation is worse than no suite at all,
because it licenses everything downstream. Hence the deliberate anti-vacuity tests here --
``test_the_comparison_detects_a_group_perturbed_at_the_cutoff_itself`` proves that every input
group the perturbations rewrite genuinely reaches the emitted table.

Four decisions are load-bearing, and each was found by mutating the code rather than reading it:

* **``build_features`` is called directly, never through ``load_panel``.** The store drops every
  row with ``as_of > end`` before any feature code runs, so a panel routed through it would have
  nothing left to perturb and these tests could never fail. The panel handed over here extends
  well past the cutoff on purpose, which is also the realistic case: Phase 2's walk-forward loads
  the dev panel once, legitimately, then evaluates at many earlier cutoffs.
* **The perturbation changes the sign structure of the future returns, not only their scale.**
  Log returns are scale-invariant, so multiplying every future close by a constant moves exactly
  one return -- the one spanning the cutoff -- and a sign-dependent forward peek
  (``semivar_neg_proxy``) survives it untouched. Every future return becomes ``-3r``, so its sign
  *and* its magnitude both change: ``bipower_proxy`` is built from ``|r|`` and is blind to the
  sign, so a sign-only flip would miss a peek there.
* **The perturbation covers every input group.** An earlier perturbation rewrote the bars'
  ``close`` and FRED's ``value`` and left the VIX-family closes and ``has_distribution``
  untouched, and a forward peek planted in ``has_distribution`` passed both leakage tests. A
  perturbation that misses an input misses every forward peek that input could host, however
  strong it is elsewhere, so the coverage is asserted structurally rather than trusted.
* **The AST scan exempts ``targets.py`` as a whole file, by name.** See ``EXEMPT``.

Three further classes were found by a review that built working peeks this file had let through,
two of which passed the whole suite. Each is closed by the *fixture*, not by a new assertion,
which is the general lesson: a leakage test can only see a leak the panel makes expressible.

* **The exogenous join key.** ``ts`` and ``as_of`` must differ on an exogenous row, or a join that
  reads the observation stamp instead of the session the value became knowable -- every FRED
  release read one session early -- is byte-invisible. See ``EXOGENOUS_TS_LAG``.
* **Order-only peeks.** "Was tomorrow's move bigger than today's?" survives any perturbation that
  merely grows the future, and can be spelled with no ``shift``, slice or ``center`` at all, so
  the scan cannot help. Hence non-monotone ``MAGNITUDES`` and the ``shrinking_returns`` group.
* **Back-fill.** A gap bridged from the *next* session feeds a future price straight into
  ``log_returns``, and a panel with no gaps and no nulls cannot tell. Hence ``HOLED``, and the
  scan's vocabulary for ``bfill``, ``interpolate`` and ``method=``.

Comparison is byte-exact and there is no float tolerance anywhere in this file: a leak the size
of a rounding error is still a leak. ``_canonical_bytes`` says how each dtype is reduced to bytes
and why ``to_numpy().tobytes()`` cannot be used literally for every column.

Every synthetic date is on or before the repository's ``dev_end`` (2025-12-31); ``conftest``'s
autouse ``_forbid_holdout_unlock`` fails an unmarked test that reads past it.

``tests/test_features_build.py`` carries leakage-shaped tests written alongside the engine. This
file is the phase's consolidated guarantee and adds what those do not have: the AST forward-look
scan over the package, byte-identity rather than ``assert_frame_equal``, and the targets' half of
the property, where the store's own filter does the work.
"""

from __future__ import annotations

import ast
import datetime as dt
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd
import pytest
from forward_scan import (
    _forward_looks,
)

import volab
from volab.features.build import (
    CLOSE_COLUMN,
    DISTRIBUTION_COLUMN,
    EXOGENOUS_SERIES,
    FRED_SERIES,
    VALUE_COLUMN,
    build_features,
    log_returns,
)
from volab.features.targets import (
    HORIZONS,
    RETURN_COLUMN,
    build_targets,
    targets_dataset,
    write_targets,
)
from volab.identity import assert_byte_identical, canonical_bytes
from volab.paths import Tier
from volab.sessions import is_session, nth_session_after, sessions_between
from volab.store import load_panel

# Built once: constructing XNYS is slow enough that per-test construction shows up in the suite.
CAL = xcals.get_calendar("XNYS")
INGESTED = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
SUBJECT = "SPY"

# The two exogenous families, derived from the engine's own constants rather than spelled out, so
# a series added to the configuration is perturbed automatically instead of silently escaping.
IV_FAMILY: tuple[str, ...] = tuple(sorted(EXOGENOUS_SERIES - set(FRED_SERIES.values())))
FRED_FAMILY: tuple[str, ...] = tuple(sorted(FRED_SERIES.values()))

VOLUME_COLUMN = "volume"
# The two stamps every row carries. Passed straight through to the table for the bars, and the
# join key for everything else, so they are inputs like any other.
STAMP_COLUMNS: tuple[str, ...] = ("ts", "ingested_at")
# Every payload column the engine is handed, bars and exogenous together. Spelled out rather than
# read off the panel: ``open``, ``high`` and ``low`` are handed over too, and requiring a
# perturbation to move them would add an at-cutoff case for a column the engine does not read,
# which would be red on an honest engine. ``volume`` *is* here, though the engine does not read it
# either -- see the ``stamps_and_volume`` group.
INPUT_COLUMNS: tuple[str, ...] = (
    CLOSE_COLUMN,
    VALUE_COLUMN,
    DISTRIBUTION_COLUMN,
    VOLUME_COLUMN,
    *STAMP_COLUMNS,
)
# Every ``(symbol, column)`` pair the engine is handed, which is what the coverage test asserts.
EXPECTED_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {(SUBJECT, column) for column in (CLOSE_COLUMN, DISTRIBUTION_COLUMN, VOLUME_COLUMN)}
    | {(name, CLOSE_COLUMN) for name in IV_FAMILY}
    | {(name, VALUE_COLUMN) for name in FRED_FAMILY}
    | {(name, column) for name in (SUBJECT, *IV_FAMILY, *FRED_FAMILY) for column in STAMP_COLUMNS}
)

# An exogenous row's ``ts`` is the vendor's observation stamp and its ``as_of`` is the session the
# value first became knowable. For a lagged series those are different days: ``ingest/fred.py``
# writes ``ts = midnight_utc(observation)`` and ``as_of = nth_session_after(calendar, day, lag)``.
# Held apart here deliberately. If the two keys coincide -- as they did in the first version of
# this fixture -- then a join reading ``ts`` instead of ``as_of``, which is every FRED release read
# one session early and the exact leak ``_exogenous_column``'s docstring claims to prevent,
# produces byte-identical output and nothing in this repository can see it.
#
# The gap is one **session**, not one calendar day: ``ts`` is the *previous session's* midnight.
# A calendar day works only when the day after the cutoff happens to be a session -- over a
# weekend or a holiday there would be no row to misread onto the cutoff row at all, and the leak
# would pass silently for no better reason than which weekday the cutoff fell on. A session gap
# also matches what ``ingest/fred.py`` produces, where the observation falls on a session and
# ``as_of`` is the next one. The constant below is only the first row's fallback, which has no
# earlier session to borrow and is nowhere near the cutoff.
EXOGENOUS_TS_LAG = dt.timedelta(days=1)

SESSIONS = sessions_between(CAL, dt.date(2024, 1, 2), dt.date(2025, 6, 30))
# Magnitudes that do **not** increase monotonically, cycling with period 8 beneath a sign that
# cycles with period 3. Monotone magnitudes make "was tomorrow's move bigger than today's?" --
# an order-only forward peek -- answer the same on every row, and then no perturbation that
# merely scales the future can flip it. Both signs appear and none is zero, which the
# sign-flip assertions below require.
MAGNITUDES: tuple[float, ...] = (0.004, 0.011, 0.002, 0.007, 0.013, 0.005, 0.009, 0.003)
RETURNS: tuple[float, ...] = tuple(
    MAGNITUDES[k % len(MAGNITUDES)] * (-1 if k % 3 == 2 else 1) for k in range(len(SESSIONS) - 1)
)
# Session 150, so every window -- including the 66-session one -- is already complete at the
# cutoff. A forward peek then has somewhere to show up in every column instead of hiding in a
# null that was going to be null anyway.
CUTOFF = SESSIONS[150]
# Sessions appended past the panel's own end, to grow it. Still inside ``dev_end``.
EXTRA = sessions_between(CAL, dt.date(2025, 7, 1), dt.date(2025, 8, 29))


def _closes_from(first: float, returns: Sequence[float]) -> tuple[float, ...]:
    """A close path whose consecutive log ratios are exactly ``returns``.

    Built by iterating forward from ``first``, so a path whose later returns are rewritten keeps
    a bit-for-bit identical prefix: the same multiplications in the same order.
    """
    path = [first]
    for value in returns:
        path.append(path[-1] * math.exp(value))
    return tuple(path)


def _returns_of(closes: Sequence[float]) -> tuple[float, ...]:
    """``log(C_t / C_{t-1})``; one shorter than ``closes``, entry ``k`` the return into ``k+1``."""
    return tuple(math.log(b / a) for a, b in zip(closes[:-1], closes[1:], strict=True))


def _midnight(day: dt.date) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC)


def _levels(base: float, count: int) -> tuple[float, ...]:
    """An exogenous level path: strictly increasing, and never zero.

    Never zero because the perturbation below replaces a level ``v`` with ``-3v``, and ``-3 * 0``
    is ``0`` -- a row the perturbation would silently fail to move.
    """
    return tuple(base + 0.25 * k for k in range(count))


@dataclass(frozen=True)
class Inputs:
    """Every value the engine reads, one entry per input group, in session order.

    The perturbations rebuild a panel from a modified ``Inputs`` rather than assigning into the
    concatenated frame. Once the exogenous rows sit beside the bars, ``has_distribution`` is an
    object column holding bools and NaNs, and writing into it is exactly the dtype-coercing
    assignment pandas warns about -- and ``filterwarnings = ["error"]`` makes a warning a failure.
    Rebuilding also keeps the close path honest: a sign-structure perturbation is expressed in
    return space and the closes are re-derived from it, which ``.loc`` assignment cannot do.
    """

    days: tuple[dt.date, ...]
    closes: tuple[float, ...]
    flags: tuple[bool, ...]
    volumes: tuple[float, ...]
    iv: dict[str, tuple[float, ...]]
    fred: dict[str, tuple[float, ...]]
    ts_shift: tuple[dt.timedelta, ...]
    ingest_shift: tuple[dt.timedelta, ...]
    # Sessions on which the exogenous series have no row at all, and sessions whose bar carries a
    # null close. Empty by default; ``HOLED`` is the variant that uses them.
    holes: frozenset[dt.date] = frozenset()
    blanks: frozenset[dt.date] = frozenset()

    def panel(self) -> pd.DataFrame:
        """The three curated reads concatenated long-form, as ``load_panel`` would hand them."""
        frames = [self._bars()]
        frames += [self._series(name, values, CLOSE_COLUMN) for name, values in self.iv.items()]
        frames += [self._series(name, values, VALUE_COLUMN) for name, values in self.fred.items()]
        joined = pd.concat(frames, ignore_index=True)
        return joined.sort_values(["symbol", "ts"]).reset_index(drop=True)

    def _bars(self) -> pd.DataFrame:
        """A curated-bars-shaped frame: raw OHLCV, the distribution flag, no return column."""
        # ``blanks`` nulls the close of a session whose path value is otherwise intact, which is
        # how a session with no usable bar reaches the engine. Applied here and not in ``closes``
        # so the null cannot propagate through ``_closes_from``, and so the perturbations' own
        # assertions -- which read ``closes`` -- still see a real path. ``open``/``high``/``low``
        # keep their values: the engine does not read them.
        ohlc = list(self.closes)
        closes = [
            float("nan") if day in self.blanks else value
            for day, value in zip(self.days, self.closes, strict=True)
        ]
        return pd.DataFrame(
            {
                "symbol": [SUBJECT] * len(self.days),
                "ts": self._stamps(self.ts_shift),
                "as_of": list(self.days),
                "ingested_at": self._ingested(range(len(self.days))),
                "open": ohlc,
                "high": ohlc,
                "low": ohlc,
                CLOSE_COLUMN: closes,
                VOLUME_COLUMN: list(self.volumes),
                DISTRIBUTION_COLUMN: list(self.flags),
            }
        )

    def _series(self, name: str, values: tuple[float, ...], column: str) -> pd.DataFrame:
        """One exogenous series, its ``ts`` a day before the ``as_of`` it is knowable on.

        A series with ``holes`` simply has no row on those sessions, which is the normal case --
        a vendor that published nothing that day -- and the engine must reindex over it rather
        than reach across it.
        """
        keep = [k for k, day in enumerate(self.days) if day not in self.holes]
        points = [
            _midnight(self.days[k - 1] if k else self.days[0] - EXOGENOUS_TS_LAG) + self.ts_shift[k]
            for k in keep
        ]
        return pd.DataFrame(
            {
                "symbol": [name] * len(keep),
                "ts": pd.Series(pd.to_datetime(points, utc=True)),
                "as_of": [self.days[k] for k in keep],
                "ingested_at": self._ingested(keep),
                column: [values[k] for k in keep],
            }
        )

    def _stamps(self, shifts: tuple[dt.timedelta, ...]) -> pd.Series:
        points = [_midnight(day) + shift for day, shift in zip(self.days, shifts, strict=True)]
        return pd.Series(pd.to_datetime(points, utc=True))

    def _ingested(self, rows: Iterable[int]) -> pd.Series:
        points = [INGESTED + self.ingest_shift[k] for k in rows]
        return pd.Series(pd.to_datetime(points, utc=True))


# Flags on every seventh session, so True and False appear on both sides of the cutoff and
# flipping them changes every post-cutoff row.
BASE = Inputs(
    days=tuple(SESSIONS),
    closes=_closes_from(100.0, RETURNS),
    flags=tuple(k % 7 == 0 for k in range(len(SESSIONS))),
    # Varying per session, and never zero. A constant volume column is one a forward or centred
    # window could read without changing anything, so the peek would be invisible.
    volumes=tuple(1_000_000.0 + 1_000.0 * k for k in range(len(SESSIONS))),
    iv={name: _levels(10.0 * (k + 1), len(SESSIONS)) for k, name in enumerate(IV_FAMILY)},
    fred={name: _levels(1.5 + k, len(SESSIONS)) for k, name in enumerate(FRED_FAMILY)},
    ts_shift=tuple(dt.timedelta() for _ in SESSIONS),
    ingest_shift=tuple(dt.timedelta() for _ in SESSIONS),
)

# A second variant, and deliberately **not** a change to ``BASE``. Without a gap anywhere, the
# whole back-fill class is inert: ``_on_sessions(close, ...).bfill()`` bridges a gap session's
# close from the session *after* it, feeding a future price straight into ``log_returns``, and a
# panel with no nulls and no holes cannot tell. It has to be separate because a null inside
# ``BASE.closes`` would propagate through ``_closes_from`` and break the perturbations' own sign
# and null assertions, and because an exogenous hole at the cutoff makes the ``iv_levels`` and
# ``fred_levels`` at-cutoff cases red on an honest engine -- there is then no exogenous row at the
# cutoff left to perturb.
HOLED = replace(BASE, holes=frozenset({CUTOFF}), blanks=frozenset({CUTOFF}))

# The panel shapes every leakage test below runs over.
VARIANTS: dict[str, Inputs] = {"whole": BASE, "holed": HOLED}


def _truncated(inputs: Inputs, count: int) -> Inputs:
    """``inputs`` cut to its first ``count`` sessions: the same panel, less future in it."""
    return replace(
        inputs,
        days=inputs.days[:count],
        closes=inputs.closes[:count],
        flags=inputs.flags[:count],
        volumes=inputs.volumes[:count],
        iv={name: values[:count] for name, values in inputs.iv.items()},
        fred={name: values[:count] for name, values in inputs.fred.items()},
        ts_shift=inputs.ts_shift[:count],
        ingest_shift=inputs.ingest_shift[:count],
    )


def _grown(inputs: Inputs, extra: Sequence[dt.date]) -> Inputs:
    """``inputs`` with ``extra`` sessions appended, carrying values unlike anything before them."""
    count = len(extra)
    tail = _closes_from(5.0, [0.4 * (-1) ** k for k in range(count - 1)])
    return replace(
        inputs,
        days=inputs.days + tuple(extra),
        closes=inputs.closes + tail,
        flags=inputs.flags + tuple(k % 2 == 0 for k in range(count)),
        volumes=inputs.volumes + tuple(-3.0 * (k + 1) for k in range(count)),
        iv={name: values + _levels(-7.0, count) for name, values in inputs.iv.items()},
        fred={name: values + _levels(-9.0, count) for name, values in inputs.fred.items()},
        ts_shift=inputs.ts_shift + tuple(dt.timedelta() for _ in extra),
        ingest_shift=inputs.ingest_shift + tuple(dt.timedelta() for _ in extra),
    )


# Byte-identity. Approximate comparison is what lets a leak the size of a rounding error through,
# so nothing here has a tolerance.


# Both live in ``volab.identity`` now: Phase 2's truncation-equivalence tests compare tables
# too, and the walk-forward reports an exhaustive comparison as part of a run, so the
# comparator is library code. A comparator written twice is one free to be weaker in one
# place. The red cases below, which prove this one can fail, stay here.
_canonical_bytes = canonical_bytes
_assert_byte_identical = assert_byte_identical


def _moved(left: pd.Series, right: pd.Series) -> pd.Series:
    """Elementwise difference, counting a null that replaced a value as a difference."""
    return (left != right) & ~(left.isna() & right.isna())


# The perturbations. One per group of values the engine reads, and
# ``test_the_perturbations_cover_every_symbol_and_column_pair_the_engine_is_handed`` proves the
# list is whole. (The name here was stale until R129's audit; a citation nobody checks is the
# same defect as a witness that does not exist.)

PERTURBATIONS: tuple[str, ...] = (
    "returns_sign_and_scale",
    "shrinking_returns",
    "shuffled_closes",
    "null_closes",
    "iv_levels",
    "fred_levels",
    "distribution_flags",
    "stamps_and_volume",
)


def _flipped(levels: dict[str, tuple[float, ...]], after: set[int]) -> dict[str, tuple[float, ...]]:
    """Every post-cutoff level replaced by ``-3v``: a different sign *and* a different magnitude.

    Both, because ``iv_var_h*`` squares the level and is therefore blind to the sign on its own,
    exactly as ``bipower_proxy`` is blind to the sign of a return. An unphysically negative VIX is
    the point: the engine must not read these rows at all, whatever they say.
    """
    return {
        name: tuple(-3.0 * value if k in after else value for k, value in enumerate(values))
        for name, values in levels.items()
    }


def _perturb(inputs: Inputs, group: str, through: dt.date) -> Inputs:
    """Rewrite one group of the engine's inputs, after ``through`` and nowhere else."""
    after = tuple(k for k, day in enumerate(inputs.days) if day > through)
    assert len(after) > 50, "the perturbation must have a real stretch of future to act on"
    moved = set(after)

    if group == "returns_sign_and_scale":
        returns = list(_returns_of(inputs.closes))
        for k in after:
            # Entry k-1 is the return *into* session k, which is a post-cutoff session's own
            # return even though its first close is the last pre-cutoff one.
            returns[k - 1] = -3.0 * returns[k - 1]
        out = replace(inputs, closes=_closes_from(inputs.closes[0], returns))
    elif group == "shrinking_returns":
        returns = list(_returns_of(inputs.closes))
        for k in after:
            # Seven, not three. Dividing by the factor ``returns_sign_and_scale`` multiplies by
            # would restore the original magnitudes exactly, so the two groups would annihilate
            # under composition and the all-at-once test would perturb nothing in return space.
            # ``_assert_it_bit`` caught precisely that when this was ``/ 3``.
            returns[k - 1] = -returns[k - 1] / 7.0
        out = replace(inputs, closes=_closes_from(inputs.closes[0], returns))
    elif group == "shuffled_closes":
        first = after[0]
        out = replace(inputs, closes=inputs.closes[:first] + inputs.closes[first:][::-1])
    elif group == "null_closes":
        out = replace(
            inputs,
            closes=tuple(
                float("nan") if k in moved else value for k, value in enumerate(inputs.closes)
            ),
        )
    elif group == "iv_levels":
        out = replace(inputs, iv=_flipped(inputs.iv, moved))
    elif group == "fred_levels":
        out = replace(inputs, fred=_flipped(inputs.fred, moved))
    elif group == "distribution_flags":
        out = replace(
            inputs,
            flags=tuple((not flag) if k in moved else flag for k, flag in enumerate(inputs.flags)),
        )
    elif group == "stamps_and_volume":
        # ``ts`` and ``ingested_at`` are passed straight through to the table for the bars and are
        # the join key for the exogenous rows, so they are inputs like any other. One shift tuple
        # drives both kinds of row, which is what makes the exogenous stamps perturbable at all.
        # An hour keeps the ts order ascending, which the engine requires.
        #
        # ``volume`` rides along here. The engine is handed it and chooses not to read it, so a
        # centred or forward window over it would be a leak no other group could see; and because
        # this group's at-cutoff case still fires through ``ts``, folding it in adds no case that
        # is red on an honest engine.
        hour, day = dt.timedelta(hours=1), dt.timedelta(days=1)
        out = replace(
            inputs,
            ts_shift=tuple(
                shift + hour if k in moved else shift for k, shift in enumerate(inputs.ts_shift)
            ),
            ingest_shift=tuple(
                shift + day if k in moved else shift for k, shift in enumerate(inputs.ingest_shift)
            ),
            volumes=tuple(
                -7.0 * value if k in moved else value for k, value in enumerate(inputs.volumes)
            ),
        )
    else:
        raise AssertionError(f"unknown perturbation {group!r}")

    _assert_it_bit(inputs, out, group, after)
    return out


def _assert_levels_moved(
    before: dict[str, tuple[float, ...]], after: dict[str, tuple[float, ...]], rows: tuple[int, ...]
) -> None:
    for name, values in after.items():
        for k in rows:
            old, new = before[name][k], values[k]
            assert (old > 0) != (new > 0), f"{name} kept the sign of row {k}"
            assert abs(old) != abs(new), f"{name} kept the magnitude of row {k}"


def _assert_it_bit(before: Inputs, after: Inputs, group: str, rows: tuple[int, ...]) -> None:
    """A perturbation that silently no-ops makes its test pass for the wrong reason.

    Asserted per row rather than as "something somewhere changed", and the rows at or before the
    cutoff are asserted *unchanged*: a perturbation that reached into the past would make the
    comparison fail for a reason that has nothing to do with a forward peek, which would read as
    proof of a guard that is not there.
    """
    first = rows[0]
    assert after.days == before.days, "a perturbation may not move the session index"
    assert after.closes[:first] == before.closes[:first], f"{group} reached before the cutoff"
    assert after.flags[:first] == before.flags[:first], f"{group} reached before the cutoff"
    assert after.volumes[:first] == before.volumes[:first], f"{group} reached before the cutoff"
    assert after.ts_shift[:first] == before.ts_shift[:first], f"{group} reached before the cutoff"
    assert after.ingest_shift[:first] == before.ingest_shift[:first], f"{group} reached back"
    for name, values in after.iv.items():
        assert values[:first] == before.iv[name][:first], f"{group} reached before the cutoff"
    for name, values in after.fred.items():
        assert values[:first] == before.fred[name][:first], f"{group} reached before the cutoff"

    if group == "returns_sign_and_scale":
        was, now = _returns_of(before.closes), _returns_of(after.closes)
        for k in rows:
            old, new = was[k - 1], now[k - 1]
            assert (old > 0) != (new > 0), f"the return into session {k} kept its sign"
            assert abs(old) != abs(new), f"the return into session {k} kept its magnitude"
    elif group == "shrinking_returns":
        # Every post-cutoff return flips its sign and *shrinks*. The ordering flip this exists to
        # produce is a property of one particular boundary rather than of every ``through``, so it
        # is asserted in ``test_the_shrinking_group_flips_the_ordering_an_order_only_peek_reads``
        # instead of here, where ``through`` varies.
        was, now = _returns_of(before.closes), _returns_of(after.closes)
        for k in rows:
            old, new = was[k - 1], now[k - 1]
            assert (old > 0) != (new > 0), f"the return into session {k} kept its sign"
            assert abs(new) < abs(old), f"the return into session {k} did not shrink"
    elif group == "shuffled_closes":
        assert after.closes != before.closes, "the permutation left the path where it was"
        assert sorted(after.closes) == sorted(before.closes), "this must be a permutation"
    elif group == "null_closes":
        assert all(math.isnan(after.closes[k]) for k in rows)
        assert not any(math.isnan(before.closes[k]) for k in rows)
    elif group == "iv_levels":
        _assert_levels_moved(before.iv, after.iv, rows)
    elif group == "fred_levels":
        _assert_levels_moved(before.fred, after.fred, rows)
    elif group == "distribution_flags":
        assert all(after.flags[k] != before.flags[k] for k in rows), "a flag survived the flip"
    elif group == "stamps_and_volume":
        assert all(after.ts_shift[k] != before.ts_shift[k] for k in rows)
        assert all(after.ingest_shift[k] != before.ingest_shift[k] for k in rows)
        for k in rows:
            old, new = before.volumes[k], after.volumes[k]
            assert (old > 0) != (new > 0), f"the volume of session {k} kept its sign"
            assert abs(old) != abs(new), f"the volume of session {k} kept its magnitude"
    else:
        raise AssertionError(f"unknown perturbation {group!r}")


# The exit criterion itself.


def test_two_builds_of_the_same_panel_are_byte_identical() -> None:
    # Before anything is asserted *not* to change, the comparison has to agree that an unchanged
    # table is unchanged -- otherwise every test below would be red for the wrong reason.
    panel = BASE.panel()
    _assert_byte_identical(
        build_features(panel, through=CUTOFF, calendar=CAL),
        build_features(panel.copy(), through=CUTOFF, calendar=CAL),
    )


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("group", PERTURBATIONS)
def test_perturbing_one_input_group_after_the_cutoff_leaves_the_table_byte_identical(
    group: str, variant: str
) -> None:
    inputs = VARIANTS[variant]
    before = build_features(inputs.panel(), through=CUTOFF, calendar=CAL)

    perturbed = _perturb(inputs, group, CUTOFF).panel()

    after = build_features(perturbed, through=CUTOFF, calendar=CAL)
    _assert_byte_identical(before, after)


@pytest.mark.parametrize("variant", VARIANTS)
def test_perturbing_every_input_group_at_once_leaves_the_table_byte_identical(
    variant: str,
) -> None:
    inputs = VARIANTS[variant]
    before = build_features(inputs.panel(), through=CUTOFF, calendar=CAL)

    perturbed = inputs
    for group in PERTURBATIONS:
        perturbed = _perturb(perturbed, group, CUTOFF)

    after = build_features(perturbed.panel(), through=CUTOFF, calendar=CAL)
    _assert_byte_identical(before, after)


@pytest.mark.parametrize("group", PERTURBATIONS)
def test_the_comparison_detects_a_group_perturbed_at_the_cutoff_itself(group: str) -> None:
    """Every group above must be one the table actually reads, or its test proves nothing.

    The same perturbation is applied one session earlier, so it reaches the cutoff row, which the
    table does emit. Each group must then be caught. A group aimed at a column the engine ignores
    would pass its perturbation test for free and quietly cover for a forward peek in that
    column -- which is how a peek planted in ``has_distribution`` passed two leakage tests once.
    """
    before = build_features(BASE.panel(), through=CUTOFF, calendar=CAL)
    reaching = _perturb(BASE, group, SESSIONS[SESSIONS.index(CUTOFF) - 1])

    after = build_features(reaching.panel(), through=CUTOFF, calendar=CAL)

    with pytest.raises(AssertionError):
        _assert_byte_identical(before, after)


def test_the_shrinking_group_flips_the_ordering_an_order_only_peek_reads() -> None:
    """Why a perturbation that only ever *grows* the future is not enough.

    "Was tomorrow's move bigger than today's?" is a forward peek that needs no ``shift``, no slice
    and no ``center`` to write, so the scan cannot see it, and it survives every growing
    perturbation: ``-3r`` scales both sides of the comparison, a reversal still leaves a larger
    neighbour, and a null answers ``NaN > x`` with False, which was already the answer. Only
    shrinking the future flips it, and only at the cutoff row -- the one emitted row whose forward
    look reaches a perturbed return at all.
    """
    index = SESSIONS.index(CUTOFF)
    # The return into the cutoff session, and the return into the session after it.
    current, following = index - 1, index

    was = _returns_of(BASE.closes)
    assert abs(was[following]) > abs(was[current]), "the base pair must start out 'bigger'"

    shrunk = _returns_of(_perturb(BASE, "shrinking_returns", CUTOFF).closes)
    assert abs(shrunk[following]) < abs(shrunk[current]), "the ordering did not flip"

    # The growing group cannot do it, which is the whole reason both groups exist.
    grown = _returns_of(_perturb(BASE, "returns_sign_and_scale", CUTOFF).closes)
    assert abs(grown[following]) > abs(grown[current])


def test_the_perturbations_cover_every_symbol_and_column_pair_the_engine_is_handed() -> None:
    """A perturbation that misses an input misses every forward peek that input could host.

    Asserted as ``(symbol, column)`` **pairs**. Accumulating the symbols and the columns into two
    independent sets asserts only the marginals, and an earlier version of this test did exactly
    that: fourteen pairs -- every exogenous series' ``ts`` and its ``ingested_at`` -- were never
    perturbed and the test passed anyway. A join reading ``ts`` instead of ``as_of`` hides in
    precisely those pairs.
    """
    panel = BASE.panel()
    assert set(panel["symbol"]) == {SUBJECT} | set(EXOGENOUS_SERIES)

    touched: set[tuple[str, str]] = set()
    for group in PERTURBATIONS:
        perturbed = _perturb(BASE, group, CUTOFF).panel()
        for column in INPUT_COLUMNS:
            differs = _moved(perturbed[column], panel[column])
            touched.update((symbol, column) for symbol in set(panel.loc[differs, "symbol"]))

    assert touched == set(EXPECTED_PAIRS)


@pytest.mark.parametrize("variant", VARIANTS)
def test_the_panels_extent_does_not_change_the_table(variant: str) -> None:
    # The leak this guards is not a per-row one: a full-sample statistic in place of a trailing
    # window changes every row a little and no row's inputs at all, so only the extent catches it.
    inputs = VARIANTS[variant]
    narrow = build_features(_truncated(inputs, 151).panel(), through=CUTOFF, calendar=CAL)
    wide = build_features(inputs.panel(), through=CUTOFF, calendar=CAL)

    assert max(narrow["as_of"]) == CUTOFF
    _assert_byte_identical(narrow, wide)


@pytest.mark.parametrize("variant", VARIANTS)
def test_deleting_and_appending_sessions_after_the_cutoff_leaves_the_table_byte_identical(
    variant: str,
) -> None:
    inputs = VARIANTS[variant]
    before = build_features(inputs.panel(), through=CUTOFF, calendar=CAL)

    trimmed = build_features(_truncated(inputs, 200).panel(), through=CUTOFF, calendar=CAL)
    grown = build_features(_grown(inputs, EXTRA).panel(), through=CUTOFF, calendar=CAL)

    _assert_byte_identical(before, trimmed)
    _assert_byte_identical(before, grown)


def test_the_holed_variant_really_does_carry_a_gap_and_a_null() -> None:
    """The back-fill class is only reachable while this is true, so it is asserted, not assumed."""
    panel = HOLED.panel()
    exogenous = panel[panel["symbol"] != SUBJECT]
    bars = panel[panel["symbol"] == SUBJECT]

    assert CUTOFF not in set(exogenous["as_of"]), "the exogenous hole closed"
    # Exactly one session between an exogenous row's observation stamp and the session it is
    # knowable on, whatever weekday the cutoff falls on.
    stamped = {row.as_of: row.ts.date() for row in exogenous.itertuples()}
    for as_of, stamp in stamped.items():
        index = SESSIONS.index(as_of)
        assert stamp == (SESSIONS[index - 1] if index else as_of - EXOGENOUS_TS_LAG)
    assert set(exogenous["symbol"]) == set(EXOGENOUS_SERIES), "a series vanished entirely"
    assert bars.loc[bars["as_of"] == CUTOFF, CLOSE_COLUMN].isna().all(), "the null close filled in"
    # The bar itself is still there; it is the close that is unusable.
    assert len(bars) == len(SESSIONS)


# The comparison's own red-before-fix cases: each of these differences must be caught.


def test_the_comparison_catches_a_one_bit_float_difference() -> None:
    frame = build_features(BASE.panel(), through=CUTOFF, calendar=CAL)
    column = "rv_proxy_d22"
    nudged = frame.copy()
    # One ulp. Any tolerance at all would accept this, which is why there is none.
    nudged.loc[nudged.index[-1], column] = math.nextafter(frame[column].iloc[-1], math.inf)
    assert nudged[column].iloc[-1] != frame[column].iloc[-1]

    with pytest.raises(AssertionError):
        _assert_byte_identical(frame, nudged)


def test_the_comparison_catches_two_instants_an_hour_apart_in_a_dst_fall_back() -> None:
    """``tz_localize(None)`` would collapse these two, because it yields wall-clock time.

    Not reachable through ``build_features`` today -- every stamp in this project is UTC -- but the
    engine requires ``ts`` to be timezone-*aware*, not UTC, so a panel carrying an exchange-local
    stamp is expressible, and 01:00 EDT and 01:00 EST are the same wall clock on 2025-11-02.
    """
    instants = pd.Series(
        pd.to_datetime(["2025-11-02 05:00:00+00:00", "2025-11-02 06:00:00+00:00"])
    ).dt.tz_convert("America/New_York")
    first, second = instants.iloc[:1], instants.iloc[1:]

    assert first.dtype == second.dtype
    assert first.iloc[0] != second.iloc[0], "these must be two different instants"
    assert _canonical_bytes(first) != _canonical_bytes(second)


def test_the_comparison_catches_a_changed_session_date() -> None:
    frame = build_features(BASE.panel(), through=CUTOFF, calendar=CAL)
    moved = frame.copy()
    moved.loc[moved.index[0], "as_of"] = dt.date(2025, 6, 30)

    with pytest.raises(AssertionError):
        _assert_byte_identical(frame, moved)


def test_the_comparison_catches_a_dtype_that_moved_under_identical_values() -> None:
    frame = build_features(BASE.panel(), through=CUTOFF, calendar=CAL)
    retyped = frame.assign(as_of=pd.to_datetime(frame["as_of"]))

    with pytest.raises(AssertionError):
        _assert_byte_identical(frame, retyped)


def test_the_comparison_catches_a_null_flag_where_a_false_one_was() -> None:
    # The null mask half of the boolean branch: ``na_value=False`` makes the values buffer alone
    # blind to exactly this, and an unknown flag is not a False one.
    frame = build_features(BASE.panel(), through=CUTOFF, calendar=CAL)
    blanked = frame.copy()
    assert not bool(frame[DISTRIBUTION_COLUMN].iloc[1])
    blanked.loc[blanked.index[1], DISTRIBUTION_COLUMN] = pd.NA

    with pytest.raises(AssertionError):
        _assert_byte_identical(frame, blanked)


# The forward-look scan. A structural ratchet in the style of tests/test_import_discipline.py:
# the property is syntactic, so it is asserted over the source rather than over any one run.

FEATURES = Path(volab.__file__).parent / "features"

# Paths relative to FEATURES, exactly as ``ALLOWED`` in tests/test_import_discipline.py: only the
# top-level targets.py is exempt, never a targets.py in a subpackage.
#
# The exemption is by FILE, and deliberately not by construct. ``forward_rv_proxy`` legitimately
# uses both a reversed slice and ``shift(-1)``, because a target is a forward quantity by
# definition -- but allow-listing those two constructs would permit reverse slicing and negative
# offsets across the whole package, which is precisely what this scan exists to forbid elsewhere.
# Targets are safe for a different reason entirely: each row is stamped with the session its
# horizon completes, so the store's point-in-time filter cannot hand back an unrealised one.
# ``test_targets_is_exempt_because_it_really_does_look_forward`` keeps the exemption from
# outliving its reason, and ``test_the_only_forward_offsets_in_targets_are_in_forward_rv_proxy``
# keeps a whole-file exemption from becoming a hiding place.
EXEMPT = {"targets.py"}

# The scan's vocabulary and the scanner itself live in ``tests/forward_scan.py``, so a test
# that must show a planted peek is invisible to the scan can run the real scanner over it.
# The red cases proving what the scan *does* catch stay below.


def _modules(root: Path = FEATURES) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py") if path.relative_to(root).as_posix() not in EXEMPT
    )


@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.relative_to(FEATURES).as_posix())
def test_no_feature_module_looks_forward(module: Path) -> None:
    found = _forward_looks(module.read_text())
    assert not found, f"{module.name}: {found}; every window in this package is trailing"


def test_the_scan_actually_has_modules_to_scan() -> None:
    # A parametrised test over an empty list passes by collecting nothing.
    names = {path.relative_to(FEATURES).as_posix() for path in _modules()}
    assert {"build.py", "har.py", "realized.py"} <= names
    assert "targets.py" not in names


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("x.rolling(5, center=True).mean()\n", "centres a window with center=True"),
        ("x.rolling(5, center=1).mean()\n", "centres a window with center=1"),
        ("x.rolling(5, center=flag).mean()\n", "centres a window with center=flag"),
        ("x.rolling(5, 5, True).mean()\n", "centres a window with rolling's third argument True"),
        ("x.rolling(5, 5, 1).mean()\n", "centres a window with rolling's third argument 1"),
        ("x.bfill()\n", "fills from later rows with bfill()"),
        ("x.backfill()\n", "fills from later rows with backfill()"),
        ("x.interpolate()\n", "fills from later rows with interpolate()"),
        ("x.interpolate(method='time')\n", "fills from later rows with interpolate()"),
        ("_on_sessions(v, d, s).bfill()\n", "fills from later rows with bfill()"),
        ("x.fillna(method='bfill')\n", "fills from later rows with fillna(method='bfill')"),
        (
            "x.reindex(s, method='nearest')\n",
            "fills from later rows with reindex(method='nearest')",
        ),
        ("x.reindex(s, method=how)\n", "fills from later rows with reindex(method=how)"),
        ("x.shift(-1)\n", "offsets with shift(-1)"),
        ("x.shift(-horizon)\n", "offsets with shift(-horizon)"),
        ("x.shift(0 - 1)\n", "offsets with shift(0 - 1)"),
        ("x.shift(lag)\n", "offsets with shift(lag)"),
        ("shift(-1)\n", "offsets with shift(-1)"),
        ("x.shift(periods=-1)\n", "offsets with shift(periods=-1)"),
        ("x.diff(-1)\n", "offsets with diff(-1)"),
        ("x.diff(periods=-5)\n", "offsets with diff(periods=-5)"),
        ("x[::-1]\n", "slices a series with [::-1]"),
        ("x.iloc[::-1]\n", "slices a series with [::-1]"),
        ("squared[::-1].rolling(h).sum()[::-1]\n", "slices a series with [::-1]"),
    ],
    ids=lambda value: value.strip().replace("\n", " ; "),
)
def test_the_forward_scan_detects_each_bypass(source: str, expected: str) -> None:
    assert expected in _forward_looks(source)


def test_the_forward_scan_detects_a_forward_slice_as_well_as_a_reversing_one() -> None:
    # ``[i + 1:]`` is a forward window without a negative anything in it.
    assert _forward_looks("x[i + 1 :]\n")


@pytest.mark.parametrize(
    "source",
    [
        "x.rolling(5, min_periods=5).sum()\n",
        "x.rolling(5, center=False).mean()\n",
        "x.rolling(5, 5, False).mean()\n",
        "x.ffill()\n",
        "x.fillna(0.0)\n",
        "x.fillna(canonical)\n",
        "x.fillna(method='ffill')\n",
        "x.reindex(sessions)\n",
        "x.reindex(sessions, method=None)\n",
        "values.set_axis(days).reindex(sessions)\n",
        "x.rolling(window, min_periods=window).sum()\n",
        "x.shift(1)\n",
        "x.shift(periods=2)\n",
        "x.diff(1)\n",
        "returns.abs() * returns.abs().shift(1)\n",
        "sessions[-1]\n",
        "frame.iloc[0]\n",
        "panel[panel['symbol'] == series]\n",
        "data[f'rv_proxy_d{window}'] = rv_proxy(returns, window=window)\n",
    ],
    ids=lambda value: value.strip().replace("\n", " ; "),
)
def test_the_forward_scan_accepts_trailing_code(source: str) -> None:
    assert _forward_looks(source) == []


def test_the_scan_recurses_and_exempts_only_the_top_level_targets(tmp_path: Path) -> None:
    for name in ("build.py", "targets.py", "realized.py", "nested/targets.py"):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("")

    scanned = {path.relative_to(tmp_path).as_posix() for path in _modules(tmp_path)}

    assert scanned == {"build.py", "realized.py", "nested/targets.py"}


def test_targets_is_exempt_because_it_really_does_look_forward() -> None:
    # The usual ratchet assertion, inverted: the exemption must not outlive its reason. If the one
    # legitimate forward offset ever leaves targets.py, this fails and the exemption goes with it.
    assert (FEATURES / "targets.py").exists()
    found = _forward_looks((FEATURES / "targets.py").read_text())
    assert any(item.startswith("offsets with shift(") for item in found)
    assert any(item.startswith("slices a series") for item in found)


def test_the_only_forward_offsets_in_targets_are_in_forward_rv_proxy() -> None:
    for statement in ast.parse((FEATURES / "targets.py").read_text()).body:
        if isinstance(statement, ast.FunctionDef) and statement.name == "forward_rv_proxy":
            continue
        found = _forward_looks(ast.unparse(statement))
        assert not found, f"targets.py: {found} outside forward_rv_proxy"


# The targets' half of the property: the forward quantity is allowed, and the store's filter --
# not anyone's discipline -- is what keeps an unrealised one out of a caller's hands.

# 2023-09-01 .. 2024-04-30 spans Thanksgiving, Christmas, New Year's Day and the year boundary,
# and is long enough for the 66-session horizon to leave bases behind.
TARGET_SESSIONS = sessions_between(CAL, dt.date(2023, 9, 1), dt.date(2024, 4, 30))
# Written to the store instead when the read matters, so the test writes 60 partitions and not
# 160. It still crosses all three closures.
STORE_SESSIONS = sessions_between(CAL, dt.date(2023, 11, 1), dt.date(2024, 1, 31))
CLOSED = (dt.date(2023, 11, 23), dt.date(2023, 12, 25), dt.date(2024, 1, 1))


def _returns_panel(days: Sequence[dt.date]) -> pd.DataFrame:
    """One symbol's daily returns, bars-shaped: ``as_of`` is the session, not a lagged stamp.

    The returns come from ``log_returns``, the project's one sanctioned derivation, rather than
    being written straight in, so the panel is the one the CLI would hand over.
    """
    returns = [0.001 * (k + 1) * (-1 if k % 3 == 2 else 1) for k in range(len(days) - 1)]
    closes = pd.Series(_closes_from(100.0, returns), index=list(days))
    points = [dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC) for d in days]
    return pd.DataFrame(
        {
            "symbol": [SUBJECT] * len(days),
            "ts": pd.Series(pd.to_datetime(points, utc=True)),
            "as_of": list(days),
            "ingested_at": pd.Series(pd.to_datetime([INGESTED] * len(days), utc=True)),
            RETURN_COLUMN: list(log_returns(closes)),
        }
    )


def test_the_target_span_crosses_a_holiday_and_a_year_boundary() -> None:
    # Said out loud, because the two tests below mean much less if it is not true.
    for closed in CLOSED:
        assert not is_session(CAL, closed)
        assert TARGET_SESSIONS[0] < closed < TARGET_SESSIONS[-1]
        assert STORE_SESSIONS[0] < closed < STORE_SESSIONS[-1]
    assert nth_session_after(CAL, dt.date(2023, 12, 29), 1) == dt.date(2024, 1, 2)


@pytest.mark.parametrize("horizon", HORIZONS)
def test_a_targets_as_of_is_exactly_h_sessions_after_its_base_session(horizon: int) -> None:
    out = build_targets(_returns_panel(TARGET_SESSIONS), horizon=horizon, calendar=CAL)

    # Counted off the session list itself, independently of nth_session_after: the holidays and
    # the year boundary are in the list, so a horizon counted in calendar days cannot match it.
    expected = {
        TARGET_SESSIONS[k]: TARGET_SESSIONS[k + horizon]
        for k in range(len(TARGET_SESSIONS) - horizon)
    }
    got = {ts.date(): as_of for ts, as_of in zip(out["ts"], out["as_of"], strict=True)}
    assert got == expected
    assert any(base.year != realised.year for base, realised in got.items())
    assert all(base < realised for base, realised in got.items())


@pytest.mark.parametrize(
    "cutoff",
    [dt.date(2023, 12, 1), dt.date(2023, 12, 25), dt.date(2023, 12, 31)],
    ids=["a_session", "christmas_day", "a_sunday_and_a_year_end"],
)
def test_reading_targets_at_a_cutoff_hides_every_unrealised_base_session(
    synthetic_store, cutoff: dt.date
) -> None:
    """``load_panel(end=E)`` hands back no base session later than ``h`` sessions before ``E``.

    The whole set is asserted, not just its last element: a filter that let one unrealised row
    through in the middle would pass a test that only looked at the maximum.
    """
    horizon = 5
    targets = build_targets(_returns_panel(STORE_SESSIONS), horizon=horizon, calendar=CAL)
    write_targets(targets, horizon=horizon, symbol=SUBJECT, settings=synthetic_store.settings)

    back = load_panel(
        targets_dataset(horizon),
        symbols=[SUBJECT],
        end=cutoff,
        tier=Tier.FEATURES,
        settings=synthetic_store.settings,
    )

    realised = {
        STORE_SESSIONS[k]: STORE_SESSIONS[k + horizon] for k in range(len(STORE_SESSIONS) - horizon)
    }
    assert {ts.date() for ts in back["ts"]} == {
        base for base, complete in realised.items() if complete <= cutoff
    }
    for ts, as_of in zip(back["ts"], back["as_of"], strict=True):
        assert as_of.date() == realised[ts.date()]
        assert as_of.date() <= cutoff
    assert len(back) < len(targets), "some targets were written but must not yet be visible"
