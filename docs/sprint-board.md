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

## Sprint 4 — The feature table · CLOSED

| Story | Goal | Role | Depends on | Acceptance | State |
|---|---|---|---|---|---|
| US-4.1 | `build_features(panel, *, through)` — cutoff separate from panel extent | implementer + code-reviewer | 3.1, 3.2 | columns match spec §9; a missing session does not shorten a window; **asserts `ts` is monotonic ascending**; derives `log_return`; HAR columns carry `_proxy` | done `acc181a` + `dbf7ebf` + `aacb10f`, reviewed |

Reviewed: spec ✅, nothing Critical, two Important findings — both fixed and each verified by
mutation. A gap session's `NaT` timestamp collapsed on read, because `load_panel` dedupes on
`(symbol, ts)` and pandas treats `NaT` as equal, so every gap row merged into one and sorted
after every real row; gap rows now take a deterministic timestamp from the calendar. And the
perturbation covered 1 of 5 input groups, which let a `has_distribution` forward peek pass both
leakage tests — now one parametrised perturbation per input group, plus a combined one.

That fix forced a layering decision (`aacb10f`): `session_close_utc` lived in
`ingest/_frames.py`, so importing it dragged in `volab.store`, `duckdb` and `pyarrow` — the whole
Parquet stack loaded to compute a feature, and the feature engine depended on the ingestion
layer it has no business knowing about. Moved to `sessions.py`, where a pure calendar function
belongs. `volab.store`, `duckdb` and `volab.ingest` are now all absent from the feature engine's
import graph.

**US-4.1 also found the sharpest thing in the phase so far, and it corrects the spec.** Its mutation
check showed that spec §11's own mutation 1 — making `semivar_neg_proxy` peek forward —
*escaped* the perturbation test, because scaling future closes leaves the **sign** of every
future return unchanged, and semivariance keys on sign. The test only bit once the perturbation
became a decaying future path. §11 listed its perturbations as a menu; a sign-changing
perturbation is now a stated requirement, because following the old wording literally produced
exactly the vacuous test §3 names as an anti-goal. Verified independently: mutating
`realized.py` now fails 3 tests and passes clean on revert.

Interfaces US-4.2 must consume, chosen by US-4.1 where the brief was open:
`build_features(panel, *, through, calendar)`; `log_returns(close)` is public so targets reuse it
rather than deriving returns twice; `COLUMNS`, `FEATURE_COLUMNS`, `HAR_COLUMNS`, `WINDOWS`,
`IV_SERIES`, `FRED_SERIES`, `EXOGENOUS_SERIES`. The panel is the **three curated reads
concatenated long-form** — bars on `close`/`has_distribution`, VIX family on `close`, FRED on
`value` — with `_subject()` picking the symbol that is not a named exogenous series. `log_return`
is **not** an output column.
| US-4.2 | `python -m volab.features build` writing the table and the four target sets | implementer | 4.1 | real table on disk per symbol; **time a 4-horizon `load_panel`** against the §12 budget | done `b35b79a` |

Closed at 445 tests, gates green. **§12's missing second number is now measured, not extrapolated:**
the full build for all three symbols took **70.1s against a 5-minute budget**, reading 15,015
files and writing 34,398 (6,936 `har_daily` plus 27,462 targets); a `load_panel` over the four
target datasets took **4.07s against 30s**, about 7x headroom. Both recorded with their commands
and file counts, which the Sprint 3 audit had faulted the earlier gate entry for lacking. That
closes US-6.6.

On real data the SPY feature table has 2,312 rows from 2016-10-20 to 2025-12-31, with `iv_h1`
non-null for 912 of them — the VIX1D inception count, again.

| US-6.8 | Normalise `curated/vix_family`'s schema so one `load_panel` can read it | implementer | done `c7230fa` — one read returns 8,079 rows |

US-4.2 found that a single `load_panel` over the real `vix_family` **fails**: VIX carries OHLC and
VVIX only a close, and `store._scan` passes no `union_by_name`. Verified. The CLI works around it
by reading each series separately, with a test pinning that.

**Ruling: `_scan` stays strict; the schema gets normalised at curation instead.** Adding
`union_by_name := true` would silently fill absent columns with NULL — exactly the silent repair
this layer exists to refuse, and a bars file that lost `volume` to a parse bug would unify rather
than raise `SchemaError`. The actual defect is that a curated *dataset* has a symbol-dependent
schema. Raw is faithful to the vendor, so VVIX genuinely has two columns there; curated is
normalised, so padding VVIX's `open`/`high`/`low` with explicit nulls is consistent with the
raw-faithful / curated-typed split the spec already establishes rather than a violation of it.
Not blocking: the pipeline works per-series today.

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

## Sprint 5 — The leakage proof (the phase's exit criterion) · OPEN

