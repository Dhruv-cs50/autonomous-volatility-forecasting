"""The recalibrated HAR (US-3.3, R96): a Mincer-Zarnowitz regression that cannot see its origin.

The trap this file is about is fitting the regression on the origin being scored, which leaks the
answer into the forecast. The acceptance criterion is a red case -- a deliberately in-sample fit
scores better and is refused -- and the lesson of R72 and R78 shapes every test here: a test that
re-implements the slice it checks passes with the leak planted. So the checks read what the model
built (``calibration``), what the model handed its base (a recording base), and what ``evaluate``
itself produces at the same origins, and each is shown to fail against a planted defect.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
import pytest
from model_conformance import (
    Toy,
    alone,
    conformance_suite,
    make_toy,
    run_cohort,
    views_of,
)
from test_truncation_equivalence import _synthetic

import volab.models.recalibrated as recalibrated
import volab.walkforward as wf
from volab.errors import ModelSkip, SchemaError
from volab.models.base import Cohort, Forecast, Forecaster, Model, TrainingView
from volab.models.recalibrated import (
    CALIBRATION_POINTS,
    MINIMUM_PAIRS,
    RecalibratedForecaster,
    RecalibratedHar,
    calibration_sample,
    har_levels_mz,
    mincer_zarnowitz,
)
from volab.panel import as_of_dates
from volab.sessions import get_calendar

BASE = wf.BASELINE_COHORT.models[0]
SEMILOG = wf.BASELINE_COHORT.models[1]
# The stride-6 toy panel is 143 sessions long and its feature rows begin late, so a late origin has
# 4 to 12 usable pairs, never 40. A model with a lowered floor is used there and only there; the
# shipped 60 and 40 are exercised on ``late_toy`` below.
TOY_FLOOR = 4


def short_model() -> RecalibratedHar:
    return RecalibratedHar(BASE, minimum_pairs=TOY_FLOOR)


def late_toy(horizon: int = 1) -> Toy:
    """The toy at stride 1 over its last few origins, where 40 pairs are reachable at h=1."""
    toy = make_toy(horizon)
    sessions = _synthetic(get_calendar(toy.settings)).sessions
    last = len(sessions) - (horizon + 2)
    return dataclasses.replace(toy, stride=1, origins=tuple(sessions[last - 3 : last + 1]))


def fit_recalibrated(model: Model, view: TrainingView) -> RecalibratedForecaster:
    fitted = model.fit(view)
    assert isinstance(fitted, RecalibratedForecaster)
    return fitted


# ---------------------------------------------------------------------------
# What the contestant is.
# ---------------------------------------------------------------------------


def test_the_shipped_contestant_is_har_levels_recalibrated_with_the_designed_defaults() -> None:
    model = har_levels_mz()
    assert model.name == "har_levels_mz"
    assert isinstance(model, Model)
    assert model.fit_depends_on_horizon is True
    assert (model.calibration_points, model.minimum_pairs) == (60, 40)
    assert (CALIBRATION_POINTS, MINIMUM_PAIRS) == (60, 40)
    assert RecalibratedHar(SEMILOG).name == "har_semilog_mz"
    with pytest.raises(dataclasses.FrozenInstanceError):
        model.minimum_pairs = 1  # type: ignore[misc]


@pytest.mark.parametrize("horizon", [1, 5])
def test_the_contestant_conforms_on_the_stride_six_toy(horizon: int) -> None:
    conformance_suite(short_model(), toy=make_toy(horizon))


def test_the_shipped_defaults_conform_where_forty_pairs_are_reachable() -> None:
    """60 and 40, not the lowered floor: the contestant as it will be run forecasts here."""
    toy = late_toy(1)
    forecasts = [fit_recalibrated(har_levels_mz(), view).calibration for view in views_of(toy)]
    assert all(len(sample) >= MINIMUM_PAIRS for sample in forecasts)
    conformance_suite(har_levels_mz(), toy=toy)


# ---------------------------------------------------------------------------
# The sample: nothing in it postdates the outer origin, and every inner fit is knowable at its own.
# ---------------------------------------------------------------------------


def sample_leaks(model: Model, toy: Toy) -> list[str]:
    """Every way the calibration sample the model built postdates the origin it was built for."""
    found: list[str] = []
    for view in views_of(toy):
        try:
            fitted = fit_recalibrated(model, view)
        except ModelSkip:
            continue
        sample = fitted.calibration
        if sample.empty:
            found.append(f"{view.origin}: an empty sample proves nothing")
            continue
        if sample["target_as_of"].max() > view.origin:
            found.append(
                f"{view.origin}: a pair's target completed on {sample['target_as_of'].max()}"
            )
        if sample["origin"].max() >= view.origin:
            found.append(f"{view.origin}: the origin itself is in the sample")
    return found


@pytest.mark.parametrize("horizon", [1, 5])
def test_the_calibration_sample_never_contains_a_target_that_completed_after_the_origin(
    horizon: int,
) -> None:
    """``max(target_as_of)`` over the sample, read off the model's own sample (R72)."""
    toy = make_toy(horizon)
    assert sample_leaks(short_model(), toy) == []
    # Anti-vacuity: the sample has pairs whose target completed strictly after their own inner
    # origin, so ``<= origin`` is a statement about something that could have been false.
    view = views_of(toy)[-1]
    sample = fit_recalibrated(short_model(), view).calibration
    assert len(sample) >= TOY_FLOOR
    assert (sample["target_as_of"] > sample["origin"]).all()


