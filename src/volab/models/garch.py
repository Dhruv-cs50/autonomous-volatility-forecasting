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

**The method (R90, amended by R117-R123).** There are three, and each variance process may use
only the ones that give its true conditional mean (``GarchModel.__post_init__`` refuses the rest):

* ``"analytic"`` is ``arch``'s recursion, which hard-codes ``E[z**2 1{z<0}] = 1/2``. That is the
  true mean only for ``o == 0`` (GARCH(1,1)). Under a skewed law ``arch``'s analytic GJR is
  wrong -- 17.7% low at h=66 against 200,000 paths on the probe series (R90).
* ``"exact"`` is GJR's recursion with the real ``kappa = E[z**2 1{z<0}]`` of the fitted skew-t:
  ``m_1`` is ``arch``'s one-step variance (known at the origin) and ``m_{k+1} = omega + phi*m_k``
  with ``phi = alpha + gamma*kappa + beta``. Deterministic, so it reads no seed, and it removes
  ``gjr11_skewt`` from R115's rule: it is an exact value, not a Monte-Carlo estimate of one. It
  does **not** make the forecast good -- the fitted process is explosive at 238/238 SPY origins
  because ``arch``'s feasible region assumes ``kappa = 1/2`` (R118) -- it makes it measurable.
* ``"simulation"`` is for EGARCH, which has no finite multi-step mean under skew-t errors (R114).
  The paths are seeded from a ``zlib.crc32`` of ``name|symbol|origin|horizon`` -- never the builtin
  ``hash``, which is salted per process.

The design is ``docs/superpowers/specs/2026-10-28-gjr-exact-forecast-design.md``. Reproduce the
figures with ``tools/measure_garch_forecast_error.py``. Witnesses:
``test_the_method_and_the_variance_process_must_agree_at_construction`` (the pairing),
``test_no_asymmetric_specification_asks_arch_for_a_multistep_analytic_forecast`` (behaviour),
``test_w1_...`` and ``test_w2_...`` (the recursion against ``arch``),
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
from typing import Any, Literal, assert_never

import numpy as np
import pandas as pd
from arch import arch_model
from arch.univariate.base import ARCHModelResult
from arch.univariate.distribution import SkewStudent
from arch.utility.exceptions import ConvergenceWarning, StartingValueWarning
from scipy.special import gammaln, stdtr

from volab.errors import ModelSkip
from volab.models.base import Forecast, Forecaster, TrainingView

# Daily log returns are order 1e-2; arch wants the series in its 1-to-10 band. Applied to the
# returns before the fit and squared into the divisor after the sum. A module constant, so the
# red case for a dropped scaling can be planted by patching it.
SCALE = 100.0

# How the multistep forecast is computed. A ``Literal`` and not an ``Enum``: ``compare/cohorts.py``
# tests ``model.method == "simulation"``, which a plain ``Enum`` would make silently ``False``.
# ``assert_never`` in ``_Fitted.forecast`` makes a fourth member without a branch a mypy failure.
Method = Literal["analytic", "simulation", "exact"]

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


def _density_constant(eta: float) -> float:
    """``c`` in the unit-variance Student-t density ``c * (1 + w**2/(eta-2))**(-(eta+1)/2)``."""
    return math.exp(gammaln((eta + 1.0) / 2.0) - gammaln(eta / 2.0)) / math.sqrt(
        math.pi * (eta - 2.0)
    )


def _truncated(eta: float, x: float) -> tuple[float, float, float]:
    """``(S0, S1, S2)(x)``: mass, first and second moment of unit-variance ``t(eta)`` below ``x``.

    ``S0`` is the Student-t CDF at ``x * sqrt(eta/(eta-2))``; ``S1`` and ``S2`` are elementary, with
    ``S2`` built from the CDF of ``t(eta-2)``. The ``(eta-1)/(eta-2)`` in ``S2`` is the exact ratio
    of the two normalisers, not a fit, and ``S2(inf) = 1`` is the unit-variance check.
    """
    if x == -math.inf:
        return 0.0, 0.0, 0.0
    s0 = float(stdtr(eta, x * math.sqrt(eta / (eta - 2.0))))
    s1 = (
        -_density_constant(eta)
        * ((eta - 2.0) / (eta - 1.0))
        * (1.0 + x * x / (eta - 2.0)) ** (-(eta - 1.0) / 2.0)
    )
    s2 = (eta - 2.0) * (((eta - 1.0) / (eta - 2.0)) * float(stdtr(eta - 2.0, x)) - s0)
    return s0, s1, s2


