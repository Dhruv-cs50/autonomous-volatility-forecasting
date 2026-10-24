# volab — an honest volatility-forecasting harness

A research lab for forecasting realized volatility, built around one rule: **a deterministic
pipeline owns the data, the splits and the scoring.** Models and, later, an LLM research agent
propose experiments through typed tools and never touch the evaluation path.

## Why the harness owns truth

The design starts from a negative result rather than an ambition. Statistical corrections do not
catch data leakage:

- A published agentic framework had an LLM reviewer **approve a feature built from future
  intraday bars**.
- A deliberately leaky strategy with a Sharpe of 35 **passed both Deflated Sharpe and PBO
  checks**.

If a reviewer and a battery of corrections can both wave a leak through, then refusal has to live
in the data layer, where it can be mechanical. So `load_panel()` is the only way to read, it
requires an explicit `end` on every call, and reading past the development boundary needs two
independent keys and leaves an audit line.

The project's claimed contribution is therefore **not** a model that beats HAR — the literature
says those wins are 1–2% and often just calibration. It is a harness that counts its trials,
reports confidence sets instead of a single winner, and can show its leakage tests failing on
demand.

## Status

| Phase | Scope | State |
|---|---|---|
| 0 — Skeleton | Parquet layout, config, `load_panel()` and the holdout guard | merged |
| 1 — Data and targets | Ingestion, realized measures, forward targets, point-in-time evidence | merged |
| **2 — Walk-forward** | Truncation equivalence, process isolation, walk-forward, HAR baseline, trial ledger | **in review** |
| 3–6 | Implied-vol contestant, scheduler, agent layer, write-up | not started |

## What Phase 1 contains

**Ingestion** (`src/volab/ingest/`) — three keyless sources, no API key and no account:

| Source | Series | Span |
|---|---|---|
| Nasdaq | daily OHLCV for SPY, QQQ, IWM | trailing 10 years (2,513 sessions) |
| Cboe | VIX, VIX9D, VIX1D, VVIX | 1990 / 2011 / 2022 / 2006 onward |
| FRED | `BAA10Y`, `DGS10`, `T10Y2Y` | 1962 onward |

Nasdaq's window is **trailing**, so sessions that fall off it cannot be refetched at any price.
Raw Parquet is therefore immutable, and the untouched vendor responses are committed under
`data/vendor_payloads/` so the repository can rebuild its own dataset from a clean checkout. See
`NOTICE` for provenance.

**Feature engine** (`src/volab/features/`) — realized variance, negative and positive
semivariance, signed jumps, bipower variation and HAR components over 1/5/22/66-session trailing
windows, plus horizon-matched implied vol and credit spreads.

Every daily-return estimator is named `*_proxy`. No free source provides multi-year intraday
bars, so these are **not** the intraday measures of the literature — they are weaker quantities
computed from daily returns, and the naming exists so a later phase cannot mistake one for the
other.

**Targets** — forward realized variance at 1, 5, 22 and 66 sessions, each row stamped with the
session on which its horizon completes. Because `load_panel` filters on `as_of`, the store
**cannot return a target that has not yet realised**. No model code has to remember to exclude
one.

## The split

```yaml
splits:
  dev_end: "2025-12-31"        # last date development code may observe
  holdout_start: "2026-01-02"  # first sealed date
```

2,312 bar sessions in development against a 200-session holdout that accrues as the project runs.
Both dates are literals in `config/base.yaml`; nothing computes a boundary from the clock.
`VOLAB_SPLITS_DEV_END` can move `dev_end` **earlier**, which only withholds data, and
`load_settings` refuses a value later than the file's literal — the direction that would open
the seal. So moving the boundary later means editing `config/base.yaml`, not exporting a
variable. The boundary moved once,
deliberately, in Phase 1 before any score existed to be influenced by the choice — the
reasoning is in `docs/superpowers/phase1-decisions.md`.

Writes may go past the boundary, and must: ingestion has to capture holdout rows or Phase 6 could
never score them. **Only reads are sealed.**

## The point-in-time evidence

