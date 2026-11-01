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
import functools
import math
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
import pytest
from model_conformance import (
    Toy,
    alone,
    clause_8_a_rebuilt_contestant_cannot_see_unknowable_targets,
    conformance_suite,
    make_toy,
    run_cohort,
    views_of,
)
from test_truncation_equivalence import _synthetic

import volab.models.recalibrated as recalibrated
import volab.walkforward as wf
from volab.config import load_settings
from volab.errors import DatasetNotFound, ModelSkip, SchemaError
from volab.models.base import (
    UNRESTRICTED,
    Cohort,
    Domain,
    Forecast,
    Forecaster,
    Model,
    TrainingView,
)
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


@dataclass(frozen=True)
class _DeclaringBase:
    """A base that declares a bound and a requirement, delegating its fit to ``har_levels``."""

    domain: Domain
    name: str = "declaring_base"
    fit_depends_on_horizon: bool = True

    def fit(self, view: TrainingView) -> Forecaster:
        return BASE.fit(view)


def test_the_recalibrated_har_inherits_its_bases_bound() -> None:
    """The recalibration consumes the base's forecast, so it cannot answer a horizon the base
    cannot. Returning ``UNRESTRICTED`` would launder the bound: a cohort holding it would pass
    ``Cohort.refusal`` at h=22 and be asked a question its base disclaims."""
    wrapped = RecalibratedHar(_DeclaringBase(Domain(max_horizon=5)))
    assert wrapped.domain.max_horizon == 5
    cohort = Cohort("c", (wrapped,))
    assert cohort.horizons((1, 5, 22, 66)) == (1, 5)
    reason = cohort.refusal(22)
    assert reason is not None and "declaring_base_mz" in reason


def test_the_recalibrated_har_inherits_its_bases_required_columns() -> None:
    wrapped = RecalibratedHar(_DeclaringBase(Domain(requires=("iv_h1",))))
    assert wrapped.domain.requires == ("iv_h1",)
    assert Cohort("c", (wrapped,)).requires_at(1) == ("iv_h1",)


def test_the_shipped_recalibrated_har_declares_what_its_base_declares() -> None:
    assert har_levels_mz().domain == BASE.domain == UNRESTRICTED


@pytest.mark.parametrize("horizon", [1, 5])
def test_the_contestant_conforms_on_the_stride_six_toy(horizon: int) -> None:
    conformance_suite(short_model(), toy=make_toy(horizon), rebuild=lambda _: short_model())


