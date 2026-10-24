"""R73 -- the forecast's calibration, and what Duan's smearing correction is worth.

Reproduces the figures recorded under **R73** in ``docs/superpowers/phase2-decisions.md``: mean
forecast over mean realized per model and horizon, where **1.0 is calibrated**, and the
correction's own effect isolated by holding the model, the window and everything else fixed and
forcing the smearing factor to 1.

Why it is a measurement and not a test. ``har_semilog`` fits a logged response on level
regressors, and ``exp(fitted log y)`` estimates the **geometric** mean of ``y`` while the
quantity being forecast is the arithmetic mean -- which is why the forecast came out a factor of
four low at h=1 before the correction existed. The standing witness,
``test_the_smearing_factor_corrects_the_retransformation_bias``, asserts only that the factor is
at least 1: that is Jensen's inequality, not calibration. Calibration is a property of the data
and the window, so it is a recorded number, and this is the script that records it.

**It appends nothing to the trial ledger.** ``evaluate`` does not write; only
``python -m volab.walkforward run`` does. The corrected losses printed here are the same numbers
the recorded run put on the ledger and can be diffed against it. The **uncorrected** ones are
not on the ledger and are not a candidate anyone may report as a result -- they are the
counterfactual that makes the correction's effect legible, and ruling R79 says so.

    uv run --locked python tools/measure_calibration.py --symbol SPY --horizon 1 --every 5
    uv run --locked python tools/measure_calibration.py --horizon 1 --from 2024-01-02 --every 5
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import time
from collections.abc import Sequence

import pandas as pd
from _harness import date_argument, heading, note

import volab.walkforward as wf
from volab.config import load_settings
from volab.features.targets import HORIZONS
from volab.sessions import get_calendar, sessions_between

# The window the recorded run used, so a bare invocation diffs against the log.
DEFAULT_START = dt.date(2021, 1, 4)


def _forced_smearing(original: object, factor: float) -> object:
    """``fit_ols`` with the retransformation factor pinned, and nothing else touched.

    The counterfactual has to differ from the shipped path in exactly one place or the comparison
    measures two changes at once. Wrapping the fit rather than editing the model keeps the
    regression, the window, the training rows and the origin set identical.
    """

    def wrapped(frame: pd.DataFrame, regressors: Sequence[str], response: str) -> wf.Fit:
        return dataclasses.replace(
            original(frame, regressors, response),  # type: ignore[operator]
            smearing=factor,
        )

    return wrapped


def _calibration(evaluation: wf.Evaluation, model: str) -> tuple[int, float, float]:
    """``(n, mean realized, mean forecast)`` over the origins where both are present."""
    pairs = evaluation.scored(model)
    if not pairs:
        return 0, float("nan"), float("nan")
    realized = sum(value for _, value, _ in pairs) / len(pairs)
    forecast = sum(value for _, _, value in pairs) / len(pairs)
    return len(pairs), realized, forecast


def _losses(evaluation: wf.Evaluation) -> dict[tuple[str, str], float]:
    return {(trial.model, trial.loss): trial.value for trial in evaluation.trials()}


def _run(
    settings: object,
    symbol: str,
    horizon: int,
    origins: Sequence[dt.date],
    panel: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    every: int,
    minimum_training: int,
) -> wf.Evaluation:
    started = time.perf_counter()
    evaluation = wf.evaluate(
        panel,
        targets,
        origins,
        symbol=symbol,
        horizon=horizon,
        calendar=get_calendar(settings),  # type: ignore[arg-type]
        minimum_training=minimum_training,
        every=every,
        audit=None,
    )
    note(f"  h{horizon}: {len(origins)} origin(s) in {time.perf_counter() - started:.1f}s")
    return evaluation


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY", help="the subject symbol (default SPY)")
    parser.add_argument(
        "--horizon",
        dest="horizons",
        action="append",
        type=int,
        choices=list(HORIZONS),
        help="repeatable; defaults to h1 and h22, the two the log quotes",
    )
    parser.add_argument("--from", dest="start", type=date_argument, default=DEFAULT_START)
    parser.add_argument(
        "--to",
        dest="stop",
        type=date_argument,
        default=None,
        help="last origin; defaults to dev_end, and there is no path here that reads past it",
    )
    parser.add_argument("--every", type=int, default=5, help="origin stride (default 5)")
    parser.add_argument("--minimum-training", type=int, default=250)
    parser.add_argument(
        "--skip-uncorrected",
        action="store_true",
        help="calibration only, skipping the forced-factor-1 counterfactual (half the compute)",
    )
    arguments = parser.parse_args(argv)
    horizons: list[int] = list(arguments.horizons or (1, 22))

    settings = load_settings()
    stop = arguments.stop or settings.splits.dev_end
    if stop > settings.splits.dev_end:
        print(f"--to {stop} is past dev_end {settings.splits.dev_end}; the holdout is sealed")
        return 1
    note(f"storage root {settings.storage_root}")
    note(f"reads stop at dev_end {settings.splits.dev_end}")
    note(f"{arguments.symbol} origins {arguments.start}..{stop} every {arguments.every}")

    calendar = get_calendar(settings)
    sessions = sessions_between(calendar, arguments.start, stop)
    if not sessions:
        print(f"no {calendar.name} sessions between {arguments.start} and {stop}")
        return 1

    heading("R73a: MEAN FORECAST OVER MEAN REALIZED -- 1.0 IS CALIBRATED")
    corrected: dict[int, wf.Evaluation] = {}
    for horizon in horizons:
        panel, targets = wf.load_for(settings, arguments.symbol, horizon)
        origins = wf.scoreable_origins(
            settings, targets, first=sessions[0], last=sessions[-1], every=arguments.every
        )
        evaluation = _run(
            settings,
            arguments.symbol,
            horizon,
            origins,
            panel,
            targets,
            every=arguments.every,
            minimum_training=arguments.minimum_training,
        )
        corrected[horizon] = evaluation
        for model in wf.MODELS:
            count, realized, forecast = _calibration(evaluation, model)
            print(
                f"h{horizon:<3} {model:12s} n={count:4d} mean realized {realized:.4e} "
                f"mean forecast {forecast:.4e}  ratio {forecast / realized:.4f}"
            )

    if arguments.skip_uncorrected:
        return 0

    heading("R73b: THE CORRECTION'S OWN EFFECT, SMEARING FORCED TO 1")
    original = wf.fit_ols
    for horizon in horizons:
        panel, targets = wf.load_for(settings, arguments.symbol, horizon)
        origins = wf.scoreable_origins(
            settings, targets, first=sessions[0], last=sessions[-1], every=arguments.every
        )
        try:
            wf.fit_ols = _forced_smearing(original, 1.0)  # type: ignore[assignment]
            flat = _run(
                settings,
                arguments.symbol,
                horizon,
                origins,
                panel,
                targets,
                every=arguments.every,
                minimum_training=arguments.minimum_training,
            )
        finally:
            # Restored whatever happens: a probe that left the shipped fit wrapped would make
            # every later number in the process a counterfactual without saying so.
            wf.fit_ols = original  # type: ignore[assignment]

        done = corrected[horizon]
        hit = _losses(done)
        flattened = _losses(flat)
        for label, evaluation, table in (
            ("corrected  ", done, hit),
            ("uncorrected", flat, flattened),
        ):
            _, realized, forecast = _calibration(evaluation, "har_semilog")
            print(
                f"h{horizon:<3} har_semilog {label} calib {forecast / realized:+.4f}  "
                f"qlike {table[('har_semilog', 'qlike')]:.5f}  "
                f"mse {table[('har_semilog', 'mse')]:.6e}"
            )
        print(
            f"h{horizon:<3} random_walk benchmark   "
            f"{'':>14}qlike {hit[('random_walk', 'qlike')]:.5f}  "
            f"mse {hit[('random_walk', 'mse')]:.6e}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
