"""The contract between the walk-forward and a contestant (R85).

Pure stdlib and pandas, with **no numpy and no scipy**: ``volab.walkforward`` imports this module,
and the walk-forward is on the sealed point-in-time path whose output the byte-exact comparator
checks. ``tests/test_import_discipline.py::test_the_point_in_time_path_imports_no_numpy_or_scipy``
names this file and is the witness.

A model is handed one frozen ``TrainingView`` per origin and returns a ``Forecaster``; the
forecaster answers for a horizon. The two stages are the framework's shape, and they license
fitting once per origin and forecasting all four horizons without the interface changing.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass(frozen=True)
class TrainingView:
    """Everything knowable at one forecast origin, for one symbol and horizon.

    Built once per origin by ``volab.walkforward.evaluate`` and handed to every contestant, so
    no contestant can be handed more than another. Every field is knowable at the origin by
    construction; ``RunAudit`` records the training targets and the returns, and the panel each
    feature table was built from.
    """

    symbol: str
    origin: dt.date
    horizon: int
    # The response column name, ``features.targets.target_column(horizon)``.
    response: str
    # One row per base session up to the origin whose target had completed by the origin,
    # carrying the HAR regressors, ``response``, and ``target_as_of``: the session each target
    # completed on, which is when it became knowable.
    training: pd.DataFrame
    # The origin's own feature row. Its target is NOT known and is not present.
    row: pd.Series
    # Daily log returns indexed by ``datetime.date``, one entry per session from the subject's
    # first bar through the origin, null wherever a bar is missing or its predecessor is. The
    # series ``features.targets.build_targets`` squares.
    returns: pd.Series
    # The harness's refusal threshold, so a model can apply its own minimum consistently.
    minimum_training: int
    # The origin stride the run is using. Only the recalibrated HAR reads it.
    stride: int


@dataclass(frozen=True)
class Forecast:
    """One contestant's answer at one origin, on the variance scale the losses use."""

    value: float
    # Rows or returns the fit consumed. Reported so a thin fit is visible rather than inferred.
    observations: int


@runtime_checkable
class Forecaster(Protocol):
    """A fitted contestant. ``forecast`` raises ``ValueError`` when ``horizon`` is not the view's
    and the fit was horizon-dependent -- ``ValueError`` and not ``SchemaError``, because
    ``evaluate`` files a ``SchemaError`` under "the window was unfittable" and a wiring bug
    would then pass as a data problem. Witness:
    ``test_forecasting_a_horizon_the_fit_was_not_made_for_is_a_value_error``."""

    # A read-only ``@property`` rather than ``observations: int``. A settable Protocol member is
    # satisfied only by a *mutable* attribute, so ``mypy --strict`` rejects a frozen dataclass
    # with "expected settable variable, got read-only attribute" -- and frozen dataclasses are
    # this project's dominant idiom (``Fit``, ``Trial``, ``OriginResult``, ``Evaluation``,
    # ``Cohort``, ``Forecast``). A read-only member accepts a frozen dataclass, a plain class
    # with an annotated class attribute, and a property alike, so it is strictly more permissive.
    @property
    def observations(self) -> int: ...

    def forecast(self, horizon: int) -> Forecast: ...


@runtime_checkable
class Model(Protocol):
    # Read-only, for the reason given on ``Forecaster.observations``: a contestant may be written
    # as a frozen dataclass, which a settable member would have forbidden.
    @property
    def name(self) -> str: ...

    # False only for a model whose fit reads no part of the target side (GARCH). The conformance
    # suite turns this into a test rather than a comment.
    @property
    def fit_depends_on_horizon(self) -> bool: ...

    def fit(self, view: TrainingView) -> Forecaster: ...


@dataclass(frozen=True)
class Cohort:
    """A named set of contestants. The name is what reaches the ledger."""

    name: str
    models: tuple[Model, ...]

    @property
    def contestants(self) -> tuple[str, ...]:
        return tuple(model.name for model in self.models)
