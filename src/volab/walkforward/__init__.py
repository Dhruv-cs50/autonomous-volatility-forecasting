"""The walk-forward: refit and forecast at each origin, from a panel truncated at that origin.

Phase 1's spec conceded that for the walk-forward "the store cannot help", because the panel is
loaded once, legitimately, with ``end=dev_end`` and then used at many earlier cutoffs. That
sentence is **retracted** -- see ``volab.features.build``'s module docstring and section 9 of the
Phase 1 spec. The store can help, by being asked again: this module rebuilds the feature table at
every forecast origin from the panel the store would have handed over at that origin, and keeps
only the origin's own row.

Three properties follow, and each has a test rather than a paragraph.

* **No row is ever read from a panel that extends past its origin.** Not "no feature function
  looks forward", which is a property of code nobody can check by reading; this is a property of
  the data each call is handed, and ``tests/test_walkforward.py`` asserts it by recording every
  panel ``build_features`` receives and checking ``max(as_of) <= through`` on each.
* **The per-origin table is an exhaustive truncation-equivalence sweep, for free.** Each row here
  is built from a panel stopping at its own session; the persisted table was built once from the
  whole panel. Byte-comparing them covers **every** session in the window, which the sampled
  sweep in ``tests/test_truncation_equivalence.py`` deliberately does not -- E1 is visible at
  only 2.2% of real cutoffs, so a sample is a ratchet and this is the proof.
* **Training targets are sliced on ``as_of``, never on ``ts``.** A target's ``ts`` is the base
  session it is *for*; its ``as_of`` is the session its horizon completes, which is when it
  becomes knowable. Training at origin ``t`` on every target with ``ts <= t`` would feed in up to
  ``h - 1`` sessions of realized future -- 21 sessions at h=22 and 65 at h=66, which is not a
  subtlety but the whole forecast. Sliced on ``as_of`` the store's own filter does the work.

**Refit at every origin, not every k-th.** Refitting is four parameters from a normal-equation
solve and costs nothing next to the feature rebuild, and refitting less often would mean
forecasting origins between refits from a model fitted on data those origins had not yet seen --
reintroducing exactly the surface this module exists to close. The expensive thing is the
rebuild, and that is per-origin regardless.

**Three models, and the reasoning behind that list was corrected once.** ``har_levels`` is
Corsi's HAR in levels -- the baseline the literature means -- ``har_semilog`` logs the response
only and corrects the retransformation with Duan's smearing estimator, and ``random_walk`` is the
last daily variance proxy scaled to the horizon.

The first draft ran **only** a log-response variant, on the grounds that HAR in levels can
forecast a non-positive variance where QLIKE is undefined, and that a floor would be a free
parameter chosen to make the loss computable. The second half of that is still right; the
conclusion was not. Declining to run the standard baseline is the worse sin, and the machinery
to handle a non-positive forecast already existed: ``Evaluation.comparable`` drops such an origin
for **every** model on that loss and reports how many, so the comparison stays like-for-like and
the cost is visible. So the levels variant is run, with no floor.

``har_semilog``'s remaining cost is real and is on the ledger rather than in a footnote: it fits
a log scale while the loss is on the variance scale.

**Every trial is counted**, in an append-only ledger keyed by the git sha. A harness that reports
a winner without saying how many candidates it considered is the thing this project refuses.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from volab.config import Settings
from volab.errors import ModelSkip, SchemaError
from volab.features.build import build_features, log_returns
from volab.features.targets import RETURN_COLUMN, target_column, targets_dataset
from volab.identity import first_difference
from volab.models.base import UNRESTRICTED, Cohort, Domain, Forecast, Forecaster, TrainingView
from volab.panel import KEYS, as_of_dates, load_curated_panel, truncate
from volab.paths import Tier
from volab.sessions import get_calendar, sessions_between
from volab.store import load_panel

# The cohort the 72 recorded ledger lines were produced by. A ledger line with no ``cohort`` key
# means this (R86), and ``evaluate`` refuses to let any other set of contestants carry the name.
BASELINE_COHORT_NAME = "phase2-baseline"

# The HAR regressors, in the order the design matrix carries them. The intercept is prepended.
HAR_REGRESSORS: tuple[str, ...] = ("har_d_proxy", "har_w_proxy", "har_m_proxy")

# Where the trial ledger lives. Tracked in git by an explicit ``.gitignore`` exception, like
# ``data/holdout_access.jsonl``: a count of trials that ``git clean -xdf`` erases cannot be the
# number a write-up reports.
TRIAL_LEDGER = Path("data") / "trials.jsonl"


# ---------------------------------------------------------------------------
# Linear algebra, in pure Python. numpy is declared in pyproject.toml since R82, but it stays
# forbidden here: this module is on the sealed point-in-time path, whose output the byte-exact
# comparator checks, so the solve is ours. R57 explains the pivoting;
# test_the_point_in_time_path_imports_no_numpy_or_scipy is the witness for the ban.
# ---------------------------------------------------------------------------


def solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve ``matrix @ x = vector`` by Gaussian elimination with partial pivoting.

    Four unknowns, so the implementation is the textbook one rather than anything clever, and it
    raises on a singular system instead of returning a plausible answer from a pivot of zero.
    Partial pivoting is not decoration: the HAR normal equations are badly scaled -- squared
    daily returns are order 1e-4 and their monthly mean smaller again -- and fixed-pivot
    elimination loses most of the mantissa on them.
    """
    size = len(vector)
    if any(len(row) != size for row in matrix):
        raise ValueError(f"matrix must be {size}x{size}")
    rows = [[*row, value] for row, value in zip(matrix, vector, strict=True)]

    # The singularity threshold is relative **per column**, and getting that right took two
    # attempts.
    #
    # It began absolute, at 1e-14, which was a latent refusal of perfectly good windows: the HAR
    # regressors are squared daily returns of order 1e-8, so their cross-products are order 1e-16
    # and a Gram entry can sit below any fixed epsilon on a well-conditioned system.
    #
    # The second attempt scaled it by the largest magnitude anywhere in the system. That is
    # **still effectively absolute** for the regressor block, because the largest entry is always
    # the intercept cell -- the observation count, 250 to 2,500 -- while the pivots that matter
    # are orders of magnitude smaller. A reviewer measured the consequence: with HAR-like
    # regressors from Gaussian returns the fit was refused as singular at every daily volatility
    # of 0.2% and below, against roughly 0.5-0.6% on real calm equity data. A 10x margin in
    # variance, and any low-vol series or change of units crosses it.
    #
    # Scaling per column is the scale-free version: a pivot is compared against the largest
    # magnitude in **its own** column, so the test asks "has this column collapsed relative to
    # itself", which is the question, and no column's units can drown another's.
    column_scales = [
        max((abs(rows[index][column]) for index in range(size)), default=0.0)
        for column in range(size)
    ]

    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(rows[index][column]))
        tolerance = 1e-12 * column_scales[column] if column_scales[column] > 0 else 1e-300
        if abs(rows[pivot][column]) <= tolerance:
            raise SchemaError(
                "cannot fit: the normal equations are singular at column "
                f"{column}; a regressor is constant or collinear over this training window"
            )
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for below in range(column + 1, size):
            factor = rows[below][column] / rows[column][column]
            if factor == 0.0:
                continue
            for across in range(column, size + 1):
                rows[below][across] -= factor * rows[column][across]

    answer = [0.0] * size
    for column in reversed(range(size)):
        total = rows[column][size] - sum(
            rows[column][across] * answer[across] for across in range(column + 1, size)
        )
        answer[column] = total / rows[column][column]
    return answer


