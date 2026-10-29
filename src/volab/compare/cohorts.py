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

**Measured Monte Carlo error travels with the contestant (US-3.9b).** ``egarch11_skewt`` is the one
contestant that forecasts by simulation (``gjr11_skewt`` stopped when its forecast became exact,
R117-R123), and a simulated forecast carries a sampling error that is *measured*, not assumed, and
printed beside it (R102). The figures used to live in a table here, keyed ``(contestant, horizon)``,
one module away from the contestant that needed them, and R119 recorded the result: it duplicated
what the contestant already knew and went stale the moment a real measurement existed. A contestant
now declares a :class:`~volab.models.base.Sampling` and any ``Measurement`` it has *on that
declaration*, so a figure is attached to the instance that measured it and **cannot outlive it** --
removing the contestant removes the figure, renaming it renames the figure, and a figure measured at
another path count reads as not measured. :func:`monte_carlo_error` only collects.

Nothing is measured today, so ``egarch11_skewt`` declares ``Sampling(paths=...)`` with no
``Measurement``. Its two remaining table rows (h=22 and h=66) were **deleted, not migrated**: the
horizon guard (``domain.max_horizon == 1``) makes them unreachable by construction, the ground R125
removed GJR's rows on. Its h=1 error is identically zero -- every path is the same number -- but
that is *not measured on the evaluated series*, and declaring it zero would hand a significance
claim to the contestant leading h=1 QLIKE after it was seen to lead (R124 refused exactly that).
``test_egarch_is_unclaimed_at_h1_and_its_error_is_not_declared_zero`` is the guard.

**A figure can only withhold a claim, never authorise one.** ``analyse`` lets a figure clear the
ceiling only if its source is the evaluated series (``volab.compare.series_source``), and "no
figure" reads as *not measured* and withholds. When no member has a measurement the cohort's
source is :data:`~volab.compare.NO_MEASUREMENT`, a sentence and not a series' name, so it can
never authorise anything. The h=1 shootout line therefore reads
``[MC error not measured (no measurement exists)]`` where it used to name a synthetic probe's
series, and the comparison record stores the new sentence; who is in ``unclaimed``, and the
reason given, are unchanged. Earlier records keep their own string (R126).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from volab.compare import NO_MEASUREMENT
from volab.features.targets import HORIZONS
from volab.models.base import Cohort, Model
from volab.models.garch import GARCH_MODELS
from volab.models.iv import har_rv_iv
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

# Phase 3's cohort (P3-04): the multistep members plus the HAR with implied variance, built beside
# the two registered cohorts above and leaving both untouched. Those two are the contestant set of
# the holdout pre-registration (R130, section 5), which forbids adding a Phase 3 contestant to that
# window after the fact; ``tests/test_preregistration_fence.py`` pins them. ``har_levels`` stays
# first (R87). ``har_rv_iv`` answers h <= 22 (``IV_SERIES`` has no three-month entry), so
# ``PHASE3_IV.horizons(HORIZONS)`` is ``(1, 5, 22)`` and the cohort is refused at h=66 rather than
# narrowed. It declares no ``Sampling``, so ``monte_carlo_error`` has nothing to collect for it.
PHASE3_IV = Cohort("phase3-iv", (*multistep_members(SHOOTOUT.models), har_rv_iv()))

# Resolved by name from the command line. The baseline is deliberately absent: its run is the
# walk-forward CLI's, and a second path to producing lines under that name is a second way for the
# recorded run to be confused with something else.
COHORTS: dict[str, Cohort] = {
    SHOOTOUT.name: SHOOTOUT,
    MULTISTEP.name: MULTISTEP,
    PHASE3_IV.name: PHASE3_IV,
}


def answering(horizon: int, registry: Mapping[str, Cohort] | None = None) -> tuple[str, ...]:
    """Names of the cohorts in ``registry`` that can be evaluated at ``horizon``, in its order.

    What a refusal points the operator at, so nobody has to remember the pairing. ``registry``
    defaults to :data:`COHORTS`; the command line passes the mapping *it* resolved names from.
    """
    cohorts = COHORTS if registry is None else registry
    return tuple(name for name, cohort in cohorts.items() if cohort.refusal(horizon) is None)


@dataclass(frozen=True)
class MonteCarlo:
    """The measured error of every simulated contestant in a cohort at one horizon."""

    # Percent, by contestant; ``None`` where none was measured. Only simulated contestants appear.
    percent: dict[str, float | None]
    # The one series every figure was measured on. :data:`~volab.compare.NO_MEASUREMENT` when a
    # member simulates and none has a measurement; ``None`` only when no member simulates.
    source: str | None


def monte_carlo_error(cohort: Cohort, horizon: int) -> MonteCarlo:
    """The measured error in percent for each simulated contestant in ``cohort`` at ``horizon``.

    A contestant is simulated if it *declares* so (``domain.sampling``), not if it is a particular
    class or has a particular name, and the figure is read off that declaration: there is no table
    here, so no key that can name a contestant that is not in the cohort. An exact or analytic
    contestant declares no sampling and is absent. ``None`` means no measurement exists at this
    horizon *for the path count in use* and is printed as "not measured", never as zero.

    **Raises** ``ValueError`` if two members were measured on different series: the comparison
    record stores one source per record, so it could label only one of them.
    """
    percent: dict[str, float | None] = {}
    sources: dict[str, str] = {}
    for model in cohort.models:
        sampling = model.domain.sampling
        if sampling is None:
            continue
        percent[model.name] = sampling.error(horizon)
        if sampling.source is not None:
            sources[model.name] = sampling.source
    if len(set(sources.values())) > 1:
        raise ValueError(
            "simulated contestants measured on different series cannot share a comparison "
            f"record, which holds one monte_carlo_error_source: {sources}"
        )
    source = next(iter(sources.values())) if sources else (NO_MEASUREMENT if percent else None)
    return MonteCarlo(percent, source)
