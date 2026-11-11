# Phase 5 — the agent layer — sprint board

Branch `phase-5-agent-layer`, cut from `main` at `ee16c3d`. Design
`docs/superpowers/specs/2026-11-10-phase5-agent-layer-design.md`, plan
`docs/superpowers/plans/2026-11-10-phase5-agent-layer.md`, decisions
`docs/superpowers/phase5-decisions.md` (R181 onward — **not** R163, see R181).

**Baseline at `ee16c3d`, measured in this worktree:** 1856 tests pass, `ruff check` clean,
`ruff format` reports 107 files formatted, `mypy --strict` clean over 45 source files. A board row
states the sha it was verified at, and a line number is treated as perishable (R155).

## Status

| Sprint | Goal | State |
|---|---|---|
| 0 | scaffold: ignores, boards, package stubs | done (`ee16c3d` + scaffold commit) |
| A | the fences, before any code of the phase | **done** (`ec80441`, `185a021`) |
| B | the vocabulary and the `HarX` contestant | **done** (`60796ca`, `e4d6e59` + a fix round) |
| C | the child, the sprint log, the counts | **done** — C1 `3e15fd7`, C2 `6da1eaa`, C3 `35c5424` |
| D | the ports and the roles | **done** — D1+D2 `91de85b`, D3 `2618462`, D4 `a029ac5`, D5 closed within D4 |
| E | the human gate, the CLI, the exit criterion | **done** — E1 `d709148`, E2 `cb9803f`, E3 `ce8ee90` |
| F | close out: audit, README, PR | not started |

## Stories

| ID | Story | Owner | Est. | Depends on | State |
|---|---|---|---|---|---|
| P5-A1 | The derived holdout-key scan as a reusable helper | implementer | 45m | — | done (`1908136`, `ec80441`) |
| P5-A2 | The three new packages reach no read path and name no unlock key | implementer | 1h | A1 | done (`185a021`) |
| P5-B1 | `HarX`: HAR plus a declared subset of the vocabulary | implementer | 1h | — | done (`7a5e928`, `e4d6e59`) |
| P5-B2 | The closed vocabulary, `Proposal`, `validate`, `gate`, canary payloads | implementer | 1h | — | done (`aae33db`, `60796ca`) |
| P5-C1 | The evaluation child and the two agent ledgers | implementer | 1h | B1, B2 | done (`3e15fd7`) |
| P5-C2 | The sprint event log and the pending approval | implementer | 45m | B2 | done (`6da1eaa`) |
| P5-C3 | `LedgerCounts`, the ledger reader, the leaderboard's added line | implementer | 1h | A2, C1 | done (`35c5424`) |
| P5-D1 | The `Completer` port, the urllib edge client, the fake | implementer | 45m | — | done (`91de85b`) |
| P5-D2 | The `Checkpointer` port and two credential-free implementations | implementer | 1h | — | done (`91de85b`) |
| P5-D3 | Subprocess-per-evaluation | implementer | 45m | C1 | done (`2618462`) |
| P5-D4 | The six roles, the seven-step machine, the sprint | implementer | 1h | C2, D1, D2, D3 | done (`a029ac5`) |
| P5-D5 | The canary bites, and an accepted canary voids the sprint | implementer | 45m | D4 | **closed, delivered within D4** — `tests/test_agent_sprint.py` asserts `voided_by_canary`, one `canary_accepted` event and no further gating; no separate dispatch, which saved one subagent on an 8 GB machine |
| P5-E1 | `python -m volab.promote` and its three refusals | implementer | 1h | C2, D4 | done (`d709148`) |
| P5-E2 | `python -m volab.agent`: `sprint` and `show` | implementer | 45m | D4 | done (`cb9803f`, hardened `f4b01fa`) |
| P5-E3 | The exit criterion: a full sprint end to end, witnessed | implementer | 1h | E1, E2 | done (`ce8ee90`) |
| P5-F1 | Delivery audit: the board against git | delivery-auditor | 30m | E3 | todo |
| P5-F2 | README status, the board's close, the PR | orchestrator | 30m | F1 | todo |