@dataclass(frozen=True)
class Fit:
    """A fitted linear model: an intercept, one coefficient per regressor, and the fit's size.

    ``smearing`` is Duan's nonparametric retransformation factor, the mean of
    ``exp(residual)``. ``fit_ols`` always computes it, so it is ``None`` only on a ``Fit`` built
    by hand; a caller fitting a response that was *not* logged simply ignores it, as
    ``_har_levels_forecast`` does. It exists because
    ``exp(fitted log y)`` estimates the *geometric* mean of ``y`` and the thing being forecast is
    the arithmetic mean. Measured on the real SPY panel before the correction existed: mean
    forecast over mean realized was **0.2529 at h=1** and 0.6768 at h=22 -- a factor of four low,
    which is a validity problem for any ranking built on it, and it was found by a review rather
    than by any test here.

    Duan's estimator rather than the lognormal ``exp(s^2 / 2)``: it assumes nothing about the
    residual distribution, and squared-return residuals are not Gaussian in logs. It is also a
    named estimator rather than a factor chosen to make the numbers look calibrated, which is the
    distinction R54 drew when it refused a forecast floor.
    """

    intercept: float
    coefficients: tuple[float, ...]
    regressors: tuple[str, ...]
    observations: int
    smearing: float | None = None

    def predict(self, row: pd.Series) -> float:
        """The model's prediction for one feature row, or NaN if a regressor is null there."""
        total = self.intercept
        for name, coefficient in zip(self.regressors, self.coefficients, strict=True):
            value = row[name]
            if pd.isna(value):
                return float("nan")
            total += coefficient * float(value)
        return total


def fit_ols(frame: pd.DataFrame, regressors: Sequence[str], response: str) -> Fit:
    """Ordinary least squares with an intercept, via the normal equations.

    Rows with a null in any regressor or in the response are dropped, and the count that
    survived is carried on the ``Fit`` so a caller can refuse a fit that is too small rather
    than discovering it in the coefficients.
    """
    columns = [*regressors, response]
    usable = frame[columns].dropna()
    # Columns to lists first, then rows. Indexing a DataFrame per cell costs a pandas scalar
    # lookup each time, and at 2,000 rows by 4 regressors per origin by 2,300 origins that
    # dominated the whole walk-forward when it was written the obvious way.
    taken = [usable[name].astype("float64").to_list() for name in regressors]
    design = [[1.0, *(column[index] for column in taken)] for index in range(len(usable))]
    target = [float(value) for value in usable[response]]
    width = len(regressors) + 1
    if len(design) < width + 1:
        raise SchemaError(f"cannot fit: {len(design)} usable observation(s) for {width} parameters")

    gram = [[sum(row[a] * row[b] for row in design) for b in range(width)] for a in range(width)]
    moment = [
        sum(row[a] * value for row, value in zip(design, target, strict=True)) for a in range(width)
    ]
    solved = solve(gram, moment)
    residuals = [
        value - sum(coefficient * cell for coefficient, cell in zip(solved, row, strict=True))
        for row, value in zip(design, target, strict=True)
    ]
    # Guarded against overflow so a diverged fit cannot make the correction itself inf. 700 is
    # near the float64 ceiling for ``exp``.
    smearing = sum(math.exp(min(residual, 700.0)) for residual in residuals) / len(residuals)
    return Fit(
        intercept=solved[0],
        coefficients=tuple(solved[1:]),
        regressors=tuple(regressors),
        observations=len(design),
        smearing=smearing,
    )


# ---------------------------------------------------------------------------
# Losses.
# ---------------------------------------------------------------------------


def qlike(realized: float, forecast: float) -> float:
    """QLIKE on the variance scale: ``log(f) + r / f``.

    Requires a strictly positive forecast. ``har_semilog`` gives one by construction, but
    ``har_levels`` and ``random_walk`` do **not**: Corsi's HAR can fit a negative variance, and
    the random walk forecasts zero on a session whose close did not move. Raising rather than
    returning NaN on such a forecast is deliberate and is how both cases were found: a loss that
    silently goes missing is how a model with an undefined forecast comes to look like a model
    with no observations. ``Evaluation.comparable`` is what turns the raise into a reported
    exclusion instead of a crash.
    """
    if not forecast > 0:
        raise ValueError(f"QLIKE needs a positive forecast, got {forecast!r}")
    return math.log(forecast) + realized / forecast


def squared_error(realized: float, forecast: float) -> float:
    return (realized - forecast) ** 2


LOSSES: dict[str, Callable[[float, float], float]] = {"qlike": qlike, "mse": squared_error}

# Where each loss is defined, as a predicate on the forecast. QLIKE needs a strictly positive
# one; squared error is defined everywhere. Kept beside ``LOSSES`` and keyed by the same names
# so a loss added without its domain fails at import rather than at the first origin that
# happens to break it.
LOSS_DOMAIN: dict[str, Callable[[float], bool]] = {
    "qlike": lambda forecast: forecast > 0,
    "mse": lambda forecast: True,
}
assert set(LOSS_DOMAIN) == set(LOSSES), "every loss must declare where it is defined"


# ---------------------------------------------------------------------------
# The per-origin feature table: the heart of it.
# ---------------------------------------------------------------------------


@dataclass
class RunAudit:
    """What every call was handed, so the two point-in-time claims are checked, not asserted.

    Three records, because there are three ways into the future and at first only one of them
    was watched:

    * ``calls`` -- the panel each ``build_features`` received, as ``(through, max(as_of))``.
    * ``training`` -- the training targets each origin used, as ``(origin, max(as_of))``. This
      one was missing, and its absence let a ``ts``-based target slice pass all 25 tests. The
      panel record could never have caught it: the leak was in the *targets*, which the panel
      audit does not see.

    * ``returns`` -- the last session of the return series each origin handed its contestants,
      as ``(origin, last session)``. The third way in, added with the model zoo (R85): a GARCH
      fit reads returns, which are neither the panel ``build_features`` saw nor a training
      target, so neither of the other two records could have seen one reach past the origin.

    ``violations`` reports all three, tagged, so a failure says which claim broke.
    """

    calls: list[tuple[dt.date, dt.date]] = field(default_factory=list)
    training: list[tuple[dt.date, dt.date]] = field(default_factory=list)
    returns: list[tuple[dt.date, dt.date]] = field(default_factory=list)

    def record(self, through: dt.date, last_as_of: dt.date) -> None:
        self.calls.append((through, last_as_of))

    def record_training(self, origin: dt.date, last_as_of: dt.date) -> None:
        self.training.append((origin, last_as_of))

    def record_returns(self, origin: dt.date, last_session: dt.date) -> None:
        self.returns.append((origin, last_session))

    def violations(self) -> list[tuple[str, dt.date, dt.date]]:
        return [
            *(("panel", through, last) for through, last in self.calls if last > through),
            *(("target", origin, last) for origin, last in self.training if last > origin),
            *(("returns", origin, last) for origin, last in self.returns if last > origin),
        ]


