# Phase 3 sprint board — IV contestant and decision evaluators

**Design:** `docs/superpowers/specs/2026-10-29-phase3-iv-and-decision-evaluators-design.md`
**Decisions:** `docs/superpowers/phase3-decisions.md`, from R134.
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
| P3-01 | Pin the registered cohorts to the pre-registration | `tests/` | a test fails if `SHOOTOUT.contestants` or `MULTISTEP.contestants` stops matching `docs/holdout-preregistration.md` section 2; docstring quotes it and names R130 and `65617ee` | todo |
| P3-02 | `Domain.requires_at`, `Cohort.requires_at`; **delete** `Cohort.requires` | `src/volab/models/base.py`, `src/volab/walkforward/__init__.py`, `tests/` | a dead-horizon pair is refused at construction; `coverage_refusal` fires at h=1 and **not** at h=5 or h=22 for `har_rv_iv`'s declaration, against the real store; no horizon-blind accessor remains | **blocked — US-3.9b owns these two files** |
| P3-03 | `har_rv_iv` | `src/volab/models/iv.py` (new), `tests/` | conformance suite passes; `domain.max_horizon == 22` so `--horizon 66` is refused by the machinery, not a comment; no numpy/scipy/arch import | todo (needs P3-02 for its domain test) |
| P3-04 | The `phase3-iv` cohort | `src/volab/compare/cohorts.py`, `tests/` | seven contestants, `har_levels` first (R87), EGARCH absent (R114); `SHOOTOUT` and `MULTISTEP` byte-identical | **blocked — US-3.9b** |
| P3-05 | `evaluators/`: `var_es`, `fz0` | `src/volab/evaluators/` (new), `tests/` | FZ0 assembled into a `LossSeries` and accepted by `volab.compare.analyse`; `walkforward.LOSSES` unchanged | todo |
| P3-06 | Kupiec, Christoffersen, and the stride refusal | `src/volab/evaluators/`, `tests/` | the coverage tests **refuse** where `stride < horizon` and name the condition; witness that the refusal fires at h=22 and does not at h=5 | todo |
| P3-07 | Vol-target P&L, turnover, ex-date count | `src/volab/evaluators/`, `tests/` | the `has_distribution` count inside the window is printed beside the P&L, so the dividend bias is visible | todo |
| P3-08 | The decisions sink and `__main__` | `src/volab/evaluators/`, `tests/` | writes `data/decisions.jsonl`; `data/trials.jsonl` line count unchanged by an evaluator run | todo |
| P3-09 | A witness for `har_levels_mz` at h >= 22 (**carried 2**) | `tests/` | R129's flag discharged by a test that bites, not by ledger lines | todo |
| P3-10 | `tools/measure_iv_requires.py` | `tools/` (new) | reproduces the design's section 4 table from the committed store | todo |

## Held, not forgotten

| item | why it is held | releases on |
|---|---|---|
| Phase 3's recorded run | D5: a run appends to `trials.jsonl`, and the reveal reports a delta against that count | **the user triggering the holdout reveal** (`holdout_end` 2026-10-30) |
| **Carried 3** — `egarch11_skewt` simulating at h=1, R115 withholding its significance (R124) | the withholding is part of **what the registered read reports**; changing it before the reveal changes the registered comparison | **the reveal.** Read R124's post-hoc-selection reasoning first |

## The one coordination hazard

`src/volab/models/base.py` and `src/volab/compare/cohorts.py` are owned by **US-3.9b**, which the
Phase 2 completion board still lists as `todo`. P3-02 and P3-04 must not be started until that
ownership is settled with the Phase 2 session. Do not infer from a stale board that US-3.9b was
abandoned.

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
