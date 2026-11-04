# Phase 4 decisions — unattended daily runs

Every ruling with the reasoning that produced it and what it costs if it is wrong. Phase 3's are
in `docs/superpowers/phase3-decisions.md` and end at R155.

---

## R156 — Phase 4's rulings start at R156 and live in their own file

**Checked before claiming it, the way R150 requires and R153-R155 each had to:**
`git show <branch>:docs/superpowers/phase3-decisions.md | grep '^## R1'` over **every** branch,
local and remote (32 refs at the time of writing), plus the same scan over
`phase2-completion-decisions.md`. The highest number in use anywhere is **R155**
(`origin/main` at `56bf9e2`, and on `docs/re-derive-deferred-findings`,
`docs/r155-open-row-count` and `guard/police-aliased-load-panel`). `main` alone would have
reported R155 correctly this time; `main` alone reported R152 last time, which is why the scan is
the rule and not the shortcut.

So Phase 4's first ruling is **R156**, continuing one sequence across the whole project. The
numbers are global; only the file is new.

**Ruling: Phase 4's rulings go in `docs/superpowers/phase4-decisions.md`, and the number sequence
continues unbroken.** Two reasons, and the second is the one that decides it:

1. `phase3-decisions.md` is 586 lines and closed. Appending a new phase to a closed log makes the
   phase boundary invisible to a reader who opens the file at the top.
2. **Two sessions appending to the same file is an append conflict every time.** R155 records
   exactly that: PRs #13 and #14 both appended to `phase3-decisions.md`, so whichever merged
   second had to resolve one. Phase 3's open work (twelve deferred findings, any of which the user
   may dispatch) will keep appending there. A separate file means a Phase 4 PR and a Phase 3
   follow-up PR never touch the same file.

**What this costs if it is wrong:** a reader looking for a ruling must know which file to open.
Mitigated by the sequence being unbroken — R156 follows R155 — and by each board naming its own
decisions file in its header.

---

## R157 — the holdout-reveal design is not built, and the brief that commissioned Phase 4 said
otherwise

The brief for this phase stated that the Phase 4 build is "unblocked today and only *live* data
waits on the user", citing the holdout-reveal design's `:92-93`. The citation is accurate. The
inference from it is not, and the difference changes what Phase 4 can be.

**Measured on `56bf9e2`, in a worktree cut from `origin/main`:**

| probe | result |
|---|---|
| `grep -rn "holdout_end" src tests config` | **no match** |
| `git log --all -S holdout_end` | 7 commits, **every one docs-only** |
| `src/volab/config.py:72-74` | `Splits` is exactly `dev_end` and `holdout_start` |
| `config/base.yaml` | exactly those two keys under `splits:` |
| `_ENV_OVERRIDES`, `config.py:53-60` | no `VOLAB_SPLITS_HOLDOUT_END` |
| the design's own status line | *"awaiting approval. Nothing here is built."* |

**So there is no `live` region in this codebase.** The three regions that design names exist in
prose only; the code has two, split at `dev_end`, and every date after `holdout_start` falls in a
sealed window with no end. The practical consequence is sharper than "live data waits on the
user": **a scheduler built today has no legal target past `dev_end` at all**, and the thing it is
waiting on is not only the user's trigger but an unwritten slice of config validation.

**Ruling: Phase 4 is built against the two regions that exist, and the `live` region is a story
(P4-05) that is blocked on the user, not an assumption.** The scheduler's refusal of an `as_of`
past `dev_end` is a deliverable in its own right and not a placeholder.

**Why this is recorded as a ruling rather than a correction in passing.** R150 says another
agent's report is evidence to check and never a fact, and names the cost: an unconditional
instruction derived from a condition the reader cannot evaluate. This is the same shape. The
brief's author had read the design and reported its conclusion faithfully; what neither the design
nor the brief states is that the design is unbuilt, because at the time each was written that was
not the question being asked. **A premise inherited from a document about a design is a premise
about the design, not about the tree.** Checking it cost two greps.

**What this costs if it is wrong:** nothing, if the slice turns out to be Phase 4's — P4-05 is on
the board and ready to be unblocked. If it had gone unchecked, Phase 4 would have built a
scheduler aimed at a region that does not exist, and the first thing to notice would have been a
test that could not be written.

---

## R158 — the four Phase 4 questions, decided by the user on 2026-11-03

Asked once, after the design draft existed and before any code, which is the order R135's
reconstruction requires: a scope reconstructed from documents may not also have its ambiguities
resolved by the agent reconstructing it.

### Q1 — the `live` region is Phase 4's. **Decided: yes, P4-05 is in scope.**

R157 established that `holdout_end` exists only in `docs/`. The user's decision puts the one
slice Phase 4 needs inside Phase 4: `holdout_end` in `Splits` and `config/base.yaml`, the
override direction **later-only** (the reverse of `dev_end`'s, which the reveal design warns a
builder will copy backwards), and the three regions named. **`live` stays unreachable** — no read
path past `holdout_end` is opened, because opening one is the reveal and the reveal is the user's.

The rejected alternative, recorded with its cost: a scheduler whose only legal `--as-of` is on or
before `dev_end` until another session builds the slice. That works, and it leaves the phase's
central refusal untestable in the direction that matters — "a date in the live region is refused
*because the reveal has not happened*" cannot be distinguished from "refused because the region
does not exist".

### Q2 — ledgers. **Decided: option A for the live path, option B for the rehearsal.**

The live daily path **never invokes** `walkforward`, `compare` or `evaluators`; it appends one
line per day per configuration to a **new** forecast log. The rehearsal invokes the whole chain
against a storage root that is not the repository's. So the three reported ledgers are untouchable
by construction and not by discipline, which is the distinction this project exists to make.

Consequence accepted with the decision: before the reveal there is no champion, so the forecast
log's lines carry a **pseudo-champion** — the current dev leaderboard winner, labelled as not the
registered one. P4-06 owns it and it is not in the first slice.

### Q3 — the run record is **gitignored**.

`data/daily_runs.jsonl` is not tracked. The tree does not go dirty every morning; the accepted
cost is that `git clean -xdf` erases the evidence that the job ran, and **no count from that file
may ever become a reported number** — stated here so it cannot become one by accident later.

### Q4 — the first slice is **P4-01 + P4-02**.

The plan as data with its refusals, and the two-line run record. P4-03 (the subprocess stage
runner), P4-04 (the whole rehearsal), P4-05, P4-06 and P4-07 are on the board and not in this PR.

### What this costs if any of it is wrong

Q1 and Q2 are the two that would be expensive to reverse: Q1 puts config validation that the
holdout seal depends on inside a phase whose exit criterion is a reconstruction, and Q2 creates a
second kind of forecast record before the registered read has happened. Both were put to the user
with their alternatives and both are recorded here as theirs, not as the orchestrator's calls.
