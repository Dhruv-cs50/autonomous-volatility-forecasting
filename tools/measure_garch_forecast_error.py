"""R90 and R117-R123 -- how wrong ``arch``'s analytic multistep forecast is, how noisy the
simulation is, and how the exact GJR forecast sits between them.

Prints the figure blocks recorded under **R90** in the Phase 2 completion design
(``docs/superpowers/specs/2026-10-25-phase2-completion-design.md``, section 4.4) and the ones the
exact-forecast design (``docs/superpowers/specs/2026-10-28-gjr-exact-forecast-design.md``, M8-M14)
cites. **The figures depend on the series**: this prints them on a stated, *synthetic* one, so the
numbers differ from the design's, which were measured on four SPY origins -- no probe here reads
market data, and a SPY figure that this does not print is a figure the repository cannot
regenerate (R32, R79):

1. the three recursions side by side, per specification, on a series that is **persistent,
   leveraged and skewed** -- ``alpha=0.05, gamma=0.10, beta=0.87`` with skew-t
   ``(eta=8, lambda=-0.30)``. ``analytic`` is ``arch``'s recursion (``kappa = 1/2``), reached
   through ``_path_sum`` and cross-checked against ``arch``'s own analytic call (M8, M9); ``exact``
   is the shipped forecast (``kappa`` from the fitted skew-t); ``sim`` is 200,000 paths. The
   design's first draft used iid Gaussian returns, where a GARCH-family fit is nearly homoskedastic
   and the symmetric case is exactly the one in which the analytic leverage recursion is correct:
   that probe agreed to 0.0147% and was worthless;
2. whether ``arch`` has an analytic multistep forecast at all (for EGARCH it does not, and that is
   read off the specification, never off an exception: a refusal must not look like "unavailable");
3. why the recursion is iterated: the geometric closed form against the iteration against exact
   rational arithmetic (M11, M12 -- ``numpy.longdouble`` is a plain double on arm64 and would make
   the comparison vacuous);
4. the Monte Carlo error of the simulated forecast itself, over six seeds, at 10,000 and 100,000
   paths, at h=22 and h=66, for the two asymmetric specifications. GJR no longer simulates in
   production; it is kept here so R102's and R119's figures for it stay reproducible.

The series is simulated here and the fit and the forecasts are made by ``volab.models.garch``
itself, so the figures describe the code that ships and not a second implementation. Six seeds
are six different ``symbol`` strings fed to the same ``zlib.crc32`` key.

**It reads no market data and appends nothing to the trial ledger.** It needs no store.

    uv run --locked python tools/measure_garch_forecast_error.py
    uv run --locked python tools/measure_garch_forecast_error.py --horizon 66 --nobs 3000

Reading the figures. ``rel`` is ``(simulated - analytic) / analytic``; ``analytic is N% LOW``
is ``1 - analytic / simulated``, the same gap measured from the other side; ``exact/analytic`` is
M10 and ``sim/exact`` is M14. Monte Carlo error is the standard deviation over seeds as a
percentage of the mean. Wall-clock goes to stderr, not here.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import math
import statistics
import time
from collections.abc import Sequence
from fractions import Fraction

import pandas as pd
from _harness import heading, note
from arch.univariate import GARCH, SkewStudent, ZeroMean

from volab.errors import ModelSkip
from volab.models.base import TrainingView
from volab.models.garch import (
    GARCH_MODELS,
    GJR11_SKEWT,
    SCALE,
    GarchModel,
    _Fitted,
    _path_sum,
    _persistence,
    _read,
)

# The generating process of the recorded run, in percent units (omega in percent-squared).
OMEGA, ALPHA, GAMMA, BETA, ETA, LAMBDA = 0.03, 0.05, 0.10, 0.87, 8.0, -0.30
REFERENCE_PATHS = 200_000
MC_SEEDS = 6
# ``arch``'s own analytic call and ``_path_sum`` at ``kappa = 1/2`` must be one number (M8, M9).
# Measured 3e-15; this is 5 orders looser and still catches a wrong recursion, and a break here
# stops the probe rather than printing a figure.
ARCH_AGREEMENT = 1e-9
ORIGIN = dt.date(2024, 4, 12)


def simulate_returns(nobs: int, seed: int) -> pd.Series:
    """Daily log returns (not percent) from the leveraged, skewed GJR above."""
    model = ZeroMean(
        volatility=GARCH(p=1, o=1, q=1), distribution=SkewStudent(seed=seed), rescale=False
    )
    path = model.simulate([OMEGA, ALPHA, GAMMA, BETA, ETA, LAMBDA], nobs, burn=500)
    days = [stamp.date() for stamp in pd.bdate_range("2010-01-04", periods=nobs)]
    return pd.Series(path["data"].to_numpy() / 100.0, index=days)


def _view(returns: pd.Series) -> TrainingView:
    return TrainingView(
        symbol="SPY",
        origin=ORIGIN,
        horizon=1,
        response="target_rv_proxy_h1",
        training=pd.DataFrame(),
        row=pd.Series(dtype="float64"),
        returns=returns,
        minimum_training=250,
        stride=1,
    )


def _fitted(spec: GarchModel, returns: pd.Series) -> _Fitted:
    fitted = spec.fit(_view(returns))
    assert isinstance(fitted, _Fitted)
    return fitted


def _step_one(fitted: _Fitted) -> float:
    """``arch``'s one-step variance (percent-squared): known at the origin, no expectation."""
    return float(fitted.result.forecast(horizon=1, reindex=False).variance.iloc[-1, 0])