# ``features_at_origins`` lived here and is **deleted**, not kept for convenience. It was a
# second per-origin loop that nothing called, and it diverged from ``evaluate``'s: it raised
# on an empty truncation where ``evaluate`` skips. Two loops with different behaviour, one of
# them never exercised, is a place for the tested one and the believed one to come apart.


def origins_in(
    calendar: xcals.ExchangeCalendar,
    first: dt.date,
    last: dt.date,
    *,
    every: int = 1,
) -> list[dt.date]:
    """The forecast origins: every ``every``-th session from ``first`` to ``last`` inclusive."""
    if every < 1:
        raise ValueError(f"every must be at least 1, got {every}")
    sessions = sessions_between(calendar, first, last)
    return sessions[::every]


# ---------------------------------------------------------------------------
# Targets, sliced the one safe way.
# ---------------------------------------------------------------------------


def load_targets(settings: Settings, symbol: str, horizon: int, *, end: dt.date) -> pd.DataFrame:
    """One horizon's targets for one symbol, read to ``end`` on ``as_of``.

    ``load_panel`` filters on ``as_of`` and never on ``ts``, which is the whole reason this
    function is one line of real work: at origin ``t`` the knowable targets are exactly those the
    store returns for ``end=t``, and the alternative -- reading everything and filtering on the
    base session -- is the h-session leak this module's docstring describes.
    """
    name = targets_dataset(horizon)
    return load_panel(
        name,
        end=end,
        symbols=[symbol],
        columns=(*KEYS, target_column(horizon)),
        tier=Tier.FEATURES,
        settings=settings,
    )


def knowable_targets(targets: pd.DataFrame, origin: dt.date) -> pd.DataFrame:
    """The targets whose horizon had completed at ``origin``. Sliced on ``as_of``, never ``ts``.

    A named function rather than an inline slice, for one reason: a test must be able to replace
    it with the wrong slice and watch the harness notice. It could not before, and that mattered
    --- a ``ts``-based slice planted here passed **all 25** walk-forward tests, real-data tests
    included, because the test that claimed to check this re-implemented both slices inside
    itself and compared them to each other instead of exercising ``evaluate``.

    What the leak buys, measured on real SPY data over identical windows, is the part worth
    keeping: QLIKE went from -4.71022 to -4.90416 at h=1, and from -3.82735 to -3.84474 at h=66.
    **A real forward peek that moves the reported loss by 0.2% to 1.5%** -- far too little for any
    test that checks a number to catch, and the whole reason this project's refusals are
    structural rather than statistical. ``RunAudit`` records the maximum training-target
    ``as_of`` per origin so the property is checked directly instead of through its effect.
    """
    return truncate(targets, origin)


def target_panel_for(bars: pd.DataFrame, calendar: xcals.ExchangeCalendar) -> pd.DataFrame:
    """The bars with a ``log_return`` column, which is what ``build_targets`` reads.

    The same derivation the features CLI uses: the close is reindexed onto the calendar's
    sessions first, so ``shift(1)`` is the previous *session* and a missing bar poisons the two
    returns spanning it rather than being bridged by a return over two sessions.
    """
    days = list(as_of_dates(bars))
    sessions = sessions_between(calendar, min(days), max(days))
    close = pd.Series(bars["close"].astype("float64").to_list(), index=days).reindex(sessions)
    returns = log_returns(close)
    panel = bars[list(KEYS)].copy()
    panel[RETURN_COLUMN] = returns.loc[pd.Index(days)].to_list()
    return panel


def returns_through(
    panel: pd.DataFrame, origin: dt.date, calendar: xcals.ExchangeCalendar, *, symbol: str
) -> pd.Series:
    """``symbol``'s daily log returns up to ``origin``, one entry per session, null at a gap.

    The series ``build_targets`` squares, by construction: it is ``target_panel_for``'s own
    derivation, called rather than copied, so the returns a contestant is handed and the returns
    the target is built from cannot drift apart. The only addition is a reindex onto the
    sessions from the first bar through ``origin``, so a session with no bar is an explicit null
    rather than an absent row -- ``log_returns`` poisons the two returns spanning a gap and a
    contestant must be able to see that, not have it bridged. ``panel`` may carry the exogenous
    series beside the bars; only ``symbol``'s rows are read, and nothing after ``origin``.
    Witness: ``test_the_return_series_handed_to_a_model_squares_to_the_target``.
    """
    bars = truncate(panel[panel["symbol"] == symbol], origin)
    if bars.empty:
        return pd.Series([], index=pd.Index([], dtype=object), dtype="float64")
    derived = target_panel_for(bars, calendar)
    days = list(as_of_dates(derived))
    returns = pd.Series(derived[RETURN_COLUMN].astype("float64").to_list(), index=days)
    return returns.reindex(sessions_between(calendar, min(days), origin))


# ---------------------------------------------------------------------------
# The trial ledger.
# ---------------------------------------------------------------------------


def git_state(scope: Path | None = None) -> tuple[str, bool | None]:
    """The commit the run happened at, and whether the tree was dirty. ``("unknown", None)`` if
    git cannot be reached.

    **Delegated to ``volab.store.git_state`` rather than reimplemented.** That function already
    carries two fixes this would otherwise have to rediscover: ``--no-optional-locks``, because
    ``git status`` otherwise takes ``index.lock`` to refresh the index and Phase 1 lost roughly an
    hour to zero-byte locks its own audit stamp was creating; and a pathspec excluding the
    derivable data tiers, because ``!data/**/`` forces git across 80,742 files and the unscoped
    call measured 2.70s against a 5.0s timeout. A third copy of that would be a third place for
    the lesson to be forgotten. A private name across sibling modules of one package is the
    smaller cost. The accessor is ``store``'s **public** one: the import ratchet forbids a
    private import from that module and caught this exact line when it reached for
    ``_git_state``, which is the ratchet doing its job rather than an obstacle to route around.

    ``scope`` is the directory the dirtiness question is asked about, and the walk-forward CLI
    passes the **repository root** rather than taking the default. The default is the package, and
    a reviewer measured that with it an edit to ``config/base.yaml`` -- which carries
    ``splits.dev_end``, the universe and the storage root -- or to ``tests/`` reads as clean. A
    provenance line blind to the configuration is not provenance.

    **The dirty flag is not decoration.** A sha alone cannot say which code ran: a run against a
    modified tree records a commit whose contents are not what executed. The first headline run
    proved the hazard rather than hypothesising it -- see R70 -- so the flag is recorded on every
    ledger line and a dirty run is one a reader must discount.
    """
    from volab.store import git_state as store_git_state

    return store_git_state(scope)


