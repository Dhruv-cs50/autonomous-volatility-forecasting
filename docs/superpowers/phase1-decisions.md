# Phase 1 — decisions taken during execution

Phase 0's `phase0-decisions.md` is the model: every decision the spec and plan did not settle,
recorded with its reasoning and what it costs if wrong, because a decision made on someone's
behalf should be reviewable rather than invisible.

Phase 1 ran as a hybrid: independent review seats only where look-ahead could hide (targets, the
feature engine, the point-in-time suite), one batched seat for the three vendor fetchers, and the
config change, write path and curation implemented directly under the final whole-branch review.

Numbering is the order the decisions were made. R1–R19 below are the full set so far.

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

Branch `phase-1-data-and-targets`. 285 tests passing; `ruff check`, `ruff format --check` and
`mypy src` all clean; the import ratchet green with `ALLOWED` unchanged at `{"store.py"}`.

Complete: the split and `exogenous` key; the write path; session arithmetic; the fetchers,
payload archive and `build_raw`, reviewed with all three Important findings fixed. The live
backfill has written 16,500 raw partitions and 10 committed payloads (696 KB), FRED excepted.

Remaining: the scoped re-review of the fetcher fix diff; FRED once the outage clears; curation
and the CLI; targets; the feature engine; the point-in-time suite with both mutation proofs; and
this log's completion alongside the PR.
