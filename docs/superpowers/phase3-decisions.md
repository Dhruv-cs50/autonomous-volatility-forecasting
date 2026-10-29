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
