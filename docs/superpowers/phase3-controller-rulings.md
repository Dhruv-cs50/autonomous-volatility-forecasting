# Phase 3 — the orchestrator's rulings

Every decision I took on the user's behalf while running the Phase 3 plan, in the order I took
them, each with what it costs if wrong. Extracted verbatim from the SDD ledger before that
workspace was deleted, because a ruling that dies with the scratch directory is a decision made
in secret.

**These are distinct from R135-R151** in `phase3-decisions.md`, which are the project's numbered
rulings about the code. These are process decisions: what to escalate, what to defer, what to
verify, where to depart from the method and why.

Seven of them record an error of mine that a subagent caught. Those are marked in the text.

---


## 1

CONFLICT 1 — T4 drops `test_the_registered_cohorts_are_unchanged_by_the_phase3_cohort`; T1's fence module already asserts it and the review rubric treats verbatim duplication as a defect. T4 instead asserts `"har_rv_iv" not in SHOOTOUT.contestants`, which is T4's own claim and not T1's. — Cost if wrong: one less assertion in T4's file; T1 still fails if a Phase 3 contestant reaches the registered cohorts, which is the property that matters.

## 2

CONFLICT 2 — T2's Files gains `tests/test_recalibrated.py` and `tests/test_coverage_real.py`. Verified: `tests/test_recalibrated.py:128` and `tests/test_domain.py:123-124` call `Cohort(...).requires`, which T2 deletes, so T2 is red on landing without them. — Cost if wrong: none; the files are genuinely broken by the deletion.

## 3

CONFLICT 3 — T8 blocks on T4 as well as T5-T7. Its CLI test asserts the `phase3-iv` refusal at h=66. — Cost if wrong: T8 dispatched early would fail on an undefined cohort.

## 4

CONFLICT 4 — T2 gains a real-store step. The board states its acceptance as "`coverage_refusal` fires at h=1 and **not** at h=5 or h=22 for `har_rv_iv`'s declaration, against the real store", and no step carried it. Without it the matched-column figures in spec section 4 — the whole basis of the horizon-dependent design — have no witness; `test_coverage_real.py` pins only the `iv_h1`-at-every-horizon numbers. — Cost if wrong: a ~4-minute real-store test; cheap against an unwitnessed load-bearing measurement.

## 5

CONFLICT 5 — T10's conditional "`tests/test_tools.py` (extend, if ...)" is struck. Verified: no such file exists. With CONFLICT 4's step landing the witness in `test_coverage_real.py`, the script is the reproducibility probe (R79) and needs no test of its own. — Cost if wrong: the script could rot unnoticed; the figures it prints are witnessed elsewhere.

## 6

mutation plants run in a scratch copy under the scratchpad, not in this worktree's `src/`, printing `volab.__file__` first to confirm the copy imports its own tree. Disjoint file sets are not enough — R131 records an architect finding a three-red suite from another agent's live plant with file sets disjoint. Task 9's plan text amended. Task 1 was already dispatched with an in-place plant-and-revert; acceptable because it is the only implementer running, so `src/` is exclusive to it, and its brief requires `git diff --exit-code -- src/` before committing. — Cost if wrong: a plant left in place would be committed by a later task's pathspec only if that task names the file, and the exit-code check catches it first.

## 7

the implementer's concern 4 is correct and the error was mine. The pre-registration's contestant list is in **section 1** ("What will be scored", lines 45-50); **section 2** is "What will be claimed, and what will not"; the "must not be added after the fact" prohibition is **section 5**. Verified by `grep -n '^## '`. R140, the spec and the board all said "section 2" and are corrected; the implementer was right to put no section claim for the list in its docstring. — Cost if wrong: a reader chasing a citation to the wrong section of a registered document, in the one file where a wrong citation is least acceptable.

## 8

