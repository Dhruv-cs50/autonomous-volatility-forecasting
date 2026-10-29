# Phase 3 — an implied-volatility contestant, and evaluators that score decisions

**Status:** design, approved in conversation on 2026-10-29, not yet implemented.
**Branch:** `phase-3-iv-and-evaluators`, cut from `phase-2-completion-groundwork`.
**Continues:** rulings from R134. Phase 3's own rulings begin at **R135** in
`docs/superpowers/phase3-decisions.md` — R134 is Phase 2's, recorded at `8e3b65b`.
**Merged `phase-2-completion-groundwork` at `7827af0`**, which brings US-3.9b (`761cf71`).

---

## 0. The requirement this phase is built against is a RECONSTRUCTION

Said first, because everything below inherits it.

**The framework document that defines each phase's exit criterion is not in this repository.**
Every phase-boundary claim in the decisions logs quotes a document nobody working here can read.
The user confirmed on 2026-10-28 that it is not to hand and said to proceed without it.

So the requirement below is assembled from what the repository itself records — the "Not this
phase's to fix, and carried forward" section of `docs/phase2-completion-sprint-board.md`, lines
226-228:

> **Phase 3 needs an options source and the pricing library**, neither of which is in this
> repository. Partial unblocks exist that do not: HAR-RV-IV can use the already-ingested VIX
> family, and the VaR/ES and vol-target evaluators need only forecasts and prices.

**This is not a quoted requirement and must not be treated as one.** Phase 2 was *reopened* after
its row text was checked against `grep` and GARCH, DM and MCS were found missing from `src/`.
Guessing an exit criterion is exactly how a phase gets declared done when it is not. Therefore:

- **Phase 3 may not be declared "exited" against this document.** It may be declared *complete as
  specified here*, which is a different and weaker claim, and any write-up must say which.
- The moment the framework can be committed into `docs/`, this section is replaced by the quoted
  row and the gap between the two is recorded as a ruling, whichever way it falls.

**Ask the user to commit the framework before the Phase 4 boundary.** Two consecutive phase
boundaries resting on memory is the condition under which R129's audit found six things wrong.

---

## 1. Scope

**In:**

1. A **HAR-RV-IV** contestant using the already-ingested implied-volatility feature columns.
2. **VaR/ES** and **vol-target** evaluators: machinery that turns a variance forecast into a
   decision and then scores the decision.
3. The two live items Phase 2 carried forward (section 8).

**Out, and stated so nobody re-derives it:**

- Anything needing an **options chain** or an **options pricing library**. Neither is in this
  repository and neither can be provisioned by an agent.
- **Any change to what the registered holdout read compares.** See section 3.

---

## 2. The four decisions the user took, and what each one closed

Recorded with the alternative that was rejected, because a decision whose alternative is not
written down gets re-litigated by the next agent.

| # | Decision | Rejected |
|---|---|---|
| D1 | Scope is the reconstruction **plus** both carried items | reconstruction alone; waiting for the framework |
| D2 | `Domain` gains **horizon-dependent `requires`** | one flat tuple; VIX-scaled h=1; two contestants |
| D3 | Evaluators are a **separate package whose FZ0 loss feeds the existing significance machinery** | reports only; `fz0` added to `walkforward.LOSSES` |
| D4 | The quantile map is **Gaussian only** | Student-t df=5; filtered historical simulation; both |
| D5 | **No Phase 3 run appends to `trials.jsonl` until the holdout reveal has been triggered** | running freely and letting the reveal's delta include Phase 3 |

### Why D4 is Gaussian and not something better-calibrated

A Gaussian map understates tails. That is not a defect of this design; it is the finding the
coverage tests are there to produce. Kupiec and Christoffersen measure exactly the understatement,
so under D4 it arrives as a *number* rather than as an assumption nobody tested. Choosing df=5
instead would have been a parameter with no evidence behind it in this repository, and tuning it
until coverage passed would be fitting the map to its own test. Filtered historical simulation is
the literature's answer and is deferred, not rejected: it needs a one-step forecast at every
session rather than every fifth, which is a stride-1 run this phase does not otherwise need.

### Why D3 keeps FZ0 out of `walkforward.LOSSES`

