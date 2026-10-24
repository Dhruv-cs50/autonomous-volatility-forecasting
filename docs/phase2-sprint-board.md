# Phase 2 sprint board — the walk-forward evaluation harness

One board per phase; Phase 1's is `sprint-board.md`. Tracked in git deliberately: a plan
`git clean` erases is not a plan.

Sizing is **scope, not wall-clock**: a story is one dispatch of new work, and a sprint closes
before the next opens. Nothing is done on assertion — a story closes when a commit exists, its
tests pass and the gates are green.

**Execution shape, and where it was wrong.** Run natively by the session controller for the
build, with an independent `code-reviewer` on the walk-forward and a `delivery-auditor` on this
board at the sprint boundary, as the delivery model requires, and an advisor pass over the
fixes those two produced -- which found the ninth escape inside the fix for the eighth.

**The native-only part of that was a mistake, and the record should say so.** The original
justification is below and it still reads plausibly; what it got wrong was the claim that "the
tests are the seat". The two independent seats found, between them: a `ts`-based target slice
that passed **all 25** walk-forward tests, a forecast biased low by a factor of four, a
singularity threshold that silently refused calm windows, an import-ratchet violation, two
rulings with no test at all, a ledger that was not tracked despite claiming to be, and four
ledger lines naming a tree that could not have produced them. A further pass over *those
fixes* then found that the target audit checked the slice rather than what the fit consumed --
R78, the ninth escape, inside the code written to close the eighth. Phase 1's R9 — review seats go
where look-ahead can hide — was right, and the walk-forward's target slicing is exactly such a
place. The original reasoning follows, kept because a decision that turned out wrong is worth
more on the record than removed from it:

The
reason is specific rather than a preference: every finding in this phase came from *running*
something and then changing the design in response — three pre-measurements that each moved the
plan, a wrong fix caught by its own red cases, a wrong blindness rule caught by a derived
assertion, and a loss domain discovered on the first full data run. That is a tight
measure-decide-remeasure loop, and the handoff cost of a review seat per step would have
exceeded what a seat could add on work whose correctness is settled by output rather than by
reading. Phase 1's rule — review seats go where look-ahead can hide — is satisfied differently
here: **the tests are the seat**, and each one asserts a derived surface rather than a count, so
a wrong implementation cannot pass by being plausible.

## Sprint 1 — The refusal layer · CLOSED

| Story | Goal | Files | Acceptance | State |
|---|---|---|---|---|
| US-2.1 | Three pre-measurements before any code: vintages, E1's real detection power, where E2 can hide | — | each one either confirms or changes the plan, and is recorded | done `3c7e087` (doc-only) — M1/M2/M3 in `phase2-decisions.md`, all three changed it. Probes committed by US-2.13, so the quoted output is now re-runnable. |
| US-2.2 | One panel-assembly path, plus `truncate`, shared by CLI, tests and walk-forward | `volab/panel.py`, `volab/features/__main__.py` | the CLI's existing tests still pin it; `truncate` carries its own witness | done `21dc15b` |
| US-2.3 | Truncation equivalence as the primary point-in-time test | `tests/test_truncation_equivalence.py` | silent on clean code at every synthetic cutoff and every sampled real one; red cases assert the exact detectable surface | done `21dc15b` |
| US-2.4 | Close E2's class | `tests/test_process_isolation.py`, `tests/isolated_build.py`, `tests/memo_plant.py` | the same plant escapes in-process and is caught across processes | done `21dc15b` |
| US-2.5 | The seventh escape, found by US-2.3 on its first run: extent dependence at a cutoff with no bar | `volab/features/build.py` | both panels emit the same rows; the persisted table does not move; the planted peeks still bite | done `21dc15b` |

Closed: 663 tests, `ruff`, `mypy` and the import ratchet green with `ALLOWED` unchanged at
`{"store.py"}`.

**US-2.5 is worth reading as a story about the method, not about a bug.** The first fix cut the
session span back to the cutoff. It restored the invariant and silenced all three planted forward
peeks, because the engine no longer held a future to read — a vacuous suite traded for a narrow
bug. The red cases caught it on the next run. I had already written the comment explaining why
the wrong version was right.

## Sprint 2 — Evidence and the record · CLOSED, then reopened by the audit, then closed

