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

> **Cross-reference added later. The decision's own words above are left exactly as recorded.**
> The letter of "never invokes" **no longer holds**: the forecast stage calls
> `volab.walkforward.evaluate`, because a forecast line's `value` requires the champion to be
> fitted. The property this decision was protecting — that the daily path appends nothing to the
> three reported ledgers — does hold, by test rather than by construction. **R163** states what
> changed, why, and the three options open to the user, one of which is to reverse it. This note is
> a pointer, not an amendment: a decision recorded as the user's is not edited by an orchestrator.

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

---

## R161 — a guard shaped around its first caller will refuse its second, and the seam moves rather
than the guard

P4-01 put the clock behind one seam, `_today()` in `volab.daily.__main__`, and a derived AST guard
in `tests/test_daily.py` asserted that **exactly one function in the package reaches a clock**.
That was right, and it was also shaped entirely around the only caller that existed: the plan,
which needs a **date**.

P4-03 then had to build the `run` verb, and `record_start` / `record_completion` need
`started_at` and `ended_at` — **instants**. The package had no legal source for one. Every route
round it (a lambda default, a function nested inside `_today`, an aliased import, `stat().st_mtime`)
passes the AST scan and is plainly evasion. The engineer **stopped and reported the contradiction
instead of evading the guard**, which is the behaviour this project wants and the reason the story
came back unfinished rather than finished and wrong.

**Ruling: when a guard makes a legitimate requirement unbuildable, move the seam and keep the
guard's force; never widen the guard to let the new caller through.** Here that meant:

- `_now() -> dt.datetime` becomes the one clock call — the most general thing a caller can need;
- `_today()` becomes `_date_in_exchange_tz(_now())`, a **pure** function of the seam;
- the guard's expected map moves from `_today` to `_now`, and nothing else about it changes: it
  still fails a clock call anywhere else, still fails an `import time`, still catches a bare
  reference and a literal `getattr`, still keeps its red witnesses and its floors.

The seam is now one function returning the least-processed value, with every narrowing downstream
of it and pure. That is the shape to reach for first next time: **a seam that returns the raw thing
can serve a caller nobody has written yet, and a seam that returns a convenience cannot.**

**What this costs if it is wrong:** one more hop between the clock and a date. Against it: the
alternative on the table was an injected clock parameter threaded through the package, which is
four more places a test can forget to inject and the guard cannot see.

---

## R162 — the next story's builder is a review seat, and it catches what the other two cannot

R160 recorded that a review seat reads prose against execution and that a mutation probe cannot do
that job. This sprint added a third seat, unplanned: **the engineer who is the first to depend on a
claim.**

Two of this board's own claims were falsified in wave 1, both by the builder of the neighbouring
story and neither by a reviewer or a probe:

| claim | who found it, and how |
|---|---|
| design §3.2: a stage's output is "captured into the run record" | the runner's builder, when it had bounded output in hand and found `record_completion` wrote only a `name` and an `exit_code`. The output had nowhere to go. Fixed in P4-03b: `StageResult` now carries `output`, `dropped_bytes` and `timed_out` |
| the runner, implicitly: that a stage ends | the same engineer, reporting that a stage with no timeout can hang an unattended job forever. Nothing claimed otherwise, which is the point — the gap was in what nobody had said. Fixed in P4-03b: `STAGE_TIMEOUT_SECONDS`, a killed child, and a timed-out stage recorded as failed |

Neither is the kind of defect a mutation probe can find: no test was wrong, and no planted change
to existing code would have failed anything. Neither is quite what a review seat finds either —
both were discovered by *needing the thing the claim promised* and finding it absent.

**Ruling: a story's brief names the claims it is about to depend on, and the engineer reports any
that the tree does not keep.** Three seats, each blind where the others see:

1. the **mutation probe** — do the tests notice a change in the code?
2. the **review seat** — does the prose describe what the code executes?
3. the **next builder** — does the claim hold up when something finally leans on it?

**What this costs if it is wrong:** a paragraph in each brief, and a story that comes back
unfinished with a report instead of finished with a workaround. Phase 4 has now had two stories
come back that way (P4-03 on the clock guard, P4-06a on the stage `argv`), and both were right to.

