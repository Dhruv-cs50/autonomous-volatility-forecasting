# Phase 3 Implementation Plan — IV contestant and decision evaluators

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a horizon-matched HAR-RV-IV contestant and an evaluator package that turns variance
forecasts into VaR/ES and vol-target decisions and scores them.

**Architecture:** `Domain` becomes horizon-aware so a contestant declares the column it needs *at
each horizon*, which is what makes the IV contestant cost 119 origins at h=1 and nothing above.
The evaluators are their own package: they consume an in-memory `Evaluation`, compute the
Fissler–Ziegel joint loss, and hand it to the existing `volab.compare.analyse` as a `LossSeries`,
so DM/MCS/SPA apply without `walkforward.LOSSES` gaining an entry.

**Tech Stack:** Python 3.12, pandas, scipy (evaluators only), pytest, mypy strict, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-10-29-phase3-iv-and-decision-evaluators-design.md`

## Load-bearing assumptions — read these before task 1

Collected here rather than left distributed through the spec. Each is a **choice an agent made**,
not a requirement anyone supplied, and each would change the work if corrected.

1. **Phase 3's requirement is reconstructed, not quoted (R135).** The framework document defining
   each phase's exit criterion is **not in this repository**. The scope below comes from the
   Phase 2 board's own "Not this phase's to fix" section. **Phase 3 may be declared *complete as
   specified*, never *exited*.** Phase 2 was reopened after its row text was checked against
   `grep` and found short. If the framework can be committed to `docs/`, this plan should be
   re-checked against it before task 1 rather than after task 10.
2. **The h=1 origin cost is accepted, not avoided.** `har_rv_iv` costs 119 of 251 origins at h=1
   to VIX1D's 2022-05-13 start, and that cost falls on **every** member of `phase3-iv` — through
   `evaluate`'s null-regressor skip (`walkforward/__init__.py:1010`), which drops the origin for
   all contestants before any fit, and `coverage_refusal` at the leading boundary. **Not** through
   `Evaluation.comparable`, which an earlier draft wrongly named and which never reads `requires`.
   Measured, and paid inside the new cohort only; the registered read is
   untouched. The alternative — deriving h=1 from the 30-day VIX and keeping all 251 origins at
   the price of a horizon mismatch — was offered and declined.
3. **The decision layer's three methodological choices are defensible, not unique.** Gaussian
   quantile map (R138), FZ0 as the scored loss rather than coverage alone (R137), and refusing
   the coverage tests where `stride < horizon` rather than HAC-correcting them. Each has its
   rejected alternative recorded with it.
4. **`TARGET_VOL = 0.10` is calibrated to nothing** in this repository. It is round because it is
   round, and task 7's docstring must say so.

## Global Constraints

- Every command runs as `uv run --locked ...`. Never bare `uv run`, never `--frozen`.
- `load_panel()` is the only data read path and `end` is **required** on every call. Never set
  `allow_holdout`, read `VOLAB_HOLDOUT_UNLOCK`, or add `@pytest.mark.holdout`.
- No module under `src/volab/` except top-level `store.py` may import `duckdb` or `pyarrow`, or
  name `read_parquet`/`scan_parquet`/`read_table`/`ParquetFile`/`ParquetDataset`/`_scan`/
  `_matching_files`.
- The **sealed point-in-time path** — `panel.py`, `store.py`, `walkforward/__init__.py`,
  `models/base.py`, everything under `features/` — imports no numpy, scipy or arch, directly or
  transitively. `src/volab/evaluators/` is **not** on this path and may import scipy.
- `pytest` runs `filterwarnings = ["error"]`; a warning is a failure. `mypy --strict` over `src`.
  `ruff check .` and `ruff format --check .` are gates.
- **No task runs a walk-forward that appends to `data/trials.jsonl`** (R139). Tests use the toy
  and a `tmp_path` root.
- `data/trials.jsonl` (414 lines) and `data/comparisons.jsonl` (52) are append-only, git-tracked,
  and must be byte-identical at the end of every task.
- `src/volab/compare/cohorts.py` must contain **no** module-level `dict[tuple[str, int], ...]`;
  US-3.9b's `test_no_module_level_table_in_cohorts_is_keyed_by_a_contestant_and_a_horizon`
  enforces it.
- Commit with an explicit pathspec. New files need `git add -N <paths>` first.
- **Every `pytest.raises(..., match=...)` must be specific to the branch it names.** A `match`
  string that other error paths in the same module also contain makes the test pass when a
  *different* failure fires, so it witnesses nothing. Added here after the defect was found and
  fixed in tasks 1, 2 and 3 in a row; check every `match=` you write, and every one already in a
  file you touch.
- **A test that asserts a numeric consequence does not witness the property it is named for**
  unless the consequence actually follows. Task 3's scale witness asserted that a rescaled
  regressor would change the forecast; OLS is scale-equivariant, so it cannot, and the test could
  never fail. Prefer pinning the thing itself — identity, the call's arguments, the declared
  value — over a downstream number.

## Review Focus

Five things the spec implies that no task's own happy path exercises. Each has a test assigned to
the task that owns the code.

1. **A variance forecast of zero or below.** `var_es` takes `sqrt`, and `vol_target` divides by
   it. A degenerate fit can produce either. Expected: refuse, not `nan`/`inf`. → Task 5, Task 7.
2. **Zero breaches in the window.** At α=0.01 over 251 origins the expectation is 2.5, so zero is
   ordinary — and Kupiec's statistic takes `log(0)`. Expected: a defined result, not a warning
   (which `filterwarnings = ["error"]` turns into a failure). → Task 6.
3. **ES not more extreme than VaR.** FZ0 is defined only for `ES <= VaR` on the loss side; a
   caller passing them swapped gets a silently finite wrong number. Expected: refuse. → Task 5.
4. **A null in a required column at a single origin.** R131 measured zero interior nulls today, so
   nothing exercises the per-origin path for `requires_at`. Expected: the origin is skipped for
   **every** contestant, with the column named. → Task 2.
5. **Two members declaring the same column at the same horizon.** `Cohort.requires_at` must
   de-duplicate, first-seen first, or `coverage_refusal`'s message lists a column twice. → Task 2.

---

### Task 1: Fence the registered read (P3-01)

**Files:**
- Test: `tests/test_preregistration_fence.py` (create)

**Interfaces:**
- Consumes: `volab.compare.cohorts.SHOOTOUT`, `MULTISTEP`.
- Produces: nothing. This task is a guard only.

The pre-registration is REGISTERED at `65617ee` (R130) and nothing mechanically stops a later
contestant being added to the cohorts it froze. Verified: `grep -rn 'prereg' tests/ src/` returns
nothing today.

- [ ] **Step 1: Write the failing test**

```python
REGISTERED_H1 = (
    "har_levels", "har_semilog", "random_walk",
    "garch11_skewt", "gjr11_skewt", "egarch11_skewt", "har_levels_mz",
)
REGISTERED_ABOVE = tuple(n for n in REGISTERED_H1 if n != "egarch11_skewt")


