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

---

## R159 — a derived guard is only as good as its exclusions, and two of this slice's three guards
needed one it did not have

R151 rules that where a test enumerates a set the codebase will extend, it must derive the set.
P4-01 needed three such guards — no planned stage may write a reported ledger, no module under
`src/volab/daily/` may use a holdout key, and the clock may be called in exactly one place.
**Two of the three were wrong on their first run, and between them they needed three separate
exclusions.** (The clock guard was right first time, which is worth saying so the count here is
not inflated: it is an AST scan over a package with one clock call, and there was nothing to
exclude.) In none of the three failures was the derivation's *idea* wrong; all three were about
what it must leave out — and a fourth, found afterwards by the probe rather than by reading, was
about how loosely it matched.

| guard | first version | what it did | the exclusion it needed |
|---|---|---|---|
| ledger writers | a package whose source **names** `trials.jsonl`, `comparisons.jsonl` or `decisions.jsonl` | flagged **`volab.leaderboard`**, whose whole design is that it "computes nothing of its own" and only *reads* the two ledgers. Reading them is the opposite of the hazard, and the leaderboard is a planned daily stage | the rule is a **conjunction**: names a ledger **and** opens a file in append mode, by AST. Pinned by `test_the_derived_writer_set_excludes_the_reader` |
| holdout keys | a substring match for `allow_holdout` and `VOLAB_HOLDOUT_UNLOCK` | flagged **`volab.daily`'s own module docstring**, which names both keys in order to state that it never uses them | the scan reads **code, not prose**: identifiers, attributes, keyword arguments and non-docstring string literals, by AST. A key spelled as a string still fires, which is the route a determined author would take. Pinned in both directions |
| holdout keys, again | the parameter name from `inspect.signature(load_panel)` | returned **no parameters at all**, because `conftest`'s holdout police replace `volab.store.load_panel` with a `(*args, **kwargs)` wrapper for the whole session (R153). The signature at runtime is the police's | read the name from `store.py`'s own `def load_panel` by AST. The `assert parameters, …` line is what turned a silently vacuous guard into a loud failure, and it was written before the failure rather than after it |
| ledger writers, again | `stage.module in writers`, an equality test | would have waved through `volab.walkforward.__main__` and `volab.evaluators.sink`, both runnable module names that write the same ledgers. Found by a reviewer reading the guard, not by running it | prefix matching, with a red witness planting the submodule spelling. The derivation's remaining limit is now stated in the docstring: it is **not transitive**, so a package that imports `append_trials` without naming a ledger or opening one for appending is not in the set — handed to P4-04's import check |

### Ruling

**A derived guard ships with a test for each exclusion it makes, a test that the derivation is not
empty, and a red witness for each way the thing it compares can be spelled.** The second clause is
the one that nearly got away: the holdout-key guard with an empty
derived set would have passed over a package that used the key on every line, and it would have
passed quietly, forever. R151's own closing paragraph says the cost of deriving is paid where the
derivation has edge cases and those get pinned; this records what that costs in practice — three
edge cases in three guards, found by running them and not by reading them, in one story.

**And the direction of each failure is worth keeping.** Two (the leaderboard, the docstring) were
**too strict** and failed loudly, which is the cheap kind. The third would have been **vacuous**,
which is the kind R154 is about — a check that removes itself breaks nothing — and the only reason
it failed loudly instead is the one line asserting the derived set was non-empty. The fourth was
**too loose** and green, and no assertion in the guard could have found it: a reviewer reading the
comparison did.

**What this costs if it is wrong:** the exclusion tests are six short tests whose subject is the
test harness rather than the product. The alternative — a guard trusted because it is green — is
what this project has now found nine leakage escapes behind.

---

## R160 — the review seat earned its place on the first slice of code in this phase

P4-01 and P4-02 were written test-first where they could be, with nine planted mutants each
failing exactly its own witness, three derived guards each with a red witness, and all four gates
green. An independent review seat then read the diff with one instruction first — **read every
docstring and every board row against what the code and the test bodies execute** — and found
**four claims stronger than the tree and four guards that did not bite as described.**

Recorded because the ratio is the point: the author had already run a mutation probe over his own
tests, and the probe could not see any of these, because a mutation probe checks that the tests
notice a change in the *code* and says nothing about whether the *prose* is true.

### The four overclaims

| claim | what the tree held |
|---|---|
| `volab.daily`'s module docstring described `python -m volab.daily run --plan-only` in the present tense | the only verb is `plan`; that command exits with an argparse error. The `__main__` docstring was accurate, so the two contradicted each other |
| the board's "written, flushed and `fsync`ed" | true of the code and **witnessed by nothing**. Deleting both calls left all 29 tests green, and no mutant removed them |
| the board's "a date whose completed record exists is a no-op" | true of `plan_for`, which a test fed a hand-built `frozenset`. `main` never read the run log, so `python -m volab.daily plan` printed `work` for a date the log said was finished. **True of the library, false of the shipped verb** |
| the timezone test's "bites at any hour" | its strong half asserted the string `_EXCHANGE_TZ` appeared in `inspect.getsource(_today)`, which a comment defeats; its other half agreed with a UTC implementation except in the evening. And the test was named for the CLI default while never calling `main` |

### The four guards that did not bite

| guard | the spelling that walked through |
|---|---|
| holdout keys | `from volab.config import HOLDOUT_UNLOCK_ENV; os.environ[HOLDOUT_UNLOCK_ENV] = "1"` — the constant's **name**, which is neither key. Also `"VOLAB_" + "HOLDOUT_UNLOCK"` and `["env", "VOLAB_HOLDOUT_UNLOCK=1", …]` |
| the clock seam | `clock = dt.datetime.now` (a reference, not a call), `getattr(dt.datetime, "now")()`, `import time`, and a **second function also named `_today`**, which the exact-map assertion accepted |
| append detection | a mode in a variable (`open(p, MODE)`), `os.open(…, os.O_APPEND)`, `logging.FileHandler`, `to_csv(mode="a")` — and a false positive on `open("archive.csv")`, since any string starting with "a" counted |
| ledger-writing stages | `volab.walkforward.__main__`, found by the same reviewer against an equality test, and already fixed before the review landed |

### What changed, and the ruling

Every one is fixed with a test of its own, the mutant count went from nine to thirteen, and the
guards now judge identifiers loosely and string literals strictly — so a refusal *message* may say
the holdout is sealed while a key *used* in code cannot hide in a string. Each guard's remaining
reach is written into its own docstring: the writer derivation is **not transitive**, an AST clock
scan cannot see a call assembled at runtime, and the holdout scan reads this package's source and
not the environment that launches it.

**Ruling: a review seat reads prose against execution, and that is a different job from a mutation
probe, which cannot do it.** Both are required on every story in this phase. The probe proves the
tests bite; the seat proves the sentences are true. Phase 3 found eleven instances of a test or
docstring claiming more than its code performed, every one caught by a review seat and never by
the author — this slice is the twelfth through fifteenth, and the author who planted nine mutants
over his own work still did not see them.

**What this costs if it is wrong:** a review dispatch per story. Against it: three of the four
overclaims were in the board, which is the document the next agent will work from, and one of them
("already_done") would have shipped a scheduler that re-ran a day it had already completed.
