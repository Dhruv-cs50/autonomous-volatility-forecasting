# Phase 5 — the agent layer — implementation plan

Design: `docs/superpowers/specs/2026-11-10-phase5-agent-layer-design.md`. Decisions:
`docs/superpowers/phase5-decisions.md` (R181 onward). Board: `docs/phase5-sprint-board.md`.

Branch `phase-5-agent-layer` in the worktree
`/Users/dhruvshah/dev/Autonomous Volotility Forcasting/vol-lab-phase5`, cut from `main` at
`ee16c3d`. Baseline measured there: **1856 tests pass**, ruff clean, mypy clean over 45 source
files.

## Method

Test-first, every story. Write the failing test, run it, confirm it fails *for the expected
reason* — a test that fails on an import error has not tested anything yet — then the smallest
implementation, then the full suite, then the gates, then a pathspec commit. A review seat reads
every story's diff, and its **first** instruction is to check each docstring against what the test
body actually executes: Phase 3 found eleven separate instances of a test or docstring claiming
more than its code performed, every one caught by a review seat and never by the author.

Guards are **derived, never enumerated** (R151), and each ships with a mutation the enumerated
version would have passed.

Commits use an explicit pathspec — `git commit -m "…" -- path/one.py path/two.py` — because several
sessions share this checkout and a bare `git add` sweeps up another agent's half-finished work. New
files need `git add -N` first, because a pathspec commit refuses a path git has never seen.

## Sizing, and the two hard rules

A story is **one dispatch of new work**, small enough for one specialist to deliver in one go; a
scoped fix round after review is normal and does not mean the story was oversized. A sprint is one
coherent group that closes before the next opens. **No two stories in a wave touch the same file** —
the file sets below are stated so that can be checked rather than hoped.

## Sprint 0 — scaffold (orchestrator, before any dispatch)

Files two stories need are created by the orchestrator, not by either story: `.gitignore`
negations, the board, the decisions log, and docstring-only `__init__.py` stubs for `agent/`,
`candidate/` and `promote/` — the last so that Sprint A's fence is not vacuous over an empty
directory.

## Sprint A — the fences, before any code of the phase exists

R140's precedent: the mechanical fence is story one, because it is the cheapest story in the phase
and the only one protecting something that cannot be undone.

| ID | Story | Files | Depends on |
|---|---|---|---|
| P5-A1 | The derived holdout-key scan, as a reusable test helper | `tests/holdout_scan.py`, `tests/test_holdout_scan.py` | — |
| P5-A2 | The agent packages reach no read path and name no unlock key | `tests/test_agent_discipline.py`, `src/volab/agent/_plant.py` (red witness, deleted by the story) | A1 |

