# Phase 5 decisions — the agent layer

Numbered continuously with every prior phase. Each record states what it costs if the decision is
wrong, because an unpriced structural decision is how an invariant gets traded away for nothing.

Design: `docs/superpowers/specs/2026-11-10-phase5-agent-layer-design.md`. Plan:
`docs/superpowers/plans/2026-11-10-phase5-agent-layer.md`.

---

## R181 — Phase 5 numbers from R181, not R163, because R161-R180 are taken on open branches

My brief said the numbering ran to R162 and to start at R163. **Measured on 2026-11-10 against
fetched refs, that was already false:**

| ref | highest `R1xx` in `docs/` |
|---|---|
| `origin/main` (ee16c3d) | R160 |
| `origin/phase-4-scheduler` (PR #18, open) | **R164** — defines R161-R164 |
| `origin/fix/before-the-reveal-rows` (PR #19, open, running) | **R180** — defines R180, cites R164, R165, R179 |

Starting at R163 would have collided with PR #18's own R163 and R164. R141 records exactly this
collision happening once before, and the consequence was renumbering six rulings — with a board row
that "says R134-R139 and cannot be corrected". So Phase 5 starts above everything observed, and
claims **R181-R209** in the shared notes folder the sibling sessions read, because PR #19's session
was still running when this was written and may yet allocate more.

*Cost if wrong:* a gap in the numbering between R160 and R181 if PR #18 and PR #19 are abandoned.
A gap is cosmetic; a collision forces a renumbering that invalidates citations in already-merged
documents.

---

## R182 — no new dependency, and R84 is explicitly **not** the reason

**Ruling: no LangGraph, no `psycopg`, nothing new in `uv.lock`.** The framework's Phase 5 row names
"LangGraph roles, typed tools, Postgres checkpointer"; the roles and the typed tools are built, the
two named technologies are not.

**R84 cannot be cited here.** MLflow was refused because it *would not resolve* —
`mlflow>=3` needs `pyarrow<26` against this project's `pyarrow>=26`. **Measured: LangGraph resolves
against this lock and imports clean under `-W error`**, adding 39 packages (39 → 78). Borrowing
R84's authority for a dependency that does resolve would be the overclaim this repository treats as
its one unforgivable defect. Three grounds that do hold:

1. `interrupt` is a mechanism where R188 needs a prohibition — we would adopt 39 packages for the
   human-gate feature and then decline to use it. **Honest caveat:** R188's process separation could
   be built on top of LangGraph. The ground is the unused feature, not impossibility.
2. 39 → 78 packages for a seven-step linear machine with one branch and one pause. This project
   already kept `volab.leaderboard` off `volab.compare` because `arch` costs three seconds.
3. `filterwarnings = ["error"]` makes 39 transitively-moving packages an **ongoing** liability: any
   future `uv lock` can introduce a call-time `DeprecationWarning` that fails the whole suite.
   **Stated as a liability, not a measured failure — it does not bite today.**

`psycopg` stays out of the lock entirely: an optional extra **does** enter `uv.lock` and is **not**
installed by `uv sync --locked` (measured), so it would change the lock and get zero mypy or test
coverage.

*Cost if wrong:* roughly 200 hand-rolled lines we own. Worth revisiting the day cycles,
retry-with-backoff or parallel branches enter scope; none is in Phase 5.

---

## R183 — agent evaluations go to a separate tracked ledger, because R139 is a *user* decision

The opposite reading had real support: the pre-registration §4 says the reveal *"takes a second
count and records both plus the delta … since further work may legitimately add trials"*, and the
reveal design insists the protection's purpose is that the number is **known and reported, not
frozen**. The leaderboard already partitions by cohort.

**What overrides it:** R139 is recorded as a **user decision** — *"no Phase 3 walk-forward writes a
ledger line until the user has triggered the holdout reveal"* — and Phase 4 inherited it unchanged.
Routing agent trials into `data/trials.jsonl` would overturn a user decision, which is not a
builder's to take. A separate ledger is the only option needing no new decision from the user.

So: **`data/agent_trials.jsonl` and `data/agent_comparisons.jsonl`, both tracked** (hence two
`!data/…` negations, since `data/**` is ignored with an allowlist), schemas byte-identical to
`Trial.record()` plus `append_trials`' prefix — so that if the user later rules the ledgers should
be one, the migration is `cat`. `data/agent_sprints.jsonl` is the event log and is **gitignored**,
as `data/daily_runs.jsonl` is by R158 Q3; no count from it is reported, and its events carry the
`(cohort, at, git_sha)` key by which trial lines are found, so the two join rather than the agent
keeping a shadow count.

**The agent's runs cannot reach the real ledger**, and that is structural rather than a rule: the
child is handed `run_configuration`'s existing `append` callable (R97), and it is a different one.

*Cost if wrong:* more than one number where the argument wants one, and nothing stops a reader
quoting only the first. See R186 for how far the enforcement actually goes.

---

## R184 — no date-typed field in any tool schema, and no stride either

A peek is made **unrepresentable** rather than forbidden. Sharpened past the brief's "no `end`":
there is no date-typed field at all, and no `every`, because choosing the window or the stride *is*
selection. The harness fixes `first = 2021-01-04`, `last = settings.splits.dev_end`, `every = 5`
(R94's stride) and `minimum_training = 250`, mirroring the existing CLI defaults.

The witness is derived: a test walks the child CLI's `parser._actions` asserting no action's `type`
is `dt.date.fromisoformat`, rather than asserting against a list of option names.

*Cost if wrong:* a legitimate experiment over a different window needs a code change rather than a
flag. That is the intended direction: a window the agent can choose is a window it can choose
badly, and nothing downstream could tell the difference between curiosity and search.

---

## R185 — a positive allowlist, not a subtraction of target columns; and three columns excluded by measurement

The brief asked that every target column be explicitly subtracted from the vocabulary. **Not done,
and the replacement is stronger.** `FEATURE_COLUMNS` contains no target column already (measured:
the intersection with `{target_column(h) for h in HORIZONS}` is empty, 33 columns), and a *positive
allowlist* rejects `target_rv_proxy_h5` **and every name nobody thought of**, where a denylist
rejects only what is on it. What makes the exclusion known rather than assumed is a derived
disjointness test against the target set computed from `volab.features.targets` — so if
`FEATURE_COLUMNS` ever admitted a forward quantity, it fails.

**Three columns *are* subtracted, and that one was not asked for.** Measured on the real feature
table, SPY, 2,312 rows: `har_d_proxy` **is** `rv_proxy_d1` (max abs difference **0.0**),
`har_w_proxy*5` vs `rv_proxy_d5` is 3.5e-18, `har_m_proxy*22` vs `rv_proxy_d22` is 6.9e-18 — and
`src/volab/features/har.py` makes this true by construction, not by coincidence. Since the HAR-X
base always carries the three `har_*_proxy` regressors, a proposal naming any of the three is an
**exactly singular** design. The witness is derived: `fit_ols` over `[*HAR_COLUMNS, column]` must
refuse each excluded name and accept every admitted one.

`MAX_EXTRA_REGRESSORS = 5` is **priced, not chosen**: 9 parameters is 16-20 ms per fit (measured
`fit_ols` at 2,000 rows: k=8 → 16.2 ms, k=16 → 41.3 ms), about 4-5 s per `(symbol, horizon)` over
251 origins. The cap exists because the sealed path may not import numpy (R82), so `fit_ols` is
pure-Python normal equations.

*Cost if wrong:* a genuinely useful sixth regressor, or one of the three excluded columns in a
specification without the HAR base, needs a code change. Both are cheap; a singular fit at run time
inside an unattended sprint is not.

---

## R186 — the dual-count *enforcement* is deferred, and the claim narrows to the leaderboard headline

The intended enforcement was an import ban leaving `count_trials` reachable only from its own
definition and a new `src/volab/counts.py`, so that no surface could print one count without
holding the other. **It fails on day one:** four modules already call `count_trials`
(`walkforward/__main__.py`, `leaderboard/__init__.py`, `compare/__main__.py`,
`evaluators/__main__.py`), so the ban requires migrating three writer CLIs — and one of them,
`src/volab/evaluators/__main__.py`, is being edited **right now** on PR #19, which is the deferred
row about `_describe` printing a trial count.

**Ruling: `src/volab/counts.py` is written and the leaderboard prints both counts as an added line;
the three writer CLIs are not migrated and the import ban is not asserted.** The honest claim
narrows to *the leaderboard headline prints both counts*. The design says so in those words and the
PR must not say more.

Equally deliberate: the leaderboard change is **additive** — a new line beneath the existing
headline, no edit to the headline's own text — because PR #18 adds 20 lines to that module and 38 to
its test, and pinned assertions and quoted documentation both survive an addition where they would
not survive a rewording.

*Cost if wrong:* a reader can quote `trials.jsonl`'s count alone from one of the three unmigrated
CLIs, each of which prints its own ledger's before-and-after delta. The migration is mechanical once
PR #18 and PR #19 land.

---

## R187 — `tests/holdout_scan.py` is written fresh, not extracted from `tests/test_daily.py`

Two derivations of the unlock-key scan exist. `test_import_discipline.py`'s `_unlocks` runs over
`tools/` only and **hardcodes** both key names; `test_daily.py`'s derives them — the environment
variable from `volab.config.HOLDOUT_UNLOCK_ENV`, the parameter name from `store.py`'s
`def load_panel` by AST (not `inspect.signature`, because conftest's police replace the object for
the whole session, R153). **R151 decides which to use: the derived one.**

The better engineering is to extract it to a shared helper with two callers. **Deliberately not
done:** PR #18 is adding 542 lines to `tests/test_daily.py`, and refactoring a file another story is
editing is how a diff gets lost. For the same reason the `_LEDGER_FILENAMES` completeness check is
replaced by a guard asserted in the new file — that no `LIVE_STAGES` entry runs any of the three new
packages — which imports `LIVE_STAGES` read-only and edits nothing.

*Cost if wrong:* two copies of one derivation, which can drift. Extraction is recorded as a
follow-up for when `daily/` is quiet.

---

## R188 — promotion is a separate package that writes an event and moves no champion

The framework's human gate is `interrupt()`. Here a sprint that reaches a promotable candidate
**persists a pending approval and exits**, and promotion requires
`python -m volab.promote --proposal-id …` in its own process. A **separate package** rather than a
second verb, because a verb in the same module means the sprint *could* promote in-process and only
a convention would stop it; separate packages make it an import-graph fact, asserted both ways.

**Promotion writes a `promoted` event and nothing else** — no champion pointer moves, and nothing a
daily stage consumes is written. The framework's own ordering is why: *"Promotion requires
dev-window MCS membership, a passing guardrail gate, human approval **and then a single logged
holdout check**"*. The holdout check comes first and is Phase 6's. Three refusals, each its own
test: no `awaiting_approval` event for that id; a recomputed fingerprint differing from the stored
one, naming both, because the ledger moved under the approval; a `promoted` event already present.
No `--latest` and no `--all`, because a flag that selects the candidate is the operator no longer
choosing it.

So the exit criterion "`interrupt` before promotion" is met by **pause-and-persist, approval in a
separate process, and a recorded decision** — not by a champion changing hands. The PR says that in
those words.

*Cost if wrong:* a promotion that has no effect beyond the record, so an operator expecting the
daily forecast to change is disappointed. That is the correct disappointment: changing it before the
holdout check would invert the framework's own ordering.

---

## R189 — the canary is injected at the parse boundary, one per sprint, across six distinct refusal codes

A canary is a deliberately illegal proposal **the harness substitutes for the Configurator's raw
payload before the gate sees it**, so the gate cannot distinguish it from a real emission. It tests
the validator on live traffic, not the model.

**Exactly one per sprint at a position derived from a sha256 of the sprint id** — not a probability,
because a probabilistic rate means some sprint has zero canaries and the gate went untested on that
sprint's traffic. Deterministic given the sprint id, so a sprint replays exactly.

**Six payloads across six distinct `RefusalCode`s, and the reason is a correction.** With a positive
allowlist (R185), a target column, an out-of-vocabulary name and `rv_proxy_d1` all land in one code,
and an `end` key and a `cohort` key land in another. A floor asserting six distinct refusal
*strings* would have passed anyway, because the echoed names differ — **a floor that looks like
evidence and is not**. Hence `RefusalCode`, and a floor on distinct codes.

**If a canary is ever accepted the sprint aborts there**, writes `canary_accepted`, exits non-zero,
and nothing from it may be promoted. **Proposals already evaluated keep their ledger lines.** The
ledger is append-only and a trial that ran is a trial; the event says the sprint was *voided*, not
that those trials did not happen. Pretending otherwise would be the rewrite-a-ledger move this
project refuses everywhere else.

*Cost if wrong:* one proposal slot per sprint. The real risk is a canary refused by *shape* before
reaching the interesting branch, which is what the distinct-code floor catches.

---

## R190 — no Postgres implementation is written, and it is not dressed up as written-but-blocked

The `Checkpointer` port is **domain-level** — `put` / `get` / `history` / `sprints` — and
deliberately not a DB-API connection: a port shaped as "execute this SQL" makes every implementation
a dialect, and a `sqlite3` test would then be evidence about sqlite3 and nothing else. **Two
implementations ship working and tested with no credential**: `FileCheckpointer` and
`SqliteCheckpointer` over an injected `sqlite3.Connection`, exercised by one conformance test
parametrised over both. Two independent implementations from day one are what make it a port rather
than one class with an interface drawn round it.

**A Postgres-backed implementation is not written.** sqlite3 uses qmark parameters, psycopg uses
`%s`, and the DDL differs. Shipping psycopg code that has never met a server and calling it
"working, blocked on a key" would be an R32 overclaim — so the blocked line says instead: *a
Postgres-backed `Checkpointer` is not written; a DSN would let it be written and run, and what that
would prove is that the SQL dialect and DDL are right, which the sqlite3 implementation does not
establish.*

*Cost if wrong:* if a DSN appears tomorrow someone writes a third implementation, roughly 60 lines,
against a port that two independent implementations already constrain. That is the cheap direction;
a port shaped by one storage engine is the expensive one.

---

## R191 — the exit-criterion rehearsal runs on a synthetic store, and the obvious witness is refused

The framework's criterion is *"one full weekly sprint runs end to end without touching the
holdout"*, and it is met with no credential by a scripted `FakeCompleter`.

**"No new line in `data/holdout_access.jsonl`" is refused as evidence, for two reasons.** conftest's
police do not exist inside subprocesses; and the boundary check precedes the `try:` that opens the
audit scope, so a **refused** read writes no line at all — making an absent line consistent with
both "no attempt" and "a refused attempt". An absent line is not a witness.

What is: the sprint never calls `load_panel` at all (R192's fence), so every read is in the
`volab.candidate` grandchild, and since `submit` is injected the rehearsal passes one that launches
a shim which logs every observed `(end, allow_holdout)` pair. The assertion is positive — **every
call carried `end == dev_end` and `allow_holdout is False`**.

The rehearsal builds a **synthetic** store, and shrinks the origin window **inside the shim**, never
with a production knob. *What it buys:* the machinery runs end to end, hermetically, in CI, with no
key. *What it does not buy, stated because it would otherwise be assumed:* it does not show that
real dev data yields a sensible candidate. That run is held under R194.

*Cost if wrong:* the rehearsal could pass over a synthetic panel whose shape differs from the real
one in some way that matters. Mitigated by P5-B2's real-panel witness and P5-C1's measured child
run, both on real data.

---

## R192 — the agent package reaches no read path, transitively, and the fence is derived

The stronger of the two options: `src/volab/agent/` imports **nothing that reaches `volab.store`**,
so "the agent cannot peek" is an import-graph fact rather than a convention. It is nearly free,
which is why it is taken — every tool in the framework's role table reads a ledger or submits a run,
and **none needs a panel figure**.

**The trap, measured:** `volab.features.targets` reaches `volab.store` two hops deep
(`from volab.store import write_features`), so `from volab.features.targets import HORIZONS` in an
agent module would pull the store, duckdb and pyarrow into the agent process — **and a per-file scan
passes it.** That is R129's hole in the numeric scan, one subsystem over. Hence the fence is
transitive, built on `test_import_discipline.py`'s existing graph walk, and its red witness is a
plant of exactly that import rather than a synthetic one. `HORIZONS` is therefore a literal in the
agent's vocabulary with a two-sided test, while `FEATURE_COLUMNS` is imported directly because
`volab.features.build` is store-free (measured).

**The fence is on the import graph and never on `sys.modules`:** pandas 3.0 imports pyarrow
unconditionally, so pyarrow's presence there is not evidence of a store reach.

**`ALLOWED` in `test_import_discipline.py` is not extended** — nothing in the new packages reads
Parquet directly, and an existing test asserts `sorted(ALLOWED) == ["store.py"]`. Worth recording
that `test_scan_recurses_and_exempts_only_the_top_level_store` already names `agent/store.py` and
`agent/tools.py` in its fixture: R35 widened that scan to `rglob` in Phase 0 *specifically* so this
phase's package would be covered the day it was written, and it is.

*Cost if wrong:* a later tool needing a panel figure must become a subprocess stage, paying one
interpreter start. Cheap, and the fence is what buys the import-level fact.

---

## R193 — the child inherits its environment unchanged; no unlock scrub

An implementer will want to scrub the unlock variable out of the child's environment. **Ruled out
in advance:** `{k: v for k, v in os.environ.items() if k != "VOLAB_HOLDOUT_UNLOCK"}` names the
variable in a string literal and **trips the very ban R187's scan installs**.

The child inherits the environment unchanged. The seal is that no code path passes
`allow_holdout=True`, and `_unlock_granted` requires **both** keys. A scrub is a second mechanism
that looks like a defence, weakens nothing, and breaks a ratchet.

*Cost if wrong:* if an operator exports `VOLAB_HOLDOUT_UNLOCK=1` in their shell, the child inherits
it — and still cannot read the holdout, because nothing passes the argument. The scrub would protect
against a second, non-existent bug while disabling a real guard.

---

## R194 — a sprint against the real storage root is **held on a user decision**, not blocked on a key

Stated as its own category because folding it into the credential-blocked list is the easy
overclaim, and it would be false: a sprint against the real panel needs **no credential at all** —
a scripted `FakeCompleter` plus the real store would produce real trial lines today.

It is held because it would **move newly reported ledgers before the reveal**, which is R139's
question one subsystem over and therefore the user's call. Not run. The PR reports it as *held on a
user decision, analogous to R139 — not blocked on a key*.

*Cost if wrong:* the phase ships without a real-data sprint on the record, so the agent ledgers are
empty at merge. That is the correct state: a ledger with lines nobody authorised is worse than an
empty one.

---

## R195 — `_runner.py` duplicates the daily runner's bounded tail rather than extracting it

`daily/runner.py:73-104`'s bounded chunked-tail loop is real engineering that should exist once, and
the right answer is extraction to `src/volab/_subprocess.py` with two callers. **Deliberately not
done:** PR #18 adds 282 lines to that file. The two `OUTPUT_BOUND_BYTES` constants are coupled by a
test asserting equality, and each file names the other in a comment.

*Cost if wrong, stated plainly:* two copies of a 25-line loop. Only the bound is test-coupled, not
the logic, so a bug fixed in one must be fixed in the other by hand. Extraction is a follow-up.

---

## R196 — `harx+iv_h5+vvix`, not `harx[iv_h5+vvix]`

`validate_segment` rejects glob metacharacters, and `[` and `]` are among them, so a contestant
named `harx[…]` would be unusable the moment a model name became a path segment. Model names reach
no path today — they are a JSON field in the ledger and a cohort join key — so this is prevention
rather than a fix. It costs nothing, and the reserved cohort namespace was already made glob-safe
for the same reason.

*Cost if wrong:* a reader expecting bracket notation does not find it.

---

## R197 — the brainstorming skill's human gate is served by review seats, and the baseline is 1856 tests

Two process records, both so that a later reader is not misled.

**The human gate.** `superpowers:brainstorming` requires the human partner to approve the design
before implementation. The user is not in this session — the task arrived from another session and
directs design → plan → build → PR in one pass, and explicitly says not to ask for credentials in
the opening turns. So the skill's open questions were resolved as the numbered decisions above, and
its approval gate was served by an independent architect seat and a review seat on every story. This
is a **substitution, not a satisfaction** of that gate: nobody but the user can approve on the
user's behalf, and anything here that should have been their call is flagged as held (R194) rather
than decided.

**The baseline.** The brief said the suite was "~2150 tests". Measured on `ee16c3d` in this worktree
with the store populated: **1856 pass**, ruff clean, 107 files formatted, mypy clean over 45 source
files. The larger figure is the tree including PR #18's roughly 300 unmerged Phase 4 tests. Nothing
in this phase's documents quotes 2,150.

A third, smaller record belongs here because it will bite the next person to cut a worktree: `data/**`
is gitignored but for an allowlist, so **a fresh worktree fails the suite** —
`tests/test_coverage_real.py` catches `DatasetNotFound` and calls `pytest.fail` with a rebuild
instruction rather than skipping. The cure is to rebuild, or to copy `curated/`, `features/` and
`raw/` from a populated clone; copying `trials.jsonl` or `comparisons.jsonl` would import another
branch's ledger state, because those two are tracked.

*Cost if wrong:* if the architect and review seats missed something the user would have caught, the
phase merged a design they did not choose. Mitigated by keeping the credential boundary and the
ledger question (R183, R194) as the user's, and by stating this substitution rather than letting a
reader assume the gate was met.

---

## R198 — `HarX` keeps its extras in `Domain.requires`, and the thin/null skips are defence in depth

A review seat found that because `HarX` declares its extra regressors in `Domain.requires`,
`evaluate` drops an origin for the **whole cohort** when a declared column is null or thin —
*before* `HarX.fit` runs. So two of its three `ModelSkip` branches are largely unreachable inside a
cohort, and the first version of its docstring claimed R87's isolation ("leaves the origin in place
for everyone else") for cases that cannot reach it.

`Domain`'s own docstring in `src/volab/models/base.py` states the mechanism outright: a declared
column "costs **every** contestant its origins, by three routes in `volab.walkforward`, each of
which reads `cohort.requires_at`", while `Evaluation.comparable` is explicitly *not* such a route.

**Ruling: keep the extras in `requires`.** The third route is the one that earns it —
`coverage_refusal` refuses a window beginning before a declared column has enough history, so a
proposal naming a sparse column is refused **up front**, with a reason an operator reads and no
trial line written. The alternative is worse than it looks: with the extras undeclared, an agent
could shrink the whole cohort's origin count — and so `n_eff`, and so every DM/MCS figure in that
sprint — merely by naming a sparse column. An up-front refusal prevents the case; a `ModelSkip`
would only absorb it.

What changed is therefore the docstring, not the code. It now says that the null-extra and
thin-training skips are defence in depth for callers that are **not** `evaluate` (the conformance
suite, and direct use), that `evaluate` screens both cohort-wide first, and that the
**singular-design** branch is the one that genuinely fires under `evaluate`, because `evaluate` does
not pre-screen for collinearity.

*Cost if wrong:* a legitimate proposal naming a column with short history is refused rather than
evaluated on the sessions where it exists. That is the conservative direction, and the refusal names
itself so the operator can see why.

---

## R199 — `has_distribution` stays in the vocabulary, because constancy is a property of the window

P5-B2's fittability witness measured that `has_distribution` has **one distinct value over 2,312
rows** on the real SPY feature table (2026-11-10), so it is collinear with the intercept and every
proposal naming it is refused.

**Ruling: keep it.** The refusal should **ask the data** rather than consult a static exclusion
list, exactly as the sparse-`iv_h1` case does — a column that is constant in this window may not be
in a later one, and a static list would need editing for it to become usable. What this requires is
that the refusal be *clean*, and it is: `HarX.fit` catches `fit_ols`'s `SchemaError` and re-raises
`ModelSkip` naming the column, so one model skips one origin and the origin stands for everyone
else.

The witness is positive rather than an admission: a test derives the constant columns **from the
data** and asserts each is refused as a `ModelSkip` naming it, and it fails if no column is constant,
so it cannot pass vacuously. The strict-xfail that first recorded the complaint was deleted.

*Cost if wrong:* one vocabulary slot that is unusable today, which the agent will learn to stop
proposing only by proposing it and reading the refusal. Cheaper than a list that goes stale.

---

## R200 — what the ratchets actually caught, recorded because an unbitten guard is not evidence

This project does not accept a guard whose only evidence is that nothing bad has happened. Each of
Phase 5's new ratchets has drawn blood, and these are the instances:

- **The transitive store-reach fence caught the realistic two-hop plant.** A planted
  `src/volab/agent/_plant.py` containing exactly `from volab.features.targets import HORIZONS`
  failed with `volab.agent._plant reaches the store through volab.agent._plant ->
  volab.features.targets -> volab.store (R192)`. A per-file scan passes that import. The plant was
  deleted and never committed.
- **The unlock scan missed three spellings across two review rounds, all now closed:** reaching the
  environment variable through `volab.config.HOLDOUT_UNLOCK_ENV` instead of its literal; passing the
  parameter as a string through `**{"allow_holdout": True}`; and a function *definition*
  `def f(allow_holdout=False)`, which is an `ast.arg` and neither a `Name` nor a `keyword`.
- **The fittability witness found two defects in the design it was handed** (R185's amendment, and
  R199).
- **Review found an R32 defect**: a docstring quoting a per-fit timing whose own author had measured
  a different figure. The number was dropped rather than swapped, because the author had not
  reproduced the figure it was offered — which is the correct treatment.

**Stated limits of all four scans**, because a ratchet whose limits are unstated invites the trust
this project withholds: they read syntax and the import graph only. A runtime `importlib` import, a
name reached through `getattr`, a key assembled from fragments, and `python -m volab.candidate` in a
subprocess are all invisible to them.

---

## R201 — `volab.promote` writes `proposal_id` on its `promoted` event

A contract discovered while building the sprint log, recorded here because it binds a module that
does not exist yet. `pending_approvals` treats a proposal as promoted when a `promoted` event
carrying the **same `proposal_id`** exists — keyed on the proposal, not the sprint, because one
sprint may raise several candidates and promoting one must not silently clear the others.

So `volab.promote` **must** write `proposal_id` on the `promoted` event. If it wrote only
`sprint_id`, every pending approval from that sprint would read as promoted, which is the
quiet-approval failure the human gate exists to prevent.

*Cost if wrong:* the gate would clear candidates nobody approved. That is the most serious failure
available in this subsystem, which is why the contract is written down rather than left to the
implementation to rediscover.

---

## R202 — the no-date witness cannot be written with `is`, and that is a latent defect wider than this phase

R184 says the witness asserts no argparse action's `type` **is** `dt.date.fromisoformat`. As written
that assertion can never be true, and the implementer found it: `fromisoformat` is a classmethod, so
every attribute access builds a **new bound method object** and `is` is false even for a parser that
genuinely uses it. A witness written with `is` would pass vacuously — it would report "no date
option" about a parser that had one.

**Ruling: the comparison is `==`, and the scan ships with a plant that proves it bites** — a
date-typed parser the test constructs and asserts is caught. R184's wording is corrected to `==`.

**The wider point, recorded as a follow-up rather than fixed here:** any other identity-based
witness in this repository over a classmethod or a bound method has the same hole, and it is the
quietest kind, because the test passes. Auditing the suite for `is`-based assertions over bound
methods is **not Phase 5's** and is carried as a follow-up.

*Cost if wrong:* none in the direction taken — `==` is strictly weaker than `is` and the plant is
what makes the test real. The cost of the original wording would have been a guard that always
passed.

---

## R203 — the child's exit codes, and one comparison record per loss

Both ratified as the implementer built them, because both are more honest than what my brief said.

**Exit codes.** `0` evaluated; `1` evaluated but the built rows disagree with the persisted table
(the walk-forward CLI's own meaning, and an uncaught exception too); `2` left to argparse; `3`
refused for coverage, no fit and no trial line; `4` the proposal file refused at the boundary,
nothing read and nothing written; `5` refused by the audit, models fitted and nothing appended.
Deliberately **not** `2` for a refusal, as `walkforward` uses, because argparse already exits 2 and a
runner could not then tell a usage error from a refusal.

**One comparison record per loss, so two lines per evaluation, not one.** My brief said "one
comparison record". `Comparison` carries a single `loss` and `data/comparisons.jsonl` already holds
one line per loss, so matching that shape is what keeps R183's promise that a merged ledger is a
`cat` away. `first_origin` and `last_origin` are taken from the **comparable** origins of that loss
rather than from the requested window, and are null when that set is empty — which is the honest
reading, since the requested window is not what was scored.

*Cost if wrong:* a reader counting "comparisons per sprint" must divide by the number of losses. The
alternative cost was a ledger that could not be concatenated with the one it mirrors.

---

## R204 — coverage is reported NOT CHECKED when no persisted feature table exists, and that gap is stated rather than closed

`run_configuration` falls through to `evaluate` when the store holds no persisted `har_daily` table,
so in that case the child's `coverage_refusal` does not run and a sparse column would be **absorbed
rather than refused**. Closing it would mean changing `volab.walkforward` or performing a second,
duplicate check.

**Ruling: state it, do not close it.** It is unreachable on the real store, which always has the
persisted table, and the only paths that reach it are synthetic test stores that build no feature
table. Changing `walkforward` for a case that cannot occur in a real run is the wrong trade, and a
second check that could disagree with the first is worse than one.

*Cost if wrong:* a synthetic harness could believe a sparse proposal was evaluated when its coverage
was never checked. The docstring says so at the point where it matters.

---

## R205 — one measurement run touched the real store, and why that is not the run R194 holds

For sizing, the child was run **once** against the real store — SPY at h=5, three references plus a
two-extra `HarX`, 250 origins at stride 5. **Measured: 45.4 s wall**, of which 36.6 s in-process, the
rest interpreter start and imports; it wrote 8 trial lines and 2 comparison records and printed
"truncation equivalence: 250 of 250 origins identical". A second run reproduced a **real** coverage
refusal: `iv_h1` at h=1 exited 3, reporting that the first qualifying origin is 2023-05-16 with 119
of 251 origins before it, and wrote no trial line.

**Those outputs were deleted and are not in the tree.** Verified afterwards: `data/agent_trials.jsonl`
and `data/agent_comparisons.jsonl` do not exist, `data/trials.jsonl` stands at **414 lines** and
`data/comparisons.jsonl` at **52** — the exact counts the pre-registration recorded at registration.
Nothing a reader counts has moved.

**This is not the run R194 holds**, and the distinction is worth being precise about rather than
glossed. R194 holds a *sprint* whose trial lines would **persist** in newly reported ledgers before
the reveal. A measurement whose output was deleted, from files that were untracked when they were
written, left no ledger line and entered no commit. What R194 still forbids, and what has not
happened, is a sprint that leaves agent ledger lines in the tree.

**So the agent ledgers are empty at merge.** The PR must say that, and must not present an empty
ledger as a sprint that found nothing.

---

## R206 — the child cannot be launched against a store outside the repository, so the rehearsal passes a hand-built `Settings`

Found while building the subprocess runner, and it invalidates a line in this phase's own design.
The design said the exit-criterion rehearsal (P5-E3) would build a **synthetic store under
`tmp_path`** and launch the real child against it. **It cannot.** `_validate_storage_root` in
`src/volab/config.py` resolves the root against the repository and refuses anything that is not
strictly inside it — and `volab.candidate.__main__` calls `load_settings` **before** it reads the
proposal file, so a `tmp_path` root makes the child exit on a traceback before it has looked at its
input. Measured: the runner's first real-child attempt did exactly that.

**Ruling: the rehearsal does not go through `__main__`.** `tests/isolated_candidate.py` imports
`volab.candidate` and calls `evaluate_proposal` with a **hand-built `Settings`** carrying a
`tmp_path` storage root. That is legitimate rather than a loophole: `load_panel` accepts a
`Settings` that is **more conservative** than the repository's and refuses only one that moves
`dev_end` *later*, which a storage-root change does not. The seal is untouched.

Rejected: putting the rehearsal's store inside the repository (it would write under `data/`, which
is where the tracked ledgers live, and a test that writes there is one mistake away from moving a
reported number); and relaxing `_validate_storage_root` (it is a guard, and R191's anti-goal is a
guard relaxed for a test's convenience).

*Cost if wrong:* the rehearsal exercises `evaluate_proposal` rather than the child's `__main__`, so
the argument parsing and the exit-code mapping of the real entry point are **not** covered by the
end-to-end witness. They are covered separately by `tests/test_candidate_cli.py` and
`tests/test_agent_runner.py`, and the PR must not claim the rehearsal covers them.

**Amended after a peer session checked Phase 4, whose treatment is fuller than this and which this
record now cites rather than re-deriving.** Verified on `origin/phase-4-scheduler`:

- `src/volab/daily/__init__.py` states the constraint in a **stronger** form — the repository root
  `storage.root` is resolved against is *derived from the installed package*, so neither
  `load_settings(path=...)` **nor** `VOLAB_STORAGE_ROOT` can name a temporary directory. My first
  draft mentioned only the environment variable; the config-file route is the one someone reaches
  for next, and it is refused identically.
- `tests/test_daily_rehearsal.py` pins **both** routes, and `tests/conftest.py` "builds its
  `Settings` by hand for exactly that reason".

So **the hand-built `Settings` is this repository's house pattern, not a Phase 5 concession** — which
is how this record should have read in the first place.

**What is genuinely ours is the ordering, and it is a defect rather than an inherited constraint.**
Phase 4's `daily/forecast.py` parses its input first and loads settings second; `candidate.__main__`
loaded settings first, so a refused configuration escaped as an **uncaught traceback** instead of
saying why. The order itself cannot be inverted — `validate` needs `settings.universe` — so what is
fixed is the refusal: `load_settings` is now wrapped, and a `ConfigError` exits **6** with the reason
printed and **no result file**, since no evaluation began. Witness:
`test_a_storage_root_outside_the_repository_is_refused_intelligibly_and_writes_no_result`.

The distinction the peer drew is the right one and worth keeping: *"cannot be aimed at a temp store"*
is true of both subsystems, by design; *"cannot explain why"* was only ours.

---

## R207 — the holdout-marker scan matches the bare literal `"holdout"`, and over-matching is the right direction

P5-C3 used `"holdout"` as a **model name** in a test constant and the marker scan failed the file:
it looks for the marker name in any test module importing a Phase 5 package, and a string constant
spelling it is indistinguishable from the marker to that scan. The story renamed its constant to
`"late"`.

**Ruling: leave the scan as it is.** The over-match costs a renamed test constant; the alternative —
narrowing it to decorator and `pytestmark` positions only — would miss a marker applied through
`request.applymarker` or assembled at runtime, and the scan already states that runtime
construction is beyond it. A guard that occasionally refuses something harmless is the right
failure direction for a guard over a sealed window.

Recorded because it will happen again: **a test module importing `volab.agent`, `volab.candidate`
or `volab.promote` must not use the bare literal `"holdout"` for anything.** It has cost one story
a rename already.

*Cost if wrong:* a future legitimate use of that word as data in such a test has to spell it
differently, and this record is where they will find out why.

---

## R208 — what "a peek is unrepresentable" earns, and what it does not

The design's central claim is that a peek is **unrepresentable rather than forbidden**: no tool takes
a date, the harness fixes the window from `dev_end`, and the agent selects from a closed vocabulary.
A peer session asked the question that broke Phase 1's exit criterion four times — *what does this
refuse to **express**, and what does it merely refuse to **do**?* — and the claim needs its scope
stated, because it is easy to let the word carry more than it has earned.

**Two attacks, and the answer to each.**

**1. Composition: can two individually harmless selections together name a window?** No, and the
reason is structural rather than a check. A `Proposal` has five fields — `family`,
`extra_regressors`, `symbol`, `horizon`, `reference` — and **none is temporal**. `horizon` is one of
`(1, 5, 22, 66)`, a forecast length and not a position in time; nothing combines with it to locate a
date, because no field holds a date to combine. The window (`first = 2021-01-04`,
`last = settings.splits.dev_end`, `every = 5`) is a set of module constants in `volab.candidate`
that no field reaches. Closure is enforced where selection happens — `validate` refuses unknown
keys, so the field set cannot be widened by adding one — **and again at the child**, which
re-validates the file it reads, so a widened payload is refused at the boundary that would act on
it rather than trusted from upstream.

**2. A pattern of calls: can a sequence of legitimate calls encode what no single call could ask
for?** The answer depends on which window, and the distinction is the whole point.

- **For the holdout: closed, structurally.** Every byte the agent can read back is a function of
  dev-window data alone. The child reads through `load_panel` with `end` fixed at `dev_end` (R184);
  the agent's trial reader drops any line whose `last_origin` is past `dev_end` and reports how
  many it dropped (P5-C3); and the agent reads no comparison file but its own, because
  `comparisons.jsonl` records carry no origin dates to filter on (R183). Since no response depends
  on a holdout value, **no pattern of responses can encode one** — a sequence of functions of dev
  data is still a function of dev data. This is the property the word "unrepresentable" actually
  earns.
- **For the dev window: open, and only counted.** A pattern of calls is exactly how dev-window
  overfitting happens. Each proposal returns real dev losses, and an agent that proposes fifty
  configurations and keeps the best has searched the dev window, which no single call could do.
  Nothing here refuses that. It is **counted** — every evaluation is a line on
  `data/agent_trials.jsonl` — and the design's "What is not structural" item 1 says so. The pattern
  attack therefore succeeds against the dev window, and the defence is the trial count, not a
  refusal.

**So the precise claim is:** *the agent cannot express a request for, or receive any function of,
data past `dev_end`; it can search the dev window as hard as it likes, and every step of that search
is counted.* The PR states it in those words. Stating only the first half would be the overclaim.

**Amended: the read surface includes counts, refusals and orderings, and the first draft of this
record missed one.** The same peer pointed at a clause in this record's own premise: *"the trial
reader drops **and counts** any line past `dev_end`"*. That count is a **statistic computed over
data past the boundary**. It is not a holdout value. But "everything the agent can read back is a
function of dev data" is false as stated if the count is among what it reads, and that is the class
of escape that beat Phase 1: a quantity over data the harness believed excluded, invisible because
nothing the suite varies changes it. **It was in the agent's prompt.** The in-flight sprint machine
formatted `{excluded} lines excluded` into text sent to the model. Today it is always 0, which is
exactly why no test would have noticed.

Resolved before P5-D4 commits, and pinned there:

- **Counts.** Nothing computed over lines past `dev_end` enters any `Prompt`, *including the count*.
  `TrialView.excluded` still exists, because a silent drop would understate the trial count **to a
  human**. It goes to the sprint log, which is the operator's surface and is read back into no
  prompt. And since the child never evaluates past `dev_end`, a nonzero count on the **agent**
  ledger is an integrity anomaly: the sprint fails loudly rather than continuing on a filtered
  view. Witness: a planted post-`dev_end` line, after which **no prompt `FakeCompleter` recorded
  contains any trace of the line or of the count**. It is written to fail against the leaking
  version before the fix.
- **Refusals.** A refusal is an observation, so a refusal that depended on holdout state would be
  a bit about the holdout. None can: every refusal the agent can trigger — the gate's codes, the
  child's coverage, proposal and audit refusals — is computed from the proposal and from data read
  with `end` fixed at `dev_end`. Stated as a consequence of R184, not left implied.
- **Ordering.** Kept lines come back in file order, which does not depend on which lines were
  dropped. **Timing** is not an agent observable: a read is a function call whose duration no
  prompt carries. Both are closed, and recorded because "unrepresentable" is a claim about the
  **whole** read surface and these are part of it.

**And the failure is itself one bit, which this record names rather than implying the set is
empty.** Failing the sprint on a nonzero count is an observable derived from data past `dev_end`:
one bit, "a line exists beyond the boundary". The same peer caught this, one step further down. So
the honest statement is not "nothing computed over post-`dev_end` lines is observable". It is:

> **No value or statistic from beyond `dev_end` reaches a prompt. One bit, that the invariant was
> violated at all, reaches the operator as a sprint failure. That bit is only ever set by a harness
> bug, never by anything the agent can cause or query.**

That is still a strong claim, and it is true. It holds for the right reason: the agent cannot write
to its own ledger past `dev_end`, because the child evaluates only up to it. So the agent cannot
provoke the failure to learn the bit, and a bit that appears only when something is already broken
is not a channel anyone can use. To keep it *exactly* true, the bit is **operator-only**. It goes to
the sprint log and not into any checkpointer state a later sprint reads back into a prompt, and the
planted-line witness checks a second sprint's prompts as well as the first's.

The broader lesson, worth more than the fix: **a count is a channel. Whenever a reader reports how
much it filtered, check who can see the report.** It generalises past this repository, and it applies
recursively: the refusal triggered by a count is a channel too.

**What this does not establish, kept separate:** that the *code* the child runs is free of a forward
peek. That is truncation equivalence's job, inherited unchanged from Phase 2 and run by the child
on every evaluation ("truncation equivalence: 250 of 250 origins identical" on the real measurement
run, R205). The closed vocabulary keeps the agent from authoring such code; it does not prove the
harness's own code clean, and nothing in Phase 5 claims it does.

*Cost if wrong:* if a response channel exists that depends on a holdout value — a field added to a
trial line later, a new reader, Phase 6 writing holdout rows somewhere the filter does not cover —
the first bullet stops being true without any test noticing. The binding Phase 6 requirements in
the design (count both agent ledgers at the reveal; give holdout comparison records a
`last_origin` before any agent reads that file) exist to keep it true.

---

## R209 — the sprint machine's three departures from the design, ratified

P5-D4 (`a029ac5`) built the sprint machine and reported three places where what it built differs from
what the design says. Each is ratified, because each is more accurate than the text it departs from.

**1. Exactly one canary per sprint *that reaches the gate*.** R189 says exactly one per sprint. A
sprint the Supervisor stops before configuring anything never reaches the gate, so it has no traffic
to substitute a canary into and carries none. The guarantee R189 wants, that the gate never goes
untested on a sprint's traffic, holds unchanged: a sprint with no traffic has nothing for the gate to
be untested on. The design's wording is corrected to this.

**2. An accepted canary always fires before anything is evaluated.** The gate step gates every payload
before the run step begins, so a canary the gate accepts voids the sprint while nothing has run.
R189's provision that "proposals already evaluated keep their ledger lines" therefore cannot arise
inside one sprint. The provision stands as a statement of the ledger's append-only rule, but it is
not exercised, and the PR does not claim it is.

**3. `completed` is in `SprintResult.outcome` and is never returned.** A sprint that runs to the end
is `no_candidate`, `awaiting_approval` or `failed`, and the implementer could not identify what
`completed` was meant to distinguish from `no_candidate`. Neither could I. It is a dead value in the
interface, kept for now so P5-E1 and P5-E2 build against an unchanged type, and recorded for removal
when the phase closes.

*Cost if wrong:* a reader taking R189's original wording literally would expect a canary event on
every sprint, including a stopped one. This record and the corrected design say otherwise.

---

## R210 — a crash between a child returning and its checkpoint resubmits the proposal, and the duplicate is counted

The sprint checkpoints after each step, so a process killed after a child has returned but before
the `run:progress` checkpoint is written **resubmits that proposal on resume**. The resubmission
runs a fresh child, which appends a second set of ledger lines for the same cohort, and all of them
land in the cohort's approval evidence.

**Ruling: this is the honest outcome, and it is documented rather than de-duplicated.** The child
genuinely ran twice, and the agent ledger exists to count every evaluation that ran (R183).
Suppressing the second set would make the ledger say one evaluation happened when two did, which is
the understatement the ledger is there to prevent. De-duplication would be a policy, and this phase
counts rather than policies (design, "What is not structural", item 7).

Two consequences are binding on P5-E1. It must recompute the fingerprint over **every** surviving
line for the cohort, duplicates included, exactly as the sprint did. And a crash-window resubmission
is a case **no test covers today**; the implementer stated so, and it is on the list of what the
rehearsal does not prove.

*Cost if wrong:* a proposal hit by the crash window carries doubled trial lines and looks as though
it was evaluated more often than it was intended to be. That is true of what happened, so it is not
an overclaim. The reader has to know the window exists, and this record is where they learn it.

---

## R211 — the sprint read its ledger from the wrong root, and its tests could not see it

**The defect.** `volab.agent._Sprint._ledger` called `read_agent_trials(settings.storage_root, …)`.
`storage.root` is the `data` directory, and `AGENT_TRIAL_LEDGER` is already `data/agent_trials.jsonl`,
so on the real configuration the sprint opened **`data/data/agent_trials.jsonl`** — an empty file.
`volab.candidate` writes to `settings.storage_root.parent / AGENT_TRIAL_LEDGER`, and `volab.promote`
and `volab.leaderboard` read from the same parent. So in production the sprint would have seen no
trials, raised no candidate, and run R208's integrity check against nothing. The leak fix that check
enforces would have been **inert on the only configuration that matters**.

**Why no test saw it, which is the part worth keeping.** The sprint's fixture set
`storage_root=tmp_path`. Under that layout a read from `storage_root` and a write from `storage_root`
land on the same file, so the tests planted lines exactly where the buggy read looked and agreed with
themselves completely. **They were consistent with one another and inconsistent with the
repository.** It took a different story, the human gate, built against the real convention, to
notice the two roots disagree.

**Found by** P5-E1's implementer, reported as a concern rather than fixed, because the file was not
in its set. That is the right behaviour, and it is how the defect reached anyone.

**Fixed test-first, and the red was large.** The fixture now uses the repository's real shape,
`storage_root = <root>/data`, with a comment saying why. Against the **unfixed** read, that one
change turned **14** tests red, among them `test_a_ledger_line_past_dev_end_fails_the_sprint_and_
reaches_no_prompt` — direct evidence that the integrity check had been checking an empty file. A new
witness, `test_the_sprint_reads_the_ledger_the_evaluation_child_writes`, writes one line where the
child writes it and asserts the sprint reads that exact line back. It checks the line's **content**
and not `TrialView.source`, because `source` holds the *relative* ledger path and cannot tell two
roots apart — an assertion on it was my own first mistake here and failed for that reason alone.
After the fix: 54 of 54 sprint tests pass.

**My own miss, recorded as such.** When I merged `main` (PR #18) I ran a hand-picked set of "seam"
tests and reported 454 passing. That set omitted `tests/test_agent_sprint.py`, which #18 broke in a
second way — `Splits` gained a required `holdout_end`, and the fixture built `Splits` by hand. The
figure I reported was true of the set I ran and silent about the file I did not. **A hand-picked
subset reports on what it picked.** The single full-suite run before the PR is what closes that; it
is not optional.

*Cost if wrong:* none in the direction taken — the fix aligns the sprint with three modules that
already agreed. The cost of the defect, had it shipped, was an agent layer that could never promote
anything, with a leak guard that guarded an empty file, and a test suite reporting all of it green.

---

## R212 — the fence caught `volab.counts` reaching the store, so the CLI keeps two copies, each pinned to its original

The agent CLI (P5-E2, `cb9803f`) could import neither `volab.counts.count_all` nor `git_state`.
**The R192 fence refused both, and that is the fence working, not a defect in it.**
`volab.counts` (P5-C3) imports `count_trials` from `volab.walkforward`, which reaches `volab.store`,
so the fence reported `volab.agent.__main__ -> volab.counts -> volab.walkforward -> volab.store` the
moment the CLI tried it. No one had planned for `volab.counts` to be on the read path; the transitive
walk found it. `git_state` lives in `volab.store` itself.

**Ruling: two local copies in `volab.agent.__main__`, each test-coupled to the original, rather than
a fence relaxed or `volab.counts` reworked mid-phase.**

- `_count_all` is pinned to `volab.counts.count_all` over the same three ledger files.
- `_git_state` is pinned to `volab.store.git_state` by
  `test_the_cli_git_state_agrees_with_the_store_git_state_it_duplicates`, which I added after the
  story landed: the CLI shipped the copy with **no** coupling, and a drifting copy would mis-record
  the sha or dirty flag a sprint stamps on its approval (R70) while still returning a plausible
  pair. Its stated limit: it proves agreement on the tree it runs on, not on every tree state.

The cleaner fix is to make `volab.counts` store-free — counting `trials.jsonl` from a path literal
rather than importing `count_trials` — which would let the CLI import it. **Not done here**: it
changes a module the leaderboard depends on, and the deliberate reuse of `count_trials` there was
R186's way of keeping the leaderboard's headline and its new line in agreement. Recorded as a
follow-up.

**Also fixed after the story landed: a test that could not fail.** The full-sprint CLI test asserted
`status == (EXIT_FAILED if ": failed" in out else EXIT_OK)` — it read the expected exit code back
out of the program's own output, so it checked only that the code agreed with the message and passed
whichever outcome occurred. The scripted run is deterministic, so it is now pinned: exit 0, outcome
`awaiting_approval`, three proposals accepted, the canary refused, and the output pointing at
`python -m volab.promote` as a separate process.

*Cost if wrong:* two copies that can drift. Each is test-coupled, so a drift fails a test rather than
a production sprint, which is the condition under which a copy is acceptable here (the same shape as
R195's duplicated runner loop).
