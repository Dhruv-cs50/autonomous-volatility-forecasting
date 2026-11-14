"""The closed vocabulary, ``Proposal``, ``validate`` and ``gate`` (P5-B2, R184, R185, R189, R192).

What these tests are for. The agent must not be able to *express* a peek, so ``validate`` is a
positive allowlist and refuses unknown keys rather than consulting a list of forbidden ones. This
module pins that, and pins the four literals ``vocabulary.py`` must carry because it may not
import anything that reaches ``volab.store``:

* ``HORIZONS`` against ``volab.features.targets.HORIZONS``;
* ``REFERENCE_CONTESTANTS`` against the real cohorts (imported here only -- tests may import
  anything);
* ``_EXACT_HAR_TRANSFORMS`` against ``fit_ols``: every vocabulary column is fittable beside the
  HAR base and every excluded name is refused. That witness runs twice -- on a synthetic frame,
  which is a real witness everywhere, and on the real ``har_daily`` table up to ``dev_end``. **The
  real-store version fails rather than skips when the store is absent** (the convention of
  ``test_coverage_real.py``), so in CI it is a witness only if the store is built there; the
  synthetic version is the one that always runs;
* ``AGENT_HORIZONS`` (P5-L13) by derivation rather than equality: the horizons whose ``n_eff``
  clears the 10% MCS floor on the candidate child's own window and stride, with the compare
  layer's own ``overlap_lag`` and ``effective_observations``. The derivation counts origins off the
  calendar, so it runs everywhere; a real-store test checks that count against the child's
  ``scoreable_origins`` and, like the fittability witness, fails rather than skips without a store.

Each of the first two pairs follows the idiom of ``test_preregistration_fence.py``: a literal in
the source and a derived truth in the test, so neither can be edited alone.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from typing import Any

import pandas as pd
import pytest
import real_panel

import volab.agent as agent
import volab.candidate as candidate
import volab.walkforward as wf
from volab.agent.vocabulary import (
    _EXACT_HAR_TRANSFORMS,
    _IDENTITY_GROUPS,
    _UNGROUPED,
    AGENT_COHORT_PATTERN,
    AGENT_HORIZONS,
    ALREADY_RUN,
    CANARY_PAYLOADS,
    HORIZONS,
    MAX_EXTRA_REGRESSORS,
    REFERENCE_CONTESTANTS,
    REPEATED_PROPOSAL,
    VOCABULARY,
    GateVerdict,
    Proposal,
    RefusalCode,
    agent_cohort_name,
    configuration_key,
    configuration_text,
    configurations_run,
    gate,
    validate,
)
from volab.compare.cohorts import COHORTS, SHOOTOUT
from volab.compare.dm import overlap_lag
from volab.compare.multiple import SIZES, effective_observations
from volab.config import load_settings
from volab.errors import ModelSkip, ProposalRefused, SchemaError, VolabError
from volab.features.build import FEATURE_COLUMNS, HAR_COLUMNS, WINDOWS
from volab.features.har import har_components
from volab.features.realized import (
    bipower_proxy,
    rv_proxy,
    semivariance_proxy,
    signed_jump_proxy,
)
from volab.features.targets import HORIZONS as REAL_HORIZONS
from volab.features.targets import target_column
from volab.models.base import TrainingView
from volab.models.harx import harx
from volab.paths import Tier, validate_segment
from volab.sessions import get_calendar, sessions_between
from volab.store import load_panel
from volab.walkforward import BASELINE_COHORT, fit_ols

UNIVERSE = ("SPY", "QQQ", "IWM")
SPRINT = "2026-11-10-sprint-one"


def good(**overrides: Any) -> dict[str, Any]:
    """A payload that validates, with ``overrides`` applied."""
    payload: dict[str, Any] = {
        "family": "harx",
        "extra_regressors": [VOCABULARY[0]],
        "symbol": "SPY",
        "horizon": 5,
        "reference": ["har_levels", "random_walk"],
    }
    payload.update(overrides)
    return payload


def refusal_of(raw: object) -> ProposalRefused:
    with pytest.raises(ProposalRefused) as caught:
        validate(raw, sprint_id=SPRINT, index=0, universe=UNIVERSE)
    return caught.value


# -- the three literals, each tied to its real source ------------------------------------------


def test_the_horizons_literal_equals_the_real_horizons() -> None:
    """``vocabulary`` cannot import ``volab.features.targets`` (it reaches ``volab.store``), so it
    carries a literal and this is the other side of it."""
    assert HORIZONS == REAL_HORIZONS


def _dev_window_origins(horizon: int) -> int:
    """How many of the candidate child's origins have a completed ``horizon`` target by ``dev_end``.

    The child's window and stride are read from ``volab.candidate``, never restated, and its
    origins come from ``origins_in`` as ``run_configuration``'s do. An origin is kept when the
    ``horizon``-th session after it is on or before ``dev_end``, which is when its target's
    ``as_of`` falls inside the window (``features/targets.py``) and so what ``scoreable_origins``
    keeps on a complete store. Calendar-only, so it runs everywhere; that it equals the child's
    count on the real store is the next test but one. It is a ceiling on the comparable origins:
    a model skip can only remove one (the third live sprint had 236 of these 238 at h=66).
    """
    settings = load_settings()
    calendar = get_calendar(settings)
    end = settings.splits.dev_end
    sessions = sessions_between(calendar, candidate.FIRST_ORIGIN, end)
    position = {day: i for i, day in enumerate(sessions)}
    origins = wf.origins_in(calendar, candidate.FIRST_ORIGIN, end, every=candidate.STRIDE)
    return sum(position[origin] + horizon < len(sessions) for origin in origins)


def test_the_offered_horizons_are_those_whose_ten_percent_mcs_the_dev_window_holds() -> None:
    """P5-L13: ``AGENT_HORIZONS`` is a literal (the inputs reach the store), and which horizons it
    should hold is a property of the window (R199), so it is derived here from the child's own
    origins and stride and the compare layer's own ``overlap_lag`` and ``effective_observations``.
    Moving ``dev_end``, ``FIRST_ORIGIN`` or ``STRIDE`` re-derives the set and fails this test until
    the literal follows. The floor ``n_eff < 2 / alpha`` is restated, as ``sink.py`` does, because
    the compare layer exports it as an expression in ``model_confidence_set`` and not a function.
    """
    size = float(agent._PROMOTION_MCS_SIZE)
    assert size in SIZES, "promotion reads an MCS size the compare layer does not report"
    n_eff = {
        h: effective_observations(_dev_window_origins(h), overlap_lag(h, candidate.STRIDE))
        for h in HORIZONS
    }
    holds = tuple(h for h in HORIZONS if n_eff[h] >= 2 / size)
    assert holds == AGENT_HORIZONS, n_eff
    assert set(AGENT_HORIZONS) <= set(HORIZONS)


def test_the_calendar_origin_count_is_the_childs_own_count_on_the_real_targets() -> None:
    """The derivation above counts origins off the calendar; the child counts them with
    ``scoreable_origins`` over the targets it reads to ``dev_end``. On the real store the two agree
    for every symbol and horizon (measured 2026-11-13: 251, 250, 247 and 238), so the derivation is
    the child's count rather than a model of it. Fails rather than skips when the store is absent,
    like the real-store fittability witness in this module."""
    settings = real_panel.settings()
    end = settings.splits.dev_end
    for symbol in settings.universe:
        for horizon in HORIZONS:
            targets = wf.load_targets(settings, symbol, horizon, end=end)
            scoreable = wf.scoreable_origins(
                settings, targets, first=candidate.FIRST_ORIGIN, last=end, every=candidate.STRIDE
            )
            assert len(scoreable) == _dev_window_origins(horizon), (symbol, horizon)


def test_the_reference_contestants_are_real_and_har_levels_leads_them() -> None:
    """Every literal name resolves to a contestant of a real cohort, and ``har_levels`` is first
    in both the literal and the shootout (R87: the first contestant's ``observations`` is
    ``OriginResult.training_rows``). Renaming a contestant, or the literal, fails this."""
    real = {name for cohort in COHORTS.values() for name in cohort.contestants}
    real |= set(BASELINE_COHORT.contestants)
    assert set(REFERENCE_CONTESTANTS) <= real
    assert len(set(REFERENCE_CONTESTANTS)) == len(REFERENCE_CONTESTANTS)
    assert REFERENCE_CONTESTANTS[0] == "har_levels" == SHOOTOUT.contestants[0]


def test_the_vocabulary_is_the_feature_columns_minus_the_har_base_and_its_transforms() -> None:
    excluded = {*_EXACT_HAR_TRANSFORMS, *HAR_COLUMNS}
    assert tuple(c for c in FEATURE_COLUMNS if c not in excluded) == VOCABULARY
    assert set(FEATURE_COLUMNS) >= excluded, "an excluded name that is not a feature"
    assert len(VOCABULARY) == len(set(VOCABULARY))


# The committed vocabulary (R214). ``VOCABULARY`` is derived from ``FEATURE_COLUMNS``, so a column
# added to the feature engine would widen what the agent may select with no review at all. This
# literal is the review: widening the vocabulary means editing it here, in a diff somebody reads.
_PINNED_VOCABULARY: tuple[str, ...] = (
    "semivar_neg_proxy_d1",
    "semivar_pos_proxy_d1",
    "signed_jump_proxy_d1",
    "semivar_neg_proxy_d5",
    "semivar_pos_proxy_d5",
    "signed_jump_proxy_d5",
    "bipower_proxy_d5",
    "semivar_neg_proxy_d22",
    "semivar_pos_proxy_d22",
    "signed_jump_proxy_d22",
    "bipower_proxy_d22",
    "rv_proxy_d66",
    "semivar_neg_proxy_d66",
    "semivar_pos_proxy_d66",
    "signed_jump_proxy_d66",
    "bipower_proxy_d66",
    "iv_h1",
    "iv_h5",
    "iv_h22",
    "iv_var_h1",
    "iv_var_h5",
    "iv_var_h22",
    "vvix",
    "baa10y",
    "dgs10",
    "t10y2y",
    "has_distribution",
)


def test_the_vocabulary_equals_the_committed_snapshot_so_widening_is_a_reviewed_edit() -> None:
    """The derivation test above recomputes the derivation, so it cannot fail when the feature
    engine grows. This one can: a new feature column changes ``VOCABULARY`` and fails here until
    someone edits ``_PINNED_VOCABULARY`` and a reviewer reads the new name."""
    assert VOCABULARY == _PINNED_VOCABULARY, (
        "the agent's vocabulary changed. If that is deliberate, edit _PINNED_VOCABULARY; "
        f"added={sorted(set(VOCABULARY) - set(_PINNED_VOCABULARY))}, "
        f"removed={sorted(set(_PINNED_VOCABULARY) - set(VOCABULARY))}"
    )


def test_the_vocabulary_names_no_target_column() -> None:
    """Derived, not typed: the target set is computed from ``volab.features.targets``. A positive
    allowlist rejects ``target_rv_proxy_h5`` and every name nobody anticipated; this is the
    witness that the allowlist itself admits no forward quantity (R185)."""
    targets = {target_column(h) for h in REAL_HORIZONS}
    assert set(VOCABULARY) & targets == set()


# -- the derived witness for _EXACT_HAR_TRANSFORMS ---------------------------------------------


def _synthetic_frame(rows: int = 120) -> pd.DataFrame:
    """Independent noise for every vocabulary column; the HAR base built exactly as
    ``features/har.py`` builds it, so the three excluded names are exact transforms of it."""
    import numpy as np

    rng = np.random.default_rng(7)
    frame = pd.DataFrame({name: rng.normal(size=rows) for name in VOCABULARY}, index=range(rows))
    d1 = pd.Series(rng.uniform(1e-5, 1e-3, size=rows))
    d5 = d1.rolling(5).sum()
    d22 = d1.rolling(22).sum()
    frame["rv_proxy_d1"], frame["rv_proxy_d5"], frame["rv_proxy_d22"] = d1, d5, d22
    frame["har_d_proxy"], frame["har_w_proxy"], frame["har_m_proxy"] = d1 / 1, d5 / 5, d22 / 22
    frame["y"] = rng.normal(size=rows)
    return frame


def _assert_fittable_exactly_when_allowed(
    frame: pd.DataFrame, response: str, constant: frozenset[str] = frozenset()
) -> None:
    """Every vocabulary column fits beside the HAR base, with no exemption except ``constant``:
    columns derived from the data as constant in ``frame``, which are collinear with the intercept
    and asserted to be refused instead (see the module docstring of ``vocabulary``). Each name in
    ``_EXACT_HAR_TRANSFORMS`` must be refused."""
    for column in VOCABULARY:
        if column in constant:
            with pytest.raises(SchemaError):
                fit_ols(frame, [*HAR_COLUMNS, column], response)
        else:
            fit_ols(frame, [*HAR_COLUMNS, column], response)  # must not raise SchemaError
    for column in sorted(_EXACT_HAR_TRANSFORMS):
        with pytest.raises(SchemaError):
            fit_ols(frame, [*HAR_COLUMNS, column], response)


def test_no_vocabulary_column_is_a_member_of_the_har_base() -> None:
    assert not set(VOCABULARY) & set(HAR_COLUMNS)


def test_every_vocabulary_column_is_fittable_beside_the_har_base_on_a_synthetic_frame() -> None:
    """The witness that always runs: every vocabulary column not itself in the base fits beside
    it, and each excluded name makes the design singular; ``_EXACT_HAR_TRANSFORMS`` is derived."""
    _assert_fittable_exactly_when_allowed(_synthetic_frame(), "y")


def test_every_vocabulary_column_is_fittable_beside_the_har_base_on_the_real_panel() -> None:
    """The same witness on the real ``har_daily`` table, read only to ``dev_end``. Fails (does not
    skip) if the store is not built, so it is a witness only where the store exists. The response
    is the next session's ``rv_proxy_d1``: any column will do, the singularity is in the
    regressors."""
    config = real_panel.settings()
    frame = load_panel(
        "har_daily",
        end=config.splits.dev_end,
        symbols=["SPY"],
        tier=Tier.FEATURES,
        settings=config,
    )
    frame = frame.reset_index(drop=True)
    frame["y"] = frame["rv_proxy_d1"].shift(-1)
    constant = frozenset(c for c in VOCABULARY if frame[c].nunique() <= 1)
    _assert_fittable_exactly_when_allowed(frame, "y", constant)


def _real_frame() -> pd.DataFrame:
    config = real_panel.settings()
    frame = load_panel(
        "har_daily", end=config.splits.dev_end, symbols=["SPY"], tier=Tier.FEATURES, settings=config
    ).reset_index(drop=True)
    frame["y"] = frame["rv_proxy_d1"].shift(-1)
    return frame


def test_a_column_constant_in_the_window_is_refused_as_a_model_skip_naming_it() -> None:
    """A constant column is not excluded from the vocabulary (constancy belongs to a window), so
    what is guaranteed is how it is refused: ``HarX.fit`` raises ``ModelSkip`` naming the column,
    not the ``SchemaError`` that would delete the origin for every contestant, and not a crash.
    The constant columns are derived from the real SPY table, not named; ``has_distribution`` is
    the live instance. Fails, rather than passing vacuously, if none is constant any more."""
    frame = _real_frame()
    constant = [c for c in VOCABULARY if frame[c].nunique() <= 1]
    assert constant, "no vocabulary column is constant in this window; this witness is vacuous"
    training, row = frame.iloc[:-1], frame.iloc[-1].drop("y")
    view = TrainingView(
        symbol="SPY",
        origin=dt.date(2025, 12, 31),
        horizon=1,
        response="y",
        training=training,
        row=row,
        returns=pd.Series(dtype="float64"),
        minimum_training=250,
        stride=1,
    )
    for column in constant:
        with pytest.raises(ModelSkip, match=column):
            harx((column,)).fit(view)


# -- the derived witness for _IDENTITY_GROUPS (R225) --------------------------------------------
#
# The first live sprint on the real store proposed semivar_neg, semivar_pos and signed_jump at d5
# three times; every one of 250 origins was refused as singular and nothing was compared. The
# identities below are definitions in ``features/realized.py``, not properties of a window, so the
# gate may refuse them statically (contrast R199). These tests build the columns with the feature
# engine's own functions, so a change to a definition breaks them rather than leaving a stale rule.

# The payload the configurator sent in sprint 2026-11-13T054207Z, verbatim in its regressors.
_FIRST_REAL_SPRINT_PAYLOAD: dict[str, Any] = {
    "family": "harx",
    "extra_regressors": ["semivar_neg_proxy_d5", "semivar_pos_proxy_d5", "signed_jump_proxy_d5"],
    "symbol": "SPY",
    "horizon": 5,
    "reference": ["har_levels"],
}


def _engine_frame(rows: int = 400) -> pd.DataFrame:
    """The realized columns and the HAR base, built from noise returns by the engine's functions."""
    import numpy as np

    rng = np.random.default_rng(11)
    returns = pd.Series(rng.normal(scale=0.01, size=rows))
    frame = pd.DataFrame(index=returns.index)
    for window in WINDOWS:
        frame[f"rv_proxy_d{window}"] = rv_proxy(returns, window=window)
        frame[f"semivar_neg_proxy_d{window}"] = semivariance_proxy(
            returns, window=window, sign="neg"
        )
        frame[f"semivar_pos_proxy_d{window}"] = semivariance_proxy(
            returns, window=window, sign="pos"
        )
        frame[f"signed_jump_proxy_d{window}"] = signed_jump_proxy(returns, window=window)
        if f"bipower_proxy_d{window}" in FEATURE_COLUMNS:
            frame[f"bipower_proxy_d{window}"] = bipower_proxy(returns, window=window)
    for name, component in har_components(returns).items():
        frame[f"{name}_proxy"] = component
    frame["y"] = rng.normal(size=rows)
    return frame.dropna().reset_index(drop=True)


