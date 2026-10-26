"""The GARCH family as contestants: GARCH(1,1), GJR and EGARCH, each with skew-t errors (R91).

Three frozen ``GarchModel`` values behind ``volab.models.base``; a harness is handed them and
never imports this module (R85). numpy and scipy are admitted here and nowhere on the
point-in-time path (R82).

**The aggregation (R88).** ``features/targets.py`` defines the target, for a base session ``t``
and horizon ``h``, as::

    "the sum of the next ``h`` squared daily returns"      (module docstring)
    "the sum of ``returns[i+1] ** 2`` through ``returns[i+horizon] ** 2``"   (forward_rv_proxy)

    target_rv_proxy_h{h}(t) = sum_{j=1..h} r_{t+j}**2

A sum over exactly ``h`` steps (so no ``/h``, no ``*h``), **uncentred** (``r**2`` and not
``(r - mu)**2``), over the window ``t+1 .. t+h`` -- which is ``arch``'s forecast steps ``1..h``
from an origin whose last observation is ``t``. So the forecast is::

    f_h(t) = ( sum_{j=1..h} nu_{t+j|t} ) / SCALE**2     nu = arch's .variance of 100 * r

``mean="Zero"`` is load-bearing: under it ``.variance`` *is* ``E_t[(100 r)**2]``, the target's own
quantity. Under ``ConstantMean`` the right forecast would be ``sum(nu + mu_hat**2)`` and
``.variance`` alone would be silently wrong. The drift is therefore not modelled -- which the
target does not remove either. The ``/ SCALE**2`` is applied once, after summing. Witnesses:
``test_a_constant_variance_series_aggregates_to_h_times_the_step_variance``,
``test_the_forecast_is_the_uncentred_second_moment_not_the_variance``,
``test_the_h1_forecast_is_the_first_step_variance_over_1e4``.

**The scale (R83, R89).** Returns are multiplied by ``SCALE`` before the fit and the variance
divided by ``SCALE**2`` after. ``rescale`` is never passed: ``False`` silences the warning that
reports a bad scale (and so R83's own red case), ``True`` would silently apply a second rescaling.
Witnesses: ``test_the_model_never_passes_rescale`` (AST),
``test_a_raw_scale_series_raises_rather_than_fitting_quietly``.

**The method (R90).** GARCH(1,1) forecasts analytically. GJR and EGARCH are *simulated*:
``arch``'s analytic GJR multistep recursion is wrong under a skewed distribution (17.7% low at
h=66 against 200,000 paths) and EGARCH has none. The paths are seeded from a ``zlib.crc32`` of
``name|symbol|origin|horizon`` -- never the builtin ``hash``, which is salted per process.
Reproduce the figures with ``tools/measure_garch_forecast_error.py``. Witnesses:
``test_the_analytic_method_is_used_only_for_the_symmetric_specification`` (AST),
``test_the_seed_is_derived_without_the_salted_builtin_hash`` (AST).

**Refusal (R91).** A fit that did not converge is a ``ModelSkip``, detected by
``convergence_flag != 0`` *and* by the two warnings that mean the same thing. Both, because the
warning can be configured away and the flag cannot. The warnings are captured with
``record=True`` and ``"always"`` rather than turned into errors, **because ``arch`` prepends its own
``always`` filter for ``ConvergenceWarning`` inside ``fit``**, which beats an enclosing ``error``
(measured; the design's first draft assumed it would not). Recording is also what makes pytest and
the CLI behave identically. ``DataScaleWarning`` and every other category are re-raised: a
mis-scaled fit is a bug here, not a property of the data.

**Window (section 4.6).** The fit uses the contiguous null-free tail of ``view.returns`` that ends
at the origin. A null is never dropped from the middle: that would bridge the gap ``log_returns``
exists to refuse. Only ``returns``, ``symbol``, ``origin`` and ``minimum_training`` are read, so
``fit_depends_on_horizon`` is ``False`` and the conformance suite can prove it.
"""

from __future__ import annotations

import datetime as dt
import math
import warnings
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd
from arch import arch_model
from arch.univariate.base import ARCHModelResult
from arch.univariate.distribution import SkewStudent
from arch.utility.exceptions import ConvergenceWarning, StartingValueWarning

from volab.errors import ModelSkip
from volab.models.base import Forecast, Forecaster, TrainingView

# Daily log returns are order 1e-2; arch wants the series in its 1-to-10 band. Applied to the
# returns before the fit and squared into the divisor after the sum. A module constant, so the
# red case for a dropped scaling can be planted by patching it.
SCALE = 100.0

# The default path count for the simulated specifications (R90): 0.19%-0.29% Monte Carlo error.
SIMULATIONS = 100_000

# The only warnings that mean "this origin cannot be answered". Anything else is not a data problem.
_SKIP_WARNINGS: tuple[type[Warning], ...] = (ConvergenceWarning, StartingValueWarning)


def _seed(name: str, symbol: str, origin: dt.date, horizon: int) -> int:
    """The simulation seed: a function of the contestant, symbol, origin and horizon alone.

    ``zlib.crc32`` and never ``hash``: a string's builtin hash is salted per process, so a ledger
    line would not reproduce. It reads nothing about the data, so it is the same in the
    full-target and truncated-target runs of R78's test.
    """
    return zlib.crc32(f"{name}|{symbol}|{origin.isoformat()}|{horizon}".encode())


