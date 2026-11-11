"""The HAR with a declared subset of the feature vocabulary: HAR-X (P5-B1).

Realized variance over the next ``h`` sessions is regressed on the three HAR components and on the
extra feature columns the contestant was constructed with, in the order given. It is the
framework's HAR-X row, and it exists so the Phase 5 research agent has something to propose: the
agent chooses the extra columns from a closed vocabulary and this contestant fits whatever it
chose. It is in the shootout to be tested against the plain HAR, not to make up the numbers.

**The name is ``"harx+" + "+".join(extra)``** -- ``harx+iv_h5+vvix`` -- and not ``harx[iv_h5,vvix]``
(R196). ``volab.paths.validate_segment`` rejects glob metacharacters and ``[`` and ``]`` are among
them, so a bracketed name would be unusable the moment a model name reached a path segment.

**A refusal is a ``ModelSkip``, never a ``SchemaError``.** A ``SchemaError`` says the *window* is
unfittable and deletes that origin for every contestant in the cohort, filing a model problem as a
data problem. ``ModelSkip`` is one contestant's own refusal: the harness records
``(origin, model, reason)``, writes a NaN for it and leaves the origin in place for everyone else
(R87). The three refusals, each naming the offending column(s):

* an extra is null at the origin. Only the extras are checked there: a null HAR column at the
  origin is not tested by this module and is handled downstream, by the training-row count or by
  ``fit_ols``, as in ``volab.models.iv``;
* too few training rows carry every extra together with the HAR regressors and the response, fewer
  than ``view.minimum_training``. The reason names the extras that are individually thin (each
  counted beside the HAR regressors and the response); when none is thin alone and only their
  overlap is, it names all of them and says so. A sparse extra is normal rather than exceptional
  (``iv_h1`` is non-null on 912 of 2,312 development rows), so this is expected to fire;
* the regression is singular and the HAR regressors alone would have fitted the same window. A
  singularity is then a property of the proposal and not of the window. ``fit_ols`` raises
  ``SchemaError`` for it; that is caught here and re-raised as ``ModelSkip`` with the original as
  ``__cause__``. If the HAR regressors alone do not fit either, the window is unfittable for
  everyone and the original ``SchemaError`` is left to say so.

The singular case is real. ``har_d_proxy`` *is* ``rv_proxy_d1`` exactly, and ``har_w_proxy * 5`` and
``har_m_proxy * 22`` equal ``rv_proxy_d5`` and ``rv_proxy_d22`` to rounding, so those three columns
beside the HAR base are exactly singular designs. Keeping them out of the vocabulary is another
story's job; this contestant's contract is only that being handed one is a skip.

There is no fallback to the plain HAR on a refusal: that would make the contestant become the
baseline while still being reported under its own name.

**It answers every horizon.** The domain requires the HAR columns and the extras at every horizon
and sets no ``max_horizon``. Whether an extra exists at a horizon is the vocabulary's concern: a
view without a declared extra is a wiring error here and raises ``ValueError``, not ``SchemaError``
(see ``Forecaster``).

The regression is ``volab.walkforward.fit_ols``, reached through the module attribute for the
reason ``volab.models.recalibrated`` gives, and no numpy, scipy or arch is imported (R82). It is
pure-Python normal equations because the sealed path may not import numpy, so the cost of a fit
grows steeply with the parameter count, and a development window of a couple of thousand rows
cannot support many regressors anyway. No timing is quoted here.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import volab.walkforward as wf
from volab.errors import ModelSkip, SchemaError
from volab.features.build import HAR_COLUMNS
from volab.models.base import Domain, Forecast, Forecaster, TrainingView

NAME = "harx"


class _HarXForecaster:
    """The fitted regression. ``forecast`` only checks it is asked what it was fitted for."""

    def __init__(self, value: float, observations: int, horizon: int) -> None:
        self.observations = observations
        self._value = value
        self._horizon = horizon

    def forecast(self, horizon: int) -> Forecast:
        # ``ValueError`` and not ``SchemaError``, for the reason ``Forecaster`` gives.
        if horizon != self._horizon:
            raise ValueError(
                f"fitted for horizon {self._horizon}, asked to forecast horizon {horizon}"
            )
        return Forecast(self._value, self.observations)


@dataclass(frozen=True)
class HarX:
    """HAR plus ``extra``, the declared extra regressors in the order they enter the fit."""

    extra: tuple[str, ...]

    # Read-only properties, for the reason given on ``Forecaster.observations``.
    @property
    def name(self) -> str:
        return "harx+" + "+".join(self.extra)

    @property
    def fit_depends_on_horizon(self) -> bool:
        return True

    @property
    def domain(self) -> Domain:
        return Domain(requires=(*HAR_COLUMNS, *self.extra))

    def fit(self, view: TrainingView) -> Forecaster:
        missing = [
            column
            for column in self.extra
            if column not in view.row.index or column not in view.training.columns
        ]
        # Not a data problem: a view without a declared column is wiring, and a ``SchemaError``
        # would file it as an unfittable window.
        if missing:
            raise ValueError(f"{self.name} has no {missing[0]!r} in the view at h={view.horizon}")
        named = ", ".join(repr(column) for column in self.extra)
        null = [column for column in self.extra if pd.isna(view.row[column])]
        if null:
            raise ModelSkip(
                f"{self.name}: {', '.join(repr(column) for column in null)} null at the origin "
                f"{view.origin}"
            )
        # The rows ``fit_ols`` will actually use, by the harness's own definition of enough rows.
        carried = wf.training_rows(view.training, view.response, self.extra)
        if carried < view.minimum_training:
            thin = [
                column
                for column in self.extra
                if wf.training_rows(view.training, view.response, (column,)) < view.minimum_training
            ]
            which = (
                ", ".join(repr(column) for column in thin) + " is thin on its own"
                if thin
                else f"{named} are not jointly carried; none is thin on its own"
            )
            raise ModelSkip(
                f"{self.name}: {carried} training row(s) at {view.origin} carry every extra "
                f"together with the HAR regressors and the response, fewer than the "
                f"{view.minimum_training} required; {which}"
            )
        try:
            fit = wf.fit_ols(view.training, (*wf.HAR_REGRESSORS, *self.extra), view.response)
        except SchemaError as singular:
            # Only this contestant reads ``extra``, so a singular system it alone causes is its
            # refusal. It is the proposal's fault only if the HAR regressors alone still fit the
            # window; if they do not, the window is unfittable for everyone and the error stands.
            try:
                wf.fit_ols(view.training, wf.HAR_REGRESSORS, view.response)
            except SchemaError:
                raise singular from None
            raise ModelSkip(
                f"{self.name}: the regression with {named} at {view.origin} is unfittable "
                f"although the HAR regressors alone fit: {singular}"
            ) from singular
        return _HarXForecaster(fit.predict(view.row), fit.observations, view.horizon)


def harx(extra: tuple[str, ...]) -> HarX:
    """A contestant that fits the HAR regressors plus ``extra``; ``extra`` must not be empty.

    Empty is refused because it would be the plain HAR under the name ``harx+``, the baseline
    reported as a proposal.
    """
    if not extra:
        raise ValueError("harx needs at least one extra regressor")
    return HarX(extra=tuple(extra))