def test_the_identity_groups_are_the_four_windows_written_out() -> None:
    """A literal beside the derivation, so a change to either is a reviewed edit."""
    assert _IDENTITY_GROUPS == (
        (("semivar_neg_proxy_d1", "semivar_pos_proxy_d1", "signed_jump_proxy_d1"), 1),
        (("semivar_neg_proxy_d5", "semivar_pos_proxy_d5", "signed_jump_proxy_d5"), 1),
        (("semivar_neg_proxy_d22", "semivar_pos_proxy_d22", "signed_jump_proxy_d22"), 1),
        (
            (
                "rv_proxy_d66",
                "semivar_neg_proxy_d66",
                "semivar_pos_proxy_d66",
                "signed_jump_proxy_d66",
            ),
            2,
        ),
    )
    for members, _ in _IDENTITY_GROUPS:
        assert set(members) <= set(VOCABULARY)


def test_one_more_than_allowed_from_a_group_is_singular_and_the_allowed_number_fits() -> None:
    """The rule against ``fit_ols`` on engine-built columns: every subset one larger than the
    group allows makes the design singular beside the HAR base, and every subset of the allowed
    size fits. If a feature definition changes so an identity no longer holds, this fails."""
    import itertools

    frame = _engine_frame()
    for members, allowed in _IDENTITY_GROUPS:
        for subset in itertools.combinations(members, allowed + 1):
            with pytest.raises(SchemaError):
                fit_ols(frame, [*HAR_COLUMNS, *subset], "y")
        for subset in itertools.combinations(members, allowed):
            fit_ols(frame, [*HAR_COLUMNS, *subset], "y")  # must not raise SchemaError


