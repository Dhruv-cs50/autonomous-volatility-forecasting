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
| F | close out: review, audit, README, PR | **done** — PR #20 merged into `main` as merge commit `0fd5096` |

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
| P5-D5 | The canary bites, and an accepted canary voids the sprint | implementer | 45m | D4 | **closed within D4**, plus `07107d4` — `tests/test_agent_sprint.py` asserts `voided_by_canary`, one `canary_accepted` event and no further gating. The planned `tests/test_agent_canary.py` was never created; the tests live in `test_agent_sprint.py`, `test_agent_vocabulary.py` and `test_agent_cli.py`. The **non-zero exit** R189 requires was silently missing from the CLI until the delivery audit found it |
| P5-E1 | `python -m volab.promote` and its three refusals | implementer | 1h | C2, D4 | done (`d709148`) |
| P5-E2 | `python -m volab.agent`: `sprint` and `show` | implementer | 45m | D4 | done (`cb9803f`, hardened `f4b01fa`) |
| P5-E3 | The exit criterion: a full sprint end to end, witnessed | implementer | 1h | E1, E2 | done (`ce8ee90`) |
| P5-F1 | Delivery audit: the board against git | delivery-auditor | 30m | E3 | done — found two defects, both fixed (`07107d4`) |
| P5-F2 | README status, the board's close, the PR | orchestrator | 30m | F1 | done — PR #20 rewritten, merged as `0fd5096` |

Waves ran **serially** after the 8 GB constraint arrived (one subagent at a time); before it, up to
five ran in parallel, which is what overloaded the machine. See the memory note on serial agents.

## Where this stands, stated plainly

**Every story is built, tested and committed.** Sprints A to E are done, and the phase has been
through one independent review and one delivery audit. Each found real defects, and each was fixed
test-first:

| fix | what it was | commit |
|---|---|---|
| review round A | the git dirty flag polluted itself: every child after the first in a sprint stamped `git_dirty=true` because the first child's append to the tracked agent ledger dirtied the tree (R70); a tampered proposal file crashed the child instead of being refused; exit 6 was not wired; a line past `dev_end` was silently dropped in two places | `f3e14c3` |
| review round B | a **mechanical** prompt-inventory witness (R208 had held only by reading the code), a snapshot pin on the vocabulary so a new feature column cannot widen it silently, the rehearsal's missing check on Phase 5's own `load_panel` call, and docstrings that claimed more than their code | `4c6f9eb` |
| delivery audit | an accepted canary exited **0** at the CLI, so the gate failing looked like success to a shell caller; and R208's narrowed claim was itself an overclaim | `07107d4` |

**CI.** Green on the merged head `84bf0c3`, both the push and the pull-request runs: **2,730
passed**, ruff and mypy clean. GitHub merged only after both checks passed on that head.

**The exit criterion, in exactly the words the delivery audit settled.** One scripted sprint, using
a fake LLM and one fresh-interpreter evaluation over a synthetic one-symbol store, runs from start to
`awaiting_approval`, and every `load_panel` call observed in the evaluating process carried
`end = dev_end` with the unlock flag unset (`tests/test_agent_rehearsal.py`; non-vacuity asserted, and
a deliberately bad `end` makes it fail). That shows the machinery runs end to end without requesting
a date past `dev_end`. It does **not** show behaviour on real data or with a live model, it does not
exercise the real child entry point (`python -m volab.candidate`), and it does not exercise
promotion. So the framework's criterion is met **in that rehearsal sense, and only in that sense**.

**The agent ledgers are empty at merge.** `data/trials.jsonl` is 414 lines and
`data/comparisons.jsonl` 52, the pre-registration's own counts, unmoved (R205).

**PR #20 is merged** (`0fd5096`, 2026-11-11), on the user's own instruction once the phase was
whole. Earlier relays from other sessions to merge were not acted on as authorisation. It merged as a
merge commit, not a squash, so the branch shas this board and the decisions log cite stay reachable
from `main`.