both Minors are escalated into fix round 1 rather than deferred, against the skill's default that Minors never enter the loop. Reason: this module is the **only** guard on a one-way door (a registered pre-registration), and the reviewer found it is both mis-described and defeatable. (a) The docstring claims "Nothing but this module mechanically stops a contestant being appended", which is false — `tests/test_compare.py:1381` already pins the same seven; verified. A false docstring is this project's signature failure (R79, R82, R129) and my own dispatch asked the reviewer to hunt exactly it, so deferring the one it found would be incoherent. (b) Test 3 checks only that each backticked name appears somewhere in the document, so removing a name from the literals passes, and any later amendment that backticks `har_rv_iv` anywhere silently defeats the fence. One docstring line and one strengthened assertion, scoped tight. — Cost if wrong: one extra fix round on a task already green.

## 9

pre-flight CONFLICT 1's conclusion stands but its premise was wrong. I ruled T4's duplicate out because T1 was "the" pin; in fact `test_compare.py:1381` was already a second pin, so T4's would have been a third. The scan missed it because I grepped for `prereg`, which those pins do not mention — the same blind spot that made the fence look absent. — Cost if wrong: none; dropping the duplicate is more right, not less.

## 10

tasks 2 and 5 are NOT run in parallel, although their file sets are disjoint (task 2: `models/base.py`, `walkforward/`, four test files; task 5: a new `evaluators/` package and a new test file) and CLAUDE.md permits concurrency for genuinely independent stories. Reason: both tasks run the full ~1451-test suite before committing, and task 2 mutates sealed-path code — `Domain`, `Cohort`, seven call sites in `walkforward` — that task 5's suite run would exercise. A green-or-red suite read by task 5 while task 2's edits are uncommitted is not task 5's signal, which is the generalised form of the R131 hazard: disjoint file sets do not make a shared suite run safe. Serialising costs wall-clock only. — Cost if wrong: the ten tasks take longer than they needed to.

## 11

the implementer's concern 1 goes back to it as a correctness follow-up BEFORE review, not deferred to task 3. `conformance_suite` runs clause 10 on the primary toy only, so task 3's three-horizon declarer would have `iv_var_h1` checked and the other two silently unchecked. That is the defect task 2 just fixed, one level up — a clause that appears to verify a declaration while verifying part of it — and task 2 owns `model_conformance.py`, so task 3 should not have to reopen it. Documenting the gap was the implementer's choice and is not sufficient: an accurate docstring describing a weaker-than-assumed check still yields a partial pass that reads as total. — Cost if wrong: one pre-review round on a task that was already green and correct within its brief.

## 12

concern 2 (the Step 7 pinned-measurement test passes on first run and cannot be seen red) is answered by mutation in a scratch copy, not by weakening the test — change the expected 119, and separately expect a refusal at h=5, and confirm both FAIL. A pinned measurement that no one has seen discriminate is an assertion, not a witness. — Cost if wrong: a few minutes of scratch-copy runs.

## 13

task 2's concern 2 (a `per_horizon` column at a horizon no toy probes stays unchecked) is closed in task 3's plan text rather than left as a note. Task 3 now passes `make_toy(22)` in `also_at` as well as `make_toy(5)`, because otherwise `iv_var_h22` is declared and never exercised — the exact vacuity task 2 existed to remove. The h=22 toy may be too thin (about 146 sessions, roughly nine stride-6 origins); if so the implementer reports the measurement rather than dropping the probe silently. — Cost if wrong: task 3 spends a run discovering the toy cannot answer h=22, and says so.

## 14

I2's fix (assert no answerable declared horizon is unprobed) SUPERSEDES my earlier plan patch to task 3. I had made task 3 pass an h=22 toy by instruction; the clause now makes it mandatory. A rule enforced by the harness beats the same rule written in a brief, so the plan text stays as guidance and the clause is the enforcement. — Cost if wrong: none; both point the same way.

## 15

I3 is MINE, not the implementer's, and is corrected at source in the same commit range. Verified before accepting: `comparable` (walkforward/__init__.py:757-775) reads `LOSS_DOMAIN` and `scored()` and never touches `requires`; the real routes are `evaluate`'s null-regressor skip (:1010), which drops the origin for every contestant before any fit, and `coverage_refusal` at the leading boundary. I had written the wrong chain in the spec, R136, the plan's assumptions block and the task-2 dispatch — one error, propagated four times because it was written once and copied. Corrected in 2c1a72c; the measured figures and the decision are unchanged. — Cost if wrong: a reader chasing the wrong function to understand why a declaration is expensive.