def test_the_groups_account_for_every_exact_dependency_among_the_realized_columns() -> None:
    """Completeness: the rank deficiency of [intercept, HAR base, every realized vocabulary
    column] is exactly what the groups predict, so no identity among these columns is unnamed."""
    import numpy as np

    frame = _engine_frame()
    realized = [c for c in VOCABULARY if c in frame.columns]
    columns = frame[[*HAR_COLUMNS, *realized]].to_numpy()
    scaled = (columns - columns.mean(axis=0)) / columns.std(axis=0)
    design = np.column_stack([np.ones(len(frame)), scaled])
    expected_deficiency = sum(len(members) - allowed for members, allowed in _IDENTITY_GROUPS)
    assert design.shape[1] - np.linalg.matrix_rank(design) == expected_deficiency


def test_the_first_real_sprints_payload_is_refused_as_collinear() -> None:
    refused = refusal_of(_FIRST_REAL_SPRINT_PAYLOAD)
    assert refused.code is RefusalCode.COLLINEAR_REGRESSORS
    assert "semivar_pos_proxy_d5" in str(refused) and "at most 1" in str(refused)


@pytest.mark.parametrize(("members", "allowed"), _IDENTITY_GROUPS)
def test_the_gate_refuses_exactly_the_subsets_that_are_singular(
    members: tuple[str, ...], allowed: int
) -> None:
    import itertools

    for size in range(1, len(members) + 1):
        for subset in itertools.combinations(members, size):
            payload = good(extra_regressors=list(subset))
            if size > allowed:
                assert refusal_of(payload).code is RefusalCode.COLLINEAR_REGRESSORS, subset
            else:
                validate(payload, sprint_id=SPRINT, index=0, universe=UNIVERSE)