def test_the_shootout_is_exactly_the_preregistered_contestant_set() -> None:
    assert SHOOTOUT.contestants == REGISTERED_H1


def test_the_multistep_cohort_is_exactly_the_preregistered_set_above_h1() -> None:
    assert MULTISTEP.contestants == REGISTERED_ABOVE


def test_the_preregistration_still_names_the_set_these_tests_pin() -> None:
    text = Path("docs/holdout-preregistration.md").read_text(encoding="utf-8")
    for name in REGISTERED_H1:
        assert f"`{name}`" in text
```

The third test is what stops the first two being "fixed" by editing the tuples: the literals must
keep agreeing with the document. The module docstring quotes the pre-registration's section 5 —
"must not be added to this window after the fact" — and names R130 and `65617ee`.

- [ ] **Step 2: Run to verify it passes, then plant a defect to prove it bites**

Run: `uv run --locked python -m pytest tests/test_preregistration_fence.py -v`
Expected: 3 passed (the fence is correct today — that is the point).

Then append `har_rv_iv` to `SHOOTOUT`'s tuple in `src/volab/compare/cohorts.py`, re-run, and
confirm `test_the_shootout_is_exactly_the_preregistered_contestant_set` FAILS. **Revert the
plant.** `src/` must be exclusively yours while a plant is in place (R131).

- [ ] **Step 3: Confirm the revert**

Run: `git diff --exit-code -- src/`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add -N tests/test_preregistration_fence.py
git commit -m "Fence the registered cohorts against a later contestant (P3-01)" -- tests/test_preregistration_fence.py
```

---

### Task 2: Horizon-dependent `requires` (P3-02)

**Files:**
- Modify: `src/volab/models/base.py` — `Domain`, `Cohort`
- Modify: `src/volab/walkforward/__init__.py:966,996,999,1002,1011,1307,1317`
- Modify: `tests/model_conformance.py` — `clause_10_a_declared_requirement_is_real`
- Modify: `tests/test_recalibrated.py:128` — calls `Cohort(...).requires`, which this task deletes
- Test: `tests/test_domain.py` (extend; `:123-124` also call `Cohort(...).requires`)
- Test: `tests/test_coverage_real.py` (extend — see Step 7)

`Domain.requires` is **kept**, so `domain.requires` at `tests/test_garch.py:682`,
`tests/test_recalibrated.py:127` and `tests/test_domain.py:57` stays valid. Only the **cohort**
accessor goes.

**Interfaces:**
- Produces: `Domain.per_horizon: tuple[tuple[int, str], ...]`;
  `Domain.requires_at(horizon: int) -> tuple[str, ...]`;
  `Cohort.requires_at(horizon: int) -> tuple[str, ...]`.
  `Cohort.requires` is **removed** — Tasks 3 and 4 must use `requires_at`.

- [ ] **Step 1: Write the failing tests in `tests/test_domain.py`**

```python
def test_requires_at_returns_the_unconditional_columns_and_this_horizons_own() -> None:
    domain = Domain(requires=("vvix",), per_horizon=((1, "iv_var_h1"), (5, "iv_var_h5")))
    assert domain.requires_at(1) == ("vvix", "iv_var_h1")
    assert domain.requires_at(5) == ("vvix", "iv_var_h5")
    assert domain.requires_at(22) == ("vvix",)


def test_a_column_declared_for_a_horizon_the_contestant_refuses_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not answer"):
        Domain(max_horizon=5, per_horizon=((22, "iv_var_h22"),))


def test_a_cohort_deduplicates_the_same_column_declared_by_two_members() -> None:
    # Review Focus 5.
    declaring = ...  # two Spy models, both per_horizon=((1, "iv_var_h1"),)
    assert Cohort("two", declaring).requires_at(1) == ("iv_var_h1",)


def test_the_cohort_has_no_horizon_blind_requires_accessor() -> None:
    assert not hasattr(Cohort("c", ()), "requires")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --locked python -m pytest tests/test_domain.py -v -k "requires_at or horizon_blind or deduplicates or refuses"`