## 16

the implementer's omission of the suggested `answers(h)` filter from the unprobed set is accepted, and I verified the reasoning at source rather than taking it — `Domain.__post_init__` (base.py) raises for any pair where `not self.answers(horizon)`, so every pair that survives construction satisfies it and the filter could never exclude anything. A filter that can never exclude anything reads as a guard while guarding nothing, which is the same category of defect as the dead assert it would have sat beside. — Cost if wrong: a future change to `__post_init__` that relaxes the refusal would silently widen the unprobed check; the two live three lines apart in the same class.

## 17

the re-review's out-of-scope item 1 — that `coverage_refusal`'s docstring (walkforward/__init__.py:1085-1092) "still names the same false mechanism" — is **REJECTED**, and checking it found my own correction over-broad. `Evaluation.scored` excludes a NaN forecast and a `ModelSkip` writes NaN (R87), so a contestant declining on thin training IS intersected out by `comparable`, which is exactly the path that docstring describes. It is accurate and must not be "fixed". Three distinct routes exist (leading boundary -> `coverage_refusal` refuses the run; interior null -> null-regressor skip at :1010 before any fit; thin training -> ModelSkip -> `comparable`), the measured 119/120/123 come from the first, and conflating them produced both the original error and my over-correction. Tabulated in R136 at cc23a3b. — Cost if wrong: a correct docstring edited into a wrong one, which is worse than the original error because it would carry a correction's authority.

## 18

concern 4 goes back as a correctness follow-up BEFORE review. Verified at source rather than from the report: `except SchemaError` (walkforward/__init__.py:1045) is at the **origin** level, outside the per-model loop, so it drops the origin for every contestant; `except ModelSkip` (:1036) is inside it and is that contestant's own refusal (R87). A singular fit caused by `iv_var_h{h}` — a column only `har_rv_iv` reads — is not "the window was unfittable", because the HAR floor alone would have fitted it. Leaving it makes every other contestant pay for a defect in one, which is the asymmetry R87 exists to expose, and `recalibrated.py` already converts exactly this case. The brief did not address it and the implementer followed the brief correctly. — Cost if wrong: an over-broad catch could swallow a genuine window failure and report it as one contestant's refusal, hiding a real data problem; the instruction is to convert the `fit_ols` call only.

## 19

concern 1 (clause 10 cannot distinguish the origin-null skip from the thin-training skip, because it nulls the column in both the row and the training frame) is NOT a defect and `tests/model_conformance.py` is not reopened. The clause claims "nulling the declared column makes the contestant raise ModelSkip" and verifies exactly that; docstring and code agree, which is the standard that has bitten this project four times. A conformance clause is a floor every contestant clears, not a replacement for a contestant's own tests, and the finer distinction already lives in tests/test_iv_model.py. Asked only for a docstring line there so the apparent redundancy is not deleted later. — Cost if wrong: a contestant could drop its origin-null check and still pass conformance; its own unit test is the guard, which is where it belongs.

## 20

the Important is upheld AND it invalidates a claim of mine. The scale witness (iv.py:30-31, test_iv_model.py:252-263) asserted that a contestant rescaling `iv_var_h5` "would not return 3*iv+1e-6". False: OLS with an intercept is scale-equivariant, so scaling a regressor by c scales its coefficient by 1/c and leaves the fitted value identical — the reviewer ran it at 2, 100 and 252/5 and got agreement to 1e-15. My brief and the spec both said a silent rescale would produce wrong numbers; it would produce *identical* numbers. The real hazard is a **one-sided or non-linear** transformation (training frame transformed, origin row not, or the reverse), so the witness must pin the column entering `fit_ols` as identical to `view.training[...]`, not a numeric consequence that does not follow. Spec corrected; fix round sent. — Cost if wrong: a stated correctness constraint with a test that cannot fail, in the one place my own brief insisted hardest.

## 21

