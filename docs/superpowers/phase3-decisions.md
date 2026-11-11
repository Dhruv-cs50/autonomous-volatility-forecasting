# Phase 3 decisions

Continues the numbering from R133 in `docs/superpowers/phase2-completion-decisions.md`.

---

## R135 — Phase 3's requirement is a reconstruction, and it is labelled as one

The framework document that defines each phase's exit criterion **is not in this repository**.
The user confirmed on 2026-10-28 that it is not to hand and said to proceed.

Phase 3's scope is therefore assembled from `docs/phase2-completion-sprint-board.md` lines
226-228, the board's own "Not this phase's to fix" section: a HAR-RV-IV contestant using the
already-ingested VIX family, plus VaR/ES and vol-target evaluators; the options chain and the
options pricing library are absent and put everything needing them out of scope.

**Ruling: Phase 3 may be declared *complete as specified*, never *exited*.** Those are different
claims and any write-up must say which it is making. Phase 2 was reopened after its row text was
checked against `grep` and GARCH, DM and MCS were found missing from `src/` — guessing an exit
criterion is how a phase gets declared done when it is not.

**Standing ask:** commit the framework into `docs/` before the Phase 4 boundary. Two consecutive
boundaries resting on memory is the condition R129's audit found six errors under.

**User decision, 2026-10-29:** scope is the reconstruction **plus** both of Phase 2's live
carried items.

---

## R136 — `Domain.requires` is horizon-dependent, and R131's figures were measured on a premise a
real IV contestant does not share

R131 measured what a VIX1D-requiring contestant costs the whole cohort: 119 of 251 origins at
h=1, 120 of 250 at h=5, 123 of 247 at h=22, 132 of 238 at h=66. R133 pinned those figures in
`tests/test_coverage_real.py::test_the_designs_measured_figures_still_hold`.

**Those figures required `iv_h1` at every horizon.** `tests/test_coverage_real.py:41` sets
`REQUIRED = "iv_h1"` and uses it unchanged for all four. A HAR-RV-IV needs the horizon-**matched**
column. Re-measured 2026-10-28 against the committed store, same window (`2021-01-04`), stride 5,
`minimum_training=250`, through `wf.coverage_refusal`:

| h | origins | `iv_h1` everywhere | matched column only |
|---|---|---|---|
| 1 | 251 | refused, 119 lost | refused, **119 lost** (`iv_var_h1`) |
| 5 | 250 | refused, 120 lost | **no refusal, 0 lost** (`iv_var_h5`) |
| 22 | 247 | refused, 123 lost | **no refusal, 0 lost** (`iv_var_h22`) |
| 66 | 238 | refused, 132 lost | n/a — `IV_SERIES` has no h=66 entry |

**The VIX1D trade-off exists at h=1 and nowhere else.** The h=5 and h=22 losses are an artifact of
one tuple being read at every horizon, and they buy nothing. R131's figures are not wrong — they
measure what they say they measure — but the premise they were briefed under, that a flat tuple
is what an IV contestant would declare, is not a premise an IV contestant has to accept. R133's
pinned test is unaffected and stays as it is: it pins `iv_h1` at every horizon, which remains a
true statement about the store.

This is not merely tidiness: a flat declaration strikes those 120 and 123 origins from every other
contestant's loss at horizons where the IV contestant does not need the column at all. The flat
design degrades the field it is being compared against, for nothing.

**Mechanism, corrected 2026-10-29.** As first written this ruling said `Evaluation.comparable()`
was the route, and so did the spec, the plan and my dispatch to the implementer. Wrong, and caught
by task 2's review. Verified at source: `comparable` (`walkforward/__init__.py:757-775`) intersects
origins where each model's forecast is loss-defined, reading `LOSS_DOMAIN` and `scored()`; it never
touches `requires`. The real routes are `evaluate`'s **null-regressor skip**
(`walkforward/__init__.py:1010`), which drops the origin for **everyone** before any fit, and
`coverage_refusal` for the leading boundary. The measured figures and the decision stand; only the
causal chain was misattributed, in four places at once, because I wrote it once and it propagated.

**Amended again, same day, because the correction above was itself over-broad.** Task 2's
re-review flagged `coverage_refusal`'s own docstring (`walkforward/__init__.py:1085-1092`) as
carrying "the same false mechanism". **It does not, and the flag is rejected.** Checked at source:
`Evaluation.scored` excludes an origin whose forecast is NaN, and a `ModelSkip` writes NaN (R87),
so a contestant declining on thin training **is** intersected out of every contestant's loss by
`comparable` — which is precisely the path that docstring describes. There are **three** routes by
which a declared column costs origins, and conflating them is what produced both errors:

| route | mechanism | reads the declaration? | who pays |
|---|---|---|---|
| leading boundary | `coverage_refusal` refuses the whole configuration | **yes** | nobody runs |
| thin training at an origin | `evaluate`'s row count, `walkforward/__init__.py:996` — `training_rows(usable, column, cohort.requires_at(horizon))`, then `continue` | **yes** | every contestant, before any fit |
| interior null at an origin | `evaluate`'s null-regressor skip, `walkforward/__init__.py:1010` | **yes** | every contestant, before any fit |
| a contestant declining | the contestant raises `ModelSkip` -> NaN -> `Evaluation.scored` drops it -> `Evaluation.comparable` intersects it out | **no** | every contestant, after the fit is attempted |

**Corrected a third time, at sprint close.** The table above had three rows and conflated the last
two: it attributed "thin training" to the contestant's `ModelSkip`, when `evaluate` has its own
thin-training skip at `:996` that reads `requires_at` and drops the origin for the whole cohort
before any contestant is asked. Those are different mechanisms with different causes — the first
three are costs **the declaration imposes**, the fourth is a contestant's own refusal and is not.
Caught by the final fix wave's implementer, which noticed this ruling disagreed with the brief I
had written from it.

**Why the fourth route is classed apart, sharpened.** The last reviewer objected that calling
`ModelSkip` "not a cost the declaration imposes" is this ruling's own classification, since a
contestant skipping on its declared column does shrink `comparable` for everyone. True as far as
it goes, but the classification survives for a stronger reason than taste: **for a *declared*
column the harness always gets there first.** A null at the origin is caught at `:1011`, which
checks `(*HAR_REGRESSORS, *cohort.requires_at(horizon))` before any contestant is asked; thin
training is caught at `:996` by the same count. So route 4 is reachable only for a column a
contestant reads but did **not** declare, or for a contestant-internal reason such as a singular
fit — neither of which the declaration causes. That is what makes the fourth row a different
kind of thing rather than a fourth flavour of the same thing.

This ruling has now been wrong three times in three different ways: `comparable` named as the sole
route; the correction over-broad in saying `comparable` was not a route at all; and the amended
table conflating two of the four. Each version was closer, and each error was found by someone
reading the code rather than the ruling. That is the argument for the table being here in a form
precise enough to be checked, rather than as prose.

The original `Domain` docstring was wrong because it attributed the **declared-column** cost to
`comparable` alone, and the measured 119/120/123 figures come from the first route, not the third.
Saying `comparable` is "not the route" is right for the null-column path and wrong as a blanket
claim. Recorded at this length because the first correction was made confidently and was still
imprecise, and a third swing back would be worse than either.

**Ruling (user decision, 2026-10-29):** `Domain` gains `per_horizon: tuple[tuple[int, str], ...]`
and `requires_at(horizon)`. A tuple of pairs, not a mapping, so the frozen dataclass stays
hashable and `eq` keeps value semantics. `requires` keeps its meaning — the columns needed at
every horizon — and `requires_at` is `requires` plus this horizon's pairs.

