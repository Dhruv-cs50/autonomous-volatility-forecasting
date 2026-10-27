"""The named cohorts. This is the only module that imports both contestant modules.

``volab.walkforward`` never names a contestant it did not define (R85): it is handed a
:class:`Cohort`. The analysis modules next door import no model either, so ``import volab.compare``
cannot pull ``arch`` through a side door. Assembling the roster is this module's whole job, and
``test_only_cohorts_imports_the_model_modules`` is the witness.

**The shootout** is the three baseline models, the three GARCH specifications (R91) and the
recalibrated HAR (R96): seven contestants. ``har_levels`` is first and has to stay first, because
the first contestant's ``observations`` is ``OriginResult.training_rows`` (R87). Its name is not
``phase2-baseline``, which is reserved for the recorded run's contestants and which ``evaluate``
refuses to let any other set carry.

**The Monte Carlo error table (R102).** ``gjr11_skewt`` and ``egarch11_skewt`` forecast by
simulation, so their figures carry a sampling error that is *measured*, not assumed, and printed
beside them. The figures are those R102 records, from ``tools/measure_garch_forecast_error.py`` at
``S = 100_000`` paths, **on the probe's series and not on SPY's** (R102 says they are
series-dependent). h=1 and h=5 were never measured and are reported as ``None``, not interpolated.
EGARCH at h=66 is 3.273% -- *more* paths made it worse than 0.951% at S=10,000 -- which is why
``volab.compare`` withholds significance for it.
"""

from __future__ import annotations

from volab.models.base import Cohort
from volab.models.garch import GARCH_MODELS, SIMULATIONS, GarchModel
from volab.models.recalibrated import har_levels_mz
from volab.walkforward import BASELINE_COHORT

SHOOTOUT = Cohort(
    "phase2-shootout",
    (*BASELINE_COHORT.models, *GARCH_MODELS, har_levels_mz()),
)

# Resolved by name from the command line. The baseline is deliberately absent: its run is the
# walk-forward CLI's, and a second path to producing lines under that name is a second way for the
# recorded run to be confused with something else.
COHORTS: dict[str, Cohort] = {SHOOTOUT.name: SHOOTOUT}

# Where the figures below were measured, **printed beside every one of them**. They come from the
# probe's synthetic persistent, leveraged, skew-t series -- not from SPY, QQQ or any series a
# shootout runs on -- so a bare ``3.273%`` beside a real result would read as that result's own
# error: true of the probe, false of the run. The day the figures are re-measured on the real
# panel, this string and the table change together, and the printed form follows.
MONTE_CARLO_ERROR_SOURCE = "synthetic probe series, not SPY"

# Percent, ``(contestant, horizon) -> sd over six seeds as a percentage of the mean``, at
# S = 100_000. R102's table, re-measured by the coordinator from the committed probe.
MONTE_CARLO_ERROR_PERCENT: dict[tuple[str, int], float] = {
    ("gjr11_skewt", 22): 0.207,
    ("gjr11_skewt", 66): 0.514,
    ("egarch11_skewt", 22): 0.163,
    ("egarch11_skewt", 66): 3.273,
}


def monte_carlo_error(cohort: Cohort, horizon: int) -> dict[str, float | None]:
    """The measured error in percent for each simulated contestant in ``cohort`` at ``horizon``.

    A contestant is simulated if it *says* so (``method == "simulation"``), not if it is called a
    particular name. ``None`` means no measurement exists -- every horizon but 22 and 66, and any
    path count but the one measured -- and is printed as "not measured".
    """
    out: dict[str, float | None] = {}
    for model in cohort.models:
        if isinstance(model, GarchModel) and model.method == "simulation":
            measured = model.simulations == SIMULATIONS
            out[model.name] = (
                MONTE_CARLO_ERROR_PERCENT.get((model.name, horizon)) if measured else None
            )
    return out
