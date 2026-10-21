# Phase 1 sprint board

Orchestrated by the session controller, which acts as the delivery manager. Specialist roles are
defined in `.claude/agents/`. Sizing is **scope, not wall-clock**: a story is one dispatch with no
fix round; a sprint closes before the next opens.

Tracked in git deliberately — the SDD ledger holding the same state lives in a gitignored
workspace, and a plan `git clean` erases is not a plan.

## Sprint 1 — Data contract · CLOSED

| Story | Goal | Role | State |
|---|---|---|---|
| US-1.1 | Move the split to 2025-12-31; add the `exogenous` symbol class | implementer | done `b82df9b` |
| US-1.2 | The sanctioned write path in `store.py`, raw immutable | controller | done `1c9c312` |
| US-1.3 | Session arithmetic tolerant of non-session dates | controller | done `5228080` |

Closed: 245 tests, gates green. US-1.2 and US-1.3 were taken natively under the final review
rather than given review seats — neither is a place look-ahead can hide.

## Sprint 2 — Ingestion · CLOSED

| Story | Goal | Role | State |
|---|---|---|---|
| US-2.1 | Three keyless fetchers, payload archive, `build_raw` | implementer + code-reviewer | done `37396c6` |
| US-2.2 | Fix the review's 3 Important findings | controller | done `4192f65` |
| US-2.3 | Content dedupe; commit the vendor archive | controller | done `688f803` |
| US-2.4 | Live backfill + the §12 measurement gate | controller | done — gate passed, 0.49s vs 30s |
| US-2.5 | Curation + the `python -m volab.ingest` CLI | controller | done `c900d7a` |

Closed: 304 tests, gates green, 16,500 raw partitions, 10 committed payloads.
Re-review verdicted all six findings ADDRESSED.

## Sprint 3 — Feature engine foundations · OPEN

Both stories are independent: **disjoint file sets**, so they run concurrently.

| Story | Goal | Role | Files | Depends on | Acceptance | State |
|---|---|---|---|---|---|---|
| US-3.1 | Trailing realized measures and HAR components, on `pd.Series` | implementer | `features/realized.py`, `features/har.py`, `tests/test_realized.py` | — | hand-computed values match exactly; incomplete windows null, never partial | in progress |
| US-3.2 | Forward RV targets, `as_of` = h-th session after `ts` | implementer | `features/targets.py`, `tests/test_targets.py` | sessions | `as_of` exactly h sessions after `ts` across a holiday; off-by-one caught | in progress |

## Sprint 4 — The feature table

| Story | Goal | Role | Depends on | Acceptance | State |
|---|---|---|---|---|---|
| US-4.1 | `build_features(panel, *, through)` — cutoff separate from panel extent | implementer + code-reviewer | 3.1, 3.2 | columns match spec §9; a missing session does not shorten a window | todo |
| US-4.2 | `python -m volab.features build` writing the table and the four target sets | implementer | 4.1 | real table on disk per symbol | todo |

## Sprint 5 — The leakage proof (the phase's exit criterion)

| Story | Goal | Role | Depends on | Acceptance | State |
|---|---|---|---|---|---|
| US-5.1 | Point-in-time suite + AST forward-look scan | implementer + code-reviewer | 4.1 | perturbing post-`T₀` data leaves features byte-identical | todo |
| US-5.2 | Mutation 1: `semivar_neg_proxy_d5` → `.shift(-1)`; record verbatim failure | controller | 5.1 | tests 1 **and** 4 both fire | todo |
| US-5.3 | Mutation 2: trailing `.rolling(22).mean()` → full-sample `.mean()` | controller | 5.1 | test 2 fires; test 1 does **not** | todo |

US-5.2 and US-5.3 are deliberately separate stories: they prove different tests, and a mutation
only one test catches means the other is too weak.

## Sprint 6 — Ship

| Story | Goal | Role | Depends on | State |
|---|---|---|---|---|
| US-6.1 | FRED backfill | controller | **blocked** — fred.stlouisfed.org is serving a maintenance page | blocked |
| US-6.2 | Finish the decisions log | controller | 5.x | todo |
| US-6.3 | Final whole-branch review, strongest model, merge base `399c4fd` | code-reviewer | 6.2 | todo |
| US-6.4 | Open the PR against main | controller | 6.3 | todo |

## Risks

- **US-6.1 is blocked externally.** Nothing before US-4.1 needs FRED; the `baa10y` feature column
  will be null until it lands. Retry at each sprint boundary.
- **Sprint 5 is the phase's exit criterion.** If US-5.2 shows test 1 passing while only the AST
  scan fires, test 1 is too weak and US-5.1 reopens rather than the mutation being accepted.