the loose-`match=` defect is promoted into the plan's **Global Constraints**, together with a second rule drawn from the scale witness — a test asserting a numeric consequence witnesses nothing unless the consequence actually follows; pin the thing itself. Both defects were found in tasks 1, 2 and 3 in a row, and catching the same class a fourth time by review is slower and less reliable than binding every remaining task to the rule up front. — Cost if wrong: two extra lines in every remaining brief.

## 22

tasks 6+7 and tasks 9+10 will each be dispatched as ONE batch, not four separate implementer/review cycles. 6 and 7 are the same shape (add a function plus tests to `evaluators/__init__.py` and `tests/test_evaluators.py`), already serialised on those two files, and neither needs the other's result; 9 and 10 are independent and touch disjoint files (`tests/test_recalibrated.py`, `tools/`). The skill permits batching small same-shape work and reviewing the diff as one unit. Tasks 4, 5 and 8 stay solo: 4 touches the registered-cohort file, 5 is the foundation 6-8 build on, 8 writes a new ledger. — Cost if wrong: a batched review is a coarser gate, so a defect in one half could be approved alongside a clean other half; mitigated by naming both halves separately in the review dispatch.

## 23

the re-review's out-of-scope item — `tests/test_iv_model.py:1-6`, the module docstring still listing "the column being rescaled" among the failures the file guards — is NOT deferred. It is the same false-docstring defect this very round corrected twice, sitting in the same file, and deferring it would make the escalation I applied in task 1 incoherent. Sent back as a docstring-only change. — Cost if wrong: one short round for text.

## 24

that docstring-only change gets NO dispatched re-review; I read the committed diff myself. The skill's rule that every round ends with a scoped re-review exists because unreviewed fixes land regressions, and a text-only edit to a test module header with the suite re-run carries none. Dispatching a fresh reviewer to read five lines of prose costs a seat to re-derive context I already hold. Recorded because it is a deliberate departure from the skill, not an oversight. — Cost if wrong: a stale sentence survives in a test module docstring, which the final whole-branch review also sees.

## 25

