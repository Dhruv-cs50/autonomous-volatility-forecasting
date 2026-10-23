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

**Log-HAR, not HAR in levels.** HAR in levels can forecast a non-positive variance, and QLIKE is
undefined there; a floor would be a free parameter chosen to make the loss computable. Fitting
``log`` variance and exponentiating gives a strictly positive forecast by construction. The cost
is that the fit targets the log scale while the loss is on the variance scale, which is recorded
rather than hidden, and the levels variant is not run at all rather than run with a floor.

**Every trial is counted**, in an append-only ledger keyed by the git sha. A harness that reports
a winner without saying how many candidates it considered is the thing this project refuses.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from volab.config import Settings
from volab.errors import SchemaError
from volab.features.build import build_features, log_returns
from volab.features.targets import RETURN_COLUMN, target_column, targets_dataset
from volab.panel import KEYS, as_of_dates, load_curated_panel, truncate
from volab.paths import Tier
from volab.sessions import get_calendar, sessions_between
from volab.store import load_panel

# The HAR regressors, in the order the design matrix carries them. The intercept is prepended.
HAR_REGRESSORS: tuple[str, ...] = ("har_d_proxy", "har_w_proxy", "har_m_proxy")

# Where the trial ledger lives. Tracked in git by an explicit ``.gitignore`` exception, like
# ``data/holdout_access.jsonl``: a count of trials that ``git clean -xdf`` erases cannot be the
# number a write-up reports.
TRIAL_LEDGER = Path("data") / "trials.jsonl"


# ---------------------------------------------------------------------------
# Linear algebra, in pure Python. numpy is a transitive dependency of pandas and is absent from
# pyproject.toml, so importing it is forbidden here as everywhere else in this package.
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

    # The singularity threshold is **relative**, scaled by the largest magnitude anywhere in the
    # system. An absolute one would be a latent refusal of perfectly good windows here: the HAR
    # regressors are squared daily returns, order 1e-8, so their cross-products are order 1e-16
    # and a Gram entry can sit below any fixed epsilon while the system is perfectly well
    # conditioned. Found by a test, before it could reject a real fit.
    scale = max((abs(value) for row in rows for value in row), default=0.0)
    tolerance = 1e-12 * scale if scale > 0 else 1e-300

    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(rows[index][column]))
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
    """A fitted linear model: an intercept, one coefficient per regressor, and the fit's size."""

    intercept: float
    coefficients: tuple[float, ...]
    regressors: tuple[str, ...]
    observations: int

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
    return Fit(
        intercept=solved[0],
        coefficients=tuple(solved[1:]),
        regressors=tuple(regressors),
        observations=len(design),
    )


# ---------------------------------------------------------------------------
# Losses.
# ---------------------------------------------------------------------------