@dataclass(frozen=True)
class Trial:
    """One evaluated configuration. Every field is part of what makes it a distinct trial."""

    model: str
    symbol: str
    horizon: int
    loss: str
    # Origins actually averaged over.
    origins: int
    value: float
    # Origins attempted but not scored for this loss, because the loss was undefined there for
    # this model or for another one. Carried on every trial so a comparison can never be read as
    # like-for-like when it is not.
    dropped: int
    # Origins the run **asked for**, and origins it could not forecast at all. Without these a
    # ledger reader cannot tell a trial covering 250 of 251 requested origins from one covering
    # 250 of 1,250: ``origins`` alone looks identical. ``skipped`` is the count whose reasons are
    # on ``Evaluation.skipped``.
    requested: int
    skipped: int
    # The origin stride the run used. A trial over every fifth session is not the same experiment
    # as one over every session, and the ledger has to be able to say which it was.
    every: int
    first_origin: dt.date
    last_origin: dt.date
    # The named set of contestants this trial was scored against. A HAR trial scored over a
    # seven-contestant intersection is a different experiment from one scored over three, by the
    # argument R75 made about the stride (R86). **A ledger line with no ``cohort`` key is
    # ``"phase2-baseline"``**: the 72 lines written before the key existed are that run.
    cohort: str = BASELINE_COHORT_NAME
    # Origins this contestant itself declined with ``ModelSkip`` (R87), so "my model refused 40
    # origins" is distinguishable from "someone else's did". The two baseline NaNs are
    # grandfathered and are not counted here.
    refused: int = 0

    def record(self) -> dict[str, object]:
        return {
            "model": self.model,
            "symbol": self.symbol,
            "horizon": self.horizon,
            "loss": self.loss,
            "origins": self.origins,
            # ``None`` rather than a bare ``NaN``, which ``json.dumps`` emits happily and no
            # strict JSON parser will read back. A ledger nobody can parse is not a ledger.
            "value": None if math.isnan(self.value) else self.value,
            "dropped": self.dropped,
            "requested": self.requested,
            "skipped": self.skipped,
            "every": self.every,
            "first_origin": self.first_origin.isoformat(),
            "last_origin": self.last_origin.isoformat(),
            "cohort": self.cohort,
            "refused": self.refused,
        }


def append_trials(
    trials: Sequence[Trial],
    *,
    root: Path,
    at: dt.datetime,
    sha: str,
    dirty: bool | None,
) -> int:
    """Append one line per trial and return how many were written. Append-only, never rewritten.

    The count is returned rather than inferred from the file, so a caller can assert that a run
    of N configurations wrote exactly N lines -- which is the only way "every trial is counted"
    is a fact about the code rather than an intention.

    ``sha`` and ``dirty`` are the caller's, captured at the **start** of its run and passed in.
    Reading them here would record the tree as it is when the writing happens, which is not the
    tree that produced the numbers -- a distinction the first headline run turned into a concrete
    mis-attribution rather than a worry (R70).
    """
    path = root / TRIAL_LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for trial in trials:
            handle.write(
                json.dumps(
                    {
                        "at": at.isoformat(),
                        "git_sha": sha,
                        "git_dirty": dirty,
                        **trial.record(),
                    }
                )
                + "\n"
            )
    return len(trials)


def count_trials(root: Path) -> int:
    path = root / TRIAL_LEDGER
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


# ---------------------------------------------------------------------------
# The models. Deliberately few, and each one counted.
# ---------------------------------------------------------------------------

# **Frozen at the three baseline names, and never appended to** (R86). ``Evaluation.comparable``
# intersects over the contestants of the cohort that was run, not over this tuple: appending a
# fourth name here would let one non-converged origin shrink every model's comparable set, and
# ``har_semilog``'s recorded MSE would move while every forecast stayed byte-identical.
# ``Evaluation.contestants`` defaults to this tuple, which is the whole of its role.
MODELS: tuple[str, ...] = ("har_levels", "har_semilog", "random_walk")


def _har_levels_forecast(
    training: pd.DataFrame, row: pd.Series, response: str
) -> tuple[float, int]:
    """Corsi's HAR: the response in **levels**, regressed on the components in levels.

    This is what "HAR" means in the literature, and it is run rather than skipped. Phase 2's
    first draft ran only a log-response variant on the grounds that HAR in levels can forecast a
    non-positive variance, where QLIKE is undefined -- and dodged the problem by not running the
    standard baseline at all. That is the wrong trade: the baseline everyone compares against
    should be on the record, and a non-positive forecast is a fact about the model worth seeing.

    No floor. An origin whose forecast comes out non-positive simply has no QLIKE, and
    ``Evaluation.comparable`` drops that origin for **every** model on that loss and reports how
    many were dropped, so the comparison stays like-for-like and the cost is visible.
    """
    fit = fit_ols(training, HAR_REGRESSORS, response)
    predicted = fit.predict(row)
    return predicted, fit.observations


def _har_semilog_forecast(
    training: pd.DataFrame, row: pd.Series, response: str
) -> tuple[float, int]:
    """A log response on level regressors, with Duan's smearing retransformation.

    **Named semi-log, not log-HAR, because that is what it is**: only the response is logged, and
    Corsi's log-HAR logs the components too. It was called ``har_log`` until a review pointed out
    both that the name was wrong and that ``exp(fitted log)`` estimates the geometric mean --
    measured at 0.2529 of the realized mean at h=1 on real data.

    Logging the components as well was considered and rejected here: ``har_d_proxy`` is a squared
    daily return and is legitimately **zero** on a session whose close did not move, so
    ``log`` of it needs either a dropped row (selection on a regressor) or an added epsilon (a
    free parameter). The smearing factor fixes the bias that mattered without introducing either.

    A non-positive target has no log, so those rows are dropped -- which is selection on the
    dependent variable, and is stated in the docstring rather than buried: it is why
    ``har_levels`` is run beside this and not replaced by it.
    """
    usable = training[training[response] > 0].copy()
    usable["_log_response"] = [math.log(float(value)) for value in usable[response]]
    fit = fit_ols(usable, HAR_REGRESSORS, "_log_response")
    predicted = fit.predict(row)
    if pd.isna(predicted):
        return float("nan"), fit.observations
    factor = 1.0 if fit.smearing is None else fit.smearing
    # Clipped only against overflow, never against being small: ``exp(700)`` is near the float64
    # ceiling, and a forecast that large is a diverged fit the loss should see as such.
    return math.exp(min(predicted, 700.0)) * factor, fit.observations


def _random_walk_forecast(row: pd.Series, horizon: int) -> float:
    """The benchmark: the last observed daily variance proxy, scaled to the horizon.

    Not a strawman and not a contender -- it is the thing a forecast has to beat before anyone
    should look at it, and it is counted as a trial like everything else.
    """
    value = row["rv_proxy_d1"]
    return float("nan") if pd.isna(value) else float(value) * horizon


# ---------------------------------------------------------------------------
# The baseline, expressed through the model interface (R85) with no arithmetic change.
#
# The three adapters call ``_har_levels_forecast``, ``_har_semilog_forecast`` and
# ``_random_walk_forecast`` -- and through them ``fit_ols`` -- **by bare name inside method
# bodies**, so each is resolved in this module's globals at the moment of the call. That is
# load-bearing and must not be tidied into a stored reference: ``tools/measure_calibration.py``
# assigns ``wf.fit_ols`` to produce the uncorrected row quoted in R73, and an adapter that had
# captured the function at import or construction would silently stop being patched.
# ``test_forcing_the_smearing_factor_still_reaches_the_baseline_cohort`` is the witness, as a red
# case: the patch must *change* the forecast.
# ---------------------------------------------------------------------------


