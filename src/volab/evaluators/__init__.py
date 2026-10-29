"""Decision evaluators: a variance forecast turned into a (VaR, ES) pair and scored as one.

The rest of the repository scores *forecasts* with QLIKE and squared error. This package scores
the *decision* a forecast licenses: the Gaussian Value-at-Risk and Expected Shortfall it implies,
judged by the Fissler-Ziegel joint loss. That loss is strictly consistent for the pair -- its
expected value is uniquely minimised by the true VaR and ES -- and that is what permits handing it
to ``volab.compare.analyse`` as a ``LossSeries``: Diebold-Mariano, the model confidence set and SPA
then say which forecaster makes the better decisions, not merely the better variance.

**Nothing here is registered in ``walkforward.LOSSES``, deliberately.** ``Evaluation.trials`` and
the driver loop iterate that dict, so an entry would write another trial line per contestant per
configuration on every future run, and ``count_trials`` -- whose line count the holdout
pre-registration pins -- would move. ``decision_series`` builds the ``LossSeries`` by hand instead
and gets the statistics without moving that number.

**The signed return is a scoring read.** ``OriginResult.realized`` is a realized *variance*; a
breach needs the signed h-day log return, which nothing in an ``Evaluation`` or a ``TrainingView``
carries. ``horizon_returns`` reads it from the store, after the forecast exists, to ``dev_end``
and no further. That is the same standing as ``realized`` itself: it is what a forecast is judged
against and is never what one is fitted on. The tests demonstrate this rather than assert it
(``tests/test_evaluators.py``).

``coverage`` tests the breaches themselves (Kupiec, Christoffersen) and **refuses** where the
origins' stride is shorter than the horizon, since their overlapping returns break the independence
both tests assume; ``coverage``'s docstring says why a HAC correction was not used instead.

``vol_target`` sizes a volatility-targeted position from the same forecasts and reports its P&L.
Close-to-close returns ignore distributions, so that P&L is wrong on ex-dates; the result carries
the ``has_distribution`` count beside it as a disclosure, not a correction.

This package is off the sealed point-in-time path and may import scipy: nothing sealed imports it,
and ``tests/test_import_discipline.py`` would fail if a sealed module reached it.

Conventions. VaR and ES are **positive magnitudes of the loss-side quantile**, and a breach is a
return below ``-var``. The forecast is of the variance of the h-day return, and the return
distribution is taken to be zero-mean Gaussian -- a stated simplification, not a finding: the
decision evaluators compare forecasters under one fixed distributional assumption.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd
from scipy.stats import chi2, norm

from volab.compare.series import LossSeries
from volab.config import Settings
from volab.features.build import CLOSE_COLUMN, SESSIONS_PER_YEAR
from volab.ingest.curate import BARS_DATASET
from volab.panel import KEYS, as_of_dates
from volab.sessions import get_calendar, sessions_between
from volab.store import load_panel
from volab.walkforward import Evaluation

# The tail levels the decision tasks score at.
ALPHAS: tuple[float, ...] = (0.01, 0.025)


def _check_alpha(alpha: float) -> None:
    # ``not (0 < alpha < 1)`` rather than ``alpha <= 0 or alpha >= 1``: every comparison with nan
    # is False, so the second spelling would wave a nan level through.
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie in the open interval (0, 1), got {alpha}")


def var_es(variance: float, alpha: float) -> tuple[float, float]:
    """The zero-mean Gaussian ``(VaR, ES)`` at tail level ``alpha``, as positive magnitudes.

    With ``sigma = sqrt(variance)`` and ``z = -Phi^-1(alpha)``: ``VaR = z * sigma`` and
    ``ES = phi(z) / alpha * sigma``, where ``phi`` is the standard normal density. ``ES > VaR``
    always, and both scale with ``sigma``.

    Refuses a variance that is not strictly positive and finite rather than returning a ``nan`` (a
    square root of a negative) or an ``inf``: the volatility-target task divides by the result.
    """
    _check_alpha(alpha)
    # ``not (0 < v < inf)`` so that nan is refused too; ``v <= 0`` is False for it.
    if not 0.0 < variance < math.inf:
        raise ValueError(f"variance must be positive and finite, got {variance}")
    z = -float(norm.ppf(alpha))
    sigma = math.sqrt(variance)
    return z * sigma, float(norm.pdf(z)) / alpha * sigma


def fz0(realized_return: float, var: float, es: float, alpha: float) -> float:
    """The Fissler-Ziegel zero-homogeneous joint loss of a ``(VaR, ES)`` pair; lower is better.

    ``var`` and ``es`` are positive magnitudes with ``es > var``, and a breach is
    ``realized_return < -var``. The loss is

        1{y < -var} * (-var - y) / (alpha * es)  +  var / es  +  log(es)  -  1.

    **Source.** The FZ0 loss of Patton, Ziegel and Chen (2019), "Dynamic semiparametric models for
    expected shortfall (and Value-at-Risk)", Journal of Econometrics 211(2): in terms of the
    *signed* quantiles ``v = -var`` and ``e = -es`` (both negative),

        S(v, e, y; alpha) = -1/(alpha e) * 1{y <= v} * (v - y) + v/e + log(-e) - 1,

    the member of the Fissler-Ziegel (2016) family that is homogeneous of degree zero.
    Substituting ``v = -var`` and ``e = -es`` gives the expression above. The test module carries
    the signed form separately, so a sign slip in either cannot cancel.

    At the true VaR the expected penalty is ``alpha * (ES - VaR) / (alpha * es)``, so the expected
    loss is ``ES / es + log(es) - 1``, which is minimised uniquely at ``es = ES`` with value
    ``log(ES)``.

    Refuses a non-finite return -- ``nan < -var`` is False, so it would otherwise score as a clean
    miss -- a non-positive ``var``, and an ``es`` that is not strictly greater than ``var``.
    """
    _check_alpha(alpha)
    if not math.isfinite(realized_return):
        raise ValueError(f"realized_return must be finite, got {realized_return}")
    if not 0.0 < var < math.inf:
        raise ValueError(f"var must be a positive magnitude, got {var}")
    if not es > var:
        raise ValueError(f"es must be more extreme than var (es > var > 0), got es={es} var={var}")
    penalty = (-var - realized_return) / (alpha * es) if realized_return < -var else 0.0
    return penalty + var / es + math.log(es) - 1.0


def horizon_returns(
    settings: Settings, symbol: str, origins: Iterable[dt.date], *, horizon: int
) -> dict[dt.date, float]:
    """``log(close_{t+h} / close_t)`` for each origin ``t``, with ``h = horizon`` sessions.

    A scoring read. It goes through ``load_panel`` with ``end=settings.splits.dev_end`` -- the only
    read path, on which ``end`` is required -- so it sees no session past the seal, and an origin
    whose ``t + h`` session lies past ``dev_end`` is **omitted** rather than given a value. An
    origin is omitted too when it is not a session or when either of its two closes is missing (no
    bar, or a null close): a gap is not bridged. Sessions are the exchange calendar's, as in the
    targets.

    The result is for scoring decisions that already exist. Nothing here is handed to a model;
    ``tests/test_evaluators.py::test_no_horizon_return_reaches_a_fit`` is the witness.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be at least 1, got {horizon}")
    end = settings.splits.dev_end
    bars = load_panel(
        BARS_DATASET,
        end=end,
        symbols=[symbol],
        columns=(*KEYS, CLOSE_COLUMN),
        settings=settings,
    )
    days = list(as_of_dates(bars))
    sessions = sessions_between(get_calendar(settings), min(days), end)
    # Reindexed onto the calendar so a session with no bar is an explicit NaN, not an absent row
    # that the position arithmetic below would silently step over.
    close = pd.Series(bars[CLOSE_COLUMN].astype("float64").to_list(), index=days).reindex(sessions)
    position = {day: index for index, day in enumerate(sessions)}

    found: dict[dt.date, float] = {}
    for origin in origins:
        start = position.get(origin)
        if start is None or start + horizon >= len(sessions):
            continue
        opening, closing = float(close.iloc[start]), float(close.iloc[start + horizon])
        if math.isnan(opening) or math.isnan(closing):
            continue
        found[origin] = math.log(closing / opening)
    return found