## Held, not forgotten

| item | why it is held | releases on |
|---|---|---|
| A sprint against the **real** storage root | it would move newly reported ledgers before the reveal — R139's question one subsystem over (R194). **Needs no key.** | a user decision |
| A live-LLM sprint | needs an API key only the user can provision | the key |
| A Postgres-backed `Checkpointer` | needs a DSN; not written, and not described as written (R190) | the DSN |
| The reveal's second count including both agent ledgers | the reveal is unbuilt, so there is nothing to assert against | Phase 6 |
| A `last_origin` on holdout comparison records | same | Phase 6, **before** any agent reads that file |

## Follow-ups with no owner yet

Recorded in the decisions log. **All eight are resolved** (R215, R216). Two were resolved by deciding
*not* to do what was first planned, because the plan would have weakened a guard. Two items remain
open deliberately, as noted in the table.

| follow-up | source | now |
|---|---|---|
| Migrate the writer CLIs onto `LedgerCounts` and assert the import ban | R186 | **done** (`d62a3c2`): four callers, one the grep had missed; ban asserted, derived |
| Share the holdout-key derivation | R187 | **done** (`ae66985`): derivation shared, the two matchers deliberately kept apart |
| The subprocess bounded tail | R195 | **done, not extracted** (`5d3c9df`): the loops diverged after #18 and the Phase 4 probe plants into one; coupled by behaviour instead (R215) |
| Make `volab.counts` store-free | R212 | **done** (`e75f5db`): store-free; the CLI's `_count_all` copy dropped (`_git_state` must stay: it copies the store itself); R186's ban tightened to the definition alone |
| Phase 4's writer guard and bare-name collisions | R213 | **done differently** (`485499c`): `(module, name)` would have weakened the guard, so the matcher is unchanged and each violation now names the module that caused it (R216) |
| Audit the suite for `is`-based assertions over bound methods | R202 | **done by audit**: 40 test and 2 source candidates classified, zero hazards, no ratchet, with the reason given (R216) |
| Remove the dead `completed` value from `SprintResult.outcome` | R209 | **done** (`d0e7d47`) |
| A crash between `awaiting_approval` events and `complete` duplicates them, so `promote` refuses and the candidate is stuck | R214 | **done** (`a2a603b`): `finish` is now idempotent on resume |
| `show` prints `trials + agent_trials`, which will include holdout lines once Phase 6 writes them | R214 | revisit at the reveal |
| `agent.ledger.read_trials` is unused | R214 | remove or keep unused |

## Not this phase's, and why

- **Taking the reveal.** Phase 6's, and the user's trigger. Nothing here names `allow_holdout` or
  `VOLAB_HOLDOUT_UNLOCK`, and a scan forbids the three new packages from doing so.
- **Moving a champion on promotion** (R188). The framework's own ordering puts the logged holdout
  check before promotion, and that check is Phase 6's.
- **A bandit supervisor, reflection memory, token tracing.** The framework puts those in Phase 6.
- **A dashboard or any UI.** Asked and declined in Phase 4; unchanged.

## Checklist

- [x] Design written and reviewed
- [x] Plan written
- [x] Decisions log opened at R181, with the collision that forced it recorded
- [x] Every story built and committed, with its acceptance criteria evidenced (delivery audit)
- [x] Independent review run; its findings fixed (`f3e14c3`, `4c6f9eb`) or recorded (R214)
- [x] Delivery audit run, and the board corrected where it and git disagreed — this revision
- [x] CI green on the merged head — `84bf0c3`, 2,730 passed
- [x] PR #20 rewritten to say what is now true, and merged (`0fd5096`)

## After the merge: the live sprints