def _skewt_moments(eta: float, lam: float, *, negative: bool) -> tuple[float, float, float]:
    """``(P, E[z], E[z**2])`` of arch's standardised Hansen skew-t, over ``z < 0`` or everywhere.

    Hansen's skew-t as arch implements it is a two-piece scaled unit-variance Student t: with
    ``y = b*z + a`` the density of ``y`` is ``s(y/(1-lam))`` below zero and ``s(y/(1+lam))`` above,
    ``s`` the unit-variance ``t(eta)`` density. So every moment is a sum of two elementary
    integrals, and ``z < 0`` is ``y < a``. Whole-line ``(1, 0, 1)`` is the machinery's own check.

    A piece whose scale ``d`` is zero has mass ``d/2 = 0`` and is skipped: arch's bound on
    ``lam`` is the *closed* interval, and at ``lam = +-1`` ``y / d`` would be a NaN.
    """
    c = math.exp(gammaln((eta + 1.0) / 2.0) - gammaln(eta / 2.0)) / math.sqrt(math.pi * (eta - 2.0))
    a = 4.0 * lam * c * (eta - 2.0) / (eta - 1.0)
    b = math.sqrt(1.0 + 3.0 * lam * lam - a * a)
    cut = a if negative else math.inf
    pieces = [(1.0 - lam, -math.inf, min(cut, 0.0))]
    if cut > 0.0:
        pieces.append((1.0 + lam, 0.0, cut))
    mass = first = second = 0.0
    for d, lower, upper in pieces:
        if d == 0.0:
            continue
        lo0, lo1, lo2 = _truncated(eta, lower / d)
        hi0, hi1, hi2 = _truncated(eta, upper / d)
        d0, d1, d2 = hi0 - lo0, hi1 - lo1, hi2 - lo2
        mass += d * d0
        first += d * (d * d1 - a * d0) / b
        second += d * (d * d * d2 - 2.0 * a * d * d1 + a * a * d0) / (b * b)
    return mass, first, second


def _kappa(eta: float, lam: float) -> float:
    """``E[z**2 1{z<0}]`` for arch's standardised Hansen skew-t, in closed form (R118's kappa).

    Exact up to the Student-t CDF. A quadrature is *not* used: Simpson over ``sinh`` is accurate
    for ``eta >= 4`` and wrong by 0.0245 at ``eta = 2.1``, and arch's bound is 2.05. It survives
    only as a test oracle. Floating point loses at most ~3e-14 here, at ``eta = 300``.
    """
    return _skewt_moments(eta, lam, negative=True)[2]


# The parameter labels arch gives a Zero-mean GARCH/GJR fit with skew-t errors. Verified against
# a live fit: ``omega, alpha[1], gamma[1], beta[1], eta, lambda`` for o=1, the same without
# ``gamma[1]`` for o=0.
_OMEGA, _ALPHA, _GAMMA, _BETA = "omega", "alpha[1]", "gamma[1]", "beta[1]"
_ETA, _LAMBDA = "eta", "lambda"


def _read(params: pd.Series, label: str, *, name: str) -> float:
    """``params[label]`` as a float, or a ``ValueError`` naming what arch actually returned.

    Not a ``ModelSkip``: a missing label is wiring, not a property of the data, and every origin
    would be equally broken. ``evaluate`` catches neither, so it aborts the run.
    """
    if label not in params.index:
        raise ValueError(
            f"{name}: the fit has no {label!r} parameter; arch returned "
            f"{list(params.index)}. Reading by position would have answered a number."
        )
    return float(params[label])


def _persistence(params: pd.Series, *, name: str) -> float:
    """GJR's mean multiplier ``phi = alpha + gamma*kappa + beta``, read from a fitted result.

    ``E[z**2] = 1`` by arch's standardisation, so ``alpha`` is not multiplied by anything.
    """
    kappa = _kappa(_read(params, _ETA, name=name), _read(params, _LAMBDA, name=name))
    return (
        _read(params, _ALPHA, name=name)
        + _read(params, _GAMMA, name=name) * kappa
        + _read(params, _BETA, name=name)
    )


def _path_sum(omega: float, phi: float, first: float, horizon: int) -> float:
    """``sum_{k=1..h} m_k`` for ``m_1 = first``, ``m_{k+1} = omega + phi*m_k``, by iteration.

    Iterated and not summed in closed form: ``alpha + beta`` arrives as ``1 - O(eps)``, the
    ``phi == 1`` branch of the geometric sum never fires, and the ratio ``(1 - phi**h)/(1 - phi)``
    is then wrong by up to 51% at h=66. Iteration reproduces an 80-bit evaluation exactly.
    ``first`` is known at the origin and is not an expectation.
    """
    total, step = 0.0, first
    for _ in range(horizon):
        total += step
        step = omega + phi * step
    return total


