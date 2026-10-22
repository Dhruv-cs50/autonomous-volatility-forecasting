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
- **R7** The final whole-branch review uses the merge base `origin/main`, since the spec commit
  is on local main but unpushed and will appear in the PR. **Superseded in place:** this ruling
  originally pinned the literal `399c4fd`, and main has since moved to `a4027d4` (the spec commit
  was pushed). Pinning a sha rather than a ref was the error; the ruling now names the ref.
  Verified at the final review: `git merge-base --is-ancestor origin/main HEAD` succeeds and
  `git log HEAD..origin/main` is empty, so the branch is a clean fast-forward with no divergence.

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
- FRED's `fredgraph.csv` does not send the `.` marker its other interfaces use — zero
  occurrences across `BAA10Y` (10,642 rows) and `DGS10` (16,904). The guard against an
  unparseable value stays, because a silent `0.0` in a credit spread is a real hazard, but it is
  labelled defensive rather than observed. **This bullet was itself wrong and is corrected
  below.** It concluded from the absent `.` that the endpoint *omits* missing observations
  altogether. The live backfill showed it sends them as rows with an **empty** value field: 444
  of `BAA10Y`'s 10,645, every one a market holiday. Counting the absence of one marker is not
  evidence about the shape that replaced it — see "US-6.1: FRED landed" below.

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

## External blocker: FRED (resolved — see "US-6.1: FRED landed" below)

Recorded while it was live, and kept because the guard proof below is worth keeping. The
blockage was also never purely an outage: three defects of mine were in the way, and they are
recorded in the US-6.1 section.

`fred.stlouisfed.org` is serving an HTML "We'll be back soon!" maintenance page, so the three
FRED series are not yet ingested. Nothing before Task 5's `curate_fred` depends on them.

This became an unplanned end-to-end test of the parse-before-archive guard, and it held: feeding
`fetch_source` the real maintenance page raised
`ValueError: … header is not 'observation_date,<SERIES_ID>', got ['<!doctype html>']` and
archived nothing. Before that guard the page would have been gzipped into an append-only,
committed archive and would have broken every future FRED replay permanently.

## Where execution stands (as of the Sprint 3 boundary — superseded by the closing section)

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

## US-5.1 round 2: all three escapes closed, 16 mutations, none surviving

Commits `167c18e` and `808a239`. The file grew 66 → 100 tests; the repository stands at 608.

Each escape and what now catches it:

| Escape | Caught by |
|---|---|
| A — exogenous join on the observation stamp | 8 failures, incl. `[iv_levels]` and `[fred_levels]` on both variants |
| B — order-only peek ("was tomorrow's move bigger") | **exactly one** test, `[shrinking_returns-whole]` |
| C — back-fill across a gap | 7, every one `[-holed]` |
| D — the exogenous half of the holed panel, previously unmeasured | 5, every perturbation case `[-holed]` |

Three things from this round worth keeping, because each is a general lesson rather than a fix:

**All three escapes were fixture gaps, not missing assertions.** The suite's assertions were
sound; it simply could not *express* the shapes a leak needed. That reframes where to look next:
a reviewer should attack the panel builder, not the test bodies. Two shapes it still cannot
express, named by the implementer: more than one subject symbol (so cross-sectional peeks are
unreachable), and an exogenous lag that varies per release.

**Composition is weaker than its parts.** The all-at-once perturbation does **not** catch escape
B, because `null_closes` runs last and NaNs away the return structure the shrinking group relies
on. A combined perturbation is not a superset of its components, and escape B is caught by a
single parametrised case — remove that one group and it is live again.

**The implementer found a hole in its own fix.** Its first version lagged the exogenous `ts` by a
calendar day, which made escape A's detection depend on the cutoff being followed by a session —
a Friday or holiday eve and there would be no row to misread, so it would have passed silently on
a weekday accident. `ts` is now the previous *session's* midnight, a one-session gap whatever the
weekday, matching what `fred.py` actually writes.

