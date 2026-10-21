# Phase 1 — Data and Targets

Status: design, approved 2026-10-20. Supersedes nothing; builds on
`2026-10-19-phase0-skeleton-design.md`.

Framework row: "1. Data and targets — Ingestion for bars, VIX family and FRED; RV,
semivariance and jump features; point-in-time tests." Exit criterion: **features are unchanged
when post-t data is perturbed.**


## 1. Purpose

Phase 0 built the refusal: `load_panel()` will not show development code a row it should not
see. That guard is only worth having if something real flows through it. Phase 1 supplies the
something real — raw vendor data, a curated panel, a feature table — and then proves the
guard's premise holds end to end, by perturbing the future and showing the past does not move.

The phase's centre of gravity is not the feature list. It is the point-in-time test. The
project's whole claim is that a deterministic harness catches leakage that statistical
corrections miss, and a leakage test nobody has seen fail is not evidence. So the exit
criterion is discharged by **mutation**: deliberately make one feature peek forward, record
that the test fails, revert, and publish that evidence.


## 2. Scope

### In scope

- `src/volab/ingest/` — fetch and write immutable raw Parquet for daily bars, the Cboe VIX
  family and FRED macro series; derive the curated tier from raw.
- A sanctioned **write** path, in `store.py` (section 7).
- `src/volab/features/` — realized measures, semivariance and jump proxies, HAR lags,
  bipower proxy, horizon-matched implied vol, all from trailing windows only.
- Forward realized-variance **targets**, stored so the store's own filter hides unrealised
  ones (section 10).
- `tests/test_point_in_time.py` — the exit criterion, plus a structural guard against the
  class of defect, proven by mutation.
- A faithful raw tier plus a replayable vendor-payload archive, so a rebuild survives Nasdaq's
  trailing window (section 13).
- A `has_distribution` flag on `curated/bars_daily`, because dividend drops bias one specific
  feature in one specific direction (section 6a).
- Moving the dev/holdout boundary (section 5), the one config change this phase makes.
- One additive config key, `exogenous`, and the matching additive change to `load_panel`'s
  symbol check (section 8).

### Out of scope

- Any model, forecast, loss or leaderboard. That is Phase 2.
- Intraday bars. Not obtainable free (section 4); the ingestion layer is shaped so an
  intraday dataset drops in later without touching the feature engine's contract.
- The options/IV engine (Phase 3), the scheduler (Phase 4), the agent (Phase 5).
- Reading anything past the new `dev_end`. The two-key unlock is not touched and not used.
- Regime-switching probabilities and news/attention features.

### Exit criteria

1. `python -m volab.ingest backfill` runs from a clean checkout with no credentials and
   writes raw Parquet for every source, each file carrying the four mandatory columns with
   correct `as_of`. CI verifies this against committed **synthetic** fixtures through the
   injected transport, never against the live network (section 13). The live run happens once
   by hand and its output is recorded in `phase1-decisions.md`.
2. A dated feature table exists per symbol in `features/`.
3. `tests/test_point_in_time.py` passes, and a recorded mutation shows it failing when a
   feature is made to peek forward.
4. `ruff check`, `ruff format --check`, `mypy src`, `pytest` all green.


## 3. Non-goals and anti-goals

**Non-goals.** Beating HAR. Completeness of the framework's input table. Any claim that the
feature set is state of the art.

**Anti-goals** — outcomes that make this phase a failure even if it technically works:

- A point-in-time test that passes because it tests nothing. Hence the mutation requirement.
- A daily-return estimator presented as if it were an intraday one. Negative semivariance and
  bipower variation are defined on intraday returns; computed on daily returns they are
  different, weaker quantities. Every such column is named `*_proxy_*` and documented as a
  proxy. Nothing in this phase is called "realized semivariance" or "bipower variation"
  without that qualification.
- Silently narrowed scope. The absence of intraday data is stated in the spec, in the module
  docstrings and in the PR, not discovered later by a reader of the code.
- A feature engine that reaches around `load_panel`.


## 4. Data sources

Every span below was obtained by fetching the endpoint on **2026-10-19**, not read from
documentation. All three working sources need **no API key** and no account.

| Dataset | Endpoint | Verified span | Grain |
|---|---|---|---|
| Daily bars, SPY/QQQ/IWM | `api.nasdaq.com/api/quote/{sym}/historical?assetclass=etf` | a trailing **2,513 sessions**; 2016-10-19 → 2026-10-19 when first probed, 2016-10-20 → 2026-10-20 two days later | daily OHLCV |
| VIX | `cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv` | 1990-01-02 → 2026-10-19 (9297) | daily OHLC |
| VIX9D | …`/VIX9D_History.csv` | 2011-01-04 → 2026-10-19 (3971) | daily OHLC |
| VIX1D | …`/VIX1D_History.csv` | **2022-05-13** → 2026-10-19 (1112) | daily OHLC |
| VVIX | …`/VVIX_History.csv` | 2006-03-06 → 2026-10-19 (5128) | daily close |
| FRED series | `fred.stlouisfed.org/graph/fredgraph.csv?id={ID}` | `BAA10Y` 1986-01-02 → 2026-10-16 (10642) | daily |