def _simulated(fitted: _Fitted, horizon: int, paths: int) -> float:
    """The shipped simulation, whatever the specification's method.

    ``_Fitted._simulated`` and not ``dataclasses.replace(spec, method="simulation")``: that is a
    legal pairing for GJR and EGARCH but ``("GARCH", 0, "simulation")`` is refused at construction,
    and GARCH(1,1) is the row that shows the simulation unbiased where the answer is known.
    """
    spec = dataclasses.replace(fitted.spec, simulations=paths)
    total, caught = dataclasses.replace(fitted, spec=spec)._simulated(horizon)
    value = total / SCALE**2
    if caught or not math.isfinite(value):
        raise ModelSkip(f"{fitted.spec.name}: the h={horizon} simulation is not finite")
    return value


def _arch_analytic(fitted: _Fitted, horizon: int) -> float:
    """``arch``'s own analytic path sum, divided by ``SCALE**2``. Defined for GARCH-family fits."""
    steps = fitted.result.forecast(horizon=horizon, method="analytic", reindex=False).variance
    return float(steps.iloc[-1].sum(skipna=False)) / SCALE**2


def _recursions(fitted: _Fitted, horizon: int) -> tuple[float, float]:
    """``(analytic, exact)`` for a GARCH-family fit, both through ``_path_sum``.

    ``analytic`` is ``arch``'s recursion, ``phi = alpha + gamma/2 + beta`` (``kappa = 1/2``; with
    no ``gamma`` for ``o == 0``). ``exact`` is the shipped one for GJR and the same number for
    GARCH(1,1), where ``kappa`` does not enter and ``arch``'s recursion is the true mean.
    """
    params, name = fitted.result.params, fitted.spec.name
    omega, first = _read(params, "omega", name=name), _step_one(fitted)
    alpha, beta = _read(params, "alpha[1]", name=name), _read(params, "beta[1]", name=name)
    if fitted.spec.o == 0:
        analytic = exact = _path_sum(omega, alpha + beta, first, horizon)
    else:
        phi_half = alpha + _read(params, "gamma[1]", name=name) / 2 + beta
        analytic = _path_sum(omega, phi_half, first, horizon)
        exact = _path_sum(omega, _persistence(params, name=name), first, horizon)
    analytic, exact = analytic / SCALE**2, exact / SCALE**2
    reference = _arch_analytic(fitted, horizon)
    if abs(analytic - reference) > ARCH_AGREEMENT * abs(reference):
        raise RuntimeError(
            f"{name} h={horizon}: _path_sum at kappa = 1/2 gives {analytic!r} but arch's own "
            f"analytic forecast gives {reference!r}; the recursion no longer matches arch's (W2)"
        )
    return analytic, exact


def block_analytic_against_simulation(returns: pd.Series, horizons: Sequence[int]) -> None:
    heading("analytic, exact and simulated, per specification")
    for spec in GARCH_MODELS:
        fitted = _fitted(spec, returns)
        for horizon in horizons:
            simulated = _simulated(fitted, horizon, REFERENCE_PATHS)
            if spec.volatility == "EGARCH":
                # Read off the specification, never off an exception: a ``ValueError`` here would
                # be a refusal, and a refusal must not be able to print as "unavailable".
                print(
                    f"{spec.name:15s} h={horizon:<3d} no analytic recursion and no finite "
                    f"multi-step mean (R114); sim {REFERENCE_PATHS // 1000}k {simulated * 1e4:8.3f}"
                )
                continue
            analytic, exact = _recursions(fitted, horizon)
            print(
                f"{spec.name:15s} h={horizon:<3d} analytic {analytic * 1e4:8.3f}  "
                f"exact {exact * 1e4:8.3f}  sim {REFERENCE_PATHS // 1000}k {simulated * 1e4:8.3f}  "
                f"rel {100 * (simulated - analytic) / analytic:+7.2f} %  "
                f"(analytic is {100 * (1 - analytic / simulated):.1f} % LOW)  "
                f"exact/analytic {exact / analytic:.4f}  sim/exact {simulated / exact:.5f}"
            )