`LOSSES` is read by the driver loop, so an entry there makes **every future run of every cohort**
write a third trial line per contestant per configuration. `count_trials` counts
`data/trials.jsonl`, the pre-registration pins an expected count, and the reveal reports a second
count plus the delta. A third loss would move that number for reasons unrelated to the holdout.
FZ0 is therefore computed in `volab.evaluators`, assembled into a `LossSeries` by hand, and handed
to `volab.compare.analyse` — which takes a `LossSeries` and knows nothing about where it came
from. The statistical machinery applies unchanged and the trial ledger does not move.

---

## 3. The fence around the registered read, which this phase builds and does not have today

`docs/holdout-preregistration.md` is **REGISTERED** at `65617ee` (R130). Its section 5 says:

> nothing about Phase 3's contestants, which do not exist yet and must not be added to this
> window after the fact

**No test enforces this.** Verified: `grep -rn 'preregistration\|prereg' tests/ src/` returns
nothing. `SHOOTOUT` and `MULTISTEP` are ordinary module-level constants in
`src/volab/compare/cohorts.py`, and a future agent adding `har_rv_iv` to either would break the
registration with a one-line diff and a green suite.

**P3-01 is therefore story one of this phase**, before any Phase 3 code exists: a witness pinning
`SHOOTOUT.contestants` and `MULTISTEP.contestants` to the pre-registration's own list, with the
document's section 5 prohibition quoted in the test's docstring, its **section 1** contestant
list as the literals, and the test naming R130 and the registering
sha. It is the cheapest story in the phase and the only one that protects something irreversible.

---

## 4. Horizon-dependent `requires` (D2)

### The measurement that produced this decision

R131 measured what a VIX1D-requiring contestant costs: 119 of 251 origins at h=1, 120 of 250 at
h=5, 123 of 247 at h=22, 132 of 238 at h=66. R133 pinned those figures in
`tests/test_coverage_real.py::test_the_designs_measured_figures_still_hold`.

**Those figures were measured with `iv_h1` required at every horizon** — `REQUIRED = "iv_h1"` at
`tests/test_coverage_real.py:41`, used unchanged for all four horizons. A HAR-RV-IV does not need
VIX1D at h=22; it needs the **horizon-matched** column. Re-measured 2026-10-28 against the
committed store, same window (`2021-01-04`), stride 5, `minimum_training=250`, through
`wf.coverage_refusal`:

| h | scoreable origins | require `iv_h1` at every horizon | require the matched column only |
|---|---|---|---|
| 1 | 251 | refused, 119 lost | refused, **119 lost** (`iv_var_h1`) |
| 5 | 250 | refused, 120 lost | **no refusal, 0 lost** (`iv_var_h5`) |
| 22 | 247 | refused, 123 lost | **no refusal, 0 lost** (`iv_var_h22`) |
| 66 | 238 | refused, 132 lost | n/a — `IV_SERIES` has no h=66 entry |

**So the VIX1D trade-off exists at h=1 and nowhere else.** The h=5 and h=22 losses are an artifact
of one tuple being read at every horizon, and they buy nothing.

This matters beyond tidiness: those 120 and 123 origins are struck from **every other
contestant's loss** in the cohort, at horizons where the IV contestant does not need the column at
all. The flat-tuple design does not merely cost the IV contestant; it degrades everyone it is
compared against, for nothing.

**By what mechanism, stated precisely** — an earlier draft of this spec said
`Evaluation.comparable()`, and that was wrong. `comparable` reads `LOSS_DOMAIN` and each model's
forecasts (`walkforward/__init__.py:757-775`); it never reads `requires`. A declared column costs
origins in two other places: `evaluate`'s **null-regressor skip** (`walkforward/__init__.py:1010`),
which drops the origin for everyone before any fit, and `coverage_refusal` for the leading
boundary. The measured figures are unaffected; only the causal chain was misattributed.

The script that produced the table is to be committed as `tools/measure_iv_requires.py` alongside
the implementation, so the figures above are reproducible rather than asserted.

### The change

`src/volab/models/base.py`, on the sealed point-in-time path. Pure stdlib and pandas; no new
import.

```python
@dataclass(frozen=True)
class Domain:
    max_horizon: int | None = None
    requires: tuple[str, ...] = ()
    per_horizon: tuple[tuple[int, str], ...] = ()
    sampling: Sampling | None = None

    def requires_at(self, horizon: int) -> tuple[str, ...]:
        """Every column this contestant needs **at this horizon**, unconditional ones first."""
        return (*self.requires, *(column for h, column in self.per_horizon if h == horizon))
```

