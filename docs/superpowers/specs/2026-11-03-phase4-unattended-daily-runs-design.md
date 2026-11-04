# Phase 4 — unattended daily runs (the scheduler): design

**Status:** awaiting approval. Nothing here is built.
**Date:** 2026-11-03
**Tree this was derived from:** `56bf9e2` (`origin/main`, the merge of PR #15), confirmed with
`git log -1 origin/main` rather than from any report of it (R150).
**Phase:** 4. Gated by `docs/superpowers/specs/2026-10-27-holdout-reveal-design.md`, which is
**itself unbuilt** — see §1.3, which is the one premise a reader of the brief that started this
work should check first.

---

## 0. What this scope is, and what the reconstruction forbids

**The framework document that defines each phase's exit criterion is not in this repository
(R135).** It was not committed for Phase 3's boundary and it is not committed now;
`grep -rniI "phase 4"` over the tree returns four documents, none of them a requirement. So the
scope below is a **reconstruction**, assembled from what the repository does contain, and it is
labelled as one everywhere it is used.

### 0.1 The four sources, each cited so a reader can check the reconstruction against them

| source | what it contributes |
|---|---|
| `README.md:35` | the phase table's only words about this phase: *"4-6 \| Scheduler, agent layer, write-up and the sealed reveal \| not started"*. The scheduler is Phase 4's; the agent layer, the write-up and the reveal are not |
| `docs/phase2-completion-sprint-board.md:221-225` | `Splits` has no holdout end, so *any* read of live data is a holdout read; the framework's Phase 4 *"wants unattended daily runs"*, which *"collides with the seal head-on"*; and the warning that *"an agent asked to make dailies work is exactly who would reach for `allow_holdout`"* |
| `docs/phase2-completion-sprint-board.md:265-266` | the same collision, carried forward as needing **a user decision before a single Phase 4 story exists** |
| `docs/superpowers/specs/2026-10-27-holdout-reveal-design.md:30-34, 60, 92-94` | the contradiction named (*"a daily run asks for data 'up to today', and today lies inside the sealed window"*), that building this scheduler is **not** that design's job, and that **Phase 4's unattended daily runs can only run on live data after Phase 6's reveal. Phase 4 can be built before** |

Everything else in this document is engineering, not requirement.

### 0.2 What the reconstruction forbids

1. **Phase 4 may be declared *complete as specified*, never *exited*.** R135's ruling, applied to
   this boundary. Any write-up citing Phase 4 must say which claim it is making. Phase 2 was
   reopened after a board row's text was checked against `grep` and found short.
2. **No requirement may be invented that the repository cannot support.** Where this design had
   to choose, the choice is marked **(my call)** so it can be overruled, exactly as the
   holdout-reveal design does.
3. **No phrase of the framework's may be quoted as if it were to hand.** The two phrases this
   document treats as given — *"unattended daily runs"* and *"Scheduler"* — are quoted from
   **this repository's own** documents, both cited above, and not from the framework.
4. **The standing ask is restated, not retired: commit the framework document into `docs/`.** It
   has been open since Phase 3 began (R135). Phase 3 closed on a reconstruction; Phase 4 would be
   the second consecutive boundary to do so, which is the condition R129's audit found six errors
   under.

---

## 1. Measured premises

Every figure here was measured on `56bf9e2` in a dedicated worktree, not inherited from the brief
that commissioned this design (R150), and each is stated with the sha it was taken at (R155 rule
5).

### 1.1 The two reported ledgers are untouched, and stay that way

```
data/trials.jsonl       414 lines   md5 090e37b08f03d0548f63d1515ec8e35c
data/comparisons.jsonl   52 lines   md5 b9f41c2651f10f3f9288d6cb427e2c9a
```

Measured before any work in this phase and to be re-measured after every suite run. R139 holds
the recorded run until the user triggers the reveal; nothing in Phase 4 may move either number.

### 1.2 The pipeline Phase 4 would drive

Six entry points, each a module `__main__` exposing `main(argv: Sequence[str] | None) -> int`:

| stage | entry point | writes |
|---|---|---|
| ingest | `python -m volab.ingest backfill` (`--source`) | `data/vendor_payloads/**` (**tracked**), `data/raw`, `data/curated` |
| features | `python -m volab.features build` | `data/features` (the feature table and four target sets) |
| walk-forward | `python -m volab.walkforward run` (`--symbol`, `--horizon`, `--from`, `--to`, `--every`, `--minimum-training`) | **`data/trials.jsonl`** |
| compare | `python -m volab.compare run` | **`data/comparisons.jsonl`** |
| evaluators | `python -m volab.evaluators run --cohort <name>` | `data/decisions.jsonl` **and `data/trials.jsonl`** (`src/volab/evaluators/sink.py:145`) |
| leaderboard | `python -m volab.leaderboard` | nothing; exit code 1 if the two ledgers disagree |

Three of those six are writers of a reported number. That is the single most important fact about
this phase and §2.2 is built on it.

`walkforward/__main__.py:206-211` already refuses a `--to` past `dev_end` loudly rather than
clamping. It is the shape every Phase 4 refusal should copy.

### 1.3 The holdout-reveal design is **unbuilt**, and the brief's premise needs correcting

The brief that commissioned this work said the build is "unblocked today and only *live* data
waits on the user". Half of that is right. Measured on `56bf9e2`:

- `grep -rn "holdout_end" src tests config` → **no match**. The identifier exists only in `docs/`.
- `git log --all -S holdout_end` → seven commits, **every one of them docs-only**.
- `src/volab/config.py:72-74`: `Splits` has exactly `dev_end` and `holdout_start`.
- `config/base.yaml` has exactly those two keys.
- `_ENV_OVERRIDES` (`config.py:53-60`) has no `VOLAB_SPLITS_HOLDOUT_END`.
- The holdout-reveal design's own status line reads *"awaiting approval. Nothing here is built."*

**Consequence, stated plainly: there is no `live` region in this codebase.** The three regions the
reveal design names (dev, holdout, live) do not exist in code; there are two, split at `dev_end`,
and *every* date after `holdout_start` is inside a sealed window with no end. So:

- A scheduler built today has **no legal target past `dev_end`**, not merely no live data.
- The holdout-reveal design is not "approved and waiting for a trigger". It is a design awaiting
  approval whose `holdout_end`, reversed override validation, log-before-read reordering,
  pre-registration gate, forecast log and champion fingerprint are all **unwritten**.

This does not block building the scheduler; it bounds what the scheduler can be, and it is the
substance of question Q1 in §6.

---

## 2. What "unattended daily runs" reconstructs to

### 2.1 The claim the phase would exit on

**One command, safe to run from an unattended trigger on any calendar day, which decides what
today's work is, does it, and leaves an attributable record of having done it — or of having
refused.** Concretely:

1. It takes **today** as an injected value, never from the boundary. The README's own invariant is
   that *"nothing computes a boundary from the clock"*; a scheduler is the first thing in this
   project that has a clock at all, so the clock enters at one seam and is a parameter there.
2. It asks the exchange calendar whether today is a session (`volab.sessions.is_session`) and is a
   **no-op on a non-session day**, with a recorded reason.
3. It is **idempotent per date**: a second run for the same date does the work zero times and says
   so. An unattended trigger that fires twice, or a machine that wakes late and catches up, must
   not double-count anything.
4. It runs its stages in a **fixed order with fail-stop semantics**, and a stage's failure is
   recorded against that stage rather than surfacing as a traceback nobody reads.
5. It writes a **run record**: one line when the run starts, a second line referring to it by id
   when it completes — the holdout-reveal design's own two-line pattern (its call #3), chosen for
   the same reason: a run that dies must still have left a visible line.
6. It **refuses, loudly and without any key, any date past `dev_end`** — which today is every real
   date. The refusal is the deliverable until the reveal exists; it is not a limitation to be
   worked around.

### 2.2 What it must not do, and this is the load-bearing half

**The scheduler's sinks are its own. It writes no line to `data/trials.jsonl`,
`data/comparisons.jsonl` or `data/decisions.jsonl`.**

A naive daily run is "re-run the pipeline", and the pipeline's middle three stages are the three
writers in §1.2. A cron of them would append N trial lines **every day** to the exact count the
reveal reports a delta against (R139), and would do it unattended, which is the worst possible
way for a reported number to move. The reveal design already names where a daily record belongs:
its protection 3, *"an append-only forecast log, one line per day per configuration, each carrying
the champion fingerprint"* — a different file, with a different meaning, written by a different
thing.

So the daily run's relationship to the three ledger-writing stages is one of these, and **which
one is the user's call** (§6, Q2):

| option | what the daily run does with walk-forward / compare / evaluators |
|---|---|
| **A — forecast only** | never invokes them. It produces today's forecast from the frozen champion and appends one line per configuration to a **new** forecast log. The three ledgers are untouchable by construction |
| **B — invoke, but only off the real store** | invokes them with a storage root that is not the repository's, as the dress rehearsal does. Proves the chain works end to end and still cannot move a reported number |
| **C — invoke against the real store** | forbidden before the reveal by R139, and forbidden in this session by the brief's hard constraint |

**Decided by the user, 2026-11-03: A for the live path and B for the rehearsal** (R158 Q2) — the
only combination consistent with R139 and with the reveal design's protection 3.

### 2.3 Out of scope, each for a stated reason

- **Taking the reveal.** R139 and the reveal design's own non-goal. Phase 6's, and the user's.
- **Building the reveal's protections 1, 2, 4 and 5** (pre-registration gate, dress rehearsal of
  the reveal itself, champion fingerprint, log-before-read reordering). They belong to whoever
  builds that design. Phase 4 needs **one** thing from it — a named `live` region — and §6 Q1 asks
  whether that slice is Phase 4's.