`__post_init__` **refuses a pair whose horizon the contestant does not answer.** A column declared
for a refused horizon is a dead declaration, and a dead declaration is how a later reader
concludes the contestant supports a horizon it does not.

**`Cohort.requires` is deleted, not kept beside `requires_at`.** All seven call sites already hold
a horizon. Keeping the horizon-blind name leaves the trap for whoever reaches for the shorter one
— the argument R131 made for refusing a bad cohort/horizon pairing rather than narrowing it.

**What the witnesses cannot do**, stated now rather than found in review: a contestant's
declaration cannot be shown *complete*. A contestant silently reading an undeclared column is
today's behaviour unchanged; R131 records this about `requires` and `per_horizon` inherits it. No
Phase 3 witness claims more.

---

## R137 — the evaluators are a separate package whose loss feeds the existing machinery

**Ruling (user decision, 2026-10-29):** `src/volab/evaluators/` consumes an in-memory
`Evaluation`, computes the Fissler-Ziegel joint (VaR, ES) loss, assembles a `LossSeries` by hand
and hands it to `volab.compare.analyse`. DM, MCS and SPA then apply unchanged. Coverage tests and
vol-target P&L sit beside it as diagnostics. Results go to `data/decisions.jsonl` through a sink
modelled on `compare/sink.py`.

**`walkforward.LOSSES` does not gain an entry, and that is the whole point.** `LOSSES` is read by
the driver loop, so an entry there makes every future run of every cohort write a third trial line
per contestant per configuration. `count_trials` counts `data/trials.jsonl`, the pre-registration
pins an expected count, and the reveal reports a second count plus the delta. FZ0 is strictly
consistent for the (VaR, ES) pair, which is what licenses `analyse` accepting it; `analyse` takes
a `LossSeries` and knows nothing about where one came from.

**A separate ledger, not a second record shape in `trials.jsonl`** — the argument `.gitignore`
already makes for `comparisons.jsonl`: a second kind of record there corrupts the one number the
write-up has to report.

**Measured, not assumed:** per-origin forecasts are persisted **nowhere**. `trials.jsonl` holds
means, `comparisons.jsonl` holds statistics, and `LossSeries` is built from an in-memory
`Evaluation` and discarded. Adding a persisted per-origin artifact was considered and rejected as
a second source of truth for numbers the driver already holds.

---

## R138 — the quantile map is Gaussian, and the understatement is the finding

**Ruling (user decision, 2026-10-29):** `VaR = z_alpha * sqrt(sigma2_h)`, ES from the Gaussian
formula. One map, no estimated parameter, one trial per contestant.

A Gaussian map understates tails. That is not a defect of the design; it is what Kupiec and
Christoffersen are there to measure, so the understatement arrives as a number rather than as an
assumption nobody tested. Student-t with `df=5` was rejected because 5 has no evidence behind it
in this repository and tuning it until coverage passed would be fitting the map to its own test.
Filtered historical simulation is the literature's answer and is **deferred, not rejected**: it
needs a one-step forecast at every session rather than every fifth, a stride-1 run this phase does
not otherwise need.

**Overlap.** Kupiec and Christoffersen assume independent hits. At stride 5 that holds at h=5 and
fails at h=22. The coverage tests **refuse** where `stride < horizon` and name the condition. A
HAC correction was considered and rejected: its own validity at these sample sizes would then need
evidence of its own, and a refusal that says why is honest at no cost. FZ0 is unaffected —
`analyse` already handles overlap through `h_eff` and the Bartlett column (R94).

**The dividend bias is printed, not fixed.** Curated bars carry raw OHLCV and `has_distribution`,
no adjusted close, so a close-to-close vol-target P&L is wrong on ex-dates. Building a
total-return series is out of scope, so the report carries the `has_distribution` count inside the
window — the move `MONTE_CARLO_ERROR_SOURCE` makes: a figure that cannot be fixed here is printed
with what it is wrong about.

---

## R139 — no Phase 3 run appends to `trials.jsonl` until the reveal has been triggered

**Ruling (user decision, 2026-10-29):** Phase 3 builds and tests to completion; no Phase 3
walk-forward writes a ledger line until the user has triggered the holdout reveal.

`count_trials` counts `data/trials.jsonl` and the reveal records a second count plus the delta.
`holdout_end` is 2026-10-30. A Phase 3 run before the reveal would put Phase 3's trials inside a
delta a reader will attribute to Phase 2 re-runs. The alternative — run freely and say in the
write-up which part of the delta is Phase 3 — was offered and declined.

Phase 3's recorded run is therefore **built, tested and held**, and the board carries it as
blocked on an event only the user can cause. Unit and integration tests do not touch the real
ledger and are unaffected.

---

## R140 — the registered read has no mechanical fence, and Phase 3 builds one before it builds
anything else

`docs/holdout-preregistration.md` section 5 says Phase 3's contestants "must not be added to this
window after the fact". **No test enforces it.** Verified: `grep -rn 'preregistration\|prereg'`
over `tests/` and `src/` returns nothing. `SHOOTOUT` and `MULTISTEP` are ordinary module-level
constants, and a future agent adding `har_rv_iv` to either would break a registration recorded as
a one-way door, with a one-line diff and a green suite.

**Ruling: P3-01 is story one**, before any Phase 3 code exists — a witness pinning
`SHOOTOUT.contestants` and `MULTISTEP.contestants` to the pre-registration's own list, with the
document's section 5 prohibition quoted in the docstring, its **section 1** contestant list as
the literals, and R130 and the registering sha `65617ee` named. It
is the cheapest story in the phase and the only one protecting something that cannot be undone.

---

## R141 — what the Phase 2 session's report changed, and the one claim in it that is not mine

Reported by `local_198fffa2` on 2026-10-29. **Every claim below was verified here before being
acted on**, which is the standing rule about another agent's report.

**Verified and acted on:**

- **US-3.9b landed** at `761cf71`; `8e3b65b` records it as **R134**. Merged into this branch at
  `7827af0`. So Phase 3's rulings were **renumbered R134-R139 to R135-R140** — the collision was
  real and the Phase 2 ruling has priority, being recorded first. The commit message of `c2570f8`
  says "R134-R139" and cannot be corrected; this ruling is the correction.
- `tests/test_compare.py::test_no_module_level_table_in_cohorts_is_keyed_by_a_contestant_and_a_horizon`
  exists. **P3-04 must not add a `dict[tuple[str, int], ...]` to `cohorts.py`.**
- `cohort.requires` still had **exactly the seven call sites** R136 names at the time of this
  ruling; US-3.9b's three-line touch of `base.py` did not move them. *(Superseded by P3-02, which
  converted all seven and deleted the accessor. The count was true when written.)*
- `main` is at `b8d11cd` and **does not carry Phase 2 completion**; PR #6 is `BLOCKED` under the
  new branch protection. The session's suggestion to re-cut from `main` was therefore **declined**
  — it would have discarded the `Domain` machinery this whole design is built on. Merging forward
  once #6 lands is the correct move and is what the board says.

**Verified, and reclassified.** The session reports a latent flaw in `Sampling`'s contract and
says it "will bite your IV contestant specifically". The flaw is real — read directly from
`src/volab/models/base.py`: `__post_init__` refuses measurements disagreeing on `source`, while
`compare.series_source` is `f"{symbol} h{horizon}"`, so one contestant cannot hold measurements
at h=22 and h=66 on one series.

