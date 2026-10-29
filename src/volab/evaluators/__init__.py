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
from collections.abc import Iterable, Mapping

import pandas as pd
from scipy.stats import norm

from volab.compare.series import LossSeries
from volab.config import Settings
from volab.features.build import CLOSE_COLUMN
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
    outside the comparable set are ignored.

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
            "the series would rest on fewer origins than the trial beside it"
        )
    origins = tuple(sorted(usable))
    values: dict[str, tuple[float, ...]] = {}
    for model in evaluation.contestants:
        by_origin = {
            origin: fz0(returns[origin], *var_es(forecast, alpha), alpha)
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
