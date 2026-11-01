# Phase 3 sprint board — IV contestant and decision evaluators

**Design:** `docs/superpowers/specs/2026-10-29-phase3-iv-and-decision-evaluators-design.md`
**Decisions:** `docs/superpowers/phase3-decisions.md`. **Numbering is allocated (R150):**
R135-R141 and **R150 onward** are Phase 3's; **R142-R149 are reserved** for the Phase 2
completion session. The next Phase 3 ruling is **R151**, not R142 — the gap is deliberate.
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
| **`superior_predictive_ability` crashes on proportional non-benchmark series, with certainty.** `src/volab/compare/multiple.py:295-298` pre-checks only the benchmark's pairs; `block_length` then re-checks **all** pairs and raises `ValueError`. Mechanism: under a Gaussian (VaR, ES) map `var/es` is constant, so an off-breach FZ0 value is `const + log(sigma)` and a **proportional** pair's differential is exactly constant — which is what `same_losses` (`dm.py:130-140`) tests for. So **an exactly proportional pair (within a few eps) trips it at any alpha where no origin breaches**, which at alpha=1% is usually. Pre-existing Phase 2 code; Phase 3's losses are its first caller. **Condition stated rather than assumed:** whether `phase3-iv` actually contains such a pair on the real panel is **unverified** — `TOY_COHORT` does, by construction (`_Scaled(models[2], 1.3)`), which is how it was found | **a real run dies *after* trials were appended**, leaving `trials.jsonl` carrying lines for a run that produced no comparison. **The crash itself is still unpinned** — the P3-08 implementer moved a test from stride 2 to stride 4 to get around it. *(Updated at sprint close: the FZ0 statistical path now **is** tested, by a `TOY_WIDE` cohort at h=1 stride 2 reaching 22 comparable origins, and the stride-4 test carries a comment recording the mechanism. But no test reproduces the crash — that belongs to the session fixing `multiple.py`, and on the toy panel every configuration reaching 11+ origins hits it.)* Its own session is proposed. **RESOLVED (R152)**, branch `fix/spa-block-length-names`. `names` is threaded through `block_length` and forwarded to `_unestimable`, so SPA's narrowed pre-check and the shared re-check agree by construction; the re-check is kept for the next caller. The crash is now pinned by `test_a_constant_differential_between_two_non_benchmark_series_does_not_refuse_spa` and, end to end on the cohort that holds the pair, by `test_a_run_on_the_proportional_toy_cohort_reaches_the_bootstrap_and_does_not_raise` (`TOY` at h=1 stride 2, 22 origins — the configuration P3-08 moved to stride 4 to avoid). A second, latent defect was fixed alongside: `politis_white` guarded with `figures.max()` on a pandas Series, which skips NaN, so a constant column's missing block length was replaced by another column's. **The unverified condition is now measured:** on the real panel at stride 5 over the recorded run's own window — every horizon `phase3-iv` answers on SPY (h=1, 5, 22) and h=1 on all three universe symbols, both alphas throughout — the closest FZ0 pair is 1.3e14 to 5.8e14 times `same_losses`' tolerance and no column is constant anywhere; `har_levels_mz`, the one plausible real candidate, refits its Mincer-Zarnowitz slope per origin, and no member of the cohort is a fixed-scale transform of another. So this was a toy-cohort hazard, not reachable on the real panel — priority changed, correctness not. Figures in R152 |
| **SPY's and IWM's dividend bias cannot be quantified.** The vendor serves distributions for QQQ only, so their `has_distribution` flags are uniformly `False` *(**row corrected at sprint close:** it previously described the hollow **disclosure**, which `1817f8c` already fixed via `distribution_flags_known` and the NOT KNOWN line. What remains is not a reporting defect but a data one.)* | the vol-target P&L for those symbols is wrong on every ex-date and **no number on the record says by how much** — the run can say it does not know, which is honest, but cannot bound the error |
| **The first real evaluator run moves `data/trials.jsonl` off 414**, which is a reported number, and the only guard is that `--cohort` is required | R139 holds the recorded run until the user triggers the reveal; until then no one may run the CLI against the real store |

