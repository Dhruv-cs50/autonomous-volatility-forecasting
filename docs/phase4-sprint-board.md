# Phase 4 sprint board — unattended daily runs (the scheduler)

**Design:** `docs/superpowers/specs/2026-11-03-phase4-unattended-daily-runs-design.md`. Its
**section 0** is the first thing to read: this phase's scope is a reconstruction, and section 0.2
says what that forbids.
**Decisions:** `docs/superpowers/phase4-decisions.md`. **Numbering: Phase 4's rulings are R156
onward, in their own file** (R156 records why). Allocated by scanning every branch, local and
remote, not `main` alone — `main` at `56bf9e2` carries R155, and R153-R155 were each allocated
that way after `main` alone would have given the wrong answer (R150, R155).
**Branch:** `phase-4-scheduler`, cut from `origin/main` at `56bf9e2`, confirmed with
`git log -1 origin/main` and not from any report of it (R150).
**Worktree:** `../vol-lab-phase4`. The shared clone at `vol-lab` was left on its own branch and
not touched.

**The exit criterion is a RECONSTRUCTION, not a quoted requirement.** The framework document is
not in this repository (R135) and is not committed now. **This phase may be declared *complete as
specified*, never *exited*** — the same ruling Phase 3 closed under, and the same distinction any
write-up citing this phase must carry. The standing ask is unchanged and is now two boundaries
old: **commit the framework document into `docs/`**.

**Ids are `P4-nn`.** Phase 3's board owns `P3-nn` and the Phase 2 completion board owns `US-3.9x`.

---

## The two numbers that must not move

```
data/trials.jsonl       414 lines   md5 090e37b08f03d0548f63d1515ec8e35c
data/comparisons.jsonl   52 lines   md5 b9f41c2651f10f3f9288d6cb427e2c9a
```

Measured in this worktree at `56bf9e2` before any work began, and to be re-measured with `wc -l`
and `md5` either side of **every** suite run. R139 holds the recorded run until the user triggers
the reveal. Three of the six pipeline stages are writers of one of these files
(`walkforward`, `compare`, and `evaluators` through `src/volab/evaluators/sink.py:145`), which is
why the design's §2.2 forbids the daily run from invoking them against the real store.

## Baseline at `56bf9e2`, measured here

| gate | reading |
|---|---|
| `uv run --locked ruff check .` | clean, 99 files |
| `uv run --locked ruff format --check .` | clean, 99 files already formatted |
| `uv run --locked mypy src` | clean, 41 source files |
| `uv run --locked pytest -q` | **1734 passed**, 0 skipped, 272.96s |

**And at `fc266bb`, the close of the first slice:** **1780 passed**, 0 skipped, 240.25s; `ruff
check` and `ruff format --check` clean over 104 files; `mypy src` clean over 44. The arithmetic,
because a test count is a gate (R154): **1780 = 1734 + 41 + 5** — 41 tests in
`tests/test_daily.py`, and 5 that existing *derived* parametrisations add for the new modules
(three `test_import_discipline` cases over `src/volab/daily/*`, two probe cases over
`tools/mutate_daily_guards.py`). `git diff 56bf9e2 -- tests/` removes no line. Both ledgers are
byte-identical either side of the run, and no `data/daily_runs.jsonl` exists — nothing in this
slice ran a stage.

A test count is a gate (R154), and a gate's reading needs the sha it was taken at (R155 rule 5),
so every commit message in this phase states the collected count and this sha. The real store is
present in this worktree: `data/{raw,curated,features}` were copy-on-write cloned from the shared
checkout, 24219 / 22127 / 34403 files, matching it exactly. A bare worktree has those tiers empty
and some modules then fail rather than skip, so a count measured without them is not comparable.

---

## Stories

Ordered so that **any prefix is a coherent deliverable**. **The first PR is P4-01 and P4-02**
(user decision, R158 Q4). No story closes on assertion: a commit must exist, a test that
bites must exist, and `ruff check`, `ruff format --check`, `mypy src` and `pytest` must all be
green, everything through `uv run --locked`.