def test_the_shipped_defaults_conform_where_forty_pairs_are_reachable() -> None:
    """60 and 40, not the lowered floor: the contestant as it will be run forecasts here."""
    toy = late_toy(1)
    forecasts = [fit_recalibrated(har_levels_mz(), view).calibration for view in views_of(toy)]
    assert all(len(sample) >= MINIMUM_PAIRS for sample in forecasts)
    conformance_suite(har_levels_mz(), toy=toy, rebuild=lambda _: har_levels_mz())


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
    domain = UNRESTRICTED
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
    ("build", "label"),
    [
        (lambda: make_toy(1), "h=1, stride 6"),
        (lambda: make_toy(5), "h=5, stride 6"),
        (lambda: late_toy(1), "h=1, stride 1"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_the_inner_forecasts_are_what_the_walk_forward_itself_produces_at_those_origins(
    build: Callable[[], Toy], label: str
) -> None:
    """The strongest witness the repository allows, and it does not re-derive the slice.

    ``evaluate`` run on ``har_levels`` alone, over exactly the inner origins the model chose,
    forecasts each one from a panel physically stopped there and scores it against its realized
    target. The model's inner ``(forecast, realized)`` pairs must equal those, compared with
    ``==``: an off-by-one in the inner slice, in either direction, moves a coefficient.
    """
    toy = build()
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
    domain = UNRESTRICTED

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
    domain = UNRESTRICTED

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
    domain = UNRESTRICTED

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
        domain = UNRESTRICTED

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
    domain = UNRESTRICTED

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
    latest = dataclasses.replace(toy, origins=toy.origins[-4:])
    clause_8_a_rebuilt_contestant_cannot_see_unknowable_targets(
        honest, latest, lambda _: short_model()
    )
    with pytest.raises(AssertionError, match="keeping a target frame of its own"):
        clause_8_a_rebuilt_contestant_cannot_see_unknowable_targets(leaky, latest, InSampleMz)
    assert sample_leaks(short_model(), toy) == []
    assert any(
        "completed on" in line or "origin itself" in line for line in sample_leaks(leaky, toy)
    ), "the in-sample fit passed the sample guard"


# ---------------------------------------------------------------------------
# The shipped 60 and 40 on the real panel, at the horizons the toy cannot reach (US-3.14).
#
# The toy has no usable regression pair at h >= 22, so those horizons were covered only by the
# hand-built lattice test above, on synthetic numbers. Here the view is the one ``evaluate`` builds
# from the curated store, read to ``dev_end`` by ``load_for`` and by nothing else, and the claims
# are relations and refusals: nothing below is a number copied from a run.
# ---------------------------------------------------------------------------

REAL_SYMBOL = "SPY"
# The recorded run's stride. The floor test also runs at stride 1, where one session moves the
# pair count by exactly one and the two sides of 40 are adjacent origins.
RECORDED_STRIDE = 5


@functools.cache
def real_frames(horizon: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The curated panel and targets for ``REAL_SYMBOL``, read once per horizon (about 3 s each).

    Cached here and not in a module-scoped fixture: a higher-scoped fixture runs before the
    autouse ones, so the read would escape the holdout police. The first call is made from inside
    a test body and is policed; ``load_for`` takes no ``end`` of its own and stops at ``dev_end``.
    Callers must not mutate the frames.
    """
    try:
        return wf.load_for(load_settings(), REAL_SYMBOL, horizon)
    except DatasetNotFound as missing:
        pytest.fail(
            f"the curated store holds no {REAL_SYMBOL} data ({missing}); rebuild it with "
            "`uv run --locked python -m volab.ingest build-raw`, then `curate`, then "
            "`python -m volab.features build`"
        )


class ViewRecorder:
    """Remembers every view ``evaluate`` hands it, so a test holds the real shape of a view.

    Its own name, not ``Recording``'s ``har_levels``: it sits in a cohort beside ``BASE``, and two
    contestants of one name would share a slot in ``OriginResult.forecasts``.
    """

    name = "view_recorder"
    fit_depends_on_horizon = True
    domain = UNRESTRICTED

    def __init__(self) -> None:
        self.views: list[TrainingView] = []

    def fit(self, view: TrainingView) -> Forecaster:
        self.views.append(view)
        return BASE.fit(view)


def walk_real(
    horizon: int,
    origins: list[dt.date],
    stride: int,
    *models: Model,
    audit: wf.RunAudit | None = None,
) -> tuple[wf.Evaluation, list[TrainingView]]:
    """``evaluate`` over the real panel at ``origins``, with ``models`` beside a view recorder."""
    panel, targets = real_frames(horizon)
    recorder = ViewRecorder()
    evaluation = wf.evaluate(
        panel,
        targets,
        origins,
        symbol=REAL_SYMBOL,
        horizon=horizon,
        calendar=get_calendar(load_settings()),
        every=stride,
        audit=audit,
        cohort=Cohort("real-data-witness", (*models, recorder)),
    )
    return evaluation, recorder.views


def late_origins(horizon: int, count: int) -> list[dt.date]:
    """The last ``count`` stride-5 origins of 2023 whose target has completed, as the run's are."""
    settings = load_settings()
    _, targets = real_frames(horizon)
    every = wf.scoreable_origins(
        settings,
        targets,
        first=dt.date(2023, 7, 3),
        last=dt.date(2023, 12, 29),
        every=RECORDED_STRIDE,
    )
    return every[-count:]


@pytest.mark.parametrize("horizon", [22, 66])
def test_the_shipped_contestant_fits_on_real_data_where_the_toy_has_no_pairs(horizon: int) -> None:
    audit = wf.RunAudit()
    origins = late_origins(horizon, 3)
    evaluation, views = walk_real(
        horizon, origins, RECORDED_STRIDE, BASE, har_levels_mz(), audit=audit
    )
    assert [result.origin for result in evaluation.results] == origins
    assert evaluation.refusals == ()
    assert audit.violations() == []
    assert [view.origin for view in views] == origins

    for result, view in zip(evaluation.results, views, strict=True):
        base, recalibrated_value = result.forecasts[BASE.name], result.forecasts["har_levels_mz"]
        assert view.horizon == horizon and view.stride == RECORDED_STRIDE
        # Finite and on the variance scale: the same order as the forecast it recalibrates. A
        # sample scaled wrongly puts the intercept orders of magnitude out.
        assert math.isfinite(recalibrated_value) and base > 0
        assert 0.25 < recalibrated_value / base < 4, (
            f"{result.origin}: {recalibrated_value} vs {base}"
        )
        # Not the plain forecast in disguise: float noise would satisfy ``!=``, so a margin.
        assert abs(recalibrated_value - base) > 1e-3 * base

        fitted = fit_recalibrated(har_levels_mz(), view)
        assert fitted.value == recalibrated_value, "the harness and a direct fit disagree"
        assert fitted.base_value == base
        assert fitted.value == fitted.intercept + fitted.slope * fitted.base_value
        assert (fitted.slope, fitted.intercept) != (1.0, 0.0)

        # The regression is the least-squares line through the pairs it was handed, by the closed
        # form and not by ``fit_ols``: the real forecasts are of order 1e-3, which is the scale the
        # toy's 1e-6 never reached.
        sample = fitted.calibration
        forecasts, realized = sample["forecast"].astype(float), sample["realized"].astype(float)
        spread = ((forecasts - forecasts.mean()) ** 2).sum()
        slope = ((forecasts - forecasts.mean()) * (realized - realized.mean())).sum() / spread
        assert fitted.slope == pytest.approx(slope, rel=1e-6)
        assert fitted.intercept == pytest.approx(
            realized.mean() - slope * forecasts.mean(), rel=1e-6
        )

        # Nothing in the sample postdates the origin, and every target in it was still pending at
        # its own inner origin -- so ``<= origin`` is a claim about something that could be false.
        assert sample["target_as_of"].max() <= view.origin
        assert sample["origin"].max() < view.origin
        assert (sample["target_as_of"] > sample["origin"]).all()


@pytest.mark.parametrize("horizon", [22, 66])
def test_the_sixty_point_lookback_bounds_what_the_real_fit_consumes(horizon: int) -> None:
    (view,) = walk_real(horizon, late_origins(horizon, 1), RECORDED_STRIDE)[1]
    shipped = fit_recalibrated(har_levels_mz(), view)
    sample = shipped.calibration
    assert len(sample) == CALIBRATION_POINTS

    # The bound is binding: older pairs exist, and a lookback one longer takes exactly one more,
    # the nearest-older, leaving the sixty the shipped model used untouched.
    older = fit_recalibrated(RecalibratedHar(BASE, calibration_points=CALIBRATION_POINTS + 1), view)
    assert len(older.calibration) == CALIBRATION_POINTS + 1
    assert older.calibration.iloc[1:].reset_index(drop=True).equals(sample)
    assert older.calibration["origin"].iloc[0] < sample["origin"].iloc[0]
    assert older.value != shipped.value, "the extra pair changed nothing, so the bound is inert"
    # And the training window the base fit consumed is far longer than the sixty-point span: the
    # lookback bounds the regression, not the HAR underneath it.
    assert min(as_of_dates(view.training)) < sample["origin"].min()
    usable = view.training.dropna(subset=[*wf.HAR_REGRESSORS, view.response])
    assert shipped.observations == len(usable)
    assert shipped.observations > 5 * CALIBRATION_POINTS

    # Sixty lattice points are sixty strides, read off the view's own session list.
    sessions = list(view.returns.index)
    positions = [sessions.index(inner) for inner in sample["origin"]]
    assert {later - earlier for earlier, later in zip(positions, positions[1:], strict=False)} == {
        RECORDED_STRIDE
    }
    # The newest pair is the nearest lattice point whose target had completed: at least ``horizon``
    # sessions back, and less than one stride further than that.
    newest = len(sessions) - 1 - positions[-1]
    assert newest % RECORDED_STRIDE == 0 and horizon <= newest < horizon + RECORDED_STRIDE


def pairs_at(horizon: int, stride: int, origin: dt.date) -> int:
    """The usable calibration pairs the shipped contestant would have at ``origin``, 0 if the
    origin cannot be fitted at all (too few training rows for ``evaluate`` to build a view)."""
    views = walk_real(horizon, [origin], stride)[1]
    return len(calibration_sample(BASE, views[0], CALIBRATION_POINTS)) if views else 0


def floor_crossing(horizon: int, stride: int) -> tuple[dt.date, dt.date]:
    """Adjacent sessions with ``MINIMUM_PAIRS - 1`` and ``MINIMUM_PAIRS`` usable pairs.

    A real pair of origins, found by bisection over a stretch of 2017 to 2019 where the count
    climbs from none to the full sixty (the first stretch with 250 training rows is what holds it
    down): the count never falls as the origin moves later, and rises by at most one a session.
    """
    sessions = wf.origins_in(
        get_calendar(load_settings()), dt.date(2017, 6, 1), dt.date(2019, 12, 31)
    )
    low, high = 0, len(sessions) - 1
    assert pairs_at(horizon, stride, sessions[low]) < MINIMUM_PAIRS, "no thin origin to start from"
    assert pairs_at(horizon, stride, sessions[high]) >= MINIMUM_PAIRS, "the count never reaches 40"
    while high - low > 1:
        middle = (low + high) // 2
        if pairs_at(horizon, stride, sessions[middle]) >= MINIMUM_PAIRS:
            high = middle
        else:
            low = middle
    return sessions[low], sessions[high]


@pytest.mark.parametrize("stride", [1, RECORDED_STRIDE])
@pytest.mark.parametrize("horizon", [22, 66])
def test_the_forty_pair_floor_refuses_one_pair_short_and_fits_at_forty_on_real_origins(
    horizon: int, stride: int
) -> None:
    """Real origins on both sides of the shipped floor, not a lowered one and not a synthetic one.

    Early in the panel the count is held down by the 250 training rows an inner fit needs, so some
    real origin has 39 pairs and the next session has 40 (see ``floor_crossing``). The unmodified
    ``har_levels_mz`` refuses the first with a recorded ``ModelSkip`` and fits the second."""
    short, enough = floor_crossing(horizon, stride)
    evaluation, views = walk_real(horizon, [short, enough], stride, BASE, har_levels_mz())
    assert [view.origin for view in views] == [short, enough]
    assert [len(calibration_sample(BASE, view, CALIBRATION_POINTS)) for view in views] == [
        MINIMUM_PAIRS - 1,
        MINIMUM_PAIRS,
    ]

    with pytest.raises(ModelSkip) as raised:
        har_levels_mz().fit(views[0])
    assert f"{MINIMUM_PAIRS - 1} usable calibration pair(s) at {short}" in str(raised.value)
    assert f"fewer than the {MINIMUM_PAIRS} required" in str(raised.value)
    fitted = fit_recalibrated(har_levels_mz(), views[1])
    assert len(fitted.calibration) == MINIMUM_PAIRS
    assert math.isfinite(fitted.value) and fitted.value != fitted.base_value

    # Through the harness: refused and recorded against the contestant at the short origin only,
    # with the baseline beside it still forecasting -- so the NaN is not the base in disguise.
    short_result, enough_result = evaluation.results
    assert [(origin, name) for origin, name, _ in evaluation.refusals] == [(short, "har_levels_mz")]
    assert evaluation.refusals[0][2] == str(raised.value)
    assert math.isnan(short_result.forecasts["har_levels_mz"])
    assert math.isfinite(short_result.forecasts[BASE.name])
    assert enough_result.forecasts["har_levels_mz"] == fitted.value

    # The floor is what refused, and nothing else about that origin: one pair fewer demanded and
    # the same view fits, on exactly the 39 pairs it has.
    lowered = fit_recalibrated(RecalibratedHar(BASE, minimum_pairs=MINIMUM_PAIRS - 1), views[0])
    assert len(lowered.calibration) == MINIMUM_PAIRS - 1
