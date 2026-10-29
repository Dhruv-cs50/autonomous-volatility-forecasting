"""The named cohorts. This is the only module that imports both contestant modules.

``volab.walkforward`` never names a contestant it did not define (R85): it is handed a
:class:`Cohort`. The analysis modules next door import no model either, so ``import volab.compare``
cannot pull ``arch`` through a side door. Assembling the roster is this module's whole job, and
``test_only_cohorts_imports_the_model_modules`` is the witness.

**The shootout** is the three baseline models, the three GARCH specifications (R91) and the
recalibrated HAR (R96): seven contestants (:data:`MULTISTEP` below drops one for h >= 2).
``har_levels`` is first and has to stay first, because the first contestant's ``observations`` is
``OriginResult.training_rows`` (R87). Its name is not ``phase2-baseline``, which is reserved for the
recorded run's contestants and which ``evaluate`` refuses to let any other set carry.

**The multistep cohort** is the same seven **minus** ``egarch11_skewt``, for h = 5, 22 and 66
(R114). The reason is a fact about the specification, not "EGARCH was unstable": the recursion is
``sigma2_{t+1} = C * (sigma2_t)**beta * exp(alpha*|z_t| + gamma*z_t)``, Hansen's standardized
skew-t has **polynomial tails**, so ``E[exp(alpha*|z| + gamma*z)]`` is ``+infinity`` for any
``alpha > 0`` and EGARCH(1,1) with skew-t errors has **no finite multi-step conditional variance**.
Every h >= 2 forecast it makes is a simulated estimate of infinity (its sample mean diverges in the
path count, measured in R114); only h = 1 is credible, because ``sigma2_{t+1}`` is known at ``t``
and takes no expectation. The refusal is therefore available a priori from the model and the error
law alone, with no draw and no target -- which is what separates it from a threshold on the
forecast's size (look-ahead, selecting on the random draw, and unworkable because
``Evaluation.comparable`` intersects origins across contestants).

**The pairing is enforced, and derived (US-3.9).** Each contestant declares a
:class:`~volab.models.base.Domain`; ``GarchModel`` derives EGARCH's bound (``max_horizon=1``) from
its specification. A cohort asked for a horizon some member cannot answer is **refused**
(``Cohort.refusal``, raised by ``evaluate`` and checked first by the CLI), never silently narrowed:
``Trial.cohort`` is the only record of which contestants a ledger line was scored against, and the
ledger already holds 14 ``phase2-shootout`` lines at each of h=5 and h=22 meaning seven. So
``SHOOTOUT.horizons(HORIZONS) == (1,)`` falls out of the declaration, :data:`MULTISTEP`'s members
are those that answer every horizon above 1 (:func:`multistep_members`), and ``--horizon`` defaults
to the cohort's own set. Nobody types either list. Witness for membership, on its own line:
``test_the_multistep_cohort_is_the_shootout_without_egarch_and_is_runnable_by_name``.

**The Monte Carlo error table (R102).** ``egarch11_skewt`` is the one contestant that forecasts by
simulation, so its figures carry a sampling error that is *measured*, not assumed, and printed
beside them. ``gjr11_skewt`` used to be the other and no longer is: its forecast is exact
(R117-R123), so it has no Monte-Carlo error to measure, drops out of :func:`monte_carlo_error` at
every horizon, and its two figures were deleted rather than left as live-looking data nothing
could read. The figures are those R102 records, from ``tools/measure_garch_forecast_error.py`` at
``S = 100_000`` paths, **on the probe's series and not on SPY's** (R102 says they are
series-dependent). h=1 and h=5 were never measured and are reported as ``None``, not interpolated.
EGARCH at h=66 is 3.273% -- *more* paths made it worse than 0.951% at S=10,000 -- which is why
``volab.compare`` withholds significance for it. Its two entries are unreachable in the registered
pairing (:data:`MULTISTEP` has no EGARCH) but reachable by construction, and their job is to stop a
figure being invented for a path count or horizon nobody measured. R119's instruction that the
table carry SPY figures is superseded: for GJR the table has no reader, and for EGARCH no finite
mean exists (R114) whose error could be measured.

**These figures can only withhold a claim, never authorise one.** They were measured on a series
that is not the one a shootout compares, and ``analyse`` lets a figure clear the ceiling only if
its source is the evaluated series (``volab.compare.series_source``). EGARCH at h=22 reads 0.163%
here, on a process (alpha 0.05, beta 0.87) far gentler than SPY's fit (alpha ~0.25, beta ~0.96) --
and a six-seed sd of an estimator with **no finite mean** is not evidence of precision whatever it
reads. There is deliberately no SPY counterpart quoted: this docstring carried "230.79% on SPY"
until R129, a figure no committed probe prints, and in any case a percentage error against a
divergent quantity invites being read as an error bar when R114 established the quantity does not
exist. So every entry below is withheld wherever it is used, and the only thing that would change
that for GJR was making it exact (R127) -- which removed it from this table entirely.
"""

