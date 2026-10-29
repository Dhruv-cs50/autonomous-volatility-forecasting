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
from collections.abc import Sequence
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


@dataclass(frozen=True)
class Measurement:
    """One Monte-Carlo error, measured once, with exactly what it was measured on.

    ``source`` is compared **by equality** to ``volab.compare.series_source``, i.e.
    ``f"{symbol} h{horizon}"``. A figure whose source does not match can only withhold a claim,
    never authorise one (R115), so a mistyped source fails safe.
    """

    percent: float
    source: str
    horizon: int
    # The path count it was measured at. A figure measured at another count does not apply.
    paths: int


@dataclass(frozen=True)
class Sampling:
    """Declared by a contestant whose forecast is a Monte-Carlo estimate rather than a value.

    ``__post_init__`` refuses *this contestant's own* measurements if they disagree on ``source``.
    The **cross-contestant** rule cannot live here -- a ``Sampling`` sees only one contestant -- so
    it is ``volab.compare.cohorts.monte_carlo_error``, which is the first to hold a whole
    cohort's figures.
    """

    paths: int
    measured: tuple[Measurement, ...] = ()

    def __post_init__(self) -> None:
        sources = sorted({figure.source for figure in self.measured})
        if len(sources) > 1:
            raise ValueError(
                f"a contestant's measurements must all come from one series, got {sources}: the "
                "comparison record holds one source per record"
            )

    def error(self, horizon: int) -> float | None:
        """The measured error in percent at ``horizon`` **for the path count in use**, else None."""
        for figure in self.measured:
            if figure.horizon == horizon and figure.paths == self.paths:
                return figure.percent
        return None

    @property
    def source(self) -> str | None:
        """The one series every measurement was made on; ``None`` when there are none."""
        return self.measured[0].source if self.measured else None


@dataclass(frozen=True)
class Domain:
    """What a contestant declares it is able to answer. Read by the harness, never inferred.

    ``max_horizon`` ``None`` means unbounded. It expresses a **prefix** of the configured horizons
    only; ``answers`` is its single reader, so a non-prefix domain changes one method. ``requires``
    names **feature columns**, the names ``volab.features.build`` writes, because a contestant
    sees columns and never the panel.

    ``requires`` is read at **every** horizon; ``per_horizon`` pairs a horizon with a column read
    only there, for a contestant whose input differs by horizon (the implied-variance model reads
    ``iv_var_h1`` at h=1 and ``iv_var_h5`` at h=5). The distinction is quantitative, not tidy:
    ``Evaluation.comparable`` intersects origins across every contestant, so a column declared at
    a horizon where it is not read removes that column's missing origins from everyone's loss.
    A tuple of pairs rather than a mapping, so ``Domain`` stays frozen and hashable and ``==``
    keeps value semantics. ``requires_at`` is the single reader of both.
    """

    max_horizon: int | None = None
    requires: tuple[str, ...] = ()
    per_horizon: tuple[tuple[int, str], ...] = ()
    sampling: Sampling | None = None

    def __post_init__(self) -> None:
        # A column declared for a horizon the contestant refuses could never be read, so it is a
        # mistake in the declaration and not a harmless extra.
        for horizon, column in self.per_horizon:
            if not self.answers(horizon):
                raise ValueError(
                    f"{column!r} is declared for h={horizon}, which this domain does not answer "
                    f"(it answers h<={self.max_horizon})"
                )

    def answers(self, horizon: int) -> bool:
        return self.max_horizon is None or horizon <= self.max_horizon

    def requires_at(self, horizon: int) -> tuple[str, ...]:
        """The feature columns read at ``horizon``: ``requires``, then this horizon's own."""
        return (*self.requires, *(column for at, column in self.per_horizon if at == horizon))


UNRESTRICTED = Domain()


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

    # What the contestant is able to answer (US-3.9). Required, so it cannot be omitted: a
    # declaration that defaulted would default to the permissive answer, the dangerous direction.
    # Read-only for the reason given on ``name``.
    @property
    def domain(self) -> Domain: ...

    def fit(self, view: TrainingView) -> Forecaster: ...


@dataclass(frozen=True)
class Cohort:
    """A named set of contestants. The name is what reaches the ledger."""

    name: str
    models: tuple[Model, ...]

    @property
    def contestants(self) -> tuple[str, ...]:
        return tuple(model.name for model in self.models)

    def refusal(self, horizon: int) -> str | None:
        """Why this cohort cannot be evaluated at ``horizon``, or ``None`` when it can.

        Dropping the member that cannot answer is not offered: ``Trial.cohort`` is the only record
        of which contestants a ledger line was scored against, and ``Evaluation.comparable``
        intersects origins across them, so the same name over a smaller set is a different
        experiment. One function, so the CLI's guard and ``evaluate``'s cannot word the same
        refusal differently or disagree about when it fires.
        """
        unable = [model for model in self.models if not model.domain.answers(horizon)]
        if not unable:
            return None
        bounds = ", ".join(
            f"{model.name!r} answers h<={model.domain.max_horizon}" for model in unable
        )
        return (
            f"cohort {self.name!r} cannot be evaluated at h={horizon}: {bounds}. "
            "Its contestant set is part of the number, so a member is never dropped silently"
        )

    def horizons(self, configured: Sequence[int]) -> tuple[int, ...]:
        """Those of ``configured`` this cohort can be evaluated at, in the caller's order.

        The caller supplies the configured set, so this module stays free of a dependency on the
        horizon configuration.
        """
        return tuple(horizon for horizon in configured if self.refusal(horizon) is None)

    def requires_at(self, horizon: int) -> tuple[str, ...]:
        """Every feature column some member reads at ``horizon``, de-duplicated, first seen first.

        There is no horizon-blind form: one would answer for the union across horizons, which is
        the over-declaration ``Domain.per_horizon`` exists to prevent.
        """
        return tuple(
            dict.fromkeys(
                column for model in self.models for column in model.domain.requires_at(horizon)
            )
        )