The claim is that the feature table at `t` is the table that would have been built had nothing
after `t` happened yet. It is tested by **truncation equivalence**: build at cutoff `t` from the
full panel, build again from a panel that *physically stops* at `t`, compare byte-exactly. That
is not a proxy for point-in-time correctness; it is the definition of it. A trailing feature
cannot tell the two panels apart. A forward peek can.

`tests/test_truncation_equivalence.py` sweeps that over a synthetic panel exhaustively and over
the real curated panel at a sample of cutoffs including awkward calendar positions — a half-day,
a holiday eve, the session before a long gap, the last session before `dev_end` — each derived
from the exchange calendar and asserted to be what it claims. `VOLAB_PIT_EXHAUSTIVE=1` runs the
real sweep over every cutoff instead: **0 of 2,245 disagree**, in 6m00s.

Cross-call state is a separate class, and truncation equivalence cannot see it in one process: a
memoised full-sample statistic filled on the full-panel build is reused by the truncated build,
so the two agree by sharing an answer. `tests/test_process_isolation.py` closes it by running
each side in a fresh interpreter, and carries the discriminating pair — the same plant escapes
in-process and is caught across processes.

### Why the old method was demoted, measured rather than argued

Phase 1's exit criterion perturbed the future and asserted the table was byte-identical. Two
escapes survive that method **as a class**, and both were built and run rather than imagined:

| Planted defect | Phase 1 point-in-time suite | Whole Phase 1 suite | Truncation equivalence | Process isolation |
|---|---|---|---|---|
| E1 — band predicate on the next return | **100 of 100 pass** | **646 of 648 pass** | **catches it** | — |
| E2 — memoised full-sample statistic | **0 of 100 fail** | passes | cannot see it | **catches it** |

E1 was planted in `src/` and the suites were run; the one test that notices is a hand-written
value test whose returns happen to land in the band, which is incidental rather than a defence.
**Both rows are reproducible from a checkout**: the plant is committed as `tools/e1_plant.patch`
and `uv run --locked python tools/reproduce_e1.py` applies it to a throwaway tree and runs both
methods against it. The whole-suite figure is 648 tests — everything but the four Phase 2 test
files the driver names — with one skip that names itself (`test_store_guard` declines to run
outside a git work tree, and a `git archive` tree has no `.git`), so in an ordinary checkout it
reads 647 of 648. An earlier **644 of 645** here was sprint 1's tree, `21dc15b`, which had 663
tests and two Phase 2 test files rather than four. `tools/README.md` lists every probe behind a figure quoted here or in
`docs/superpowers/phase2-decisions.md`.
E1 escapes perturbation because at the cutoff a fixture can only present `|r_next|` from a finite
set, so a predicate thresholded into a gap of that set is invariant under *every* perturbation
image — more perturbation groups raise the attacker's cost without closing the class. Truncation
has no image to be constant across: the row is gone.

**The perturbation suite and the AST scan are kept, and demoted.** They are cheap per-commit
ratchets against a careless spelling or an obvious regression, and they are not the guarantee.
The scan in particular is **never to be cited as a defence**: four evasions of it have been
measured — an order-only peek ("was tomorrow's move bigger than today's?"), a volume peek, a
back-fill spelled invisibly, and E1 — and none of them needs a `shift`, a slice or a `center`.

### What truncation equivalence does not catch

Stated because a leakage suite whose limits are not stated invites exactly the trust this project
exists to withhold.

- **A sampled sweep is not a proof.** A forward peek is visible at cutoff `t` only if it changes
  a row at or before `t`. E1 changes one row, so it is visible iff `t`'s next return lands in its
  band: **52 of 2,312 real sessions, 2.2%**. A 40-cutoff sample expects 0.9 hits. The sampled
  tier is therefore a ratchet with a stated power, not a proof. The dev-window proof is
  `VOLAB_PIT_EXHAUSTIVE=1` on the sweep itself — 2,245 cutoffs, 6m00s, and not per-commit. The
  walk-forward adds exhaustive coverage **of its own origins**, which are not the dev window: it
  rebuilds the table at every origin it evaluates from a panel truncated there, and the recorded
  run used a stride of 5, so 251 of 1,255 sessions in its window.