both Minors (a comment claiming "the very instances ... not copies" above a value-equality assertion, and the cohorts.py docstring's "the only module that imports both contestant modules" now that it imports three) go back as one prose round, reviewed by me rather than by a dispatched seat — the same departure and the same reasoning already logged for task 3's docstring round. — Cost if wrong: two stale comment lines, which the final whole-branch review also sees.

## 26

the deferred-findings list is MOVED to `docs/phase3-sprint-board.md`, which is tracked, and is no longer kept only in this ledger. The reviewer pointed out that its own deferral's sole remaining risk was the final review being skipped; I then noticed the sharper problem — this ledger lives under `.superpowers/`, which is gitignored and which the skill tells me to DELETE when the plan finishes. A finding recorded only here vanishes at exactly the moment someone would triage it. Seven findings carried over at 210a3e4. — Cost if wrong: a slightly longer board, which is the correct place for it anyway.

## 27

scope WIDENED beyond the prose round, deliberately, to cover a real coverage gap the implementer surfaced. Verified at source: `tests/test_compare.py:1514` `test_only_cohorts_imports_the_model_modules` hardcodes `volab.models.garch` and `volab.models.recalibrated`, so for every non-`cohorts.py` module it asserts only those two are absent — a `volab/compare/` module importing `volab.models.iv` passes it. The invariant is unenforced for exactly the module task 4 added, and the docstring's "`cohorts` is the one place that names them" is now false. Nothing is broken today because `iv.py` imports no arch, but the guard is structural and the failure mode is the point: it missed a third contestant module because it was written as a list of two, so every future one inherits the hole. Instructed to DERIVE the set (`volab.models.*` except `base`) rather than extend the list, with a mutation planting the iv import into `dm.py` or `series.py` to prove it bites. — Cost if wrong: a test-only change in one file, re-run and mutation-checked; the risk is an over-broad exclusion accidentally permitting a real contestant import, which the mutation guards against.

## 28

the implementer is right and my plan was wrong about the ES constant. Verified here independently, not adjudicated between two claims: `phi(z_0.01)/0.01 = 2.6652142203458`, confirmed to 13 places by quadrature of `E[-X | X < -z]`. The plan's 2.6652142437 is off by 8.8e-9 relative and **fails the `rel=1e-9` the same assertion states** — a literal that contradicts its own tolerance. Corrected at 64c8c10, with the derivation recorded beside it. The implementer also added a quadrature cross-check so the test derives the constant instead of trusting a literal, which is the right response and more than I asked for. — Cost if wrong: a numeric literal in a test that would have failed as soon as anyone tightened the tolerance, in the first task of this plan with real mathematics.

## 29

the Important is MINE. `tests/test_evaluators.py:748` claims the autouse holdout fixture is a second independent check on `end`; it cannot fire there, because `_forbid_holdout_unlock` triggers only when `end > dev_end` (2025-12-31) and the synthetic store is sealed 2023-12-31 with data stopping 2024-01-31. The same sentence is in the plan at the test I drafted, so it originated in my brief — seventh instance in this repo of a claim outrunning its code. Plan corrected at 3f70f59 to name the two guards that are real (every `load_panel` call carrying `end == dev_end`, and a sentinel close beyond the seal). — Cost if wrong: a reader trusting a guard that cannot fire, in the test whose only job is to establish point-in-time standing.

## 30

the reviewer established that task 5's raise-rather-than-drop choice is safe **by construction**, not merely by the SPY probe — the variance target is NaN if any return in t+1..t+h is NaN, while `horizon_returns` drops only on a missing endpoint, so `comparable("qlike")` is a subset of the returns' keys for any symbol. The implementer's own concern 7 called that "stated, not tested". It is the premise the entire choice rests on, so it becomes **task 6 step 0** rather than a deferred note, with interior-gap and boundary-gap cases. — Cost if wrong: one extra test in task 6; leaving it stated would mean the refusal's safety is an argument rather than a witness.

## 31

the re-review's out-of-scope item — a docstring estimating "a return of about 20" where `log(1e9/~100)` is about 16 — is DEFERRED to the board rather than escalated, unlike the docstring findings in tasks 1 and 3. The distinction is that this is an order-of-magnitude aside motivating a bound that still holds, not a claim that a test verifies something it does not. Escalating every imprecise number would make the escalation rule meaningless; escalating claims about what a test checks is what has caught seven real defects. — Cost if wrong: an imprecise parenthetical survives in a docstring, visible to the final review.

## 32

TWO more of my numbers were wrong and the implementer is right on both. (a) At stride 5 and h=22 a holding overlaps the next **four**, not three — 5k < 22 for k=1..4. (b) The position cap binds at `TARGET_VOL / POSITION_CAP` = 0.10/2.0 = **5%** annualised vol, not the "about 0.25%" the spec claimed — wrong by twentyfold. It found (b) by testing both sides of the real boundary instead of the one the spec named. Corrected at 52d4fdc (spec) and 8c3a1fd (plan). Whether the cap ever binds on real SPY data at 5% is recorded as unmeasured rather than assumed. — Cost if wrong: a stated boundary nobody could reproduce, in the one constant that decides whether a risk limit is live or decorative.

## 33

the first Minor is sent back although both were classed non-blocking, because it is the failure I explicitly asked the reviewer to hunt and it is live. All five rows of `test_a_breach_sequence_with_transitions_to_compare_still_gets_its_p_value` have `p01 != p11`, so adding `or p01 == p11` to the withhold condition — suppressing a genuine result — **passes the entire suite**; so do the two half-condition mutants, since no row has `n01 == 0` or `n10 == 0`. The withholding is pinned in the under-withholding direction and unpinned in the over-withholding one. One row (`[F,F,T,T,F]`: all four transition counts 1, LR -4.4e-16, p == 1.0 and NOT withheld) closes all three. — Cost if wrong: a test row; leaving it means the guard protecting against suppressing real results has no witness.

## 34

**a contradiction in my own plan, caught before dispatching task 8.** The brief carries a test named `test_an_evaluator_run_does_not_append_to_the_trial_ledger` AND tells the driver to reuse `run_configuration`, which appends trials per configuration by design (R74). Both cannot hold. Resolved in favour of reusing `run_configuration` — R97 deleted the last per-configuration loop for diverging from `evaluate`'s, and a second driver loop here repeats that mistake; trials written by the walk-forward are real trials and writing them is correct. What must be true is narrower: the **evaluator layer** writes only to `decisions.jsonl`, adds no trial lines of its own, and never puts a second record shape in `trials.jsonl`. The test is reshaped to assert the trial count equals exactly what the walk-forward wrote, against a tmp_path root. A test asserting ZERO new trial lines would assert something false about a correct design. The implementer is told to stop and say so if reading the code changes its mind. — Cost if wrong: a driver that duplicates `evaluate`'s loop, which is the defect R97 exists to prevent.

## 35

TWO of task 8's concerns go on the board as **blocking the recorded run**, not as deferred findings (87dbffd). (a) `superior_predictive_ability` pre-checks only the benchmark's pairs while `block_length` re-checks all of them, so two near-identical non-benchmark FZ0 series raise ValueError — reproduced on the toy cohort at stride 2. Pre-existing Phase 2 code that Phase 3's losses are the first to exercise, and **a real run would die after trials were appended**, leaving the ledger carrying a run that produced no comparison. (b) SPY's and IWM's `has_distribution` flags are uniformly False because the vendor serves distributions for QQQ only, so spec 7.4's ex-date count reads as "no ex-dates" when it means "no data" — a hollow disclosure is worse than none, because it looks discharged. — Cost if wrong: the recorded run crashes mid-flight, or ships a disclosure that misleads precisely where it was meant to inform.

## 36

task 8's concern 3 — that a missing bar makes `decision_series` raise mid-run — is put to the reviewer to DERIVE rather than accepted, because I believe it contradicts the invariant task 5's review established and task 6 step 0 pinned: a missing close at t NaNs r_{t+1} and so NaNs the variance target, removing the origin from `comparable`, and a missing close at t+h does the same. If the concern is right there is a hole in the step-0 proof and that is Important; if wrong, the raise is unreachable from a real run. Not adjudicated by me on either side. — Cost if wrong: either an unreachable branch treated as a live hazard, or a live hazard treated as unreachable.

## 37

**P3-09 is WITHDRAWN, not done — its premise was stale and the error was mine.** The implementer returned NEEDS_CONTEXT rather than writing a test, and it was right. Verified here rather than accepted: `git merge-base --is-ancestor b04f079 HEAD` passes; `b04f079` is "Witness har_levels_mz on the real panel at h=22 and h=66 (US-3.14)"; `tests/test_recalibrated.py:750` asserts `abs(recalibrated_value - base) > 1e-3 * base` at every origin answered, which is **exactly** the assertion P3-09's brief specified; and :753 additionally cross-checks the harness against a direct fit. The implementer's scratch-copy plant of `value=outer.value` fails all 8 of the h>=22 cases.

## 38

the Important is MINE AGAIN and is the clearest case yet of a corrected claim propagating. `tools/measure_iv_requires.py:11-12` attributes a declared column's cost to `Evaluation.comparable`. I wrote that chain originally, corrected it in four places (spec, R136, the plan's assumptions block, a dispatch), and it reappeared in a file written **after** the correction — because the correction lived in documents the implementer was not required to read. Sent back. — Cost if wrong: the probe behind the design's load-bearing figure would explain that figure by the wrong mechanism.