## Deferred findings, carried to the final whole-branch review

**Tracked here, not only in the SDD ledger.** That ledger lives under `.superpowers/`, which is
gitignored and is deleted when the plan finishes, so a finding recorded only there disappears
exactly when someone would want to triage it. Raised by task 4's reviewer.

| from | finding | why deferred |
|---|---|---|
| P3-01 | The fence's h>=2 side is derived, not parsed: only the "minus" clause is read, so a document amendment adding a contestant to h>=2 only would not be caught | the h=1 set plus the one removal fully determines the h>=2 set as the document is worded today |
| P3-01 | The pre-registration's own prose counts ("Seven at h=1; six at ...", line 45) and its section 4 trial-count table are tied to the code by no test | out of that task's scope |
| P3-02 | `_NeedsIvVar` and its two witnesses (`tests/test_domain.py:198-224`) are subsumed by `test_clause_10_checks_every_probed_horizon` | consolidation only; costs a docstring edit elsewhere |
| P3-02 | Clause 10's docstring cites a witness with two probes as covering "the second and third horizon"; no test covers a third | wording, not behaviour |
| P3-02 | The 13/12 origin counts and the HAR-floor clearance of origins 60-61 are evidenced only by that round's 553-passed run (the suite is now 1716), not from the diff | would need a dedicated construction test |
| P3-03 | `test_it_is_named_and_fits_the_target_side` is mostly covered by the conformance suite | redundancy, not a gap |
| final review | The `Domain` docstring says the thin-training route "counts non-null rows of `requires_at`", omitting that `training_rows` also requires the HAR regressors and the response to be non-null | looseness, not an overclaim |
| final review | No test reproduces the `multiple.py` proportional-pair crash; a strict-xfail test would pin it | belongs to the session fixing `multiple.py`, and every toy configuration reaching 11+ origins hits it |
| final review | Whether `phase3-iv` contains an exactly proportional pair on the **real** panel is unverified | the crash is conditional on it; `TOY_COHORT` contains one by construction |
| P3-08 | `_describe`'s NOT KNOWN branch suppresses a nonzero ex-date count if the raw dividend tier and the curated flags disagree (a stale-curation state). The count is still written to the ledger | an inconsistent-store state, not a code path |
| P3-08 | The `distribution_flags_known` parameter of `decide` shadows the module function of the same name | harmless |
| P3-05 | A docstring estimates "a return of about 20" for the sentinel close; `log(1e9/~100)` is about 16. The bound it motivates still holds | an order-of-magnitude aside, not a claim that a test checks something it does not |
| P3-05 | The leak detector (`tests/test_evaluators.py:872`) matches at `atol=1e-12, rtol=0` and its red witness plants the bit-identical float, so only an exact-value leak is demonstrated caught; a leak through any transform is invisible | acceptable as a ratchet; widening it is its own design question |
| P3-04 | `_imports` ignores relative imports (`from ..models import x`), so the derived guard could be bypassed by one; none exist in `volab/compare/` today | not addressed, and no relative import exists in the package |
| P3-04 | `test_every_named_cohort_begins_with_har_levels` (`tests/test_model_interface.py:141`) iterates a one-element tuple despite its name; its docstring says a later story should extend it and none did | **protection is real** at `tests/test_compare.py:1400-1402`, which loops `COHORTS.values()` including `phase3-iv`; the defect is the name and a stale instruction |

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

**Still open, and none of it blocks the merge.** Fifteen deferred findings in the table above, each
with why it was deferred. One blocker that cannot be fixed here: SPY's and IWM's dividend bias is
unquantifiable because the vendor serves distributions for QQQ only — the run says it does not
know, which is honest, but it is not a measurement. And the standing ask, unchanged since the
phase began: **commit the framework document into `docs/`**, so the next boundary does not rest on
a reconstruction. This one did, and the clearest evidence that it matters is that a stale board
line produced an entire story that had to be withdrawn.