Controller verification, independent of the report: `src/` clean, no commits of mine clobbered,
and the 1e-9 forward leak the implementer explicitly declined to claim on — **7 of its 100 tests
fire**, so the no-tolerance design holds at that magnitude.

Standing caveat, now measured twice: **the AST scan is evadable by construction.** Escape B and
the volume peek both spelled a forward look without `shift`, slice or `center`. It is a ratchet
against a careless spelling, not a proof. The perturbations are the defence, which is why their
coverage matters more than their strength.

## US-6.8: `vix_family` readable in one call (c7230fa)

A single `load_panel` over VIX, VIX9D, VIX1D and VVIX now returns **8,079 rows** (2,389 / 2,389 /
912 / 2,389). VVIX's `open`/`high`/`low` are genuine `float64` nulls, and **raw VVIX still carries
only `close`** — verified. Raw stays faithful to what Cboe publishes; curated is normalised. That
is the split the project already draws, which is why `union_by_name` was the wrong fix and this
was the right one.

The implementer went slightly past its brief in a way worth keeping: `_pad_to_ohlc` pads **only**
the exact close-only shape. A series that has *lost* a column — VIX arriving without `high` —
raises `SchemaError` instead of being padded, because padding that would be precisely the silent
repair R28 rejected. Padding an absence the vendor declares is normalisation; padding an absence
nobody expected is a repair. The distinction is the whole point and it drew the line correctly
without being told to.

No feature rebuild needed: features read only `close`, and no close value changed.

Carried forward: `features/__main__.py`'s per-series exogenous read was a workaround for this
defect and could now be a single call. Left alone deliberately — it also carries the
latest-observation-per-session collapse from the FRED work, so simplifying it is a real change
rather than a deletion, and it is not worth a regression risk today.


## US-6.9: the CLI's two defects, and a typed refusal (cb1f28b, cd46c45, and the base change)

A rejected payload now reports REFUSED rather than FAILED, and `curate --source nasdaq_dividends`
says where dividends are actually curated instead of exiting 0 in silence. 77 CLI tests, suite 634.

Two decisions worth recording.

**The implementer's `CURATED_WITH` mapping is better than the brief.** I asked it to fix the
dividends no-op; it added a mapping plus a `RuntimeError` for any source with no curation rule, so
a source added later cannot silently do nothing. It fixed the class rather than the instance.

**Ruling R30: `ParseRefused` derives from `VolabError` alone, not `(VolabError, ValueError)`.**
The implementer used the dual base to keep an out-of-scope test passing, flagged it, and asked —
correctly. Dropping it, for two reasons. The taxonomy's design property is that one clause catches
the whole data layer, and a second base weakens that. And the accidental-capture surface is
real rather than theoretical: `src/volab/` holds **eleven** `except ValueError` sites, including
the date parsers in `_frames.py` and the numeric guard in `fred.py`, so a `ParseRefused` passing
through any of them would be converted or swallowed. No live path does today — `fetch_source`
raises above all of them — but the hazard is one refactor away. The test that asserted
`pytest.raises(ValueError)` was pinning the old implementation detail; it now asserts the
contract. *Cost if wrong:* an external caller written against `except ValueError` stops catching
it, which is the point.

Accepted with the implementer's own caveat: `except Exception` around the parse means a genuine
parser bug also reports as REFUSED. The chained cause and the original exception name in the
printed line keep it traceable, and the alternative — enumerating the exception types a parser may
raise — would be wrong the first time a vendor changed shape.

## US-6.1: FRED landed (5ba4814), and the three defects of mine that were blocking it

The maintenance page above was real, but it was not the whole reason FRED took three attempts.
Three defects of my own were in the way, and each is a different species of the same error —
trusting a claim that had not been measured.