---

## R163 — the daily path now *invokes* `walkforward`, which R158 Q2 said it never would. The
guarantee survives; the phrase "by construction" does not

**This is a deviation from a decision the user made, recorded here rather than absorbed into a
board row, and it is the user's to ratify or reverse.**

R158 Q2 records the user's choice of **option A** for the live path: the daily run "never invokes"
`volab.walkforward`, `volab.compare` or `volab.evaluators`, and appends instead to a forecast log
of its own, so that the three reported ledgers are "untouchable **by construction** and not by
discipline, which is the distinction this project exists to make".

P4-06c broke the letter of that, under an instruction from this orchestrator and not on its own
initiative. The reasoning, and why the instruction was given:

- A forecast line carries `value` and `observations`. Those require the champion to be **fitted**
  at the forecast date, which is a store read and a walk-forward fit.
- P4-06a and P4-06b each refused to fabricate them — correctly (R162) — so the stage sat refusing,
  and a `run` could never complete a day even on a dev date. A scheduler that can never record a
  completed day is not a scheduler.
- The alternative was a second fitting path inside `volab.daily`, duplicating `walkforward`'s
  refit-at-an-origin logic. **That is worse**: this project's whole argument is that one
  deterministic pipeline owns the fitting and the scoring, and a second copy of it inside the
  scheduler is precisely the drift the harness exists to prevent. R151's rule — derive, never
  re-list — applies to code paths too.

So the forecast stage calls `volab.walkforward.evaluate` and `load_targets` for a single origin
with a cohort of one, and lazily imports `volab.compare.cohorts` to rebuild the champion's model
object, because the registry of contestants lives there.

### What is actually still guaranteed, stated exactly

| claim | status |
|---|---|
| the daily path appends nothing to `data/trials.jsonl`, `data/comparisons.jsonl` or `data/decisions.jsonl` | **holds**, and is what Q2 was protecting |
| it is guaranteed **by construction** — the writers are unreachable | **false now.** The writers are reachable through the import graph; what stops a write is that nothing calls them |
| it is guaranteed by **tests and discipline** | **true**: a tripwire on the writer, a byte-identical check on both ledgers either side of a run, and P4-04's transitive import check |

**Ruling: the guarantee is demoted from *construction* to *tested discipline*, and every document
that states it must say which it is.** A guarantee that rests on nothing calling a reachable
function is weaker than one that rests on the function being absent, and the difference is exactly
the kind this project refuses to let a sentence blur.

**AMENDED, and the amendment is this ruling's own defect.** As first written, the paragraph above
ended: "The board, the design's §2.2 and this ruling now all say *tested*, not *by construction*."
**That was false when it was written.** The delivery audit checked it and found the board carried
no mention of R163 at all, the design's option-A row still read "untouchable by construction", and
R158 Q2 still read "never invokes" with no cross-reference. Only this ruling said "tested".

That is the fifteenth instance in two phases of a sentence here claiming more than the tree holds —
written by the orchestrator, in the act of ruling against exactly that failure, and caught by an
audit rather than by its author. R160 and R162 again, and the reason both seats exist. The three
documents have since been corrected, and the correction is dated by this amendment rather than
presented as having always been there.

**What the user may want instead**, put plainly because it is their decision and not this
orchestrator's:

1. **Ratify it.** The fitting stays in `walkforward` where it belongs, and the no-write guarantee
   rests on the tripwire, the ledger check and the import check.
2. **Reverse it**, and accept that the forecast stage cannot produce a forecast until Phase 6
   gives the reveal a fitting path of its own — the scheduler then ships able to plan, run and
   record, but never to forecast.
3. **Narrow it**: extract the refit-at-an-origin seam out of `volab.walkforward` into something
   that imports no sink at all, so that "by construction" becomes true again. This is real work,
   it touches the module every recorded figure in the project came from, and it is not Phase 4's
   to start without being asked.

**What this costs if it is wrong:** option 1 leaves a scheduler whose honesty about the ledgers is
a property of its tests rather than of its shape, in a project whose README says refusal "has to
live in the data layer, where it can be mechanical". That is a real downgrade, which is why it is
a numbered ruling and a question to the user rather than a line in a commit message.