Waves, each wave's stories touching disjoint files: **A1** → **A2** → **B1 ∥ B2** →
**C1 ∥ C2** → **C3** → **D1 ∥ D2 ∥ D3** → **D4** → **D5** → **E1 ∥ E2** → **E3** → **F1** → **F2**.

## Two open PRs overlap this phase's files, and every story routes around them

Measured with `git diff --stat ee16c3d...<branch>` on fetched refs:

| PR | branch | files this phase cares about |
|---|---|---|
| #18 | `phase-4-scheduler` | `tests/test_daily.py` (+542), `src/volab/leaderboard/__init__.py` (+20), `tests/test_leaderboard.py` (+38), `src/volab/daily/runner.py` (+282), `runlog.py` (+71) |
| #19 | `fix/before-the-reveal-rows` | `src/volab/evaluators/__main__.py` (+14), `tests/test_preregistration_fence.py` (+431) |

Consequences, each a recorded decision rather than an accident: `tests/holdout_scan.py` is written
fresh instead of extracted out of `tests/test_daily.py` (R187); the leaderboard change is additive
and leaves the headline's wording alone (R186); the three writer CLIs are **not** migrated onto a
combined counter (R186); `agent/_runner.py` duplicates the daily runner's bounded tail rather than
extracting it (R195).

## Held, not forgotten

| item | why it is held | releases on |
|---|---|---|
| A sprint against the **real** storage root | it would move newly reported ledgers before the reveal — R139's question one subsystem over (R194). **Needs no key.** | a user decision |
| A live-LLM sprint | needs an API key only the user can provision | the key |
| A Postgres-backed `Checkpointer` | needs a DSN; not written, and not described as written (R190) | the DSN |
| The reveal's second count including both agent ledgers | the reveal is unbuilt, so there is nothing to assert against | Phase 6 |
| A `last_origin` on holdout comparison records | same | Phase 6, **before** any agent reads that file |

## Not this phase's, and why

- **Taking the reveal.** Phase 6's, and the user's trigger. Nothing here names `allow_holdout` or
  `VOLAB_HOLDOUT_UNLOCK`, and a scan now forbids the three new packages from doing so.
- **Moving a champion on promotion** (R188). The framework's own ordering puts the logged holdout
  check before promotion, and that check is Phase 6's.
- **Extracting the subprocess tail and the holdout scan** into shared helpers (R187, R195) — both
  blocked on PR #18 landing, both recorded as follow-ups rather than dropped.
- **Migrating the three writer CLIs onto `LedgerCounts`** (R186), blocked on PR #18 and PR #19.
- **A bandit supervisor, reflection memory, token tracing.** The framework puts those in Phase 6.
- **A dashboard or any UI.** Asked and declined in Phase 4; unchanged.

## Checklist

- [x] Design written and reviewed
- [x] Plan written
- [x] Decisions log opened at R181, with the collision that forced it recorded
- [ ] Every story meets its acceptance criteria
- [ ] Full suite passing, ruff and mypy clean
- [ ] Delivery audit run, board corrected where it and git disagree
- [ ] PR open, with the three remainder categories kept separate

## Risks

- **The agent ledgers are empty at merge**, because R194 holds the only run that would fill them.
  That is intended, and the PR must not present an empty ledger as a run that found nothing.
- **Textual conflict with PR #18** in `leaderboard/__init__.py` and `tests/test_leaderboard.py`.
  Both sides are additive, so the resolution should be mechanical; it is still a conflict.
- **A sibling session allocating R181+.** Claimed in the shared notes folder; PR #19's session was
  running when the claim was written.

## What the fences actually caught, recorded because a guard with no demonstrated bite is not evidence

- **P5-A2's store-reach fence caught the realistic two-hop plant.** A planted
  `src/volab/agent/_plant.py` holding `from volab.features.targets import HORIZONS` failed with
  `volab.agent._plant reaches the store through volab.agent._plant -> volab.features.targets ->
  volab.store (R192)`. A per-file scan passes that import; only the transitive walk sees it. The
  plant was deleted and never committed.