- **The agent layer** (Phase 5; needs Postgres and LLM keys an agent cannot provision) and **the
  write-up** (Phase 6). `README.md:35` groups 4-6 in one row; they are not one phase.
- **A web page or any UI.** Asked and declined. The leaderboard already renders the result.
- **Installing the unattended trigger.** §4.5.
- **MLflow.** R84 refuses it for a stated reason that has not changed: MLflow 3.x needs
  `pyarrow<26` and this project pins `>=26`.

---

## 3. Shape

### 3.1 A new package, `src/volab/daily/`

A package rather than a module, because it has at least four separable pieces (the plan, the
stage runner, the run log, the CLI) and because the import-discipline test is per-module.

```
src/volab/daily/
  __init__.py   the plan: what today's work is, as data. No I/O.
  runner.py     executes a plan's stages. Owns subprocesses and timing.
  runlog.py     the two-line append-only run record.
  __main__.py   the CLI: python -m volab.daily run [--as-of DATE] [--plan-only]
```

**The plan is data, computed before anything runs, and printable without running.** `--plan-only`
prints exactly what would happen and touches nothing — this is how an operator inspects an
unattended job, and how a test asserts the refusal without executing a stage.

This is not a `--dry-run` that disables a safety. The refusals in §4.1 fire inside the plan, so
`--plan-only` and a real run refuse identically; the flag selects whether the *stages* execute,
never whether the *guards* do.