def decision_series(
    evaluation: Evaluation, *, alpha: float, cohort: str, returns: Mapping[dt.date, float]
) -> LossSeries:
    """The FZ0 loss of every contestant's Gaussian ``(VaR, ES)`` at every comparable origin.

    Origins are ``evaluation.comparable("qlike")`` -- the set the QLIKE trial beside it is computed
    over, on which every contestant has a strictly positive variance forecast, so ``var_es`` is
    defined there unless a forecast is ``inf``, which it refuses (QLIKE's domain admits it) -- so a
    decision comparison and a forecast comparison rest on the same origins. ``symbol``,
    ``horizon`` and ``stride`` are the evaluation's.

    ``returns`` maps origin to the signed horizon return (``horizon_returns``). A comparable origin
    with no entry there is **refused**, not dropped: the series would silently rest on fewer origins
    than the trial beside it, which is the silent-denominator defect again. Entries for origins
    outside the comparable set are ignored. A forecast ``var_es`` refuses (``inf``) is re-raised
    naming the contestant and origin.

    The loss is named ``fz0_a<alpha>`` and is **not** in ``walkforward.LOSSES``, so building and
    analysing this series writes no trial line and moves no ledger count.
    """
    _check_alpha(alpha)
    if cohort != evaluation.cohort:
        raise ValueError(
            f"cohort {cohort!r} was asked for but the evaluation ran cohort {evaluation.cohort!r}"
        )
    usable = evaluation.comparable("qlike")
    absent = sorted(usable - returns.keys())
    if absent:
        raise ValueError(
            f"no horizon return for {len(absent)} comparable origin(s), first {absent[0]}: "
            "the series would rest on fewer origins than the trial beside it; pass "
            "horizon_returns(..., origins=[r.origin for r in evaluation.results])"
        )
    origins = tuple(sorted(usable))
    values: dict[str, tuple[float, ...]] = {}

    def loss(model: str, origin: dt.date, forecast: float) -> float:
        # ``inf`` is inside QLIKE's domain and so in ``usable``, but ``var_es`` refuses it. Name
        # the contestant and origin, or a long run dies with no locus.
        try:
            return fz0(returns[origin], *var_es(forecast, alpha), alpha)
        except ValueError as refusal:
            raise ValueError(f"contestant {model!r} at origin {origin}: {refusal}") from refusal

    for model in evaluation.contestants:
        by_origin = {
            origin: loss(model, origin, forecast)
            for origin, _, forecast in evaluation.scored(model)
            if origin in usable
        }
        values[model] = tuple(by_origin[origin] for origin in origins)
    return LossSeries(
        symbol=evaluation.symbol,
        horizon=evaluation.horizon,
        loss=f"fz0_a{alpha}",
        cohort=cohort,
        stride=evaluation.every,
        origins=origins,
        values=values,
    )