def _geometric(omega: float, phi: float, first: float, horizon: int) -> float:
    """The closed-form sum the implementation does *not* use, with its ``phi == 1`` branch."""
    if phi == 1.0:
        return first * horizon + omega * horizon * (horizon - 1) / 2
    g = (1.0 - phi**horizon) / (1.0 - phi)
    return first * g + omega / (1.0 - phi) * (horizon - g)


def _rational(omega: float, phi: float, first: float, horizon: int) -> float:
    """The same recursion in exact rational arithmetic on the same floats."""
    total, step = Fraction(0), Fraction(first)
    for _ in range(horizon):
        total += step
        step = Fraction(omega) + Fraction(phi) * step
    return float(total)


def block_path_sum_numerics(returns: pd.Series, horizons: Sequence[int]) -> None:
    heading("why the recursion is iterated: geometric form and iteration against exact rationals")
    for spec in (s for s in GARCH_MODELS if s.volatility == "GARCH"):
        fitted = _fitted(spec, returns)
        params, name = fitted.result.params, spec.name
        omega, first = _read(params, "omega", name=name), _step_one(fitted)
        phi = (
            _read(params, "alpha[1]", name=name) + _read(params, "beta[1]", name=name)
            if spec.o == 0
            else _persistence(params, name=name)
        )
        # The fitted persistence of a gentle series is far from 1; SPY's is within an ulp of it at
        # most origins (R118, M11), which is where the geometric form breaks. So the fit's own
        # ``omega`` and step one are also run at a planted persistence one ulp below 1.
        for label, persistence in (("fitted", phi), ("planted 1-2**-52", 1.0 - 2.0**-52)):
            for horizon in horizons:
                truth = _rational(omega, persistence, first, horizon)
                iterated = _path_sum(omega, persistence, first, horizon)
                geometric = _geometric(omega, persistence, first, horizon)
                print(
                    f"{spec.name:15s} h={horizon:<3d} {label:17s} 1-phi {1.0 - persistence:+.3e}  "
                    f"iteration err {abs(iterated - truth) / truth:.2e}  "
                    f"geometric err {abs(geometric - truth) / truth:.2e}"
                )


def block_skew_estimate(returns: pd.Series) -> None:
    heading("the fitted skew parameter on the probe series")
    fitted = _fitted(GJR11_SKEWT, returns)
    print(f"lambda-hat {fitted.result.params.iloc[-1]:+.3f}  (generating {LAMBDA:+.2f})")


def block_monte_carlo_error(returns: pd.Series, horizons: Sequence[int]) -> None:
    heading("Monte Carlo error of the simulated forecast, over six seeds")
    for spec in (s for s in GARCH_MODELS if s.o != 0):
        fitted = _fitted(spec, returns)
        for horizon in horizons:
            cells = []
            for paths in (10_000, 100_000):
                started = time.perf_counter()
                values = [
                    _simulated(dataclasses.replace(fitted, symbol=f"seed{seed}"), horizon, paths)
                    for seed in range(MC_SEEDS)
                ]
                seconds = (time.perf_counter() - started) / MC_SEEDS
                note(f"  {spec.name} h={horizon} S={paths}: {seconds:.2f}s/call")
                error = 100 * statistics.stdev(values) / statistics.fmean(values)
                cells.append(f"S={paths:<7,d} {error:6.3f} %")
            print(f"{spec.name:15s} h={horizon:<3d} " + "   ".join(cells))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--horizon",
        dest="horizons",
        action="append",
        type=int,
        help="repeatable; defaults to h22 and h66, the two the design quotes",
    )
    parser.add_argument("--nobs", type=int, default=2000, help="length of the simulated series")
    parser.add_argument("--seed", type=int, default=20241012, help="seed of the simulated series")
    arguments = parser.parse_args(argv)
    horizons: list[int] = list(arguments.horizons or (22, 66))

    returns = simulate_returns(arguments.nobs, arguments.seed)
    note(f"{arguments.nobs} simulated returns, series seed {arguments.seed}")
    print(
        f"generating process: omega={OMEGA} alpha={ALPHA} gamma={GAMMA} beta={BETA} "
        f"eta={ETA} lambda={LAMBDA} (percent units); nobs={arguments.nobs}"
    )
    block_skew_estimate(returns)
    block_analytic_against_simulation(returns, horizons)
    block_path_sum_numerics(returns, horizons)
    block_monte_carlo_error(returns, horizons)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