def qlike(realized: float, forecast: float) -> float:
    """QLIKE on the variance scale: ``log(f) + r / f``.

    Requires a strictly positive forecast, which log-HAR gives by construction. Raising rather
    than returning NaN on a non-positive one is deliberate: a loss that silently goes missing is
    how a model with an undefined forecast comes to look like a model with no observations.
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
class PanelAudit:
    """What every ``build_features`` call was handed, so the claim can be checked not asserted."""

    calls: list[tuple[dt.date, dt.date]] = field(default_factory=list)

    def record(self, through: dt.date, last_as_of: dt.date) -> None:
        self.calls.append((through, last_as_of))

    def violations(self) -> list[tuple[dt.date, dt.date]]:
        return [(through, last) for through, last in self.calls if last > through]


def features_at_origins(
    panel: pd.DataFrame,
    origins: Sequence[dt.date],
    calendar: xcals.ExchangeCalendar,
    *,
    audit: PanelAudit | None = None,
) -> pd.DataFrame:
    """One feature row per origin, each built from a panel that physically stops at that origin.

    This is the expensive part of the walk-forward and it is not an optimisation target: the
    whole point is that origin ``t``'s row is computed without ``t + 1`` ever being in the frame.
    Measured on the real SPY panel at roughly 0.06s per origin.

    The row kept is the one whose ``as_of`` *is* the origin, and that is asserted rather than
    assumed by taking the last row: the span reaches to ``through`` so such a row always exists,
    but "always" is the kind of claim this project checks.
    """
    rows: list[pd.DataFrame] = []
    for origin in origins:
        stopped = truncate(panel, origin)
        if audit is not None:
            audit.record(origin, max(as_of_dates(stopped)))
        table = build_features(stopped, through=origin, calendar=calendar)
        if table.empty:
            raise SchemaError(f"no feature row at origin {origin}: the truncated panel is empty")
        last = table.iloc[[-1]]
        emitted = as_of_dates(last).iloc[0]
        if emitted != origin:
            raise SchemaError(
                f"the table built through {origin} ends at {emitted}, so the origin has no row "
                "of its own; the session span no longer reaches the cutoff"
            )
        rows.append(last)
    return pd.concat(rows, ignore_index=True)


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


# ---------------------------------------------------------------------------
# The trial ledger.
# ---------------------------------------------------------------------------


def git_sha() -> str:
    """The working tree's commit, or ``"unknown"``. ``--no-optional-locks``, always.

    Phase 1 lost about an hour to ``index.lock`` files that its own audit stamp was creating by
    running ``git status`` without this flag on a 24,218-partition tree. The flag makes the call
    read-only; the lesson was expensive enough to apply everywhere rather than once.
    """
    try:
        finished = subprocess.run(
            ["git", "--no-optional-locks", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return finished.stdout.strip() if finished.returncode == 0 else "unknown"


@dataclass(frozen=True)
class Trial:
    """One evaluated configuration. Every field is part of what makes it a distinct trial."""

    model: str
    symbol: str
    horizon: int
    loss: str
    origins: int
    value: float
    # Origins attempted but not scored for this loss, because the loss was undefined there for
    # this model or for another one. Carried on every trial so a comparison can never be read as
    # like-for-like when it is not.
    dropped: int
    first_origin: dt.date
    last_origin: dt.date

    def record(self) -> dict[str, object]:
        return {
            "model": self.model,
            "symbol": self.symbol,
            "horizon": self.horizon,
            "loss": self.loss,
            "origins": self.origins,
            "value": self.value,
            "dropped": self.dropped,
            "first_origin": self.first_origin.isoformat(),
            "last_origin": self.last_origin.isoformat(),
        }


def append_trials(trials: Sequence[Trial], *, root: Path, at: dt.datetime, sha: str) -> int:
    """Append one line per trial and return how many were written. Append-only, never rewritten.

    The count is returned rather than inferred from the file, so a caller can assert that a run
    of N configurations wrote exactly N lines -- which is the only way "every trial is counted"
    is a fact about the code rather than an intention.
    """
    path = root / TRIAL_LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for trial in trials:
            handle.write(
                json.dumps({"at": at.isoformat(), "git_sha": sha, **trial.record()}) + "\n"
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

MODELS: tuple[str, ...] = ("har_log", "random_walk")


def _har_log_forecast(training: pd.DataFrame, row: pd.Series, response: str) -> tuple[float, int]:
    """Fit log-HAR on the training rows and forecast the origin's variance.

    The response is ``log`` of the target, so a non-positive target has no log and its row is
    dropped; ``exp`` of the prediction is then strictly positive and QLIKE is always defined.
    """
    usable = training[training[response] > 0].copy()
    usable["_log_response"] = [math.log(float(value)) for value in usable[response]]
    fit = fit_ols(usable, HAR_REGRESSORS, "_log_response")
    predicted = fit.predict(row)
    if pd.isna(predicted):
        return float("nan"), fit.observations
    # Clipped only against overflow, not against being small: exp(700) is near the float64
    # ceiling, and a forecast that large is a diverged fit which the loss should see as such
    # rather than as an inf that poisons the mean.
    return math.exp(min(predicted, 700.0)), fit.observations


def _random_walk_forecast(row: pd.Series, horizon: int) -> float:
    """The benchmark: the last observed daily variance proxy, scaled to the horizon.

    Not a strawman and not a contender -- it is the thing a forecast has to beat before anyone
    should look at it, and it is counted as a trial like everything else.
    """
    value = row["rv_proxy_d1"]
    return float("nan") if pd.isna(value) else float(value) * horizon


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
            for model in MODELS
        ]
        return set.intersection(*sets) if sets else set()

    def trials(self) -> list[Trial]:
        """One trial per model and loss. Every configuration evaluated, counted."""
        out: list[Trial] = []
        if not self.results:
            return out
        attempted = {result.origin for result in self.results}
        for name, loss in LOSSES.items():
            usable = self.comparable(name)
            dropped = len(attempted - usable)
            for model in MODELS:
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
                        first_origin=self.results[0].origin,
                        last_origin=self.results[-1].origin,
                    )
                )
        return out


def evaluate(
    panel: pd.DataFrame,
    targets: pd.DataFrame,
    origins: Sequence[dt.date],
    *,
    symbol: str,
    horizon: int,
    calendar: xcals.ExchangeCalendar,
    minimum_training: int = 250,
    audit: PanelAudit | None = None,
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
    """
    column = target_column(horizon)
    target_sessions = {
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
        row = table.iloc[-1]
        rows.append(table.iloc[[-1]])

        # The training set: feature rows whose target had completed by the origin. ``truncate``
        # on the targets is the store's own filter, applied to the frame it already returned.
        knowable = truncate(targets, origin)
        bases = {stamp.date() for stamp in pd.to_datetime(knowable["ts"], utc=True)}
        usable = table[[day in bases for day in emitted]].copy()
        usable[column] = [target_sessions[day] for day in as_of_dates(usable)]

        forecasts: dict[str, float] = {}
        observations = 0
        if len(usable.dropna(subset=[*HAR_REGRESSORS, column])) < minimum_training:
            skipped.append((origin, f"only {len(usable)} knowable training row(s)"))
            continue
        # An origin whose own regressors are null has no forecast, and that is skipped rather
        # than scored as NaN. The case is real, not hypothetical: a session within 22 of a
        # missing bar has a null monthly component, because an incomplete window is null here and
        # never partially filled. Scoring it as a NaN would quietly shrink the denominator of
        # every loss while the origin still appeared to have been evaluated.
        null_regressors = [name for name in HAR_REGRESSORS if pd.isna(row[name])]
        if null_regressors:
            skipped.append((origin, f"null regressor(s) at the origin: {null_regressors}"))
            continue
        try:
            forecasts["har_log"], observations = _har_log_forecast(usable, row, column)
        except SchemaError as refused:
            skipped.append((origin, f"har_log refused the window: {refused}"))
            continue
        forecasts["random_walk"] = _random_walk_forecast(row, horizon)

        results.append(
            OriginResult(
                origin=origin,
                realized=target_sessions.get(origin, float("nan")),
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