class _Fitted:
    """A forecaster for a model whose fit and forecast are one computation, done at fit time.

    The baseline functions fit and predict together, so the answer exists when ``fit`` returns;
    ``forecast`` only checks it is being asked the question it was fitted for. A mismatched
    horizon is a ``ValueError`` and not a ``SchemaError`` (R85): ``evaluate`` files the second
    under "the window was unfittable", and a wiring bug would pass as a data problem. Witness:
    ``test_forecasting_a_horizon_the_fit_was_not_made_for_is_a_value_error``.
    """

    def __init__(self, value: float, observations: int, horizon: int) -> None:
        self.observations = observations
        self._value = value
        self._horizon = horizon

    def forecast(self, horizon: int) -> Forecast:
        if horizon != self._horizon:
            raise ValueError(
                f"fitted for horizon {self._horizon}, asked to forecast horizon {horizon}"
            )
        return Forecast(self._value, self.observations)


class _HarLevelsModel:
    name: str = "har_levels"
    fit_depends_on_horizon: bool = True
    domain: Domain = UNRESTRICTED

    def fit(self, view: TrainingView) -> Forecaster:
        value, observations = _har_levels_forecast(view.training, view.row, view.response)
        return _Fitted(value, observations, view.horizon)


class _HarSemilogModel:
    name: str = "har_semilog"
    fit_depends_on_horizon: bool = True
    domain: Domain = UNRESTRICTED

    def fit(self, view: TrainingView) -> Forecaster:
        value, observations = _har_semilog_forecast(view.training, view.row, view.response)
        return _Fitted(value, observations, view.horizon)


class _RandomWalkModel:
    name: str = "random_walk"
    # Its fit reads only the origin's own row, but the forecast is that row scaled by the horizon,
    # so it is asked the horizon it was fitted for like the others.
    fit_depends_on_horizon: bool = True
    domain: Domain = UNRESTRICTED

    def fit(self, view: TrainingView) -> Forecaster:
        return _Fitted(_random_walk_forecast(view.row, view.horizon), 0, view.horizon)


# The cohort the recorded run used. ``har_levels`` is first, and has to stay first: the first
# entry's ``observations`` is ``OriginResult.training_rows`` (R87).
BASELINE_COHORT = Cohort(
    BASELINE_COHORT_NAME, (_HarLevelsModel(), _HarSemilogModel(), _RandomWalkModel())
)
assert BASELINE_COHORT.contestants == MODELS, "the baseline cohort must be exactly MODELS"


# ---------------------------------------------------------------------------
# The walk-forward itself.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OriginResult:
    """One origin's forecasts and the realized value they are scored against."""

    origin: dt.date
    realized: float
    forecasts: dict[str, float]
    training_rows: int


@dataclass(frozen=True)
class Evaluation:
    """A finished walk-forward for one symbol and horizon, plus its trials."""

    symbol: str
    horizon: int
    results: tuple[OriginResult, ...]
    features: pd.DataFrame
    skipped: tuple[tuple[dt.date, str], ...]
    # How many origins the run asked for, and the stride it used. Carried so a trial can say what
    # it covers rather than only what it managed.
    requested: int = 0
    every: int = 1
    # The contestants the run was made over, which is what ``comparable`` and ``trials`` read.
    # Never the module-global ``MODELS`` (R86); the default exists so a hand-built baseline
    # ``Evaluation`` needs no argument.
    contestants: tuple[str, ...] = MODELS
    # ``(origin, model, reason)`` for every origin a contestant declined with ``ModelSkip``. The
    # origin stays in ``results`` with a NaN for that model (R87), so every NaN a new
    # contestant wrote has a reason on this list. The baseline's own two NaNs are grandfathered
    # and are not here: ``test_the_baseline_nans_are_not_counted_as_refusals``.
    refusals: tuple[tuple[dt.date, str, str], ...] = ()
    # The name of the cohort that was run, which is what reaches the ledger.
    cohort: str = BASELINE_COHORT_NAME

    def scored(self, model: str) -> list[tuple[dt.date, float, float]]:
        """``(origin, realized, forecast)`` for the origins where both are present."""
        return [
            (result.origin, result.realized, result.forecasts[model])
            for result in self.results
            if not pd.isna(result.realized) and not pd.isna(result.forecasts[model])
        ]

    def comparable(self, loss: str) -> set[dt.date]:
        """The origins on which ``loss`` is defined for **every** model, so the models compare.

        Averaging a loss over a different origin set per model is the silent-denominator defect
        again, one level up: two numbers that look like a comparison would be computed over
        different windows and nothing would say so. So the set is intersected across models, and
        what fell out is reported on every trial.

        The case is real, not defensive. On the first full run the random-walk forecast was
        ``0.0`` at some origins -- a session whose close did not move has a zero daily variance
        proxy -- and QLIKE is undefined there. It raised rather than returning NaN (R55), which
        is how this was found instead of averaged away.
        """
        defined = LOSS_DOMAIN[loss]
        sets = [
            {origin for origin, _, forecast in self.scored(model) if defined(forecast)}
            for model in self.contestants
        ]
        return set.intersection(*sets) if sets else set()

    def _refused(self, model: str) -> int:
        """How many origins ``model`` itself declined, distinct from origins others did."""
        return len({origin for origin, name, _ in self.refusals if name == model})

    def trials(self) -> list[Trial]:
        """One trial per model and loss. Every configuration evaluated, counted.

        **A configuration that produced no forecast at all still yields trials**, with
        ``origins=0`` and a null value. Returning an empty list was the earlier behaviour and it
        was a hole in the one thing this ledger exists for: a configuration that was run and
        produced nothing is exactly the result a selective reporter would omit.
        """
        out: list[Trial] = []
        attempted = {result.origin for result in self.results}
        if not self.results:
            for name in LOSSES:
                for model in self.contestants:
                    out.append(
                        Trial(
                            model=model,
                            symbol=self.symbol,
                            horizon=self.horizon,
                            loss=name,
                            origins=0,
                            value=float("nan"),
                            dropped=0,
                            requested=self.requested,
                            skipped=len(self.skipped),
                            every=self.every,
                            first_origin=dt.date.min,
                            last_origin=dt.date.min,
                            cohort=self.cohort,
                            refused=self._refused(model),
                        )
                    )
            return out
        for name, loss in LOSSES.items():
            usable = self.comparable(name)
            dropped = len(attempted - usable)
            for model in self.contestants:
                pairs = [
                    (realized, forecast)
                    for origin, realized, forecast in self.scored(model)
                    if origin in usable
                ]
                value = (
                    sum(loss(realized, forecast) for realized, forecast in pairs) / len(pairs)
                    if pairs
                    else float("nan")
                )
                out.append(
                    Trial(
                        model=model,
                        symbol=self.symbol,
                        horizon=self.horizon,
                        loss=name,
                        origins=len(pairs),
                        value=value,
                        dropped=dropped,
                        requested=self.requested,
                        skipped=len(self.skipped),
                        every=self.every,
                        first_origin=self.results[0].origin,
                        last_origin=self.results[-1].origin,
                        cohort=self.cohort,
                        refused=self._refused(model),
                    )
                )
        return out


def with_knowable_targets(table: pd.DataFrame, knowable: pd.DataFrame, column: str) -> pd.DataFrame:
    """The rows of ``table`` whose target had completed, each carrying its target as ``column``.

    Values are read from ``knowable``, not from a dict built over the whole target frame. Under
    the store's dedupe on ``(symbol, ts)`` the two agree, so this is not a live bug; it removes
    the dependence on that dedupe rather than documenting it, because a frame holding two
    vintages of one base session would otherwise have let the later one -- possibly with
    ``as_of > origin`` -- win the lookup. Shared by ``evaluate`` and ``coverage_refusal``, so the
    two cannot disagree about which rows are training rows.
    """
    emitted = list(as_of_dates(table))
    values = {
        stamp.date(): float(value)
        for stamp, value in zip(
            pd.to_datetime(knowable["ts"], utc=True), knowable[column], strict=True
        )
    }
    usable = table[[day in values for day in emitted]].copy()
    usable[column] = [values[day] for day in as_of_dates(usable)]
    return usable