@dataclass(frozen=True)
class _Fitted:
    """A fitted GARCH. The forecast is made on demand, so one fit answers every horizon."""

    spec: GarchModel
    result: ARCHModelResult
    observations: int
    symbol: str
    origin: dt.date

    def _from_arch(
        self, horizon: int, options: dict[str, Any]
    ) -> tuple[float, list[warnings.WarningMessage]]:
        """``sum`` of arch's ``.variance`` over steps 1..horizon, with warnings recorded not raised.

        ``skipna=False``: pandas would otherwise sum a NaN step as zero and answer too low.
        """
        steps, caught = _recorded(
            lambda: self.result.forecast(horizon=horizon, reindex=False, **options).variance
        )
        return float(steps.iloc[-1].sum(skipna=False)), caught

    def _exact(self, horizon: int) -> tuple[float, list[warnings.WarningMessage]]:
        """GJR's exact path sum: arch for step one, this module's recursion for steps 2..horizon.

        Step one takes no expectation, so every method agrees on it and arch's own backcast and
        initialisation are reused rather than re-derived. It is asked with **no** ``method``, which
        is arch's default ``"analytic"`` (``test_arch_still_defaults_to_the_analytic_method``). No
        seed is read: the result is a function of the fit alone.
        """
        first, caught = self._from_arch(1, {})
        params, name = self.result.params, self.spec.name
        total = _path_sum(
            _read(params, _OMEGA, name=name), _persistence(params, name=name), first, horizon
        )
        return total, caught

    def _simulated(self, horizon: int) -> tuple[float, list[warnings.WarningMessage]]:
        seed = _seed(self.spec.name, self.symbol, self.origin, horizon)
        # ``rng`` and not ``random_state``: for ``method="simulation"`` arch draws from the
        # *distribution's* generator, and ``random_state`` is read only by the bootstrap.
        # Passing it here compiled, ran, warned of nothing and seeded nothing. A fresh
        # generator per call, so a second call on one fit repeats the first.
        shape = SkewStudent(seed=seed)
        return self._from_arch(
            horizon,
            {
                "method": "simulation",
                "simulations": self.spec.simulations,
                "rng": shape.simulate(self.result.params.to_numpy()[-shape.num_params :]),
            },
        )

    def forecast(self, horizon: int) -> Forecast:
        if horizon < 1:
            raise ValueError(f"horizon must be at least 1, got {horizon}")
        method = self.spec.method
        if method == "exact":
            total, caught = self._exact(horizon)
        elif method == "simulation":
            total, caught = self._simulated(horizon)
        elif method == "analytic":
            total, caught = self._from_arch(horizon, {"method": "analytic"})
        else:
            assert_never(method)
        for item in caught:
            if not issubclass(item.category, RuntimeWarning):
                _raise(item)
        value = total / SCALE**2
        # An explosive fit overflows ``exp`` while simulating: a refusal, not a number. The exact
        # branch has no ``exp`` and so no such route (``phi`` is ~1.03 at most over h <= 66), but a
        # NaN step one or shape parameter would reach it, and this is the assertion that it cannot.
        if caught or not math.isfinite(value):
            detail = "; ".join(sorted({str(item.message) for item in caught})) or str(value)
            raise ModelSkip(f"{self.spec.name}: the h={horizon} forecast is not finite ({detail})")
        return Forecast(value, self.observations)


# Exactly four, and everything else is refused at construction.
_LEGAL: frozenset[tuple[str, int, str]] = frozenset(
    {
        ("GARCH", 0, "analytic"),
        ("GARCH", 1, "exact"),
        ("GARCH", 1, "simulation"),
        ("EGARCH", 1, "simulation"),
    }
)


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
    method: Method
    simulations: int = SIMULATIONS
    maxiter: int | None = None

    def __post_init__(self) -> None:
        """The method and the variance process must agree, refused here rather than at forecast.

        R90 was a comment plus an AST test counting a string literal. It is a property of the
        pairing and belongs where the pairing is made. ``"analytic"`` is arch's recursion, which
        hard-codes ``kappa = 1/2`` and is therefore the true mean only for ``o == 0``. ``"exact"``
        is GJR's recursion and needs its leverage term; EGARCH's parameters carry the *same labels*
        with different meanings, so a label read would succeed and answer a confident wrong number.
        EGARCH has no finite multi-step mean (R114), so it can only simulate. The fourth legal
        pairing, GJR simulated, exists because ``tests/test_compare.py`` builds it to prove that
        ``monte_carlo_error`` finds a contestant by its method and not by its name.
        """
        if (self.volatility, self.o, self.method) not in _LEGAL:
            raise ValueError(
                f"{self.name}: method {self.method!r} is not legal for "
                f"({self.volatility}, o={self.o}); the legal pairings are {sorted(_LEGAL)}"
            )

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
GJR11_SKEWT = GarchModel("gjr11_skewt", "GARCH", 1, "exact")
EGARCH11_SKEWT = GarchModel("egarch11_skewt", "EGARCH", 1, "simulation")

GARCH_MODELS: tuple[GarchModel, ...] = (GARCH11_SKEWT, GJR11_SKEWT, EGARCH11_SKEWT)