| Story | Goal | Role | Depends on | Acceptance | State |
|---|---|---|---|---|---|
| US-5.1 | Point-in-time suite + AST forward-look scan | implementer + code-reviewer | 4.1 | perturbing post-`T₀` data leaves features byte-identical | in progress |

US-5.1 carries both spec corrections earlier stories forced — the perturbation must change the
sign structure of future returns **and** cover every input group — plus the AST scan exempting
`targets.py` as a whole file rather than by construct. It is dispatched with five mutations it
must demonstrate firing, including the two that escaped earlier suites: a sign-dependent peek
(`semivar_neg_proxy`) and a sign-blind one (`bipower_proxy`, which is `|r_t| · |r_{t-1}|`, so
only a magnitude change catches it). **If any mutation survives, that is the finding, and the
story reopens rather than being reported green.**
| US-5.2 | Mutation 1: `semivar_neg_proxy_d5` → `.shift(-1)`; record verbatim failure | controller | 5.1 | tests 1 **and** 4 both fire | done — 6 failed, both fired |
| US-5.3 | Mutation 2: full-sample statistic | controller | 5.1 | behavioural tests fire; **AST scan is blind** | done — 6 failed, scan passed 5 |

US-5.2 and US-5.3 are deliberately separate stories: they prove different tests, and a mutation
only one test catches means the other is too weak.

## Sprint 6 — Ship

| Story | Goal | Role | Depends on | State |
|---|---|---|---|---|
| US-6.1 | FRED backfill | controller | done `5ba4814` — 7,718 raw partitions; macro columns live |
| US-6.2 | Finish the decisions log | controller | 5.x | todo |
| US-6.3 | Final whole-branch review, strongest model, merge base **`origin/main`** | code-reviewer | 6.2 | todo |
| US-6.4 | Open the PR against main | controller | 6.3 | todo |

## Carried from the Sprint 3 delivery audit

| Story | Goal | Role | State |
|---|---|---|---|
| US-6.5 | Tests for `python -m volab.ingest` — the four verbs have none, so "done" rested on a manual run | implementer | done `6cd52a5`, 60 tests |
| US-6.9 | Two CLI defects US-6.5 found and correctly did not fix | implementer | done `cb1f28b`; `ParseRefused` follow-up approved |

US-6.5 could not get a RED, because the CLI already worked — so it proved its tests bite with
**10 in-process mutants** applied through a scratchpad pytest plugin, leaving `src/` untouched.
Each mutant failed 2–20 tests: dropped exit code, fetch stopping at the first failure, a missing
raw dataset no longer caught, curate reading past `dev_end`, `--end` silently accepted, unknown
`--source` doing nothing, backfill stages out of order, and `build-raw` fetching.

It also found that the obvious network guard would not work: `exploding_transport` is useless
here because `_fetch` catches `Exception`, so a network hit would become a green exit-1 test. It
used `pytest.fail` plus a socket block instead. That is the kind of thing only writing the test
reveals.

US-6.9, both found and left alone per instruction: a payload that fails to parse — FRED's
maintenance page included — prints `FAILED ValueError` rather than `REFUSED`, contradicting
`_fetch`'s own comment; and `curate --source nasdaq_dividends` creates no jobs, prints nothing
and exits 0, so asking to curate dividends returns silence and success.
| US-6.6 | Measure the **feature build** against §12's 5-minute budget. Only the `load_panel` half was measured; the build half was extrapolated, and §12 requires both | controller | done — 70.1s vs 5 min, in `b35b79a` |
| US-6.7 | Reconcile the decisions log: R8–R12, R23, R24 exist only in the gitignored ledger | controller | done — all 28 rulings now recorded |

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


## Resume state (2026-10-22, context at 90%)

Branch `phase-1-data-and-targets`, pushed. HEAD `5ba4814`. Gates clean; 508 passing excluding
`tests/test_point_in_time.py`, which a live agent is still refining after its commit `167c18e`.

**Done since the last resume note:** the README rewrite (`2538e0b`); US-5.1's fix round closing
the three forward-peek escapes (`167c18e`); both mutation proofs recorded (`3b90a1d`); the
older leakage comparisons made exact after a 1e-9 leak was shown to pass them (`0da1085`,
`65b465b`); all 29 rulings reconciled (`30df855`); and FRED ingested along with the three real
defects that were blocking it (`5ba4814`).

**FRED is no longer blocked, and was never purely an outage.** Three of my own defects were in
the way: a browser User-Agent that FRED tarpits while Nasdaq requires it; a spec claim that the
endpoint omits missing observations when it actually sends 444 empty-valued holiday rows; and
two observations sharing one knowable session because FRED's calendar and XNYS differ.

**Next, in order:**
1. US-5.1 re-review — owed, because it failed its first review.
2. US-6.8 `vix_family` schema, US-6.9 the two CLI defects — both dispatched.
3. US-6.2 final decisions-log pass, US-6.3 final whole-branch review (merge base `origin/main`),
   US-6.4 the PR.