**It does not bite `har_rv_iv`.** Under R138 the quantile map is Gaussian and under section 5 the
contestant is OLS: deterministic, no simulation, no `Sampling`, nothing to measure. The premise
offered — that an IV contestant would naturally declare a per-horizon measured error — is true of
a *simulated* IV contestant and this one is not.

Recorded rather than absorbed, because taking on a sealed-path contract change on another
session's framing would be the inverse of R129's lesson: it is the same failure as declaring a
phase done on an unchecked claim, run in the generous direction.

**Ruling: carried as optional P3-11, not scheduled, and the user's call.** The coupling is
genuine — P3-02 already makes `Domain` horizon-aware through `per_horizon`, and `Sampling` is the
other per-horizon declaration in the same frozen dataclass, so one architect's pass is cheaper
than two a sprint apart. But no Phase 3 story needs it, and without it the flaw stays exactly as
latent as it is today.

---

## R150 — the ruling-number allocation, and a standing correction about `main`

**Numbered R150, not R142**, which is the first thing this ruling settles.

### The allocation

The Phase 2 completion session proposed it and it is adopted, because it ends a race rather than
patching one. Verified before adopting: `6c02348` records **R142** in
`docs/superpowers/phase2-completion-decisions.md`.

| range | owner |
|---|---|
| R135-R141 | Phase 3 (already recorded; R141 is the last) |
| **R142-R149** | **reserved for the Phase 2 completion session** |
| **R150 onward** | Phase 3 |

The gap is deliberate. A gap in the sequence costs a reader one sentence; a second collision
costs a renumber, which is what R141 already had to do. **Do not continue from R141.**

### The standing correction, which outlives both sessions

The Phase 2 session's advice to re-cut this branch from `main` was conditioned on PR #6 having
merged — a condition the reader had no way to evaluate at the moment of reading, which makes it
an unconditional instruction, and a destructive one. Re-cutting would have discarded the `Domain`
machinery this entire design rests on.

**Ruling: never rebase, re-cut or merge onto a branch on another session's report of its state.**
Run `git log -1 <branch>` and confirm the commits you depend on are present. Verified again here
while adopting this ruling: `main` was still at `b8d11cd`, "Merge pull request #5", and carried no
Phase 2 work at the time of this ruling. **Updated at sprint close:** PR #6 has since merged as
`3e76790`, which is this branch's merge-base. Verified with `gh pr view 6` and
`git log -1 origin/main`, per this ruling's own instruction — and the stale sentence was caught
by the delivery audit, not by me, which is the point the ruling was making.

This generalises past branches. A report from another agent is evidence to check, not a fact, and
the three exchanges that produced R141 and R142 found an error in each direction.

### What the Phase 2 session may not be around to answer

It has auto-archive-on-PR-close enabled and is expected to archive shortly after #6 merges.
Everything load-bearing it holds is in `docs/superpowers/phase2-completion-decisions.md` through
R142 and on its board. Plan on not being able to ask it.

### One claim this ruling first declined to adopt, then verified — AMENDED 2026-10-29

**As first written, this section was wrong, and the correction is the Phase 2 session's.**

It had reported that the user was offered "I'll read it first" and declined. This ruling answered
that the options put to the user **from this session** were "Write the plan" and "Changes first",
so whether they read the spec was not established. That reasoning was sound on the information
to hand, and the conclusion was still false: the offer was made in **the Phase 2 session**, which
this session could not see.

**Verified here before amending**, rather than accepted on the second telling. A search of that
session's transcript returns a user turn whose content is exactly `Yes, write the plan`, and its
own contemporaneous note naming the rejected alternative: *"...ite the plan" over "I'll read it
first", so the spec stands approved by waiver rather than review."* Two excerpts, one of them the
user's own words.

**So the fact is established: the spec is approved by waiver, not by review.** The amendment is
made because this is the same log that carried a wrong figure for two sprints (R129), and a small
untrue statement about the user is exactly the kind that survives by being small.

**Neither the action nor the procedure changes.** The plan's load-bearing assumptions block is
right on its own merits, whoever read what; and declining to record an unverifiable claim was
correct, with the right repair being to go and verify it, which is what happened.

The underlying point stands on its own merits regardless, and is acted on: the spec's
load-bearing assumptions — above all R135's reconstruction — are now collected at the **top of
the implementation plan** instead of distributed through a 450-line spec, so the one person who
can correct them meets them first.

---

## R151 — a rule the harness derives beats the same rule written as a list

Found twice in this phase, and once before it, which is what makes it a ruling rather than three
fixes.

| where | the list | how it failed |
|---|---|---|
| `multistep_members` (R131, Phase 2) | a name filter for the horizon-limited contestant | "needed someone to remember it when a second horizon-limited contestant arrived" |
| `conformance_suite` clause 10 (P3-02) | clause 10 ran on the primary toy only | a contestant declaring at three horizons had one checked and two silently unchecked |
| `test_only_cohorts_imports_the_model_modules` (P3-04) | `volab.models.garch` and `volab.models.recalibrated`, hardcoded | it did not notice the third contestant module the same task added; a `volab/compare/` module importing `volab.models.iv` passed it |

Each list was correct when written. Each stopped being correct the moment the set it enumerated
grew, and in every case **the suite stayed green** — a list cannot report its own incompleteness.

**Ruling: where a test or a harness enumerates a set the codebase will extend, derive the set.**
`volab.models.*` minus `base`, every probed horizon, every configured horizon. Where a floor is
enough, assert the floor (`>=`) rather than an equality, so a fourth member needs no test edit —
an equality is a list wearing a set's clothes.

**The witness for a derived rule is a mutation that the listed version passes.** P3-04's is the
model: planting `from volab.models.iv import har_rv_iv` into `compare/series.py` makes the new
test fail and the **HEAD** test pass, which demonstrates the hole existed rather than asserting
it did.

The cost is paid where the derivation has edge cases, and those get pinned: `base` is exempt
**exactly**, so `volab.models.basement` is still flagged, and there is a parametrised test over
nine import forms saying so.

---

## R152 — a narrowed pre-check must narrow the re-check it precedes, and a skipna reduction is no guard

Two defects in `src/volab/compare/multiple.py`, one reached and one latent, fixed together
because they are the same mistake at two scales: a check written to refuse something, placed
where it cannot see the thing it refuses.

**Numbered R152, not R143.** R150's allocation reserved **R142-R149** for the Phase 2 completion
session and gave **R150 onward** to Phase 3; R150 and R151 are taken. The gap R143-R149 is left
open even though that session finished without using it, for R150's own reason: a gap costs a
reader one sentence, a collision costs a renumber, and this project has already paid that once.
Verified by `grep -rn 'R15[0-9]' docs/`, which finds only R150, R151 and this one, and against
the uncommitted `phase2-decisions.md` in the shared checkout, whose diff claims no number at or
above R142.

### The reached defect

`superior_predictive_ability` pre-checked `_unestimable(series, names=(benchmark,),
confidence_set=False)` — the benchmark's pairs only, which is right, because SPA differences every
contestant against that one benchmark and a degenerate pair among the *others* says nothing about
any SPA statistic. It then called `block_length`, which re-checked on its own behalf with **no
`names`**: every pair again. The caller narrowed and the callee silently re-widened, so a
degenerate pair not involving the benchmark passed the pre-check and raised `ValueError` from
inside `block_length`.

**Why Phase 3's losses were the first to reach it, and the substitution that hides it.** Under
the Gaussian `(VaR, ES)` map of `volab.evaluators`, `var/es` is a constant ratio, so an
off-breach FZ0 value is `const + log(sigma)`: scaling a contestant's forecast **variance** by `c`
shifts its loss by an **additive** `0.5 * log(c)` at every non-breaching origin, and
`same_losses` (`dm.py`) tests exactly that — `ptp(left - right)`, a constant additive
differential. QLIKE and MSE never produced one. `TOY_COHORT`'s `rw_scaled` is
`_Scaled(random_walk, 1.3)`, and `0.5 * log(1.3) = 0.131182`.

