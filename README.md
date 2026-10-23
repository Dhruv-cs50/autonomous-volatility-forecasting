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
| **1 — Data and targets** | Ingestion, realized measures, forward targets, point-in-time evidence | **in review** |
| 2 — Baseline shootout | GARCH family, HAR, recalibrated HAR, walk-forward, DM + MCS | not started |
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

The phase's exit criterion is that features at `t` do not move when data after `t` changes. A
leakage suite nobody has watched fail is not evidence, so two defects were planted and run, with
the output recorded in `docs/superpowers/phase1-decisions.md`. Two mutants caught is evidence
that the layers fire, not a proof that nothing gets past them — what does get past them is
below:

| Planted defect | Behavioural tests | AST scan |
|---|---|---|
| forward offset (`shift(-1)`) | catch it | catches it |
| full-sample statistic | catch it | **blind to it** |

Two layers, because neither suffices alone. The scan reads syntax, so it cannot see a leak that
looks like ordinary normalisation; the behavioural tests depend on the perturbation being well
chosen. During this phase a badly chosen perturbation let a real forward peek through **five
separate times**, and every one was found by running something rather than by reading it.

### What it does not catch

Two classes are measured and remain open, so the claim is deliberately narrow: **the suite
catches the spellings the AST scan enumerates — a `shift`/`diff` offset that is not a
non-negative integer literal, a `bfill`/`backfill`/`interpolate`, a `method=` other than
`ffill`/`pad`/`None`, a non-false `center=`, any slice — plus every leak the perturbation images can
distinguish, and nothing stronger.** A forward peek spelled outside that list is not
hypothetical: the first class below is one, written in source, and it passes 607 of 608 tests.

- **A predicate constant across the perturbation images.** At the cutoff the fixture presents
  `|r_next|` from a finite set, so a band threshold placed in a gap of that set — a credible
  crash-eve signal — is invariant under every perturbation. Measured at **607 of 608 tests
  passing**. More perturbation groups raise the attacker's cost without closing the class; dense
  or property-based magnitudes would.
- **Cross-call state.** A memoised full-sample statistic defeats the only full-sample detector,
  because both tables it compares are built in one process. Measured at **0 of 100 failures**.
  A subprocess-level check would see it.

Both are Phase 2 work. They are named here rather than in a footnote because a leakage suite
whose limits are not stated invites exactly the trust this project exists to withhold.

The AST scan is a ratchet against a careless spelling, never a defence: four evasions of it have
been measured, and an order-only peek — "was tomorrow's move bigger than today's?" — needs no
`shift`, slice or `center` at all.

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