## 39

the reviewer's note that "the same wording sits in `coverage_refusal`'s own message and docstring" is flagged to the implementer as a **trap, not a fix**. R136 tabulates three routes; `coverage_refusal` describes the thin-training route, where a `ModelSkip` writes NaN, `Evaluation.scored` drops it and `comparable` genuinely does intersect it out. That docstring is correct, and editing it would turn a true statement into a false one carrying a correction's authority — which is exactly the failure my own over-correction nearly caused earlier in this plan. — Cost if wrong: a correct docstring "fixed" into a wrong one.

## 40

all five audit findings were documentation and all five were mine; fixed at 8867380. The important one: **the board's Coordination section and R150 both still said PR #6 was open and `main` was at `b8d11cd`.** #6 merged as `3e76790`, which is now this branch's merge-base. That is the *same class of staleness* that produced the withdrawn P3-09 story — a record asserting a state of the world that had moved — and this time the audit caught it rather than me, which is precisely what the audit exists for. Also fixed: the plan's Task 9 still asserted R129 was not discharged, contradicting the board (now marked withdrawn, text left intact as the record of what was asked); a blocker row orphaned by a blank line so it would not render inside its table; and a deferred note citing a 553-test run against a suite now at 1716. — Cost if wrong: a reader trusting a board that describes a world two merges old.