The trap, recorded because a first reproduction attempt fell into it: "proportional" here means
proportional in the forecast **variance**, which is additive in the **loss**. Two series whose
*losses* are multiplicatively proportional are not caught, and reproducing with `a` and `a * 1.3`
shows no crash. The test asserts both halves, so the distinction cannot be lost again.

**Ruling: a function that re-checks what its caller already checked takes the caller's narrowing
as a parameter.** `block_length(series, *, q, confidence_set=True, names=None)`, forwarded to
`_unestimable` unchanged, with SPA passing `names=(benchmark,)`. Chosen over deleting the
re-check, which was the smaller diff: the re-check earns its place — `block_length` is shared, and
a future caller that forgets its own pre-check should still be stopped — and threading the
parameter makes the two checks agree **by construction** rather than by coincidence. `None` and
not `()` as the default, to match `_unestimable`'s existing signature exactly, which is the point:
two identical signatures cannot drift apart the way these two did.

**The all-pairs check guarded nothing on SPA's path**, established two ways rather than assumed.
Politis-White is a per-column plug-in rule — `arch.bootstrap.optimal_block_length` returns one
figure per column, so no inter-column structure can enter it, and measured here a column and the
same column plus a constant get block lengths agreeing to 4e-16 relative. And Hansen's SPA
compares the benchmark to the best alternative through `d_k = L_bench - L_k`: two alternatives
differing by a constant give two `d_k` columns differing by a constant, neither of them constant,
both with positive bootstrap variance. What stays refused is unchanged: a constant loss column,
the benchmark itself constant-shifted from an alternative, and the whole all-pairs check for the
**MCS**, where arch eliminates pairwise and `_swamped` exists for that family.

### The latent defect, and why `.max()` is the wrong reduction here

`politis_white` guarded with `math.isfinite(figures.max())` on a **pandas Series**, and
`Series.max()` **skips NaN**. A constant column has no Politis-White block length — arch divides
by its variance and returns `NaN`, with a `RuntimeWarning` on the way — so the reduction returned
*another* column's perfectly finite figure and the guard passed on a matrix it was written to
refuse. Measured: `{0: nan, 1: 3.569}` reduces to `3.569` under pandas and to `nan` under
`to_numpy().max()`. Fixed to `figures.to_numpy().max()`.

Unreachable today, because `_unestimable`'s constant-column check fires first on every path into
`block_length`. It is fixed anyway, and its test asserts **both** the hole and that ordering, so
the claim "unreachable" is a failing test rather than a comment if the ordering ever changes.

**Ruling: a guard against a non-finite value is written on the array, not on the pandas object
that may drop it.** The skipna default is right for a statistic over observations and wrong for a
reduction whose purpose is to notice that one entry is missing.

### What the cost was, and where the witness had to live

The crash reached `cli.main` **after** `run_configuration` had appended its trial lines, so a real
recorded run would have left `data/trials.jsonl` — append-only, git-tracked, and a line count the
write-up reports — carrying a run that produced no comparison. That is why the acceptance witness
is end-to-end and not a unit test:
`test_a_run_on_the_proportional_toy_cohort_reaches_the_bootstrap_and_does_not_raise` runs the
evaluators CLI on `TOY` (the cohort *with* `rw_scaled`) at h=1 stride 2 to 22 origins — the exact
configuration the P3-08 implementer moved to stride 4 to avoid — and asserts both edges in one
run: SPA against `har_levels` reports three p-values, SPA against `random_walk` (itself half of
the degenerate pair) is still refused, DM refuses that pair and the MCS refuses the whole set.

**Nothing pinned this defect before.** The suite was green and the hole was invisible, because the
story that hit it worked around it in the test rather than reporting it as a test gap. Two records
asserting the defect still exists are corrected here rather than left: the stride-4 comment in
`tests/test_evaluators_cli.py` and the `TOY_WIDE` docstring's "`TOY` cannot do this".

### Measured: on the real panel this was a toy-only hazard

Stated rather than assumed, because the board recorded the condition as **unverified**. Built the
FZ0 series for `phase3-iv` on the real panel through `run_configuration` with a no-op `append`
(so no ledger count moved) and compared every pair's `ptp(L_a - L_b)` against `same_losses`' own
`4 * eps * max|L|`:

| configuration | n | closest pair | its ratio to the threshold |
|---|---|---|---|
| SPY h=1, alpha=0.01 | 126 | `garch11_skewt` / `gjr11_skewt` | 2.4e14 |
| SPY h=1, alpha=0.025 | 126 | `garch11_skewt` / `gjr11_skewt` | 2.9e14 |
| SPY h=5, alpha=0.01 | 249 | `har_semilog` / `har_levels_mz` | 2.5e14 |
| SPY h=5, alpha=0.025 | 249 | `har_semilog` / `har_levels_mz` | 2.7e14 |
| SPY h=22, alpha=0.01 | 246 | `garch11_skewt` / `gjr11_skewt` | 2.9e14 |
| SPY h=22, alpha=0.025 | 246 | `har_levels` / `har_semilog` | 5.8e14 |
| QQQ h=1, alpha=0.01 | 119 | `har_levels` / `har_rv_iv` | 2.0e14 |
| QQQ h=1, alpha=0.025 | 119 | `har_levels` / `har_semilog` | 1.8e14 |
| IWM h=1, alpha=0.01 | 131 | `garch11_skewt` / `gjr11_skewt` | 1.3e14 |
| IWM h=1, alpha=0.025 | 131 | `garch11_skewt` / `gjr11_skewt` | 2.1e14 |

All at stride 5 over 2021-01-04 to `dev_end`, which is the recorded run's own window: **every
horizon `phase3-iv` answers** on SPY, and h=1 across the **whole configured universe**. The
closest pair anywhere is 1.3e14 times the tolerance — fourteen orders of magnitude clear — and no
column is constant in any configuration.

**The one plausible real candidate was `har_levels_mz`**, a Mincer-Zarnowitz recalibration of
`har_levels`: a **fixed** slope `b` would be exactly the variance-proportional case. Its `b` is
refitted per origin by an inner walk-forward, so its differential against `har_levels` is not
constant — and the structural reading is the general one, because **no member of `phase3-iv` is a
fixed-scale transform of another**. That is a property of the models, not of the data, which is
why it is not surprising that three symbols agree; the figures are given because measuring was
the point.

**So the crash was a toy-cohort hazard, present in `TOY_COHORT` by construction and not reachable
on the real panel in any configuration measured.** That changes its priority, not its correctness.
The defect was real, and real the moment a fixed-scale contestant is added; the fix is the same
either way.

**What the measurement overturns, said plainly, because "RESOLVED" is the wrong thing for a future
reader to generalise from.** The board's row claimed "a real run dies *after* trials were
appended". For the ten configurations above that claim does **not** hold: the recorded run would
have completed, and no ledger line would have been orphaned. What genuinely blocked the run was
the row itself — an unverified claim about the recorded run's own path is not something to run a
phase on, which is why the row was right to block even though its stated consequence was wrong.
Two configurations remain unmeasured (QQQ and IWM at h=5 and h=22), so the honest statement is
"not reachable in any configuration measured", not "never at risk". The structural reading above —
no member of `phase3-iv` is a fixed-scale transform of another — is what makes the unmeasured two
unsurprising rather than unknown, and it is an argument, not a measurement.