| id | story | files | acceptance | state |
|---|---|---|---|---|
| P4-01 | **The plan, and the refusal.** Today's work computed as data from an injected `as_of`, printable without running anything | `src/volab/daily/__init__.py` (new), `src/volab/daily/__main__.py` (new), `tests/` | an `as_of` past `dev_end` is refused with a message naming `dev_end`, and the refusal is decided inside the plan rather than at the point of execution, so the `plan` verb refuses exactly as a future `run` will (the ordering is pinned: a sealed date that is also a closed day is refused **for being sealed**); a non-session day is a no-op carrying its reason, derived from the XNYS calendar and not from a weekday test; **three derived scans (R151) over every module under `src/volab/daily/`: no holdout key is *used* in code (identifiers loosely, string literals strictly, so the refusal message may say the holdout is sealed), no clock is reached outside the one seam (calls, bare references, literal `getattr`, and an import of `time`), and no planned stage runs a ledger-writing package (matched by prefix, derived by reading the tree). Each ships a red witness, a floor against a vacuous scan, and a test for every exclusion it makes (R159, R160)** | **done** (`fc266bb`) — thirteen planted defects, each failing exactly the tests named against it and no others, reproducible with `tools/mutate_daily_guards.py`, which refuses to start on a red suite. Reviewed by an independent seat, whose eight findings are fixed and recorded in R160 |
| P4-02 | **The run record.** Two append-only lines per run, the second referring to the first by id | `src/volab/daily/runlog.py` (new), `tests/` | the start line is written, flushed and `fsync`ed by `record_start` — witnessed by counting the `fsync` calls, because every test that reads the file back would pass without them — so it is on disk before the caller does anything else; **that it precedes a *stage* is unwitnessed until P4-03, since no stage runs in this slice**; a start with no completion leaves its date retryable, and `incomplete_run_ids` reports it (attempts are counted, not forbidden); a date whose *completed* record exists makes the plan `already_done`, **through the shipped `plan` verb and not only through a hand-built set** — and a completion recorded as anything but `done` leaves its date available, because a failed run has not done the day's work; **the start line carries the sha and the dirty flag** and the completion line joins to it by `ref` rather than repeating them; the join is on the id and survives interleaved runs; a completion referring to no start counts as nothing; **a torn final line is skipped rather than raised on**, so one crash cannot stop every later run asking what has already completed | **done** (`fc266bb`) |
| P4-03 | **The stage runner.** Each stage a fresh interpreter, its exit code its verdict | `src/volab/daily/runner.py` (new), `tests/` | a stage is run as `sys.executable -m …` and never by calling its `main` in-process, with the reason named in the docstring (`tests/test_process_isolation.py`'s E2: a memoised full-sample statistic shared between two builds in one process); a non-zero exit is recorded against *that* stage and stops the run; captured output is bounded, and the bound is asserted | **todo** |
| P4-04 | **The rehearsal: a whole daily run off the real store.** | `tests/` (and whatever P4-01..03 need) | a complete run against a `tmp_path` storage root produces a start and a completion line and a stage result per stage; **the real `data/trials.jsonl` and `data/comparisons.jsonl` md5s are asserted unchanged either side**; a derived import check asserts `volab.daily` imports neither `volab.walkforward.sink`-side writer nor `volab.evaluators.sink`, with a planted import proving it bites | **todo** |
| P4-05 | **The `live` region** — `holdout_end` in `Splits` and `config/base.yaml`, the **later-only** override direction, the three regions named | `src/volab/config.py`, `config/base.yaml`, `tests/` | `VOLAB_SPLITS_HOLDOUT_END` is refused when it moves the date **earlier** (the opposite direction to `dev_end`'s rule, which the reveal design warns a builder will copy backwards), and a test pins both directions; `live` stays unreachable — no read path past `holdout_end` is opened | **todo — in scope (user decision, R158 Q1).** The reveal design's own "left to the builder" item, unbuilt today; Phase 4 has no legal target past `dev_end` without it. Not in the first slice |
| P4-06 | **The forecast log and the pseudo-champion** | `src/volab/daily/`, `tests/` | one append-only line per day per configuration in a file that is **not** one of the three ledgers; before the reveal the champion is a clearly-labelled dev winner, never the registered one | **todo — in scope (user decision, R158 Q2: option A for the live path).** Needs a champion, and before the reveal there is none, so the dev winner is used and labelled as such. Not in the first slice |
| P4-07 | **The trigger, shipped and not installed** | `docs/`, a template file, `README.md` | a `launchd` plist (and a `cron` line) committed as a template with the install command in prose; **nothing in the repository installs, loads or enables it**, and a test asserts no code path writes to a LaunchAgents path or shells out to `crontab` | **todo** |

## Found while building the first slice, and not fixed here

Recorded rather than acted on, which is R155 rule 4: re-derivation and repair are separate passes,
and a board that records its own intentions in the column where the tree's state belongs is how
row 5 of Phase 3's table came to describe a diff it had.

| finding | state at `fc266bb` | whose |
|---|---|---|
| **`volab.ingest backfill` writes `data/vendor_payloads/**`, which is *tracked*.** So an unattended daily run dirties the working tree every morning, which makes the `git_dirty` flag on every record written that day read `True` — and that flag is how this project tells a run against a modified tree from a clean one (R70) | OPEN. The stage is in `LIVE_STAGES` and nothing runs it yet; the first thing to run it is P4-03 | **P4-03's**, with P4-07: either the daily job commits the payloads it archives, or the dirty flag's meaning has to be stated for a scheduled run |
| **A derived guard needs its exclusions pinned, and this slice paid for three of them** — naming a ledger is not writing one, a docstring naming a key is not using it, and `inspect.signature` at runtime returns the holdout police's wrapper rather than the store's own signature | CLOSED, each by a test of its own (R159) | done |
| **An independent review seat found four claims stronger than the tree and four guards that did not bite**, including a `run --plan-only` docstring for a verb that does not exist, an `fsync` clause with no witness, `already_done` true of the library and false of the CLI, and a holdout scan that an `os.environ[HOLDOUT_UNLOCK_ENV]` walked straight through | CLOSED. Each is fixed and carries its own test; the mutant count went from nine to thirteen (R160) | done |
| **Three limits of the derived guards are now stated rather than discovered later**: the writer derivation is not transitive (a package that imports `append_trials` without naming a ledger is invisible to it, which is P4-04's import check to close), an AST clock scan cannot see a call assembled at runtime, and the holdout scan reads this package's source and not the environment that launches it | OPEN as stated limits, each written into the guard's own docstring | **P4-04's**, for the transitive half |

## Held, not forgotten

| item | why it is held | releases on |
|---|---|---|
| Any daily run against the **real** storage root | three of six stages write a reported number (R139); and the live path has no legal target while `holdout_end` does not exist | the reveal, and P4-05 |
| The first genuinely live morning | the holdout-reveal design, §92-94: live data is readable only after the reveal | **the user triggering the holdout reveal** |
| Phase 3's recorded run | inherited, unchanged: R139, D5 | the same event |

## Not this phase's, and why

- **Taking the reveal** (R139) and **protections 1, 2, 4 and 5** of the holdout-reveal design —
  the pre-registration gate, the dress rehearsal of the reveal itself, the champion fingerprint
  and the log-before-read reordering. Phase 4 needs one thing from that design, a named `live`
  region, and P4-05 is that slice.
- **The twelve open rows of Phase 3's deferred-findings table.** They are Phase 3's, and R155's
  own ruling says acting on any of them is the user's call. Four are marked `before the reveal`
  and two of those touch what the registered read reports — the leak detector demonstrably
  catches only an exact-value leak (row 13), and `_describe`'s NOT KNOWN branch prints a literal
  "a count of 0" while the ledger beside it records the real figure (row 10). Noted so the next
  boundary does not meet them for the first time.
- **The agent layer** (Phase 5) and **the write-up** (Phase 6). `README.md:35` groups 4-6 in one
  table row; they are not one phase.
- **A web page or any UI.** Asked and declined.
- **MLflow.** R84's refusal stands and its reason has not changed.

## Method

Test-first, with a planted defect proving the test bites wherever test-first was departed from. A
review seat on every story whose **first** instruction is to read each docstring against what the
test body executes — Phase 3 found eleven separate instances of a test or docstring claiming more
than its code performed, every one caught by a review seat and never by the author. Derived
guards, never enumerated ones, each shipping with a mutation the enumerated version would have
passed (R151). A board row states the sha it was verified at, and a line number is treated as
perishable (R155).

Nothing is done on assertion.