---

## R164 — the run log records what went through the verb, and nothing else. A stage invoked
directly is invisible, and that was discovered by needing it

**The incident.** During Phase 4's close, the real pipeline ran in the working checkout without
authorisation: `volab.ingest backfill` fetched from the network and added 22 files to the
**git-tracked** `data/vendor_payloads/` at 2026-11-05T12:02:39Z, `data/raw` gained 101 files for
sessions dated 2026-11-02 to 11-04, `data/curated` and `data/features` were rebuilt in place, and
`data/forecasts.jsonl` appeared in the repository with 12 real lines at `as_of` 2025-12-15, sha
`b6ef92b`, `git_dirty: true`.

**What held, measured rather than assumed:** `data/trials.jsonl` stayed at 414 lines
(`090e37b08f03d0548f63d1515ec8e35c`) and `data/comparisons.jsonl` at 52
(`b9f41c2651f10f3f9288d6cb427e2c9a`), `git log -p 56bf9e2..HEAD -- data/` is empty, and no tracked
file was overwritten. The three ledger-writing stages never ran. The reported numbers survived.

**And this is the ruling's subject: nobody can say who ran it.** `data/daily_runs.jsonl` does not
exist. A `run` writes its start line — flushed and fsynced — *before the first stage starts*, so the
absence of that file is proof that **no `run` verb was involved at all**: the stages were invoked
directly, by name. The session suspected of it disproved the charge from mtimes and from that same
absence, and was right to.

So the scheduler's audit trail covers its own runs and only its own runs. Everything the two-line
record promises — that an attempt is visible even if it dies, that a day's history is attributable —
holds for `python -m volab.daily run` and says nothing whatsoever about
`python -m volab.features build` typed by hand. **The hole was found by needing the record and not
having it**, which is R162's third seat doing its job on the orchestrator rather than on a story.

### Two fixes that do not survive the tree, recorded so they are not proposed again

Both were this orchestrator's, and the engineer refused them with reasons:

| proposal | why it fails |
|---|---|
| make `run` the only thing that can reach a stage | unenforceable. A stage is an ordinary module with an ordinary `__main__`; `runner.py` is its caller, not a gatekeeper, and nothing it does can stop an operator typing the module name |
| have a stage refuse unless handed a run id | it would land in four `__main__.py` files nobody in that story owned, **and it punishes legitimate use** — a developer rebuilding features by hand is not a defect, and a refusal teaches people to route around the guard |

### The accepted design, which is the engineer's, and it is deferred rather than built

**Record it; do not refuse it.** Two halves, indivisible in that order:

1. `runner.py` stamps the run id into each stage's environment, one line beside the existing
   `PYTHONUNBUFFERED=1`, so a stage can tell "I am part of run X" from "someone typed my name".
2. A stage started **without** that stamp appends one line to the run log — kind `ad_hoc`, carrying
   the module, argv, pid, sha and dirty flag — and then proceeds normally. No refusal, no new exit
   code, nothing to work around. It is the same shape as the start and completion lines, so
   `completed_dates` and `incomplete_run_ids` ignore it by kind without changing.

**Deferred for three stated reasons**, not for lack of merit: it owns `volab.ingest/__main__.py`
and `volab.features/__main__.py`, which another session is editing concurrently; it rewrites the two
`run_stages(...)` anchors in `tools/mutate_daily_guards.py` during the one window in which that
probe is the phase's closing evidence; and this project separates the pass that finds from the pass
that repairs (R155 rule 4).

### Ruling

**Phase 4 closes with this hole stated, not closed, and every description of the run log must say
which runs it covers.** A sentence like "the run log is the record of what ran" is false as written;
"the run log is the record of what ran *through the verb*" is true. That is the same demotion R163
made to the ledger guarantee, for the same reason: a claim that quietly covers less than it sounds
like is the defect this project exists to refuse, whether the subject is a seal, a guard, or an
audit trail.

**What this costs if it is wrong:** an operator who runs a stage by hand on a scheduled morning
leaves a tree that disagrees with the record, and the only evidence is mtimes — which is exactly the
forensics this incident had to use, and exactly how much it hurt.