def test_one_column_from_every_group_together_is_accepted() -> None:
    """The rule is per group: one from each window is four extras and no identity."""
    picks = [members[-1] for members, _ in _IDENTITY_GROUPS]
    validate(good(extra_regressors=picks), sprint_id=SPRINT, index=0, universe=UNIVERSE)


# -- validate: the accepted shape, then one test per RefusalCode -------------------------------


def _admissible(count: int) -> list[str]:
    """The first ``count`` vocabulary names in order, skipping any that would name more of an
    identity group than R225 allows: ``VOCABULARY[:5]`` itself holds the whole d1 group."""
    taken: list[str] = []
    for name in VOCABULARY:
        group = next(((m, a) for m, a in _IDENTITY_GROUPS if name in m), None)
        if group is None or sum(t in group[0] for t in taken) < group[1]:
            taken.append(name)
        if len(taken) == count:
            return taken
    raise AssertionError(f"the vocabulary has fewer than {count} admissible names")


def test_a_good_payload_becomes_a_frozen_proposal_with_a_harness_assigned_id() -> None:
    first, second = _admissible(2)
    proposal = validate(
        good(extra_regressors=[second, first]),
        sprint_id=SPRINT,
        index=3,
        universe=UNIVERSE,
    )
    assert isinstance(proposal, Proposal)
    assert proposal.proposal_id == f"{SPRINT}-03"
    assert proposal.extra_regressors == (second, first)
    assert proposal.reference == ("har_levels", "random_walk")
    assert (proposal.family, proposal.symbol, proposal.horizon) == ("harx", "SPY", 5)
    with pytest.raises(AttributeError):
        proposal.horizon = 22  # type: ignore[misc]


def test_a_proposal_id_the_model_supplies_is_refused_as_an_unknown_key() -> None:
    refused = refusal_of(good(proposal_id="mine"))
    assert refused.code is RefusalCode.UNKNOWN_KEY and "proposal_id" in str(refused)


def test_a_payload_that_is_not_a_mapping_is_refused() -> None:
    refused = refusal_of(["family", "harx"])
    assert refused.code is RefusalCode.NOT_A_MAPPING and "list" in str(refused)


