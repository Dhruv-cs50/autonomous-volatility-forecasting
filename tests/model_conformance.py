"""The shared fixture and the conformance suite every contestant is run through (R85, R86).

Not a ``test_*`` file, in the manner of ``tests/forward_scan.py``: the story that builds a
contestant imports ``conformance_suite`` and calls it on its own model, so that a contestant is
held to exactly the standard the baseline is and no story can quietly hold its own to less::

    def test_my_model_conforms() -> None:
        conformance_suite(MyModel(), toy=make_toy(1))

Each clause is also a function of its own, so a failure names the clause that broke and a story
can run one against a planted defect to prove the clause can fail.

The model under test is run **alone** in a cohort of one. Putting it beside the baseline would
make the clauses about the intersection rather than about the model, and a model sharing a name
with a baseline model would overwrite its forecasts.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from dataclasses import dataclass

import pandas as pd
from test_truncation_equivalence import Synthetic, _synthetic

from volab.config import Settings, load_settings
from volab.errors import ModelSkip
from volab.features.build import build_features
from volab.features.targets import build_targets
from volab.models.base import Cohort, Forecast, Forecaster, Model, TrainingView
from volab.panel import truncate
from volab.sessions import get_calendar
from volab.walkforward import (
    LOSSES,
    ConfigurationOutcome,
    Evaluation,
    RunAudit,
    Trial,
    compare_against_persisted,
    evaluate,
    run_configuration,
    target_panel_for,
)

SYMBOL = "SPY"


@dataclass(frozen=True)
class Toy:
    """A synthetic panel with its own targets for one horizon, so the loop runs without a store."""

    panel: pd.DataFrame
    targets: pd.DataFrame
    origins: tuple[dt.date, ...]
    settings: Settings
    horizon: int
    minimum_training: int
    stride: int


STRIDE = 6


def make_toy(horizon: int = 1, *, minimum_training: int = 20) -> Toy:
    """The walk-forward's synthetic panel at ``horizon``, with a stride-6 origin set.

    Origins start late enough that the 66-session monthly window is full behind them and stop
    ``horizon + 2`` sessions before the end, so every origin has a completed target to be scored
    against. The fixture has one session with no bar, which is what makes a gap expressible.
    """
    settings = load_settings()
    calendar = get_calendar(settings)
    synthetic: Synthetic = _synthetic(calendar)
    bars = synthetic.panel[synthetic.panel["symbol"] == SYMBOL].reset_index(drop=True)
    targets = build_targets(target_panel_for(bars, calendar), horizon=horizon, calendar=calendar)
    usable = list(synthetic.sessions[70 : -(horizon + 2)])
    return Toy(
        panel=synthetic.panel,
        targets=targets,
        origins=tuple(usable[::STRIDE]),
        settings=settings,
        horizon=horizon,
        minimum_training=minimum_training,
        stride=STRIDE,
    )


def run_cohort(
    toy: Toy, cohort: Cohort, *, audit: RunAudit | None = None, targets: pd.DataFrame | None = None
) -> Evaluation:
    """``evaluate`` over ``toy`` for ``cohort``, the way every test in this seam calls it."""
    return evaluate(
        toy.panel,
        toy.targets if targets is None else targets,
        toy.origins,
        symbol=SYMBOL,
        horizon=toy.horizon,
        calendar=get_calendar(toy.settings),
        minimum_training=toy.minimum_training,
        every=toy.stride,
        audit=audit,
        cohort=cohort,
    )


def alone(model: Model) -> Cohort:
    return Cohort(f"conformance-{model.name}", (model,))


# ---------------------------------------------------------------------------
# Test doubles. Public, because a story testing a contestant needs the same ones.
# ---------------------------------------------------------------------------


class Answer:
    """A forecaster that returns what it was built with, for whatever horizon it is asked."""

    def __init__(self, value: float, observations: int = 1) -> None:
        self.observations = observations
        self._value = value

    def forecast(self, horizon: int) -> Forecast:
        return Forecast(self._value, self.observations)


class Spy:
    """A contestant that records every view it is handed and forecasts a constant.

    It exists so a test can look at what ``evaluate`` hands a contestant rather than at what a
    contestant makes of it. ``skip_on`` raises ``ModelSkip`` at those origins, ``fail_on`` raises
    ``SchemaError`` -- the two refusals R87 keeps apart.
    """

    fit_depends_on_horizon = False

    def __init__(
        self,
        name: str = "spy",
        *,
        value: float = 1e-4,
        skip_on: frozenset[dt.date] = frozenset(),
        fail_on: frozenset[dt.date] = frozenset(),
    ) -> None:
        self.name = name
        self.views: list[TrainingView] = []
        self._value = value
        self._skip_on = skip_on
        self._fail_on = fail_on

    def fit(self, view: TrainingView) -> Forecaster:
        from volab.errors import SchemaError

        self.views.append(view)
        if view.origin in self._skip_on:
            raise ModelSkip(f"declined {view.origin}")
        if view.origin in self._fail_on:
            raise SchemaError(f"the window at {view.origin} is unfittable")
        return Answer(self._value)


class AlwaysSkips:
    """Wraps a model's identity but declines every origin, for the clause about accounting."""

    def __init__(self, name: str, *, fit_depends_on_horizon: bool) -> None:
        self.name = name
        self.fit_depends_on_horizon = fit_depends_on_horizon

    def fit(self, view: TrainingView) -> Forecaster:
        raise ModelSkip(f"{self.name} declines every origin")