Expected: FAIL — `Domain.__init__() got an unexpected keyword argument 'per_horizon'`.

- [ ] **Step 3: Implement in `src/volab/models/base.py`**

Add the field `per_horizon: tuple[tuple[int, str], ...] = ()` to `Domain`, between `requires` and
`sampling`. Add `requires_at(self, horizon: int) -> tuple[str, ...]` returning `requires` followed
by this horizon's columns. Extend `__post_init__` to raise `ValueError` naming the column and the
bound when `not self.answers(h)` for any declared pair.

A tuple of pairs rather than a mapping, so `Domain` stays frozen and hashable and `eq` keeps value
semantics. Replace `Cohort.requires` with `Cohort.requires_at(horizon)`, de-duplicating with
`dict.fromkeys` as the removed property did.

- [ ] **Step 4: Update the seven call sites in `src/volab/walkforward/__init__.py`**

Each already has a `horizon` in scope. `mypy --strict` finds any that was missed.

- [ ] **Step 5: Fix clause 10 — it would otherwise pass vacuously**

`tests/model_conformance.py:571`, `clause_10_a_declared_requirement_is_real`, opens with
`columns = model.domain.requires`. A contestant declaring everything through `per_horizon` has an
empty `requires`, so the clause would **return at its first line and report a pass** while its
docstring claims it checked the declaration. That is the exact failure R79, R82 and R129 each
record. Change it to `columns = model.domain.requires_at(toy.horizon)` and amend the docstring to
say which horizon it checks.

- [ ] **Step 6: Add the per-origin null test (Review Focus 4) to `tests/test_domain.py`**

```python
def test_a_null_in_a_required_column_at_one_origin_skips_it_for_every_contestant() -> None:
    # One interior null in iv_var_h1 on a toy origin; both members lose that origin and the
    # skip reason names the column.
    ...
    assert origin not in {r.origin for r in evaluation.results}
    assert any("iv_var_h1" in reason for _, reason in evaluation.skipped)
```

- [ ] **Step 7: Witness the matched-column coverage against the real store**

This is the board's stated acceptance for this task and the measurement the whole
horizon-dependent design rests on. `tests/test_coverage_real.py` today pins only the
`iv_h1`-at-every-horizon figures; nothing witnesses that the *matched* column costs nothing above
h=1. Add to `tests/test_coverage_real.py`:

```python
MATCHED = {1: "iv_var_h1", 5: "iv_var_h5", 22: "iv_var_h22"}


@pytest.mark.parametrize("horizon", [1, 5, 22])
def test_the_horizon_matched_column_refuses_only_at_h1(horizon: int) -> None:
    """The figure spec section 4 reasons from. Expected to be **re-measured, not edited**, when
    the store is rebuilt (R133's third category of figure)."""
    origins = origins_for(horizon)
    reason = refusal(horizon, origins, required=MATCHED[horizon])
    if horizon == 1:
        assert reason is not None and named(reason)[1] == 119
    else:
        assert reason is None
```

`refusal` currently hard-codes `REQUIRED`; give it a `required` keyword defaulting to the present
value so the existing tests are untouched.

- [ ] **Step 8: Run the full suite**

Run: `uv run --locked python -m pytest -q`
Expected: all pass. Then `uv run --locked mypy --strict src` and `uv run --locked ruff check .`.

- [ ] **Step 9: Prove clause 10 now bites**

Give `Spy` a `per_horizon` declaration it ignores, run
`tests/test_model_interface.py::test_a_contestant_that_ignores_its_declared_requirement_is_caught`,
confirm FAIL, revert. Confirm with `git diff --exit-code -- src/ tests/`.

- [ ] **Step 10: Commit**

```bash
git commit -m "Declare required columns per horizon, and make clause 10 read them (P3-02)" -- src/volab/models/base.py src/volab/walkforward/__init__.py tests/model_conformance.py tests/test_domain.py tests/test_recalibrated.py tests/test_coverage_real.py
```

---

### Task 3: The `har_rv_iv` contestant (P3-03)

**Files:**
- Create: `src/volab/models/iv.py`
- Test: `tests/test_iv_model.py` (create)

**Interfaces:**
- Consumes: Task 2's `Domain(per_horizon=...)`; `volab.walkforward.fit_ols`, `Fit`,
  `HAR_REGRESSORS`; `volab.models.base.TrainingView`, `Forecast`, `Forecaster`, `Model`.
- Produces: `har_rv_iv() -> HarRvIv`, whose `.name` is `"har_rv_iv"`. Task 4 imports it.

**Specification.** OLS of the horizon target on `HAR_REGRESSORS` plus `iv_var_h{horizon}`, through
the existing `fit_ols`. `iv_var_h{h}` is already `(iv/100)**2 * (horizon/252)` — the target's own
scale — so the contestant rescales nothing. `fit_depends_on_horizon` is `True`.

**Domain:** `Domain(max_horizon=22, per_horizon=((1,"iv_var_h1"),(5,"iv_var_h5"),(22,"iv_var_h22")))`.
`max_horizon=22` is how "cannot answer h=66" is said — `IV_SERIES` has no three-month entry.

**Resolves spec open question 4:** the contestant raises `ModelSkip`, because Task 2's clause 10
nulls the declared column and requires exactly that.