**1. The User-Agent was backwards per host.** Nasdaq's historical endpoint *refuses* a request
without a browser User-Agent; FRED *tarpits* one. Measured: the same FRED URL returns 168,568
bytes in 1.8s with no User-Agent or a curl-like one, and times out with a browser string. So the
User-Agent cannot be a single global, and `_headers_for(url)` now decides per host, sending FRED
no User-Agent at all rather than a fake one. A fake would be a guess about what the host wants;
urllib's own default is the honest statement of what this client is.

**2. The spec said the endpoint omits missing observations. It sends them empty.** 444 of
`BAA10Y`'s 10,645 rows carry an empty value field, every one a market holiday — MLK Day,
Presidents' Day, Good Friday, Memorial Day, Independence Day, Labor Day. The original probe had
counted occurrences of the `.` placeholder, found none, and concluded the rows were absent. They
were not; they had a different shape. **Counting the absence of one marker is not evidence about
what replaced it.** The parser now skips an empty field deliberately — an empty field is the
vendor stating there was no observation, which differs in kind from a value it cannot read — and
still raises on anything else. `fred.py`'s module docstring carried the wrong claim until the
US-6.2 pass, seventy lines above the comment recording the measurement that disproved it.

**3. Two observations can become knowable on the same session.** FRED's publication calendar and
XNYS are different calendars, so when the NYSE closes on a day FRED still publishes, the
preceding business day and that day both lag onto the same next session. Measured over the full
curated dev window this happens **7 times across 3 dates**: Good Friday 2021-04-02 and
2023-04-07, and 2025-01-09, the national day of mourning. Each collides for `DGS10` and
`T10Y2Y`; only the mourning day also collides for `BAA10Y`, because `BAA10Y` is one of the series
FRED leaves empty on Good Friday. The recurring case is therefore Good Friday and the one-off is
the freak — the reverse of the first record of this, which cited only 2025-01-09 and made the
collapse read like near-dead code. It is 7 rows in 7,118. `build_features` refuses a panel carrying two rows for one session, correctly, so the
collapse lives in `features/__main__.py` instead: keep the **latest**, because standing on Friday
the newest value known is Thursday's, and the earlier one is superseded rather than lost. The
engine's guard stays strict on purpose — curated data legitimately holds both observations, and
choosing between them is the caller's decision, not something the engine should quietly absorb.

**Ruling R31: FRED's `as_of` is a conservative assumption, not an observation.** The keyless
`fredgraph.csv` endpoint serves only the latest vintage, so a release's true publication instant
is not recoverable from it. Every series therefore takes a lag in **sessions**, defaulting to
one, configured by editing `FRED_LAG_SESSIONS` rather than by a config key. The asymmetry decides
the direction: too long a lag only withholds information from a model, too short a lag leaks it.
*Cost if wrong:* a series whose true lag exceeds one session is read earlier than it was
knowable, which is a leak this harness would not catch, because the harness can only test that
the code honours the `as_of` it was given — not that the `as_of` is true. **This is the one
point-in-time claim in Phase 1 that rests on an assumption rather than on a measurement, and it
must be stated as such wherever the data is described.**

## US-6.2: the final accuracy pass

Checking every claim in this log against the code and the disk as they now stand. Seven records
were wrong, stale, or recorded nowhere but the board. Four are corrected in place above (R7's
merge base, the FRED-omission bullet, the FRED blocker heading, the "where execution stands"
heading); the FRED work and R31 are the section above; the remaining two are here.

### The mutation evidence was measured against a 66-test file

The recorded "6 failed, 60 passed" for both mutations is accurate for `f4e4c92`, which is where
it was measured and where it is labelled. But the file that merges has **100 tests**, so those
numbers understate the suite that ships. Both mutations were re-run against a pristine
`git archive` copy of the final HEAD, in a scratch directory, `src/` in the repository untouched.
Baseline there and after each revert: **100 passed**.

**Mutation 1 — a forward offset.** `realized.py`, `squared = returns**2` →
`squared = returns.shift(-1) ** 2`:

```
7 failed, 93 passed in 3.45s
```

```
FAILED test_perturbing_one_input_group_...byte_identical[returns_sign_and_scale-whole]
FAILED test_perturbing_one_input_group_...byte_identical[shrinking_returns-whole]
FAILED test_perturbing_one_input_group_...byte_identical[shuffled_closes-whole]
FAILED test_perturbing_one_input_group_...byte_identical[null_closes-whole]
FAILED test_perturbing_every_input_group_at_once_leaves_the_table_byte_identical[whole]
FAILED test_the_panels_extent_does_not_change_the_table[whole]
FAILED test_no_feature_module_looks_forward[realized.py]
```

`-k looks_forward` alone: `1 failed, 4 passed`, naming the construct —
`AssertionError: realized.py: ['offsets with shift(-1)']; every window in this package is trailing`.

**Mutation 2 — a full-sample statistic.** `har.py`, the per-window divisor replaced by
`(returns**2).mean()` over the whole series:

```
14 failed, 86 passed in 3.42s
```

Both the `-whole` and `-holed` panel variants of all four perturbation groups, the combined
perturbation, the panel-extent test and the delete-and-append test. `-k looks_forward` alone:
**`5 passed, 95 deselected`** — the scan is still blind to it, as it must be, because there is no
forward syntax to find. The pair's complementarity, which is the actual claim, is unchanged and
now measured on the file that ships.

One asymmetry worth recording rather than smoothing over: mutation 1 fires only on the `-whole`
variants, while mutation 2 fires on both. The `-holed` panel NaNs away part of the return
structure a one-session offset perturbs, so the holed fixture is weaker against a forward offset
and stronger against nothing in particular. It was added for back-fill escapes, which it does
catch. Seven failures is ample detection; the point is that fixture variants are not uniformly
stronger, and the combined perturbation already showed the same thing.

### The suite does not cover the features CLI's own exogenous read

`build_features` is called directly by the point-in-time suite, deliberately — routing through
`load_panel` would strip every post-cutoff row and leave nothing to perturb. The consequence,
recorded here because it is a real coverage boundary rather than a defect: `features/__main__.py`
performs its own per-series exogenous read **and** the latest-observation-per-session collapse
described in the US-6.1 section, and no perturbation in the suite reaches either. A forward peek
planted in that collapse — taking the newest observation regardless of knowability rather than
the newest *knowable* one — would be invisible to `tests/test_point_in_time.py`. The collapse's
correctness today rests on reading it and on `tests/test_features_cli.py`, not on the
perturbation harness. **Stated rather than discovered later**, which is the standard the rest of
this phase has been held to.

### Verified at HEAD, not carried forward

| Claim | Measured |
|---|---|
| Test suite | **634 passed** |
| `ruff check` / `ruff format --check` / `mypy src` | clean; 47 files formatted, 20 source files |
| Import ratchet | green, `ALLOWED` unchanged at `{"store.py"}` |
| Raw partitions | **24,218** — `cboe_vix` 8,919, `fred` 7,718, `nasdaq_bars` 7,539, `nasdaq_dividends` 42 |
| Curated partitions | **22,126** — `bars_daily` 6,936, `fred_daily` 7,111, `vix_family` 8,079 |
| Feature + target files | **34,398** — `har_daily` 6,936; targets 6,933 / 6,921 / 6,870 / 6,738 |
| Merge base | `origin/main` = `a4027d4`, a strict ancestor of HEAD; no divergence |

The earlier "16,500 raw / 15,015 curated" figures were correct **before FRED** and are left in
place where they are dated; FRED added 7,718 raw and 7,111 curated.

`has_distribution` is served for **QQQ only** — 42 dividend partitions. SPY and IWM return no
dividend rows from the Nasdaq endpoint at all, so for those two symbols the unadjusted-close bias
is present and documented rather than detected. That split is in the README's limitations and is
recorded here because it is the kind of partial coverage that reads as full coverage later.