| Story | Goal | Files | Acceptance | State |
|---|---|---|---|---|
| US-2.6 | Prove it bites: E1 planted in `src/`, both methods run, both outputs quoted | — | Phase 1's suite passes; the sweep fails; the exhaustive count matches M2's prediction | done `3c7e087` (doc-only) — 100/100 pass planted; 52 of 2,245 caught, predicted 52. Plant and driver committed by US-2.13; the whole-suite figure is re-recorded there, since the suite has grown since. |
| US-2.7 | Demote the perturbation suite and the AST scan in the documentation, delete neither | `README.md`, `tests/test_point_in_time.py` | both still run; the scan is never cited as a defence | done `3c7e087` |
| US-2.8 | Retract *"the store cannot help"*, in the spec and in the docstring that copied it | spec §9, `volab/features/build.py` | the retraction says what the paragraph still gets right | done `3c7e087` |
| US-2.9 | The decisions log, each ruling with what it costs if wrong | `docs/superpowers/phase2-decisions.md` | every mechanical claim names an executable witness (R32) | **reopened then done** `02eb0e5` — a delivery audit found ~9 rulings with no test id and two (R62, R63) with no test at all. Witnesses added, the tests written, R58/R59/R60 corrected, R70–R78 added. |

## Sprint 3 — The walk-forward · CLOSED, then reopened by the review, then closed

| Story | Goal | Files | Acceptance | State |
|---|---|---|---|---|
| US-2.10 | Per-origin rebuild, HAR levels and semi-log, random-walk benchmark, QLIKE and MSE | `volab/walkforward/` | every build stops at its own origin, audited call by call | **reopened then done** `02eb0e5` — a review found the forecast biased low by 4x at h=1 and the standard levels baseline not run at all. See R73. |
| US-2.11 | The trial ledger: append-only, git-tracked, keyed by the git sha and a dirty flag | `volab/walkforward/`, `.gitignore`, `data/trials.jsonl` | a run of N configurations appends exactly N lines, and every line identifies the tree that produced it | **reopened then done** `02eb0e5`, ledger `064c1a4` — the audit found the file untracked (so "git-tracked" was false) and four lines naming a tree without the code. See R59, R60, R70, R74, R75. |
| US-2.12 | The truncation comparison as a by-product of the walk-forward, over every origin **the run visits** | `volab/walkforward/__main__.py`, `tests/test_walkforward.py`, `tests/test_walkforward_cli.py` | per-origin rows byte-identical to the persisted table at every visited origin; the CLI's own refusals tested | **reopened then done** `02eb0e5` — the acceptance line said "every origin", which is true at `--every 1` and false of the recorded stride-5 run, and no test imported the CLI at all. |

**Three defects found by these stories' own tests, each recorded with its ruling:**

- an origin whose own regressors are null was scored as a NaN instead of being skipped (R56);
- the singularity threshold was absolute where it must be relative — and the first repair,
  scaling by the largest entry in the whole system, was *still* effectively absolute, because
  that entry is always the intercept cell. Now per-column (R58);
- the random-walk forecast is `0.0` at a session whose close did not move, so QLIKE is undefined
  there — found because QLIKE raises rather than returning NaN (R55), and fixed by scoring every
  model over the common set of origins where the loss is defined, with the dropped count carried
  on each trial (`Evaluation.comparable`).

## Sprint 4 — The evidence as artefacts · CLOSED

| Story | Goal | Files | Acceptance | State |
|---|---|---|---|---|
| US-2.13 | Every figure the decisions log quotes is reproducible from a checkout, or says it is not | `tools/`, `docs/superpowers/phase2-decisions.md`, `README.md` | the plant is committed and applied to a throwaway tree; the three pre-measurements and the calibration probe are scripts; every quoted figure names what prints it; the ratchet is unchanged and `ALLOWED` stays `{"store.py"}` | done `6110f36` — R79–R81 |

**The figures were checked mechanically, not by eye.** Each fenced block the log now calls
reproducible was diffed line by line against the saved stdout of the script that claims it, and
the misses were real: three calibration blocks were in formats no script prints. They were
replaced with verbatim output rather than left to look checkable. Separately, every one of the 35
`test_*` ids the log cites was checked to exist — Phase 1's spec naming five tests that never
existed is the reason that check is worth running rather than assuming.