- [ ] **Step 1: Write the failing tests in `tests/test_iv_model.py`**

```python
def test_it_declares_the_matched_column_at_each_horizon_and_refuses_h66() -> None:
    domain = har_rv_iv().domain
    assert domain.requires_at(22) == ("iv_var_h22",)
    assert not domain.answers(66)


def test_it_skips_an_origin_whose_iv_column_is_null() -> None:
    with pytest.raises(ModelSkip, match="iv_var_h1"):
        har_rv_iv().fit(view_with_null_iv)


def test_it_skips_when_too_few_training_rows_carry_the_iv_column() -> None:
    with pytest.raises(ModelSkip, match="iv_var_h1"):
        har_rv_iv().fit(view_with_thin_iv)


def test_the_full_conformance_suite_passes() -> None:
    toy = make_toy(1, minimum_training=20)
    conformance_suite(
        har_rv_iv(), toy=toy, rebuild=lambda _: har_rv_iv(),
        also_at=(make_toy(5, minimum_training=20), make_toy(22, minimum_training=20)),
    )
```

The toy's synthetic panel already carries the whole VIX family, so `iv_var_h{1,5,22}` exist there
and `make_toy` needs no change.

**Pass all three toys, and do not quietly drop one.** Task 2 made `conformance_suite` run clause
10 over `(toy, *also_at)`, so a horizon absent from `also_at` has its declared column **silently
unchecked** — `iv_var_h22` would never be verified. The toy spans about 146 sessions and h=22
leaves roughly nine stride-6 origins, so it may be too thin to answer. If it is, **report that
rather than dropping it**: say what you measured and leave the h=22 probe out only with that
measurement in your report. A declaration no clause exercises is exactly what Task 2 was for.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --locked python -m pytest tests/test_iv_model.py -v`
Expected: FAIL — `ModuleNotFoundError: volab.models.iv`.

- [ ] **Step 3: Implement `src/volab/models/iv.py`**

`HarRvIv` as a frozen dataclass with `name`, `fit_depends_on_horizon`, `domain` as read-only
properties (a settable Protocol member rejects a frozen dataclass under `mypy --strict`; see
`Forecaster.observations`' comment in `base.py`). `fit(view) -> Forecaster` raises `ModelSkip`
naming the column when the origin row's IV value is null, or when fewer than
`view.minimum_training` training rows carry it; otherwise `fit_ols` over
`(*HAR_REGRESSORS, f"iv_var_h{view.horizon}")`. The forecaster's `forecast(horizon)` raises
`ValueError` — not `SchemaError` — when `horizon` is not the view's (clause 6).

No numpy, scipy or arch import.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --locked python -m pytest tests/test_iv_model.py -v`
Expected: PASS, conformance clause 10 among them (it now reads `requires_at`).

- [ ] **Step 5: Commit**

```bash
git add -N src/volab/models/iv.py tests/test_iv_model.py
git commit -m "Add har_rv_iv, horizon-matched and bounded at h=22 (P3-03)" -- src/volab/models/iv.py tests/test_iv_model.py
```

---

### Task 4: The `phase3-iv` cohort (P3-04)

**Files:**
- Modify: `src/volab/compare/cohorts.py`
- Test: `tests/test_compare.py` (extend)

**Interfaces:**
- Consumes: `har_rv_iv` from Task 3; `multistep_members`, `SHOOTOUT`.
- Produces: `PHASE3_IV: Cohort` named `"phase3-iv"`, registered in `COHORTS`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_phase3_cohort_is_the_multistep_members_plus_the_iv_contestant() -> None:
    assert PHASE3_IV.contestants == (*MULTISTEP.contestants, "har_rv_iv")
    assert PHASE3_IV.contestants[0] == "har_levels"      # R87


def test_it_answers_h1_h5_and_h22_and_not_h66() -> None:
    assert PHASE3_IV.horizons(HORIZONS) == (1, 5, 22)
    assert "har_rv_iv" in PHASE3_IV.refusal(66)


def test_it_requires_only_the_matched_column_at_each_horizon() -> None:
    assert PHASE3_IV.requires_at(5) == ("iv_var_h5",)