### 3.2 Stages run in subprocesses **(my call)**

Each stage is `sys.executable -m volab.<stage> ...`, not an in-process call of its `main`.

The repository's own reason: `tests/test_process_isolation.py` exists because a memoised
full-sample statistic filled by one build and reused by the next is invisible to truncation
equivalence within a process, and the README states the pair of defects that demonstrated it (E2).
A daily run chaining six stages in one interpreter reintroduces exactly that sharing, in
production, where no test is watching. The cost is process start-up per stage, which is
irrelevant at a daily cadence.

Consequence to pin: a stage's exit code is its verdict and its output is captured into the run
record, so a `return 1` from any of the six CLIs is a recorded stage failure, not a silent skip.

### 3.3 The run record

`data/daily_runs.jsonl`, append-only, two lines per run:

```
{"kind": "start",    "id": "<uuid4>", "as_of": "2026-11-03", "sha": "...", "dirty": false, "plan": [...], "started_at": "...Z"}
{"kind": "complete", "id": "<uuid4>", "ref": "<the start id>", "stages": [...], "outcome": "done|refused|failed", "ended_at": "...Z"}
```

- **Two lines, the second referring to the first by id**, so a run that dies leaves a visible
  start with no completion and neither line can be rewritten to conceal the other. Borrowed from
  the reveal design's call #3 rather than invented.