def test_an_end_key_is_refused_by_being_unknown() -> None:
    """``end`` is the peek. It is refused because it is not one of the five known keys, not
    because it is on a list -- there is no such list -- so a key invented in 2027 is refused
    today by the same branch."""
    refused = refusal_of(good(end="2026-06-30"))
    assert refused.code is RefusalCode.UNKNOWN_KEY and "end" in str(refused)


@pytest.mark.parametrize("key", ["start", "every", "code", "cohort", "anything_invented_in_2027"])
def test_every_key_that_is_not_one_of_the_five_is_refused_as_unknown(key: str) -> None:
    refused = refusal_of(good(**{key: "x"}))
    assert refused.code is RefusalCode.UNKNOWN_KEY and key in str(refused)


def test_a_missing_key_is_refused_and_named() -> None:
    raw = good()
    del raw["horizon"]
    refused = refusal_of(raw)
    assert refused.code is RefusalCode.MISSING_KEY and "horizon" in str(refused)


def test_a_column_outside_the_vocabulary_is_refused_and_named() -> None:
    refused = refusal_of(good(extra_regressors=["definitely_not_a_column"]))
    assert refused.code is RefusalCode.OUT_OF_VOCABULARY
    assert "definitely_not_a_column" in str(refused)


def test_a_forward_target_column_is_refused_as_out_of_vocabulary() -> None:
    refused = refusal_of(good(extra_regressors=[target_column(5)]))
    assert refused.code is RefusalCode.OUT_OF_VOCABULARY and target_column(5) in str(refused)


@pytest.mark.parametrize("name", sorted(_EXACT_HAR_TRANSFORMS))
def test_an_exact_har_transform_is_refused_as_out_of_vocabulary(name: str) -> None:
    refused = refusal_of(good(extra_regressors=[name]))
    assert refused.code is RefusalCode.OUT_OF_VOCABULARY and name in str(refused)


def test_a_bare_string_for_the_regressors_is_refused_rather_than_iterated_by_character() -> None:
    refused = refusal_of(good(extra_regressors=VOCABULARY[0]))
    assert refused.code is RefusalCode.OUT_OF_VOCABULARY and "str" in str(refused)


def test_a_repeated_regressor_is_refused_and_named() -> None:
    refused = refusal_of(good(extra_regressors=[VOCABULARY[0], VOCABULARY[0]]))
    assert refused.code is RefusalCode.DUPLICATE_REGRESSOR and VOCABULARY[0] in str(refused)


def test_more_than_the_cap_of_regressors_is_refused_and_the_count_named() -> None:
    names = list(VOCABULARY[: MAX_EXTRA_REGRESSORS + 1])
    refused = refusal_of(good(extra_regressors=names))
    assert refused.code is RefusalCode.TOO_MANY_REGRESSORS
    assert str(len(names)) in str(refused) and str(MAX_EXTRA_REGRESSORS) in str(refused)


def test_exactly_the_cap_of_regressors_is_accepted() -> None:
    names = _admissible(MAX_EXTRA_REGRESSORS)
    proposal = validate(good(extra_regressors=names), sprint_id=SPRINT, index=0, universe=UNIVERSE)
    assert proposal.extra_regressors == tuple(names)


def test_no_regressors_is_refused() -> None:
    refused = refusal_of(good(extra_regressors=[]))
    assert refused.code is RefusalCode.NO_REGRESSORS


def test_a_symbol_outside_the_universe_is_refused_and_named() -> None:
    refused = refusal_of(good(symbol="TSLA"))
    assert refused.code is RefusalCode.SYMBOL_NOT_IN_UNIVERSE and "TSLA" in str(refused)


@pytest.mark.parametrize("horizon", [10, 0, True, 5.0, "5", None])
def test_a_horizon_that_is_not_a_configured_integer_is_refused(horizon: object) -> None:
    refused = refusal_of(good(horizon=horizon))
    assert refused.code is RefusalCode.HORIZON_NOT_CONFIGURED and repr(horizon) in str(refused)


def test_a_configured_horizon_whose_ten_percent_confidence_set_cannot_form_is_refused() -> None:
    """P5-L13: the third live sprint spent three proposals at h=66, and every comparison refused the
    10% MCS (``n_eff`` 16.9 against a floor of 20), so none could ever have been promoted. The
    refusal says why, since 66 *is* a configured horizon."""
    refused = refusal_of(good(horizon=66))
    assert refused.code is RefusalCode.HORIZON_NOT_CONFIGURED
    assert "66" in str(refused) and "n_eff" in str(refused)
    # The message's figures are literals in vocabulary.py; this ties them to the promotion size.
    size = float(agent._PROMOTION_MCS_SIZE)
    assert f"2/alpha = {2 / size:.0f} at alpha = {size:g}" in str(refused)


@pytest.mark.parametrize("horizon", [1, 5, 22])
def test_the_offered_horizons_are_accepted(horizon: int) -> None:
    proposal = validate(good(horizon=horizon), sprint_id=SPRINT, index=0, universe=UNIVERSE)
    assert proposal.horizon == horizon


def test_an_unknown_family_is_refused_and_named() -> None:
    refused = refusal_of(good(family="garch"))
    assert refused.code is RefusalCode.UNKNOWN_FAMILY and "garch" in str(refused)


def test_an_unknown_reference_contestant_is_refused_and_named() -> None:
    refused = refusal_of(good(reference=["har_levels", "oracle"]))
    assert refused.code is RefusalCode.UNKNOWN_REFERENCE and "oracle" in str(refused)


def test_a_reference_that_does_not_begin_with_har_levels_is_refused_as_misordered() -> None:
    refused = refusal_of(good(reference=["random_walk", "har_levels"]))
    assert refused.code is RefusalCode.REFERENCE_ORDER and "har_levels" in str(refused)


def test_an_empty_reference_is_refused_as_having_nothing_to_order() -> None:
    assert refusal_of(good(reference=[])).code is RefusalCode.NO_REFERENCE