- **It is blind where the leak's own input is already missing.** If `r[t+1]` is null — the next
  session is a gap — the peek reads null from the full panel too and agrees with the truncated
  one. That is a blind spot of the leak rather than of the method: there is no difference to
  find. Derived and asserted, not assumed.
- **A cross-sectional peek is unreachable rather than tested.** `build_features` refuses a panel
  holding more than one non-exogenous symbol, so the shape cannot be expressed; if that guard
  were relaxed, nothing here would notice.
- **Refusal is not equivalence.** The engine still rejects a malformed row dated after the
  cutoff, where a truncated panel would simply not have it. That is fail-closed — it declines to
  build rather than carrying a value across — but the two panels do differ in that one behaviour.

## Getting started

```bash
uv sync
uv run --locked pytest                          # the full suite
uv run --locked python -m volab.ingest backfill # fetch, archive, build raw, curate
uv run --locked python -m volab.features build  # the feature table and the four target sets
```

Gates, all of which CI enforces:

```bash
uv run --locked ruff check . && uv run --locked ruff format --check .
uv run --locked mypy src && uv run --locked pytest -q
```

`--locked` matters: `--frozen` installs from the lock without comparing it to `pyproject.toml`,
and a bare `uv run` re-locks silently.

## Layout

```
src/volab/
  store.py       the ONLY module that touches Parquet, in either direction
  config.py      typed settings; five validation rules define the split
  paths.py       pure path algebra, no I/O
  sessions.py    trading-session arithmetic, tolerant of non-session dates
  ingest/        fetchers, the payload archive, raw -> curated
  features/      realized measures, HAR, forward targets, the feature table
data/
  vendor_payloads/   committed: the untouched vendor responses
  raw/ curated/ features/   gitignored: derivable from the payloads
docs/
  sprint-board.md            delivery state, story by story
  superpowers/specs/         the design, with every decision's reasoning
  superpowers/plans/         the implementation plan
  superpowers/phase1-decisions.md   29 rulings, each with what it costs if wrong
```

## Enforced invariants

- **One module touches Parquet.** `tests/test_import_discipline.py` fails the build if anything
  under `src/volab/` other than `store.py` imports `duckdb` or `pyarrow`, or names
  `read_parquet`, `scan_parquet`, `read_table`, `ParquetFile` or the store's private helpers.
  This is what later makes a research agent's *missing* read tool enforceable rather than
  decorative.
- **A row's `as_of` must equal its partition.** Checked on write, so the read-side corruption
  check cannot fire on anything this code produced.
- **Raw is immutable.** Writing over an existing raw file raises.
- **No tolerance in a leakage test.** Exact comparison only: a 1e-9 leak reads as floating-point
  noise, which is the most dangerous size for one to be.
- **Trailing windows only**, outside the one module that owns forward quantities.

## Known limitations, stated rather than discovered later

- **No intraday data.** Verified across every free tier: Alpha Vantage gates intraday behind
  premium, Yahoo returns `429`, Stooq serves a JavaScript challenge, and free minute windows from
  paid vendors are trailing and land inside the sealed holdout. So the framework's two
  highest-ranked inputs are unavailable and their daily-return analogues carry `_proxy`.
- **Nasdaq closes are unadjusted.** Each distribution injects a spurious ~0.3% negative return
  straight into negative semivariance. `has_distribution` flags the dates where the vendor serves
  them — QQQ only; SPY and IWM return no dividend rows at all, so the bias is present and
  documented rather than detected.
- **FRED's `as_of` is an assumption.** Its keyless endpoint serves only the latest vintage, so
  publication dates are modelled as a conservative one-session lag rather than observed.
- **The 200-session holdout is thin at long horizons.** At 22 and 66 sessions it yields few
  non-overlapping blocks, so the final confirmatory check will be weak there. Phase 6 must report
  that rather than discover it.