def views_of(toy: Toy) -> list[TrainingView]:
    """The views the harness hands contestants over ``toy``, captured by a spy."""
    spy = Spy("view-spy")
    run_cohort(toy, Cohort("conformance-views", (spy,)))
    assert spy.views, "the toy produced no scored origin, so nothing was handed to anyone"
    return spy.views


def _same(left: float, right: float) -> bool:
    return left == right or (math.isnan(left) and math.isnan(right))


# ---------------------------------------------------------------------------
# The clauses. Numbered as in the design's section 3.7.
# ---------------------------------------------------------------------------


def clause_1_per_origin_truncation_equivalence(model: Model, toy: Toy) -> None:
    """The rows the run built are byte-identical to the table built once from the whole panel."""
    evaluation = run_cohort(toy, alone(model))
    assert not evaluation.features.empty, "no origin was built, so nothing was compared"
    stored = build_features(
        toy.panel, through=max(toy.origins), calendar=get_calendar(toy.settings)
    )
    compared, difference = compare_against_persisted(evaluation.features, stored)
    assert compared == len(evaluation.features) > 0
    assert difference is None, f"a per-origin row differs from the whole-panel build: {difference}"


def clause_2_target_side_equivalence(model: Model, toy: Toy) -> None:
    """R78: a forecast made with the future targets removed is the forecast made with them.

    Compared with ``==``. For a fit that never reads the target side this is trivially true and is
    asserted anyway, because that is a property of the code until a refactor makes it a property
    of the data it was handed.
    """
    checked = 0
    for origin in toy.origins[::3]:
        full = run_cohort(dataclasses.replace(toy, origins=(origin,)), alone(model))
        stopped = run_cohort(
            dataclasses.replace(toy, origins=(origin,)),
            alone(model),
            targets=truncate(toy.targets, origin),
        )
        assert len(full.results) == len(stopped.results), (
            f"{origin}: removing future targets changed whether the origin was forecast"
        )
        if not full.results:
            continue
        checked += 1
        mine, theirs = (
            full.results[0].forecasts[model.name],
            stopped.results[0].forecasts[model.name],
        )
        assert _same(mine, theirs), (
            f"{origin}: forecast {mine!r} with the future present, {theirs!r} without it"
        )
        assert full.results[0].training_rows == stopped.results[0].training_rows
    assert checked >= 2, f"only {checked} origin(s) were actually compared"


def clause_3_audit_clean_at_every_origin(model: Model, toy: Toy) -> None:
    """Every panel, training set and return series the fit was handed stops at its origin."""
    audit = RunAudit()
    evaluation = run_cohort(toy, alone(model), audit=audit)
    assert evaluation.results, "nothing was forecast, so nothing was audited"
    assert audit.violations() == []
    assert len(audit.returns) == len(evaluation.results), "a scored origin was not audited"
    assert len(audit.training) >= len(evaluation.results)