class Recording:
    """The baseline HAR, remembering every view it was fitted on, outer and inner alike."""

    fit_depends_on_horizon = True
    name = "har_levels"

    def __init__(self) -> None:
        self.views: list[TrainingView] = []

    def fit(self, view: TrainingView) -> Forecaster:
        self.views.append(view)
        return BASE.fit(view)


def inner_leaks(toy: Toy) -> list[str]:
    """Every way an inner fit was handed more than was knowable at its own origin."""
    found: list[str] = []
    for view in views_of(toy):
        base = Recording()
        fit_recalibrated(RecalibratedHar(base, minimum_pairs=1), view)
        inner = [seen for seen in base.views if seen.origin != view.origin]
        if not inner:
            found.append(f"{view.origin}: no inner fit was made")
        sessions = list(view.returns.index)
        for seen in inner:
            where = f"{view.origin}/{seen.origin}"
            if seen.training.empty:
                found.append(f"{where}: an empty training frame")
                continue
            if max(seen.training["target_as_of"]) > seen.origin:
                found.append(
                    f"{where}: trained on a target completing {seen.training['target_as_of'].max()}"
                )
            if seen.response in seen.row.index or "target_as_of" in seen.row.index:
                found.append(f"{where}: the inner row carries its own target")
            if seen.returns.index[-1] != seen.origin:
                found.append(f"{where}: returns end {seen.returns.index[-1]}")
            if (len(sessions) - 1 - sessions.index(seen.origin)) % view.stride:
                found.append(f"{where}: not on the stride lattice")
    return found


@pytest.mark.parametrize("horizon", [1, 5])
def test_every_inner_fit_is_handed_only_what_was_knowable_at_its_own_origin(horizon: int) -> None:
    assert inner_leaks(make_toy(horizon)) == []


def test_a_ts_sliced_training_frame_is_caught_by_the_sample_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The R72 plant at the harness level: ``knowable_targets`` cut on the base session ``ts``
    instead of ``as_of`` hands every contestant ``horizon - 1`` sessions of realized future. The
    walk-forward's audit catches that; this shows the model's own sample check does too, at a
    stride where a lattice point lands in the leaked rows."""
    toy = late_toy(5)
    assert sample_leaks(short_model(), toy) == []

    def by_session(targets: pd.DataFrame, origin: dt.date) -> pd.DataFrame:
        return targets[
            [stamp.date() <= origin for stamp in pd.to_datetime(targets["ts"], utc=True)]
        ]

    monkeypatch.setattr(wf, "knowable_targets", by_session)
    assert any("completed on" in line for line in sample_leaks(short_model(), toy))


def _slice_on_the_rows_own_session(training: pd.DataFrame, origin: dt.date) -> pd.DataFrame:
    """The ts-for-as_of mistake: a row is kept once its *session* is past, not its target."""
    return training[[session <= origin for session in as_of_dates(training)]]


def _slice_at_the_outer_origin(training: pd.DataFrame, origin: dt.date) -> pd.DataFrame:
    """The inner fit trained at the outer origin: ``training`` already stops there."""
    return training


@pytest.mark.parametrize("plant", [_slice_on_the_rows_own_session, _slice_at_the_outer_origin])
@pytest.mark.parametrize("horizon", [1, 5])
def test_a_planted_inner_slice_is_caught(
    plant: Callable[[pd.DataFrame, dt.date], pd.DataFrame],
    horizon: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red case for the check above, at h=5 as well, where the first plant differs by several rows
    and not one. The plant is also *live*: it changes the forecast the contestant makes."""
    toy = make_toy(horizon)
    view = views_of(toy)[-1]
    honest = fit_recalibrated(short_model(), view).value
    monkeypatch.setattr(recalibrated, "_knowable_at", plant)
    leaked = inner_leaks(toy)
    assert leaked, "a planted inner slice passed the inner-view check"
    assert any("trained on a target completing" in line for line in leaked), leaked
    assert fit_recalibrated(short_model(), view).value != honest, "the plant changed nothing"