@dataclass(frozen=True)
class CoverageResult:
    """What ``coverage`` found: the count, what was expected, and the two p-values.

    ``kupiec_p`` and ``christoffersen_p`` are ``None`` exactly when ``refusal`` is a reason; the
    counts are reported either way.
    """

    breaches: int
    expected: float
    kupiec_p: float | None
    christoffersen_p: float | None
    refusal: str | None


def _log_likelihood_term(count: int, probability: float) -> float:
    """``count * log(probability)``, with the limit ``0 * log(0) = 0`` taken and not evaluated.

    A maximised binomial or Markov likelihood has a zero probability exactly where its count is
    zero -- no breach at all, none after a breach, and so on -- and ``log(0)`` is then neither
    needed nor evaluated. A positive count with a zero probability cannot arise from estimated
    probabilities, and ``math.log`` raises on it rather than returning ``-inf``.
    """
    return 0.0 if count == 0 else count * math.log(probability)


def _chi_squared_p(statistic: float) -> float:
    # A statistic that is zero analytically can come out a rounding error below it; the survival
    # function is 1.0 for any negative argument, which is the right p-value for it.
    return float(chi2.sf(statistic, 1))


def coverage(hits: Sequence[bool], alpha: float, *, horizon: int, stride: int) -> CoverageResult:
    """Kupiec's unconditional-coverage test and Christoffersen's independence test of breaches.

    ``hits[i]`` is whether origin ``i`` breached its VaR (``return < -var``), in origin order, for
    a VaR at tail level ``alpha``. Both tests are likelihood ratios against chi-squared with one
    degree of freedom (``scipy.stats.chi2.sf``):

    * **Kupiec** (1995): the breach count is binomial with probability ``alpha``. Zero breaches is
      ordinary -- the expectation is 2.5 over 251 origins at ``alpha = 0.01`` -- and the statistic
      is defined there; so it is when every origin breaches.
    * **Christoffersen** (1998), independence only: the indicator is a first-order Markov chain
      whose breach probability does not depend on the previous origin's outcome. Zero or all
      breaches leave nothing to compare, the statistic is zero and ``christoffersen_p`` is ``1.0``:
      the test could not reject, which is not evidence that breaches are independent.

    **Both assume independent indicators, so an overlapping window is refused.** An h-day return
    shares ``h - stride`` sessions with the next origin's when ``stride < horizon``, so successive
    indicators are dependent by construction -- at stride 5 and h=22 each overlaps the next three
    -- and the tests' p-values would be reported against a null they do not satisfy. Where
    ``stride < horizon`` the result carries a ``refusal`` naming that condition, and no p-value.
    A HAC correction was considered and set aside: its own validity at these sample sizes would
    need evidence of its own. A refusal also covers fewer than two origins, as the independence
    test counts transitions between them.

    ``expected`` is ``alpha * len(hits)``. The counts are in the result whether or not it refuses.
    """
    _check_alpha(alpha)
    if horizon < 1:
        raise ValueError(f"horizon must be at least 1, got {horizon}")
    if stride < 1:
        raise ValueError(f"stride must be at least 1, got {stride}")
    flags = [bool(hit) for hit in hits]
    count, breaches = len(flags), sum(flags)
    expected = alpha * count

    def refused(reason: str) -> CoverageResult:
        return CoverageResult(breaches, expected, None, None, reason)

    if stride < horizon:
        return refused(
            f"the breach indicators overlap: stride {stride} < horizon {horizon}, so each "
            f"{horizon}-session return shares sessions with the next origin's, and Kupiec's and "
            "Christoffersen's tests assume independent indicators; use stride >= horizon"
        )
    if count < 2:
        return refused(f"fewer than two origins ({count}): there is no transition to count")

    # Kupiec: the binomial likelihood at alpha against its maximum at x / n.
    pi = breaches / count
    kupiec = -2.0 * (
        _log_likelihood_term(count - breaches, 1.0 - alpha)
        + _log_likelihood_term(breaches, alpha)
        - _log_likelihood_term(count - breaches, 1.0 - pi)
        - _log_likelihood_term(breaches, pi)
    )

    # Christoffersen: transitions between consecutive origins, ``n[before][now]``.
    n = [[0, 0], [0, 0]]
    for before, now in zip(flags, flags[1:], strict=False):
        n[before][now] += 1
    from_clear, from_breach = n[0][0] + n[0][1], n[1][0] + n[1][1]
    # A state never left has no estimate, and then every term that would use it has a zero count.
    p01 = n[0][1] / from_clear if from_clear else 0.0
    p11 = n[1][1] / from_breach if from_breach else 0.0
    p = (n[0][1] + n[1][1]) / (from_clear + from_breach)
    independence = -2.0 * (
        _log_likelihood_term(n[0][0] + n[1][0], 1.0 - p)
        + _log_likelihood_term(n[0][1] + n[1][1], p)
        - _log_likelihood_term(n[0][0], 1.0 - p01)
        - _log_likelihood_term(n[0][1], p01)
        - _log_likelihood_term(n[1][0], 1.0 - p11)
        - _log_likelihood_term(n[1][1], p11)
    )
    return CoverageResult(
        breaches, expected, _chi_squared_p(kupiec), _chi_squared_p(independence), None
    )