**A tuple of pairs, not a mapping.** `Domain` is a frozen dataclass and the project's dominant
idiom; a `dict` field would make it unhashable and give `eq` dict semantics. Pairs stay immutable,
hashable and declarative, and they compose with `requires`, which keeps its present meaning: the
columns needed at *every* horizon.

`__post_init__` **refuses a pair whose horizon the contestant does not answer**. A column declared
for a horizon the contestant refuses is a dead declaration, and a dead declaration is how a later
reader concludes the contestant supports a horizon it does not.

### `Cohort.requires` is deleted, not kept beside the new accessor

```python
def requires_at(self, horizon: int) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        column for model in self.models for column in model.domain.requires_at(horizon)
    ))
```

All seven call sites already hold a horizon: `evaluate` at `walkforward/__init__.py:966`, `:996`,
`:999`, `:1002`, `:1011`, and `run_configuration` at `:1307`, `:1317`. Keeping a horizon-blind
`Cohort.requires` beside `requires_at` leaves the trap in place for whoever reaches for the
shorter name — the same argument R131 made for refusing a bad cohort/horizon pairing rather than
silently narrowing it. Delete it; let the type checker find the callers.

### What the witnesses can and cannot do

Stated here rather than discovered in review, following R131's example.

- **Checkable:** that `requires_at` returns the matched column and not the others; that a
  dead-horizon pair is refused at construction; that `Cohort.requires_at` de-duplicates across
  members and preserves first-seen order; that `coverage_refusal` fires at h=1 and does **not**
  fire at h=5 or h=22 for `har_rv_iv`'s declaration, against the real store.
- **Not checkable:** that a contestant's declaration is *complete*. A contestant silently reading
  an undeclared column is today's behaviour unchanged (R131 says so about `requires`, and
  `per_horizon` inherits the limitation). Conformance clause 10 — "a declared requirement is
  real" — reaches the declared columns only. No witness in this phase claims more.

---

## 5. The contestant — `src/volab/models/iv.py`

A new file. No existing module is edited to add it.

**Name:** `har_rv_iv`.

**Specification.** OLS of the horizon target on `HAR_REGRESSORS` (`har_d_proxy`, `har_w_proxy`,
`har_m_proxy`) plus `iv_var_h{horizon}`, fitted once per origin through the existing
`walkforward.fit_ols`. `iv_var_h{h}` is already on the target's scale — `features/build.py` writes
it as `(iv/100)**2 * (horizon/252)` — so no rescaling happens in the contestant and none should be
added. `fit_depends_on_horizon` is `True`.

**Where the actual hazard is, corrected 2026-10-29.** An earlier draft of this section, and the
brief built from it, said a silent rescale "would be a correctness bug with no symptom except
wrong numbers". **That is false, and task 3's review demonstrated it empirically.** OLS with an
intercept is scale-equivariant: scale a regressor by `c` and its coefficient scales by `1/c`,
leaving the fitted value unchanged. Measured by the reviewer at scalings of 2, 100 and 252/5, the
forecast agreed to 1e-15.

So a *consistent* rescale is undetectable in the forecast and harmless. The hazard is a
**one-sided or non-linear** transformation — the training frame transformed and the origin row
not, or the reverse — and that is what the witness must pin: the column entering `fit_ols`
**identical** to `view.training[...]`, not a numeric consequence that does not follow from it.
The test originally written for this asserted the consequence, and so witnessed nothing.

**Domain:**

```python
Domain(
    max_horizon=22,
    per_horizon=((1, "iv_var_h1"), (5, "iv_var_h5"), (22, "iv_var_h22")),
)
```

**`max_horizon=22` is how "cannot answer h=66" is said.** `IV_SERIES` is
`{1: "VIX1D", 5: "VIX9D", 22: "VIX"}` and has no three-month entry, so no horizon-matched implied
volatility exists at h=66. Declaring the bound makes `Cohort.horizons(HORIZONS)` return `(1, 5,
22)` and makes the CLI refuse `--horizon 66` by itself, with the reason printed. A comment would
have required somebody to read it.

