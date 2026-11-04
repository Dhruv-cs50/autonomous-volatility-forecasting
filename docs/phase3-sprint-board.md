# Phase 3 sprint board — IV contestant and decision evaluators

**Design:** `docs/superpowers/specs/2026-10-29-phase3-iv-and-decision-evaluators-design.md`
**Decisions:** `docs/superpowers/phase3-decisions.md`. **Numbering is allocated (R150):**
R135-R141 and **R150 onward** are Phase 3's; **R142-R149 are reserved** for the Phase 2
completion session. The next Phase 3 ruling is **R151**, not R142 — the gap is deliberate.
*(Allocated since: R151, R152 on `main`; **R153 and R154 on open PR #13**, not yet merged; **R155**
is this board's re-derivation of the deferred-findings table. The next free number is **R156**, and
it must be checked against every branch and not only `main` — R153-R155 were allocated that way.)*
**Plan:** `docs/superpowers/plans/2026-10-29-phase3-iv-and-decision-evaluators.md`, committed at
`6ded822`. Its load-bearing assumptions block is the first thing to read.
**Branch:** `phase-3-iv-and-evaluators`, cut from `phase-2-completion-groundwork` at `ea050b2`.

**The exit criterion is a RECONSTRUCTION, not a quoted requirement.** The framework document is
not in this repository. Section 0 of the design says what that forbids: this phase may be
declared *complete as specified*, never *exited*. Phase 2 was reopened because a row's text was
checked against `grep` and found short.

**Ids are `P3-nn`.** Phase 2 completion still owns `US-3.9a`/`US-3.9b`; two live boards must not
share a numbering scheme.

---

## Stories

