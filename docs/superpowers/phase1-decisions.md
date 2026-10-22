# Phase 1 — decisions taken during execution

Phase 0's `phase0-decisions.md` is the model: every decision the spec and plan did not settle,
recorded with its reasoning and what it costs if wrong, because a decision made on someone's
behalf should be reviewable rather than invisible.

Phase 1 ran as a hybrid: independent review seats only where look-ahead could hide (targets, the
feature engine, the point-in-time suite), one batched seat for the three vendor fetchers, and the
config change, write path and curation implemented directly under the final whole-branch review.

Numbering is the order the decisions were made. **R1–R28 are all recorded here**, reconciled
from the session's working ledger under story US-6.7 after a delivery audit found eleven of them
missing and an earlier version of this line claiming to hold the full set while the body already
ran past it. Visibly partial would have been better than falsely complete; complete is better
still.

## Pre-flight rulings

- **R1** No separate git worktree; work stays on `phase-1-data-and-targets` in this checkout. A
  peer session shares the folder and a worktree would force the shared checkout off the branch.
  *Cost if wrong:* the peer sees the working tree change under it.
- **R2** `build_raw` **skips** an existing raw partition rather than calling `write_raw` blindly.
  `write_raw` refuses to overwrite, so a backfill of ~2,500 partitions interrupted halfway could
  otherwise never be resumed. *Cost if wrong:* a corrupt partition from an earlier run is never
  replaced; a rebuild needs it deleted by hand.
- **R3** `build_raw` groups by `as_of` and calls `write_raw` once per partition. Forced by
  `write_raw`, which validates that every row's `as_of` equals its single partition argument.
- **R4** The realized-measure functions take and return `pd.Series`, importing no numpy. numpy is
  only a transitive dependency of pandas and absent from `pyproject.toml`; declaring it would
  touch `uv.lock` against the no-new-dependencies constraint, and pandas does everything needed.
- **R5** The duplicated `test_load_panel_at_dev_end_hides_unrealised_targets` belongs to the
  point-in-time suite, per spec §11, not to the targets task.
- **R6** Session arithmetic also exposes a memoised `get_calendar(settings)`; four tasks need a
  calendar and none owned building one, and XNYS construction is slow enough to matter.
- **R7** The final whole-branch review uses merge base `399c4fd` (origin/main), since the spec
  commit is on local main but unpushed and will appear in the PR.

## Execution shape

- **R8** Task 1 was already dispatched to a subagent when the execution method changed to
  hybrid; it was allowed to land with **no separate review seat**. The code is the same either
  way, the hybrid saving is the review seat rather than the implementer, and killing a running
  agent wastes work already paid for. Its diff was verified by the controller instead.
  *Cost if wrong:* one unreviewed config diff reaches the final review, which is where hybrid
  intends it to be reviewed anyway.
- **R9** Seat allocation. Native, no review seat: the split and `exogenous` key, the write path,
  session arithmetic, curation, the docs. One batched dispatch plus one reviewer: the three
  fetchers. Own implementer and reviewer: targets, the feature engine, the point-in-time suite.
  Review seats go where look-ahead can hide and nowhere else. Session arithmetic and curation
  went native although nobody named them, because neither is such a place — four calendar
  wrappers, and a parse-loudly rule.
- **R10** The fetcher batch implemented and tested against committed fixtures only, with **no
  network**; the controller ran the live backfill and the §12 measurement gate. The gate carries
  a stop-and-escalate decision about abandoning a storage invariant, which is not a subagent's
  call to make.
- **R11** Targets were dispatched **before** the feature-engine seat, because the feature CLI
  writes target datasets through `HORIZONS` and `build_targets`, so that interface had to exist
  first. The grouping that named three feature files as one seat did not fix their order against
  targets.