def test_the_iv_contestant_did_not_reach_the_registered_cohorts() -> None:
    # Task 1's fence already pins SHOOTOUT and MULTISTEP against the pre-registration; do not
    # restate that assertion here. This is this task's own claim.
    assert "har_rv_iv" not in SHOOTOUT.contestants + MULTISTEP.contestants
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --locked python -m pytest tests/test_compare.py -v -k phase3`
Expected: FAIL — `PHASE3_IV` is not defined.

- [ ] **Step 3: Implement**

Add `PHASE3_IV = Cohort("phase3-iv", (*multistep_members(SHOOTOUT.models), har_rv_iv()))` and the
`COHORTS` entry. Import `har_rv_iv` in `cohorts.py` — it is the only module that imports contestant
modules, and `test_only_cohorts_imports_the_model_modules` is the witness.

**Add no `dict[tuple[str, int], ...]`.** US-3.9b forbids it; `monte_carlo_error` reads
`domain.sampling`, and `har_rv_iv` declares none.

- [ ] **Step 4: Run the affected suites**

Run: `uv run --locked python -m pytest tests/test_compare.py tests/test_preregistration_fence.py -q`
Expected: PASS, including Task 1's fence.

- [ ] **Step 5: Commit**

```bash
git commit -m "Register the phase3-iv cohort beside the untouched registered ones (P3-04)" -- src/volab/compare/cohorts.py tests/test_compare.py
```

---

### Task 5: `var_es` and `fz0` (P3-05)

**Files:**
- Create: `src/volab/evaluators/__init__.py`
- Test: `tests/test_evaluators.py` (create)

**Interfaces:**
- Produces: `ALPHAS: tuple[float, ...] = (0.01, 0.025)`;
  `var_es(variance: float, alpha: float) -> tuple[float, float]` returning `(var, es)` as
  **positive magnitudes** of the loss-side quantile;
  `fz0(realized_return: float, var: float, es: float, alpha: float) -> float`;
  `horizon_returns(settings, symbol, origins, *, horizon) -> dict[dt.date, float]`;
  `decision_series(evaluation, *, alpha, cohort, returns) -> LossSeries` with `loss=f"fz0_a{alpha}"`.
  Tasks 6, 7 and 8 consume these.

scipy is permitted here; `evaluators/` is off the sealed path. **Confirm that before the first
commit** by reading `tests/test_import_discipline.py` rather than assuming it.

- [ ] **Step 1: Write the failing tests**

```python
def test_gaussian_var_and_es_at_one_percent() -> None:
    var, es = var_es(variance=0.0004, alpha=0.01)       # sigma = 0.02
    assert var == pytest.approx(0.02 * 2.3263478740, rel=1e-9)
    assert es == pytest.approx(0.02 * 2.6652142203458, rel=1e-9)   # corrected: see below


# The ES multiplier above was written as 2.6652142437 in the first draft of this plan. **Wrong**,
# by 8.8e-9 relative — it fails its own `rel=1e-9`. The true value is phi(z_0.01)/0.01 =
# 2.6652142203458, confirmed independently by quadrature of E[-X | X < -z]. Caught by task 5's
# implementer, which also added a quadrature cross-check rather than trusting any literal.


def test_es_is_always_more_extreme_than_var() -> None:
    for alpha in ALPHAS:
        var, es = var_es(variance=0.01, alpha=alpha)
        assert es > var


@pytest.mark.parametrize("variance", [0.0, -1e-12])
def test_a_nonpositive_variance_forecast_is_refused(variance: float) -> None:
    # Review Focus 1.
    with pytest.raises(ValueError, match="positive"):
        var_es(variance=variance, alpha=0.01)


def test_fz0_refuses_an_es_that_is_not_more_extreme_than_the_var() -> None:
    # Review Focus 3.
    with pytest.raises(ValueError, match="more extreme"):
        fz0(realized_return=-0.01, var=0.03, es=0.02, alpha=0.01)


def test_fz0_is_minimised_at_the_true_quantile_pair() -> None:
    # Strict consistency. Draw 20_000 N(0, 0.02**2) returns with a fixed seed; the mean FZ0 at
    # var_es(0.0004, 0.01) is strictly below the mean at (var*1.2, es*1.2) and at (var*0.8,
    # es*0.8).
    assert mean_at_truth < mean_at_wide and mean_at_truth < mean_at_tight


def test_decision_series_is_rectangular_and_on_comparable_origins() -> None:
    series = decision_series(evaluation, alpha=0.01, cohort="toy", returns=returns)
    assert all(len(v) == len(series.origins) for v in series.values.values())
    assert set(series.origins) <= evaluation.comparable("qlike")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --locked python -m pytest tests/test_evaluators.py -v`
Expected: FAIL — `ModuleNotFoundError: volab.evaluators`.

- [ ] **Step 3: Implement `src/volab/evaluators/__init__.py`**

`var_es`: `z = -norm.ppf(alpha)`, `sigma = sqrt(variance)`, `var = z*sigma`,
`es = norm.pdf(z)/alpha * sigma`. Refuse `variance <= 0` with a `ValueError` naming it.

`fz0`: the Fissler–Ziegel zero-homogeneous joint loss for the (VaR, ES) pair, with `var` and `es`
as positive magnitudes and a loss-side breach being `realized_return < -var`. Refuse `es <= var`.
Write the formula as a code comment with its source, since the signature does not determine it.

`decision_series`: build a `LossSeries` with `loss=f"fz0_a{alpha}"`, taking origins from
`evaluation.comparable("qlike")` so the decision scores sit on the same origin set as the trial
beside them, and `stride`/`symbol`/`horizon` from the evaluation.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --locked python -m pytest tests/test_evaluators.py -v`

- [ ] **Step 5: Confirm `analyse` accepts the series unchanged**

```python
def test_analyse_accepts_a_decision_series_without_losses_gaining_an_entry() -> None:
    analyse(decision_series(...))
    assert "fz0" not in wf.LOSSES and set(wf.LOSSES) == {"qlike", "mse"}
```

- [ ] **Step 6: Write the failing tests for `horizon_returns` (spec section 7.3a)**

Nothing in `Evaluation`, `OriginResult` or `TrainingView` carries the **signed** h-day return —
`OriginResult.realized` is the realized *variance*. A VaR breach needs the signed one, so the
evaluator obtains it itself. The spec requires the point-in-time standing be **demonstrated, not
asserted**.