**No numpy, no scipy, no arch.** `fit_ols` and `solve` are pure Python. `models/iv.py` is not on
the sealed path, but staying off those imports costs nothing here and keeps the contestant
runnable wherever the baselines are.

---

## 6. The cohort — `phase3-iv`

`src/volab/compare/cohorts.py` gains one constant and one registry entry.

**Membership:** `multistep_members(SHOOTOUT.models)` plus `har_rv_iv` — seven contestants.
`har_levels` stays **first**, because the first contestant's `observations` becomes
`OriginResult.training_rows` (R87). EGARCH is absent for R114's reason, which is a fact about the
specification and does not change between phases.

**Why seven and not "baselines plus IV".** The question Phase 3 asks is whether implied volatility
adds information *over what already works*, and the things that already work are the HAR family,
the GARCH family and the recalibrated HAR. A cohort of baselines plus IV could only answer the
weaker question.

**The h=1 price is accepted and is paid inside this cohort only.** At h=1 all seven lose 119 of
251 origins to VIX1D's 2022-05-13 start. `SHOOTOUT` and `MULTISTEP` are not touched and the
registered read is not touched — section 3's witness is what makes that structural rather than a
promise.

---

## 7. The evaluators — `src/volab/evaluators/`

A new package. It mirrors `volab.compare`'s existing shape — consume an `Evaluation`, compute,
hand the result to a sink — rather than inventing a new kind of artifact.

### 7.1 Why it consumes an `Evaluation` and not a file

**Per-origin forecasts are not persisted anywhere.** Verified: `trials.jsonl` holds means;
`comparisons.jsonl` holds statistics; `LossSeries` is built from an in-memory `Evaluation` by
`compare/series.py` and discarded. Adding a persisted per-origin forecast artifact was considered
and rejected as a second source of truth for numbers the driver already holds. The evaluators run
in the same pass, exactly as the comparison layer does.

### 7.2 Modules

- **`__init__.py`** — pure computation, no I/O.
  - `var_es(variance: float, alpha: float) -> tuple[float, float]`. Gaussian:
    `VaR = z_alpha * sqrt(variance)`, `ES = phi(z_alpha)/alpha * sqrt(variance)`.
  - `fz0(realized_return, var, es, alpha) -> float`. The Fissler–Ziegel joint loss, strictly
    consistent for the (VaR, ES) pair, which is what licenses passing it to `analyse`.
  - `kupiec(breaches, n, alpha)`, `christoffersen(hits)` — unconditional and conditional coverage.
  - `vol_target(variance, realized_return, target, cap)` — position, P&L, turnover.
- **`sink.py`** — `JsonlSink` writing `data/decisions.jsonl`, modelled on `compare/sink.py`.
  A **separate ledger**, not a second record shape in `trials.jsonl`: `count_trials` counts that
  file's lines and the write-up reports the count, so a second kind of record there corrupts the
  one number the write-up has to report. This is the same argument `.gitignore` already makes for
  `comparisons.jsonl`.
- **`__main__.py`** — the driver.

scipy is permitted here. `evaluators/` is **not** on the sealed point-in-time path
(`panel.py`, `store.py`, `walkforward/__init__.py`, `models/base.py`, `features/`), and
`tests/test_import_discipline.py` must be read before the first commit to confirm the new package
is outside every scanned set, not assumed to be.

### 7.3 Two problems the implementer must solve, not route around

**(a) No structure carries the signed return.** `OriginResult.realized` is the realized
*variance*; a VaR breach needs the **signed h-day log return** from the origin. Nothing in
`Evaluation`, `OriginResult` or `TrainingView` holds it.

The spec's answer: the evaluator obtains it itself, through `load_panel` with `end=dev_end`, and
the design document states the point-in-time argument explicitly — this is a **scoring** read,
taken after the forecast exists, with the same standing as `realized` itself, and no value from it
reaches a fit. The implementer must demonstrate that standing with a test, not assert it. If the
cleaner route turns out to be a field on `OriginResult`, that is a change to the sealed path and
needs its own ruling.

**(b) Overlap breaks the coverage tests.** Kupiec and Christoffersen assume independent hits. At
stride 5 that holds at h=5 (stride equals horizon, windows abut) and **fails at h=22**, where each
breach indicator overlaps the next three.