- **R12** `next_session_after` and `nth_session_after` must accept a **non-session** `day`, and
  the plan's tests already pinned it. Verified empirically first:
  `exchange_calendars`' own `next_session` **raises** `NotSessionError` on a non-session
  argument, so a naive wrapper could not satisfy the test. Callers hand these helpers vendor
  observation dates, which are not all sessions. *Cost if wrong:* a FRED value dated on a
  holiday would crash ingestion instead of being lagged.

## Write path

- **R13** `store._write` judges emptiness **before** the `ts` dtype check, reversing the plan's
  stated order. An empty frame's columns carry pandas' `float64` default, so the planned order
  reported an empty frame as "not timezone-aware" — naming the wrong fault. *Cost if wrong:*
  none; both raise `SchemaError`.
- **R14** `store._write` rejects a `datetime` passed as `as_of`. `datetime` subclasses `date`, so
  mypy accepts one and the path builders would emit `date=2023-06-15T12:00:00`, a partition
  `parse_path` rejects and Hive misreads. **This closes a Phase 0 deferred minor** — recorded
  there as "`_partition` accepts a `datetime` … the builder should fail first". Its deferral
  premise was that the defect was writer-side only with no writers; the write path retired that
  premise in the same commit that made it reachable, and the bad partition was verified by probe
  before the guard was written rather than taken on the old note's word.
- **R15** The overwrite check stays **after** validation. A schema fault is a code defect; a path
  collision is often expected operational state, such as a resumed backfill. Reporting the defect
  first names the thing that must be fixed for anything to proceed. Safe either way: validation
  is read-only and nothing reaches disk until both checks pass, because the write goes to a temp
  file and is `os.replace`d.
- **R17** Raw `ts` is 16:00 America/New_York in UTC, not the calendar's true close. Correct for
  every regular session, three hours late on an early close. Nothing in Phase 1 keys on intraday
  time — `as_of` is the point-in-time axis — so it affects no feature.

## Ingestion and the archive

- **R16** Task 4 was implemented directly after its dispatched implementer died on a rate limit
  having written nothing, but **kept its review seat**, since the independent review was the half
  of that allocation carrying the value. The review returned three Important findings, all real.
- **R18** Each payload gets a `.meta.json` sidecar carrying `source`, `symbol`, `fetched_at` and
  `source_url`. The body file must stay byte-exact, and a bars URL embeds the date range it asked
  for, which cannot be recovered afterwards.
- **R19** `archive_payload` skips a body byte-identical to the newest payload already held.
  Nasdaq sits outside `ARCHIVE_MONTHLY` because each payload holds sessions later ones lose, but
  two fetches on the same day see the *same* window, and a committed archive pays for a redundant
  copy in git history forever. Compared by content, so the rule needs no per-source calendar.
  Immediately justified: 7 of the first 17 payloads were byte-identical duplicates — 41% of the
  archive on day one — pruned with no loss.

## The read path, naming, and the leakage proof

- **R23** `load_panel` normalises `ts` and `ingested_at` to **UTC** before returning. DuckDB
  renders a TIMESTAMP WITH TIME ZONE in its session timezone, which defaults to the host's, so
  the same read returned `datetime64[us, America/Los_Angeles]` locally and would return
  `datetime64[us, UTC]` on CI. The instants were always correct; the **dtype** was not
  reproducible, and in this project that is the same class of defect. A naive column is left
  alone rather than localised, because assuming naive means UTC is the silent repair this layer
  refuses. Found by the targets story, which had worked around it per-test.
- **R24** Spec §9 contradicted itself: the naming rule requires every daily-return estimator to
  carry `_proxy`, and the same section's table listed bare `har_d`/`har_w`/`har_m`. They are
  means of the squared-daily-return proxy and carry exactly the confusion the rule prevents.
  **Persisted column names take the suffix; `har_components()`'s dict keys stay bare**, because
  those are internal to a function whose docstring says what they are made of, and the confusion
  happens where a column is read back months later.
