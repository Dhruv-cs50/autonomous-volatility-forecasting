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
| P3-06 | Kupiec, Christoffersen, and the stride refusal | `src/volab/evaluators/`, `tests/` | the coverage tests **refuse** where `stride < horizon` and name the condition; witness that the refusal fires at h=22 and does not at h=5 | todo |
| P3-07 | Vol-target P&L, turnover, ex-date count | `src/volab/evaluators/`, `tests/` | the `has_distribution` count inside the window is printed beside the P&L, so the dividend bias is visible | todo |
| P3-08 | The decisions sink and `__main__` | `src/volab/evaluators/`, `tests/` | writes `data/decisions.jsonl`; `data/trials.jsonl` line count unchanged by an evaluator run | todo |
| P3-09 | A witness for `har_levels_mz` at h >= 22 (**carried 2**) | `tests/` | R129's flag discharged by a test that bites, not by ledger lines | todo |
| P3-10 | `tools/measure_iv_requires.py` | `tools/` (new) | reproduces the design's section 4 table from the committed store | todo |
| P3-11 | **Optional.** Make `Sampling` horizon-aware (R134's latent flaw) | `src/volab/models/base.py`, `src/volab/compare/`, `tests/` | one source **per horizon**, or the horizon dropped from `series_source`; a contestant measured at h=22 and h=66 on one series is accepted | **not scheduled — user's call.** No Phase 3 story needs it; `har_rv_iv` is OLS and declares no `Sampling` |

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
| P3-02 | The 13/12 origin counts and the HAR-floor clearance of origins 60-61 are evidenced only by the 553-passed run, not from the diff | would need a dedicated construction test |
| P3-03 | `test_it_is_named_and_fits_the_target_side` is mostly covered by the conformance suite | redundancy, not a gap |
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

**`main` does not yet carry Phase 2 completion.** It is at `b8d11cd`; PR #6 is open and
`BLOCKED`. Re-cutting this branch from `main` would lose everything the design is built on, so
this branch stays based on `phase-2-completion-groundwork` and merges forward once #6 lands.

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
