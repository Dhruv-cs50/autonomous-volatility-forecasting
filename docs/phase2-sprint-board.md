# Phase 2 sprint board — the walk-forward evaluation harness

One board per phase; Phase 1's is `sprint-board.md`. Tracked in git deliberately: a plan
`git clean` erases is not a plan.

Sizing is **scope, not wall-clock**: a story is one dispatch of new work, and a sprint closes
before the next opens. Nothing is done on assertion — a story closes when a commit exists, its
tests pass and the gates are green.

**Execution shape.** Run natively by the session controller, with no subagent dispatches. The
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
| US-2.1 | Three pre-measurements before any code: vintages, E1's real detection power, where E2 can hide | — | each one either confirms or changes the plan, and is recorded | done — M1/M2/M3 in `phase2-decisions.md`, all three changed it |
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

## Sprint 2 — Evidence and the record · CLOSED

| Story | Goal | Files | Acceptance | State |
|---|---|---|---|---|
| US-2.6 | Prove it bites: E1 planted in `src/`, both methods run, both outputs quoted | — | Phase 1's suite passes; the sweep fails; the exhaustive count matches M2's prediction | done — 100/100 and 644/645 pass planted; 52 of 2,245 caught, predicted 52 |
| US-2.7 | Demote the perturbation suite and the AST scan in the documentation, delete neither | `README.md`, `tests/test_point_in_time.py` | both still run; the scan is never cited as a defence | done |
| US-2.8 | Retract *"the store cannot help"*, in the spec and in the docstring that copied it | spec §9, `volab/features/build.py` | the retraction says what the paragraph still gets right | done |
| US-2.9 | The decisions log, R33–R69, each with what it costs if wrong | `docs/superpowers/phase2-decisions.md` | every mechanical claim names an executable witness (R32) | done |

## Sprint 3 — The walk-forward · CLOSED

| Story | Goal | Files | Acceptance | State |
|---|---|---|---|---|
| US-2.10 | Per-origin rebuild, log-HAR, random-walk benchmark, QLIKE and MSE | `volab/walkforward/` | every build stops at its own origin, audited call by call | done |
| US-2.11 | The trial ledger: append-only, git-tracked, keyed by the git sha | `volab/walkforward/`, `.gitignore`, `data/trials.jsonl` | a run of N configurations appends exactly N lines | done |
| US-2.12 | The exhaustive truncation sweep as a by-product of the walk-forward | `volab/walkforward/__main__.py`, `tests/test_walkforward.py` | per-origin rows byte-identical to the persisted table over every origin | done |

**Three defects found by these stories' own tests, each recorded with its ruling:**

- an origin whose own regressors are null was scored as a NaN instead of being skipped (R56);
- the singularity threshold was absolute where it must be relative, a latent refusal of good
  windows given HAR's 1e-8 scale (R58);
- the random-walk forecast is `0.0` at a session whose close did not move, so QLIKE is undefined
  there — found because QLIKE raises rather than returning NaN (R55), and fixed by scoring every
  model over the common set of origins where the loss is defined, with the dropped count carried
  on each trial (R60's sibling rule in `Evaluation.comparable`).

## Risks and what is carried

- **A sampled sweep is a ratchet, not a proof.** 2.2% power per cutoff against a band-threshold
  peek. The exhaustive run (6m00s) and the walk-forward are the proof, and neither is per-commit.
- **A cross-sectional peek stays unreachable rather than tested**, inherited from Phase 1:
  `build_features` refuses a panel with more than one non-exogenous symbol, so nothing here would
  notice if that guard were relaxed.
- **Refusal is not equivalence** (R40): the engine still rejects a malformed row after the cutoff,
  where a truncated panel simply would not have it.
- **The 200-session holdout is thin at the 22- and 66-session horizons.** Unchanged by anything
  here, and Phase 6 remains the only confirmatory read.
- **The model set is deliberately two.** Log-HAR and a random walk. The GARCH family and the
  recalibrated HAR of the framework's Phase 2 row are **not** in this branch; what is here is the
  harness they will be run through, plus the baseline. Said plainly rather than left to be
  inferred from the ledger.