Recording the figures is the point: "unverified" was the honest state of that row, and this is
what verifying it produced.

---

## R153 — the holdout police match `load_panel` by identity, and pay for it once per session

**Numbered R153**, continuing from R152 under R150's allocation (R150 onward is Phase 3's). Checked
before claiming it: `## R15[3-9]` appears in no copy of this file on `main`,
`perf/share-real-panel-loads` or `docs/phase3-sprint-close`, and there were no other open PR
branches.

### The defect

`_forbid_holdout_unlock` wrapped `load_panel` by matching the binding **name** `"load_panel"`
across imported modules. A test that read through the same function under another name was
therefore not wrapped by the police at all:

- `from volab.store import load_panel as aliased`
- a module-level `captured = store.load_panel`, taken at collection time

This is **the same defect, in the same file, as the one `pytest_fixture_setup` was rewritten to
fix one change earlier** — there a review reproduced a module-scoped fixture reading the real
store under an alias and passing. That makes it an instance of R151 rather than a new lesson, and
it extends R151's scope: the set a test must derive rather than enumerate is not always a set of
*names in the codebase*. Here it was the set of **bindings of one object**, and the enumeration
was a single string literal. One literal is still a list.

### How far it actually reached, which is less far than it sounds

Both witnesses have to **move the boundary** to get past the seal; neither can use the keys. That
is forced, not a convenience: `load_panel` resolves `_unlock_granted` through `volab.store`'s own
globals, which the fixture patches *by attribute*, so an unmarked test that reaches for the keys
is caught however it spells its import. Moving the boundary — a hand-built `Settings` with a later
`dev_end`, with `volab.store`'s own refusal of that neutralised — is the only route an alias could
take. The hole was real and is closed; it was not a second open door onto the holdout.

The police also still see only reads routed through `volab` code. A test that opens Parquet
itself is invisible to them, exactly as `pytest_fixture_setup`'s docstring already discloses, and
so is a reference held in a closure, a default argument or an object's attribute and taken before
the install.

### Why this was not folded into the performance change that found it

The naive fix is to run the identity sweep in the per-test police too. #12 measured 3.6ms a
sweep, about 6s across the suite — a fifth of its own ~30s saving, spent re-deriving an answer that
does not change between tests. It was deliberately left out, and this change pays for itself
instead by installing **once per session**.

**Re-measured here rather than inherited**, in a process with the whole suite collected (1845
modules in `sys.modules`), timing both sweeps side by side:

| sweep | median | min | over 1,731 tests |
|---|---|---|---|
| identity, `_names_bound_to` | 3.249ms | 2.768ms | **≈ 5.6s** — the naive fix |
| name, `getattr(module, "load_panel")` — what this replaced did per test | 0.198ms | 0.188ms | ≈ 0.34s |
| this design: 1 install + 2 per marked test = 7 sweeps | — | — | **≈ 23ms** |

#12's figure is confirmed: 3.249ms against its 3.6ms, and 5.6s against its ~6s.

**But the saving against the code this replaces is ≈ 0.32s, not 6s, and that distinction matters.**
The 6s was always the cost of the *naive fix*, never of `main`. `main` paid a *name* sweep per test,
which is 16x cheaper than the identity sweep because `getattr` on one attribute does not walk a
module's whole namespace. So the right way to state this change's performance is: it avoids the 5.6s
the correct-by-identity fix would have cost, and incidentally returns about 0.32s.

The once-per-session design works because the install makes `volab.store.load_panel` *be* the
wrapper. Every later import therefore resolves to the wrapper by whatever name it chooses, with no
re-sweep, and the only per-test state is the boundary pushed onto `_ACTIVE_DEV_END`. The three
phases the value passes through are all ones the suite controls: the wrapper normally, the
original for the duration of a `@pytest.mark.holdout` test, and `_refuse_store_read` while a
wider-scoped fixture is being set up.

### The one thing that forced a sweep to stay

A marked test gets the wrapper **removed**, not merely disarmed. `store._caller_frame` names the
first frame outside `volab` in the audit line, so a wrapper left in the call chain becomes the
"caller", and `test_store_guard.py:280` asserts that caller is `test_store_guard`. That is two
sweeps for each marked test, and `pytest --collect-only -m holdout` counts **3** of them, so **6
sweeps a session against 1,730 tests** — which is why the install is still worth having.

`_install_holdout_police` also refuses to install while `_WIDER_SCOPE_SETUPS` is non-empty. That
is unreachable as the fixtures stand, and checked anyway because the failure mode is silent:
`store.load_panel` is `_refuse_store_read` during such a setup, and wrapping *that* as the
original would leave the police guarding the refusal and the real function unguarded for the rest
of the session.

### Installing once per session has to be made fail-closed, and was not at first

The per-test version re-captured whatever `store.load_panel` was for every test. That made it
robust to a leak by accident: a rebinding which escaped the test that made it was simply wrapped
again by the next test. **Nothing re-captures now**, and a reviewer found the hole that opens.

A **marked** test that does `monkeypatch.setattr(store, "load_panel", fake)` corrupts the police
permanently. `_forbid_holdout_unlock` requests `monkeypatch`, so it is finalised *first*: its
re-install captures `fake` as the original, and monkeypatch then restores the real function out
from under it. What is left is a wrapper delegating to a stand-in, a `volab.store.load_panel`
that nothing wraps, and every later import binding the unwrapped function — silently, for the
rest of the session. Reproduced: the test after the marked one runs green and unpoliced.

Nothing in the suite rebinds `load_panel` from a marked test today — `grep` finds five rebindings,
all of them in unmarked tests and all restored by monkeypatch — so this was **latent, not
reached**. It is closed by comparing `store.load_panel` against the wrapper by identity at each
unmarked setup: one pointer comparison, not a sweep, and it fails closed, because a test must not
run on police that cannot be vouched for.

**The first version of this witness was vacuous, and that is worth recording.** It put the marked
test first, where `_POLICED_ORIGINAL` is still empty, so no re-install happens and nothing is
corrupted: the nested run went green for the boring reason, not the interesting one, and it was
only the mutation check that exposed the difference. The order — an unmarked test to install, the
marked test to leak, an unmarked test to be refused — *is* the scenario.

### The suite wall time cannot resolve this, and waiting for a quiet machine would not have helped

The brief asked for full-suite wall time before and after. Both are reported, from CI, and
together they show that **no wall-clock measurement can resolve a 0.32s effect on this suite.**
Nine runs, on trees whose test content differs by at most four tests:

| tree | tests | event | pytest duration |
|---|---|---|---|
| `main` `01fcee7` — **before** | 1731 | push | 450.99s |
| `097610d`, tree identical to `01fcee7` | 1731 | push | 455.68s |
| `097610d`, same tree | 1731 | pull_request | 326.56s |
| `b2cd193` | 1730 | push | 446.37s |
| `16584c3` | 1731 | push | **465.57s** |
| `16584c3` | 1731 | pull_request | 453.31s |
| `199eb11` | 1734 | push | 342.04s |
| `199eb11` | 1734 | pull_request | 455.77s |
| `f202724` — **after** | 1734 | pull_request | **237.89s** |

Provenance checked rather than assumed, per R150: `gh run view --json headSha,event` on each, and
`git diff 097610d 01fcee7` is empty, so the three "before" runs executed the same tree — the
`pull_request` run's merge ref included, since `097610d` already merges `main`.

**The spread is 227.68s, about 710x the effect being measured.** Three runs of the *identical*
`01fcee7` tree alone span 326.56s to 455.68s.

