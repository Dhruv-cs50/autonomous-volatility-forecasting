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

## Sprint 3 — Feature engine foundations · CLOSED

Both stories are independent: **disjoint file sets**, so they run concurrently.

| Story | Goal | Role | Files | Depends on | Acceptance | State |
|---|---|---|---|---|---|---|
| US-3.1 | Trailing realized measures and HAR components, on `pd.Series` | implementer | `features/realized.py`, `features/har.py`, `tests/test_realized.py` | — | hand-computed values match exactly; incomplete windows null, never partial | done `6169eff` + `6e6774d` |
| US-3.2 | Forward RV targets, `as_of` = h-th session after `ts` | implementer | `features/targets.py`, `tests/test_targets.py` | sessions | `as_of` exactly h sessions after `ts` across a holiday; off-by-one caught | done `9987e0a` + `11744ea` |
| US-3.3 | Return read timestamps in UTC whatever the host's timezone | controller | `store.py` | — | read-back dtype is `datetime64[us, UTC]` on any host | done `aad5d6f` |

Closed at 380 tests, gates green, reviewed: spec ✅ both, no Critical, no Important. Four of ten
minors fixed in a scoped round, six deferred to the final review.

US-3.3 was not planned. US-3.2 hit it while writing a test: DuckDB renders a timestamp in its
session timezone, so `load_panel` returned `datetime64[us, America/Los_Angeles]` here and would
return `datetime64[us, UTC]` on CI. Same instants, different dtype — a reproducibility defect in
a project whose claim rests on reproducibility, so it was fixed in the read path rather than
worked around per-test.

**Parallelism held, but the account of why was wrong.** Corrected after a delivery audit checked
the commit timestamps.

What is true: `6169eff` (US-3.1) and `9987e0a` (US-3.2) share no *story* files, so the pathspec
discipline did its job, and a `git add -N` step is needed because a pathspec commit refuses a
file git has never seen.

What was false: this board claimed the shared `features/__init__.py` "was created up front" by
the controller. Git says otherwise — `9987e0a` **created** it at 15:29:41 and the controller's
`6f92f34` *modified* it four seconds later. US-3.2 was already running when the file was created,
so creating it up front protected US-3.1 only. The claim was written from intent rather than from
the log, which is exactly the kind of thing an audit is for.

Both dispatched concurrently. `features/__init__.py` was created by the controller beforehand
(`6f92f34`) so neither story owns a shared file, and each commits with an explicit pathspec
rather than `git add`, because the git index is shared state two agents can otherwise collide on.

## Sprint 4 — The feature table

| Story | Goal | Role | Depends on | Acceptance | State |
|---|---|---|---|---|---|
| US-4.1 | `build_features(panel, *, through)` — cutoff separate from panel extent | implementer + code-reviewer | 3.1, 3.2 | columns match spec §9; a missing session does not shorten a window; **asserts `ts` is monotonic ascending**; derives `log_return`; HAR columns carry `_proxy` | todo |
| US-4.2 | `python -m volab.features build` writing the table and the four target sets | implementer | 4.1 | real table on disk per symbol; **time a 4-horizon `load_panel`** against the §12 budget | todo |

Carried into Sprint 4 from Sprint 3's review:

- **`build_features` must assert `ts` is monotonic ascending.** The realized measures are purely
  positional, so a descending panel would silently produce forward-looking features — and the
  Sprint 5 AST scan could not catch it, because the scan reads syntax and this is a property of
  the data. One assertion in the one place that owns ordering, not a check inside hot pure
  functions.
- **`log_return` has to be derived here.** Curated bars keep raw OHLCV and `has_distribution`;
  no return column exists yet. US-3.2's panel contract expects one.
- **Reuse `write_targets`** rather than re-implementing the per-`as_of` write. It already handles
  the `Timestamp`-key pitfall that would otherwise produce `date=...T00:00:00` partitions.
- **Settle `ingested_at` for targets**: currently taken from the row that completed the horizon,
  where the conservative choice is the max over the window. A no-op for a bulk backfill, which is
  why it was deferred rather than fixed.

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
| US-6.3 | Final whole-branch review, strongest model, merge base **`origin/main`** | code-reviewer | 6.2 | todo |
| US-6.4 | Open the PR against main | controller | 6.3 | todo |

## Carried from the Sprint 3 delivery audit

| Story | Goal | Role | State |
|---|---|---|---|
| US-6.5 | Tests for `python -m volab.ingest` — the four verbs have none, so "done" rested on a manual run | implementer | todo |
| US-6.6 | Measure the **feature build** against §12's 5-minute budget. Only the `load_panel` half was measured; the build half was extrapolated, and §12 requires both | controller | todo (needs 4.2) |
| US-6.7 | Reconcile the decisions log: R8–R12, R23, R24 exist only in the gitignored ledger | controller | todo |

Audit corrections already applied: the merge base is re-pointed to `origin/main` (it had moved to
`a4027d4`, making `399c4fd` stale); three committed `.DS_Store` files are untracked; R15's
promised comment, silently dropped, has landed; and the UTC test now sets DuckDB's session
timezone so it can actually fail — it previously could not go red on a UTC host like CI, which
made it a test that proved nothing there.

## Risks

- **US-6.1 is blocked externally.** Nothing before US-4.1 needs FRED; the `baa10y` feature column
  will be null until it lands. Retry at each sprint boundary.
- **Sprint 5 is the phase's exit criterion.** If US-5.2 shows test 1 passing while only the AST
  scan fires, test 1 is too weak and US-5.1 reopens rather than the mutation being accepted.