def training_rows(usable: pd.DataFrame, column: str, required: Sequence[str] = ()) -> int:
    """Training rows that count: non-null in the HAR regressors, the response and ``required``.

    The one definition of "enough training rows", used by ``evaluate``'s sufficiency skip and by
    ``coverage_refusal``. ``HAR_REGRESSORS`` stay an unconditional floor: they are the view's own
    contract, and deriving them from the HARs' declarations would stop a GARCH-only cohort
    skipping null-regressor origins.
    """
    return len(usable.dropna(subset=[*HAR_REGRESSORS, column, *required]))


def evaluate(
    panel: pd.DataFrame,
    targets: pd.DataFrame,
    origins: Sequence[dt.date],
    *,
    symbol: str,
    horizon: int,
    calendar: xcals.ExchangeCalendar,
    minimum_training: int = 250,
    every: int = 1,
    audit: RunAudit | None = None,
    cohort: Cohort = BASELINE_COHORT,
) -> Evaluation:
    """Refit and forecast at every origin, each from a panel that physically stops there.

    **One build per origin, and it does two jobs.** The table built from the panel truncated at
    ``t`` holds a row for every session up to ``t``, so it supplies both the training rows and
    the origin's own row -- and neither was computed with ``t + 1`` in the frame. The obvious
    alternative, training from the persisted table restricted to ``t' <= t``, would be correct
    *only if* truncation equivalence holds, which is the very thing under test; this way the
    walk-forward does not lean on its own test result.

    **Targets are truncated on ``as_of``**, by the same ``truncate`` the panel goes through, so
    the knowable training targets at ``t`` are exactly the ones whose horizon had completed.
    Slicing on their base session ``ts`` instead would hand the fit up to ``horizon - 1``
    sessions of realized future.

    An origin that cannot be fitted -- too few usable rows, or a singular design over its window
    -- is **skipped and recorded**, never silently dropped: ``skipped`` is part of the result and
    the trial count is over the origins actually scored.

    **The contestants are a parameter** (R85, R86). ``cohort`` defaults to the baseline, so every
    caller written before it existed is unchanged. Each contestant is handed one frozen
    ``TrainingView`` per origin. A ``SchemaError`` from any of them skips the origin for
    everyone, as it always has; a ``ModelSkip`` is that contestant's own refusal (R87), recorded
    on ``Evaluation.refusals`` with a NaN written for it, and the origin stays in place.
    """
    if cohort.name == BASELINE_COHORT_NAME and cohort.contestants != MODELS:
        raise ValueError(
            f"the cohort name {BASELINE_COHORT_NAME!r} is reserved for the recorded run's "
            f"contestants {MODELS}, but this cohort holds {cohort.contestants}: a cohort name is "
            "a claim about which experiment produced a ledger line"
        )
    # The same class of invariant, and ``ValueError`` for the reason ``Forecaster.forecast``
    # records: a ``SchemaError`` is filed under "the window was unfittable", and a wiring bug
    # would pass as a data problem. A contestant is never dropped to make the cohort fit the
    # horizon -- the set is part of the number -- so a cohort asked a question a member cannot
    # answer is refused, before any fit (R114, US-3.9).
    refusal = cohort.refusal(horizon)
    if refusal is not None:
        raise ValueError(refusal)
    column = target_column(horizon)
    # Used **only** for the realized value a forecast is scored against, which is future relative
    # to its origin by definition and is bounded by ``dev_end`` through ``scoreable_origins``.
    # Training values come from ``knowable_targets`` instead; see below.
    realized_by_base = {
        base: float(value)
        for base, value in zip(
            [stamp.date() for stamp in pd.to_datetime(targets["ts"], utc=True)],
            targets[column],
            strict=True,
        )
    }

    rows: list[pd.DataFrame] = []
    results: list[OriginResult] = []
    skipped: list[tuple[dt.date, str]] = []
    refusals: list[tuple[dt.date, str, str]] = []

    for origin in origins:
        stopped = truncate(panel, origin)
        if audit is not None:
            audit.record(origin, max(as_of_dates(stopped)))
        table = build_features(stopped, through=origin, calendar=calendar)
        if table.empty:
            skipped.append((origin, "the truncated panel yields no feature rows"))
            continue
        emitted = list(as_of_dates(table))
        if emitted[-1] != origin:
            raise SchemaError(
                f"the table built through {origin} ends at {emitted[-1]}: the session span no "
                "longer reaches the cutoff, so this origin has no row of its own"
            )
        # A declared column the table does not have is wiring, not data: every origin would be
        # equally broken, so the run aborts instead of skipping each origin with a reason.
        absent = [name for name in cohort.requires if name not in table.columns]
        if absent:
            raise ValueError(
                f"cohort {cohort.name!r} declares required column(s) {absent} that the feature "
                f"table does not have; its columns are {list(table.columns)}"
            )
        row = table.iloc[-1]
        rows.append(table.iloc[[-1]])

        # The training set: feature rows whose target had completed by the origin. The slice is
        # ``knowable_targets``, by name, so a test can replace it with the wrong one and watch
        # the audit below notice -- which it could not before.
        knowable = knowable_targets(targets, origin)
        # Values are read from ``knowable`` (see ``with_knowable_targets``), never from the whole
        # target frame.
        if audit is not None and len(knowable):
            audit.record_training(origin, max(as_of_dates(knowable)))
        usable = with_knowable_targets(table, knowable, column)
        # Built from ``knowable``, not from a dict over the whole target frame: the rule above
        # applies to the completion date for exactly the reason it applied to the value. A column
        # the HARs never read -- ``fit_ols`` selects its regressors and response by name -- that
        # lets a contestant see, and the conformance suite check, when each target became known.
        completed = {
            stamp.date(): done
            for stamp, done in zip(
                pd.to_datetime(knowable["ts"], utc=True), as_of_dates(knowable), strict=True
            )
        }
        usable["target_as_of"] = [completed[day] for day in as_of_dates(usable)]

        counted = training_rows(usable, column, cohort.requires)
        if counted < minimum_training:
            why = f"only {len(usable)} knowable training row(s)"
            if cohort.requires:
                # The count that fell short is not ``len(usable)``: "only 1500 rows" against a
                # minimum of 250 would contradict itself when the shortfall is in a column.
                why += f", {counted} of them non-null in {list(cohort.requires)}"
            skipped.append((origin, why))
            continue
        # An origin whose own regressors are null has no forecast, and that is skipped rather
        # than scored as NaN. The case is real, not hypothetical: a session within 22 of a
        # missing bar has a null monthly component, because an incomplete window is null here and
        # never partially filled. Scoring it as a NaN would quietly shrink the denominator of
        # every loss while the origin still appeared to have been evaluated.
        null_regressors = [
            name for name in (*HAR_REGRESSORS, *cohort.requires) if pd.isna(row[name])
        ]
        if null_regressors:
            skipped.append((origin, f"null regressor(s) at the origin: {null_regressors}"))
            continue
        view = TrainingView(
            symbol=symbol,
            origin=origin,
            horizon=horizon,
            response=column,
            training=usable,
            row=row,
            returns=returns_through(stopped, origin, calendar, symbol=symbol),
            minimum_training=minimum_training,
            stride=every,
        )
        if audit is not None:
            audit.record_returns(origin, view.returns.index[-1])
        forecasts: dict[str, float] = {}
        declined: list[tuple[dt.date, str, str]] = []
        observations = 0
        try:
            for index, model in enumerate(cohort.models):
                try:
                    answer = model.fit(view).forecast(horizon)
                except ModelSkip as reason:
                    forecasts[model.name] = float("nan")
                    declined.append((origin, model.name, str(reason)))
                    continue
                forecasts[model.name] = answer.value
                # ``training_rows`` is the first contestant's, which is ``har_levels`` in every
                # named cohort; ``test_every_named_cohort_begins_with_har_levels`` is the witness.
                if index == 0:
                    observations = answer.observations
        except SchemaError as refused:
            skipped.append((origin, f"a fit refused the window: {refused}"))
            continue
        refusals.extend(declined)

        results.append(
            OriginResult(
                origin=origin,
                realized=realized_by_base.get(origin, float("nan")),
                forecasts=forecasts,
                training_rows=observations,
            )
        )

    features = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return Evaluation(
        symbol=symbol,
        horizon=horizon,
        results=tuple(results),
        features=features,
        skipped=tuple(skipped),
        requested=len(origins),
        every=every,
        contestants=cohort.contestants,
        refusals=tuple(refusals),
        cohort=cohort.name,
    )