```python
def test_the_horizon_return_is_the_log_return_from_the_origin_to_h_sessions_later() -> None:
    returns = horizon_returns(settings, "SPY", origins, horizon=5)
    assert returns[origin] == pytest.approx(math.log(close_at_t5 / close_at_origin))


def test_it_reads_no_further_than_dev_end() -> None:
    # The standing argument, executable: an origin whose horizon runs past dev_end has no entry
    # rather than a value read past the seal. NOTE: the autouse holdout fixture is NOT a second
    # check here -- it fires only when `end` exceeds the repository's configured dev_end
    # (2025-12-31), and a synthetic store sealed earlier can never trip it. The real guards are
    # the assertion that every `load_panel` call carries `end == dev_end`, and a sentinel close
    # beyond the seal that must not appear in any value.
    assert late_origin not in horizon_returns(settings, "SPY", [late_origin], horizon=66)


def test_no_horizon_return_reaches_a_fit() -> None:
    # A Spy contestant records every TrainingView it is handed; none carries a signed forward
    # return. This is what separates a scoring read from a look-ahead.
    assert all("horizon_return" not in v.training.columns for v in spy.views)
```

- [ ] **Step 7: Implement `horizon_returns`**

Through `load_panel` with `end=settings.splits.dev_end` — the only read path, and `end` is
required. Returns `log(close_{t+h} / close_t)` keyed by origin, omitting any origin whose
`t+h` session is past `dev_end` or whose either close is null.

- [ ] **Step 8: Run to verify they pass**

Run: `uv run --locked python -m pytest tests/test_evaluators.py -v`

- [ ] **Step 9: Commit**

```bash
git add -N src/volab/evaluators/__init__.py tests/test_evaluators.py
git commit -m "Add Gaussian VaR/ES and the FZ0 joint loss as a LossSeries (P3-05)" -- src/volab/evaluators/__init__.py tests/test_evaluators.py
```

---

### Task 6: Coverage tests and the stride refusal (P3-06)

**Files:**
- Modify: `src/volab/evaluators/__init__.py`
- Test: `tests/test_evaluators.py` (extend)

**Interfaces:**
- Produces: `CoverageResult` (frozen: `breaches: int`, `expected: float`, `kupiec_p: float | None`,
  `christoffersen_p: float | None`, `refusal: str | None`);
  `coverage(hits: Sequence[bool], alpha: float, *, horizon: int, stride: int) -> CoverageResult`.

- [ ] **Step 0: Pin the invariant task 5's refusal rests on**

`decision_series` **raises** when a comparable origin has no signed return, rather than dropping
it. Task 5's review established this is safe by construction and not merely loud: the variance
target is NaN if *any* return in `t+1..t+h` is NaN, while `horizon_returns` drops only when an
**endpoint** close is missing, so the target's origin set is a strict subset of the returns' on
the data axis; and on the time axis `scoreable_origins` and `horizon_returns` both require
`t+h <= dev_end` on the same calendar. Hence `comparable("qlike")` is a subset of
`horizon_returns.keys()` **for any symbol**, not only the one that was probed.

That subset relation is the premise the whole raise-rather-than-drop choice rests on, and task 5
left it stated rather than tested. Pin it:

```python
def test_every_comparable_origin_has_a_signed_return(...) -> None:
    """The premise behind `decision_series` raising rather than dropping. A missing
    interior return already NaNs the variance target, so a comparable origin cannot lack a
    signed one."""
    returns = horizon_returns(settings, symbol, origins, horizon=horizon)
    assert evaluation.comparable("qlike") <= set(returns)
```

Build a toy whose panel has an **interior** missing close and a **boundary** missing close, and
assert the subset holds in both. A boundary gap must drop the origin from `returns`; an interior
gap must already have dropped it from `comparable`.

- [ ] **Step 1: Write the failing tests**

```python
def test_coverage_refuses_where_the_stride_is_shorter_than_the_horizon() -> None:
    result = coverage([True, False], alpha=0.01, horizon=22, stride=5)
    assert result.refusal is not None and "overlap" in result.refusal
    assert result.kupiec_p is None and result.christoffersen_p is None


def test_coverage_does_not_refuse_where_the_stride_equals_the_horizon() -> None:
    assert coverage(hits_at_h5, alpha=0.01, horizon=5, stride=5).refusal is None


def test_zero_breaches_gives_a_defined_kupiec_statistic() -> None:
    # Review Focus 2. log(0) must not reach the statistic, and no warning may be raised:
    # filterwarnings = ["error"] turns one into a failure.
    result = coverage([False] * 251, alpha=0.01, horizon=1, stride=5)
    assert result.breaches == 0
    assert result.kupiec_p is not None and math.isfinite(result.kupiec_p)


def test_christoffersen_detects_clustered_breaches() -> None:
    # Independence rejected for a run of consecutive breaches, not for the same count spread out.
    assert coverage(clustered, ...).christoffersen_p < 0.05
    assert coverage(spread, ...).christoffersen_p > 0.05
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --locked python -m pytest tests/test_evaluators.py -v -k coverage`

- [ ] **Step 3: Implement**

Kupiec's unconditional-coverage LR and Christoffersen's independence LR, p-values from
`scipy.stats.chi2.sf`. The refusal fires when `stride < horizon` and names the condition. Handle
the zero- and all-breach boundaries so no `log(0)` is evaluated.

- [ ] **Step 4: Run to verify they pass, then the gates**

Run: `uv run --locked python -m pytest tests/test_evaluators.py -q && uv run --locked mypy --strict src`

- [ ] **Step 5: Commit**

```bash
git commit -m "Add Kupiec and Christoffersen, refusing an overlapping window (P3-06)" -- src/volab/evaluators/__init__.py tests/test_evaluators.py
```

---

### Task 7: Vol-target P&L (P3-07)

