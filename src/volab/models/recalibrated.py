"""The recalibrated HAR: a baseline forecast pushed through a Mincer-Zarnowitz regression (R96).

The framework's line 27 is why this contestant exists: a 2026 study found that after
Mincer-Zarnowitz recalibration most of a foundation model's edge over Log-HAR at 1 and 5 days
disappeared. A recalibrated HAR is cheap, strong, and *may absorb an apparent edge*, so it is in
the shootout to be beaten, not to make up the numbers.

Mincer-Zarnowitz regresses realized on forecast, ``y = a + b * f``, and applies the fitted
intercept and slope to the next forecast. **The trap is the sample the regression is fitted on.**
Fit it on the origin being scored and the answer has been leaked into the forecast. This project
has found a forward peek nine times, twice (R72, R78) on the target side of the walk-forward, so
the sample is built so that the leak is not something a condition has to remember to prevent:

* The sample is an **inner walk-forward at the outer stride**. At each inner origin ``c`` the
  forecast is the base model's, fitted on ``training`` rows whose ``target_as_of <= c`` -- a
  further truncation of a frame that is already truncated at the outer origin -- and the realized
  value paired with it is ``c``'s own target, which the outer origin has already seen complete.
* ``view.training`` holds only base sessions whose target had completed by the outer origin. The
  origin's own target completes ``horizon`` sessions *after* it, so the origin's row is **not in
  the frame at all**, and neither is any session after it. The exclusion is the same ``as_of``
  slice the whole harness rests on, not a filter someone remembered to write.
* ``target_as_of`` is on the view for this reason. Without it the model would have to reach back
  into the target frame to learn when each training row became knowable, which is the R78 bypass
  class: the audit would pass and the fit would be reading the future.

Witnesses (``tests/test_recalibrated.py``):

* ``test_an_in_sample_mincer_zarnowitz_scores_better_and_is_refused`` -- the red case.
* ``test_the_calibration_sample_never_contains_a_target_that_completed_after_the_origin`` -- the
  sample, read through the model.
* ``test_every_inner_fit_is_handed_only_what_was_knowable_at_its_own_origin`` and its two plants.
* the a=0, b=1 test, which also witnesses the scaling in ``mincer_zarnowitz`` (its name is 97
  characters and does not fit a bullet):
test_the_recalibrated_forecast_reduces_to_the_base_forecast_when_a_is_zero_and_b_is_one
* ``test_the_inner_forecasts_are_what_the_walk_forward_itself_produces_at_those_origins`` -- the
  inner pairs equal what ``evaluate`` produces at those origins, compared with ``==``.

Two things this module deliberately does *not* do:

* **No fallback.** A sample too thin or a regression singular is a ``ModelSkip`` (R87), recorded
  against this contestant. Quietly returning the base forecast instead would make the contestant
  become the baseline while still being reported as recalibrated. Witness:
  ``test_too_few_calibration_points_is_a_recorded_skip``.
* **No floor and no clamp.** ``a + b * f`` can be non-positive, where QLIKE is undefined; those
  origins leave the comparable set for everyone, exactly as ``har_levels`` does (R54). Nor is the
  slope constrained: a bound is a free parameter chosen to make a number look sensible.

The regression is ``volab.walkforward.fit_ols`` -- the project's own least squares with partial
pivoting (R57) -- reached **through the module attribute**, never a captured reference, because
``tools/measure_calibration.py`` replaces ``wf.fit_ols`` by attribute and a captured one would
silently stop being replaced. Witness:
``test_the_regression_is_reached_through_the_walkforward_module_attribute``. numpy and scipy are
admitted here by R82 and not needed.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from dataclasses import dataclass, field

import pandas as pd

import volab.walkforward as wf
from volab.errors import ModelSkip, SchemaError
from volab.models.base import Forecast, Forecaster, Model, TrainingView
from volab.panel import as_of_dates

# The trailing lattice points the regression looks back over, and the fewest usable pairs it will
# be fitted on. Both are constructor arguments; these are the shipped values, R96's.
#
# 60 and 40 are R96's, not chosen here. The reading of 40 offered in support is this module's own
# gloss and not the design's: two parameters at twenty observations each, below which the slope's
# standard error is wide enough that "recalibrated" would mostly mean "noisier". It is a refusal
# threshold and nothing was searched over. ``minimum_pairs`` is an argument so a test on a short
# panel can lower it without a second code path (the shipped values are exercised by
# ``test_the_shipped_defaults_conform_where_forty_pairs_are_reachable``).
CALIBRATION_POINTS = 60
MINIMUM_PAIRS = 40

SAMPLE_COLUMNS = ("origin", "forecast", "realized", "target_as_of")


def _knowable_at(training: pd.DataFrame, origin: dt.date) -> pd.DataFrame:
    """The training rows whose target had completed by ``origin``: ``target_as_of <= origin``.

    A named function so a test can replace it with the wrong slice -- the whole frame, or one cut
    on the row's own session rather than on when its target completed -- and watch the inner-view
    check notice. R72 is why: a slice re-implemented inside the test that checks it passes with
    the leak planted. Witness: ``test_a_planted_inner_slice_is_caught``.
    """
    return training[[done <= origin for done in training["target_as_of"]]]


def calibration_sample(base: Model, view: TrainingView, points: int) -> pd.DataFrame:
    """The ``(inner origin, forecast, realized, target_as_of)`` pairs the regression is fitted on.

    Inner origins are the sessions ``stride`` apart counting back from the outer origin, taken from
    ``view.returns`` (one entry per session through the origin, missing bars included), so they
    coincide with the origins the outer run itself visits. A lattice point counts only if it has a
    row in ``view.training`` -- its target had completed by the outer origin. Of those the trailing
    ``points`` are used.

    Counting only those, rather than the trailing ``points`` lattice points of which the nearest
    ``horizon / stride`` have no completed target yet, is a choice the design left open and it
    matters: at stride 1 the literal reading leaves ``61 - horizon`` pairs, which is 39 at h=22
    (under the minimum of 40, so the contestant would refuse every origin) and none at h=66.
    Witness: ``test_a_horizon_longer_than_the_stride_does_not_eat_the_sample``.

    Each inner fit is handed a ``TrainingView`` truncated at its own origin: training cut by
    ``_knowable_at``, the row stripped of its response and ``target_as_of``, and the returns
    stopped there. A pair is dropped, never raised, when the inner fit is unfittable or too thin or
    its forecast is null: a ``SchemaError`` escaping from here would skip the origin for the whole
    cohort (R87) over one contestant's inner problem. Witness:
    ``test_an_unfittable_inner_fit_costs_a_pair_and_never_the_origin``.
    """
    training = view.training
    sessions = list(view.returns.index)
    position = {done: index for index, done in enumerate(as_of_dates(training))}
    lattice: list[dt.date] = []
    step = 1
    while len(lattice) < points and len(sessions) - 1 - step * view.stride >= 0:
        inner = sessions[len(sessions) - 1 - step * view.stride]
        step += 1
        if inner in position:
            lattice.append(inner)
    lattice.reverse()

    returns_end = {session: index for index, session in enumerate(sessions)}
    pairs: list[dict[str, object]] = []
    for inner in lattice:
        at = position[inner]
        realized = training[view.response].iloc[at]
        known = _knowable_at(training, inner)
        if pd.isna(realized) or (
            len(known.dropna(subset=[*wf.HAR_REGRESSORS, view.response])) < view.minimum_training
        ):
            continue
        inner_view = dataclasses.replace(
            view,
            origin=inner,
            training=known,
            row=training.iloc[at].drop(labels=[view.response, "target_as_of"]),
            returns=view.returns.iloc[: returns_end[inner] + 1],
        )
        try:
            predicted = base.fit(inner_view).forecast(view.horizon).value
        except (SchemaError, ModelSkip):
            continue
        if not math.isfinite(predicted):
            continue
        pairs.append(
            {
                "origin": inner,
                "forecast": predicted,
                "realized": float(realized),
                "target_as_of": training["target_as_of"].iloc[at],
            }
        )
    return pd.DataFrame(pairs, columns=list(SAMPLE_COLUMNS))


def mincer_zarnowitz(sample: pd.DataFrame) -> tuple[float, float]:
    """``(a, b)`` from regressing ``realized`` on ``forecast`` over ``sample``.

    Fitted in units of the typical forecast. The regression is scale-equivariant -- dividing
    forecast and realized by ``s`` leaves the slope unchanged and divides the intercept by ``s`` --
    and ``solve``'s singularity test is relative per *column*, so a forecast column of order 1e-6
    beside a column of ones can read as collinear when it merely has a small relative spread. That
    happened on the synthetic panel. Scaled, "singular" means collinear and not "in small units".
    Witness: the a=0, b=1 test named in the module docstring
    -- its toy forecasts have a relative spread near 1e-3 at order 1e-6, and the unscaled fit was
    refused as singular there.
    ``SchemaError`` from the solve is left to the caller, which turns it into a ``ModelSkip``.
    """
    scale = float(sample["forecast"].abs().mean())
    scale = scale if scale > 0 else 1.0
    scaled = pd.DataFrame(
        {
            "forecast": sample["forecast"].astype("float64") / scale,
            "realized": sample["realized"].astype("float64") / scale,
        }
    )
    # Through the module attribute, deliberately; see the module docstring.
    fit = wf.fit_ols(scaled, ("forecast",), "realized")
    return fit.intercept * scale, fit.coefficients[0]


@dataclass(frozen=True, eq=False)
class RecalibratedForecaster:
    """The recalibrated answer for one origin, with the sample it was calibrated on kept visible.

    ``calibration`` is there so the property "nothing in the sample postdates the origin" can be
    asserted through the model rather than re-derived beside it (R72). ``eq=False`` because a
    frame inside a dataclass makes ``==`` ambiguous.
    """

    value: float
    observations: int
    horizon: int
    intercept: float
    slope: float
    base_value: float
    calibration: pd.DataFrame = field(repr=False)

    def forecast(self, horizon: int) -> Forecast:
        if horizon != self.horizon:
            raise ValueError(
                f"fitted for horizon {self.horizon}, asked to forecast horizon {horizon}"
            )
        return Forecast(self.value, self.observations)


@dataclass(frozen=True)
class RecalibratedHar:
    """``base`` with its forecast replaced by ``a + b * f``, ``(a, b)`` from the R96 sample."""

    base: Model
    calibration_points: int = CALIBRATION_POINTS
    minimum_pairs: int = MINIMUM_PAIRS

    @property
    def name(self) -> str:
        return f"{self.base.name}_mz"

    # The fit reads the target side: the training targets are the realized half of every pair.
    @property
    def fit_depends_on_horizon(self) -> bool:
        return True

    def fit(self, view: TrainingView) -> Forecaster:
        # The base's own fit, on the origin's full knowable window, is the forecast being
        # recalibrated. Its ``SchemaError`` is the window's, and skips the origin as it would for
        # the base on its own.
        outer = self.base.fit(view).forecast(view.horizon)
        if not math.isfinite(outer.value):
            raise ModelSkip(
                f"{self.name}: the {self.base.name} forecast at {view.origin} is {outer.value!r}, "
                "so there is nothing to recalibrate"
            )
        sample = calibration_sample(self.base, view, self.calibration_points)
        if len(sample) < self.minimum_pairs:
            raise ModelSkip(
                f"{self.name}: {len(sample)} usable calibration pair(s) at {view.origin}, "
                f"fewer than the {self.minimum_pairs} required"
            )
        try:
            intercept, slope = mincer_zarnowitz(sample)
        except SchemaError as singular:
            raise ModelSkip(
                f"{self.name}: the Mincer-Zarnowitz regression over {len(sample)} pair(s) at "
                f"{view.origin} is unfittable: {singular}"
            ) from singular
        return RecalibratedForecaster(
            value=intercept + slope * outer.value,
            observations=outer.observations,
            horizon=view.horizon,
            intercept=intercept,
            slope=slope,
            base_value=outer.value,
            calibration=sample,
        )


def har_levels_mz() -> RecalibratedHar:
    """The shipped contestant: ``har_levels`` recalibrated. ``har_semilog_mz`` is one argument away
    (``RecalibratedHar(<the semilog model>)``) and is not on the roster, to keep the trial count
    honest."""
    return RecalibratedHar(wf.BASELINE_COHORT.models[0])
