"""The HAR with implied variance: the HAR regressors plus the matched implied variance (P3-03).

Realized variance over the next ``h`` sessions is regressed on the three HAR components and on the
variance the options market implies for the same ``h`` sessions: ``iv_var_h1`` from VIX1D at h=1,
``iv_var_h5`` from VIX9D at h=5, ``iv_var_h22`` from VIX at h=22. It exists to answer whether the
market's own forecast adds anything to a HAR, so it is in the shootout to be tested and not to
make up the numbers.

**The column is not rescaled.** ``volab.features.build`` writes ``iv_var_h{h}`` as
``(iv / 100) ** 2 * (h / 252)``, which is already the target's scale -- annualised percent turned
into the variance accrued over ``h`` sessions. The regression finds its own coefficient on it;
nothing here converts units. Witness:
``test_the_forecast_is_the_ols_on_the_har_regressors_plus_the_matched_column``.

**It cannot answer h=66.** ``IV_SERIES`` has no three-month entry, so the domain says
``max_horizon=22``; a cohort holding this contestant is refused at h=66 by ``Cohort.refusal``
rather than having the contestant dropped from it.

**A null or degenerate column is a refusal, not a fit around it.** Null at the origin, too thin in
the training window, or singular only because of the column (constant, or collinear with the HAR
regressors, where the HAR alone would fit), the contestant raises ``ModelSkip`` naming the column
(R87): recorded against this contestant, not filed as an unfittable window for the whole cohort.
Conformance clause 10 nulls the declared column and demands this of the first two. A window the
HAR regressors cannot fit either stays the window's ``SchemaError``. There is no fallback to the
plain HAR -- that would make the contestant become the baseline while still being reported as
implied-variance.

The regression is ``volab.walkforward.fit_ols``, reached through the module attribute for the
reason ``volab.models.recalibrated`` gives, and no numpy, scipy or arch is imported.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import volab.walkforward as wf
from volab.errors import ModelSkip, SchemaError
from volab.models.base import Domain, Forecast, Forecaster, TrainingView

# The horizons with an implied-variance maturity (``IV_SERIES``: VIX1D, VIX9D, VIX) and the column
# each reads. Spelled out here, not derived, because the Domain is a declaration the conformance
# suite checks against the view's real columns.
DOMAIN = Domain(
    max_horizon=22,
    per_horizon=((1, "iv_var_h1"), (5, "iv_var_h5"), (22, "iv_var_h22")),
)


class _IvForecaster:
    """The fitted regression. ``forecast`` only checks it is asked what it was fitted for."""

    def __init__(self, value: float, observations: int, horizon: int) -> None:
        self.observations = observations
        self._value = value
        self._horizon = horizon

    def forecast(self, horizon: int) -> Forecast:
        # ``ValueError`` and not ``SchemaError``: ``evaluate`` files the second under "the window
        # was unfittable", so a wiring bug would pass as a data problem (see ``Forecaster``).
        if horizon != self._horizon:
            raise ValueError(
                f"fitted for horizon {self._horizon}, asked to forecast horizon {horizon}"
            )
        return Forecast(self._value, self.observations)


@dataclass(frozen=True)
class HarRvIv:
    # Read-only properties, for the reason given on ``Forecaster.observations``: a settable
    # Protocol member would reject a frozen dataclass under ``mypy --strict``.
    @property
    def name(self) -> str:
        return "har_rv_iv"

    @property
    def fit_depends_on_horizon(self) -> bool:
        return True

    @property
    def domain(self) -> Domain:
        return DOMAIN

    def fit(self, view: TrainingView) -> Forecaster:
        column = f"iv_var_h{view.horizon}"
        # Not a data problem: ``evaluate`` never hands a horizon the domain refuses, so a view
        # without the column is wiring, and a ``SchemaError`` would file it as an unfittable window.
        if column not in view.row.index or column not in view.training.columns:
            raise ValueError(
                f"{self.name} has no {column!r} in the view at h={view.horizon}: it declares a "
                f"column at h={[at for at, _ in DOMAIN.per_horizon]} only"
            )
        if pd.isna(view.row[column]):
            raise ModelSkip(f"{self.name}: {column} is null at the origin {view.origin}")
        regressors = (*wf.HAR_REGRESSORS, column)
        # The rows ``fit_ols`` will actually use, counted the way it drops them.
        carried = len(view.training[[*regressors, view.response]].dropna())
        if carried < view.minimum_training:
            raise ModelSkip(
                f"{self.name}: {carried} training row(s) at {view.origin} carry {column} together "
                f"with the HAR regressors and the response, fewer than the "
                f"{view.minimum_training} required"
            )
        try:
            fit = wf.fit_ols(view.training, regressors, view.response)
        except SchemaError as singular:
            # Only this contestant reads ``column``, so a singular system it alone causes is its
            # refusal: a ``SchemaError`` out of ``fit`` skips the origin for the whole cohort (R87).
            # It is this column's fault only if the HAR regressors alone still fit the window; if
            # they do not, the window is unfittable for everyone and the error is left to say so.
            try:
                wf.fit_ols(view.training, wf.HAR_REGRESSORS, view.response)
            except SchemaError:
                raise singular from None
            raise ModelSkip(
                f"{self.name}: the regression with {column} at {view.origin} is unfittable "
                f"although the HAR regressors alone fit: {singular}"
            ) from singular
        return _IvForecaster(fit.predict(view.row), fit.observations, view.horizon)


def har_rv_iv() -> HarRvIv:
    """The shipped contestant."""
    return HarRvIv()