# Both are committed literals, not configuration: every alternative would be a trial.
#
# ``TARGET_VOL`` is 10% annualised, and it is round because it is round. It is calibrated to
# nothing in this repository: no result here, on any series, suggests ten rather than eight or
# fifteen. The choice sets the scale of the P&L and, through ``target / cap``, where the cap binds.
#
# ``POSITION_CAP`` is a leverage ceiling of two times notional, so a vanishing variance forecast
# cannot ask for an unbounded position. It is an absolute position, not a multiple of the target,
# and it binds wherever the forecast's annualised volatility is below ``target / cap`` -- 5% at
# these defaults (``test_vol_target_cap_binds_below_a_five_percent_annualised_forecast...``).
# That is low for an equity index, so the cap is expected to bind rarely on real data (not measured
# here); it is there for the degenerate forecast, and ``capped_origins`` says how often it fired.
TARGET_VOL = 0.10
POSITION_CAP = 2.0


@dataclass(frozen=True)
class VolTargetResult:
    """The P&L of a volatility-targeted position, and the ex-date count printed beside it.

    ``mean_return`` and ``realized_vol`` are annualised from the horizon; ``sharpe`` is their
    ratio, with no risk-free rate subtracted (none is available here). ``turnover`` is the mean
    absolute change of position between consecutive origins. ``realized_vol``, ``sharpe`` and
    ``turnover`` are ``None`` where they are undefined: with one origin, which has neither a sample
    standard deviation nor a change of position, and ``sharpe`` also where the P&L is constant.
    ``capped_origins`` counts origins whose uncapped position exceeded the cap, and
    ``distribution_sessions`` the ``has_distribution`` sessions the caller passed.
    """

    mean_return: float
    realized_vol: float | None
    sharpe: float | None
    turnover: float | None
    capped_origins: int
    distribution_sessions: int


