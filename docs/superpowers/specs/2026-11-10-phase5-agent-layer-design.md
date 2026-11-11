# Phase 5 — the agent layer — design

**Status: draft, 2026-11-10.** Written against `main` at `ee16c3d`, on branch
`phase-5-agent-layer`. Phases 0-4 are merged.

The framework's Phase 5 row reads: *"LangGraph roles, typed tools, Postgres checkpointer, trials
table, canary feature, `interrupt` before promotion"*, with the exit criterion *"one full weekly
sprint runs end to end without touching the holdout"*
(`reference/Autonomous volatility forecasting framework.md:127`).

---

## Intent, and the one sentence the whole design turns on

This project exists because of a negative result. A published agentic framework had an LLM
reviewer **approve a feature built from future intraday bars**, and a deliberately leaky strategy
with a Sharpe of 35 **passed both Deflated Sharpe and PBO** (`README.md:13-16`, citing
[arXiv 2608.12841](https://arxiv.org/pdf/2608.12841) and
[arXiv 2608.27734](https://arxiv.org/pdf/2608.27734)). If a reviewer and a battery of corrections
can both wave a leak through, refusal has to live where it can be mechanical.

Every phase so far has put refusal in the data layer. **Phase 5 is the first phase that adds the
thing those defences were built against**: a component whose output is not determined by its
input, and which is therefore not auditable by reading it. So the governing sentence of this
design is:

> **A prompt that says "do not look at the future" is exactly the kind of defence this project
> exists to reject.** Where a refusal can be made structural it is made structural. Where it
> cannot, this document says so plainly rather than papering it with prompt text.

"Structural" here has a specific, checkable meaning: **the illegal request is not expressible**.
Not forbidden, not validated against, not caught — absent from the vocabulary in which the agent
can speak at all. A validator that rejects a bad date is a defence; a tool surface with no date
parameter is a structure. Both appear below, and the document is explicit about which is which,
because the first kind can be edited away in a one-line diff with a green suite and the second
cannot.

## Goals

- `src/volab/agent/` exists: a typed tool surface, hub-and-spoke roles, a sprint that runs end to
  end, a guardrail gate, a canary that proves the gate bites, and a human gate before promotion.
- **The agent's reachable request space contains no peek.** Established by construction and
  asserted by tests, not by review of its prompts.
- A full sprint runs today, in CI, with **no credential of any kind**, against a scripted fake
  LLM — because that is the framework's own exit criterion and it does not mention a live model.
- Every number the layer prints names the thing that printed it (R32).
- Whatever remains genuinely blocked on a credential is a short, named list, each item saying
  what running it would prove.

## Non-goals

- **Taking the holdout reveal.** Phase 6's, and the user's trigger. Nothing here reads past
  `dev_end`; nothing here names `allow_holdout` or `VOLAB_HOLDOUT_UNLOCK`.
- **Beating HAR.** Unchanged from `README.md:22-25`: the claimed contribution is the harness. An
  agent sprint whose candidate loses to `har_levels` is a successful sprint.
- **An agent that writes feature code.** See *What is not structural* — this is a deliberate
  scope boundary and the reason for it is a property of the defences, not an estimate of effort.
- **A dashboard, a web page, or any UI.** Asked and declined in Phase 4
  (`docs/phase4-sprint-board.md:145`); unchanged.
- **MLflow.** R84's refusal stands and its reason has not changed.
- **Moving `dev_end`**, or adding any flag, environment variable or argument that relaxes a guard.

## Anti-goals — what would make this a failure even if it ran

Named in the Phase 4 and holdout-reveal style, because these are the outcomes that look like
success:

- **A refusal that lives in a prompt.** If the only thing standing between the agent and a
  forward-looking feature is a sentence of instruction, this phase has reproduced the published
  failure it cites on its own front page.
- **Agent trials that nobody counts.** A side ledger that no reported surface mentions is the
  forking-paths hole the trial ledger exists to close, moved one directory sideways. An agent that
  tried forty configurations and reported the best is the thing this repository refuses, whether
  or not the forty are written down somewhere.
- **A gate that has never been shown to refuse anything.** A guardrail whose only evidence is
  that nothing bad has happened yet is not evidence.
- **A promotion that happens in the process that proposed it.** "Human in the loop" implemented as
  a function the agent's own run can call is not a human gate.
- **A sprint that cannot run without a key.** That would make every claim in this phase a promise
  about untested code, and would leave the credential boundary exactly where it was.

## The credential boundary

Phase 5 needs **Postgres and LLM API keys that only the user can provision**
(`docs/phase2-completion-sprint-board.md:229`, carried again at
`docs/superpowers/specs/2026-11-03-phase4-unattended-daily-runs-design.md:168`). This design does
not ask for them, does not provision them, and puts no key in the repository.

The keys gate **live runs only**. The mechanism is the one Phase 1 already uses for HTTP: a port at
the boundary, a fake behind it in tests, the real client constructed at the edge from the
environment. `src/volab/ingest/_http.py` is the precedent — `Transport = Callable[[str], bytes]`,
with `default_transport` the one implementation that touches the wire and **no test using it**.
That module's own docstring states the property this design copies: *"the whole ingestion layer is
exercisable from committed fixtures with no network access."*

So: everything buildable without a key is built, tested and merged — the tool surface, the
contracts, the refusals, the ratchets, the sprint, the docs. What a key would add is stated at the
end of this document and nowhere else.

---

## The measurements this design rests on

Taken on this tree before the design was written, because an unmeasured design decision is how
invariants get traded away for nothing. Each was re-run by the orchestrator where it drives a
code literal.

| # | Measurement | How |
|---|---|---|
| M1 | `volab.features.targets` reaches `volab.store` **transitively** (`from volab.store import write_features`). `volab.features.build` and `volab.models.base` do **not** — `build.py`'s only mention of the store is a comment at line 80. | the `_graph` / `_first_party_imports` walk from `tests/test_import_discipline.py`, re-checked by AST |
| M2 | `har_d_proxy` **is** `rv_proxy_d1`: max abs difference **0.0**. `har_w_proxy*5` vs `rv_proxy_d5` is 3.5e-18; `har_m_proxy*22` vs `rv_proxy_d22` is 6.9e-18. | `load_panel('har_daily', end=dev_end, tier=FEATURES)`, 2,312 rows, SPY |
| M3 | `iv_h1` / `iv_var_h1` are non-null in 912 of 2,312 rows, first **2022-05-13** (VIX1D's own start) | same read |
| M4 | `fit_ols` at 2,000 rows: k=4 → 9.3 ms, k=8 → 16.2 ms, k=12 → 27.5 ms, k=16 → 41.3 ms per fit | timed |
| M5 | LangGraph **does** resolve against this lock and imports clean under `-W error`; it adds 39 packages (39 → 78) | `uv lock` + `uv sync --locked` in a scratchpad copy, never this worktree |
| M6 | An optional extra **does** enter `uv.lock` and is **not** installed by `uv sync --locked` | scratchpad probe |
| M7 | `data/comparisons.jsonl` records carry **no origin-date field**; `data/trials.jsonl` lines carry both `first_origin` and `last_origin` | `json.loads` of line 1 of each |
| M8 | `pandas` 3.0 pulls `pyarrow` into `sys.modules` on its own, so pyarrow's presence there is **not** evidence of a store read | `python -c "import pandas; 'pyarrow' in sys.modules"` |
| M9 | `count_trials` already has callers in four modules: `walkforward/__main__.py`, `leaderboard/__init__.py`, `compare/__main__.py`, `evaluators/__main__.py` | `grep -rn count_trials src/` |
| M10 | `.gitignore` carries exactly three `!data/*.jsonl` negations: `holdout_access.jsonl`, `comparisons.jsonl`, `trials.jsonl` | `grep -oE` |
| M11 | `FEATURE_COLUMNS` holds 33 names and its intersection with `{target_column(h) for h in HORIZONS}` is **empty** | direct import |
| M12 | Baseline on `ee16c3d` with the store populated: **1856 tests pass**, ruff clean, 107 files formatted, mypy clean over 45 source files | `uv run --locked pytest -q` |

**M12 corrects a figure that was in this phase's brief.** The suite is 1856 tests on `main`, not
"~2,150". The larger number is the tree with PR #18's ~300 Phase 4 tests, which are not merged.
Nothing in this document quotes 2,150.

## Dependencies: none added, and *not* for R84's reason

**Ruling: no LangGraph, no `psycopg`, no new lock entry.** The state machine, the LLM adapter over
stdlib `urllib`, and the checkpointer port are ours.

**R84 cannot be cited here and this document says so rather than borrowing it.** MLflow was refused
because it *would not resolve* — `mlflow>=3` requires `pyarrow<26` against this project's
`pyarrow>=26` (`docs/superpowers/phase2-completion-decisions.md:114`). LangGraph resolves and
imports clean under `-W error` (M5). Anyone citing R84 for LangGraph is overclaiming, which is the
defect this repository treats as unforgivable.

Three grounds that do hold:

1. **LangGraph's `interrupt` is a mechanism where the human gate needs a prohibition.** `interrupt`
   pauses a graph and `Command(resume=…)` resumes it — in the same process, if the caller wishes.
   This design requires that promotion be *impossible* in the proposing process. Adopting a
   39-package framework for its human-gate feature and then declining to use that feature is the
   wrong trade. Stated honestly: the process separation ruled below **could** be built on top of
   LangGraph. The ground is "39 packages for the one feature we would not use", not "LangGraph
   makes it impossible".
2. **39 → 78 packages (M5) for a seven-step linear machine with one branch and one pause.** This
   project already engineered `volab.leaderboard` not to import `volab.compare` because `arch`
   costs three seconds (`src/volab/leaderboard/__init__.py:39-42`).
3. **`filterwarnings = ["error"]` is an ongoing liability rather than an import-time fact.** Clean
   today. Every future `uv lock` moves 39 packages, any of which may emit a `DeprecationWarning`
   at *call* time and fail the whole suite. **A liability, not a measured failure** — it does not
   bite today.

**`psycopg` stays out of the lock entirely.** M6 settles it: an extra changes `uv.lock` and gets
zero mypy or test coverage, which is the worst of both. When a keyed story needs it, it arrives as
`[project.optional-dependencies] postgres`, a lazy import inside the edge function, and a mypy
override shaped like the existing `pyarrow` one — and that story runs the suite with the extra
installed.

*Price if wrong:* roughly 200 hand-rolled lines we own and maintain. Worth revisiting if cycles,
retry-with-backoff or parallel branches ever enter scope, because those are LangGraph's real value
and none of them is in Phase 5.

## The agent's trials are counted, in a ledger of their own

This is the decision that matters most, and the reasoning matters more than the answer.

The reading that *appending to `data/trials.jsonl` under a distinct cohort is the honest option* has
real support. The pre-registration gives its own permission — §4: *"The reveal takes a second count
and records both plus the delta … since further work may legitimately add trials between
registration and the read"* — and the reveal design is emphatic that the protection's purpose is
that the number is **known and reported, not frozen**. The attribution machinery exists already:
the leaderboard partitions by cohort and a test asserts the breakdown sums to the headline.

**What overrides it is that R139 is a *user* decision.** `docs/superpowers/phase3-decisions.md:188`
— *"**Ruling (user decision, 2026-10-29):** … no Phase 3 walk-forward writes a ledger line until
the user has triggered the holdout reveal"* — inherited unchanged by Phase 4. Routing agent trials
into `trials.jsonl` would overturn a user decision, and that is not a builder's to take. A separate
ledger is the only option that needs no new decision from the user.

**The ruling:**

- **`data/agent_trials.jsonl`, tracked.** Tracked requires a `!data/agent_trials.jsonl` negation in
  `.gitignore`, because `data/**` is ignored with an allowlist (M10) — without it the file is not a
  reported number and `git clean -xdf` erases it.
- **Schema byte-identical to `Trial.record()`** plus the `at` / `git_sha` / `git_dirty` prefix
  `append_trials` adds. Deliberately not a new schema: if the user later rules that the ledgers
  should be one, the migration is `cat`.
- **`data/agent_comparisons.jsonl`, tracked**, holding the MCS result a promotion rests on. Its
  schema **adds `first_origin` and `last_origin`**, closing M7's gap for our own file.
- **`data/agent_sprints.jsonl`, gitignored** — the sprint event log (hypotheses, proposals, gate
  verdicts, canaries, approvals). No count from it is a reported number, exactly as
  `data/daily_runs.jsonl` is gitignored by R158 Q3. Each evaluation event carries the
  `(cohort, at, git_sha)` key by which its trial lines are found, so the two **join** rather than
  the agent keeping a shadow count.
- **One writer:** `src/volab/candidate/`, through the `append: Callable[[Sequence[Trial]], int]`
  seam `run_configuration` already takes (`src/volab/walkforward/__init__.py:1251`, R97). Nothing in
  `walkforward` changes. **The agent's runs cannot reach the real ledger because they are handed a
  different `append`** — a structural fact, not a rule.
- **The headline prints both.** `src/volab/counts.py` holds `LedgerCounts(trials, agent_trials,
  agent_comparisons, total)` and `count_all(root)`, and the leaderboard prints the agent counts
  **as an added line beneath its existing headline**, leaving the headline's own text untouched.

**What this design does NOT claim.** It does not claim that a surface physically cannot print one
count without the other. That enforcement — an import ban leaving `count_trials` reachable only
from its definition and `counts.py` — **fails on day one**, because four modules already call it
(M9), and making it true means migrating three writer CLIs. One of those,
`src/volab/evaluators/__main__.py`, is being edited right now on PR #19. **Deferred, as a
decision** (R186): the honest claim narrows to *the leaderboard headline prints both counts*, and
the migration plus the ban is a follow-up once PR #18 and PR #19 land.

*Price if wrong:* more than one number where the argument wants one. Nothing stops a reader quoting
only the first. If the user later rules the ledgers should be one file, the migration is a
concatenation, because the schemas are identical by construction.

**A binding Phase 6 requirement, recorded because it cannot be tested now.** The reveal's second
count must count `agent_trials.jsonl` and `agent_comparisons.jsonl` too. The reveal is unbuilt, so
there is nothing to assert against. This is a recorded requirement, not a guard, and the PR says so
in those words.

## The tool surface: a peek is unrepresentable

### No date, anywhere

**Sharpened past "no `end`": no date-typed field at all, and no `every`.** Choosing the window or
the stride *is* selection. The harness fixes all of it in `volab.candidate`, mirroring the existing
CLI defaults: `first = 2021-01-04`, `last = settings.splits.dev_end`, `every = 5` (R94's stride),
`minimum_training = 250`.

The witness is derived rather than asserted on a list:
`test_the_candidate_cli_has_no_date_typed_option` walks `parser._actions` and asserts no action's
`type` is `dt.date.fromisoformat`. The child reaches the panel only through
`load_for`, whose docstring already states: *"There is no parameter here that could ask for a later
`end`."*

### The agent package reaches no read path, and not transitively either

**Ruling: `src/volab/agent/` imports nothing that reaches `volab.store`.** Every tool in the
framework's role table reads either a ledger (JSONL) or submits a run; **none needs a panel
figure** (`reference/…framework.md:78-84`). So the stronger option is nearly free, which is why it
is taken: *"the agent cannot peek"* becomes an import-graph fact an AST test asserts, rather than a
convention a reviewer checks.

**M1 is the trap, and it is the mistake an implementer would actually make.**
`from volab.features.targets import HORIZONS` pulls `volab.store`, duckdb and pyarrow into the agent
process two hops deep — and a per-file scan passes it. This is R129's hole in the numeric scan, one
subsystem over. So the fence is **transitive**:
`tests/test_agent_discipline.py::test_no_agent_module_reaches_a_read_path_even_indirectly`, built on
the existing `_graph` / `_packages` / `_first_party_imports` helpers with a `_store_reach` walk
modelled on `_numeric_reach`. Its red witness is a planted
`src/volab/agent/_plant.py` containing exactly that import, caught with the chain printed.

**For the fence's author (M8):** the fence is on `volab.store` **by import graph**, never on
`sys.modules`. pandas 3.0 imports pyarrow unconditionally, so pyarrow in `sys.modules` proves
nothing.

The agent **may** import, all verified store-free: `volab.config`, `volab.errors`,
`volab.sessions`, `volab.paths`, `volab.features.build`, `volab.models.base`.

### The closed vocabulary, and why it is a positive allowlist

`FEATURE_COLUMNS` is imported directly from `volab.features.build` — store-free (M1), so no drift
is possible. `HORIZONS` cannot be imported, because `volab.features.targets` reaches the store, so
`src/volab/agent/vocabulary.py` carries a four-integer literal with a **two-sided test** asserting
equality against `volab.features.targets.HORIZONS`, shaped exactly like
`tests/test_preregistration_fence.py`. (Tests may import anything.)

**A positive allowlist, not a subtraction of target columns.** The brief asked for the forward-RV
target columns to be explicitly subtracted. They are not in `FEATURE_COLUMNS` to begin with (M11),
and an allowlist is strictly stronger: it rejects `target_rv_proxy_h5` **and** every name nobody
thought of, where a denylist rejects only what is on it. What makes the exclusion *known* rather
than assumed is the derived test `test_the_vocabulary_names_no_target_column`, which asserts
disjointness against the target set computed from `volab.features.targets` — so if
`FEATURE_COLUMNS` ever admitted a forward quantity, it fails.

The agent never names a **response** either. The response is `target_column(horizon)`, chosen by the
child. There is no field for it.

**Three columns are subtracted, by measurement.** M2: `har_d_proxy` *is* `rv_proxy_d1`, and the
week and month HAR components are `rv_proxy_d5 / 5` and `rv_proxy_d22 / 22` to within float
epsilon — which `src/volab/features/har.py` makes true by construction rather than by coincidence.
Since the HAR-X base always carries the three `har_*_proxy` regressors, a proposal naming any of
those three is an **exactly singular** design. They are excluded, and the witness is derived rather
than enumerated: `fit_ols` is run over `[*HAR_COLUMNS, column]` for every column in the vocabulary
asserting no refusal, and over each excluded name asserting it **does** refuse.

`MAX_EXTRA_REGRESSORS = 5` is **priced from M4, not chosen**: 3 HAR + 5 extra + an intercept is 9
parameters, about 16-20 ms per fit, roughly 4-5 s per `(symbol, horizon)` over 251 origins. The cap
exists because the sealed path may not import numpy (R82), so `fit_ols` is pure-Python normal
equations; k=16 is 41 ms per fit for regressors a 2,312-row dev window cannot support anyway.

### The proposal, and the load-bearing detail

A `Proposal` is a frozen dataclass whose every field is a selection from a finite set the harness
owns: `family`, `extra_regressors` (1 to 5, unique, from the vocabulary), `symbol`, `horizon`,
`reference`. There is no field in which a forward-looking quantity, a window, or a line of code can
be expressed.

**`validate` refuses *unknown* keys rather than consulting a forbidden list, and that is the
load-bearing detail.** `end`, `start`, `every`, `code` and `cohort` are refused *by being unknown*
— so `end` is refused today, and so is a key invented in 2027. A denylist of
`{"end", "start", "allow_holdout"}` is a list that cannot report its own incompleteness, which is
R151's exact failure mode. Every branch carries a `RefusalCode`, so a refusal is classifiable
rather than a sentence; the canary table below depends on that.

`ProposalRefused` joins `src/volab/errors.py` as a `VolabError` **and nothing else** — deliberately
not also a `ValueError` — per the taxonomy rule R30 records.

### What the agent may read, and the channel M7 closes

| Tool | Reads |
|---|---|
| `list_models()`, `list_vocabulary()` | module constants; no I/O |
| `read_trials()` | `data/trials.jsonl`, **filtered** |
| `read_agent_trials()` | `data/agent_trials.jsonl` |
| `read_memory(sprint_id)` | the `Checkpointer` |
| `submit(proposal)` | nothing; spawns the child |
| `write_report(text)` | appends an event |

**M7 is a leak channel, and it closes the way the evidence allows rather than the way one would
prefer.** Phase 6 will write holdout scores. For `trials.jsonl` a filter works, because every line
carries `last_origin` (M7): the single reader drops any line with `last_origin > dev_end` **and
reports how many it dropped**, since a silent drop understates the trial count and would be its own
overclaim.

**For `comparisons.jsonl` the filter cannot work, so the agent does not read that file at all.** Its
records carry `origins` and `stride` but no origin dates (M7, re-verified here), so a post-reveal
holdout comparison would be indistinguishable from a dev one and there is nothing to filter on.
Failing closed, `read_comparisons` is not written; the framework's `get_mcs_result` is served from
the comparison the **child** computed for the agent's own cohort, which by construction covers only
origins at or before `dev_end`. **A second binding Phase 6 requirement:** if the reveal writes
holdout comparisons to `data/comparisons.jsonl`, that record must gain a `last_origin` *before* any
agent reads the file.

## Cohort safety: a reserved namespace

`tests/test_preregistration_fence.py` already pins `SHOOTOUT.contestants` and
`MULTISTEP.contestants` to literals **and** ties them to the registered document by set equality,
so an agent candidate entering either cohort already fails and **needs no new test**.

What it does not cover is an agent run writing ledger lines **under a registered cohort name**:
`evaluate` guards only `phase2-baseline`. So agent cohorts live in a reserved namespace,

```
agent-{isodate}-{8hex}-{index:02d}      e.g.  agent-2026-11-10-a1b2c3d4-03
```

with no path separator and no glob metacharacter, so it would pass `validate_segment` if anything
ever made it a path segment. The child refuses any cohort name outside the pattern, and the test is
**parametrised over the cohort registry** so a cohort registered later is covered the day it is
added.

**The same hazard, caught in review of the model name.** `validate_segment` rejects glob
metacharacters, and `[` and `]` are among them — so a contestant named `harx[iv_h5+vvix]` would be
unusable as a path segment. Model names reach no path today (they are a JSON field only), but the
cheap spelling costs nothing: the family is named **`harx+iv_h5+vvix`**. *Price if wrong:* a reader
expecting bracket notation does not find it.

## The canary: one per sprint, at the parse boundary

A canary is a deliberately illegal proposal **injected by the harness at the parse boundary, as if
the LLM had emitted it** — the Configurator's raw payload is replaced before the gate sees it. The
gate cannot distinguish it from a real emission, which is the point: it tests the validator on live
traffic, not the model.

**Exactly one per sprint at a deterministic position, not a probability.** A probabilistic rate
means some sprint has zero canaries and the gate went untested on that sprint's traffic; "exactly
one" makes the evidence a certainty and the count reportable. The position and payload are chosen
by a sha256 digest of the sprint id, so a sprint replays exactly while remaining unpredictable from
the proposals.

Six payloads across six **distinct refusal codes** — and the reason that matters is a correction
the architect seat made against itself. With a positive allowlist, a target column, an
out-of-vocabulary name and `rv_proxy_d1` would all land in one code; an `end` key and a `cohort` key
would land in another. A floor asserting six distinct refusal *strings* would have passed anyway,
because the echoed names differ — a floor that looks like evidence and is not. Hence the codes:

| payload | code |
|---|---|
| `extra_regressors: ["target_rv_proxy_h5"]` | `OUT_OF_VOCABULARY` — the forward-target case |
| an extra `"end": "2026-06-30"` key | `UNKNOWN_KEY` — the peek |
| nine columns | `TOO_MANY_REGRESSORS` |
| a repeated column | `DUPLICATE_REGRESSOR` |
| `horizon: 10` | `HORIZON_NOT_CONFIGURED` |
| `symbol: "TSLA"` | `SYMBOL_NOT_IN_UNIVERSE` |

A canary reaches no ledger: it is refused before `submit`, so no child runs.

**If a canary is ever accepted, the sprint aborts at that point**, writes a `canary_accepted` event
and exits non-zero, and nothing from that sprint may be promoted. Said plainly: proposals already
evaluated **keep their ledger lines**. The ledger is append-only and a trial that ran is a trial;
the event says the sprint was *voided*, not that those trials did not happen. Pretending otherwise
would be the rewrite-a-ledger move this project refuses everywhere else.

Two tests, because one is not enough: every payload is refused, with a floor on **distinct codes**;
and a sprint run with a stub gate that accepts everything aborts, records `canary_accepted`, and
leaves no pending approval. Without the second, the first proves the validator works and says
nothing about what the sprint does when it does not.

## The human gate: a different process, and that is all it is

A sprint that reaches a promotable candidate **persists a pending approval and exits**. Promotion
requires a separate top-level package invoked as its own process:
`python -m volab.promote --proposal-id agent-2026-11-10-a1b2c3d4-03`.

**Why a separate package rather than a second verb:** a verb in the same module means the sprint
*could* promote in-process and only a convention stops it. Separate packages make it an import-graph
fact, asserted by `test_the_agent_package_cannot_reach_the_promote_package` — the edge runs one way,
`volab.promote` importing `volab.agent.sprintlog` and never the reverse.

The pending approval carries the sprint and proposal ids, the cohort, the validated proposal as
canonical JSON, the git sha and dirty flag (`None` meaning *git unreachable*, never *clean*, per
R70), the agent-ledger line hashes the approval rests on, and a **fingerprint** over the proposal
and that evidence. `promote` has three refusals, each its own test: no `awaiting_approval` event for
that id; a recomputed fingerprint differing from the stored one, naming both, because the ledger
moved under the approval; and a `promoted` event already present, because promotion happens once.
**There is no `--latest` and no `--all`** — the operator types the id, because a flag that selects
the candidate is the operator no longer choosing it.

**What promotion does in Phase 5 is deliberately narrow: it writes a `promoted` event and nothing
else.** It does not move a champion pointer and writes nothing a daily stage consumes. The reason
is the framework's own ordering: *"Promotion requires dev-window MCS membership, a passing
guardrail gate, human approval **and then a single logged holdout check**"* — the holdout check
comes first and is Phase 6's. So the exit criterion "`interrupt` before promotion" is met by
**pause-and-persist, approval in a separate process, and a recorded decision**, not by a champion
changing hands. The PR says that in those words rather than implying a champion moved.

## Process isolation: a fresh interpreter per evaluation

Not a preference. `src/volab/daily/__init__.py:53-54` states the reason: *"a memoised full-sample
statistic filled by one build and reused by the next is invisible to truncation equivalence within
a process."* A sprint evaluates several proposals in sequence, so chaining them in one interpreter
reintroduces the README's E2 **across proposals, in a loop**, with nothing comparing the second
build to a clean one.

So `submit` writes the validated proposal to a temp JSON file and runs
`[sys.executable, "-m", "volab.candidate", "--proposal", <path>, "--result", <path>]`, one fresh
interpreter per proposal. `--result` is a file and not stdout because a comparison record is about
10 KB against a 16 KiB output tail, so the result would truncate. The proposal travels as a **file
path in argv rather than as argv fields**, which keeps the child's argparse free of per-field
options that could drift from `Proposal`, makes the no-date-option witness trivially true, and
leaves the exact bytes inspectable afterwards. The child **re-validates** the file, because the
gate's verdict does not travel and a tampered temp file must be refused at the boundary that reads
it.

The witness cannot pass vacuously: the child prints `os.getpid()` and two submissions print two
different pids.

**M3 is the live edge case.** `iv_h1` and `iv_var_h1` begin 2022-05-13, so a proposal naming either
against the fixed 2021-01-04 window trips `coverage_refusal`. The child therefore runs
`coverage_refusal` **before fitting**; a refusal writes a `refused` event carrying the reason
verbatim, writes **no** trial line, and exits with a distinct code. Not a static exclusion list —
the refusal asks the data, so a column whose history grows becomes usable without a code change,
and a refused proposal is an event the operator reads rather than a silent drop.

## The checkpointer port

A **domain-level** port, deliberately not a DB-API connection: a port shaped as "execute this SQL"
makes every implementation a dialect, and a `sqlite3` test would then be evidence about sqlite3 and
nothing else. `Checkpointer` is `put` / `get` / `history` / `sprints`, and **two implementations
ship working and tested with no credential** — `FileCheckpointer` (append-only JSONL, the default)
and `SqliteCheckpointer` over an injected `sqlite3.Connection`. Two independent implementations from
day one are what make it a port rather than one class with an interface drawn around it; a shared
conformance test parametrised over both is the witness, in the idiom of `tests/model_conformance.py`.

**A Postgres-backed `Checkpointer` is not written, and is not dressed up as written-but-blocked.**
sqlite3 uses qmark parameters, psycopg uses `%s`, and the DDL differs. Shipping psycopg code that
has never met a server and calling it working would be the R32 overclaim this phase exists to
avoid.

## What is NOT structural

Stated plainly, because a design whose limits are unstated invites exactly the trust this project
withholds.

1. **Dev-window overfitting through many trials — counted, never refused.** The agent ledger's line
   count is the only instrument. Nothing refuses the fiftieth proposal.
2. **An agent that authors feature code — out of scope, and the reason is measured.** Structural
   refusal ends at a closed vocabulary. Code authorship would make truncation equivalence the gate,
   and the README measures that as *a sampled ratchet with a stated power*, not a proof: E1 is
   visible at 52 of 2,312 sessions (2.2%), so a 40-cutoff sample expects 0.9 hits. Not strong
   enough to gate adversarial code.
3. **Report tone and emphasis — unvalidatable.** A numeric-literal check covers digits only. A
   report with every number right and the emphasis wrong passes every check here.
4. **That a *human* approved, rather than a script.** `python -m volab.promote` is a CLI
   invocation, and nothing distinguishes a person typing it from a cron line. What is guaranteed is
   a **different process**, not a different agent. This is the one place where "human gate" is
   weaker than it sounds.
5. **The Critic's hypothesis-to-config alignment check — recorded, never gating.** The framework
   gives the Critic *"alignment between hypothesis and config"*. That is an LLM judging text against
   a configuration, which is **precisely the founding negative result**. Its verdict goes to the
   sprint log and gates nothing. Only `gate()` gates.
6. **Multiple-comparison validity across sprints.** Within a sprint the cohort is new and DM/MCS/SPA
   are valid. *Which sprint's winner to promote* is a comparison over every sprint ever run, and no
   statistic here corrects it. The trial count and `n_eff` report it; nothing refuses it.
7. **A degenerate proposal space.** Nothing stops the agent proposing the same configuration every
   sprint, or five jointly useless columns. De-duplication would be a policy; we count instead.

## The new ratchets

**The holdout-key scan is extended to the three new packages, using the derived version.** Two
candidates existed and they are not equivalent: `test_import_discipline.py`'s `_unlocks` runs over
`tools/` only and **hardcodes** the key names, while `test_daily.py`'s derivation reads the
environment variable from `volab.config.HOLDOUT_UNLOCK_ENV` and the parameter name from `store.py`'s
`def load_panel` **by AST** (not `inspect.signature`, because conftest's police replace the object
for the session, R153). R151 decides it: derive, so renaming either key keeps the scan honest.

**`tests/holdout_scan.py` is written fresh rather than extracted**, in the idiom of
`tests/forward_scan.py`. The extraction would be better engineering and is deliberately not done:
PR #18 adds 542 lines to `tests/test_daily.py` right now, and refactoring a file another story is
editing is how a diff gets lost. *Price, stated:* two copies of the derivation, with extraction
recorded as a follow-up once PR #18 lands.

**`ALLOWED` in `test_import_discipline.py` is NOT extended.** Nothing in the three new packages
reads Parquet directly; the child reads through `load_panel`. An existing test asserts
`sorted(ALLOWED) == ["store.py"]`, and keeping that true is worth more than any convenience. Worth
noting that `test_scan_recurses_and_exempts_only_the_top_level_store` **already names
`agent/store.py` and `agent/tools.py`** in its fixture — the ratchet was written in anticipation of
this phase and needs no edit.

**One thing is ruled out in advance, because an implementer will reach for it.** Scrubbing the
unlock variable out of the child's environment —
`{k: v for k, v in os.environ.items() if k != "VOLAB_HOLDOUT_UNLOCK"}` — **names the variable in a
string literal and trips the very ban**. The child inherits the environment unchanged. The seal is
that no code path passes `allow_holdout=True`, and `_unlock_granted` requires *both* keys. A scrub
is a second mechanism that looks like a defence, weakens nothing, and breaks a ratchet.

## The exit criterion, and how it is witnessed

The framework's criterion is *"one full weekly sprint runs end to end without touching the
holdout"*. It is met today, with no credential, by a sprint driven by a scripted `FakeCompleter`.

**The obvious witness is not evidence, and this document says why.** "No new line in
`data/holdout_access.jsonl`" fails twice over: conftest's police do not exist inside subprocesses,
and the boundary check precedes the `try:` that opens the audit scope — so a *refused* read writes
**no** line at all, making an absent line consistent with both "no attempt" and "a refused
attempt".

And the wrapper belongs in the right process. The sprint is fenced from the store and never calls
`load_panel`; **every read happens in the `volab.candidate` grandchild.** Since `submit` is
injected, the rehearsal passes one that launches `tests/isolated_candidate.py`, a shim in the idiom
of `tests/isolated_build.py` which imports `volab.candidate`, applies conftest's identity sweep over
`sys.modules`, calls the child's `main`, and logs every observed `(end, allow_holdout)` pair to a
file the test asserts on: **every call carried `end == dev_end` and `allow_holdout is False`.**

The rehearsal runs against a **synthetic store** built by the test, with the origin window shrunk
inside the shim and never by a production knob. *What that buys and what it costs:* it proves the
machinery runs end to end and never asks for a date past `dev_end`, hermetically and in CI. It does
**not** show that real dev data yields a sensible candidate — that is the held run below.

## What remains, in three separate categories

The categories are kept apart because folding them together is the easy overclaim.

**Blocked on a credential — two items, and only these two.**

1. **A live-LLM sprint.** Needs an API key. *What running it would prove:* that a real model emits
   payloads the gate can parse, and that the vocabulary is expressive enough to be used. A scripted
   `FakeCompleter` establishes neither.
2. **A Postgres-backed `Checkpointer`.** Needs a DSN. *What running it would prove:* that the SQL
   dialect and DDL are right. The sqlite3 implementation establishes the port's shape and nothing
   about Postgres SQL.

**Held on a user decision — not blocked on a key.** A sprint against the **real** storage root
needs no credential: a scripted `FakeCompleter` plus the real panel would produce real trial lines.
It is held because it would **move newly reported ledgers before the reveal**, which is R139's
question one subsystem over and therefore the user's call. Not run.

**Recorded requirements on Phase 6, testable only once the reveal exists.** The reveal's second
count must include both agent ledgers; and if the reveal writes holdout comparisons to
`data/comparisons.jsonl`, that record must gain a `last_origin` before any agent reads it.
