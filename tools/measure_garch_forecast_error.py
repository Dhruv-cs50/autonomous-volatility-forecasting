"""R90 -- how wrong ``arch``'s analytic multistep forecast is, and how noisy the simulation is.

Reproduces the three figure blocks recorded under **R90** in
the Phase 2 completion design (``docs/superpowers/specs/2026-10-25-phase2-completion-design.md``,
section 4.4), which is why GARCH(1,1) forecasts analytically and GJR and EGARCH are *simulated*:

1. analytic against a 200,000-path simulation, per specification, on a series that is
   **persistent, leveraged and skewed** -- ``alpha=0.05, gamma=0.10, beta=0.87`` with skew-t
   ``(eta=8, lambda=-0.30)``. The design's first draft used iid Gaussian returns, where a
   GARCH-family fit is nearly homoskedastic and the symmetric case is exactly the one in which
   the analytic leverage recursion is correct: that probe agreed to 0.0147% and was worthless;
2. whether ``arch`` has an analytic multistep forecast at all (for EGARCH it does not);
3. the Monte Carlo error of the simulated forecast itself, over six seeds, at 10,000 and 100,000
   paths, at h=22 and h=66.

The series is simulated here and the fit and the forecasts are made by ``volab.models.garch``
itself, so the figures describe the code that ships and not a second implementation. The
simulation uses the module's own ``rng`` seeding: six seeds are six different ``symbol`` strings
fed to the same ``zlib.crc32`` key.

**It reads no market data and appends nothing to the trial ledger.** It needs no store.

    uv run --locked python tools/measure_garch_forecast_error.py
    uv run --locked python tools/measure_garch_forecast_error.py --horizon 66 --nobs 3000

Reading the figures. ``rel`` is ``(simulated - analytic) / analytic``; ``analytic is N% LOW``
is ``1 - analytic / simulated``, the same gap measured from the other side. Monte Carlo error is
the standard deviation over seeds as a percentage of the mean. Wall-clock goes to stderr, not here.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import statistics
import time
from collections.abc import Sequence

import pandas as pd
from _harness import heading, note
from arch.univariate import GARCH, SkewStudent, ZeroMean

from volab.models.base import TrainingView
from volab.models.garch import GARCH_MODELS, GJR11_SKEWT, GarchModel, _Fitted

# The generating process of the recorded run, in percent units (omega in percent-squared).
OMEGA, ALPHA, GAMMA, BETA, ETA, LAMBDA = 0.03, 0.05, 0.10, 0.87, 8.0, -0.30
REFERENCE_PATHS = 200_000
MC_SEEDS = 6
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


def _forecast(fitted: _Fitted, horizon: int, **changes: object) -> float:
    return (
        dataclasses.replace(
            fitted,
            spec=dataclasses.replace(fitted.spec, **changes),  # type: ignore[arg-type]
        )
        .forecast(horizon)
        .value
    )


def block_analytic_against_simulation(returns: pd.Series, horizons: Sequence[int]) -> None:
    heading("analytic against simulation, per specification")
    for spec in GARCH_MODELS:
        fitted = _fitted(spec, returns)
        for horizon in horizons:
            simulated = _forecast(fitted, horizon, method="simulation", simulations=REFERENCE_PATHS)
            try:
                analytic = _forecast(fitted, horizon, method="analytic")
            except ValueError as unavailable:
                print(f"{spec.name:15s} h={horizon:<3d} analytic unavailable: {unavailable}")
                continue
            print(
                f"{spec.name:15s} h={horizon:<3d} analytic {analytic * 1e4:8.3f}  "
                f"sim {REFERENCE_PATHS // 1000}k {simulated * 1e4:8.3f}  "
                f"rel {100 * (simulated - analytic) / analytic:+7.2f} %  "
                f"(analytic is {100 * (1 - analytic / simulated):.1f} % LOW)"
            )


def block_skew_estimate(returns: pd.Series) -> None:
    heading("the fitted skew parameter on the probe series")
    fitted = _fitted(GJR11_SKEWT, returns)
    print(f"lambda-hat {fitted.result.params.iloc[-1]:+.3f}  (generating {LAMBDA:+.2f})")


def block_monte_carlo_error(returns: pd.Series, horizons: Sequence[int]) -> None:
    heading("Monte Carlo error of the simulated forecast, over six seeds")
    for spec in (s for s in GARCH_MODELS if s.method == "simulation"):
        fitted = _fitted(spec, returns)
        for horizon in horizons:
            cells = []
            for paths in (10_000, 100_000):
                started = time.perf_counter()
                values = [
                    _forecast(
                        dataclasses.replace(fitted, symbol=f"seed{seed}"),
                        horizon,
                        simulations=paths,
                    )
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
    block_monte_carlo_error(returns, horizons)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