def coverage_refusal(
    stored: pd.DataFrame,
    targets: pd.DataFrame,
    required: Sequence[str],
    origins: Sequence[dt.date],
    *,
    horizon: int,
    minimum_training: int,
) -> str | None:
    """Why ``required`` cannot support a run over ``origins``, or ``None`` when it can.

    A configuration is refused when, at the **first requested origin**, a column in ``required``
    has fewer than ``minimum_training`` sessions that are both non-null **and have a completed
    target** -- the predicate ``evaluate`` counts, through the same ``with_knowable_targets`` and
    ``training_rows``. That is keyed to the ``minimum_training``-th such session and not to the
    column's first: a window beginning at the first non-null session would otherwise pass, and the
    contestant would then decline on thin training while ``Evaluation.comparable`` intersected
    those origins out of **every** contestant's loss (US-3.9, measured: 69 of the 119 origins a
    VIX1D contestant would cost the shootout are caught by the first-session rule, 119 by this).
    ``targets`` and ``horizon`` are required, not conveniences: the qualifying origin sits ``h``
    sessions after the ``minimum_training``-th non-null session, and a signature without them can
    only compute the wrong boundary.

    **An interior gap is not refused.** It is a data accident (bond holidays with XNYS open), and
    ``evaluate`` skips the origin for everyone with the column named. A window too thin for the
    HAR floor alone is not this refusal's either: the column is not to blame, and that is today's
    per-origin skipping.

    ``stored`` is the persisted feature table ``run_configuration`` already holds for the
    truncation comparison, and ``targets`` is the frame it passes to ``evaluate``: no read path is
    opened and no ``end`` is taken. The decision reads the whole of both, sessions after the first
    origin included. That is a configuration-level decision and not a forecast -- the standing of
    ``scoreable_origins``, which reads the whole target frame to choose origins -- and no value
    from it reaches a fit; ``RunAudit`` continues to police the per-origin builds.
    """
    if not required or not origins:
        return None
    absent = [name for name in required if name not in stored.columns]
    if absent:
        raise ValueError(
            f"required column(s) {absent} are not in the stored feature table, "
            f"whose columns are {list(stored.columns)}"
        )
    column = target_column(horizon)
    stored_days = list(as_of_dates(stored))

    def rows(origin: dt.date, extra: Sequence[str]) -> int:
        built = stored[[day <= origin for day in stored_days]]
        usable = with_knowable_targets(built, knowable_targets(targets, origin), column)
        return training_rows(usable, column, extra)

    first = origins[0]
    if rows(first, ()) < minimum_training or rows(first, required) >= minimum_training:
        return None
    short = [name for name in required if rows(first, (name,)) < minimum_training] or list(required)
    qualifying = next((o for o in origins if rows(o, required) >= minimum_training), None)
    where = (
        f"none of the {len(origins)} requested origins qualifies"
        if qualifying is None
        else f"the first that qualifies is {qualifying}, and {origins.index(qualifying)} of the "
        f"{len(origins)} requested origins precede it"
    )
    return (
        f"column(s) {short} have fewer than {minimum_training} sessions that are both non-null and "
        f"have a completed target at the first requested origin {first} (h={horizon}); {where}. "
        "Every other contestant's origins before that would be intersected out of its loss "
        "(Evaluation.comparable). Move --from to the first origin that qualifies, or run the "
        "contestant in a cohort of its own"
    )