The spec's answer: **the coverage tests refuse where `stride < horizon`** and print the reason, in
the project's refusal idiom. A HAC correction was considered; it was rejected because the
correction's own validity at these sample sizes would then need its own evidence, and a refusal
that names the condition is honest at no cost. FZ0 is unaffected — it goes through `analyse`,
which already handles overlap through `h_eff` and the Bartlett column (R94).

### 7.4 Vol-target P&L and the dividend bias, stated rather than hidden

The project derives close-to-close log returns from raw OHLCV; curated bars carry no adjusted
close, only `has_distribution`. So a vol-target P&L computed from them is **wrong on ex-dates**.

Inventing a total-return series in this phase would be a new data pipeline and is out of scope.
Instead the vol-target report **carries the count of `has_distribution` sessions inside the
window**, so the bias is a printed number beside the result rather than a silent one inside it.
This is the same move `MONTE_CARLO_ERROR_SOURCE` makes: a figure that cannot be fixed here is
printed with what it is wrong about.

### 7.5 Fixed constants

Committed literals, not configuration, for the reason D4 gives — every alternative is a trial:

- `ALPHAS = (0.01, 0.025)` — 99% VaR and the 97.5% level ES is conventionally quoted at.
- `TARGET_VOL = 0.10` — 10% annualised. A round number chosen for being round, which is the
  honest reason; it is not calibrated to anything in this repository and the implementation
  ruling must say so rather than implying evidence.
- `POSITION_CAP = 2.0` — leverage ceiling, so a vanishing variance forecast cannot produce
  unbounded size. Binding rather than decorative: at 10% target a forecast below about 0.25%
  annualised vol hits it.

---

## 8. The two carried items

**Carried 2 — `har_levels_mz` has no witness at h >= 22.** R129 flagged it; it is **not**
discharged. It has ledger lines there, which is not a test. This is an ordinary test story and
belongs in this phase.

**Carried 3 — `egarch11_skewt` still simulates at h=1, so R115 withholds its significance
(R124).** Phase 2 said to revisit "only after US-3.9", and US-3.9a has landed.

**This phase will not touch it, and the reason is not caution.** R115's withholding is part of
**what the registered read reports**. `docs/holdout-preregistration.md` section 3 freezes the
significance rules, and its closing paragraph says a change means a new registration and a new
look, both counted. Revisiting carried 3 before the reveal changes the registered comparison.

It stays on the board as **blocked on the reveal**, with R124's post-hoc-selection reasoning cited
so whoever picks it up afterwards reads that first.

---

## 9. Sequencing, and the ledger constraint (D5)

**US-3.9b has landed** (`761cf71`, merged here at `7827af0`), so `src/volab/models/base.py` and
`src/volab/compare/cohorts.py` are free and **nothing in this phase is blocked on another
session**. The Phase 2 board's `todo` was stale; it was confirmed with the Phase 2 session rather
than inferred.

**Two things it leaves behind that constrain this phase.**

First, `monte_carlo_error` now reads each member's `domain.sampling` instead of a module table,
and `tests/test_compare.py::test_no_module_level_table_in_cohorts_is_keyed_by_a_contestant_and_a_horizon`
forbids reintroducing a `dict[tuple[str, int], ...]` in `cohorts.py`. **P3-04 must not add a
name-and-horizon keyed table**, as a convenience or otherwise.

Second, R134 records a **latent flaw in `Sampling`'s own contract**, verified here by reading
`src/volab/models/base.py`: `__post_init__` refuses a contestant whose measurements disagree on
`source`, while `compare.series_source` builds that string as `f"{symbol} h{horizon}"`. A single
contestant therefore **cannot hold measurements at two horizons on one series** — `"SPY h22"` and
`"SPY h66"` read as two sources. It is latent only because no `Measurement` ships anywhere.

**It does not bite `har_rv_iv`.** That contestant is OLS: deterministic, no simulation, no
`Sampling`, so it declares no measurement and cannot reach the constraint. The Phase 2 session
raised it on the premise that an IV contestant would naturally declare a per-horizon measured
error; under D4 and section 5 this one has nothing to measure.

**But the coupling is real and worth naming.** P3-02 already makes `Domain` horizon-aware through
`per_horizon`/`requires_at`. `Sampling` is the other per-horizon declaration in the same frozen
dataclass, and the fix is of the same shape — require one source *per horizon* rather than one
per contestant, or drop the horizon from `series_source` given `Measurement` already carries it
as a field. Doing both in one architect's pass is cheaper than doing them a sprint apart.