- **R25** Spec §11's perturbation must **change the sign structure** of future returns, not only
  their magnitudes — a requirement, not one option in a menu. Scaling future closes leaves log
  returns almost unchanged, because they are scale-invariant, so only the boundary return moves.
  §11's own mutation 1 (`semivar_neg_proxy` peeking forward) therefore **escaped** the
  perturbation test until it became a decaying path. An implementer following the old wording
  literally would have produced exactly the vacuous test §3 names as an anti-goal, on the
  phase's own exit criterion.
- **R26** The perturbation must also **cover every input group**. US-4.1's covered one of five —
  the bars' `close` and FRED's `value` — so a forward peek planted in `has_distribution`
  (`flag | flag.shift(-3)`) passed **both** leakage tests, caught only incidentally by a value
  test. Verified by mutation, not inferred. A perturbation that misses an input misses every
  forward peek that input could host, however strong it is elsewhere. **Sprint 5 would have
  inherited the blind spot wholesale.**
- **R27** `session_close_utc` moved from `ingest/_frames.py` to `sessions.py`. Importing it from
  the ingestion package executed `from volab.store import write_raw`, so importing the feature
  engine pulled in `volab.store`, `duckdb` **and** `pyarrow` — the whole Parquet stack loaded to
  compute a feature. The deeper reason holds regardless of any test: the feature engine was
  importing from the ingestion layer, and that dependency runs the wrong way. Features consume
  curated data and have no business knowing how it got there.
- **R28** `store._scan` stays **strict** — no `union_by_name`. A single `load_panel` over the
  real `vix_family` fails, because VIX carries OHLC and VVIX only a close, but `union_by_name`
  would silently fill absent columns with NULL: precisely the silent repair this layer refuses,
  and a bars file that lost `volume` to a parse bug would unify instead of raising `SchemaError`.
  The real defect is a curated *dataset* with a symbol-dependent schema, so the fix belongs at
  curation (story US-6.8), where raw-faithful becomes curated-normalised.

## Two factual errors corrected in the spec and plan

Both were mine, and a parser built on either would have targeted a field that does not exist.
Found by probing live payloads before dispatching the parser.

- Nasdaq bars carry **no currency symbol**: prices arrive as bare strings (`"766.48"`) and the
  thousands separators are in **volume** (`"38,680,060"`). The `$` is on dividend amounts, a
  different endpoint. The spec asserted `$`-prefixed prices.
- FRED's `fredgraph.csv` **omits** missing observations rather than sending the `.` marker its
  other interfaces use — zero occurrences across `BAA10Y` (10,642 rows) and `DGS10` (16,904). The
  guard against an unparseable value stays, because a silent `0.0` in a credit spread is a real
  hazard, but it is now labelled defensive rather than observed.

## Measurement gate (spec §12): passed

| Step | Result |
|---|---|
| fetch + archive, SPY bars | 4.82s, 57,441 bytes gzipped |
| `build_raw` | 4.38s, 2,513 partitions |
| raw files on disk | 2,513 files, 17.6 MB, mean 7,012 B |
| **`load_panel(end=dev_end)`** | **0.49s**, 2,312 rows |

Budget was 30s for a one-symbol read, so roughly 60× headroom. Extrapolated to the ~25,600 paths
a full feature build touches, about 5s against a 5-minute budget. The ~88,900-file figure in §12
is correct arithmetic but not a real performance problem. **No escalation; the
one-`as_of`-per-partition invariant stays and lever (b) is unused.**

## Corrections made to the spec

The trailing window drifted during execution, exactly as §15 predicted. Store inception is
**2016-10-20**, not 2016-10-19, and dev-window bar sessions are **2,312**, not 2,313. Two days of
delay cost one session, permanently. The split decision is unchanged: 2,312 against a
200-session holdout is the same trade Dhruv approved. §4, §5 and §15 are corrected, and §5 now
states which of its columns drift and which are fixed.

## Curation decisions