def load_for(
    settings: Settings, symbol: str, horizon: int, *, report: Callable[[str], None] | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The panel and the targets for one symbol and horizon, both read to ``dev_end``.

    ``dev_end`` and nothing else. There is no parameter here that could ask for a later ``end``:
    the holdout is Phase 6's single confirmatory read, and an argument accepting a later date
    would be the way this module became the thing that opened it.
    """
    end = settings.splits.dev_end
    panel = load_curated_panel(settings, symbol, end=end, report=report)
    targets = load_targets(settings, symbol, horizon, end=end)
    return panel, targets


def scoreable_origins(
    settings: Settings,
    targets: pd.DataFrame,
    *,
    first: dt.date,
    last: dt.date,
    every: int = 1,
) -> list[dt.date]:
    """Origins in ``[first, last]`` whose target has completed at or before ``dev_end``.

    An origin whose horizon runs past the seal has no realized value this phase may read, so it
    is excluded here rather than scored against a NaN and quietly diluting the loss.
    """
    bases = {stamp.date() for stamp in pd.to_datetime(targets["ts"], utc=True)}
    calendar = get_calendar(settings)
    return [day for day in origins_in(calendar, first, last, every=every) if day in bases]


# ---------------------------------------------------------------------------
# One driver loop, not two (R97).
#
# R76 deleted the last per-configuration loop for diverging from ``evaluate``'s. Four rulings live
# in the loop below and all four are load-bearing: R63 (an audit violation refuses), R74 (trials
# are appended per configuration, so one that refuses later cannot erase them), the empty
# configuration still being counted, and the truncation-equivalence report. The walk-forward CLI
# and the model shootout both call this, so neither can drift from the other.
# ---------------------------------------------------------------------------


def compare_against_persisted(
    built: pd.DataFrame, stored: pd.DataFrame | None
) -> tuple[int, str | None]:
    """Byte-compare the per-origin rows against the persisted table, on the origins built.

    Returns the number of origins compared and the first disagreement, if any. The comparison
    normalises the store's two documented round-trip asymmetries and nothing else: ``as_of``
    (written as ``date``, read back as ``datetime64[us]``) and ``has_distribution`` (written as
    nullable ``boolean``, read back as ``bool`` because no null ever reached disk). The second
    cast runs the narrow way, ``bool`` up to ``boolean``, so a null appearing in a fresh build is
    still a difference rather than being normalised away.
    """
    if stored is None or built.empty:
        return 0, None
    wanted = set(as_of_dates(built))
    rows = stored[[day in wanted for day in as_of_dates(stored)]].reset_index(drop=True)
    if len(rows) != len(built):
        return len(built), f"the store holds {len(rows)} of the {len(built)} origins built"

    left = built.assign(
        as_of=list(as_of_dates(built)),
        has_distribution=built["has_distribution"].astype("boolean"),
    )
    right = rows[list(built.columns)].assign(
        as_of=list(as_of_dates(rows)),
        has_distribution=rows["has_distribution"].astype("boolean"),
    )
    return len(built), first_difference(left, right)


@dataclass(frozen=True)
class ConfigurationOutcome:
    """What one configuration did, for the caller's loop to act on."""

    # ``None`` when no origin in the window was scoreable and only the empty trials were written.
    evaluation: Evaluation | None
    trials: tuple[Trial, ...]
    # Ledger lines this configuration appended.
    written: int
    # Nothing was appended for this configuration and the caller exits non-zero. Two causes with
    # the same consequence: an audit recorded a read past a cutoff (R63), or the cohort's declared
    # columns do not cover the window (``coverage_refusal``), in which case ``evaluation`` is
    # ``None`` because no fit was made.
    refused: bool
    # The built rows differ from the persisted table. The caller's exit status, not a refusal.
    disagrees: bool


def run_configuration(
    settings: Settings,
    symbol: str,
    horizon: int,
    panel: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    calendar: xcals.ExchangeCalendar,
    first: dt.date,
    last: dt.date,
    every: int,
    minimum_training: int,
    cohort: Cohort,
    persisted: Callable[[], pd.DataFrame | None],
    append: Callable[[Sequence[Trial]], int],
    report: Callable[[str], None] = print,
) -> ConfigurationOutcome:
    """Evaluate one ``(symbol, horizon)`` over ``cohort`` and append its trials (R97).

    **Everything that reaches the disk or the clock is passed in**, not reached for: the frames
    are the caller's (loaded by ``load_for``, which stops at ``dev_end`` and takes no later
    ``end``), ``persisted`` produces the stored feature table to compare against, and ``append``
    writes the trials -- the ledger for the CLI, a sink for the shootout. So the sha and dirty flag
    (R70) are captured by the caller at the start of its run and arrive inside ``append``, and the
    date window is the caller's, already clamped to ``dev_end`` (R62). This function takes no
    ``end`` of its own and nothing here opens a path.

    Trials are appended **now**, per configuration (R74), and a configuration that was run and
    produced nothing is still counted. Witnesses, each through this function rather than through
    the CLI's use of it: ``test_the_driver_appends_one_line_per_trial_labelled_with_the_cohort``,
    ``test_the_driver_refuses_an_audit_violation_and_appends_nothing``,
    ``test_the_driver_counts_a_configuration_with_nothing_to_score`` and
    ``test_the_driver_reports_a_disagreement_with_the_persisted_table``.
    """
    # Before the empty-window branch below, which appends counted lines labelled with the cohort
    # without ever reaching ``evaluate``: an illegal pairing must not be written under that name
    # just because the window happened to hold no origin.
    refusal = cohort.refusal(horizon)
    if refusal is not None:
        raise ValueError(refusal)
    origins = scoreable_origins(settings, targets, first=first, last=last, every=every)
    if not origins:
        # Counted, not skipped. A configuration that was run and produced nothing is exactly the
        # result selective reporting omits.
        report("  no origin in the window has a target completed at or before dev_end")
        empty = Evaluation(
            symbol=symbol,
            horizon=horizon,
            results=(),
            features=pd.DataFrame(),
            skipped=(),
            requested=0,
            every=every,
            contestants=cohort.contestants,
            cohort=cohort.name,
        )
        counted = empty.trials()
        return ConfigurationOutcome(
            evaluation=None,
            trials=tuple(counted),
            written=append(counted),
            refused=False,
            disagrees=False,
        )
    report(f"  {len(origins)} origin(s) from {origins[0]} to {origins[-1]}")

    # Coverage is checked only for a cohort that declares a requirement, so every existing run's
    # output is unchanged, and the stored table is read once for this and the comparison below.
    stored: pd.DataFrame | None = None
    loaded = False
    if cohort.requires:
        stored, loaded = persisted(), True
        if stored is None:
            # Nothing to check is not a pass, and not a refusal on no evidence either: it falls
            # through to ``evaluate``'s per-origin skips, which still record each reason.
            report("  coverage: NOT CHECKED, the store holds no built table")
        else:
            reason = coverage_refusal(
                stored,
                targets,
                cohort.requires,
                origins,
                horizon=horizon,
                minimum_training=minimum_training,
            )
            if reason is not None:
                # Before ``evaluate``, or the fits this exists to prevent have already run.
                report(f"  REFUSED: {reason}")
                return ConfigurationOutcome(
                    evaluation=None, trials=(), written=0, refused=True, disagrees=False
                )

    audit = RunAudit()
    evaluation = evaluate(
        panel,
        targets,
        origins,
        symbol=symbol,
        horizon=horizon,
        calendar=calendar,
        minimum_training=minimum_training,
        every=every,
        audit=audit,
        cohort=cohort,
    )
    violations = audit.violations()
    if violations:
        # Fail loudly. Reading past a cutoff is the one defect this whole module exists to
        # prevent, and reporting it as a warning would be the posture this project refuses.
        kind, at, latest = violations[0]
        report(f"  REFUSED: {len(violations)} call(s) read past their cutoff")
        report(f"           first: {kind} at={at} max as_of={latest}")
        return ConfigurationOutcome(
            evaluation=evaluation, trials=(), written=0, refused=True, disagrees=False
        )
    report(
        f"  {len(audit.calls)} build(s) and {len(audit.training)} training set(s), "
        "every one stopping at its own origin"
    )

    compared, difference = compare_against_persisted(
        evaluation.features, stored if loaded else persisted()
    )
    disagrees = False
    if compared == 0:
        # Nothing to compare is not agreement. Printing "0 of 0 identical" and exiting 0 would be
        # a check that passes by checking nothing, which is this project's named anti-goal; it
        # happens whenever the store holds no persisted table, so it is a reachable state and not
        # a defensive branch.
        report("  truncation equivalence: NOT COMPARED, the store holds no built table")
    elif difference is None:
        report(f"  truncation equivalence: {compared} of {compared} origins identical")
    else:
        disagrees = True
        report(f"  truncation equivalence: DISAGREES at {compared} origins: {difference}")

    if evaluation.skipped:
        report(
            f"  {len(evaluation.skipped)} origin(s) skipped, first: "
            f"{evaluation.skipped[0][0]} {evaluation.skipped[0][1]}"
        )
    trials = evaluation.trials()
    for trial in trials:
        report(
            f"  {trial.model:12s} {trial.loss:5s} {trial.value:+.6g} "
            f"over {trial.origins} origin(s), {trial.dropped} dropped"
        )
    return ConfigurationOutcome(
        evaluation=evaluation,
        trials=tuple(trials),
        written=append(trials),
        refused=False,
        disagrees=disagrees,
    )