The sharpest way to see that the instrument is useless here: **the fastest run of all nine, 237.89s,
is the branch carrying the most tests.** This branch adds three witnesses whose nested
`runpytest_subprocess` runs cost roughly ten seconds between them, so it is strictly more work than
`main` and should never be fastest. It was fastest by 89s. A measurement in which the heavier tree
wins by that margin is reporting the runner, not the code.

**The local A/B/A/B was run anyway, started on an idle machine, and says the same thing.** All four
runs passed with the right counts and nothing skipped — the real store was present, so local
coverage matched CI's:

| run | tree | tests | pytest time | 1-min load before → after |
|---|---|---|---|---|
| A1 | `01fcee7` | 1731 | **525.38s** | 6.69 → 9.46 |
| B1 | `f202724` | 1734 | 455.32s | 9.46 → **235.42** |
| A2 | `01fcee7` | 1731 | **253.61s** | 235.42 → 11.59 |
| B2 | `f202724` | 1734 | 260.41s | 11.59 → 8.76 |

`main` measured against *itself* spans 253.61s to 525.38s — a **2.07x spread**, and 8.6x the 31.6s
between the two conditions' means. The machine went from load 6.69 to 235 and back inside four
runs, unprompted. B2's wrapper also recorded 1328s of wall time against pytest's own 260.41s, so
roughly 1068s went somewhere outside the test session entirely.

**The plain statement the brief asked for: no reading here is trustworthy, and the before/after
figures above are reported as a bound, not a measurement.** What they do bound is useful. This
branch runs three *extra* witnesses whose nested `runpytest_subprocess` runs cost about ten seconds
between them, and it was still not measurably slower than `main` in either pairing — consistent
with a small net saving that roughly offsets the tests it adds, and inconsistent with any
regression large enough to matter.

**So this phase's practice is kept and sharpened: measure the mechanism, not the suite.** One sweep
is measurable to three digits and its multiplier is a test count, which is why the table above is a
measurement and a suite duration is an anecdote. This also corrects the sprint-close note that
framed the runtime question as "measure it on a quiet machine or report that it could not" — for an
effect this size there is no quiet machine, only a smaller instrument.

### The witnesses, and the mutations that prove they bite

Seen red before the fix. The figure is **`3 failed, 8 passed`**, measured against `main`'s
`conftest.py` with this branch's test file — each nested subprocess reporting `1 passed` for a read
of 2026-03-02, past the repository's `dev_end` of 2025-12-31, with nothing firing. Three defects
earlier in this phase were witnesses nobody had seen red, which is why this is an observation and
not a claim.

**An earlier draft of this ruling said `2 failed, 5 passed`.** That count was taken in the clobbered
test file described in R154, which was missing three of `main`'s tests, so it understated both
columns. Re-measured against the restored file.

**Two of the three witnesses are for the defect; the third is for a hazard this design introduces,
and they should not be conflated.** The alias and pre-captured witnesses are red against `main`
because `main` has the bug. The leak witness is red against `main` for the opposite reason: `main`
re-captures `load_panel` for every test, so a leaked rebinding heals itself and no error is raised,
which is what that witness asserts must happen *here*. `main` needs no canary. This design does.

Each piece of the fix was then mutated separately, because a witness that survives the mutation of
the thing it is supposed to test is not testing it:

| mutation | what went red | what it proves |
|---|---|---|
| install patches `store.load_panel` only, no sweep | all **3** boundary witnesses, the pre-existing one included | the sweep is load-bearing, and the pre-captured witness really does test it |
| sweep matches the **name** `load_panel` (the original bug) | the **2** new witnesses; the pre-existing one stayed green | the new witnesses target the alias blind spot specifically, and nothing else |
| marked tests leave the wrapper installed | `test_store_guard.py:280`, with `caller_module == 'conftest'` | the uninstall is load-bearing, and :280 is what catches its absence |
| the drift canary removed | the leak witness: `3 passed` where the fix gives `passed=2, errors=1` | the police really do go silently dead after a marked test's leak |

The second row is R151's own standard — "the witness for a derived rule is a mutation that the
listed version passes" — run in reverse: the listed version *fails* the new witnesses and passes
the old one, which locates the hole rather than asserting it.

---

## R154 — a deleted test is invisible to every gate this project has

While fixing the alias blind spot I **silently deleted three tests** that PR #12 had added, in
`tests/test_conftest_policy.py`:

- `test_a_store_read_during_a_wider_scoped_fixture_is_an_error`
- `test_the_refusal_follows_the_function_and_not_the_name_it_is_bound_to`
- `test_a_module_first_imported_during_wider_scope_setup_is_not_left_refusing`

The second is **the review's own witness that the fixture-scope guard matches by identity and not
by name** — the nearest relative of the very defect the change was written to fix. I deleted the
test for the bug while fixing the same bug one layer down.

### The mechanism, which is duller than the irony

I read the file with `cat` from the **shared checkout** while it sat on `docs/phase3-sprint-close`,
a branch that predates #12, and then wrote the whole file into a worktree based on `main`, which
carries #12. The content was a faithful copy — of the wrong branch. `tests/conftest.py` came
through untouched because it was patched in place with targeted replacements that assert a single
match; the one file rewritten whole is the one file that lost work.

**Ruling: never write a whole file from content read earlier in the session.** Edit in place, or
re-read the file from the tree being edited immediately before writing it. This generalises past
branches: the same hazard exists between worktrees, and between a file read before a rebase and
written after one.

### Why nothing caught it, which is the part worth inheriting

Every gate this repository has passed on both commits. They cannot do otherwise: **deleting a test
removes a check without breaking one.** `pytest` was green, `ruff` and `mypy` were green, and CI
was green twice. A reviewer reading the diff would have seen it; no automated gate could.

What caught it was a **test count that moved the wrong way** — CI reported 1731 on `main` and 1730
on a commit that *added two tests* — and it was noticed only while chasing an unrelated question
about whether CI exercises the real-panel paths. Three tests were already gone and pushed by then.

**Ruling: a test count is a gate.** A change that adds tests must raise the collected count by
exactly what it added, and the number belongs in the commit message where the next reader can check
it, as this branch's now does (1734 = `main`'s 1731 + 3). Where a diff against the base should add
and never remove, say so and check it: `git diff <base> -- <path>` showing no removed lines is one
command and it is now part of this branch's evidence.

### The related correction

The same rewrite reinvented `_run_nested`, which #12 had already introduced, and the commit message
claimed credit for factoring out boilerplate that already existed. Both the deletion and the false
claim are corrected in `10a03fc` rather than rebased away, because a branch that hid this would be
the wrong artefact to inherit — R152's standard, applied to my own error instead of a board row.

---

## R155 — a board used as a work queue decays, and the decay is not uniform

The "Deferred findings" table in `docs/phase3-sprint-board.md` carried fifteen rows, each a finding
a reviewer raised and a reason it was deferred. It was tracked in git deliberately, because the SDD
ledger under `.superpowers/` is gitignored and is deleted when a plan finishes, so a finding
recorded only there disappears exactly when someone would want to triage it. That decision was
right and is not what this ruling revisits. What it revisits is the second job the table drifted
into: being read as a queue of work to do.

**Every row was re-derived from the working tree at `01fcee7`, from the code the row names and
never from the row's own wording or from a commit message. Six of fifteen did not state what the
repository contained**, and a seventh carried a suite count stale twice over. The rows were written
on 2026-10-29 (`210a3e4`, `166f881`, `e75497f`) and the table was closed on an independent delivery
audit in that same week; `main` was three merges further on when it was re-derived, and it had
already decayed.