@pytest.mark.parametrize(
    ("toy", "label"),
    [
        (make_toy(1), "h=1, stride 6"),
        (make_toy(5), "h=5, stride 6"),
        (late_toy(1), "h=1, stride 1"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_the_inner_forecasts_are_what_the_walk_forward_itself_produces_at_those_origins(
    toy: Toy, label: str
) -> None:
    """The strongest witness the repository allows, and it does not re-derive the slice.

    ``evaluate`` run on ``har_levels`` alone, over exactly the inner origins the model chose,
    forecasts each one from a panel physically stopped there and scores it against its realized
    target. The model's inner ``(forecast, realized)`` pairs must equal those, compared with
    ``==``: an off-by-one in the inner slice, in either direction, moves a coefficient.
    """
    view = views_of(toy)[-1]
    sample = calibration_sample(BASE, view, CALIBRATION_POINTS)
    assert len(sample) >= TOY_FLOOR, label
    walked = run_cohort(
        dataclasses.replace(toy, origins=tuple(sample["origin"])), alone(BASE)
    ).results
    assert [result.origin for result in walked] == list(sample["origin"])
    assert [result.forecasts["har_levels"] for result in walked] == list(sample["forecast"])
    assert [result.realized for result in walked] == list(sample["realized"])


def _synthetic_view(sessions: int, horizon: int, stride: int) -> TrainingView:
    """A hand-built view with a long history, for lattice arithmetic the toy is too short for."""
    days = [dt.date(2020, 1, 1) + dt.timedelta(days=index) for index in range(sessions)]
    frame = pd.DataFrame(
        {
            "as_of": days,
            "har_d_proxy": [1e-4 * (1 + index % 7) for index in range(sessions)],
            "har_w_proxy": [1e-4 * (1 + index % 5) for index in range(sessions)],
            "har_m_proxy": [1e-4 * (1 + index % 3) for index in range(sessions)],
            "target": [2e-4 * (1 + index % 4) for index in range(sessions)],
        }
    )
    frame["target_as_of"] = [
        days[index + horizon] if index + horizon < sessions else None for index in range(sessions)
    ]
    training = frame[frame["target_as_of"].notna()].reset_index(drop=True)
    return TrainingView(
        symbol="SPY",
        origin=days[-1],
        horizon=horizon,
        response="target",
        training=training,
        row=frame.iloc[-1].drop(labels=["target", "target_as_of"]),
        returns=pd.Series([0.01] * sessions, index=days),
        minimum_training=5,
        stride=stride,
    )


class Varying:
    """A base whose forecast varies with the origin, so a regression on it is not singular."""

    name = "varying"
    fit_depends_on_horizon = True

    def fit(self, view: TrainingView) -> Forecaster:
        return Fixed(1e-4 * (1 + view.origin.toordinal() % 9))


class Fixed:
    observations = 1

    def __init__(self, value: float) -> None:
        self.value = value

    def forecast(self, horizon: int) -> Forecast:
        return Forecast(self.value, 1)


@pytest.mark.parametrize(("horizon", "stride"), [(1, 1), (5, 1), (22, 1), (66, 1), (22, 5)])
def test_a_horizon_longer_than_the_stride_does_not_eat_the_sample(
    horizon: int, stride: int
) -> None:
    """The design counted the trailing 60 lattice points. At stride 1 the nearest ``horizon`` of
    them have no completed target yet, which leaves ``61 - horizon`` pairs: 39 at h=22 (under the
    40 required, so the contestant refused every origin) and none at h=66. The sample counts only
    lattice points that have a completed target, so 60 are found whenever the history holds them."""
    view = _synthetic_view(600, horizon, stride)
    sample = calibration_sample(Varying(), view, CALIBRATION_POINTS)
    assert len(sample) == CALIBRATION_POINTS
    assert sample["target_as_of"].max() <= view.origin
    gaps = {
        later - earlier
        for earlier, later in zip(sample["origin"], list(sample["origin"])[1:], strict=False)
    }
    assert gaps == {dt.timedelta(days=stride)}
    literal = max(0, CALIBRATION_POINTS + 1 - math.ceil(horizon / stride))
    if horizon > stride:
        assert len(sample) > literal


# ---------------------------------------------------------------------------
# Refusals are recorded, never a silent fallback.
# ---------------------------------------------------------------------------


def test_too_few_calibration_points_is_a_recorded_skip() -> None:
    toy = make_toy(1)
    demanding = RecalibratedHar(BASE, minimum_pairs=100)
    view = views_of(toy)[-1]
    found = len(calibration_sample(BASE, view, CALIBRATION_POINTS))
    with pytest.raises(ModelSkip) as raised:
        demanding.fit(view)
    assert f"{found} usable calibration pair(s)" in str(raised.value)
    assert "fewer than the 100 required" in str(raised.value)

    # Through the harness: recorded against the contestant, a NaN in its slot, the origin kept, and
    # the baseline beside it still forecasting -- so the NaN is not the base forecast in disguise.
    evaluation = run_cohort(toy, Cohort("skips", (BASE, demanding)))
    assert evaluation.results
    assert all(math.isnan(r.forecasts[demanding.name]) for r in evaluation.results)
    assert all(math.isfinite(r.forecasts[BASE.name]) for r in evaluation.results)
    assert {name for _, name, _ in evaluation.refusals} == {demanding.name}
    assert len(evaluation.refusals) == len(evaluation.results)
    trial = next(t for t in evaluation.trials() if t.model == demanding.name)
    assert trial.refused == len(evaluation.results) and trial.origins == 0


class Flaky:
    """A base whose fits fail (``SchemaError``) at the origins in ``fail_on`` -- inner ones only."""

    name = "har_levels"
    fit_depends_on_horizon = True

    def __init__(self, fail_on: frozenset[dt.date]) -> None:
        self.fail_on = fail_on

    def fit(self, view: TrainingView) -> Forecaster:
        if view.origin in self.fail_on:
            raise SchemaError(f"the window at {view.origin} is unfittable")
        return BASE.fit(view)


def test_an_unfittable_inner_fit_costs_a_pair_and_never_the_origin() -> None:
    """A ``SchemaError`` escaping ``fit`` would skip the origin for the whole cohort (R87)."""
    toy = make_toy(1)
    whole = run_cohort(toy, alone(RecalibratedHar(BASE, minimum_pairs=1)))
    last = views_of(toy)[-1]
    # Inner origins that are not themselves outer origins, so only inner fits are made to fail.
    doomed = frozenset(calibration_sample(BASE, last, CALIBRATION_POINTS)["origin"]) - set(
        toy.origins
    )
    assert doomed
    flaky = run_cohort(toy, alone(RecalibratedHar(Flaky(doomed), minimum_pairs=1)))
    assert [r.origin for r in flaky.results] == [r.origin for r in whole.results]
    assert flaky.skipped == whole.skipped
    # Where the doomed pairs were most of a thin sample the contestant refuses, with a reason, and
    # the origin is still there; the richest origin loses pairs and nothing else.
    assert last.origin not in {origin for origin, _, _ in flaky.refusals}
    assert all(
        "calibration pair" in reason or "unfittable" in reason for *_, reason in flaky.refusals
    )
    assert (
        flaky.results[-1].forecasts["har_levels_mz"] != whole.results[-1].forecasts["har_levels_mz"]
    ), "the failure was inert"


def test_a_singular_regression_is_a_recorded_skip() -> None:
    """A base that always answers the same leaves the forecast collinear with the intercept."""
    view = _synthetic_view(300, 1, 1)
    model = RecalibratedHar(ConstantBase(1e-4))
    with pytest.raises(ModelSkip) as raised:
        model.fit(view)
    assert "unfittable" in str(raised.value)


def test_a_base_forecast_that_is_not_a_number_is_a_recorded_skip() -> None:
    with pytest.raises(ModelSkip) as raised:
        RecalibratedHar(ConstantBase(float("nan"))).fit(_synthetic_view(300, 1, 1))
    assert "nothing to recalibrate" in str(raised.value)


@dataclass(frozen=True)
class ConstantBase:
    value: float
    name: str = "constant"
    fit_depends_on_horizon: bool = True

    def fit(self, view: TrainingView) -> Forecaster:
        return Fixed(self.value)


# ---------------------------------------------------------------------------
# The arithmetic.
# ---------------------------------------------------------------------------


def test_the_recalibrated_forecast_reduces_to_the_base_forecast_when_a_is_zero_and_b_is_one() -> (
    None
):
    """A base that forecasts each inner origin's realized value exactly has a=0 and b=1, so the
    recalibrated answer is the base answer. (Not exact: a normal-equations solve.)"""
    toy = make_toy(1)
    realized = {
        stamp.date(): float(value)
        for stamp, value in zip(
            pd.to_datetime(toy.targets["ts"], utc=True),
            toy.targets["target_rv_proxy_h1"],
            strict=True,
        )
    }

    class Oracle:
        name = "oracle"
        fit_depends_on_horizon = True

        def fit(self, view: TrainingView) -> Forecaster:
            return Fixed(realized[view.origin])

    for view in views_of(toy)[-3:]:
        fitted = fit_recalibrated(RecalibratedHar(Oracle(), minimum_pairs=TOY_FLOOR), view)
        scale = abs(fitted.base_value)
        assert abs(fitted.intercept) < 1e-6 * scale
        assert fitted.slope == pytest.approx(1.0, abs=1e-6)
        assert fitted.value == pytest.approx(fitted.base_value, rel=1e-6)

    # And the contrast, so the identity above is a finding and not a property of every input.
    real = fit_recalibrated(short_model(), views_of(toy)[-1])
    assert real.value != real.base_value
    assert real.base_value == BASE.fit(views_of(toy)[-1]).forecast(1).value


def test_the_regression_is_reached_through_the_walkforward_module_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tools/measure_calibration.py`` assigns ``wf.fit_ols``; a captured reference would ignore
    it. The patch has to *change* the forecast (a red case, as for the baseline cohort)."""
    view = views_of(make_toy(1))[-1]
    genuine = fit_recalibrated(short_model(), view)
    seen: list[tuple[str, ...]] = []
    original = wf.fit_ols

    def identity_for_the_mz_step(
        frame: pd.DataFrame, regressors: tuple[str, ...], response: str
    ) -> wf.Fit:
        seen.append(tuple(regressors))
        if tuple(regressors) == ("forecast",):
            return wf.Fit(0.0, (1.0,), ("forecast",), len(frame))
        return original(frame, regressors, response)

    monkeypatch.setattr(wf, "fit_ols", identity_for_the_mz_step)
    patched = fit_recalibrated(short_model(), view)
    assert ("forecast",) in seen
    assert patched.value == patched.base_value == genuine.base_value
    assert patched.value != genuine.value


# ---------------------------------------------------------------------------
# The red case: a Mincer-Zarnowitz fitted on the origin it scores.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InSampleMz:
    """The planted leak: the shipped sample plus the origin's own ``(forecast, realized)``.

    Manufacturing the origin's realized value is only possible by reading the target frame it is
    built from, which is why it takes ``targets`` -- the same frame ``evaluate`` is given -- and
    cannot be built from a ``TrainingView``, which is the structural refusal.
    """

    targets: pd.DataFrame
    name: str = "har_levels_mz_in_sample"
    fit_depends_on_horizon: bool = True

    def fit(self, view: TrainingView) -> Forecaster:
        outer = BASE.fit(view).forecast(view.horizon)
        sample = calibration_sample(BASE, view, CALIBRATION_POINTS)
        stamps = [stamp.date() for stamp in pd.to_datetime(self.targets["ts"], utc=True)]
        at = stamps.index(view.origin)
        own = pd.DataFrame(
            {
                "origin": [view.origin],
                "forecast": [outer.value],
                "realized": [float(self.targets[view.response].iloc[at])],
                "target_as_of": [as_of_dates(self.targets).iloc[at]],
            }
        )
        leaked = pd.concat([sample, own], ignore_index=True)
        if len(leaked) < TOY_FLOOR:
            raise ModelSkip("too thin")
        intercept, slope = mincer_zarnowitz(leaked)
        return RecalibratedForecaster(
            value=intercept + slope * outer.value,
            observations=outer.observations,
            horizon=view.horizon,
            intercept=intercept,
            slope=slope,
            base_value=outer.value,
            calibration=leaked,
        )


def perturb_the_unknowable(targets: pd.DataFrame, origin: dt.date) -> pd.DataFrame:
    """Every target that had not completed by ``origin`` -- the origin's own included -- scaled."""
    moved = targets.copy()
    future = [done > origin for done in as_of_dates(moved)]
    for column in moved.columns:
        if column.startswith("target_"):
            moved.loc[future, column] = moved.loc[future, column] * 10.0
    assert any(future), "no target postdated the origin, so nothing was perturbed"
    return moved


def forecast_moves_with_the_unknowable(
    make: Callable[[pd.DataFrame], Model], toy: Toy
) -> list[str]:
    """Origins at which a forecast changed when only targets unknown at the origin were changed.

    A strengthening of conformance clause 2, which *removes* future targets: removing is not
    distinguishable from a model that never looked, while scaling them is. The model is rebuilt
    from each frame, because a leak has to come from somewhere and the only place the origin's own
    target exists is the frame ``evaluate`` is given.
    """
    moved_at: list[str] = []
    for origin in toy.origins:
        single = dataclasses.replace(toy, origins=(origin,))
        model = make(toy.targets)
        honest = run_cohort(single, alone(model))
        other = perturb_the_unknowable(toy.targets, origin)
        moved = run_cohort(single, alone(make(other)), targets=other)
        if not honest.results or not moved.results:
            continue
        a, b = honest.results[0].forecasts[model.name], moved.results[0].forecasts[model.name]
        if not (a == b or (math.isnan(a) and math.isnan(b))):
            moved_at.append(f"{origin}: {a!r} became {b!r}")
    return moved_at


def test_an_in_sample_mincer_zarnowitz_scores_better_and_is_refused() -> None:
    """The acceptance criterion, as a red case.

    The leaked variant manufactures the origin's realized target to build itself at all. By the
    leverage identity of least squares its residual at the origin is ``(1 - h)`` times the honest
    one, so it is closer to the truth at *every* origin, and the squared error is lower in
    aggregate. The shipped model reports the worse number; the refusals below are what stops the
    better one being believed.
    """
    toy = make_toy(1)
    leaky = InSampleMz(toy.targets)
    honest = short_model()
    evaluation = run_cohort(toy, Cohort("red-case", (honest, leaky)))
    assert len(evaluation.results) >= 8

    for result in evaluation.results:
        shipped = (result.realized - result.forecasts[honest.name]) ** 2
        planted = (result.realized - result.forecasts[leaky.name]) ** 2
        assert planted <= shipped, f"{result.origin}: the leak did not help"
    assert any(
        (r.realized - r.forecasts[leaky.name]) ** 2 < (r.realized - r.forecasts[honest.name]) ** 2
        for r in evaluation.results
    )
    trials = {(t.model, t.loss): t for t in evaluation.trials()}
    shipped_mse, planted_mse = (
        trials[(honest.name, "mse")].value,
        trials[(leaky.name, "mse")].value,
    )
    assert planted_mse < shipped_mse, "the leaked fit must score better, or it is not a leak"

    # Refused. Both guards, each on the shipped model (silent) and the leaked one (fires).
    assert forecast_moves_with_the_unknowable(lambda _: short_model(), toy) == []
    assert forecast_moves_with_the_unknowable(lambda targets: InSampleMz(targets), toy), (
        "the in-sample fit passed the perturbation guard"
    )
    assert sample_leaks(short_model(), toy) == []
    assert any(
        "completed on" in line or "origin itself" in line for line in sample_leaks(leaky, toy)
    ), "the in-sample fit passed the sample guard"


def test_the_leaked_fit_is_not_caught_by_removing_the_future_alone() -> None:
    """Why the perturbation guard exists: conformance clause 2 (truncate the targets) cannot see
    this leak when the model holds the full frame, and a model rebuilt from the truncated frame
    simply cannot find the origin. Recorded so the red case above is not mistaken for redundant."""
    from model_conformance import clause_2_target_side_equivalence

    toy = make_toy(1)
    clause_2_target_side_equivalence(InSampleMz(toy.targets), toy)