def vol_target(
    forecasts: Sequence[float],
    returns: Sequence[float],
    *,
    horizon: int,
    target: float = TARGET_VOL,
    cap: float = POSITION_CAP,
    distributions: Sequence[bool],
) -> VolTargetResult:
    """The P&L of sizing a long position at each origin to a target annualised volatility.

    ``forecasts[i]`` is the variance forecast of the ``horizon``-session log return from origin
    ``i`` and ``returns[i]`` is that return, signed (``horizon_returns``). The position is

        min(target / (sqrt(forecast) * sqrt(252 / horizon)), cap)

    -- the target over the forecast's annualised volatility, at most ``cap`` -- and the P&L at the
    origin is the position times the return. It is long-only because ``target`` and ``cap`` are
    refused unless positive. ``turnover`` leaves out the first position's build from flat, and the
    statistics are annualised as if the holdings followed one another (``252 / horizon`` per
    year). **There is no stride argument:** each P&L is one ``horizon``-session holding, and where
    the origins are closer than ``horizon`` the holdings overlap, so these are per-holding
    statistics and not the return of a strategy that compounds.

    **The P&L is wrong on ex-dates, and ``distribution_sessions`` is the printed measure of that.**
    Close-to-close log returns are computed from raw closes, which fall by the dividend on an
    ex-date: a long position's P&L is understated across every holding that contains one. Curated
    bars carry no adjusted close, only ``has_distribution``, and building a total-return series is
    out of scope here. So the count is a **disclosure, not a correction** -- nothing in the P&L
    uses it. ``distributions`` is the ``has_distribution`` flag of each session in the window the
    P&L spans, and the caller supplies it: this function takes no dates and cannot check that the
    flags cover the window. It is the move ``MONTE_CARLO_ERROR_SOURCE`` made before US-3.9b retired
    it: a figure that cannot be fixed here is printed with what it is wrong about.

    ``TARGET_VOL`` is calibrated to nothing in this repository (see its comment). A forecast that
    is not strictly positive and finite is refused rather than turned into an ``inf`` position or
    a ``nan``, and so is a return that is not finite.
    """
    if horizon < 1:
        raise ValueError(f"the forecast horizon must be at least 1 to annualise it, got {horizon}")
    if not 0.0 < target < math.inf:
        raise ValueError(f"target volatility must be positive and finite, got {target}")
    if not 0.0 < cap < math.inf:
        raise ValueError(f"position cap must be positive and finite, got {cap}")
    if len(forecasts) != len(returns):
        raise ValueError(f"{len(forecasts)} forecasts but {len(returns)} returns")
    if not forecasts:
        raise ValueError("vol_target needs at least one origin")

    periods = SESSIONS_PER_YEAR / horizon  # holdings per year
    scale = math.sqrt(periods)
    positions: list[float] = []
    pnl: list[float] = []
    capped = 0
    for index, (variance, realized) in enumerate(zip(forecasts, returns, strict=True)):
        if not 0.0 < variance < math.inf:
            raise ValueError(
                f"variance forecast at position {index} must be positive and finite, got {variance}"
            )
        if not math.isfinite(realized):
            raise ValueError(f"return at position {index} must be finite, got {realized}")
        wanted = target / (math.sqrt(variance) * scale)
        if wanted > cap:
            capped += 1
        positions.append(min(wanted, cap))
        pnl.append(positions[-1] * realized)

    count = len(pnl)
    mean_pnl = math.fsum(pnl) / count
    volatility: float | None = None
    sharpe: float | None = None
    turnover: float | None = None
    if count > 1:
        # Exactly zero for a constant series, which the arithmetic below would only approach.
        spread = (
            0.0
            if min(pnl) == max(pnl)
            else math.sqrt(math.fsum((x - mean_pnl) ** 2 for x in pnl) / (count - 1))
        )
        volatility = spread * scale
        if volatility > 0.0:
            sharpe = mean_pnl * periods / volatility
        steps = [
            abs(after - before) for before, after in zip(positions, positions[1:], strict=False)
        ]
        turnover = math.fsum(steps) / len(steps)
    return VolTargetResult(
        mean_return=mean_pnl * periods,
        realized_vol=volatility,
        sharpe=sharpe,
        turnover=turnover,
        capped_origins=capped,
        distribution_sessions=sum(bool(flag) for flag in distributions),
    )