- **R20** `curate` normalises `as_of` to `dt.date` before grouping. The store is asymmetric: a
  fetcher writes `as_of` as `date` objects but `load_panel` returns `datetime64`, so grouping a
  read frame yields `Timestamp` keys and `write_curated` would have built
  `date=2023-06-16T00:00:00`. R14's guard refused it rather than letting a malformed partition
  through, which is how this was found. **Worth a reviewer's attention as a store-contract wart:
  what you write is not the dtype you read back.**
- **R21** The non-session policy **splits by tier**. Bars **reject** a row dated outside an XNYS
  session; exogenous series **drop and report** it. Cboe genuinely publishes VIX on NYSE
  holidays — 33 rows since 2016-07-01 with real values, while VIX9D, VIX1D and VVIX have none.
  A bar dated when the market was shut means the source disagrees with the calendar and would
  corrupt the return series. An exogenous value on a closed day has no session to attach to and
  can never enter a session-indexed window, so refusing outright would discard 9,298 usable VIX
  rows over 33 unusable ones. Dropping is safe point-in-time: it removes rows rather than
  inventing them, on dates no feature is evaluated on. *Cost if wrong:* if those holiday values
  ever mattered, they are absent; the count is printed so a vendor change surfaces.
- **R22** Numeric stripping is narrow **per field** — `,` for bars, `$` for dividend amounts —
  rather than a blanket punctuation strip. Bars carry no currency symbol at all, so a `$` in a
  price means the vendor changed format and must fail loudly.

## Real-store curation counts

Bars 2,312 partitions per symbol. VIX, VIX9D and VVIX 2,389 each, covering the 77-session warmup
before the bars floor. **VIX1D 912 — exactly the dev-session count computed in §5 when arguing to
move the split**, which is the arithmetic behind that decision confirmed against real data.

## External blocker: FRED

`fred.stlouisfed.org` is serving an HTML "We'll be back soon!" maintenance page, so the three
FRED series are not yet ingested. Nothing before Task 5's `curate_fred` depends on them.

This became an unplanned end-to-end test of the parse-before-archive guard, and it held: feeding
`fetch_source` the real maintenance page raised
`ValueError: … header is not 'observation_date,<SERIES_ID>', got ['<!doctype html>']` and
archived nothing. Before that guard the page would have been gzipped into an append-only,
committed archive and would have broken every future FRED replay permanently.

## Where execution stands

Branch `phase-1-data-and-targets`. **382 tests passing**; `ruff check`, `ruff format --check` and
`mypy src` all clean; the import ratchet green with `ALLOWED` unchanged at `{"store.py"}`.

Complete: the split and `exogenous` key; the write path; session arithmetic; the fetchers,
payload archive and `build_raw` (reviewed, three Important findings fixed, re-review clean);
curation and the ingest CLI; trailing realized measures and HAR; forward targets; and the UTC
read normalisation. The live backfill has written 16,500 raw partitions, 15,015 curated
partitions and 10 committed payloads (696 KB), FRED excepted.