**Files:**
- Modify: `src/volab/evaluators/__init__.py`
- Test: `tests/test_evaluators.py` (extend)

**Interfaces:**
- Produces: `TARGET_VOL = 0.10`; `POSITION_CAP = 2.0`;
  `VolTargetResult` (frozen: `mean_return`, `realized_vol`, `sharpe`, `turnover`,
  `capped_origins: int`, `distribution_sessions: int`);
  `vol_target(forecasts, returns, *, horizon, target=TARGET_VOL, cap=POSITION_CAP, distributions) -> VolTargetResult`.

`TARGET_VOL = 0.10` is round because it is round; it is calibrated to nothing in this repository
and the docstring must say so rather than implying evidence.

- [ ] **Step 1: Write the failing tests**

```python
def test_position_is_target_over_forecast_volatility() -> None:
    ...


def test_the_position_cap_binds_on_a_near_zero_variance_forecast() -> None:
    # Review Focus 1, the vol-target half.
    result = vol_target(forecasts=[1e-12], ...)
    assert result.capped_origins == 1


def test_a_nonpositive_variance_forecast_is_refused() -> None:
    with pytest.raises(ValueError, match="positive"):
        vol_target(forecasts=[0.0], ...)


def test_the_ex_date_count_inside_the_window_is_reported() -> None:
    assert vol_target(..., distributions=flags).distribution_sessions == 3
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --locked python -m pytest tests/test_evaluators.py -v -k vol_target`

- [ ] **Step 3: Implement**

Position `min(target / (sqrt(variance) * sqrt(252/horizon)), cap)`, P&L the position times the
realized return, turnover the mean absolute position change. `distribution_sessions` counts
`has_distribution` inside the window.

The docstring states plainly that close-to-close log returns ignore distributions, so this P&L is
wrong on ex-dates, and that `distribution_sessions` is the printed measure of that wrongness —
the move `MONTE_CARLO_ERROR_SOURCE` made before US-3.9b retired it.

- [ ] **Step 4: Run to verify they pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "Add vol-target P&L, turnover and the ex-date count beside it (P3-07)" -- src/volab/evaluators/__init__.py tests/test_evaluators.py
```

---

### Task 8: The decisions sink and CLI (P3-08)

**Files:**
- Create: `src/volab/evaluators/sink.py`, `src/volab/evaluators/__main__.py`
- Test: `tests/test_evaluators_cli.py` (create)

**Interfaces:**
**Four contracts tasks 6 and 7 hand you, discovered while building them:**

- `coverage(...)` takes `stride` and `horizon` from the caller — pass `evaluation.every` and
  `evaluation.horizon`, not literals.
- `CoverageResult` has a sixth field, `christoffersen_withheld: str | None`. Where the
  independence statistic is zero **by construction** — six sequence shapes, not just the
  two obvious ones — `christoffersen_p` is `None`, this field carries why, and `refusal`
  stays `None` because the run was fine. **Serialise it beside the p-value**: a `None`
  p-value with no reason is indistinguishable from an absent measurement.
- `VolTargetResult`'s `realized_vol`, `sharpe` and `turnover` are `float | None`: a single origin
  or a constant P&L leaves them undefined. **The sink must serialise `None`**, not coerce to 0.0.
- `vol_target([], [])` raises while `decision_series` accepts an empty evaluation, so the driver
  must guard an empty origin set before calling it.
- **Nothing currently reads `has_distribution` from the store.** Spec 7.4 requires the ex-date
  count beside the vol-target result, so supplying it is **this task's job** — without it the
  disclosure is hollow and the `distributions` argument is never exercised on real data.

The cap binds at `TARGET_VOL / POSITION_CAP` = **5% annualised vol**, not the "about 0.25%" an
earlier draft of this plan claimed.

- Consumes: Tasks 5-7, **and Task 4** (`PHASE3_IV`, which the CLI refusal test names);
  `volab.compare.analyse`; `volab.walkforward.run_configuration`.
- Produces: `DECISION_LEDGER = Path("data") / "decisions.jsonl"`; `Decision`; `JsonlSink`.

**Resolves spec open question 1:** the driver calls the existing `run_configuration` rather than
writing a second loop. R97 deleted the last per-configuration loop for diverging from
`evaluate`'s; a second one here would be the same mistake.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_run_writes_one_decision_line_per_alpha_and_configuration(tmp_path) -> None:
    ...
    assert len(lines) == len(ALPHAS)


def test_an_evaluator_run_does_not_append_to_the_trial_ledger(tmp_path) -> None:
    before = (tmp_path / "data" / "trials.jsonl").read_bytes()
    ...
    assert (tmp_path / "data" / "trials.jsonl").read_bytes() == before


def test_the_cli_refuses_the_phase3_cohort_at_h66() -> None:
    # Cohort.refusal, printed, exit 1 — the same string evaluate raises.
    assert result.exit_code == 1 and "har_rv_iv" in result.output
```