@pytest.mark.parametrize("reference", ["har_levels", 5, None, {"har_levels": 1}])
def test_a_reference_that_is_not_a_list_is_refused_rather_than_iterated(reference: object) -> None:
    """The non-list branch of ``_names`` for ``reference``: a bare string must not be iterated by
    character, and the refusal says what was received."""
    refused = refusal_of(good(reference=reference))
    assert refused.code is RefusalCode.UNKNOWN_REFERENCE
    assert "reference must be a list of names" in str(refused)
    assert type(reference).__name__ in str(refused)


def test_a_repeated_reference_is_refused_and_named() -> None:
    refused = refusal_of(good(reference=["har_levels", "har_levels"]))
    assert refused.code is RefusalCode.DUPLICATE_REFERENCE and "har_levels" in str(refused)


def _payload_without(key: str) -> dict[str, Any]:
    raw = good()
    del raw[key]
    return raw


# One payload per code, and the code it must raise. The table is typed by hand; what the test below
# checks is that every payload really raises its code and that no member of the enum is missing.
_PAYLOAD_PER_CODE: dict[RefusalCode, object] = {
    RefusalCode.NOT_A_MAPPING: "not a mapping",
    RefusalCode.UNKNOWN_KEY: good(end="2026-06-30"),
    RefusalCode.MISSING_KEY: _payload_without("symbol"),
    RefusalCode.OUT_OF_VOCABULARY: good(extra_regressors=["no_such_column"]),
    RefusalCode.DUPLICATE_REGRESSOR: good(extra_regressors=[VOCABULARY[0], VOCABULARY[0]]),
    RefusalCode.TOO_MANY_REGRESSORS: good(
        extra_regressors=list(VOCABULARY[: MAX_EXTRA_REGRESSORS + 1])
    ),
    RefusalCode.NO_REGRESSORS: good(extra_regressors=[]),
    RefusalCode.COLLINEAR_REGRESSORS: good(
        extra_regressors=["semivar_neg_proxy_d5", "semivar_pos_proxy_d5"]
    ),
    RefusalCode.SYMBOL_NOT_IN_UNIVERSE: good(symbol="TSLA"),
    RefusalCode.HORIZON_NOT_CONFIGURED: good(horizon=10),
    RefusalCode.UNKNOWN_FAMILY: good(family="garch"),
    RefusalCode.UNKNOWN_REFERENCE: good(reference=["har_levels", "oracle"]),
    RefusalCode.REFERENCE_ORDER: good(reference=["random_walk", "har_levels"]),
    RefusalCode.NO_REFERENCE: good(reference=[]),
    RefusalCode.DUPLICATE_REFERENCE: good(reference=["har_levels", "har_levels"]),
}


def test_every_refusal_code_is_raised_by_a_payload_in_the_table() -> None:
    """Each code has a payload in ``_PAYLOAD_PER_CODE`` that ``validate`` refuses with exactly that
    code, and the table's keys are every member of the enum. It does not check that a separate,
    detailed test exists for each code: the per-branch tests above are the ones that assert what
    the refusal says."""
    assert set(_PAYLOAD_PER_CODE) == set(RefusalCode), "a code was added without a payload"
    for code, payload in _PAYLOAD_PER_CODE.items():
        assert refusal_of(payload).code is code, code


def test_a_refusal_is_a_volab_error_and_deliberately_not_a_value_error() -> None:
    """Ruling R30, recorded in ``ParseRefused``: a second base weakens the one-``except`` property
    and ``src/volab`` holds ``except ValueError`` sites that would swallow it."""
    refused = refusal_of("not a mapping")
    assert isinstance(refused, VolabError)
    assert not isinstance(refused, ValueError)


def test_validate_does_not_mutate_the_payload() -> None:
    raw = good()
    before = copy.deepcopy(raw)
    validate(raw, sprint_id=SPRINT, index=0, universe=UNIVERSE)
    assert raw == before


# -- gate: refusal as data ---------------------------------------------------------------------


def test_gate_accepts_a_good_payload_with_a_proposal_and_no_code() -> None:
    verdict = gate(good(), sprint_id=SPRINT, index=1, universe=UNIVERSE, canary=False)
    assert isinstance(verdict, GateVerdict)
    assert verdict.accepted and verdict.code is None and verdict.reason == "accepted"
    assert verdict.proposal is not None and verdict.proposal.proposal_id == f"{SPRINT}-01"
    assert verdict.canary is False


def _missing_horizon() -> dict[str, Any]:
    raw = good()
    del raw["horizon"]
    return raw


@pytest.mark.parametrize("raw", ["a string", ["a", "list"], None, _missing_horizon(), 7, b"x"])
def test_gate_never_raises_for_a_malformed_payload(raw: object) -> None:
    verdict = gate(raw, sprint_id=SPRINT, index=0, universe=UNIVERSE, canary=True)
    assert not verdict.accepted and verdict.proposal is None
    assert verdict.reason and verdict.reason != "accepted"
    assert isinstance(verdict.code, RefusalCode)
    assert verdict.canary is True


def test_gate_carries_the_refusal_of_validate_as_its_reason() -> None:
    raw = good(end="2026-06-30")
    verdict = gate(raw, sprint_id=SPRINT, index=0, universe=UNIVERSE, canary=False)
    assert verdict.reason == str(refusal_of(raw)) and verdict.code is RefusalCode.UNKNOWN_KEY


# -- the canary --------------------------------------------------------------------------------


def test_the_canary_payloads_are_refused_across_at_least_six_distinct_codes() -> None:
    """The floor that matters is on distinct *codes*. A floor on distinct refusal strings was
    rejected (R189): it would pass while exercising one branch six times, because the echoed names
    differ -- a floor that looks like evidence and is not."""
    verdicts = [
        gate(p, sprint_id=SPRINT, index=0, universe=UNIVERSE, canary=True) for p in CANARY_PAYLOADS
    ]
    assert len(CANARY_PAYLOADS) >= 6
    assert not any(v.accepted for v in verdicts)
    assert len({v.code for v in verdicts}) >= 6
    assert {
        RefusalCode.OUT_OF_VOCABULARY,
        RefusalCode.UNKNOWN_KEY,
        RefusalCode.TOO_MANY_REGRESSORS,
        RefusalCode.DUPLICATE_REGRESSOR,
        RefusalCode.HORIZON_NOT_CONFIGURED,
        RefusalCode.SYMBOL_NOT_IN_UNIVERSE,
    } == {v.code for v in verdicts}