Remaining: `build_features` and the features CLI; the point-in-time suite with both mutation
proofs; FRED once the outage clears; the three stories the Sprint 3 audit added (US-6.5 CLI
tests, US-6.6 the feature-build measurement, US-6.7 this log's reconciliation); the final
whole-branch review; and the PR.

A delivery audit at the Sprint 3 boundary found this section stale by four stories and two
sprints, and found five further records wrong or unevidenced. What it corrected:

- The Sprint 3 concurrency account was written from intent rather than the log. `9987e0a`
  created `features/__init__.py`; the controller's `6f92f34` modified it four seconds later. The
  pathspec discipline worked, but "created up front" protected only the second story dispatched.
- **The UTC test could not fail on a UTC host.** It asserted a dtype that is already UTC when
  the session timezone is UTC, so on CI it would have passed with the fix reverted. It now sets
  DuckDB's session timezone explicitly and is parametrised over `Asia/Kolkata`,
  `America/Los_Angeles` and `UTC`; reverting the normalisation now fails 2 of 3 cases, verified.
  A test that cannot fail is the exact defect this project exists to catch.
- R15 promised a one-line comment at the overwrite check and it was never written. It has landed.
- The final review's merge base `399c4fd` was stale: `origin/main` has moved to `a4027d4`. The
  board now points at `origin/main`.
- Three `.DS_Store` files had been committed by careless `git add` and are now untracked.

## Measurement gate (spec §12), second number: the full feature build

Recorded 2026-10-21 on the development laptop (macOS, Apple silicon, repo on iCloud-synced
`~/Desktop`), `HEAD` before this story `aacb10f`, warm `.venv`. FRED absent (see above), so the
`baa10y`/`dgs10`/`t10y2y` columns are null and no FRED file is read.

Reproduce, from the repository root:

```bash
time uv run --locked python -m volab.features build
```

| Measure | Result | Budget |
|---|---|---|
| **Full build, all three symbols** (read + features + 4 target horizons + all writes) | **70.1s** in-process (`built in`), **72.8s** wall incl. `uv run` start | < 5 min |
| Files read | 6,936 bars (3 × 2,312) + 8,079 VIX family (3 × 2,389 + 912 for VIX1D) = 15,015 | |
| Files written | 34,398: 6,936 `har_daily` (3 × 2,312) + 27,462 targets (h1 6,933, h5 6,921, h22 6,870, h66 6,738) | |

About 4x headroom, and the dominant cost is the 34,398 single-row writes, not the reads. **Passed;
no escalation, and §12 lever (b) is unused.**

`load_panel` over the four target datasets (all three symbols, `end=dev_end`, `Tier.FEATURES`),
via this script, run as `uv run --locked python <script>`:

```python
import time
from volab.config import load_settings
from volab.features.targets import HORIZONS, targets_dataset
from volab.paths import Tier
from volab.store import load_panel

settings = load_settings()
begin = time.perf_counter()
for horizon in HORIZONS:
    name = targets_dataset(horizon)
    started = time.perf_counter()
    frame = load_panel(name, end=settings.splits.dev_end, tier=Tier.FEATURES, settings=settings)
    print(f"{name}: {len(frame)} rows in {time.perf_counter() - started:.2f}s")
print(f"all four: {time.perf_counter() - begin:.2f}s")
```

| Dataset | Files | Rows | Time |
|---|---|---|---|
| `targets_rv_h1` | 6,933 | 6,933 | 0.95s |
| `targets_rv_h5` | 6,921 | 6,921 | 0.84s |
| `targets_rv_h22` | 6,870 | 6,870 | 0.79s |
| `targets_rv_h66` | 6,738 | 6,738 | 1.49s |
| **All four** | **27,462** | **27,462** | **4.07s** (6.1s wall incl. `uv run`) |

Against the 30s single-`load_panel` budget this is roughly 7x headroom, for 12x the file count of
the one-symbol bars read (2,312 files, 0.49s) that the first gate entry recorded.

Two things this run found, neither of which changed the verdict:

- **The curated VIX family is not one schema.** VIX carries open/high/low beside its close; VVIX
  carries only a close. A single `load_panel` over the four series fails inside DuckDB with
  `schema mismatch in glob: column "open" ... could not be found`, which the first real run hit.
  The CLI reads each exogenous series separately. `store._scan` has no `union_by_name`; that is a
  `store.py` matter and is reported rather than changed here.
- The first attempt at the real run took 15m48s of wall-clock with 1.6s of CPU: that was `uv run`
  stalling on the iCloud-synced `.venv`, the known cold-start quirk, and it failed before reaching
  the build. The 72.8s above is a warm run.


## Mutation evidence: the exit criterion, proven

Both mutations were run against a **pristine `git archive` copy of `f4e4c92`** in a scratch
directory, so the repository's own `src/` was never modified. Baseline there: 66 passed.
Reverted and re-verified at 66 passed after each.

### Mutation 1 — a forward offset (US-5.2)

`src/volab/features/realized.py`, in `semivariance_proxy`:

```python
-    squared = returns**2
+    squared = returns.shift(-1) ** 2   # peek one session forward
```

`uv run --locked pytest tests/test_point_in_time.py -q` → **6 failed, 60 passed**:

```
FAILED test_perturbing_one_input_group_after_the_cutoff_leaves_the_table_byte_identical[returns_sign_and_scale]
FAILED test_perturbing_one_input_group_after_the_cutoff_leaves_the_table_byte_identical[shuffled_closes]
FAILED test_perturbing_one_input_group_after_the_cutoff_leaves_the_table_byte_identical[null_closes]
FAILED test_perturbing_every_input_group_at_once_leaves_the_table_byte_identical
FAILED test_the_panels_extent_does_not_change_the_table
FAILED test_no_feature_module_looks_forward[realized.py]
```

with the scan naming the construct exactly:

```
AssertionError: realized.py: ['offsets with shift(-1)']; every window in this package is trailing
```

Acceptance required the behavioural test **and** the AST scan to fire. Both did.

### Mutation 2 — a full-sample statistic (US-5.3)

`src/volab/features/har.py`, replacing the per-window divisor with a whole-series one:

```python
-    return {name: rv_proxy(returns, window=window) / window for name, window in HAR_WINDOWS.items()}
+    full_sample = (returns**2).mean()
+    return {name: rv_proxy(returns, window=window) / full_sample for ...}
```

→ **6 failed, 60 passed**: the three perturbation variants, the combined perturbation, the
panel-extent test, and the delete-and-append test.

And the result that matters: **the AST scan does not catch it** — `-k looks_forward` gives
`5 passed`. There is no forward syntax to find.

### What the pair proves

| Mutation | Behavioural tests | AST scan |
|---|---|---|
| forward offset | catch it | catches it |
| full-sample statistic | catch it | **blind to it** |

The two layers are complementary, not redundant. A suite with only the structural scan would
miss every leak of the second kind — which is the commoner kind in real research code, because
it looks like ordinary normalisation. A suite with only the behavioural tests would depend
entirely on the perturbation being well chosen, and this phase has already shown twice that a
badly chosen one lets a real peek through.

This also corrected the spec a fourth time. §11 predicted test 1 would *not* catch mutation 2,
"no individual row changed" — wrong, because a full-sample statistic includes the perturbed
future rows. The prediction was never run before being written down.


## A fourth escape, found while fixing a Minor

The review of the point-in-time suite noted in passing that the older leakage tests in
`tests/test_features_build.py` compared with `pd.testing.assert_frame_equal`'s **default
tolerance**, so they would accept a rounding-sized leak. It was filed Minor. Verifying the fix
turned it into something sharper.

Those five comparisons now carry `check_exact=True`. The measurement that justifies it, a
forward leak **ten thousand times smaller** than the tolerance they used to allow:

```python
data[f"rv_proxy_d{window}"] += returns.shift(-1).fillna(0.0) * 1e-9
```

| Comparison | Result |
|---|---|
| **with** `check_exact=True` | **3 failed** — growing-panel, `[close]` perturbation, combined |
| **without** it (the code as it stood) | **43 passed** — the leak walks straight through |
| both reverted | 43 passed |

So the tests meant to catch look-ahead would have accepted a genuine forward-looking feature,
provided it was small. That is the worst shape for this defect to take: a leak of 1e-9 does not
look like a bug, it looks like floating-point noise, and the test that should refuse it instead
confirmed the code was clean.

**Ruling R29: a leakage test may carry no tolerance at all.** Not a tight one — none. Every
comparison in a test whose purpose is to detect look-ahead compares exactly, because the size of
a leak is evidence about how it was introduced and not about whether it matters. *Cost if wrong:*
a genuine floating-point non-determinism in the engine would now fail these tests rather than
pass them, which is information we would want.

Worth noting how this was found: not by review — the review filed it Minor, correctly on the
evidence it had — but by mutating the fix to check it did what the commit message claimed. The
phase's record on this is now four for four: every leakage gap was found by running something,
and none by reading.