### The three ways a row went wrong, which are not one failure

Lumping them together as "stale" hides the only part worth inheriting: they have different causes
and call for different defences.

| rows | how it failed | what it costs a reader |
|---|---|---|
| 8, 9, 15 | **discharged after the row was written.** The evidence is *not* an ancestor of the row's own birth commit | work re-done from scratch — P3-09's exact cost |
| 4 | **half-discharged.** The overclaiming docstring was fixed; the gap behind it survived, now disclosed | either the whole row is dropped and a live gap leaves the queue, or it is kept and a fixed defect is re-fixed |
| 3, 13 | **still true, pointing at the wrong lines.** `tests/test_evaluators.py:872` is now an unrelated test | a reader checks the cited line, finds something else, and must decide whether the finding moved or evaporated |

The nine remaining rows were accurate, row 5 among them (see the correction note below), though
its suite figure was not. **Two of those nine had never been *measured* either** — row 14's "no
relative import exists in the package" and row 12's arithmetic were both assertions that happened
to hold. They are now checked, which is a different state from being asserted. A third claim of
this re-derivation's own making needed the same treatment: row 6's cell first said the conformance
suite reaches all three of the redundant test's assertions, and it reaches two
(`tests/model_conformance.py:665` and `:371-380`); the literal contestant name is pinned nowhere
else, because `:511` only checks that the name is stable across a rebuild.

**The test that separates these is one command**, and it is the one thing this re-derivation could
not have been done without:

```
git log -S '<fragment of the row>' -- docs/phase3-sprint-board.md   # when the row was written
git merge-base --is-ancestor <evidence commit> <row commit>         # did the evidence come first?
```

This is what established that rows 4, 8, 9 and 15 went stale *after* they were written rather than
being wrong when written: in each case the discharging commit is **not** an ancestor of the row's
own birth commit. Run it before acting on any row, because a row that was discharged last week and
a row that was never right look identical on the page.

**What the check does not establish, which this ruling got wrong the first time.** Evidence that
*predates* a row does not make the row wrong on arrival. It only does so when the row claims that
evidence is **absent**. Row 5 does not: it says the origin counts are "evidenced only by that
round's run, **not from the diff**", which is a claim about *derivability*, and it asks for "a
dedicated construction test". The assertions are there, hard-coded; the reviewer saw them, which is
precisely why `04479bb` and `fbbf8be` are ancestors of `210a3e4`. The first version of this ruling
read the ancestry result and not the row, classified row 5 as wrong on arrival, and so reported a
failure mode that **no row in the table exhibits** — committing the same error the ruling was
written to name: trusting a mechanical signal over the text it was supposed to illuminate. **An
ancestry result is evidence about two commits, never a reading of the claim.** Read the row first,
decide what it asserts, and only then ask what the dates mean.

### Ruling

**A finding is a claim with a date, and a queue entry is a claim about now. One row cannot be
both.** Concretely, for any table of findings that anyone may later work from:

1. **A row states the commit it was verified at.** The table header now carries `01fcee7`. A row
   whose evidence has no sha is a claim about a tree nobody can identify.
2. **A row cites a file and a line, and a line number is treated as perishable.** Rows 3, 13 and 15
   all drifted. Prefer a test or symbol name, which `grep` relocates, over a bare line.
3. **Before acting on a row, run the ancestry check above.** R150 already says another agent's
   report is evidence to check and never a fact; this extends it to the project's own board,
   including rows this orchestrator wrote. A board is a report, and it is one with no author
   present to question.
4. **Re-derivation and repair are separate passes.** Nothing in this table was fixed in the pass
   that verified it, deliberately. A pass that does both starts recording its own intentions in the
   column where the tree's state belongs — which is how row 5 came to describe a diff it had.
5. **No bare suite count in a row.** Row 5 cited 553, was "fixed" by appending "(the suite is now
   1716)", and both figures were stale by the time anyone read them; the suite is **1731** at
   `01fcee7`. A count is a gate (R154) and a gate's reading needs the sha it was taken at.

**Numbered R155, not R153.** R150's allocation gives R150 onward to Phase 3 and reserves R142-R149
for the Phase 2 completion session, which used only R142. **R153 and R154 were taken, at the time
of writing on the then-open PR #13 branch `guard/police-aliased-load-panel` and not on `main`**
*(both have since merged, as `571aa95`; `main` is at `56bf9e2`)* — checked with
`git show <branch>:docs/superpowers/phase3-decisions.md` across every local and remote branch, not
by reading `main` alone, which would have reported R152 as the high-water mark and produced a
collision. That is R150's rule applied to the ledger's own numbering. Both that PR and this one
append to this file, so whichever merges second resolves an append conflict; the two blocks are
independent.

**What this ruling does not claim.** It does not say the twelve rows still open should be fixed, or
in what order. The `verdict` column is a recommendation (`now`, `before the reveal`, `cosmetic`) and
one row's worth of judgement each; acting on any of them is the user's call, and the two rows that
propose deleting a test (3 and 6) are held by R154 regardless.

**Twelve, not eleven — corrected after the fact, and written to be checkable rather than counted
again.** Three of the fifteen rows are `closed` (8, 9 and 15), which leaves one `now` (14), four
`before the reveal` (1, 2, 10 and 13) and seven `cosmetic` (3, 4, 5, 6, 7, 11 and 12): 1 + 4 + 7 =
12. Row 9 is `closed` with a caveat about its evidence being a recorded measurement rather than a
gate, and is counted among the three on either reading, so the figure is twelve whichever way that
caveat is taken.

The row counts this paragraph rested on have since been revised once more: **six rows misstated
the tree, a seventh (row 5) was accurate but carried a stale suite figure, and nine were
accurate** — see the correction note under the ancestry check, which moved row 5 out of "wrong on
arrival". The twelve/three split above is unaffected, because row 5 was `cosmetic` and still open
on either reading, and 1 + 4 + 7 = 12 whichever way it is classified.

What went wrong here was a closing tally that disagreed by one with the table it summarised, in the
very ruling about a board whose rows stopped matching the thing they described. That is R129's
pattern: a small untrue number is the kind that survives by being small, and it survived two
sprints last time. **Both of this ruling's two corrections were found by a reviewer reading it
against the table, neither by its author** — the same finding Phase 3's close recorded eleven times
over, and the reason a verdict column is a recommendation and not a decision.

---

## R180 — the four `before the reveal` rows are discharged, and a leak detector is widened in what it searches for and not in how near it matches

### Numbered R180, not R156, and the gap is deliberate

R156 is taken. **Phase 4 claimed R156 onward** for `docs/superpowers/phase4-decisions.md` (R156's
own text: "the numbers are global; only the file is new"), and it had reached **R164** at the time
of writing. The brief that commissioned this work said "R155 is the highest I know of", which was
true of `phase3-decisions.md` and false of the project — exactly the failure R150 exists to
prevent, arriving in the shape R150 predicted: a report of another branch's state, taken on trust.
The scan that settled it, over every ref and every worktree rather than over `main`:

```
for b in $(git branch -a --format='%(refname:short)'); do
  for f in $(git ls-tree -r --name-only "$b" | grep 'docs/superpowers/.*\.md$'); do
    git show "$b:$f" | grep -ohE '\bR[0-9]{2,3}\b'
  done
done | sed 's/R//' | sort -n | uniq | tail
```

`R165` was free and is **not** taken here. Two Phase 4 sessions were working while this ran, either
could claim the next number, and R156 anticipated this case precisely: Phase 3's open findings
"will keep appending there". So **R165-R179 are reserved for Phase 4's continuation** and Phase 3's
reopened work resumes at **R180**. This is R150's own remedy — "a gap in the sequence costs a
reader one sentence; a second collision costs a renumber" — applied a second time rather than
relearned.