def _recorded[T](call: Callable[[], T]) -> tuple[T, list[warnings.WarningMessage]]:
    """``call()`` with every warning it emits recorded rather than raised or printed.

    ``"always"`` inside a scoped ``catch_warnings``: whatever filters the caller has -- pytest's
    ``error``, the CLI's defaults, a stray ``ignore`` -- the same warnings are seen, and none of
    them escapes this scope. The caller classifies them afterwards.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = call()
    return value, caught


def _raise(item: warnings.WarningMessage) -> None:
    message = item.message
    raise message if isinstance(message, Warning) else item.category(message)


def _tail(returns: pd.Series) -> np.ndarray[Any, np.dtype[np.float64]]:
    """The contiguous null-free run of ``returns`` that ends at its last entry."""
    values = returns.to_numpy(dtype="float64", na_value=np.nan)
    nulls = np.flatnonzero(np.isnan(values))
    return values[(nulls[-1] + 1 if nulls.size else 0) :]


@dataclass(frozen=True)
class _Fitted:
    """A fitted GARCH. The forecast is made on demand, so one fit answers every horizon."""

    spec: GarchModel
    result: ARCHModelResult
    observations: int
    symbol: str
    origin: dt.date

    def forecast(self, horizon: int) -> Forecast:
        if horizon < 1:
            raise ValueError(f"horizon must be at least 1, got {horizon}")
        options: dict[str, Any] = {"method": self.spec.method}
        if self.spec.method == "simulation":
            seed = _seed(self.spec.name, self.symbol, self.origin, horizon)
            # ``rng`` and not ``random_state``: for ``method="simulation"`` arch draws from the
            # *distribution's* generator, and ``random_state`` is read only by the bootstrap.
            # Passing it here compiled, ran, warned of nothing and seeded nothing. A fresh
            # generator per call, so a second call on one fit repeats the first.
            shape = SkewStudent(seed=seed)
            options.update(
                simulations=self.spec.simulations,
                rng=shape.simulate(self.result.params.to_numpy()[-shape.num_params :]),
            )
        steps, caught = _recorded(
            lambda: self.result.forecast(horizon=horizon, reindex=False, **options).variance
        )
        for item in caught:
            if not issubclass(item.category, RuntimeWarning):
                _raise(item)
        # ``skipna=False``: pandas would otherwise sum a NaN step as zero and answer too low.
        value = float(steps.iloc[-1].sum(skipna=False)) / SCALE**2
        # An explosive fit overflows ``exp`` while simulating: a refusal, not a number.
        if caught or not math.isfinite(value):
            detail = "; ".join(sorted({str(item.message) for item in caught})) or str(value)
            raise ModelSkip(f"{self.spec.name}: the h={horizon} forecast is not finite ({detail})")
        return Forecast(value, self.observations)


@dataclass(frozen=True)
class GarchModel:
    """One specification: ``mean="Zero"``, skew-t errors, and the variance process named here.

    ``maxiter`` caps the optimiser's iterations and exists so a non-converging fit can be planted
    rather than hoped for; ``None`` leaves ``arch``'s own limit.
    """

    name: str
    volatility: Literal["GARCH", "EGARCH"]
    # The asymmetry order: 1 adds GJR's leverage term (or EGARCH's sign term), 0 is symmetric.
    o: int
    # ``"analytic"`` or ``"simulation"``.
    method: str
    simulations: int = SIMULATIONS
    maxiter: int | None = None

    @property
    def fit_depends_on_horizon(self) -> bool:
        return False

    def fit(self, view: TrainingView) -> Forecaster:
        tail = _tail(view.returns)
        if len(tail) < view.minimum_training:
            raise ModelSkip(
                f"{self.name}: {len(tail)} contiguous returns end at {view.origin}, fewer than "
                f"the {view.minimum_training} required"
            )
        model = arch_model(
            tail * SCALE,
            mean="Zero",
            vol=self.volatility,
            p=1,
            o=self.o,
            q=1,
            dist="skewt",
        )
        fit_options: dict[str, Any] = {"disp": "off"}
        if self.maxiter is not None:
            fit_options["options"] = {"maxiter": self.maxiter}
        result, caught = _recorded(lambda: model.fit(**fit_options))
        for item in caught:
            if not issubclass(item.category, _SKIP_WARNINGS):
                _raise(item)
        reasons = [f"{item.category.__name__}" for item in caught]
        if result.convergence_flag != 0:
            reasons.insert(0, f"convergence_flag={result.convergence_flag}")
        if reasons:
            raise ModelSkip(f"{self.name}: the optimiser did not converge ({', '.join(reasons)})")
        return _Fitted(self, result, len(tail), view.symbol, view.origin)


GARCH11_SKEWT = GarchModel("garch11_skewt", "GARCH", 0, "analytic")
GJR11_SKEWT = GarchModel("gjr11_skewt", "GARCH", 1, "simulation")
EGARCH11_SKEWT = GarchModel("egarch11_skewt", "EGARCH", 1, "simulation")

GARCH_MODELS: tuple[GarchModel, ...] = (GARCH11_SKEWT, GJR11_SKEWT, EGARCH11_SKEWT)