**One figure was wrong rather than stale, and the correction is the interesting part.** The log
quoted `1 failed, 644 passed` for "the whole suite minus the new Phase 2 files" in a section
about the final tree. On HEAD that exclusion set leaves **648**, and the first guess — that the
suite had grown — was wrong: `git diff 3c7e087 HEAD -- tests/` shows no test file outside the
exclusion set changed at all. Measured instead of inferred, by collecting on the archived trees:
the 645 is **sprint 1's tree**, `21dc15b`, which held 663 tests and only two Phase 2 test files,
since the walk-forward's two did not exist yet. So the number was right about a tree the section
did not name. Both figures are now in the log, each with its tree.

**One defect, found by this story's own first run, and it is the reason the driver refuses rather
than reports.** `git archive` creates an empty `data/curated` (the `.gitignore` keeps
`data/**/` tracked), `cp -R` into an existing destination nests, and the real sweep tiers raised
`DatasetNotFound` — which the driver swallowed, **exiting 0 with those rows missing from the
output**. A script written to close a reproducibility gap had produced a check that passes by not
running. Fixed, and recorded under R80 rather than quietly corrected.

## Risks and what is carried

- **A sampled sweep is a ratchet, not a proof.** 2.2% power per cutoff against a band-threshold
  peek. The dev-window proof is the recorded exhaustive sweep (2,245 cutoffs, 6m00s), which is
  not per-commit; the walk-forward is exhaustive **only over the origins a given run visits**,
  and the recorded run used a stride of 5.
- ~~**The headline figures are not reproducible from a checkout.**~~ **Closed by US-2.13.**
  `tools/` holds the E1 plant as a patch with a driver that applies it to a throwaway tree, and
  the three pre-measurements and the calibration probe as scripts. Every figure in
  `phase2-decisions.md` now names a `tools/` command, an existing test id, or says it is
  historical and not reproducible on HEAD. Verified from a tree with no derived data: the
  committed payloads rebuild the store in 68.7s with no network and the probes then print the
  same numbers. R79–R81.
- **The real-data tiers skip silently on a clean checkout.** `data/**` is gitignored and
  derivable, so `test_truncation_equivalence.py` and `test_walkforward.py` skip their real-data
  fixtures where the curated store is absent — which means a fresh clone's green suite has not
  checked them. The synthetic tiers always run and carry the red cases, so a leak is still caught
  somewhere; but "the suite is green" means less on a clean checkout than it looks, and that is
  worth knowing rather than discovering.
- **A cross-sectional peek stays unreachable rather than tested**, inherited from Phase 1:
  `build_features` refuses a panel with more than one non-exogenous symbol, so nothing here would
  notice if that guard were relaxed.
- **Refusal is not equivalence** (R40): the engine still rejects a malformed row after the cutoff,
  where a truncated panel simply would not have it.
- **The semi-log specification fits a log scale while the loss is on the variance scale** (R73),
  and the levels variant can forecast a non-positive variance, which drops those origins from
  QLIKE for every model. Both are on the ledger rather than in a footnote.
- **The 200-session holdout is thin at the 22- and 66-session horizons.** Unchanged by anything
  here, and Phase 6 remains the only confirmatory read.
- **The model set is deliberately three.** HAR in levels, HAR semi-log and a random walk. The
  GARCH family and the recalibrated HAR of the framework's Phase 2 row are **not** in this
  branch; what is here is the harness they will be run through, plus the baseline. Said plainly
  rather than left to be inferred from the ledger.

## Found by the delivery audit, and not Phase 2's to fix

The `delivery-auditor` seat turned up one thing that belongs to Phase 1 and is recorded rather
than quietly corrected, since changing a closed phase's spec to match the code is how a spec
stops being a check on anything:

**The Phase 1 spec names five tests that have never existed.** `2026-10-20-phase1-data-and-targets-design.md`
lines 624, 653, 659, 669 and 671 require `test_perturbing_post_t_leaves_features_byte_identical`,
`test_panel_extent_does_not_change_features`, `test_shuffled_future_canary`,
`test_targets_carry_as_of_h_sessions_after_base` and
`test_load_panel_at_dev_end_hides_unrealised_targets`. None has a `def` at `2ba2fe5` or at HEAD;
the real suite implements the same *properties* under different names. So they were never
deleted — the spec's named test list never matched the suite it was describing. The properties
are covered. The citations are not, which makes §11's list unusable as a checklist and is one
more instance of the pattern §11 itself names: prose in that section that nobody ran against an
interpreter.
