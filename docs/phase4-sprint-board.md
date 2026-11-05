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

**At `5a017d9`, measured by the orchestrator and re-derived independently by the delivery audit:**
**2013 collected and 2013 passed**, 0 skipped, 281.81s; `ruff check` and `ruff format --check` clean
over 111 files; `mypy src` clean over 47. Both ledgers byte-identical either side of the run at 414
and 52 lines with the pinned md5s; `git log -p 56bf9e2..HEAD -- data/` empty; no `data/daily_runs.jsonl`
or `data/forecasts.jsonl` in the repository; and `git diff 56bf9e2 -- tests/` removes **no**
`def test_` line (its ten removed lines are the `Splits(...)` constructions P4-05b widened).

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
| P4-03 | **The stage runner.** Each stage a fresh interpreter, its exit code its verdict | `src/volab/daily/runner.py` (new), `tests/test_daily_runner.py` | a stage is run as `sys.executable -m …` and never by calling its `main` in-process, with the reason named in the docstring (`tests/test_process_isolation.py`'s E2: a memoised full-sample statistic shared between two builds in one process); a non-zero exit is recorded against *that* stage and stops the run; captured output is bounded, and the bound is asserted | **done** (`e037c7d`) — three distinct pids including the parent's, the stage module asserted never imported into the calling process, `OUTPUT_BOUND_BYTES = 16 * 1024` with the tail kept and the dropped count exact, and a megabyte-printing stage asserted not held in memory. **The `run` verb was deliberately left out**: `record_start` needs an instant and the clock guard permitted only a date-returning seam, so the engineer reported the contradiction rather than evading the guard. That is P4-03b |
| P4-03b | **The `run` verb, and the two gaps P4-03 found.** The clock seam returns an instant; the stage output lands in the record; a per-stage timeout | `src/volab/daily/__main__.py`, `src/volab/daily/runlog.py`, `tests/test_daily.py`, `tests/test_daily_runner.py`, `tools/mutate_daily_guards.py` | the seam becomes `_now() -> datetime` and `_today()` becomes pure, with the derived clock guard following it and weakened in no other way; `run` writes the start line **before the first stage** — witnessed by a probe stage that exits non-zero unless the line is already in the log, which is what this claim has lacked since P4-02; a refusal writes nothing at all; the completion line carries each stage's bounded output and dropped-byte count (design §3.2 claimed this and `record_completion` did not do it); a timed-out stage is a failed stage, recorded, fail-stop, child killed | **done** (`48d5cfc`, with a follow-up folded into `aa65c5a` — see the attribution note below) — the seam is `_now()` and `_today()` is pure on top of it (R161); `run` writes the start line before the first stage, **witnessed** by a probe stage handed the log path in argv that exits non-zero unless the line is already there, which is the claim P4-02 carried unwitnessed; a refused, closed or already-done date runs nothing and leaves no file behind; each completion-line stage object carries `output`, `dropped_bytes` and `timed_out`; a stage past `STAGE_TIMEOUT_SECONDS` is killed by process group, recorded `exit_code` 124 with `timed_out=True`, and the run stops. Exit codes: 1 a refusal, 2 a failed or timed-out stage, 0 nothing to do. **21 mutants, each failing exactly the tests named against it** |
| P4-05b | **Make `holdout_end` required rather than defaulted** | `src/volab/config.py`, `tests/conftest.py`, `tests/test_evaluators.py`, `tests/test_conftest_policy.py`, `tests/test_store_guard.py`, `tests/test_daily.py`, `tests/test_sessions.py` | the field has no default and every `Splits(...)` construction in the suite passes one; a boundary that can be omitted is not a boundary | **done** (`567616c`) — the grep found **12** construction sites, not the "about ten" the brief carried over from another agent's report, and 11 needed changing; each value sits after its own `holdout_start`, and `dt.date.max` now appears at exactly one `Splits(...)` construction, whose comment says the holdout never ends there (the literal occurs three times in `tests/test_config.py`, the other two being parametrize cases). `test_splits_cannot_be_built_without_a_holdout_end` pins that omission raises. **One departure, recorded:** the default was removed and the pinning test written in the same pass, so the test was never seen failing for the expected reason; the audit owes it a planted witness |
| P4-04 | **The rehearsal: a whole daily run off the real store.** | `tests/test_daily_rehearsal.py` | a complete run against a `tmp_path` root produces a start line, a completion line and a stage result per stage; **the real `data/trials.jsonl` and `data/comparisons.jsonl` are asserted unchanged either side** — read from the files rather than hard-coded, since pinned digests would fail on the day the reveal legitimately appends — and the whole of `data/` is snapshotted by size and mtime so a write anywhere under it is caught; a **transitive** import check over `volab.daily`'s closure asserts no module in it names a ledger-writer entry point, with planted violations proving each half bites | **done** (`021623d`) — 38 tests. **Its acceptance as written above was wrong and is corrected here:** the original row said the check asserts `volab.daily` imports "neither `volab.walkforward.sink`-side writer nor `volab.evaluators.sink`". There is no `volab.walkforward.sink`, and `volab.walkforward` and `volab.compare.sink` **are** reachable by design (R163). What shipped pins that the closure's writer modules are **exactly** `{walkforward, compare.sink}` and that no `volab.evaluators*` module is in it — a third writer arriving fails the test and is named. The audit re-derived the closure independently: 42 modules, no violations, and the pin verified to fail on four separate planted spellings of an `evaluators` import |
| P4-05 | **The `live` region** — `holdout_end` in `Splits` and `config/base.yaml`, the **later-only** override direction, the three regions named | `src/volab/config.py`, `config/base.yaml`, `tests/` | `VOLAB_SPLITS_HOLDOUT_END` is refused when it moves the date **earlier** (the opposite direction to `dev_end`'s rule, which the reveal design warns a builder will copy backwards), and a test pins both directions; `live` stays unreachable — no read path past `holdout_end` is opened | **done** (`85de6a5`), **with one deviation carried as P4-05b.** `holdout_end` is `2026-10-30` in `config/base.yaml` and on `Splits`; `VOLAB_SPLITS_HOLDOUT_END` is refused when it moves the date **earlier** and accepted later or equal, and a file pinning no literal is refused too, so deleting the key cannot sidestep the rule; `Region` and `Splits.region_of` name dev, holdout and live, total over all dates, with a date in the dev/holdout seam classified **holdout** — because `dev` is exactly the test `load_panel` applies to a read's `end`, so calling the seam `dev` would label as free a date the guard refuses. No read path past `holdout_end` was opened: a live-dated read is refused by the existing guard with a message identical to a holdout-dated one once the date is masked. ~~**The deviation:** the field defaults to `dt.date.max` rather than being required~~ — **superseded by P4-05b (`567616c`), which removed the default.** The row is left struck through rather than deleted, so a reader meeting P4-05 alone does not conclude the default still exists |
| P4-06 | **The forecast log and the pseudo-champion** | `src/volab/daily/`, `tests/` | one append-only line per day per configuration in a file that is **not** one of the three ledgers; before the reveal the champion is a clearly-labelled dev winner, never the registered one | **P4-06a done** (`aa65c5a`, and see the attribution note), **P4-06b done** (`3db9b52`), **P4-06c done** (`a233447`). P4-06a built `data/forecasts.jsonl`, its schema and the champion fingerprint, refusing to write a line while no champion existed rather than inventing one; it also refuses a duplicate `(as_of, symbol, horizon)` before writing anything, so the log cannot hold two forecasts for one key. P4-06b derived the **labelled pseudo-champion** from the trial ledger: lowest QLIKE in the **latest run** per symbol and horizon (R86 forbids ranking runs against each other), with a tie, an empty ledger, an uncovered configuration, two runs at the same instant, or an undefined QLIKE each refused by reason and never guessed. `kind` is `"pseudo"` on every line, and an AST scan over the package asserts **no code path can write `"registered"`**, with a red witness over a literal, a keyword and a subscript. It also gave `Stage` a `needs_as_of` flag so the plan dates the stages it carries, which is how a stage learns the date without reaching a clock. **P4-06c closed the gap both stories reported**: a line needs `value` and `observations`, so the champion is fitted through `volab.walkforward.evaluate` for a single origin — no second fitting path — reading the store at `min(as_of, dev_end)` with no key, and the run's instant travels in argv from the one seam, so a forecast line's `at` equals its start line's `started_at`. A configuration that cannot be fitted is refused with its reason, and any refusal makes the stage exit non-zero, so a partial day is recorded `failed` and stays retryable. **That fitting call is a deviation from R158 Q2, which is the user's decision: see R163, and the three options it leaves open.** The file stays gitignored: before the reveal its lines carry a pseudo-champion, and a tracked file would put unregistered numbers in git |
| P4-07 | **The trigger, shipped and not installed** | `docs/phase4-daily-trigger.md`, `tests/test_daily_trigger.py`, `README.md` | a `launchd` plist (and a `cron` line) committed as a template with the install command in prose; **nothing in the repository installs, loads or enables it**, and a test asserts no code path writes to a LaunchAgents path or shells out to `crontab` | **done** (`2990337`) — 26 tests. The scan is derived from the tree with a per-root floor, judges code and not prose (a docstring naming an installer does not fire, a literal used in code does, and a concatenation is folded first), has a planted-installer witness, and each exclusion has its own test. The document is checked against the CLI rather than trusted: the plist parses, its verb is one the CLI accepts, the plist and cron line agree on verb and time, and the job logs outside the checkout |

## Three stories came back with a report instead of a workaround, which is the sprint's result

R162 rules that a brief names the claims its story will lean on and the engineer reports any the
tree does not keep. It happened three times, and each time the alternative was an evasion that
would have passed every gate:

| story | what it refused to do |
|---|---|
| P4-03 | build the `run` verb by sneaking an instant past the clock guard — a lambda default, a nested function, an aliased import, `stat().st_mtime`. It reported the contradiction and the seam moved instead (R161) |
| P4-06a | invent an `argv` for a forecast stage that had no way to learn its date, when `LIVE_STAGES` was a static tuple |
| P4-06b | fabricate a forecast line. `value` and `observations` need the champion fitted, which needs a store read its brief forbade, so it narrowed the refusal and said so rather than writing a line with made-up numbers |

**None of those three would have failed a single gate.** A fabricated forecast line is the exact
defect this whole project exists to refuse, and it would have been green.

## Two process failures in this sprint, recorded rather than tidied away

**A commit was misattributed by an `--amend` that raced another engineer.** P4-03b committed
`48d5cfc`, then amended to add two follow-up fixes — but HEAD had moved to another engineer's
`806a28f` in the meantime, so the amend rewrote **their** commit. The result is `aa65c5a`, whose
message says "P4-06a: the append-only forecast log" while it also carries P4-03b's
`tests/test_daily_runner.py` (+5) and `tools/mutate_daily_guards.py` (+36/-6, per `--numstat`).

Verified before deciding anything: the reflog shows `806a28f -> aa65c5a (amend)` happening *before*
the orchestrator's next commit, `48d5cfc` is still an ancestor and still holds the main P4-03b
change, and nothing was lost. Nothing had been pushed at `806a28f`, so no published history was
rewritten. **It is not being repaired**: rewriting history with two engineers committing into the
same worktree would cost more than the misattribution does, and a commit message that misdescribes
its own contents is exactly the kind of claim this project writes down instead of quietly fixing.
So: `aa65c5a` is P4-06a **plus** those two P4-03b files.

**The rule this adds to the pathspec discipline.** Concurrent agents already commit with an
explicit pathspec so one cannot stage another's work. That does not cover `--amend`, which rewrites
whatever HEAD happens to be — and HEAD is shared state too. **No `--amend` in a shared worktree:
make a second commit.**

**The mutation probe was run against a copy, not the tree.** P4-03b could not run
`tools/mutate_daily_guards.py` in place, because its plant-and-revert would have clobbered the two
files another engineer was editing live (R131 names this hazard). It ran against a `git archive` of
`aa65c5a` with `PYTHONPATH` pointed at the copy and reported 21 of 21 mutants exact. **That figure
is not yet evidence about this tree**, and the delivery audit re-runs the probe in place once the
engineers are idle.

## Found while building the first slice, and not fixed here

Recorded rather than acted on, which is R155 rule 4: re-derivation and repair are separate passes,
and a board that records its own intentions in the column where the tree's state belongs is how
row 5 of Phase 3's table came to describe a diff it had.

| finding | state at `fc266bb` | whose |
|---|---|---|
| **`volab.ingest backfill` writes `data/vendor_payloads/**`, which is *tracked*.** So an unattended daily run dirties the working tree every morning, which makes the `git_dirty` flag on every record written that day read `True` — and that flag is how this project tells a run against a modified tree from a clean one (R70) | OPEN. The stage is in `LIVE_STAGES` and nothing runs it yet; the first thing to run it is P4-03 | **P4-03's**, with P4-07: either the daily job commits the payloads it archives, or the dirty flag's meaning has to be stated for a scheduled run |
| **A derived guard needs its exclusions pinned, and this slice paid for three of them** — naming a ledger is not writing one, a docstring naming a key is not using it, and `inspect.signature` at runtime returns the holdout police's wrapper rather than the store's own signature | CLOSED, each by a test of its own (R159) | done |
| **An independent review seat found four claims stronger than the tree and four guards that did not bite**, including a `run --plan-only` docstring for a verb that does not exist, an `fsync` clause with no witness, `already_done` true of the library and false of the CLI, and a holdout scan that an `os.environ[HOLDOUT_UNLOCK_ENV]` walked straight through | CLOSED. Each is fixed and carries its own test; the mutant count went from nine to thirteen (R160) | done |
| **The 2-hour stage timeout is a bound, not a measurement.** `STAGE_TIMEOUT_SECONDS = 2 * 60 * 60` was chosen without timing the real stages, deliberately long because a false timeout abandons a day's work while a late one costs a morning | OPEN as a stated limit, in the runner's own docstring | whoever first times a real unattended run — which cannot happen before the reveal |
| **A `work` plan with zero stages records `done`.** Only reachable by patching `LIVE_STAGES`, which the real tuple is never empty of | OPEN, and benign today | noted so it is not discovered as a surprise |
| **Three limits of the derived guards were stated rather than discovered later**: the writer derivation is not transitive, an AST clock scan cannot see a call assembled at runtime, and the holdout scan reads this package's source and not the environment that launches it | **The transitive half is CLOSED** by `tests/test_daily_rehearsal.py` (P4-04, `021623d`), and `tests/test_daily.py`'s docstring now says what that check found instead of what someone intended to build (`5a017d9`). **The other two are OPEN, and a review seat measured them to be wider than their own docstrings admit** — see the table below | P4-08b |

## Sprint 1 — the remaining stories, run as waves

Each story is at most one dispatch of work; a story that grows mid-build is re-cut rather than
stretched, which is why P4-03b and P4-05b exist. A wave holds only stories whose **file sets are
disjoint**, because the git index is shared state here: every engineer commits with an explicit
pathspec and no two touch one file.

| wave | stories | state |
|---|---|---|
| 1 | P4-05, P4-03, P4-07 | **closed.** `85de6a5`, `e037c7d`, `2990337` |
| 2 | P4-03b, P4-06a | in progress |
| 3 | P4-06b, P4-05b | **closed.** `3db9b52`, `567616c` |
| 4 | P4-06c | **closed.** `a233447` — and it also gave `volab.leaderboard` a public `ranked_trials`, so `volab.daily` stops importing a private name |
| 5 | P4-04 | **closed.** `021623d` |
| 6 | the delivery audit, a review seat, and the mutation probe **in place** | **the probe ran in place at `5a017d9`: 21 of 21 mutants fail exactly the tests named against them.** The audit has reported; a review seat is reading prose against execution |
| 7 | P4-08 — close what the audit found | the audit's findings are below, and they are not all cosmetic |

**What wave 1 found that this board had claimed and the tree did not support**, now owned by
P4-03b: design §3.2 says a stage's output is "captured into the run record", and
`record_completion` wrote only a name and an exit code; and the runner had no timeout, so one hung
stage would hang an unattended job forever. **Both were found by the engineer building the
neighbouring story**, not by a reviewer — which is the first time in two phases that has happened.

**A note on the restart.** The session running this sprint was killed while waves 2 and 3 were in
flight. Nothing of their work had reached disk; the two engineers were resumed from their own
transcripts after the tree state was re-verified against `git log` and `git status` rather than
reported to them (R150). P4-07's work *was* on disk, uncommitted, and was finished and committed by
the orchestrator, including three claims in its document that the tree had moved under.

## What the delivery audit found, re-derived from the tree at `5a017d9`

An independent audit re-derived every row from the code and the test bodies, never from a commit
message or a report (R150). **It confirmed that every commit cited exists, is an ancestor of HEAD,
and touches what its row says; that no `def test_` line is removed against `56bf9e2`; and that both
ledgers are untouched, with `git log -p 56bf9e2..HEAD -- data/` empty.** It re-measured the suite at
**2013 collected** and re-derived the import closure independently (42 modules, writer modules
exactly `{walkforward, compare.sink}`, no `evaluators` module, and the pin verified to fail on four
planted spellings).

It also found eight things this board or its guards claimed and the tree did not support. Ranked by
how much each would mislead the next reader, and **none of them fixed in the pass that found them**
(R155 rule 4 — re-derivation and repair are separate passes):

| # | finding | state |
|---|---|---|
| 1 | **R163 was absent from this board, the design still said "by construction", and R158 Q2 had no cross-reference** — while R163 itself claimed all three already said "tested". That claim was false when written, by the orchestrator, in the act of ruling against exactly that failure | **FIXED** in the same pass as this table, because a false claim about where a correction lives is worse than no claim: R163 now carries a dated amendment, the design's option-A row says what was actually built, and R158 Q2 carries a pointer with the user's own wording untouched |
| 2 | **P4-04 was marked `todo` and P4-06c `in progress`, both committed**, and the wave table was two waves behind | **FIXED** above |
| 3 | **P4-04's acceptance text contradicted the check that shipped** — it demanded no `walkforward` writer import, which R163 makes impossible | **FIXED** in the row itself, with the original wording quoted so the change is visible |
| 4 | ~~**"21 of 21 mutants" covers `tests/test_daily.py` and `tests/test_daily_runner.py` only.**~~ The trigger guard, the registered-kind guard, the closure and pin tests, the rehearsal and the forecast stage have **no mutant at all** — while this board's Method promises a mutation for each derived guard | **CLOSED — P4-08c (`8a88d0f`): 26 mutants, all biting exactly their named tests.** The five new ones each target a guard that had none: a `launchctl bootstrap` planted in `src/volab/errors.py` (fails only the trigger scan), `PSEUDO_KIND = "registered"`, an `import volab.evaluators.sink` in `champion.py` (fails only the writer-module pin), a stage the rehearsal **really runs** appending to the **real** `data/trials.jsonl`, and a default restored to `Splits.holdout_end` — which is **P4-05b's owed witness, now seen red**. The script also stopped hard-coding its test files: it resolves each witness name to the file defining it and refuses to start if a name is defined in none or in two. And it now protects the ledgers from its own plants: it copies both aside before the first plant, restores them byte for byte after any mutant that moves them, and **refuses to continue if a ledger moves under a mutant not declared `moves_ledger`** |
| 5 | **The clock guard misses `p.stat().st_mtime`, `os.path.getmtime`, `os.times()` and `uuid.uuid1()`.** The audit ran each through the scan and got an empty set. `st_mtime` is named on this board as an evasion an engineer *refused*, and nothing stops it | **OPEN — P4-08.** A measured hole in the phase's central guard |
| 6 | **The registered-kind guard misses `"reg" + "istered"`, `list(_KINDS).pop()` and `"REGISTERED".lower()`** | **OPEN — P4-08** |
| 7 | **The forecast tripwire's `decisions.jsonl` comparison is vacuous**: the file does not exist, `_digest` returns `None` for a missing file, so it compares `None == None`. The tripwire also patches only `walkforward.append_trials` | **OPEN — P4-08.** A test that cannot fail is not a test (R159's not-empty rule, applied to a digest) |
| 8 | Minor: this board said the tools diff was "+42/-8" where `--numstat` gives 36/6; "`dt.date.max` appears exactly once" is loose (one `Splits(...)` construction, three occurrences in `test_config.py`); and **P4-05b's planted witness is still owed** — its pinning test was never seen red | **OPEN — P4-08** for the witness; the two figures are **FIXED** where they appear |

**The audit's own limits, stated because it is a report like any other (R150):** it did not run the
full suite (one was in flight), did not run the mutation probe, and did not independently verify
R156-R162 or the design beyond §2.2.

## What the review seat found, reading prose against execution at `5a017d9`

A seat whose first instruction was to read every docstring and board row against what the code
executes. It found **one defect the audit did not**, by probe rather than by reading, and it is the
most serious thing in the phase:

**A torn final line corrupts the next append, in both logs — and both logs claim it does not.**
`_append` (`runlog.py`) and the forecast writer open in `"a"` and write onto a fragment that has no
trailing newline. Reproduced: a forecast log ending `{"as_of": "2026-11-0` took an append that
**returned 1** while the file held one unparseable line and `_existing_keys` returned `set()`; in the
run log, torn bytes plus a start and a `done` completion gave `completed_dates() == frozenset()`.
So a retry of a partly-written day loses its first new line *and* hides that key from the duplicate
check — the day can be recorded `done` with a configuration silently missing — and a run log in that
state never reads as complete, so the whole pipeline re-runs forever. **Both existing tests passed
against the broken code**: one asserted a return count and never read the file back, the other tore
and read but never appended.

**FIXED in P4-08a (`23a5190`), red first.** Six new tests were run against the unfixed code and
each failed on the glue for the stated reason — `b'{"kind": "complete", "ref": "ab{"kind": "start"…'`
as one line, and the duplicate-key consequence reproduced as `('2026-11-04', 'SPY', 5) in set()`.
The fix is one shared writer, `runlog.append_text`, which opens `"ab+"`, reads the last byte, and
writes `b"\n" + text` in a **single** write when the file is non-empty and does not end in a
newline — so the fragment stays on its own line where `_lines` and `_existing_keys` already skip it,
nothing is truncated or rewritten, and a crash cannot land between the separator and the record. The
forecast writer now calls it instead of carrying a copy. Each test reads the bytes back and asserts
the prefix survives, the file ends in a newline with no blank line, the fragment is on its own line
and still skipped, every appended line parses, and the derived views agree. **What it still does not
guarantee, now said in the docstrings:** the torn line remains unparseable and the record it carried
is lost — the fix only stops it taking the next one with it; the tail check is not atomic, so two
writers racing on one file are not made safe (nothing here runs two); and `fsync` is a request to the
OS, not a promise about hardware. Pre-existing torn files are not repaired.

The rest, each either fixed here or carried:

| finding | state |
|---|---|
| **A torn final line corrupted the next append** (above) | **FIXED** (`23a5190`) |
| **The `root` injection and "a whole daily run" claim more than they cover.** `LIVE_STAGES` hands no stage a `--root`, so under `main(["run"], root=tmp)` the real forecast stage would read the repository's trial ledger and write the repository's `data/forecasts.jsonl`; `ingest` would write `data/raw` and the tracked payloads. The rehearsal substitutes a probe for `ingest` and `features` and hands the forecast shim an explicit `--root`, which its helper admits in a docstring while the module docstring and the test name do not | **OPEN — P4-08b.** The test is sound; the claim around it is not |
| **"No code path can write `registered`" is a literal lint, not a "cannot".** `"reg"+"istered"`, `f'regist{"ered"}'`, `"REGISTERED".lower()`, a generator over `_KINDS`, and `take_fingerprint(kind=var)` all pass the scan, and both `take_fingerprint` and `Fingerprint` accept any `kind` in `_KINDS` | **OPEN — P4-08b.** The fix is to make the claim true, not to widen the lint |
| **The clock guard misses more than its docstring admits**: `pd.Timestamp("now")`, `os.stat().st_mtime`, `uuid.uuid1()`, `os.times()`, `os.clock_gettime()`, `threading.Timer`, `importlib.import_module("time")` — and `runner.py` says "this module takes no clock" while arming a `Timer` | **OPEN — P4-08b** |
| **The holdout scan does not fold concatenation** (`"VOLAB_"+"HOLD"+"OUT_UNLOCK"` evades) **although the trigger scan in the same suite does** — one asymmetry, concretely found | **OPEN — P4-08b** |
| **A `runpy` stage spelling evades the ledger-stage guard**: `Stage("x", "runpy", ("volab.walkforward",))` reads clean and runs the writer | **OPEN — P4-08b** |
| **`definition_sources` hashes less than it claims.** Its docstring calls the covered files "everything a forecast is computed from", and it omits `panel.py`, `compare/cohorts.py` (where the champion's model object comes from), `sessions.py` and `forecast.py`'s own `MINIMUM_TRAINING` — so an edit to any of them changes a forecast without changing `definition_hash`, which defeats protection 4 rather than only its wording | **OPEN — P4-08b** |
| **A `setsid` descendant outlives the timeout.** Probed: a stage spawning `sleep 6` in its own session, with `timeout_seconds=0.4`, returned after 6.05s flagged `timed_out`. The runner's reasoning covers same-group grandchildren only. And an interrupt or `SIGTERM` to `run` abandons the stage entirely — neither claimed nor handled, and there is no run lock | **OPEN — P4-08b** |
| **The trigger document misdescribed the tree**: "one verb today", `run` "being built", and a claim that every real date is in the **holdout** region when `holdout_end` 2026-10-30 puts today in **`live`** | **FIXED.** That region error was the orchestrator's own, introduced while correcting a different staleness in the same document |
| **The refusal says "the holdout is sealed" for a `live` date too** | **OPEN — P4-08b.** A reporting defect on the run's own output |
| Vacuous or mis-named tests: `test_a_comment_naming_a_writer_entry_point_does_not_fire` cannot fail under any AST scan; `..._abandoned_mid_stage` patches `run_stages` to raise so no stage is ever in flight; `..._past_dev_end_...writes_nothing` passes with the `min()` bound removed; three tests assert the repository's forecast log is absent and will turn red the day the job legitimately writes it | **OPEN — P4-08b** |
| `tools/README.md` said "thirteen planted defects" and that the probe runs "the whole of `tests/test_daily.py`" | **FIXED** (`8a88d0f`) |
| **An eleventh guard hole, found by the probe story itself**: `"".join(("launch", "ctl"))` evades the trigger scan, whose folding handles `+` but not a `str.join` of literals. It was found because the script's own plant text tripped the guard until spelled that way | **OPEN — P4-08b1** |

**One honesty note the probe story insisted on, and it is the right instinct.** The witness lists
are **descriptive, not predictive**: they record what each plant was *measured* to break, and an
earlier commit widened six of them to match what the probe observed, which makes "fails exactly its
named tests" true partly by construction of the list. The script's docstring now says so, and says
what the figure is actually worth — **every plant is noticed by something that names it** — rather
than claiming one-to-one precision. The figure also covers only the six test files the witnesses
name, which it now derives rather than hard-codes.

**The probe's own caveat: `8a88d0f`'s 26-of-26 was measured in a `git archive` copy at `0c807b8`,
not in this tree**, because its older mutants plant into `runlog.py` and P4-08a was editing that
file — a restore would have overwritten live work (R131). The orchestrator re-runs it in place once
no engineer is editing `src/`, and the in-place figure is the one that counts.

**The seat's own limits, stated (R150):** it ran no full suite and no probe, and **no test or command
in this phase has ever run `forecast.main` against the real store** — every end-to-end test fakes
`load_curated_panel`, `load_targets` and `load_settings`. One engineer reported exercising the real
path once by hand, outside the suite, against a scratch root.

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