**P5-A1.** A helper deriving both unlock keys: the environment variable from
`volab.config.HOLDOUT_UNLOCK_ENV`, the parameter name from `store.py`'s `def load_panel` **by AST**
(not `inspect.signature` — conftest's police replace the object for the whole session, R153).
Identifiers matched loosely, strings strictly, docstrings exempt so prose about the ban is not read
as the ban. Written fresh rather than extracted from `tests/test_daily.py`, which PR #18 is adding
542 lines to (R187). Acceptance: red cases proving it catches `allow_holdout=` as a keyword, as a
bare name, as an attribute, and the environment variable as a string; and a green case proving a
docstring mentioning either is not a hit.

**P5-A2.** Three scans over `src/volab/agent/`, `src/volab/candidate/`, `src/volab/promote/`:
reaching `volab.store` **transitively** (built on `test_import_discipline.py`'s `_graph`,
`_packages`, `_first_party_imports`, with a `_store_reach` walk modelled on `_numeric_reach`);
naming either unlock key (via A1); and carrying `@pytest.mark.holdout` in any of the phase's test
modules, derived from `pyproject.toml`'s `markers`. Plus a guard that no `LIVE_STAGES` entry runs
any of the three packages — asserted in **this** file, importing `LIVE_STAGES` read-only, because
editing `tests/test_daily.py` would collide with PR #18.

The transitive fence's red witness is the realistic mistake, not a synthetic one: a planted
`src/volab/agent/_plant.py` holding `from volab.features.targets import HORIZONS`, which a per-file
scan passes and which pulls `volab.store`, duckdb and pyarrow in two hops (M1). The story asserts
the chain is printed, then deletes the plant.

**The fence is on the import graph, never on `sys.modules`** — pandas 3.0 imports pyarrow
unconditionally (M8), so pyarrow's presence there proves nothing.

## Sprint B — the vocabulary and the contestant

| ID | Story | Files | Depends on |
|---|---|---|---|
| P5-B1 | `HarX`: HAR plus a declared subset of the vocabulary | `src/volab/models/harx.py`, `tests/test_harx.py` | ∥ B2 |
| P5-B2 | The closed vocabulary, `Proposal`, `validate`, `gate`, the canary payloads | `src/volab/agent/vocabulary.py`, `src/volab/errors.py`, `tests/test_agent_vocabulary.py` | ∥ B1 |

**P5-B1.** Modelled on `src/volab/models/iv.py`; passes `tests/model_conformance.py`; reaches
`fit_ols` through the module attribute as `recalibrated.py` and `iv.py` do; imports no numpy, scipy
or arch. Named `harx+{'+'.join(extra)}` — **not** `harx[...]`, because `[` and `]` are glob
metacharacters that `validate_segment` rejects (R193). Raises `ModelSkip` and **not**
`SchemaError` on a null extra column at the origin, on too few training rows carrying it, and on a
design singular only because of it: `iv.py:30-40` states why — a `SchemaError` deletes the origin
from every contestant in the cohort and files a model problem as a data problem.

**P5-B2.** `VOCABULARY` is `FEATURE_COLUMNS` (imported directly, store-free per M1) minus the three
exact HAR transforms. `HORIZONS` is a literal with a two-sided test against
`volab.features.targets.HORIZONS`. `validate` refuses **unknown keys** rather than a forbidden list,
one branch per `RefusalCode`. `ProposalRefused` joins `errors.py` as a `VolabError` and nothing
else (R30). Also owns `CANARY_PAYLOADS`, so Sprint D's canary story writes only a test file and no
two stories edit `vocabulary.py`.

Acceptance includes the two derived witnesses: `VOCABULARY` is disjoint from
`{target_column(h) for h in HORIZONS}`; and `fit_ols` over `[*HAR_COLUMNS, column]` refuses each
excluded name and accepts every admitted one. Ship the second over a **synthetic** build as well as
the real panel, so it is a witness rather than a skip wherever the real store is absent.

## Sprint C — the child, the sprint log, and the counts

| ID | Story | Files | Depends on |
|---|---|---|---|
| P5-C1 | The evaluation child: one proposal, one fresh interpreter, two agent ledgers | `src/volab/candidate/__init__.py`, `src/volab/candidate/__main__.py`, `tests/test_candidate_cli.py` | B1, B2 ∥ C2 |
| P5-C2 | The sprint event log and the pending approval | `src/volab/agent/sprintlog.py`, `tests/test_agent_sprintlog.py` | B2 ∥ C1 |
| P5-C3 | `LedgerCounts`, the read-only ledger reader, and the leaderboard's added line | `src/volab/counts.py`, `src/volab/agent/ledger.py`, `src/volab/leaderboard/__init__.py`, `tests/test_counts.py`, `tests/test_leaderboard.py` | A2, C1 |

**P5-C1.** Writes `data/agent_trials.jsonl` and `data/agent_comparisons.jsonl` through
`run_configuration`'s existing `append` seam (R97) — nothing in `walkforward` changes. Re-validates
the proposal file it is handed, because the gate's verdict does not travel. Refuses any cohort name
outside the reserved pattern, parametrised over the cohort registry so a later cohort is covered the
day it is added. Runs `coverage_refusal` **before** fitting, and records a refusal as an event with
the reason verbatim and no trial line (M3's `iv_h1` case is live). **Measure and report one child
run's wall time** — Sprint E needs it.

**P5-C2.** Mirrors `src/volab/daily/runlog.py` rather than reusing it: `record_start` takes a
`volab.daily.Plan` and `completed_dates` is keyed on a date, which a sprint is not. Three design
properties are copied verbatim because they are right: write-then-fsync, two lines joined by id
rather than one mutated line, and a torn final line **skipped rather than raised on**.

**P5-C3.** The leaderboard change is **additive** — a new line beneath the existing headline, with
no edit to the headline's own text — because PR #18 is adding 20 lines to that module and 38 to its
test. Read `tests/test_leaderboard.py`'s pinned assertions **before** touching anything: the change
must be priced, not discovered. `AGENT_TRIAL_LEDGER` is a literal in `agent/ledger.py` with a
two-sided test against the child's constant, because importing it from `volab.candidate` would
cross the Sprint A fence. The reader drops any `trials.jsonl` line with `last_origin > dev_end` and
**reports the count dropped**, since a silent drop understates the trial count.

`counts.py` counts the agent ledgers from path literals and must **not** import `volab.candidate` —
that module resolves reference models and would drag `arch` into the leaderboard, which
`leaderboard/__init__.py:39-42` was deliberately engineered to avoid. Check that module's
import-cost witness before writing.

## Sprint D — the ports and the roles

| ID | Story | Files | Depends on |
|---|---|---|---|
| P5-D1 | The `Completer` port, the urllib edge client, the fake | `src/volab/agent/_llm.py`, `tests/test_agent_llm.py` | ∥ D2, D3 |
| P5-D2 | The `Checkpointer` port and its two credential-free implementations | `src/volab/agent/memory.py`, `tests/test_agent_memory.py` | ∥ D1, D3 |
| P5-D3 | Subprocess-per-evaluation | `src/volab/agent/_runner.py`, `tests/test_agent_runner.py` | C1 ∥ D1, D2 |
| P5-D4 | The six roles, the seven-step machine, and the sprint | `src/volab/agent/roles.py`, `src/volab/agent/__init__.py`, `tests/test_agent_sprint.py` | D1, D2, D3, C2 |
| P5-D5 | The canary bites, and an accepted canary voids the sprint | `tests/test_agent_canary.py` | D4 |

**P5-D1.** `Completer = Callable[[Prompt], Completion]`, a `Callable` alias rather than a Protocol
for the reason `_http.py` chose one. `default_completer` is the **one** function that touches the
wire and **no test uses it**; `completer_from_environment` is the only credential read and is called
from `__main__` alone, never at import. Token counts are `int | None`, where `None` means the
provider did not report them and never zero.

**P5-D2.** A domain-level port — `put` / `get` / `history` / `sprints` — not a DB-API connection, so
a sqlite3 test is evidence about the port rather than about sqlite3. Both `FileCheckpointer` and
`SqliteCheckpointer` ship, exercised by one conformance test parametrised over both, in the idiom of
`tests/model_conformance.py`. No Postgres implementation is written (R190).

**P5-D3.** Duplicates `daily/runner.py:73-104`'s bounded chunked-tail loop rather than extracting it,
because PR #18 is adding 282 lines to that file (R195). The two `OUTPUT_BOUND_BYTES` constants are
coupled by a test asserting equality, and each file names the other in a comment. *Price:* two
copies of a 25-line loop whose logic is not test-coupled, so a bug fixed in one must be fixed in the
other by hand. Extraction to `src/volab/_subprocess.py` is a follow-up once `daily/` is quiet.

**P5-D4.** Everything reaching the disk, the clock, the network or a subprocess is **passed in** —
the shape `run_configuration` already uses — so a sprint is exercisable end to end from fakes with
no key and no network. The canary is injected here, at the parse boundary: the Configurator's raw
payload is *replaced* before `gate()` sees it. The Critic's verdict goes to the log and **gates
nothing** (design, *What is not structural*, item 5).

**P5-D5.** Two tests. Every payload refused, with floors on both the payload count and the number of
**distinct refusal codes** — the distinct-code floor is the one that matters, because echoed names
make the refusal *strings* differ and a strings-only floor would pass while testing one branch six
times. And the mutation: a sprint run with a stub gate that accepts everything must exit non-zero,
record `canary_accepted`, and leave no pending approval.

## Sprint E — the human gate, the CLI, and the exit criterion

| ID | Story | Files | Depends on |
|---|---|---|---|
| P5-E1 | `python -m volab.promote`, and its three refusals | `src/volab/promote/__init__.py`, `src/volab/promote/__main__.py`, `tests/test_promote.py` | C2, D4 ∥ E2 |
| P5-E2 | `python -m volab.agent`: the `sprint` and `show` verbs | `src/volab/agent/__main__.py`, `tests/test_agent_cli.py` | D4 ∥ E1 |
| P5-E3 | The exit criterion: a full sprint end to end, witnessed | `tests/test_agent_rehearsal.py`, `tests/isolated_candidate.py` | E1, E2 |

**P5-E1.** A separate package so the separation is an import-graph fact, asserted by
`test_the_agent_package_cannot_reach_the_promote_package` with a planted reverse import as its red
witness. Three refusals, each its own test: no `awaiting_approval` event for that id; a recomputed
fingerprint differing from the stored one, naming both; a `promoted` event already present. No
`--latest`, no `--all`. Promotion writes a `promoted` event and **nothing else** (R188).

**P5-E2.** `sprint` constructs the real completer at the edge; `show` prints a sprint's events and
the ledger counts. No verb here promotes.

**P5-E3.** The exit criterion, with the witness that actually witnesses it. **"No new line in
`data/holdout_access.jsonl`" is not evidence** — conftest's police do not exist in subprocesses, and
the boundary check precedes the `try:` that opens the audit scope, so a *refused* read writes no
line at all and an absent line is consistent with both "no attempt" and "a refused attempt". The
sprint never calls `load_panel` anyway (Sprint A's fence); every read is in the `volab.candidate`
grandchild. So `submit` is handed a launcher for `tests/isolated_candidate.py`, a shim in the idiom
of `tests/isolated_build.py` that imports `volab.candidate`, applies conftest's identity sweep over
`sys.modules`, calls the child's `main`, and logs every observed `(end, allow_holdout)` to a file
this test asserts on: **every call carried `end == dev_end` and `allow_holdout is False`**.

Runs against a **synthetic store** the test builds, with the origin window shrunk inside the shim
and never by a production knob (R191). `_clean_volab_environment` strips `VOLAB_*`, so the child
must be handed its storage root explicitly or it writes to the real `data/`.

## Sprint F — close out

| ID | Story |
|---|---|
| P5-F1 | `delivery-auditor` checks the board against git, and the board is corrected where they disagree |
| P5-F2 | README's status table, the decisions log, the board's close, and the PR |

Nothing is done on assertion. A story closes when a commit exists, its tests pass, and the gates are
green.