Branch `agent/first-real-sprint`, cut from `main` at `60da98f` (PR #25's merge, R224).

| ID | Story | Owner | Depends on | Acceptance check | State |
|---|---|---|---|---|---|
| P5-L1 | First live sprint on the real store | orchestrator | the user's go, R224 | sprint completes; `trials.jsonl` and `comparisons.jsonl` byte-identical; agent ledgers committed | done (`61d2da0`): `no_candidate`, every origin singular |
| P5-L2 | Root cause of the 250-of-250 refusal | orchestrator | L1 | reproduced in-process, cause stated with evidence | done: identities in `features/realized.py` (R225) |
| P5-L3 | The gate refuses identity-group combinations; canaries stay single-defect | orchestrator | L2 | observed payload refused as `collinear_regressors`; fit_ols and rank witnesses on engine-built columns; agent tests, ruff, mypy green | done (`f7ef947`, `b18ea29`, `e531638`) |
| P5-L4 | Keep the first refusal reason in the proposal event | — | — | "no usable MCS" can no longer hide why | todo (follow-up, R225) |
| P5-L5 | Second live sprint on the real store | orchestrator | L3 merged, the user's go | sprint completes; real ledgers byte-identical | failed in the Researcher step, exit 1, nothing written; real ledgers byte-identical (R226) |
| P5-L6 | Refuse a reply that did not finish; size the cap from measured replies | orchestrator | L5 | cut-off, refusal and absent stop reasons refused with the reason named; each role finishes under the cap; agent tests, ruff, mypy green | done (`6c5be53`) |
| P5-L7 | `invented_numbers` stops flagging window digits inside feature names | — | — | sprint 1's report would validate | todo (follow-up, R226) |
| P5-L8 | Rerun the second sprint on L6, fresh id | orchestrator | L6, the user's go, main free of the PR #22 hold | as L5 | done (`5865a8e`): `2026-11-13T122201Z` on `833a2ee`, `awaiting_approval`; real ledgers byte-identical; 12 agent trials, 6 comparisons |
| P5-L9 | Stop identical proposals within a sprint | orchestrator | L8 | one configuration is evaluated once per sprint, not once per slot | done (`d85ffd6`, R227): each slot sees earlier emissions; the hub refuses a repeat as `repeated_proposal`, after the canary check |
| P5-L11 | Refuse a configuration already run in an earlier sprint | — | — | a later sprint cannot re-run L8's configuration | todo (follow-up, R227) |
| P5-L10 | Decide whether "in the 10% MCS" alone makes a candidate promotable | the user | — | a ruling in the decisions log | todo (decision): L8's candidates are in the MCS with `har_levels` (p 0.89); DM p 0.90; SPA finds nothing beating `har_levels` |

## What the fences actually caught, recorded because a guard with no demonstrated bite is not evidence

- **The store-reach fence caught the realistic two-hop plant**, `from volab.features.targets import
  HORIZONS`, which a per-file scan passes. It later caught `volab.counts` reaching the store through
  `volab.walkforward` — a transitive reach nobody had planned for (R212).
- **The unlock scan missed three spellings across two review rounds**, all closed: the variable via
  the config constant, the parameter via `**{"allow_holdout": True}`, and a `def f(allow_holdout=…)`.
- **The fittability witness found two defects in my own design** (R185's amendment, R199).
- **The real-layout fixture turned 14 tests red** against a sprint that read its ledger from
  `data/data/` (R211), including the leak fix's own integrity witness, which had been checking an
  empty file.
- **A Phase 4 guard and this phase met on `main` and failed every CI run** through a bare-name
  collision on `_append` (R213), a false positive, proven in memory before anything changed.

## Lessons the phase paid for

- **A hand-picked test subset reports on what it picked** (R211, R213): twice, a merge's seam check
  omitted the one file that broke.
- **Narrowing a claim is itself a claim** (R208): the first narrowing repeated the error it corrected.
- **A count is a channel** (R208): whenever a reader reports how much it filtered, check who can see it.
- **Tests that agree with each other can disagree with the repository** (R211).