def test_each_canary_is_one_defect_in_an_otherwise_valid_payload() -> None:
    """A canary that was also malformed some other way would be refused by an earlier branch and
    prove nothing about its own: removing the single defect must leave something acceptable."""
    for payload in CANARY_PAYLOADS:
        verdict = gate(payload, sprint_id=SPRINT, index=0, universe=UNIVERSE, canary=True)
        fixed = dict(payload)
        match verdict.code:
            case RefusalCode.UNKNOWN_KEY:
                fixed.pop("end")
            case RefusalCode.OUT_OF_VOCABULARY | RefusalCode.DUPLICATE_REGRESSOR:
                fixed["extra_regressors"] = [VOCABULARY[0]]
            case RefusalCode.TOO_MANY_REGRESSORS:
                fixed["extra_regressors"] = [VOCABULARY[0]]
            case RefusalCode.HORIZON_NOT_CONFIGURED:
                fixed["horizon"] = 5
            case RefusalCode.SYMBOL_NOT_IN_UNIVERSE:
                fixed["symbol"] = "SPY"
            case _:
                pytest.fail(f"unexpected canary code {verdict.code}")
        validate(fixed, sprint_id=SPRINT, index=0, universe=UNIVERSE)


def test_every_canary_not_about_the_horizon_names_an_offered_one() -> None:
    """P5-L13: a canary planted for another defect at h=66 would now carry two, and a broken branch
    of its own would go unnoticed behind the horizon refusal."""
    for payload in CANARY_PAYLOADS:
        verdict = gate(payload, sprint_id=SPRINT, index=0, universe=UNIVERSE, canary=True)
        if verdict.code is not RefusalCode.HORIZON_NOT_CONFIGURED:
            assert payload["horizon"] in AGENT_HORIZONS, payload


def test_no_canary_also_breaks_an_identity_group() -> None:
    """R225: a canary that named too many of one group would be refused as collinear even if its
    own branch broke, and the six-code floor would not notice. Counted with repeats, so a
    duplicate canary ``[x, x]`` counts ``x`` twice, as the gate does."""
    for payload in CANARY_PAYLOADS:
        named = payload["extra_regressors"]
        assert isinstance(named, list)
        for members, allowed in _IDENTITY_GROUPS:
            assert sum(name in members for name in named) <= allowed, (payload, members)


# -- the reserved cohort namespace -------------------------------------------------------------


def test_an_agent_cohort_name_matches_the_pattern_and_is_a_legal_path_segment() -> None:
    """R196: the namespace carries no glob metacharacter, so ``validate_segment`` accepts it if a
    cohort name ever becomes a path segment."""
    name = agent_cohort_name(SPRINT, 3)
    assert re.fullmatch(AGENT_COHORT_PATTERN, name)
    assert name.startswith("agent-2026-11-10-") and name.endswith("-03")
    assert validate_segment("cohort", name) == name


def test_an_agent_cohort_name_is_deterministic_and_varies_with_sprint_and_index() -> None:
    assert agent_cohort_name(SPRINT, 1) == agent_cohort_name(SPRINT, 1)
    assert agent_cohort_name(SPRINT, 1) != agent_cohort_name(SPRINT, 2)
    assert agent_cohort_name(SPRINT, 1) != agent_cohort_name("2026-11-10-other", 1)


def test_a_sprint_id_with_no_leading_date_has_no_cohort_name() -> None:
    with pytest.raises(ValueError):
        agent_cohort_name("sprint-one", 0)


@pytest.mark.parametrize("registered", [*COHORTS, BASELINE_COHORT.name])
def test_an_agent_cohort_name_is_never_a_registered_cohort_name(registered: str) -> None:
    assert not re.fullmatch(AGENT_COHORT_PATTERN, registered)
    assert agent_cohort_name(SPRINT, 0) != registered


# -- one configuration once per sprint (R227) ----------------------------------------------------


def _proposal(index: int = 0, **changes: object) -> Proposal:
    fields: dict[str, object] = {
        "proposal_id": f"2026-11-10-s-{index:02d}",
        "family": "harx",
        "extra_regressors": (_UNGROUPED[0], _UNGROUPED[1]),
        "symbol": "SPY",
        "horizon": 5,
        "reference": ("har_levels",),
    }
    fields.update(changes)
    return Proposal(**fields)  # type: ignore[arg-type]


def test_the_configuration_key_ignores_the_id_and_the_order_of_names() -> None:
    base = configuration_key(_proposal())
    assert configuration_key(_proposal(index=3)) == base
    assert configuration_key(_proposal(extra_regressors=(_UNGROUPED[1], _UNGROUPED[0]))) == base


@pytest.mark.parametrize(
    "changes",
    [
        {"extra_regressors": (_UNGROUPED[0],)},
        {"symbol": "QQQ"},
        {"horizon": 22},
        {"reference": ("har_levels", "har_semilog")},
    ],
)
def test_the_configuration_key_separates_any_change_to_what_is_run(
    changes: dict[str, object],
) -> None:
    assert configuration_key(_proposal(**changes)) != configuration_key(_proposal())


def test_the_repeat_code_is_not_a_refusal_code_because_no_single_payload_can_carry_it() -> None:
    """``validate`` sees one payload; a repeat needs two. So ``REPEATED_PROPOSAL`` is outside
    ``RefusalCode``, whose every member a single payload reaches (the per-code test above)."""
    assert REPEATED_PROPOSAL not in {code.value for code in RefusalCode}


# -- a configuration runs once across sprints (P5-L11) ------------------------------------------