- **P5-A1's scan missed two spellings on first delivery**, both found by review and both closed:
  the parameter name passed as a string through `**{"allow_holdout": True}`, and a function
  *definition* `def f(allow_holdout=False)`, which is an `ast.arg` rather than a `Name`. An
  earlier round had already closed a third: reaching the environment variable through
  `volab.config.HOLDOUT_UNLOCK_ENV` rather than by its literal spelling.
- **P5-B2's fittability witness found two defects in the design it was given.** `VOCABULARY` still
  admitted `har_d_proxy`, `har_w_proxy` and `har_m_proxy` — the HAR base itself, which my spec
  failed to subtract because `FEATURE_COLUMNS` contains them; and `has_distribution` is constant
  on the real SPY table (one distinct value over 2,312 rows), so every proposal naming it is
  refused. The first was fixed; the second is kept deliberately, with a witness that the refusal
  is a clean `ModelSkip` naming the column, because constancy is a property of the window and not
  of the column.
- **Review found an R32 defect in P5-B1** — a docstring quoting a per-fit timing whose author had
  measured a different figure — and an inaccurate mechanism claim about how a null HAR column at
  the origin is handled. Both corrected.

## Where this stands at the first pull request, stated plainly

**Built, tested and committed:** both fences (A1, A2), the closed vocabulary with its gate and canary
payloads (B2), the `HarX` contestant (B1), the evaluation child with its two agent ledgers (C1), the
sprint event log and pending approval (C2), and the two ports — the LLM completer and the
checkpointer — each with a fake and neither needing a credential (D1, D2).

**Not built, and therefore not claimed:**

| story | what is missing |
|---|---|
| P5-C3 | `LedgerCounts`, the read-only ledger reader, and the leaderboard's added line. **So the leaderboard does not yet print the agent ledgers' counts**, which is the surface R183 promises. |
| P5-D3 | the subprocess-per-evaluation runner |
| P5-D4 | the six roles and the seven-step sprint machine |
| P5-D5 | the canary tests. The six payloads and their distinct refusal codes exist and are tested in `tests/test_agent_vocabulary.py`; what is missing is the test that an **accepted** canary voids a sprint. |
| P5-E1 | `python -m volab.promote` and its three refusals |
| P5-E2 | `python -m volab.agent` |
| P5-E3 | **the exit criterion.** No sprint runs end to end yet, so the framework's "one full weekly sprint runs end to end without touching the holdout" is **not met** by this pull request. |

The components a sprint is assembled from exist and are tested; the assembly does not. That is the
honest summary and the pull request says it in those words.

**The agent ledgers are empty**, and R205 says why: the only runs against the real store were two
measurements whose output was deleted. `data/trials.jsonl` stands at 414 lines and
`data/comparisons.jsonl` at 52 — the pre-registration's own counts, unmoved.

## Resumed, and the merged tree is verified

Branch merged up to `main` (`c207c59`) and pushed. **Gates on the merged tree: 2241 tests pass**,
`ruff check` clean, `ruff format` reports 130 files formatted, `mypy --strict` clean over 57 source
files. `data/trials.jsonl` is **414 lines** and `data/comparisons.jsonl` is **52**, checked by hand,
with `git status -- data/` clean.

An earlier revision of this section said the post-merge suite had not finished. It has, and the
figures above are from that run rather than from a story's own report.

**Landed since the PR opened:** P5-C3 (`35c5424`) and P5-D3 (`2618462`).

**Still to build:** P5-D4 (the keystone: the six roles and the seven-step sprint machine), then D5,
E1, E2 and E3. R206 is new and changes E3: the rehearsal **cannot** launch the real child against a
`tmp_path` store, so it must call `evaluate_proposal` with a hand-built `Settings` instead.

**Not done and not authorised:** auto-merge on PR #20. A peer session relayed that the user
authorised merge-when-green; a peer cannot carry that authorisation, and separately #20 declares its
own exit criterion unmet, so arming it would land a partial Phase 5 under an honest label. That
decision is the user's.