### What discharged each row

Four rows of the board's deferred-findings table, the ones R155's triage marked
`before the reveal` because they affect what the registered read *reports*. Each was re-derived
from the code before it was touched, because R155's lesson is that seven of fifteen rows had
misstated the tree after the table was reviewed, audited and closed. All four held.

| row | commit | what discharged it |
|---|---|---|
| 10 | `fbf92df` | `_describe`'s NOT KNOWN branch now reads the count instead of printing a literal `0` |
| 13 | `03a830e` | the leak detector searches a transform family and every `k` in 1..h |
| 1 | `ab263f0` | the registered h>=2 contestant set is parsed, not inferred from the "minus" clause |
| 2 | `16a38fd` | the prose counts and section 4's trial table are tied to the code |

Collected tests **1856 -> 1874**, the deltas being +4, +5, +2 and +6 for the four rows and +1 for
a witness the review seat asked for, each stated in its own commit message (R154). The baseline is 1856 at `ee16c3d` and **not** the 1734 the brief carried,
which was measured at `56bf9e2` before PRs #16 and #17 merged.

`docs/holdout-preregistration.md` is byte-identical to `origin/main`, md5
`1620e031c0a34b409a321cb00fe61567`. It is REGISTERED and R130 holds its content hash, so every
planted amendment in rows 1 and 2 is an in-memory transform of the text; the one on-disk plant was
reverted and the hash re-checked. Both ledgers are untouched at 414 and 52 lines.

### The design question row 13 raised, and the answer

The row asked whether the detector's `atol=1e-12, rtol=0` should be widened, and warned that
"widen it" must not become "make it flaky". Both halves of that warning are right, and they point
in opposite directions, so the answer separates two things the word "widen" conflates.

**The tolerance stays at 1e-12, and widening it would make this detector worse.** The detector
scans every numeric cell of a training frame, an origin's row and a return series against the
forward returns of every base session. HAR features are *built* to track `|r|` and `r**2` — that
is what volatility clustering means — so a loose match against transforms of future returns flags
features that are doing their job. A detector that cries wolf is one that gets muted, and a muted
leak detector is worse than a narrow one because it still reads as a guard.

**What widens is the search space, each member still matched exactly.** Two axes:

- a closed family of transforms (`_LEAK_TRANSFORMS`): as is, negated, squared, as a simple
  return. A leak need not arrive as the return itself — a sign convention at the evaluator seam, a
  variance response, or a log-against-simple mix-up transforms it on the way in.
- every `k` from 1 to the horizon, each with its own knowability rule (`t + k <= origin`), because
  tomorrow's one-day return is the classic look-ahead and the detector searched only for `h`.

**What it still cannot catch, stated rather than implied.** An unknown rescale `c * r` is out of
reach and stays out of reach: cell matching cannot see an unknown constant, and the only way to
reach it would be the loose tolerance this ruling refuses. Nor can it catch an arbitrary nonlinear
transform. **This is a detector for leaks that arrive whole**, and that sentence now sits in the
function's own docstring, where a reader meets it, instead of being a property nobody had written
down.

The witness is parametrised over the family rather than listing its members (R151), so a transform
added to the family cannot arrive without a witness. The mutation proving it bites — the detector
searching `_LEAK_TRANSFORMS[:1]` while the family declares four — was run against the final code
and reverted, and three of the four cells went red.

### Two errors of this pass, both caught by a test rather than by me

Recorded because this phase's close named the pattern: **the hard claims were verified and the easy
ones were trusted.** Both of these were easy claims.

1. **The floor was put on the wrong quantity.** Guarding `abs(planted) > 1e-6` — the obvious
   reading, and the one suggested to me — *silently disables* the squared member, because `r ** 2`
   of this view's 3e-4 return is 9e-8. The witness caught it on its first run. The floors are now
   separated by job: 1e-6 on the **return**, to skip a flat session whose near-zero return many
   unrelated cells sit close to, and 1e-9 on the **transformed value**, to keep the 1e-12 match a
   strict one rather than a zero-against-zero artefact. The `as is` path reduces to the original
   guard, so the case that already worked is unchanged.
2. **Configuration uniqueness was asserted across the file instead of within a run.** Row 2's new
   test failed with `assert 154 == 414` and was right to: section 4 keeps all three shootouts, so
   "the ledger says 414 things were tried and not 150" and a configuration recurs between runs *by
   design*. The invariant is one line per `(model, symbol, horizon, loss)` **per run**, which is
   what makes the table's arithmetic a count of configurations.

A third belongs to the brief rather than to this pass, and is the reason R150 keeps earning its
place: it reported `origin/main` at `56bf9e2`, the suite at 1734 and the next ruling number as
R156. All three were stale, and `git log -1 origin/main` plus two greps settled each before any
work was cut.

### The tolerance decision, measured rather than argued

The reviewer probed the detector over the toy panel -- all 8 views, every `k` in 1..5, all four
transforms -- counting cells that match at each tolerance:

| `atol` | spurious hits |
|---|---|
| **1e-12** | **0** |
| 1e-10 | 288 |
| 1e-9 | 378 |
| 1e-8 | 584 |

So the decision above is not a preference. **Two orders of magnitude above the chosen tolerance the
detector starts matching features that carry no leak at all**, which is precisely the "make it
flaky" failure the row warned about, and the zero at 1e-12 holds across every view rather than
being one seed's luck. The squared member's effective return floor is about 3.2e-5 rather than
1e-6, because `r ** 2` must clear the 1e-9 floor; that is negligible for equity returns and is now
stated in the code.

### The review seat caught the signature defect again, in this very pass

A `code-reviewer` seat was dispatched with the board's Method instruction first: read each docstring
against what the test body executes. It found that **`test_an_edited_prose_count_is_caught` was
vacuous.** It parsed the amended prose and then asserted `at_h1 != len(REGISTERED_H1)` -- which is
`8 != 7`, arithmetic rather than the fence. It never called the comparison the green test makes, so
it would have passed with that comparison deleted. Row 2's prose half rested on it.

The fix is the one the table half already had: the assertions live in `_check_prose_counts`, which
the green test and the witness both call, so deleting the green test's check now fails the witness.

**This is the twelfth instance in this project of a test or docstring claiming more than its code
performs, and the twelfth found by a reviewer rather than by its author.** It was written in the
same pass whose brief named that defect as the thing to avoid, by an author who had read R155 and
quoted it. The lesson is not that authors should try harder; it is that the review seat is
load-bearing and is not optional on a one-row change. Four further findings of that seat were
taken: the "byte-compared either side" claim covered one ledger of two and now covers both, a test
name promised a prefix check its body does not perform, "every factor is a quantity the code holds"
overlooked one literal, and the new console line asserted a stale curation as fact where the code
knows only that the flag and the count disagree.

It also closed a gap in row 1: both grammar witnesses used backticked names, so the unbackticked
route ("plus har_rv_iv") had none. There are now two guards -- backticked tokens, which catch a
name the project does not have yet, and known contestant names whatever their punctuation, with
the vocabulary derived from the cohort registry (R151) -- and a witness for each.

### What this ruling does not claim

It does not say the eight rows still open should be fixed, or in what order. Row 14 (`now`) is real
and was deliberately left: it is a different guard — `_imports` not reading `node.level` — and
bundling it here would have put an unrelated change in a PR whose subject is what the registered
read reports. Rows 3 and 6 remain held by R154. The `verdict` column stays a recommendation.