- **`sha` and `dirty`**, as every other record in this project carries them.
- **Tracked or ignored?** `data/**` is gitignored with named exceptions. A run log is evidence of
  unattended behaviour, and `git clean -xdf` erasing it would make "it ran every day" unprovable —
  the argument `.gitignore` already makes for `trials.jsonl`. But a tracked file an unattended job
  appends to every day makes the working tree dirty daily, and a dirty tree is itself a recorded
  flag elsewhere in this project. **Decided by the user, 2026-11-03: gitignored** (R158 Q3). The
  tree does not go dirty every morning; the cost, accepted, is that `git clean -xdf` erases the
  evidence that the job ran.
- **No count from this file is a reported number**, stated here so it never becomes one by
  accident.

### 3.4 Idempotence

A plan for a date whose **completed** run record already exists is a no-op with reason
`already_done`. Keyed on `as_of`, not on wall-clock time, so a run at 09:00 and a retry at 11:00
on the same date are one run. An **incomplete** record (start with no completion) does **not**
block a retry — that is the reveal design's call #1 applied here: attempts are counted, not
forbidden, and a transient failure must not brick tomorrow.

---

## 4. Anti-goals — what would make this a failure even if it worked

Each has a witness, because R32 requires one and because this project has found nine leakage
escapes by running something rather than by reading it.

| # | anti-goal | why | witness |
|---|---|---|---|
| A1 | **The scheduler reaches for `allow_holdout` or `VOLAB_HOLDOUT_UNLOCK`.** `phase2-completion-sprint-board.md:225` predicted this exact failure: *"an agent asked to make dailies work is exactly who would reach for `allow_holdout`"* | it is the one line of code that turns an honest harness into a leaky one, and an unattended job is where nobody is watching | a derived scan (R151) over `src/volab/daily/**` asserting neither name appears, plus a behavioural test: an `--as-of` past `dev_end` is refused and **no** audit line is written |
| A2 | **A new ledger line in `trials.jsonl`, `comparisons.jsonl` or `decisions.jsonl`** | they are reported numbers and the reveal reports a delta against the first (R139) | the daily run executed against a `tmp_path` root, with the real ledgers' md5 asserted unchanged either side; **and** a derived import check that `volab.daily` imports neither sink module |
| A3 | **The clock inside a boundary.** Anything that computes `dev_end`, `holdout_start` or a cutoff from `date.today()` | the README's invariant: *"nothing computes a boundary from the clock"*. A scheduler is the first module here with a clock, so it is the first that could break it | a test passing `as_of` = a date past `dev_end` and asserting refusal, and a derived scan asserting no `date.today()`/`datetime.now()` call outside the one injection seam |
| A4 | **A flag that disables a guard** — a `--force`, a `--no-check`, a rehearsal mode inside the real path | the reveal design's anti-goal #1 in another hat. A seal a flag can lift was never a seal | `--plan-only` asserted to refuse identically to a real run; the rehearsal exercised by pointing at another storage root, never by a mode flag |
| A5 | **An unattended job installed by this work** — a crontab line, a launchd plist loaded, a GitHub Actions schedule enabled | the brief's constraint is to build it so it *can* run and leave it unrun; and a persistent trigger on this machine is the user's to create | the install snippet ships as documentation and a committed template only; no test and no code loads it, and §4.5 says so |
| A6 | **A run that cannot be attributed.** A record without a sha, a dirty flag, or a completion line | an unattended record nobody can attribute is worse than none, because it looks like evidence (the reveal design's anti-goal #4, same argument) | a test that kills a run mid-stage and asserts the start line survives with no completion, and that the next run is permitted |

### 4.5 On installing the trigger

The deliverable is an idempotent entry point plus a documented snippet (a `launchd` plist for this
machine, or a `cron` line) that the **user** installs. Nothing in this work writes to
`~/Library/LaunchAgents`, runs `crontab`, or enables a scheduled workflow. A scheduler that
installs itself during its own construction is an unattended job running against a tree that is
mid-change.

---

## 5. What is left to the builder

- The exact plan dataclass, and whether a stage is a dataclass or a tuple.
- Whether the forecast log (§2.2 option A) is Phase 4's to define or Phase 6's — §6 Q2.
- How a stage's captured output is bounded. A stage printing megabytes must not make the run
  record unreadable.
- Where the `launchd`/`cron` template lives (`tools/` has a README convention for probes; a
  template is not a probe).
- Whether `--as-of` defaults to the injected today or is required. A required flag cannot be
  forgotten; a default is what makes the job unattended. Likely: defaulted in the CLI, required in
  the library.

---

## 6. Open, and genuinely the user's — **all four answered 2026-11-03; see R158**

**Q1 — does Phase 4 own the `live` region?** The reveal design's "left to the builder" list opens
with *"where `holdout_end` lives in `Splits`, its YAML key, and its validation (including the
reversed override direction)"*. That is unbuilt (§1.3). Phase 4 needs it to have any legal target
past `dev_end` at all. Either Phase 4 builds that one slice — `holdout_end` in `Splits` and
`config/base.yaml`, the **later-only** override direction, the three regions named, `live` still
unreachable until the reveal — or Phase 4 is a scheduler whose only legal `--as-of` is on or
before `dev_end` until some other session builds it.