It is nonetheless a **sealed-path contract change that no Phase 3 story needs**, so it is carried
as **optional P3-11** and is the user's call, not an agent's. Without it, Phase 3 is complete; the
flaw stays latent exactly as it is today.

**D5: no Phase 3 walk-forward run appends a line to `trials.jsonl` until the user has triggered
the holdout reveal.** `count_trials` counts that file and the reveal records a second count plus
the delta; `holdout_end` is 2026-10-30. Phase 3's recorded run is therefore **built, tested and
held**, and the board carries it as blocked on an event only the user can cause. Unit and
integration tests do not touch the real ledger and are unaffected.

### Proposed story order

Phase 3's ids are **`P3-nn`**, not `US-3.n`. Phase 2 completion already spent `US-3.9a` and
`US-3.9b`, and two live boards sharing a numbering scheme is how a story gets ticked off against
the wrong row.

| id | story | files | blocks on |
|---|---|---|---|
| P3-01 | Pin the registered cohorts to the pre-registration | `tests/` | nothing |
| P3-02 | `Domain.requires_at` and `Cohort.requires_at`; delete `Cohort.requires` | `models/base.py`, `walkforward/`, `tests/` | nothing — US-3.9b landed |
| P3-03 | `har_rv_iv` in `models/iv.py` | new file, `tests/` | P3-02 for its domain test |
| P3-04 | The `phase3-iv` cohort | `compare/cohorts.py`, `tests/` | P3-02, P3-03 |
| P3-05 | `evaluators/`: `var_es`, `fz0` | new package, `tests/` | nothing |
| P3-06 | Coverage tests and their stride refusal | `evaluators/`, `tests/` | P3-05 |
| P3-07 | Vol-target P&L, turnover and the ex-date count | `evaluators/`, `tests/` | P3-05 |
| P3-08 | The decisions sink and `__main__` | `evaluators/`, `tests/` | P3-05–P3-07 |
| P3-09 | A witness for `har_levels_mz` at h >= 22 (carried 2) | `tests/` | nothing |
| P3-10 | `tools/measure_iv_requires.py` | new file | P3-02 |
| P3-11 | **Optional.** Make `Sampling` horizon-aware (R134's latent flaw) | `models/base.py`, `compare/`, `tests/` | user's call; no Phase 3 story needs it |
| — | Recorded run | `data/` | **the reveal** |
| — | Carried 3, EGARCH at h=1 | — | **the reveal** |

P3-01, P3-05 and P3-09 are mutually independent and touch disjoint files.

---

## 10. Testing

Test-first throughout, with a planted defect proving the test bites wherever test-first was
departed from. Gates: `ruff check .`, `ruff format --check .`, `mypy --strict` over `src`,
`pytest` with `filterwarnings = ["error"]`. Everything through `uv run --locked`.

**The lesson this phase inherits and must not repeat.** Three times in Phase 2 a test existed and
passed while its **docstring described a stronger test than the code performed**, and each was
caught by a review seat rather than the author (R79, R82, R129). Every story gets a review seat,
and the reviewer's first instruction is to read each docstring against what the body executes.

**Figures in tests.** R133 established a third category: a figure pinned inside a test, expected
to fail on an honest store rebuild and to be **re-measured, not edited**. The h=1 `119 of 251` is
one of these. Any new pinned figure must say so in its docstring and name the script that
measured it.

---

## 11. Open questions for the implementation plan

Listed rather than silently decided:

1. Does the evaluator driver reuse `run_configuration`, or does it take an `Evaluation` an existing
   driver produced? The second avoids a second driver loop (R97's standing concern); the first is
   simpler to invoke. Resolve before P3-08.
2. `TARGET_VOL`'s value, and whether the position cap is absolute or a multiple of target.
3. Whether `data/decisions.jsonl` needs a `.gitignore` exception on the same footing as
   `comparisons.jsonl` — it does if any count from it reaches the write-up, and does not otherwise.
4. Whether `har_rv_iv` should decline an origin where its IV column is null (`ModelSkip`, R87) or
   rely on the cohort-wide per-origin skip. The columns have **zero interior nulls** today (R131),
   so this is currently unreachable either way, and the choice should be made on which failure a
   future backfill should produce. Resolve before P3-03.