`BAA10Y` (Moody's Baa corporate yield minus the 10-year Treasury) is the default/credit spread
the research ranks highest for equity volatility. `DGS10` and `T10Y2Y` are ingested alongside
it as cheap context.

### Only Nasdaq's window is trailing, and that decides how much to ingest

Cboe and FRED serve their **full** history on every request — VIX back to 1990, `BAA10Y` back
to 1986 — and will still serve it next year. Only Nasdaq's is a trailing 10-year window that
loses a session a day. The urgency in section 15 therefore applies to bars alone, and there is
no reason to hoard VIX and FRED history that no feature can use.

**Ingestion is scoped to the bars window plus a 66-session warmup**: from 2016-07-01, the 66th
session before the bars floor, so every `n = 66` window at the first bar session is complete.
That is ~2,579 sessions per exogenous series rather than up to 10,642, and it roughly halves
the store's file count (section 12). Their deeper history stays one fetch away if a later phase
wants it.

Nasdaq's historical endpoint requires a browser `User-Agent` header; without one it refuses.
It is the site's own backend, undocumented but public and keyless. This is recorded as a
supply risk in section 15.

### Sources rejected, with the reason

- **Yahoo / yfinance.** `query1` and `query2` both return a hard `429 Too Many Requests` from
  the development host, and the `fc.yahoo.com` cookie bootstrap returns `404`, so the
  cookie/crumb flow cannot even begin. Separately, its data is licensed for personal use only,
  which the framework document already flags.
- **Stooq.** Serves a JavaScript proof-of-work browser challenge instead of CSV.
- **Alpha Vantage.** Advertises 1-minute intraday back to 2000-01 via the `month` parameter,
  but its own documentation marks intraday a **premium** endpoint; the free tier returns recent
  trading days only. This was the sole deep-history free-key candidate.
- **Tiingo.** `403` without a key.
- **Massive.** Declined by Dhruv; the $29/mo tier is explicitly out of scope. Its free tier is
  a *trailing* 2-year minute window, which on today's data falls almost entirely inside the
  sealed holdout and is therefore useless for development regardless of price.
- **Polygon / Twelve Data.** Both need a key and both give short, drifting windows (Polygon a
  trailing 2 years at 5 req/min; Twelve Data does not state free intraday depth). Held as the
  future intraday option (section 12), not used now.

### The consequence, stated plainly

**No free source, keyed or keyless, provides multi-year intraday bars.** Therefore Phase 1
computes realized variance from **daily returns**, which is the documented fallback the phase
brief anticipated. Three of the framework's ranked inputs are affected:

- *Intraday realized variance* (ranked first) — **not available.** Replaced by a
  squared-daily-return proxy.
- *Negative semivariance and signed jumps* (ranked second) — **not available in the
  Patton–Sheppard sense.** Daily-return analogues are computed and named as proxies.
- *Horizon-matched implied vol* (ranked third) — **fully available**, from Cboe, with deep
  history. With intraday gone this is now the phase's strongest real input, which is why the
  split change in section 5 is worth making.


## 5. The dev/holdout boundary moves

### The change

```yaml
splits:
  dev_end: "2025-12-31"        # was 2023-12-31
  holdout_start: "2026-01-02"  # was 2024-01-02
```

Approved by Dhruv on 2026-10-20. `dev_end` was an arbitrary pick during Phase 0, and the
Phase 0 spec's own risk table reserved the right to move it once, in Phase 1, before any score
exists that the choice could influence. That window is open now and closes the moment Phase 2
produces a number.

### Why these dates

Bar counts are as of the 2016-10-20 floor; the window is trailing, so the first column drifts
down by one for every session that passes before the backfill runs (see section 15). VIX1D's 912
and the holdout's 200 are fixed, because neither source is trailing.

| `dev_end` | bar sessions in dev | VIX1D sessions in dev | holdout sessions (at 2026-10-19) |
|---|---|---|---|
| 2023-12-31 (old) | 1811 | 410 | 702 |
| 2025-06-30 | 2185 | 784 | 328 |
| **2025-12-31 (chosen)** | **2312** | **912** | **200** |
| 2026-03-31 | 2374 | 973 | 139 |
| 2026-06-30 | 2436 | 1035 | 77 |

The original reason for moving the boundary — fitting a free trailing *intraday* window inside
the dev window — turned out void, because no such window exists. One independent reason
survives and decides it: **VIX1D begins 2022-05-13**, and with intraday RV unavailable,
horizon-matched implied vol is the phase's best remaining input rather than its third-ranked
one. The old boundary left VIX1D just 410 dev sessions. The new one gives 912, a 2.2x gain,
plus 502 extra bar sessions.

The trade runs the right way because of where the statistics live: the framework puts the DM
tests and the Model Confidence Set in the **dev** window, and the holdout is a *single* logged
confirmatory check at Phase 6. Dev-window length is therefore where statistical power actually
matters, which is what makes trading holdout sessions for VIX1D history correct rather than
merely convenient. 2026-06-30 was considered and rejected: it buys only 123 more bar sessions
than the chosen date while cutting the holdout to 77 sessions, too thin to be the one number
the final write-up rests on.

The 200-session holdout **accrues** as the project runs, since `holdout_start` is pinned and
data keeps arriving; by Phase 6 it should be 300–400 sessions.

### Rule 4 and pinning

`config.py`'s rule 4 forbids any XNYS session falling strictly between `dev_end` and
`holdout_start`. 2026-01-01 is a New Year's Day holiday, so the gap contains **zero** sessions
— verified against `exchange_calendars`. The pair also keeps Phase 0's own Dec-31/Jan-02
convention. Committed boundary tests catch a one-day error in either direction.

**Both dates are pinned absolute literals. Nothing in this project may compute a split
boundary from `today()`, `now()` or a trailing offset.** A boundary that moves on its own is a
boundary nobody can reason about, and this project's entire claim rests on the split being
trustworthy. The builder must not add a "rolling dev_end" convenience, in this phase or any
later one.

### Known limitation to record, not solve

At the 22- and 66-day horizons a 200-session holdout yields very few non-overlapping blocks, so
the final confirmatory check will be **weak at the long horizons**. Phase 6 must report this
rather than discover it. It is a property of the split, accepted deliberately.

### Test fallout

Only `test_repo_default_config_loads` asserts the repository's own dates (two lines).
`test_config.py` otherwise drives its self-contained `VALID` fixture, which keeps the old dates
and still exercises the Sunday-`dev_end` case meaningfully — leave it alone.
`tests/conftest.py`'s `synthetic_store` pins `dev_end=2023-12-31`, which is now *earlier* than
the repository's and therefore still accepted by `load_panel` step 0, which refuses only a
*later* boundary. If any other test breaks, fix the test, never the rule.


## 6. Store layout and `as_of` semantics

### Datasets

```
raw/nasdaq_bars/{SPY,QQQ,IWM}/date=<as_of>/part-<stamp>.parquet
raw/cboe_vix/{VIX,VIX9D,VIX1D,VVIX}/date=<as_of>/part-<stamp>.parquet
raw/fred/{BAA10Y,DGS10,T10Y2Y}/date=<as_of>/part-<stamp>.parquet

curated/bars_daily/{SPY,QQQ,IWM}/date=<as_of>/part.parquet
curated/vix_family/{VIX,VIX9D,VIX1D,VVIX}/date=<as_of>/part.parquet
curated/fred_daily/{BAA10Y,DGS10,T10Y2Y}/date=<as_of>/part.parquet

features/har_daily/{SPY,QQQ,IWM}/date=<as_of>/part.parquet
features/targets_rv_h{1,5,22,66}/{SPY,QQQ,IWM}/date=<as_of>/part.parquet
```

The raw tier mirrors the vendor's natural grain: a Cboe file lives under `symbol=VIX` because
that is what Cboe published. `paths.raw_path` applies `validate_segment` but imposes no
universe membership, so this needs nothing new. Reading those files back through `load_panel`
does, which section 8 handles.

### `as_of` is the date a value first became knowable

This is the column the store partitions on and filters by, and getting it wrong is the one
mistake this phase exists to prevent.

| Source | `ts` | `as_of` | Reasoning |
|---|---|---|---|
| Daily bars | session close, 16:00 ET as UTC | the session date `D` | OHLCV for `D` is complete at `D`'s close |
| VIX family | session close, 16:00 ET as UTC | the session date `D` | the index close for `D` is published that day |
| FRED | observation date `D`, 00:00 UTC | the **next XNYS session strictly after `D`** | deliberately conservative; see below |

FRED's publication lag is real and varies by series: Treasury yields land the same afternoon,
while Moody's corporate yields lag further. Rather than model each release calendar, every FRED
series takes a conservative one-session lag. A lag that is too long can only ever *withhold*
information from the model; a lag that is too short leaks. The asymmetry decides it.

The per-series lags live in a **module-level dict in `ingest/fred.py`**, mapping series id to a
session count with a default of 1 — not in `config/base.yaml`. `config.py` rejects any key
absent from `_ENV_OVERRIDES`, so a `fred_lags:` section would need a second additive config key,
and section 8's single key is worth keeping. "Configurable" here means editing that dict, stated
rather than quietly assumed.

`fredgraph.csv` also **omits** missing observations rather than marking them: neither `BAA10Y`
(10,642 rows) nor `DGS10` (16,904 rows) holds a single `.` placeholder, which is the marker
FRED's other interfaces use. Parsing still refuses to coerce an unrecognised value, so a `.`
arriving from a series not sampled here fails loudly rather than becoming `0.0` — but that
handling is defensive, not a response to anything this endpoint was observed to do.

FRED's keyless `fredgraph.csv` endpoint serves the **latest vintage only**, so revisions are
not captured and a revised value silently replaces its original. For daily rate and spread
series revisions are rare, so this is accepted as a known limitation; capturing true vintages
needs ALFRED and an API key, and is deferred. It is recorded in section 15 because it is the
one place where `as_of` is an assumption rather than an observation.

### Writing past `dev_end` is allowed; reading it is not

Ingestion must capture holdout-window rows — otherwise Phase 6 could never score them. The
seal is on **reads**. `write_*` therefore accepts any `as_of`, and nothing in the write path
consults the holdout boundary. The builder must not add a guard there; doing so would make the
holdout unscoreable and would confuse the one invariant that matters.

The consequence is asymmetric, and intended:

- **raw accumulates past `dev_end`.** Every backfill and daily update writes whatever the vendor
  serves, holdout sessions included. Nothing is lost.
- **curated, features and targets stop at `dev_end`.** Deriving them reads raw through
  `load_panel`, which refuses `end > dev_end` without both keys, and this phase neither touches
  nor uses the unlock. The derived tiers simply end at 2025-12-31.

That is the correct resting state, not a gap to close. **The Phase 1 CLI has no unlock flag and
never passes an `end` past `dev_end`**; the curation and feature steps pass
`settings.splits.dev_end`. When Phase 6 scores the holdout it unlocks once, derives the holdout
rows of the curated and feature tiers from raw, and leaves the audit line recording it. So
section 8's "raw must be the rebuild source" holds for the dev window now and the holdout window
then.


### 6a. Distributions are flagged, never silently adjusted

Nasdaq's closes are unadjusted, so every distribution date carries a spurious negative return.
This is not generic noise to revisit later: it is **systematic, signed, and lands in the one
feature whose entire purpose is to treat negative returns differently.** SPY distributes four
times a year at roughly 0.3% of price; squared that is ~7.8e-6 against a typical daily variance
near 1.2e-4, so each event injects roughly 6% of a normal day's variance — always negative,
always into `semivar_neg_proxy_d{n}` and `signed_jump_proxy_d{n}`, on about 40 of the dev
window's 2,513 sessions. Leaving that to "Phase 2 diagnostics" would mean the framework's
second-ranked input is quietly contaminated on dates that were knowable in advance.

So `curated/bars_daily` carries a boolean **`has_distribution`** column.

**Returns are flagged, not adjusted.** Adjusting would destroy raw fidelity (section 13) and
would bake a correction into the data before anyone has decided it is the right one. Flagging
hands Phase 2 the information and lets it choose to adjust, exclude, or dummy the date — which
is the same philosophy this spec applies everywhere else.

#### What the vendor actually serves, verified 2026-10-19

`api.nasdaq.com/api/quote/{sym}/dividends?assetclass=etf` returns ex-date, amount, record date
and payment date — **for QQQ only** (58 rows, 2012-06-15 → 2026-09-21). **SPY and IWM return
zero rows**, under `assetclass` values `etf`, `stocks` and `index` alike. No other keyless
declared source was found.

The resulting split, which the builder implements as stated rather than papering over:

- **QQQ** — ingest the distribution calendar as `raw/nasdaq_dividends/QQQ/...` and set
  `has_distribution` from it.
- **SPY and IWM** — `has_distribution` is written as `False` everywhere, and the column carries
  a dataset-level note that it is **unverified** for these two symbols. The contamination is
  recorded in section 15 with its magnitude.

**The builder must not infer distribution dates from the bars.** Detecting a close-to-close drop
the open does not confirm conflates distributions with genuine overnight news, and a false
positive here is worse than a missing flag: Phase 2 would exclude or dummy a real volatility
event, which is a leak of the researcher's hindsight into the sample. A missing flag only leaves
a known bias known.

A deterministic schedule may exist — SPY ex-dates are widely held to fall on the third Friday of
each quarter-end month — but that is unverified here and is not asserted. If Phase 2 finds the
contamination material, verifying that schedule against a declared source is the cheapest fix.


## 7. The write path lives in `store.py`

`store.py` gains three functions:

```python
def write_raw(frame, *, source, symbol, as_of, ingested_at, settings=None) -> Path
def write_curated(frame, *, dataset, symbol, as_of, settings=None) -> Path
def write_features(frame, *, feature_set, symbol, as_of, settings=None) -> Path
```

Each one, in this order:

1. requires every column in `MANDATORY_COLUMNS` to be present;
2. requires `ts` to be timezone-aware, so a naive timestamp cannot reach the store;
3. requires every row's `as_of` to equal the `as_of` argument — the partition value. This makes
   `load_panel` step 9, which raises when a row contradicts its partition, unreachable from
   data this path wrote;
4. rejects an empty frame and a null `as_of`;
5. writes to a temporary file in the destination directory and `os.replace`s it into place, so
   a crash never leaves a half-written Parquet file for a later read to trip over.

**Raw files are immutable:** `write_raw` refuses to overwrite an existing path rather than
replacing it. This also closes a Phase 0 deferred finding — the raw filename stamp has
whole-second precision, so two ingests of the same source/symbol/date inside one second would
collide on the filename; the refusal turns a silent clobber into an error. Curated and features
files are deterministic derivations of raw, so they *are* overwritten, which is what makes a
rebuild idempotent.

### Why here, and not a new module

The import ratchet exempts exactly one module, top-level `store.py`, and fails any other module
under `src/volab/` that touches Parquet. The phase brief allows extending the ratchet's
`ALLOWED` set with a reason. **This design deliberately does not.** "Exactly one module in the
package touches Parquet" is a stronger and more legible invariant than "two do", and a
singleton that grows into a list is a change a reviewer notices; a list that gains one more
entry is not. `ALLOWED` stays `{"store.py"}` and the ratchet is unchanged.

The cost is that `store.py` grows from ~406 to roughly 560 lines and holds both directions of
I/O. That is accepted: the module's subject is Parquet access, not reading specifically.

Rejected alternatives: a new `src/volab/writer.py` added to `ALLOWED` (loosens the one rule
that makes Phase 5's missing `read_parquet` tool enforceable); a `store/` package with
`_read.py` and `_write.py` (the ratchet's comment explicitly refuses to exempt a `store.py`
inside a subpackage, and the relative-path comparison would reject it anyway).

**Nothing in `src/volab/ingest/` or `src/volab/features/` imports pyarrow or duckdb.** The
fetchers return DataFrames and hand them to `store.write_*`; the feature engine reads through
`load_panel` and writes through `store.write_features`. The ratchet keeps passing untouched.


## 8. One additive config key: `exogenous`

The VIX family and the FRED series are not forecast targets, so they do not belong in
`universe`. But `load_panel` validates requested symbols against `universe` and would raise
`UnknownSymbol` for `VIX`, making `curated/vix_family/VIX/...` unreadable through the only
sanctioned read path.

```yaml
universe: [SPY, QQQ, IWM]
exogenous: [VIX, VIX9D, VIX1D, VVIX, BAA10Y, DGS10, T10Y2Y]
```

`Settings` gains `exogenous: tuple[str, ...]`, validated by exactly the rules `universe` uses
(`validate_segment`, no duplicates, no surrounding whitespace, strings only), plus: it must not
overlap `universe`. It joins `_ENV_OVERRIDES` as `VOLAB_EXOGENOUS`.

`load_panel`'s step 1 changes from "symbols must be in `universe`" to "symbols must be in
`universe ∪ exogenous`". **`symbols=None` continues to scope to `universe` alone**, so every
existing call behaves identically and no exogenous directory can wander into a bars panel.
This is the only change to the guard, it sits in the symbol check far from the holdout logic,
and it is additive — the full Phase 0 suite must still pass unmodified apart from section 5's
two date assertions.

Why this rather than the alternatives: storing exogenous series broadcast across the three
universe symbols would make the raw tier stop mirroring the vendor; adding them to `universe`
would break `symbols=None`; and passing `load_panel` a hand-built `Settings` whose `universe`
is the exogenous ids would technically work today but makes "universe" mean two different
things in two calls, which is exactly the cleverness a reviewer should reject.

The decisive argument for solving this at all, rather than curating in one pass straight from
the fetched frames: Nasdaq's window is a trailing 10 years, so **re-fetching loses history
permanently.** If curated can only be built by re-fetching, that loss propagates into every
derived table. Raw must be the rebuild source, which means raw must be readable.


## 9. Feature engine

All features are computed per symbol from a **trailing** window of the curated panel and
nothing else. Let `r_t = log(C_t / C_{t-1})` over consecutive XNYS sessions, from
`curated/bars_daily`. Windows `n ∈ {1, 5, 22, 66}`.

| Column | Definition | Honest name |
|---|---|---|
| `rv_proxy_d{n}` | `Σ_{i=0}^{n-1} r_{t-i}²` | squared-daily-return proxy for RV |
| `semivar_neg_proxy_d{n}` | `Σ_{i=0}^{n-1} r_{t-i}² · 1{r_{t-i} < 0}` | daily-return analogue of negative semivariance |
| `semivar_pos_proxy_d{n}` | `Σ r² · 1{r > 0}` over the window | as above, positive |
| `signed_jump_proxy_d{n}` | `semivar_pos_proxy_d{n} − semivar_neg_proxy_d{n}` | daily-return analogue of the signed jump |
| `bipower_proxy_d{n}` | `(π/2) · Σ_{i=0}^{n-2} \|r_{t-i}\| · \|r_{t-i-1}\|`, `n ≥ 2` | bipower on daily returns |
| `har_d_proxy`, `har_w_proxy`, `har_m_proxy` | `r_t²`; mean of the last 5; mean of the last 22 | standard HAR components, computed on the RV **proxy** |
| `iv_h1`, `iv_h5`, `iv_h22` | VIX1D, VIX9D, VIX closes | horizon-matched implied vol, annualised % |
| `iv_var_h{1,5,22}` | `(close/100)² · (h/252)` | the same, as variance over the horizon |
| `vvix` | VVIX close | vol-of-vol |
| `baa10y`, `dgs10`, `t10y2y` | FRED levels, lagged per section 6 | credit spread and rate context |
| `has_distribution` | passed through from `curated/bars_daily` | ex-distribution flag; QQQ only, see section 6a |

Horizon matching follows the research: VIX1D for 1 day, VIX9D (9 calendar days ≈ 6 sessions)
for the 5-day horizon, VIX (30 calendar days ≈ 21 sessions) for 22 days.

**Naming is a correctness requirement, not style.** Every daily-return estimator carries
`_proxy`. A later phase comparing against the literature must not be able to mistake these for
intraday measures.

An earlier draft of this table listed the HAR components as bare `har_d` / `har_w` / `har_m`,
which contradicted that rule in the same section that states it: they are means of the
squared-daily-return proxy and carry exactly the confusion the rule exists to prevent. The
**persisted column names** therefore take the suffix. `har_components()`'s own dict keys stay
`har_d` / `har_w` / `har_m`, because they are internal to a function whose docstring says what
they are made of, and the confusion the rule guards against happens where a column is read back
months later, not inside one function.

**Incomplete windows yield null, never a partial sum.** A feature at `t` whose window extends
before the panel's first session is `NaN`. Nulls are written, not dropped, so the table has one
row per session. Nothing is forward-filled, back-filled or imputed anywhere in this phase.

**Implementation constraint.** Trailing-only is enforced by construction: pandas `.rolling(n)`
is right-closed, and the engine may never pass `center=True`, never use a negative `shift`,
and never index forward. Section 11's structural test enforces this at the AST level, so the
property is checked rather than remembered.

### Modules

```
src/volab/features/realized.py   # pure functions on a return series -> same-length arrays
src/volab/features/har.py        # HAR aggregation
src/volab/features/targets.py    # forward realized-variance targets
src/volab/features/build.py      # build_features(panel, *, through) -> frame
src/volab/features/__main__.py   # CLI: load_panel -> build_features -> store.write_features
```

`realized.py` and `har.py` take arrays and return arrays. They never read, never write and
never see a date, which is what makes them cheap to test exhaustively against hand-computed
values.

### The evaluation cutoff is a separate argument from the panel's extent

```python
def build_features(panel: pd.DataFrame, *, through: dt.date) -> pd.DataFrame
```

`build_features` takes an **already-loaded panel** and an evaluation cutoff. It emits feature
rows for sessions `t <= through` only, and **the panel it is handed may extend past
`through`**. The CLI is the only thing that touches the store: it calls
`load_panel(..., end=settings.splits.dev_end)`, then `build_features(panel, through=dev_end)`,
then `store.write_features`.

This two-parameter shape is deliberate and is the single most important structural decision in
the phase. The tempting alternative — have the engine call `load_panel(end=t)` itself and let
the store's filter guarantee honesty — **would make the exit criterion vacuous.** `load_panel`
step 10 drops every row with `as_of > end` before the feature code sees anything, so a feature
that peeked forward would produce identical output whether or not future rows existed on disk,
and section 11's perturbation test could never fail. That is exactly the section 3 anti-goal: a
point-in-time test that passes because it tests nothing.

It is also the realistic leak. Phase 2's walk-forward loads the dev panel **once**, legitimately,
with `end=dev_end`, and then refits and evaluates at many inner cutoffs `t << dev_end`. Every row
it holds is one the store was right to hand over, so the store cannot help; the only thing
standing between that panel and a leak is whether the feature functions look forward. Separating
the cutoff from the extent is what makes that property testable here, in the phase that owns it,
instead of in the phase that would silently depend on it.

Holding rows it must not use is therefore not a lapse in posture — it is the condition under
which the engine's honesty means anything.


## 10. Targets, and why they cannot leak

For horizon `h ∈ {1, 5, 22, 66}` and base session `t`:

```
target_rv_h(t) = Σ_{i=1}^{h} r_{t+i}²
```

A target is forward-looking by definition, so it is kept in its **own dataset** and stamped
with the date it becomes knowable:

- `ts` = the base session `t`, the date the forecast is *for*;
- `as_of` = the `h`-th XNYS session after `t`, the date the last return in the sum is known.

A feature row and a target row for the same `t` therefore carry different `as_of` values, which
is why they cannot share a table: a combined row would have to take the later `as_of` and would
hide the features until the target existed.

The payoff is that **the store itself prevents target leakage.** `load_panel(..., end=E)`
filters on `as_of ≤ E`, so it returns targets only for base dates at least `h` sessions before
`E`. No caller can obtain an unrealised target, and no model code has to remember to exclude
one. The guard Phase 0 built does the work.


## 11. Point-in-time tests — the exit criterion

`tests/test_point_in_time.py`. Byte-identity means per column: equal dtype, and
`frame[col].to_numpy().tobytes()` equal. Float tolerance is not used anywhere in this file.

These tests call `build_features(panel, through=T₀)` **directly**, with a panel built in the
test and never round-tripped through `load_panel`. That is essential: routing the panel through
the store would strip post-`T₀` rows and leave nothing to perturb, which is the trap section 9
describes. The store's filter is Phase 0's property and already has its own tests; what is under
test here is the feature code's behaviour when the future *is* in front of it.

Two constraints on every test in this file. The synthetic panel's dates must stay at or before
the repository's `dev_end`, because `conftest.py`'s autouse `_forbid_holdout_unlock` fails any
unmarked test that reads past it. And perturbations must change values the features actually
consume, so each one is asserted to have changed the panel before the recomputation runs — a
perturbation that silently no-ops would make its test pass for the wrong reason.

1. **`test_perturbing_post_t_leaves_features_byte_identical`** — the exit criterion. Build a
   panel through `T`. Call `build_features(panel, through=T₀)` with `T₀ < T`. Then mutate every
   row after `T₀` and recompute with the same `through=T₀`. Assert byte-identical, column by
   column.

   **The perturbation must change the sign structure of future returns, not only their
   magnitudes, and this is a requirement rather than one option among several.** Scaling every
   future close by a constant leaves the sign of every future return unchanged, so a
   sign-dependent feature that peeks forward survives it — and `semivar_neg_proxy` is exactly
   such a feature, which makes mutation 1 below the one a scale-only perturbation lets escape.
   This was found the hard way during US-4.1: the mutation initially passed the perturbation
   test, and the test was only made to bite by replacing the scaling with a decaying future
   path. A perturbation set that misses this is the vacuous-test failure in section 3, reached
   by following section 11 too literally.

   Use a decaying or sign-alternating future path, plus the rest: inject NaN, delete rows,
   append new sessions.

   **And the perturbation must touch every input group, not only the ones under suspicion.**
   This was found the same way, one story later, and it is the same mistake in a different
   dress. US-4.1's perturbation rewrote the bars' `close` and FRED's `value` and left the
   VIX-family closes and `has_distribution` alone — so a forward peek planted in
   `has_distribution` (`flag | flag.shift(-3)`) passed **both** leakage tests, caught only
   incidentally by a value test. Verified by mutation, not inferred.

   A perturbation that misses an input misses every forward peek that input could host, however
   strong it is on the inputs it does touch. So the test perturbs the bars' closes, every
   exogenous series' values, and the pass-through flags — all of them, every time. A reviewer
   should check the perturbation's *coverage* before examining its strength.
2. **`test_panel_extent_does_not_change_features`** — `build_features(panel_to_T₀,
   through=T₀)` equals `build_features(panel_to_T, through=T₀)`. Catches a feature that depends
   on the panel's extent — a mean over the whole frame, a `max()`, a normalisation by a
   full-sample standard deviation — rather than on its own trailing window. This is a distinct
   defect from test 1: a full-sample normaliser changes when the panel grows even if no row is
   individually perturbed.
3. **`test_shuffled_future_canary`** — permute the post-`T₀` **close values across their
   sessions**, leaving the session dates in place, then assert features at `t ≤ T₀` do not move.
   Permuting whole rows would be a no-op, since the engine sorts and indexes by date and would
   simply undo it; permuting values is what actually tests that no future value reaches a past
   row. The framework's component-12 canary.
4. **`test_no_feature_module_looks_forward`** — a structural AST scan over
   `src/volab/features/`, failing on `center=True`, a negative `shift`/`diff` argument,
   `.shift(-n)`, and forward slicing. This catches the *class* of defect rather than one
   instance, in the spirit of the Phase 0 import ratchet, and like that ratchet it carries its
   own red-before-fix cases so the scan is itself tested.
5. **`test_targets_carry_as_of_h_sessions_after_base`** — `as_of` is exactly the `h`-th XNYS
   session after `ts`, checked across a holiday and a year boundary.
6. **`test_load_panel_at_dev_end_hides_unrealised_targets`** — reading targets with
   `end=dev_end` returns no base date later than `h` sessions before `dev_end`.

### Proof by mutation — required, not optional

A leakage test nobody has watched fail is not evidence. Before the PR, the builder must:

1. edit one feature to peek forward — the intended mutation is changing
   `semivar_neg_proxy_d5`'s window to `.shift(-1)`;
2. run `pytest tests/test_point_in_time.py` and **record the actual failure output verbatim**;
3. confirm tests 1 and 4 both fire, since one is behavioural and the other structural;
4. revert, confirm green;
5. publish the captured output in `docs/superpowers/phase1-decisions.md` and quote it in the
   PR description.

A mutation that only test 4 catches means test 1 is too weak and must be strengthened. A
mutation that only test 1 catches is acceptable but worth noting.

Run a **second** mutation against test 2, since the two tests catch different defects and test
2's is the easier one to write by accident: replace a trailing `.rolling(22).mean()` with a
full-sample `.mean()`. Test 1 will not catch it — no individual row changed — and test 2 must.
Record that output too. If test 2 misses it, the test is wrong, not the mutation.


## 12. Physical layout cost, and the performance budget

Two rules combine into a layout decision neither of them was making on purpose. `as_of` for
bars and the VIX family is the session date (section 6), and `write_*` requires every row's
`as_of` to equal the partition value (section 7, rule 3). A `date=` partition therefore holds
exactly one session, so a curated file holds exactly one row per symbol.

After the section 4 warmup scoping, the arithmetic is:

| Tier | Files |
|---|---|
| raw + curated bars, 3 symbols x 2513 sessions | 15,078 |
| raw + curated VIX family, 4 series x 2579 | 20,632 |
| raw + curated FRED, 3 series x 2579 | 15,474 |
| features, 3 symbols x 2513 | 7,539 |
| targets, 4 horizons x 3 symbols x 2513 | 30,156 |
| **total** | **~88,900 single-row Parquet files** |

The read side costs more than the write side. `load_panel` does no projection and no partition
pruning, deliberately — step 5's comment says correctness must never depend on pruning — so one
feature build needing bars plus the VIX family plus three FRED series expands to roughly
**25,600 file paths** handed to DuckDB in a single `$patterns` list, each with its own Parquet
footer. Phase 0's risk table deferred this ("performance is a Phase 1 concern with real data
volumes"). Phase 1 picks it up, and picks it up by measuring rather than guessing.

**Measure before the bulk backfill.** The first implementation step backfills **one symbol's
bars only** (2,513 files), then times a `load_panel` over it and a feature build on it, and
records both numbers in `phase1-decisions.md`.

**Budget.** A full dev-window feature build for all three symbols finishes in under 5 minutes
on the development laptop; a single `load_panel` of curated bars finishes in under 30 seconds.

**Revisit trigger.** If either budget is missed, **stop before writing the remaining ~86,000
files** and escalate. Four levers exist, in order of preference:

(a) **Narrow the ingested history further.** Cheapest, loses sample.

(b) **Partition coarser — `date=YYYY-MM` — and weaken step 9 from equality to containment**,
asserting each row's `as_of` falls inside its partition's month rather than equalling it. This
cuts the file count by roughly 21x and is the lever to reach for first. Equality is a *stronger*
assertion than the defect requires: step 9 exists to catch a row parked under a contradicting
partition, and an `as_of` of 2024-03 under a `date=2023-06` partition still fires on containment.
What containment gives up is the narrow case of a row misfiled *within* its own month — a
2024-03-07 row under `date=2024-03` alongside 2024-03-11 rows — which the partition value can no
longer distinguish. That case is not what the check was written for, and `write_*`'s own
per-row assertion still covers everything this code writes. State that trade explicitly when
proposing it.

(c) **Relax one-`as_of`-per-partition entirely**, so a partition may span any date range. Strictly
weaker than (b) for the same benefit.

(d) **Add partition pruning to `load_panel`**, which contradicts its own docstring and is the last
resort.

All four are design changes, not tuning, and none is the builder's call alone. If the budget is
missed, propose (b).

The invariant is not pre-emptively traded away, because one `as_of` per partition is what makes
step 9 unreachable from data this code wrote, and that is worth more than file count until a
measurement says otherwise. Guessing at a performance problem before measuring it is how a
simple invariant gets spent for nothing.


## 13. Raw fidelity, the payload archive, and what CI can actually prove

### Raw is faithful, not merely immutable

Once a session falls off Nasdaq's trailing window, whatever was written is the only copy that
will ever exist. "Immutable" and "faithful" are different properties and only the first one
follows from section 7, so the second is stated here: **the raw tier keeps every field the
vendor served, as served.** Raw rows carry the four mandatory columns, every vendor field
unmodified, and provenance — `source_url`, `vendor_date_text`, `fetched_at`.

What "as served" means here, verified against live payloads on 2026-10-19 rather than assumed:

- **Nasdaq bars** send prices as bare numeric strings (`"766.48"`) with **no** currency symbol,
  and volume with thousands separators (`"38,680,060"`). An earlier draft of this spec claimed
  the prices carried a `$`; on this endpoint they do not.
- **Nasdaq dividends** do use a currency symbol, on the amount (`"$0.75143"`), and send `"N/A"`
  for an absent declaration date.
- **Cboe** sends `MM/DD/YYYY` dates and plain decimals; `VVIX_History.csv` has two columns where
  the other three have five.
- **FRED** sends `observation_date,<SERIES_ID>` as its header, so the value column is named after
  the series rather than `value`.

All parsing, numeric coercion and normalisation happen at the **curated** step. A parse bug
found in Phase 3 is then a curated rebuild rather than destroyed history, which is the whole
point of having a raw tier at all.

### The payload archive is committed, deliberately

Each fetch writes the untouched response body to
`data/vendor_payloads/<source>/<symbol>/<fetched_at>.<ext>.gz`, and `build-raw --replay` rebuilds
raw from those instead of the network. That is what makes any rebuild possible once Nasdaq's
trailing window has moved past the data.

**The archive is committed to this public repository, for all three sources.** `.gitignore` keeps
ignoring `data/**` and gains one narrow exception for `data/vendor_payloads/**` only — the
curated, features and targets tiers stay out of git, and `data/holdout_access.jsonl` keeps its
own existing exception.

This is Dhruv's decision, taken with the trade in front of him and recorded here so a later
reader can see it was weighed rather than overlooked. What he was shown: that the three sources
are not equally exposed — Cboe publishes these CSVs openly and FRED's own series are US
government work in the public domain, so only Nasdaq's undocumented backend is genuinely being
republished; that git history is effectively permanent, so a committed payload cannot
meaningfully be withdrawn later; and that the durability goal was reachable without publishing
anything, by keeping the Nasdaq tree in a private repository. He chose durability and
self-contained reproducibility over licensing caution for all three.

The framework document's own rule — publish code, download scripts and derived metrics rather
than raw vendor data — is therefore **knowingly departed from here**, for Nasdaq specifically.
That is the cost of the decision and it is not hidden.

What it buys is real: the repository reproduces its own dataset from a clean checkout with no
credentials and no live endpoint, which is a property the trailing window would otherwise have
made impossible. A `NOTICE` file at the repository root names each payload tree's provenance,
records that the data remains Nasdaq's, Cboe's and the St. Louis Fed's respectively, and states
that it is included to make the research reproducible.

### Archive shape and cadence: one immutable file per payload

Committing the archive makes its growth git's problem, so the shape is a design decision rather
than an implementation detail. Measured payload sizes, fetched 2026-10-19:

| Payload | Raw | gzip -9 |
|---|---|---|
| Cboe `VIX_History.csv` (full history, every request) | 474 KB | **106 KB** |
| FRED `BAA10Y` (full history, every request) | 169 KB | **37 KB** |
| Nasdaq SPY, 10-year backfill | 273 KB | **57 KB** |
| Nasdaq SPY, one-session update | 160 B | **155 B** |

Two facts follow. First, the payload count is **~2,500 a year** (10 fetches a day), not the
~88,900 of section 12 — that figure counts Parquet partitions, which are not archived. Git
handles a few thousand immutable blobs without difficulty. Second, **Cboe and FRED re-serve
their entire history on every request**, so archiving each daily fetch would add ~530 KB
compressed per day, about **194 MB a year**, essentially all of it redundant.

The cadence therefore differs by source, which is what keeps the archive small:

- **Nasdaq bars and dividends — archive every fetch.** The window is trailing, so each payload
  carries sessions that later payloads will have lost. At 155 bytes a day per symbol this is
  free.
- **Cboe and FRED — archive at most one payload per series per calendar month.** A full-history
  payload subsumes every earlier one, so replay needs only the most recent; keeping one a month
  still leaves a monthly vintage trail, which is worth having precisely because section 6 notes
  the keyless FRED endpoint serves latest-vintage only. Skip the write if that month already
  has one.

That is roughly **160 files and 4–5 MB a year**, against 194 MB for archiving everything.

Files are **immutable and never rewritten**: one payload, one path, named by `fetched_at`. A
rolling or monthly *archive container* was considered and rejected — a `.tar.gz` cannot be
appended to, so every fetch would decompress, add and recompress the current period's container,
and git would store a whole new multi-megabyte blob on each rewrite. That converts a few
thousand small immutable objects into a few hundred large mutable ones and increases total
history, which is the opposite of the intent. Immutable per-payload files also keep `build-raw
--replay` trivially resumable, since a partial run leaves completed payloads untouched.

### What CI can prove, and what it cannot

- **Ingestion**: exercised against small committed **synthetic** fixtures — hand-written, not
  vendor data — through the injected transport. Parsing, `as_of` assignment and the write path
  are all covered with no network access. CI cannot prove the live endpoints still respond;
  that is a manual run, recorded in `phase1-decisions.md`.
- **The point-in-time tests**: fully CI-verifiable, because they build their own synthetic
  panels and need no store. This is deliberate — the one criterion the phase is judged on must
  not depend on data CI cannot have.
- **Exit criterion 2** (a dated feature table exists per symbol): verified locally and
  recorded, since CI starts from an empty store.

No CI job makes a network call. A suite running under `filterwarnings = ["error"]` against an
undocumented keyless endpoint would be a flaky gate that teaches the team to ignore red.


## 14. Designed for a later intraday source, not built

Dhruv will create one free, no-card API key (Polygon or Twelve Data) when it is useful.
Phase 1 must not require it, and must not be rearranged to accommodate it later:

- an intraday source arrives as a new raw dataset (`raw/<vendor>_minute/...`) plus a new
  curated dataset (`curated/bars_minute/...`); no existing dataset changes shape;
- `features/build.py` branches on whether the intraday curated dataset exists for a symbol and
  date range. Where it does, true intraday RV, Patton–Sheppard semivariance and bipower
  variation are computed and written under their proper, unqualified names alongside the
  proxies. Where it does not, the proxies stand alone. The feature table gains columns; none
  are renamed or redefined.
- the fetchers already take an injected transport, so an authenticated one is a constructor
  argument, not a rewrite.


## 15. Risks and known limitations

| Risk | Consequence | Mitigation / status |
|---|---|---|
| Nasdaq's 10-year cap is a **trailing** window | the dev window's start drifts forward ~1 session/day; history is lost permanently once it falls off | **Observed, not predicted:** the floor was 2016-10-19 when first probed and 2016-10-20 two days later, still exactly 2,513 sessions, so two days of delay cost one session permanently. The backfill ran before the feature engine existed, as planned, and the store's inception is therefore **2016-10-20**. Immutable raw Parquet plus the committed payload archive pin it; the store only accumulates from here and the span cannot be reproduced from scratch. This is what makes Phase 0's immutability load-bearing rather than decorative. |
| `api.nasdaq.com` is undocumented and keyless | it may change shape, rate-limit or block without notice | raw archive means a break costs new data, never existing data; the fetcher's injected transport makes a vendor swap local |
| Nasdaq closes are **unadjusted**, and distributions are only obtainable for QQQ | every distribution date carries a spurious ~0.3% negative return that flows **systematically and signed** into `semivar_neg_proxy_*` and `signed_jump_proxy_*` — ~6% of a typical day's variance, on ~40 of 2,513 dev sessions, always in the same direction | `has_distribution` is ingested and set for QQQ (section 6a). **For SPY and IWM no keyless declared source exists**, so the flag is `False` everywhere and the bias is present but unflagged. Bar-derived inference is forbidden: a false positive would exclude a real volatility event. Phase 2 must treat SPY/IWM semivariance as carrying this known bias |
| FRED keyless gives the **latest vintage only** | a revision silently replaces its original, so `as_of` is an assumption for FRED alone | conservative one-session lag; rare for daily rate series; true vintages need ALFRED and a key |
| No intraday data | the framework's two highest-ranked inputs are unavailable | stated everywhere, proxies named as proxies, section 14 keeps the door open |
| 200-session holdout | the Phase 6 check is weak at 22 and 66 days | recorded in section 5; Phase 6 must report it |
| **Cboe publishes VIX on days the NYSE is closed** | 33 rows since 2016-07-01 — Memorial Day, Independence Day, Labor Day, Thanksgiving, MLK Day — carry real values with no equity session to attach them to. VIX9D, VIX1D and VVIX have none | verified against the archived payloads. The non-session policy splits by tier: **bars reject** such a row, because a bar dated when the market was shut means the source disagrees with the calendar and would corrupt the return series; **exogenous series drop and report** it, because refusing outright would discard 9,298 usable VIX rows over 33 unusable ones. The dropped count is printed, so a vendor change surfaces rather than passing silently |
| `load_panel` is modified | a regression here would break the project's central guarantee | the change is additive and confined to the symbol check; the entire Phase 0 suite must pass unmodified apart from two date assertions |
| ~88,900 single-row Parquet files; ~25,600 paths per feature build | reads may be slow enough to make the suite and the daily pipeline painful | section 12: measure on one symbol first, stated budget, stop-and-escalate trigger before the bulk backfill |
| The payload archive is **committed to a public repository**, republishing an undocumented vendor's responses | Nasdaq may send a takedown request; Cboe's CSVs are already public and FRED is public domain, so the exposure is Nasdaq's tree alone | Dhruv's informed decision (section 13), taken over the private-repo alternative. Remedy if it happens: make the repository private, or rewrite history to drop `data/vendor_payloads/nasdaq_*`. A `NOTICE` file records provenance. The durability risk this replaces — irreplaceable history on one laptop — is now closed by git |


## 16. Left to the builder

- Module and function decomposition inside `ingest/` and `features/` beyond the files named.
- The HTTP helper's retry, timeout and backoff policy (stdlib `urllib`; **no new dependency** —
  `pandas`, `numpy` and `exchange_calendars` are already present and sufficient).
- Exact CSV/JSON parsing per vendor, including Cboe's `MM/DD/YYYY` dates and `VVIX`'s
  two-column shape, which differs from the other three.
- How the backfill chunks requests and resumes after an interruption.
- Fixture design for network-free ingestion tests via the injected transport.
- Whether `__main__.py` is one CLI or two.
- Everything not fixed above is the builder's call, including anything the mutation step
  reveals about test 1's strength.
