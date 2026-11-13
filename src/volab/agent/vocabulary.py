"""The closed vocabulary: what an agent may propose, and the gate that refuses everything else.

The agent authors no code and names no date. It **selects from a finite set this module owns**, so
"a feature built from future bars" is unrepresentable here rather than forbidden (R184, R185). A
:class:`Proposal` is a frozen dataclass whose every field is such a selection, and :func:`validate`
is the only door a raw LLM payload passes through.

**``validate`` refuses *unknown* keys, and consults no list of forbidden ones.** A payload carrying
``end``, ``start``, ``every``, ``code`` or ``cohort`` is refused because it carries a key that is
not one of the five this module knows, not because any of those names is on a list -- and there is
no such list anywhere in this file. A denylist is a list that cannot report its own incompleteness,
which is R151's exact failure mode; refusing by being unknown means a key invented in 2027 is
refused today by the same branch that refuses ``end``.

**The vocabulary is derived, and pinned so that widening it is a deliberate act (R185, R214).**
:data:`VOCABULARY` is ``FEATURE_COLUMNS`` minus the HAR base and its exact transforms: a
subtraction, so a column added to the feature engine would join it silently. A snapshot test
(``test_the_vocabulary_equals_the_committed_snapshot_so_widening_is_a_reviewed_edit``) compares it
with a literal tuple, so any widening is an edit to that literal and appears in a reviewed diff.
What *is* a positive allowlist is the rejection of target columns: ``FEATURE_COLUMNS`` holds no
forward quantity, so ``target_rv_proxy_h5`` and every name nobody anticipated is refused as not in
the vocabulary, where a denylist would refuse only what it lists. That the vocabulary admits no
target column is witnessed by a derived test (``test_the_vocabulary_names_no_target_column``), not
asserted here. The agent names no response either: there is no field for it.

**Two sets of columns are excluded, for different reasons, and both are witnessed by running
``fit_ols`` rather than by trusting these sentences.** ``HAR_COLUMNS`` (``har_d_proxy``,
``har_w_proxy``, ``har_m_proxy``) are excluded because they *are* the base every proposal already
carries: naming one repeats a design column. ``rv_proxy_d1``, ``rv_proxy_d5`` and ``rv_proxy_d22``
(:data:`_EXACT_HAR_TRANSFORMS`) are excluded because they are exact linear transforms of that base
(``har_d_proxy`` *is* ``rv_proxy_d1``, measured to 0.0; ``har_w_proxy * 5`` and ``har_m_proxy * 22``
equal the other two to within float epsilon, which ``features/har.py`` makes true by
construction), so naming one gives an exactly singular design.

**Some *combinations* of vocabulary columns are refused too, for the same reason (R225).** Per
window, ``semivar_neg + semivar_pos = rv_proxy`` and ``signed_jump = semivar_pos - semivar_neg``
by definition (``features/realized.py``). So at d1, d5 and d22, where ``rv_proxy`` is in the HAR
base, at most one of the window's semivariance and jump columns may be named; at d66 at most two
of its four. :data:`_IDENTITY_GROUPS` is derived from those definitions, and the gate refuses
more than allowed as ``COLLINEAR_REGRESSORS``. The first live sprint on the real store named
three of the d5 group in every slot, and all 250 origins of each were refused as singular. Each
column is still in the vocabulary on its own; only naming too many from one group is refused.

**What is deliberately *not* excluded: a column that is constant in some window.** Constancy is a
property of a window, not of a column, so a static list would be wrong the day the window moves.
``has_distribution`` is the live instance today: measured on 2026-11-10 over the real SPY table
read to ``dev_end`` (2025-12-31), it holds **one distinct value over 2,312 rows** and is therefore
collinear with the intercept. The date is when the measurement was taken, not the boundary it was
taken to; an earlier wording conflated the two, which is the sort of slip R32 exists to catch. A
proposal
naming it is refused at fit time by ``HarX.fit`` as a ``ModelSkip`` naming the column, which skips
one model at one origin and leaves the origin standing for everyone else.

**This module imports nothing that reaches ``volab.store``** (R192). ``volab.features.build`` is
store-free; ``volab.features.targets`` and ``volab.compare.cohorts`` are not, so :data:`HORIZONS`
and :data:`REFERENCE_CONTESTANTS` are literals, each pinned to the real source by a two-sided test.

What this does **not** cover: it checks that a name is in the vocabulary, not that the resulting
model is a good one; it does not check that the symbol has data, only that it is in the universe
it is handed; and a proposal that passes is still only a *proposal* -- running it is another
process's job.
"""

from __future__ import annotations