from __future__ import annotations

from collections.abc import Mapping

from volab.features.targets import HORIZONS
from volab.models.base import Cohort, Model
from volab.models.garch import GARCH_MODELS, SIMULATIONS, GarchModel
from volab.models.recalibrated import har_levels_mz
from volab.walkforward import BASELINE_COHORT

SHOOTOUT = Cohort(
    "phase2-shootout",
    (*BASELINE_COHORT.models, *GARCH_MODELS, har_levels_mz()),
)


def multistep_members(models: tuple[Model, ...]) -> tuple[Model, ...]:
    """Those of ``models`` that answer every configured horizon above 1, in their order.

    Derived from the declarations. A name filter needed someone to remember it when a second
    horizon-limited contestant arrived; this excludes it because of what it declares.
    """
    return tuple(
        model
        for model in models
        if all(model.domain.answers(horizon) for horizon in HORIZONS if horizon >= 2)
    )


MULTISTEP = Cohort("phase2-shootout-multistep", multistep_members(SHOOTOUT.models))

# Resolved by name from the command line. The baseline is deliberately absent: its run is the
# walk-forward CLI's, and a second path to producing lines under that name is a second way for the
# recorded run to be confused with something else.
COHORTS: dict[str, Cohort] = {SHOOTOUT.name: SHOOTOUT, MULTISTEP.name: MULTISTEP}


def answering(horizon: int, registry: Mapping[str, Cohort] | None = None) -> tuple[str, ...]:
    """Names of the cohorts in ``registry`` that can be evaluated at ``horizon``, in its order.

    What a refusal points the operator at, so nobody has to remember the pairing. ``registry``
    defaults to :data:`COHORTS`; the command line passes the mapping *it* resolved names from.
    """
    cohorts = COHORTS if registry is None else registry
    return tuple(name for name, cohort in cohorts.items() if cohort.refusal(horizon) is None)


# Where the figures below were measured, **printed beside every one of them**. They come from the
# probe's synthetic persistent, leveraged, skew-t series -- not from SPY, QQQ or any series a
# shootout runs on -- so a bare ``3.273%`` beside a real result would read as that result's own
# error: true of the probe, false of the run. The day the figures are re-measured on the real
# panel, this string and the table change together, and the printed form follows.
MONTE_CARLO_ERROR_SOURCE = "synthetic probe series, not SPY"

# Percent, ``(contestant, horizon) -> sd over six seeds as a percentage of the mean``, at
# S = 100_000. R102's table, re-measured by the coordinator from the committed probe. Only the
# contestants that simulate: ``test_every_monte_carlo_figure_is_reachable`` fails on a stale key.
MONTE_CARLO_ERROR_PERCENT: dict[tuple[str, int], float] = {
    ("egarch11_skewt", 22): 0.163,
    ("egarch11_skewt", 66): 3.273,
}


def monte_carlo_error(cohort: Cohort, horizon: int) -> dict[str, float | None]:
    """The measured error in percent for each simulated contestant in ``cohort`` at ``horizon``.

    A contestant is simulated if it *says* so (``method == "simulation"``), not if it is called a
    particular name. An exact or analytic contestant has no sampling error and is absent. ``None``
    means no measurement exists -- every horizon but 22 and 66, and any path count but the one
    measured -- and is printed as "not measured".
    """
    out: dict[str, float | None] = {}
    for model in cohort.models:
        if isinstance(model, GarchModel) and model.method == "simulation":
            measured = model.simulations == SIMULATIONS
            out[model.name] = (
                MONTE_CARLO_ERROR_PERCENT.get((model.name, horizon)) if measured else None
            )
    return out