## 41

**C1 is Critical, it blocks the merge, and I missed it.** The evaluator CLI's entire statistical layer is never computed by any test — every committed e2e run ends under 11 comparable origins, so `analyse` only ever runs its thinness-refusal branch, and the one stride that reaches the bootstrap (2) raises and was retired to stride 4. **I knew about the stride move**: the task-8 review told me the implementer "dodged it by moving a test from stride 2 to stride 4, so nothing in the suite pins it", and I recorded that on the board as part of the SPA blocker. What I failed to draw is the stronger consequence — not merely that the crash is unpinned, but that **DM, MCS and SPA are never exercised at all on the FZ0 path**. I treated it as a fact about one defect when it was a fact about the whole layer. Worse, the dodge lived only in a gitignored report the board itself says is deleted at plan close, so it would have left no trace. — Cost if wrong: shipping a statistical layer whose only tested behaviour is declining to compute.

## 42

the review's two board corrections are both right and both applied at c6d9486. (a) The SPA row asserted any proportional pair trips the crash without stating that the pair must be **exactly** proportional and that whether `phase3-iv` contains one on the real panel is **unverified** — `TOY_COHORT` does by construction, which is how it was found. (b) The ex-date row was **stale**: it described the hollow disclosure that `1817f8c` already fixed. What remains is the underlying data gap — SPY's and IWM's P&L is wrong on every ex-date and nothing can bound the error. A run that says it does not know is honest, but it is not a measurement. That is the second stale row of mine the final checks have caught.

## 43

**my prescribed fix for C1 was impossible and the substitution is better.** I said to widen the window rather than shrink the stride. The toy panel ends 2024-07-31, stride 5 never exceeds 10 origins, and the implementer probed h=1 at strides 1/2/3 and h=5 at strides 3/4 — **every** toy configuration reaching 11+ origins hits the identical-losses crash, so a wider window at stride 4 would crash too. It instead built `TOY_WIDE`: the toy **minus the proportional `rw_scaled` member**, at h=1 stride 2, reaching 22 comparable origins and asserting the statistics are computed (p-values in range, block lengths present, no refusal text). That removes the degenerate pair rather than steering around the region it lives in, which is the opposite of the dodge that created C1. — Cost if wrong: a test exercising the statistics on a cohort one member smaller than the shipped one.

## 44

**comment over guard accepted for C1 part 2.** Its reasoning is right on both counts: `decide()` runs after `run_configuration` has already appended the trials, so a guard there cannot prevent the append it was meant to prevent; and catching the error would patch `multiple.py`'s defect from outside, in a branch that does not own that file, hiding a crash another session is fixing. The comment carries the mechanism, the 7-origin reason the stride-4 test survives, and pointers to the board row and the new test. — Cost if wrong: a crash stays reachable from a configuration no test uses, recorded in a comment rather than prevented.

## 45

**R136 corrected a third time, to four routes** (b6f6c60). The implementer noticed the ruling disagreed with the brief I wrote from it. Verified at source: `walkforward/__init__.py:996` is `counted = training_rows(usable, column, cohort.requires_at(horizon))` followed by `continue`, which drops the origin for the whole cohort **before any contestant is asked** — distinct from a contestant's own `ModelSkip`. Three routes read the declaration (`coverage_refusal`, `:996`, `:1010`); the fourth, `ModelSkip` -> NaN -> `comparable`, does not and is not a cost the declaration imposes. This ruling has now been wrong three times in three different ways, each version closer, each error found by someone reading the code rather than the ruling.