| id | story | files | acceptance | state |
|---|---|---|---|---|
| P3-01 | Pin the registered cohorts to the pre-registration | `tests/` | a test fails if `SHOOTOUT.contestants` or `MULTISTEP.contestants` stops matching the contestant list in `docs/holdout-preregistration.md` **section 1**; docstring quotes section 5's prohibition and names R130 and `65617ee` | **done** (`c279657`) — set equality against the document's parsed bullets, biting in both directions |
| P3-02 | `Domain.requires_at`, `Cohort.requires_at`; **delete** `Cohort.requires` | `src/volab/models/base.py`, `src/volab/walkforward/__init__.py`, `tests/` | a dead-horizon pair is refused at construction; `coverage_refusal` fires at h=1 and **not** at h=5 or h=22 for `har_rv_iv`'s declaration, against the real store; no horizon-blind accessor remains | **done** (`6a545c7`, `6a820b4`, `fbbf8be`) — each of the seven call sites pinned by individual mutation; clause 10 refuses an unprobed declared horizon |
| P3-03 | `har_rv_iv` | `src/volab/models/iv.py` (new), `tests/` | conformance suite passes; `domain.max_horizon == 22` so `--horizon 66` is refused by the machinery, not a comment; no numpy/scipy/arch import | **done** (`590e9e3`, `b7777d6`, `d5bf2fe`, `c6b3707`) — conformance at h=1, 5 and 22; a column-only singular fit is its own `ModelSkip`, decided by re-fitting the HAR floor |
| P3-04 | The `phase3-iv` cohort | `src/volab/compare/cohorts.py`, `tests/` | seven contestants, `har_levels` first (R87), EGARCH absent (R114); `SHOOTOUT` and `MULTISTEP` byte-identical; **no `dict[tuple[str, int], ...]` added** — US-3.9b's `test_no_module_level_table_in_cohorts_is_keyed_by_a_contestant_and_a_horizon` forbids it | **done** (`8b8c793`, `cc5c499`, `837d740`) — registered cohorts untouched and the fence fired on a planted leak; the import guard is now derived, not a list (R151) |
| P3-05 | `evaluators/`: `var_es`, `fz0` | `src/volab/evaluators/` (new), `tests/` | FZ0 assembled into a `LossSeries` and accepted by `volab.compare.analyse`; `walkforward.LOSSES` unchanged | **done** (`6d5fa62`, `71b1c9b`, `1f8d6ee`) — FZ0 verified by exact quadrature: argmin at the truth, minimum equal to `log(ES)` to 1e-15 |
| P3-06 | Kupiec, Christoffersen, and the stride refusal | `src/volab/evaluators/`, `tests/` | the coverage tests **refuse** where `stride < horizon` and name the condition; witness that the refusal fires at h=22 and does not at h=5 | **done** (`ef2b0b6`, `dd635bd`, `86bdc52`, `2b22e64`) — a degenerate Christoffersen p is withheld with a reason across all six shapes, independently re-derived and brute-forced to length 14 |
| P3-07 | Vol-target P&L, turnover, ex-date count | `src/volab/evaluators/`, `tests/` | the `has_distribution` count inside the window is printed beside the P&L, so the dividend bias is visible | **done** (`84d5d70`) — approved with no findings; the log-vs-simple return approximation is disclosed too |
| P3-08 | The decisions sink and `__main__` | `src/volab/evaluators/`, `tests/` | writes `data/decisions.jsonl`; the evaluator layer adds no trial lines of its own | **done** (`40283cc`, `1817f8c`, `444d7d8`) — `distribution_flags_known` separates no-ex-dates from no-data, and affected holdings are counted beside ex-date sessions |
| P3-09 | A witness for `har_levels_mz` at h >= 22 (**carried 2**) | `tests/` | R129's flag discharged by a test that bites, not by ledger lines | **withdrawn — already discharged before this phase began** by `b04f079` (US-3.14), an ancestor of this branch, which added real-panel tests at h=22 and h=66; `tests/test_recalibrated.py:750` asserts `abs(recalibrated - base) > 1e-3*base` at every origin answered. Verified here, and the `value=outer.value` plant fails all 8 of its h>=22 cases. The story existed because the Phase 2 board's carried-items text predated US-3.14 and I took it on trust |
| P3-10 | `tools/measure_iv_requires.py` | `tools/` (new) | reproduces the design's section 4 table from the committed store | **done** (`a5e29b3`, `2017059`, `2b3794c`) — reproduces every figure exactly, and distinguishes "not refused" from "could not be measured" |
| P3-11 | **Optional.** Make `Sampling` horizon-aware (R134's latent flaw) | `src/volab/models/base.py`, `src/volab/compare/`, `tests/` | one source **per horizon**, or the horizon dropped from `series_source`; a contestant measured at h=22 and h=66 on one series is accepted | **not scheduled — user's call.** No Phase 3 story needs it; `har_rv_iv` is OLS and declares no `Sampling` |

## Blocks the recorded run

Found by P3-08 and **not** deferred: these must be settled before Phase 3's recorded run, which
is itself held until the user triggers the holdout reveal (R139).

**Status at 2026-11-01:** the first row is **resolved and merged** — PR #9, `bfedef3` on `main`
(R152), with the fix verified in `src/volab/compare/multiple.py` line by line rather than from a
report. R152's own measurement then overturned this row's stated consequence; see the row itself.
The second row is **open and not fixable here** — it is a vendor data gap, not a defect. The third
is a process gate under R139, not a defect.

| finding | why it blocks |
|---|---|
| **`superior_predictive_ability` crashes on proportional non-benchmark series, with certainty.** `src/volab/compare/multiple.py:295-298` pre-checks only the benchmark's pairs; `block_length` then re-checks **all** pairs and raises `ValueError`. Mechanism: under a Gaussian (VaR, ES) map `var/es` is constant, so an off-breach FZ0 value is `const + log(sigma)` and a **proportional** pair's differential is exactly constant — which is what `same_losses` (`dm.py:130-140`) tests for. So **an exactly proportional pair (within a few eps) trips it at any alpha where no origin breaches**, which at alpha=1% is usually. Pre-existing Phase 2 code; Phase 3's losses are its first caller. **Condition stated rather than assumed:** whether `phase3-iv` actually contains such a pair on the real panel is **unverified** — `TOY_COHORT` does, by construction (`_Scaled(models[2], 1.3)`), which is how it was found | **a real run would die *after* trials were appended **if the crash were reachable** — R152 measured that it is not, in any of the ten configurations tested, so this stated consequence did **not** hold for the recorded run; what genuinely blocked the run was the unverified claim itself**, leaving `trials.jsonl` carrying lines for a run that produced no comparison. **The crash itself is still unpinned** — the P3-08 implementer moved a test from stride 2 to stride 4 to get around it. *(Updated at sprint close: the FZ0 statistical path now **is** tested, by a `TOY_WIDE` cohort at h=1 stride 2 reaching 22 comparable origins, and the stride-4 test carries a comment recording the mechanism. But no test reproduces the crash — that belongs to the session fixing `multiple.py`, and on the toy panel every configuration reaching 11+ origins hits it.)* Its own session is proposed. **RESOLVED (R152)**, branch `fix/spa-block-length-names`. `names` is threaded through `block_length` and forwarded to `_unestimable`, so SPA's narrowed pre-check and the shared re-check agree by construction; the re-check is kept for the next caller. The crash is now pinned by `test_a_constant_differential_between_two_non_benchmark_series_does_not_refuse_spa` and, end to end on the cohort that holds the pair, by `test_a_run_on_the_proportional_toy_cohort_reaches_the_bootstrap_and_does_not_raise` (`TOY` at h=1 stride 2, 22 origins — the configuration P3-08 moved to stride 4 to avoid). A second, latent defect was fixed alongside: `politis_white` guarded with `figures.max()` on a pandas Series, which skips NaN, so a constant column's missing block length was replaced by another column's. **The unverified condition is now measured:** on the real panel at stride 5 over the recorded run's own window — every horizon `phase3-iv` answers on SPY (h=1, 5, 22) and h=1 on all three universe symbols, both alphas throughout — the closest FZ0 pair is 1.3e14 to 5.8e14 times `same_losses`' tolerance and no column is constant anywhere; `har_levels_mz`, the one plausible real candidate, refits its Mincer-Zarnowitz slope per origin, and no member of the cohort is a fixed-scale transform of another. So this was a toy-cohort hazard, not reachable on the real panel — priority changed, correctness not. Figures in R152 |
| **SPY's and IWM's dividend bias cannot be quantified.** The vendor serves distributions for QQQ only, so their `has_distribution` flags are uniformly `False` *(**row corrected at sprint close:** it previously described the hollow **disclosure**, which `1817f8c` already fixed via `distribution_flags_known` and the NOT KNOWN line. What remains is not a reporting defect but a data one.)* | the vol-target P&L for those symbols is wrong on every ex-date and **no number on the record says by how much** — the run can say it does not know, which is honest, but cannot bound the error |
| **The first real evaluator run moves `data/trials.jsonl` off 414**, which is a reported number, and the only guard is that `--cohort` is required | R139 holds the recorded run until the user triggers the reveal; until then no one may run the CLI against the real store |

## Deferred findings, carried to the final whole-branch review

**Tracked here, not only in the SDD ledger.** That ledger lives under `.superpowers/`, which is
gitignored and is deleted when the plan finishes, so a finding recorded only there disappears
exactly when someone would want to triage it. Raised by task 4's reviewer.

**Every row below was re-derived from the working tree at `01fcee7` (`main`, PR #12 merged), from
the code each row names and never from the row's own wording or from a commit message — R150's
standard. Seven of the fifteen did not state what the repository contained**, and they failed in
four distinct ways, which is why each row now carries its own verified state and the evidence for
it. **Three rows (8, 9, 15) were discharged after they were written**, so a reader working the
table as a queue would have re-done finished work. **One (4) was half-discharged** — its wording
was fixed and the gap behind it remains, now disclosed. **One (5) was wrong when it was written**:
the assertions it said were missing had landed in `04479bb` and `fbbf8be`, both ancestors of the
row's own birth commit `210a3e4`, which is exactly the failure that produced the withdrawn P3-09.
**Two (3, 13) still hold but pointed at the wrong lines.** The remaining eight were accurate.
Ruling: R155. **Rows are numbered here so they can be cited; the order is unchanged.**

**The `verdict` column is a recommendation only. Nothing in this table was fixed in the pass that
verified it**, deliberately: re-deriving a queue and working it are different jobs, and mixing
them is how a board starts describing its own intentions instead of the tree. `now` means worth
fixing before anything else in the table; `before the reveal` means it weakens a guard on what
the registered read reads or reports, so it must not still be open when the holdout is revealed;
`cosmetic` means wording, redundancy or a hard-coded literal with no consequence for what the run
reports. **Rows 3 and 6 propose consolidation by deleting a test. R154 rules that a deleted test
is invisible to every gate this project has**, so neither may be done without recording the suite
count either side of it.

| # | from | finding | verified state at `01fcee7` | verdict |
|---|---|---|---|---|
| 1 | P3-01 | The fence's h>=2 side is derived, not parsed: only the "minus" clause is read, so a document amendment adding a contestant to h>=2 only would not be caught | **OPEN, as described.** `tests/test_preregistration_fence.py:73` is the only read of the h>=2 bullet and it is `re.search(r"minus ((?:`\w+`[, ]*)+)\*\*", above)` — the removal clause alone. The bullet's own names are never parsed, so `set(h1) - set(REGISTERED_ABOVE)` is compared against the removal and nothing against an addition. *Deferred because* the h=1 set plus the one removal fully determines the h>=2 set as the document is worded today — true, and it is the document's wording that the fence then depends on | **before the reveal** — it is the tie between the registered contestant set and the document that froze it |
| 2 | P3-01 | The pre-registration's own prose counts ("Seven at h=1; six at ...", line 45) and its section 4 trial-count table are tied to the code by no test | **OPEN, as described.** `tests/test_preregistration_fence.py` is the only test that opens the document (`PREREGISTRATION`, `:36`) and it reads exactly the two section-1 bullets (`_bullet`, `:59`). The prose sentence at `docs/holdout-preregistration.md:45` and the section 4 ledger table at `:147-152` are read by nothing. The only other mention of 414 anywhere under `tests/` is a docstring, `tests/test_leaderboard.py:265`, which pins per-line structure and not the table's arithmetic | **before the reveal** — section 4 is the registered trial count the reveal reports a delta against |
| 3 | P3-02 | `_NeedsIvVar` and its two witnesses (`tests/test_domain.py:198-224`) are subsumed by `test_clause_10_checks_every_probed_horizon` | **OPEN, but the line range is wrong** — the class is at `tests/test_domain.py:192` and its witnesses at `:208` and `:218`, not `198-224`. The subsumption claim holds: the two mutations those witnesses target — a clause that reads `requires` alone, and one that checks a column at a probe of another horizon — both also fail `test_clause_10_checks_every_probed_horizon` (`:264`), because `_NeedsIvVarAtTwoHorizons` defaults to `requires=()` (`:242`) and so exercises the empty-`requires` path too. The docstring edit the row anticipates is real and locatable: `tests/model_conformance.py:584` names `test_clause_10_checks_a_requirement_declared_only_through_per_horizon` as clause 10's witness | **cosmetic.** Under R154 a deletion must record the suite count either side; the saving is three short tests |
| 4 | P3-02 | Clause 10's docstring cites a witness with two probes as covering "the second and third horizon"; no test covers a third | **WORDING DISCHARGED after this row was written**, by `ea8ace3` ("correct three docstrings"), which is **not** an ancestor of this row's birth commit `210a3e4`. `tests/model_conformance.py:587-589` now reads "That witness has two probes, so it covers the second and **not** a third: a mutation that checks `(toy, *also[:1])` passes it, and nothing pins a third probe." **The gap itself is still open** — no test pins a third probe — but it is now disclosed by the docstring that used to overclaim it, which is what the row asked for | **cosmetic.** The overclaim was the defect; the gap is named by the code that has it |
| 5 | P3-02 | The 13/12 origin counts and the HAR-floor clearance of origins 60-61 are evidenced only by that round's 553-passed run (the suite is now 1716), not from the diff | **OPEN, but WRONG WHEN WRITTEN, and the suite figure is stale twice over.** The counts are asserted in the diff, not merely implied by a passing run: `tests/test_domain.py:561-562` (13/13 clean, 12/12 with the interior gap), `:614` and `:622` (13/13 declared at h=1, 12/12 at h=5), and the HAR-floor clearance of origins 60-61 at `:647` with `:664-666` (`run(1) == []`, `len(run(5)) == 2`). Those landed in `04479bb` (US-3.9a) and `fbbf8be` (P3-02), **both ancestors of `210a3e4`, the commit that wrote this row** — the same class of failure as the withdrawn P3-09. What is genuinely unmet is the row's own remedy: the literals are hard-coded, not derived from the window bounds `sessions[98]`..`sessions[110]`, so moving the window silently moves the expectation. **The suite is 1731 at `01fcee7`**, not 1716 and not 553 | **cosmetic.** A construction test would derive 13 from the window; the figures are pinned either way |
| 6 | P3-03 | `test_it_is_named_and_fits_the_target_side` is mostly covered by the conformance suite | **OPEN, as described.** `tests/test_iv_model.py:69` asserts `isinstance(model, Model)`, the name, and `fit_depends_on_horizon is True` — all three of which the conformance suite reaches for every contestant it is run on. Redundancy, and `tests/test_iv_model.py:60` (the per-horizon declaration and the h=66 refusal) is the file's non-redundant neighbour | **cosmetic.** R154 applies: deleting it changes the count, and the count is a gate |
| 7 | final review | The `Domain` docstring says the thin-training route "counts non-null rows of `requires_at`", omitting that `training_rows` also requires the HAR regressors and the response to be non-null | **OPEN, as described.** `src/volab/models/base.py:129` still reads "the thin-training skip, which counts non-null rows of `requires_at(horizon)`". The function it describes says the rest itself: `src/volab/walkforward/__init__.py:871` is "Training rows that count: non-null in the HAR regressors, the response and `required`", implemented at `:878` as `dropna(subset=[*HAR_REGRESSORS, column, *required])`. So the loose sentence is one hop from the exact one | **cosmetic** — the authoritative docstring is already correct and is the one a reader of `training_rows` meets |
| 8 | final review | No test reproduces the `multiple.py` proportional-pair crash; a strict-xfail test would pin it | **DISCHARGED after this row was written** — and not by an xfail but by a fix plus two green tests, under **R152** (`docs/superpowers/phase3-decisions.md:369`), commit `aa54528`, merged as `bfedef3`. The row's birth commit `166f881` **is** an ancestor of `aa54528`, so this went stale rather than being wrong. The crash is pinned at the unit level by `test_a_constant_differential_between_two_non_benchmark_series_does_not_refuse_spa` (`tests/test_compare.py:949`) and end to end by `test_a_run_on_the_proportional_toy_cohort_reaches_the_bootstrap_and_does_not_raise` (`tests/test_evaluators_cli.py:954`) — the `TOY` h=1 stride-2 configuration P3-08 had moved to stride 4 to avoid | **closed.** No work left |
| 9 | final review | Whether `phase3-iv` contains an exactly proportional pair on the **real** panel is unverified | **DISCHARGED after this row was written**, by a measurement rather than a test — **R152**, `docs/superpowers/phase3-decisions.md:369`, same `aa54528` lineage. On the real panel at stride 5 over the recorded run's own window, every horizon `phase3-iv` answers on SPY (h=1, 5, 22) plus h=1 on all three universe symbols, both alphas, the closest FZ0 pair is **1.3e14 to 5.8e14 times** `same_losses`' tolerance and no column is constant anywhere. Because the evidence is a figure in a ruling and not a green test, **nothing in the suite or in `tools/` reproduces it** — a reader must re-run the measurement to re-check it, which is the honest limit of this discharge | **closed**, with the caveat that its evidence is a recorded measurement, not a gate |
| 10 | P3-08 | `_describe`'s NOT KNOWN branch suppresses a nonzero ex-date count if the raw dividend tier and the curated flags disagree (a stale-curation state). The count is still written to the ledger | **OPEN, and the mechanism is blunter than "suppresses".** `src/volab/evaluators/__main__.py:368-373` does not read the count at all on that branch: it prints the **literal** "a count of 0", so a nonzero `distribution_sessions` is contradicted rather than hidden, while the true branch at `:360-366` prints the real figure. The row's second sentence still holds — the count does reach the ledger, via `src/volab/evaluators/sink.py:88` (and `:139`) — so the console and `decisions.jsonl` can disagree on the same run | **before the reveal.** A printed literal that contradicts the ledger beside it is a reporting defect on the run's own output, not merely an odd store state |
| 11 | P3-08 | The `distribution_flags_known` parameter of `decide` shadows the module function of the same name | **OPEN, as described.** Module function at `src/volab/evaluators/__main__.py:116`, keyword-only parameter of `decide` at `:221`, and `:401` calls the function into a `known` mapping before `:460` passes it in as that parameter — so the two never need to be in scope together at the point either is used | **cosmetic.** `mypy --strict` and `ruff` are both clean over it today |
| 12 | P3-05 | A docstring estimates "a return of about 20" for the sentinel close; `log(1e9/~100)` is about 16. The bound it motivates still holds | **OPEN, as described, and the arithmetic is now closed.** `tests/test_evaluators.py:564` still says "a return of about 20". `SEALED_CLOSE = 1.0e9` (`:485`) and `_close_at(index) = 100.0 + 0.7 * index + 3.0 * sin(index/3)` (`:488-490`), so the log return against the sentinel runs from `log(1e9/100) = 16.1` down to about `15.0` by index 300 — never near 20, and the magnitude bound it motivates is unaffected | **cosmetic** — an aside in a docstring, and the test it sits on asserts the bound, not the estimate |
| 13 | P3-05 | The leak detector (`tests/test_evaluators.py:872`) matches at `atol=1e-12, rtol=0` and its red witness plants the bit-identical float, so only an exact-value leak is demonstrated caught; a leak through any transform is invisible | **OPEN, but the line is wrong** — `tests/test_evaluators.py:872` is `test_the_evaluators_never_unlock_the_holdout`, an unrelated scan. The detector is `_unknowable_forward_returns_in` (`:757`), matching at `np.isclose(cells, value, rtol=0.0, atol=1e-12)` (`:774`); the green test is `test_no_horizon_return_reaches_a_fit` (`:787`) and the red witness `test_the_leak_detector_can_fail` (`:815`), which plants `value = forward[view.origin]` (`:830`) — the bit-identical float, exactly as the row says. So only an exact-value leak is demonstrably caught: a leak through `2 *`, a rescale or a sign flip passes | **before the reveal.** This is the guard that separates a scoring read from a look-ahead; widening it is a design question but an un-widened detector should not be the only thing standing between the panel and the reveal |
| 14 | P3-04 | `_imports` ignores relative imports (`from ..models import x`), so the derived guard could be bypassed by one; none exist in `volab/compare/` today | **OPEN, as described, with the mechanism confirmed.** `tests/test_compare.py:1671-1679` never reads `node.level`, so `from ..models import x` records `models` and `models.x` — neither prefixed `volab.models.`, so `_contestant_modules` (`:1681`) returns nothing — and `from . import x` has `node.module is None` and is skipped at the `and node.module` guard outright. Re-checked: **no relative import exists anywhere under `src/volab/`**, so the bypass is latent, not live. It is not widened by the dotted route either — `src/volab/models/__init__.py` imports nothing by design, so `import volab.models` pulls no contestant | **now.** Reading `node.level` is a few lines in a test helper that already has a nine-case parametrisation (`:1727`) to extend, and it closes a hole in a guard the project relies on rather than adding a new one |
| 15 | P3-04 | `test_every_named_cohort_begins_with_har_levels` (`tests/test_model_interface.py:141`) iterates a one-element tuple despite its name; its docstring says a later story should extend it and none did | **DISCHARGED after this row was written**, by `ea8ace3`, which is **not** an ancestor of this row's birth commit `210a3e4`. The test is now at `tests/test_model_interface.py:142` and loops `for cohort in (BASELINE_COHORT, *COHORTS.values())` at `:146`, so it iterates the baseline plus every registered cohort — the name is now accurate. The docstring (`:143-145`) no longer defers to a later story; it states that the baseline and every registered cohort are checked and points at `tests/test_compare.py` for the same assertion over `COHORTS`. The line number in the row is also one short of the `def` | **closed.** No work left |

## Held, not forgotten

| item | why it is held | releases on |
|---|---|---|
| Phase 3's recorded run | D5: a run appends to `trials.jsonl`, and the reveal reports a delta against that count | **the user triggering the holdout reveal** (`holdout_end` 2026-10-30) |
| **Carried 3** — `egarch11_skewt` simulating at h=1, R115 withholding its significance (R124) | the withholding is part of **what the registered read reports**; changing it before the reveal changes the registered comparison | **the reveal.** Read R124's post-hoc-selection reasoning first |

## Coordination

**US-3.9b landed at `761cf71` and is merged here at `7827af0`.** The Phase 2 completion board's
`todo` was stale; this was confirmed with the Phase 2 session, not inferred. `models/base.py` and
`compare/cohorts.py` are free, and **no Phase 3 story is blocked on another session**.

**`main` carries Phase 2 completion.** *(Updated at sprint close; this paragraph previously said
#6 was open and `main` was at `b8d11cd`, and the delivery audit caught it stale — the same failure
that produced the withdrawn P3-09.)* PR #6 **merged** as `3e76790`, which is this branch's
merge-base, so Phase 3 sits on top of it and is **0 commits behind**.

**Branch protection is live on `main`**: a pull request is required, the `check` status must
pass, strict mode means this branch must be current with `main` before merging, and force pushes
and deletions are blocked. A Phase 3 PR sitting `BLOCKED` until CI is green is the gate working.

R131 also records that "never two implementers writing the same files" does **not** cover
mutation testing: a plant-and-revert temporarily breaks a file in `src/` that every concurrent
agent can see. Phase 3 works in its own worktree (`../vol-lab-phase3`) partly for this reason.
A plant-and-revert still needs exclusive `src/`, or a scratch copy.

## Method

Test-first, with a planted defect proving the test bites wherever test-first was departed from.
A review seat on every story, whose **first** instruction is to read each docstring against what
the test body executes — three times in Phase 2 a docstring described a stronger test than the
code performed (R79, R82, R129). Nothing is done on assertion: a story closes when a commit
exists, its tests pass, and `ruff check`, `ruff format --check`, `mypy --strict` and `pytest` are
green. Everything through `uv run --locked`.

---

## Sprint close

**Phase 3 is complete as specified. That is deliberately not the same as *exited*, and the
difference is the first thing any write-up citing this phase must carry.** The framework document
that defines each phase's exit criterion is not in this repository (R135); the scope here was
reconstructed from the Phase 2 board's own "Not this phase's to fix" section. The phase met the
reconstruction. Whether the reconstruction is the requirement, only the framework can say.

**Nine stories done, one withdrawn, one optional and unscheduled.** Closed on an independent
delivery audit and a whole-branch review rather than on assertion: the audit re-derived every
acceptance check from code instead of from commit messages, confirmed all 30 cited commits exist
and are ancestors of `main`, ran the gates itself, and ran `tools/measure_iv_requires.py` to
reproduce the design's coverage table a third time. It found **no row the repository does not
support**.

Verified on `main` at the close: **1723 tests passing**, `mypy --strict` clean over 41 source
files, `ruff check` and `ruff format --check` clean over 97, and `data/trials.jsonl` at 414 lines
with `data/comparisons.jsonl` at 52 — byte-identical to the phase's merge-base. The count is
independently the same figure the P3-08 fix session measured on the merged tree.

**No wall-clock figure is recorded here, deliberately.** Every suite run at this close shared the
machine with another worktree's run, and the same 1723 tests read anywhere from 232s to 907s. A
duration measured under contention is not a measurement of this suite, and the one place a runtime
number matters — the shared real-panel loader on `perf/share-real-panel-loads` — must measure it on
a quiet machine or report that it could not.

**What the phase exits on.** `python -m volab.evaluators` turns a variance forecast into decisions
and scores them: Gaussian VaR/ES at both alphas; the Fissler-Ziegel joint loss assembled into a
`LossSeries` and handed to the existing `volab.compare.analyse`, so DM, MCS and SPA apply
unchanged and `walkforward.LOSSES` stays exactly `{"qlike", "mse"}`; Kupiec and Christoffersen
with a refusal where the sampling window overlaps and a withheld p-value with its reason wherever
the independence statistic is zero by construction; vol-target P&L with turnover, an ex-date count
and a flag saying whether that count means anything; and `data/decisions.jsonl`. Beside it,
`har_rv_iv` declares the horizon-matched implied-variance column **per horizon** and refuses h=66
through `Domain`, not through a comment. Merged as PR #7, `b2bd304`.

**What was not done, each deliberately.**

- **No recorded run** (R139, D5). A run appends to `data/trials.jsonl`, whose delta the holdout
  reveal reports, and the reveal belongs to the user. Both ledgers are byte-identical to the
  phase's merge-base at 414 and 52 lines.
- **Carried 3** (`egarch11_skewt` simulating at h=1) is held until the reveal, because R115's
  withholding is part of what the registered read *reports*.
- **P3-09 was withdrawn, not skipped.** R129's flag was already discharged by `b04f079`
  (US-3.14), an ancestor of this branch. The story existed because the Phase 2 board's
  carried-items text predates US-3.14 and I took it on trust. The implementer returned
  NEEDS_CONTEXT rather than writing a redundant test, which was right.
- **P3-11** (making `Sampling` horizon-aware, R134's latent flaw) is the user's call and no
  Phase 3 story needs it. `har_rv_iv` is OLS and declares no `Sampling`.

**What the phase found out about itself, which is the part worth inheriting.** **Eleven** separate
instances of a test or docstring claiming more than its code performs — the same defect R79, R82
and R129 each recorded in Phase 2. Every one was caught by a review seat, never by the author who
wrote it. Several were introduced *by the correction for an earlier one*: R136 was wrong three
times in three different ways before settling at a four-route table, each version closer, each
error found by someone reading the code rather than the ruling. Two rules were promoted into the
plan's Global Constraints mid-phase as a result: every `pytest.raises(match=...)` must be specific
to the branch it names, and a test asserting a numeric consequence witnesses nothing unless the
consequence actually follows.

The sharpest instance: a test named "wrong variance loses" passed while the FZ0 formula was
broken, because it only tested under-forecasting — with the `log(ES)` term dropped, the
over-forecast margin was exactly **zero**. And a withholding guard had no witness in the
over-withholding direction until one row was added; mutating the condition had passed all 1,644
tests.

**Seven errors of the orchestrator's, recorded because the pattern matters more than the count.**
The `Evaluation.comparable` mechanism; the rescale-hazard framing (OLS is scale-equivariant, so
the test written for it could never fail); a "roughly nine origins" estimate against a measured
five; the Gaussian ES multiplier, wrong by 8.8e-9 and so failing the `rel=1e-9` its own assertion
stated; the overlap count (four, not three); the position-cap boundary, wrong twentyfold; and the
stale board line that invented P3-09 from nothing. Four were caught by reviewers, three by
implementers. **The pattern: the hard claims were verified and the easy ones were trusted.** The
full list of 45 process rulings is in `docs/superpowers/phase3-controller-rulings.md`.

**Rulings.** R135-R141 (the phase's design decisions), R150 (the numbering allocation and a
standing rule never to re-cut a branch on another session's report of its state), R151 (derive a
set the codebase will extend; do not list it), R152 (the SPA fix, and the measurement that
overturned this board's own stated consequence). R142-R149 belong to the Phase 2 completion
session, which used only R142; the gap is deliberate.

**Still open, and none of it blocks the merge.** **Eleven** of the fifteen deferred findings in
the table above are still open — rows 8, 9 and 15 were discharged after they were written and row
4's overclaim with them, which R155 records along with what a board used as a queue costs. Each
open row now carries its verified state at `01fcee7`, the file and line that evidences it, and a
recommendation (`now`, `before the reveal`, or `cosmetic`) that nothing in that pass acted on. One blocker that cannot be fixed here: SPY's and IWM's dividend bias is
unquantifiable because the vendor serves distributions for QQQ only — the run says it does not
know, which is honest, but it is not a measurement. And the standing ask, unchanged since the
phase began: **commit the framework document into `docs/`**, so the next boundary does not rest on
a reconstruction. This one did, and the clearest evidence that it matters is that a stale board
line produced an entire story that had to be withdrawn.