def child_lines(
    cohort: str,
    extra: tuple[str, ...],
    *,
    symbol: str = "SPY",
    horizon: int = 5,
    reference: tuple[str, ...] = ("har_levels",),
    last_origin: str = "2025-12-17",
) -> list[dict[str, Any]]:
    """A cohort's agent-ledger lines in the child's shape: per loss, the reference in its order,
    then the candidate (``Evaluation.trials`` loops losses, then ``contestants``)."""
    models = (*reference, "harx+" + "+".join(extra))
    return [
        {
            "model": model,
            "symbol": symbol,
            "horizon": horizon,
            "loss": loss,
            "last_origin": last_origin,
            "cohort": cohort,
        }
        for loss in ("qlike", "mse")
        for model in models
    ]


COHORT_A = "agent-2026-11-13-1731f8f3-00"
COHORT_B = "agent-2026-11-13-1731f8f3-01"


def test_a_cohort_in_the_childs_shape_gives_back_the_configuration_it_ran() -> None:
    reference = ("har_levels", "random_walk", "har_semilog")  # not alphabetical, on purpose
    ran = configurations_run(
        child_lines(COHORT_A, (_UNGROUPED[1], _UNGROUPED[0]), horizon=22, reference=reference)
    )
    proposal = _proposal(horizon=22, reference=reference)
    assert ran == {configuration_key(proposal): COHORT_A}


def test_the_recovered_reference_keeps_the_order_it_ran_in() -> None:
    reference = ("har_levels", "random_walk", "har_semilog")
    ran = configurations_run(child_lines(COHORT_A, (_UNGROUPED[0],), reference=reference))
    reordered = _proposal(
        extra_regressors=(_UNGROUPED[0],), reference=reference[:1] + reference[:0:-1]
    )
    assert configuration_key(reordered) not in ran
    assert (
        configuration_key(_proposal(extra_regressors=(_UNGROUPED[0],), reference=reference)) in ran
    )


def test_a_configuration_run_by_two_cohorts_is_attributed_to_the_first() -> None:
    lines = [*child_lines(COHORT_A, (_UNGROUPED[0],)), *child_lines(COHORT_B, (_UNGROUPED[0],))]
    assert list(configurations_run(lines).values()) == [COHORT_A]


def test_the_candidate_name_the_parser_splits_is_the_one_the_model_gives_itself() -> None:
    """Two-sided: the parser reads ``harx+<a>+<b>`` because ``HarX.name`` writes it."""
    from volab.models.harx import harx

    extra = (_UNGROUPED[0], _UNGROUPED[1])
    ran = configurations_run(
        [
            {**line, "model": harx(extra).name} if line["model"].startswith("harx") else line
            for line in child_lines(COHORT_A, extra)
        ]
    )
    assert configuration_key(_proposal(extra_regressors=extra)) in ran


@pytest.mark.parametrize(
    "spoil",
    [
        pytest.param(
            lambda ls: [{k: v for k, v in ls[0].items() if k != "symbol"}, *ls[1:]], id="no-symbol"
        ),
        pytest.param(lambda ls: [{**ls[0], "horizon": 22}, *ls[1:]], id="two-horizons"),
        pytest.param(lambda ls: [{**ls[0], "horizon": True}, *ls[1:]], id="bool-horizon"),
        pytest.param(lambda ls: [{**ls[0], "symbol": "QQQ"}, *ls[1:]], id="two-symbols"),
        pytest.param(lambda ls: [{**ls[0], "model": None}, *ls[1:]], id="model-not-a-string"),
        pytest.param(lambda ls: [{**ls[0], "model": "garch"}, *ls[1:]], id="unknown-model"),
        pytest.param(lambda ls: [{**ls[0], "model": "harx+"}, *ls[1:]], id="empty-candidate"),
        pytest.param(lambda ls: [{**ls[0], "model": "harx+a++b"}, *ls[1:]], id="empty-name"),
        pytest.param(lambda ls: [{**ls[0], "model": "harx+other"}, *ls[1:]], id="two-candidates"),
        pytest.param(
            lambda ls: [line for line in ls if line["model"].startswith("har_")], id="no-candidate"
        ),
        pytest.param(
            lambda ls: [line for line in ls if line["model"].startswith("harx")], id="no-reference"
        ),
    ],
)
def test_a_cohort_that_does_not_parse_refuses_nothing_and_does_not_raise(spoil: Any) -> None:
    """Fail safe: a cohort the parser cannot read whole contributes no configuration, so it can
    refuse no proposal; dropping only its bad line could shorten the recovered reference and so
    match a configuration that was never run. A sound cohort beside it is still read."""
    spoiled = spoil(child_lines(COHORT_A, (_UNGROUPED[0],)))
    sound = child_lines(COHORT_B, (_UNGROUPED[1],))
    assert configurations_run([*spoiled, *sound]) == {
        configuration_key(_proposal(extra_regressors=(_UNGROUPED[1],))): COHORT_B
    }


def test_a_line_with_no_cohort_is_ignored() -> None:
    lines = [
        {"model": "harx+x", "symbol": "SPY", "horizon": 5},
        {"cohort": 7, "model": "har_levels"},
    ]
    assert configurations_run(lines) == {}


def test_the_canonical_text_is_sorted_json_of_the_five_payload_keys() -> None:
    key = configuration_key(_proposal(extra_regressors=(_UNGROUPED[1], _UNGROUPED[0])))
    text = configuration_text(key)
    assert json.loads(text) == {
        "family": "harx",
        "extra_regressors": sorted((_UNGROUPED[0], _UNGROUPED[1])),
        "symbol": "SPY",
        "horizon": 5,
        "reference": ["har_levels"],
    }
    assert text == json.dumps(json.loads(text), sort_keys=True)


def test_the_already_run_code_is_not_a_refusal_code_because_no_single_payload_can_carry_it() -> (
    None
):
    """Like ``REPEATED_PROPOSAL``: whether a configuration already ran is a fact about the ledger,
    not about one payload."""
    assert ALREADY_RUN not in {code.value for code in RefusalCode}
    assert ALREADY_RUN != REPEATED_PROPOSAL