**Q2 — are the forecast log and the champion fingerprint Phase 4's or Phase 6's?** §2.2 option A
needs a champion to forecast from, and before the reveal there is no champion. Exercising option A
on dev therefore needs a *pseudo*-champion (the leaderboard's current dev winner, clearly labelled
as not the registered one). That is honest but it is a second kind of record, and whether it should
exist before the reveal is a research decision rather than an engineering one.

**Q3 — is `data/daily_runs.jsonl` tracked or ignored?** §3.3 states both arguments. Tracked makes
unattended behaviour provable and makes the tree dirty daily; ignored keeps the tree clean and
makes the record erasable by `git clean -xdf`.

**Q4 — the first slice.** The board's stories are ordered so that any prefix of them is a
coherent deliverable; which prefix lands in the first PR is the user's call.

---

## 7. Constraints inherited, none negotiable

- `uv run --locked …` always; never bare `uv run`, never `--frozen`.
- `filterwarnings = ["error"]`: a warning is a failing test.
- `tests/test_import_discipline.py` keeps `ALLOWED == {"store.py"}`. `volab.daily` touches no
  Parquet and imports no numpy or scipy — it drives CLIs and writes JSONL.
- The autouse `_forbid_holdout_unlock` fixture fails any unmarked test that reads past `dev_end`.
  It is a feature; no Phase 4 test may need `@pytest.mark.holdout`, and if one appears to, that is
  a finding about the design and not a reason for the marker.
- `data/trials.jsonl` at 414 and `data/comparisons.jsonl` at 52 — measured either side of every
  suite run.
- **A test count is a gate (R154).** Every commit message in this phase states the collected count
  and the sha it was measured at (R155 rule 5), and a diff that should only add tests is checked
  with `git diff <base> -- tests/` showing no removed lines.
- **Derive, never enumerate (R151).** Every guard in §4 that scans a set — modules under
  `volab/daily/`, forbidden names, the ledgers that must not move — derives it, and each derived
  guard ships with a mutation that the enumerated version would have passed.
- **Another agent's report is evidence to check, never a fact (R150)** — including this document,
  the brief that commissioned it, and the board beside it.