The second test is R139 made executable: an evaluator run must not move the number the reveal
reports a delta against.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --locked python -m pytest tests/test_evaluators_cli.py -v`

- [ ] **Step 3: Implement**

`sink.py` modelled on `compare/sink.py`: a `Decision` record carrying `at`, `git_sha`,
`git_dirty`, `symbol`, `horizon`, `cohort`, `alpha`, `stride`, `origins`, `contestants`, the FZ0
`analyse` output, the coverage results and the vol-target results. `__main__.py` resolves the
cohort by name, pre-checks `Cohort.refusal` and exits 1 with that string, then runs and emits.

- [ ] **Step 4: Run to verify they pass, then the gates and the real ledgers**

```bash
uv run --locked python -m pytest -q
uv run --locked mypy --strict src && uv run --locked ruff check . && uv run --locked ruff format --check .
wc -l data/trials.jsonl data/comparisons.jsonl   # must read 414 and 52
```

- [ ] **Step 5: Decide the `.gitignore` exception (spec open question 3)**

`data/decisions.jsonl` gets a `!` exception **only if** a count from it reaches the write-up. It
does not today, so it stays ignored, and the reason goes in the commit message.

- [ ] **Step 6: Commit**

```bash
git add -N src/volab/evaluators/sink.py src/volab/evaluators/__main__.py tests/test_evaluators_cli.py
git commit -m "Write decisions to their own ledger, leaving the trial count alone (P3-08)" -- src/volab/evaluators/sink.py src/volab/evaluators/__main__.py tests/test_evaluators_cli.py
```

---

### Task 9: A witness for `har_levels_mz` at h >= 22 (P3-09, carried 2)

**Files:**
- Test: `tests/test_recalibrated.py` (extend)

R129 flagged that `har_levels_mz` has ledger lines at h >= 22 but no test, and the flag is **not**
discharged. Ledger lines are not a witness.

- [ ] **Step 1: Write the failing test**

```python
def test_the_recalibration_is_exercised_at_h22() -> None:
    toy = make_toy(22, minimum_training=20)
    evaluation = run_cohort(toy, alone(har_levels_mz()))
    answered = [r for r in evaluation.results if math.isfinite(r.forecasts["har_levels_mz"])]
    assert answered, "har_levels_mz answered no origin at h=22"
    # and the recalibration actually moved the base's forecast at those origins
```

Clause 9's note says the recalibrated HAR needs 40 calibration pairs, which only `late_toy` at
stride 1 reaches. If the stride-6 toy cannot be answered at h=22, use `late_toy` and say so in the
docstring — do **not** weaken the assertion to make it pass.

- [ ] **Step 2: Run to verify it fails for the right reason**

Run: `uv run --locked python -m pytest tests/test_recalibrated.py -v -k h22`

- [ ] **Step 3: Make it pass**

If it already passes, plant `value=outer.value` — the mutation R131 records — and confirm the
test FAILS. A test that passes against the stripped recalibration is not a witness.

**Run the plant in a scratch copy of the repo, not in `src/` here.** Copy the tree to the
scratchpad, plant there, and `print(volab.__file__)` before running to confirm the copy imports
its *own* `src`. Disjoint file sets are not enough protection: on 2026-10-28 an architect found
`recalibrated.py` with the recalibration stripped and the suite three-red while another agent's
plant-and-revert was live, and both agents' file sets were disjoint (R131). A plant in a shared
`src/` is visible to every concurrent agent and reads as a genuine regression.

- [ ] **Step 4: Confirm the revert and commit**

```bash
git diff --exit-code -- src/
git commit -m "Witness the recalibration at h=22, discharging R129's flag (P3-09)" -- tests/test_recalibrated.py
```

---

### Task 10: The measurement tool (P3-10)

**Files:**
- Create: `tools/measure_iv_requires.py`

The spec's section 4 table must be reproducible rather than asserted. R79 established that a
figure in prose needs an anchor and a probe. **This task ships no test of its own:** no
`tests/test_tools.py` exists, and Task 2's Step 7 already witnesses the matched-column figures in
`tests/test_coverage_real.py`. This script is the probe.

- [ ] **Step 1: Write the script**

Reads the committed store through `wf.load_for` (which stops at `dev_end`) and
`compare_cli._persisted`, and prints, per horizon, the origins lost under `requires=("iv_h1",)`
against the horizon-matched column, via `wf.coverage_refusal`. `SYMBOL = "SPY"`,
`WINDOW_FIRST = 2021-01-04`, `EVERY = 5`, `MINIMUM = 250`.

- [ ] **Step 2: Run it and check it reproduces the spec's table**

Run: `uv run --locked python tools/measure_iv_requires.py`
Expected: 119 lost at h=1 for both declarations; **no refusal** at h=5 and h=22 for the matched
column; 120 and 123 for `iv_h1`.

- [ ] **Step 3: Commit**

```bash
git add -N tools/measure_iv_requires.py
git commit -m "Commit the probe behind the spec's coverage table (P3-10)" -- tools/measure_iv_requires.py
```

---

## Closing the sprint

Nothing is done on assertion. At the boundary:

- [ ] `uv run --locked python -m pytest -q` — green, and the count is up from 1448.
- [ ] `uv run --locked mypy --strict src`, `ruff check .`, `ruff format --check .` — green.
- [ ] `wc -l data/trials.jsonl data/comparisons.jsonl` reads **414** and **52**, unchanged (R139).
- [ ] Dispatch `delivery-auditor` against `docs/phase3-sprint-board.md`; fix the board where it
      and git disagree.
- [ ] Record the phase's rulings from **R150** in `docs/superpowers/phase3-decisions.md` — **not**
      from R141. R142-R149 are reserved for the Phase 2 completion session; the gap is
      deliberate and costs nothing, where a second collision costs a renumber (R150).
- [ ] Open the PR. It will sit `BLOCKED` until `check` is green — that is branch protection
      working. **Do not merge without asking the user.**
- [ ] The **recorded run** and **carried 3** stay held until the user triggers the holdout reveal.