import datetime as dt
import enum
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from volab.errors import ProposalRefused
from volab.features.build import FEATURE_COLUMNS, HAR_COLUMNS, WINDOWS

# Exact linear transforms of the HAR base, so a proposal naming one is ALWAYS singular.
# MEASURED: har_d_proxy IS rv_proxy_d1 (max abs diff 0.0); har_w_proxy*5 == rv_proxy_d5 (3.5e-18);
# har_m_proxy*22 == rv_proxy_d22 (6.9e-18). features/har.py makes this true by construction.
_EXACT_HAR_TRANSFORMS: frozenset[str] = frozenset({"rv_proxy_d1", "rv_proxy_d5", "rv_proxy_d22"})

VOCABULARY: tuple[str, ...] = tuple(
    c for c in FEATURE_COLUMNS if c not in _EXACT_HAR_TRANSFORMS and c not in HAR_COLUMNS
)


def _identity_groups() -> tuple[tuple[tuple[str, ...], int], ...]:
    """Per window, the vocabulary columns tied by an identity, and how many of them may be named.

    ``features/realized.py`` defines ``semivar_neg + semivar_pos = rv_proxy`` and
    ``signed_jump = semivar_pos - semivar_neg`` for every window, so the four columns of a window
    span two dimensions. Where ``rv_proxy`` is an exact HAR transform (and so out of the
    vocabulary), one of those two is already in every design and only one more may be named.
    """
    groups = []
    for window in WINDOWS:
        family = tuple(
            f"{stem}_proxy_d{window}"
            for stem in ("rv", "semivar_neg", "semivar_pos", "signed_jump")
        )
        members = tuple(name for name in family if name in VOCABULARY)
        groups.append((members, 2 - (len(family) - len(members))))
    return tuple(groups)


# R225. Naming more than ``allowed`` of one group gives a design that is singular at EVERY origin,
# by construction rather than by the data (contrast ``has_distribution``, R199). MEASURED on the
# real dev panel (2026-11-13): these are exactly the 8 exact dependencies among the HAR base and
# the realized vocabulary columns, for each of SPY, QQQ and IWM.
_IDENTITY_GROUPS: tuple[tuple[tuple[str, ...], int], ...] = _identity_groups()

# A literal, because ``volab.features.targets`` reaches ``volab.store``. Pinned by
# ``test_the_horizons_literal_equals_the_real_horizons``.
HORIZONS: tuple[int, ...] = (1, 5, 22, 66)

# Priced from a measurement, not chosen: 3 HAR + 5 extra + an intercept is 9 parameters, about
# 16-20 ms per pure-Python ``fit_ols``, roughly 4-5 s per (symbol, horizon) over 251 origins.
MAX_EXTRA_REGRESSORS: int = 5

# Literals for the same reason as HORIZONS; ``har_levels`` leads because the first contestant's
# ``observations`` is ``OriginResult.training_rows`` (R87).
REFERENCE_CONTESTANTS: tuple[str, ...] = (
    "har_levels",
    "har_semilog",
    "random_walk",
    "har_levels_mz",
)

AGENT_COHORT_PATTERN: str = r"^agent-\d{4}-\d{2}-\d{2}-[0-9a-f]{8}-\d{2}$"

_FAMILY = "harx"
_KEYS: tuple[str, ...] = ("family", "extra_regressors", "symbol", "horizon", "reference")


class RefusalCode(enum.Enum):
    """Which branch of :func:`validate` refused. Classifiable, so a canary floor can count codes."""

    NOT_A_MAPPING = "not_a_mapping"
    UNKNOWN_KEY = "unknown_key"
    MISSING_KEY = "missing_key"
    OUT_OF_VOCABULARY = "out_of_vocabulary"
    DUPLICATE_REGRESSOR = "duplicate_regressor"
    COLLINEAR_REGRESSORS = "collinear_regressors"
    TOO_MANY_REGRESSORS = "too_many_regressors"
    NO_REGRESSORS = "no_regressors"
    SYMBOL_NOT_IN_UNIVERSE = "symbol_not_in_universe"
    HORIZON_NOT_CONFIGURED = "horizon_not_configured"
    UNKNOWN_FAMILY = "unknown_family"
    UNKNOWN_REFERENCE = "unknown_reference"
    REFERENCE_ORDER = "reference_order"
    NO_REFERENCE = "no_reference"
    DUPLICATE_REFERENCE = "duplicate_reference"