## US-5.1 re-review: all eight findings ADDRESSED, and TWO NEW ESCAPES — the story reopens again

All eight earlier findings verified ADDRESSED, each mutant reconstructed independently with
failure sets matching the implementer's report exactly. No new breakage; 66 → 100 test ids with
nothing dropped. The Friday/holiday-eve fix is confirmed: escape A is caught at **70 of 70** swept
cutoffs, 59 of them followed by a calendar gap.

**It also corrected my own brief.** I told it `src/` was unchanged in `f4e4c92..808a239`. Wrong —
the range holds **8 commits, not 2**, and `5ba4814` (the FRED work) changed `fred.py`,
`features/__main__.py` and `_http.py`. It verified instead of relying on me. The engine under
test is not stale, but `_latest_observation_per_session` is production code the PIT file never
executes.

### E1 — a band-threshold peek on the future return. Passes 607 of 608 tests.

In `semivariance_proxy`, build `later` as escape B did (`drop`/`set_axis`/`reindex`, so no
`shift`, no slice, no `center`) and OR a band into the sign mask:
`wrong_sign | ((later > 0.03) & (later < 0.10))`. Measured: **100/100 PIT pass**, 43/43
`test_features_build`, 19/19 `test_features_cli`, AST scan silent. The only catcher anywhere is
one hand-written unit value test whose returns happen to land in the band — incidental, which is
this phase's signature failure mode for the fifth time.

**Why it works, and why enumeration cannot fix it:** at the cutoff the suite can only present
`|r_next|` from the finite set `{0.0013, 0.0039, 0.009, 0.027, 0.2199, 0.513, NaN}`. **Any
predicate thresholded into a gap of that set is invariant under every perturbation.** Adding
more perturbation groups raises the attacker's cost without closing the class; only **dense or
property-based magnitudes** would. And a 3–10% next-day move is a credible crash-eve signal, so
this is not contrived.

### E2 — a memoised full-sample statistic. 0 of 100 PIT failures.

A module-level `_SCALE` filled on the first `build_features` call and reused defeats
`test_the_panels_extent_does_not_change_the_table` — the file's **only** detector of the
full-sample class, the one that caught round 1's M6 — because both tables being compared are
built in the same process. **Class: any cross-call state — a global, a dict, `functools.cache`.
No in-process comparison can see it.** Latent until Phase 2's walk-forward, which runs in one
process.

### Three other results worth keeping

- **A cross-sectional peek is unreachable in production, not merely inexpressible**: `build.py`'s
  `_subject` raises `SchemaError` on more than one non-exogenous symbol. But the PIT file never
  mentions a second subject, so if `_subject` were relaxed, nothing in the exit-criterion file
  would notice.
- **The all-at-once perturbation is redundant.** Across all ten mutants, none is caught *only* by
  it. Its failure on escape B has two causes, not the one claimed: `null_closes` NaNing the
  structure, and `shuffled_closes` restoring a large boundary return that gives the base answer.
- **The reviewer corrected its own measurement.** Its first sweep suggested B was blind at 28/70;
  that was an artifact of measuring a single group. As the union of all groups it is 70/70, with
  which detector fires rotating by cutoff.

### Standing judgement on the AST scan

Keep it as a cheap ratchet against a careless spelling; **never cite it as a defence.** Measured
evasions now number **four** — escape B, the volume peek, E1, and a back-fill spelled
invisibly. On the report's own table only one mutant is scan-only, and that one mutates the scan
itself.

### What this means for the PR

The exit criterion holds against every *syntactic* forward peek and every leak the perturbation
images can distinguish. It does **not** hold against a predicate constant across those images,
nor against cross-call state. **The PR must say so.** Claiming the suite catches leakage in
general would be exactly the overclaim this project was built to refuse — and we would be making
it in the document that argues the harness is honest.