def clause_4_every_configuration_is_counted(model: Model, toy: Toy) -> None:
    """A configuration that forecast nothing is still on the ledger, whatever the reason."""
    lines: list[Trial] = []

    def sink(trials: list[Trial] | tuple[Trial, ...]) -> int:
        lines.extend(trials)
        return len(trials)

    calendar = get_calendar(toy.settings)
    window = {"first": toy.origins[0], "last": toy.origins[-1]}

    def drive(cohort: Cohort, **overrides: object) -> ConfigurationOutcome:
        arguments: dict[str, object] = {
            "calendar": calendar,
            "every": toy.stride,
            "minimum_training": toy.minimum_training,
            "cohort": cohort,
            "persisted": lambda: None,
            "append": sink,
            "report": lambda _: None,
            **window,
            **overrides,
        }
        return run_configuration(
            toy.settings,
            SYMBOL,
            toy.horizon,
            toy.panel,
            toy.targets,
            **arguments,  # type: ignore[arg-type]
        )

    # The contestant as it is: one line per loss, labelled with the cohort it ran in.
    outcome = drive(alone(model))
    assert not outcome.refused
    assert len(lines) == len(LOSSES) == outcome.written
    assert {trial.model for trial in lines} == {model.name}
    assert {trial.cohort for trial in lines} == {f"conformance-{model.name}"}

    # A window with no scoreable origin: nothing forecast, still counted.
    lines.clear()
    drive(alone(model), first=dt.date(2020, 1, 2), last=dt.date(2020, 1, 3))
    assert len(lines) == len(LOSSES)
    assert all(trial.origins == 0 and math.isnan(trial.value) for trial in lines)

    # A contestant that declines every origin (R87's empty-cohort case): reported, not guarded.
    lines.clear()
    decliner = AlwaysSkips(model.name, fit_depends_on_horizon=model.fit_depends_on_horizon)
    declined = drive(alone(decliner))
    assert len(lines) == len(LOSSES)
    assert all(trial.origins == 0 and math.isnan(trial.value) for trial in lines)
    assert declined.evaluation is not None and declined.evaluation.results
    assert {trial.refused for trial in lines} == {len(declined.evaluation.results)}


def clause_5_determinism(model: Model, toy: Toy) -> None:
    """Two ``fit().forecast()`` calls on one view agree, which is what admits a simulation (R90)."""
    compared = 0
    for view in views_of(toy)[:4]:
        try:
            first = model.fit(view).forecast(view.horizon)
            second = model.fit(view).forecast(view.horizon)
        except ModelSkip:
            continue
        compared += 1
        assert _same(first.value, second.value), (
            f"{view.origin}: {first.value!r} then {second.value!r} from the same view"
        )
        assert first.observations == second.observations
    assert compared >= 1, "the model declined every view, so determinism was not exercised"


def clause_6_horizon_contract(model: Model, toy: Toy) -> None:
    """``fit_depends_on_horizon`` is a claim, and this is where it is tested rather than read."""
    views = views_of(toy)
    exercised = 0
    for view in views[:4]:
        try:
            fitted = model.fit(view)
        except ModelSkip:
            continue
        exercised += 1
        if model.fit_depends_on_horizon:
            try:
                fitted.forecast(view.horizon + 1)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"{model.name} says its fit depends on the horizon but forecast "
                    f"{view.horizon + 1} from a fit for {view.horizon} without raising ValueError"
                )
        else:
            # The whole target side replaced by sentinels: a fit that returns the same forecast
            # never touched it, by construction rather than by reading the source.
            blind = dataclasses.replace(
                view,
                horizon=-1,
                response="__sentinel__",
                training=view.training.iloc[0:0],
                row=pd.Series(dtype="float64"),
            )
            assert _same(
                model.fit(blind).forecast(view.horizon).value, fitted.forecast(view.horizon).value
            ), f"{model.name} claims a horizon-free fit but its answer moved with the target side"
    assert exercised >= 1, "the model declined every view, so the contract was not exercised"


def conformance_suite(model: Model, *, toy: Toy) -> None:
    """Run every clause against ``model`` on ``toy``. See the module docstring for how to call."""
    assert isinstance(model, Model), "the contestant does not satisfy the Model protocol"
    clause_1_per_origin_truncation_equivalence(model, toy)
    clause_2_target_side_equivalence(model, toy)
    clause_3_audit_clean_at_every_origin(model, toy)
    clause_4_every_configuration_is_counted(model, toy)
    clause_5_determinism(model, toy)
    clause_6_horizon_contract(model, toy)