@dataclass(frozen=True)
class Proposal:
    """One experiment, every field a selection from a finite set."""

    proposal_id: str  # the HARNESS assigns it, never the model
    family: Literal["harx"]
    extra_regressors: tuple[str, ...]  # 1..MAX_EXTRA_REGRESSORS, unique, from VOCABULARY
    symbol: str
    horizon: int
    reference: tuple[str, ...]  # >=1 of REFERENCE_CONTESTANTS, "har_levels" FIRST (R87)


@dataclass(frozen=True)
class GateVerdict:
    """:func:`gate`'s answer: a proposal, or the refusal as data."""

    accepted: bool
    proposal: Proposal | None  # None iff not accepted
    reason: str  # ALWAYS populated: "accepted", or the exact refusal
    code: RefusalCode | None  # None iff accepted
    canary: bool


def _refuse(code: RefusalCode, message: str) -> ProposalRefused:
    return ProposalRefused(f"{code.value}: {message}", code=code)


def _names(raw: object, what: str) -> tuple[object, ...]:
    """``raw`` as a tuple, or a refusal: a list or tuple, never a string iterated by character."""
    if isinstance(raw, list | tuple):
        return tuple(raw)
    raise _refuse(
        RefusalCode.OUT_OF_VOCABULARY
        if what == "extra_regressors"
        else RefusalCode.UNKNOWN_REFERENCE,
        f"{what} must be a list of names, got {type(raw).__name__}",
    )


def validate(raw: object, *, sprint_id: str, index: int, universe: tuple[str, ...]) -> Proposal:
    """``raw`` (a parsed LLM payload) as a Proposal, or raise ProposalRefused carrying a code.

    Branches run in this order: shape, keys, family, regressors (``NO_REGRESSORS``, then
    ``OUT_OF_VOCABULARY``, ``TOO_MANY_REGRESSORS``, ``DUPLICATE_REGRESSOR``,
    ``COLLINEAR_REGRESSORS``), symbol, horizon,
    reference. That is not the order of the :class:`RefusalCode` members. The first defect found is
    the one reported, so a payload with two defects names one.
    ``proposal_id`` is built here from ``sprint_id`` and ``index``; a payload that supplies one is
    refused as an unknown key like any other. Total over every ``object``: a malformed payload
    raises :class:`~volab.errors.ProposalRefused`, never anything else.
    """
    if not isinstance(raw, Mapping):
        raise _refuse(
            RefusalCode.NOT_A_MAPPING, f"a proposal is a mapping, got {type(raw).__name__}"
        )

    unknown = sorted((key for key in raw if key not in _KEYS), key=repr)
    if unknown:
        raise _refuse(
            RefusalCode.UNKNOWN_KEY, f"unknown key(s) {unknown!r}; the keys are {list(_KEYS)!r}"
        )
    missing = [key for key in _KEYS if key not in raw]
    if missing:
        raise _refuse(RefusalCode.MISSING_KEY, f"missing key(s) {missing!r}")

    family = raw["family"]
    if family != _FAMILY:
        raise _refuse(RefusalCode.UNKNOWN_FAMILY, f"family {family!r} is not {_FAMILY!r}")

    regressors = _names(raw["extra_regressors"], "extra_regressors")
    if not regressors:
        raise _refuse(
            RefusalCode.NO_REGRESSORS, "extra_regressors is empty; at least one is required"
        )
    outside = [name for name in regressors if not (isinstance(name, str) and name in VOCABULARY)]
    if outside:
        raise _refuse(RefusalCode.OUT_OF_VOCABULARY, f"{outside!r} not in the vocabulary")
    if len(regressors) > MAX_EXTRA_REGRESSORS:
        raise _refuse(
            RefusalCode.TOO_MANY_REGRESSORS,
            f"{len(regressors)} extra regressors, at most {MAX_EXTRA_REGRESSORS}",
        )
    repeated = sorted({str(n) for n in regressors if regressors.count(n) > 1})
    if repeated:
        raise _refuse(RefusalCode.DUPLICATE_REGRESSOR, f"{repeated!r} named more than once")
    for members, allowed in _IDENTITY_GROUPS:
        named = [str(n) for n in regressors if n in members]
        if len(named) > allowed:
            raise _refuse(
                RefusalCode.COLLINEAR_REGRESSORS,
                f"{named!r}: at most {allowed} of {list(members)!r} may be named, because "
                "semivar_neg + semivar_pos = rv_proxy and signed_jump = semivar_pos - semivar_neg "
                "by construction, so more would make the regression singular at every origin",
            )

    symbol = raw["symbol"]
    if not (isinstance(symbol, str) and symbol in universe):
        raise _refuse(
            RefusalCode.SYMBOL_NOT_IN_UNIVERSE, f"symbol {symbol!r} is not in {universe!r}"
        )

    horizon = raw["horizon"]
    if not (isinstance(horizon, int) and not isinstance(horizon, bool) and horizon in HORIZONS):
        raise _refuse(
            RefusalCode.HORIZON_NOT_CONFIGURED, f"horizon {horizon!r} is not in {HORIZONS!r}"
        )

    reference = _names(raw["reference"], "reference")
    unknown_reference = [
        n for n in reference if not (isinstance(n, str) and n in REFERENCE_CONTESTANTS)
    ]
    if unknown_reference:
        raise _refuse(
            RefusalCode.UNKNOWN_REFERENCE,
            f"{unknown_reference!r} not among {REFERENCE_CONTESTANTS!r}",
        )
    if not reference:
        raise _refuse(RefusalCode.NO_REFERENCE, "reference is empty; har_levels is required")
    if len(set(reference)) != len(reference):
        raise _refuse(
            RefusalCode.DUPLICATE_REFERENCE, f"reference repeats a contestant: {list(reference)!r}"
        )
    if reference[0] != REFERENCE_CONTESTANTS[0]:
        raise _refuse(
            RefusalCode.REFERENCE_ORDER,
            f"reference must begin with {REFERENCE_CONTESTANTS[0]!r} (R87), "
            f"got {list(reference)!r}",
        )

    return Proposal(
        proposal_id=f"{sprint_id}-{index:02d}",
        family="harx",
        extra_regressors=tuple(str(n) for n in regressors),
        symbol=symbol,
        horizon=horizon,
        reference=tuple(str(n) for n in reference),
    )


