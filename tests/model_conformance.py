"""The shared fixture and the conformance suite every contestant is run through (R85, R86).

Not a ``test_*`` file, in the manner of ``tests/forward_scan.py``: the story that builds a
contestant imports ``conformance_suite`` and calls it on its own model, so that a contestant is
held to exactly the standard the baseline is and no story can quietly hold its own to less::

    def test_my_model_conforms() -> None:
        conformance_suite(MyModel(), toy=make_toy(1), rebuild=lambda targets: MyModel())

``rebuild`` is required (R105): it builds the contestant from a target frame, and a contestant
that takes no frame ignores the argument. See ``clause_8``.

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
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
from test_truncation_equivalence import Synthetic, _synthetic

from volab.config import Settings, load_settings
from volab.errors import ModelSkip
from volab.features.build import build_features
from volab.features.targets import build_targets, target_column
from volab.models.base import (
    UNRESTRICTED,
    Cohort,
    Domain,
    Forecast,
    Forecaster,
    Model,
    TrainingView,
)
from volab.panel import as_of_dates, truncate
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
    domain = UNRESTRICTED

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

    def __init__(
        self, name: str, *, fit_depends_on_horizon: bool, domain: Domain = UNRESTRICTED
    ) -> None:
        self.name = name
        self.fit_depends_on_horizon = fit_depends_on_horizon
        self.domain = domain

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
    decliner = AlwaysSkips(
        model.name, fit_depends_on_horizon=model.fit_depends_on_horizon, domain=model.domain
    )
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


def clause_7_unknowable_targets_are_out_of_reach(model: Model, toy: Toy) -> None:
    """R104: a forecast does not move when the targets unknown at the origin are *changed*.

    Clause 2 truncates the frame ``evaluate`` is handed, so it sees only a model that reads that
    frame. A model that kept its own reference to the target frame (R78's shape: the audit checked
    the slice, not what the fit consumed) reads the same untruncated values in both runs and
    passes it. This clause instead writes into ``toy.targets`` **in place** -- scaling by ten
    every cell of the response column whose ``as_of`` is after the origin, the origin's own target
    included -- and runs ``evaluate`` on that same object, so a reference to it sees the new
    values. Scaling rather than removing is the point: a removed value can be invisible to a
    model, a changed one cannot. The cells are restored from a saved copy in a ``finally``, and
    the frame is asserted equal to its snapshot afterwards.

    Only the forecast is compared; ``realized`` legitimately scales with the origin's own target.

    The boundary, stated rather than hidden: this reaches a reference to ``toy.targets`` itself.
    A model that copied the frame at construction, derived a Series from it (copy-on-write, on
    for pandas >= 3, separates that from later writes to the parent) or loaded its own from a
    store is out of reach of a clause that is handed only a built model; ``clause_8`` is the one
    that rebuilds. Witnesses: for what this reaches, and for clause 2 passing the same plant,
    ``test_a_contestant_holding_its_own_target_frame_is_caught_by_clause_7_and_not_by_clause_2``;
    for what it misses,
    ``test_clause_7_passes_a_contestant_that_copied_the_frame_and_clause_8_fails_it``.
    """
    column = target_column(toy.horizon)
    snapshot = toy.targets.copy()
    checked = 0
    for origin in toy.origins[::3]:
        single = dataclasses.replace(toy, origins=(origin,))
        honest = run_cohort(single, alone(model))
        unknowable = pd.Series(
            [done > origin for done in as_of_dates(toy.targets)], index=toy.targets.index
        )
        assert unknowable.any(), f"{origin}: no target was unknowable, so nothing was perturbed"
        try:
            toy.targets.loc[unknowable, column] = snapshot.loc[unknowable, column] * 10.0
            moved = run_cohort(single, alone(model))
        finally:
            toy.targets.loc[unknowable, column] = snapshot.loc[unknowable, column]
        assert len(honest.results) == len(moved.results), (
            f"{origin}: changing unknowable targets changed whether the origin was forecast"
        )
        if not honest.results:
            continue
        checked += 1
        mine, theirs = (
            honest.results[0].forecasts[model.name],
            moved.results[0].forecasts[model.name],
        )
        assert _same(mine, theirs), (
            f"{origin}: forecast {mine!r} became {theirs!r} when only unknowable targets "
            "were scaled, so the model is reading a target frame of its own"
        )
    assert toy.targets.equals(snapshot), "the clause did not restore the target frame it moved"
    assert checked >= 2, f"only {checked} origin(s) were actually compared"


def perturb_the_unknowable(targets: pd.DataFrame, origin: dt.date) -> pd.DataFrame:
    """A copy of ``targets`` with every target not completed by ``origin`` scaled by ten.

    The origin's own target is included. Every ``target_*`` column is scaled; ``make_toy`` builds
    one horizon's column only, so this matters just for a multi-horizon frame.
    """
    moved = targets.copy()
    future = pd.Series([done > origin for done in as_of_dates(moved)], index=moved.index)
    assert future.any(), f"{origin}: no target postdated the origin, so nothing was perturbed"
    for column in moved.columns:
        if column.startswith("target_"):
            moved.loc[future, column] = moved.loc[future, column] * 10.0
    return moved


def clause_8_a_rebuilt_contestant_cannot_see_unknowable_targets(
    model: Model, toy: Toy, rebuild: Callable[[pd.DataFrame], Model]
) -> None:
    """R105: a contestant rebuilt from a frame with unknowable targets scaled forecasts as before.

    Clause 7 writes into ``toy.targets`` and so reaches only a model holding that object. A model
    that ``.copy()``d the frame at construction, derived a Series from it, or loaded its own is
    unmoved by that write; the only way to reach it is to build it again from a frame that
    already carries the perturbed values. ``rebuild(frame)`` is that build. For each sampled
    origin it is called on ``toy.targets`` and on ``perturb_the_unknowable(toy.targets, origin)``,
    each model is run alone with the frame it was built from handed to ``evaluate`` too, and the
    two forecasts must agree. ``toy.targets`` is never written to, so there is nothing to restore
    (witness: the ``equals(before)`` assertion in
    ``test_clause_7_passes_a_contestant_that_copied_the_frame_and_clause_8_fails_it``).

    **It is mandatory because an optional factory would be skipped by exactly the contestant that
    needed it** (R104 again: a guarantee that applies only where someone remembered to ask).

    **What it tests today, honestly: nothing.** None of the seven shipped contestants takes a
    target frame at construction, so for each of them ``rebuild`` is a closure that ignores its
    argument and this clause passes trivially (witness: every call site in ``test_garch.py``,
    ``test_model_interface.py`` and ``test_recalibrated.py`` passes ``lambda _: ...``).
    Its value is prospective -- it binds the next contestant, and a clause that is vacuous today
    and bites tomorrow is still worth having.
    Witness that it does bite, and that clause 7 passes the same plant:
    ``test_clause_7_passes_a_contestant_that_copied_the_frame_and_clause_8_fails_it``.
    """
    checked = 0
    for origin in toy.origins[::3]:
        single = dataclasses.replace(toy, origins=(origin,))
        honest_model = rebuild(toy.targets)
        other = perturb_the_unknowable(toy.targets, origin)
        moved_model = rebuild(other)
        assert honest_model.name == moved_model.name == model.name, (
            "the rebuild factory returned a contestant other than the one under test"
        )
        honest = run_cohort(single, alone(honest_model))
        moved = run_cohort(single, alone(moved_model), targets=other)
        assert len(honest.results) == len(moved.results), (
            f"{origin}: rebuilding from perturbed unknowable targets changed whether the origin "
            "was forecast"
        )
        if not honest.results:
            continue
        checked += 1
        mine, theirs = (
            honest.results[0].forecasts[model.name],
            moved.results[0].forecasts[model.name],
        )
        assert _same(mine, theirs), (
            f"{origin}: forecast {mine!r} became {theirs!r} when the contestant was rebuilt from "
            "a frame whose unknowable targets were scaled, so it is keeping a target frame of its "
            "own"
        )
    assert checked >= 2, f"only {checked} origin(s) were actually compared"


def conformance_suite(model: Model, *, toy: Toy, rebuild: Callable[[pd.DataFrame], Model]) -> None:
    """Run every clause against ``model`` on ``toy``. See the module docstring for how to call.

    ``rebuild`` is required, not optional (R105, clause 8): it builds ``model`` from a target
    frame. A contestant that takes no frame passes a closure that ignores its argument, e.g.
    ``rebuild=lambda _: model``. Witness that omitting it is an error, not a skipped clause:
    ``test_the_conformance_suite_requires_a_rebuild_factory``.
    """
    assert isinstance(model, Model), "the contestant does not satisfy the Model protocol"
    clause_1_per_origin_truncation_equivalence(model, toy)
    clause_2_target_side_equivalence(model, toy)
    clause_3_audit_clean_at_every_origin(model, toy)
    clause_4_every_configuration_is_counted(model, toy)
    clause_5_determinism(model, toy)
    clause_6_horizon_contract(model, toy)
    clause_7_unknowable_targets_are_out_of_reach(model, toy)
    clause_8_a_rebuilt_contestant_cannot_see_unknowable_targets(model, toy, rebuild)