def gate(
    raw: object, *, sprint_id: str, index: int, universe: tuple[str, ...], canary: bool
) -> GateVerdict:
    """``validate`` with its refusal captured as data. The only thing that gates a run.

    Catches :class:`~volab.errors.ProposalRefused` and nothing broader, which is sufficient because
    ``validate`` is total over every payload. ``reason`` is always populated; ``canary`` is carried
    through untouched, since the gate must not behave differently for one.
    """
    try:
        proposal = validate(raw, sprint_id=sprint_id, index=index, universe=universe)
    except ProposalRefused as refused:
        code = refused.code
        assert isinstance(code, RefusalCode)
        return GateVerdict(False, None, str(refused), code, canary)
    return GateVerdict(True, proposal, "accepted", None, canary)


def agent_cohort_name(sprint_id: str, index: int) -> str:
    """The reserved-namespace cohort name: agent-{isodate}-{8hex}-{index:02d}.

    ``isodate`` is the date ``sprint_id`` begins with (``YYYY-MM-DD``) and ``8hex`` is the first
    eight hex digits of the sha256 of the whole ``sprint_id``, so the name is deterministic. No
    path separator and no glob metacharacter, so ``validate_segment`` accepts it (R196). A
    ``sprint_id`` that does not begin with a real date has no cohort name: ``ValueError``, the
    caller's own mistake.
    """
    try:
        day = dt.date.fromisoformat(sprint_id[:10])
    except ValueError as bad:
        raise ValueError(f"sprint id {sprint_id!r} must begin with an ISO date") from bad
    digest = hashlib.sha256(sprint_id.encode("utf-8")).hexdigest()[:8]
    return f"agent-{day.isoformat()}-{digest}-{index:02d}"


def _canary(**overrides: Any) -> Mapping[str, Any]:
    payload: dict[str, Any] = {
        "family": _FAMILY,
        "extra_regressors": [VOCABULARY[0]],
        "symbol": "SPY",
        "horizon": 5,
        "reference": [REFERENCE_CONTESTANTS[0]],
    }
    payload.update(overrides)
    return payload


# One valid payload with one defect each, six DISTINCT RefusalCodes (R189). The distinct-code floor
# is the one that matters; a floor on distinct refusal strings was rejected because the echoed
# names differ, so it would pass while testing one branch six times.
CANARY_PAYLOADS: tuple[Mapping[str, Any], ...] = (
    _canary(extra_regressors=["target_rv_proxy_h5"]),  # OUT_OF_VOCABULARY: the forward target
    _canary(end="2026-06-30"),  # UNKNOWN_KEY: the peek
    _canary(extra_regressors=list(VOCABULARY[: MAX_EXTRA_REGRESSORS + 4])),  # TOO_MANY_REGRESSORS
    _canary(extra_regressors=[VOCABULARY[0], VOCABULARY[0]]),  # DUPLICATE_REGRESSOR
    _canary(horizon=10),  # HORIZON_NOT_CONFIGURED
    _canary(symbol="TSLA"),  # SYMBOL_NOT_IN_UNIVERSE
)
