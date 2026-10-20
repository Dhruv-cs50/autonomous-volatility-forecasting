# Phase 0 — Skeleton and Data Contract

**Date:** 2026-10-19
**Status:** Approved design, not yet implemented
**Parent document:** `reference/Autonomous volatility forecasting framework.md`, "Phased build order for one student", Phase 0.

## 1. Purpose

Build the foundation of an autonomous volatility-forecasting research lab in which **a
deterministic pipeline owns the data, the splits and the scoring, and an LLM agent only
proposes experiments through typed, validated tools**. Phase 0 delivers that foundation's
first and most important guarantee: *code written during development cannot see the
holdout window.*

The framework document's central argument is that the project's contribution is not a
model that beats HAR — the literature says those wins are small and often just
calibration — but an *honest harness*. Phase 0 is where honesty is made mechanical rather
than aspirational.

### Why the guard comes first

Two findings in the parent document motivate putting leakage defence before any model:

- An LLM reviewing agent approved a feature built from future intraday bars (AQuA).
- A deliberately leaky strategy with a Sharpe of 35 passed both Deflated Sharpe and PBO
  checks ("What survives honest evaluation?").

Statistical correction does not catch leakage. Only the data layer can. So the data layer
is built first, and it is built to refuse.

## 2. Scope

### In scope

- Git repository, `uv` project, Python 3.12 pin.
- Lint, type-check and test configuration; GitHub Actions CI.
- Canonical Parquet directory layout with point-in-time columns.
- `volab.paths` — build and parse dataset paths.
- `volab.config` — typed configuration loader with split validation.
- `volab.store.load_panel()` — the only sanctioned read path, carrying the holdout guard.
- `volab.errors` — the exception taxonomy.
- A lint rule forbidding direct Parquet reads outside `volab.store`, so `load_panel()` is
  the only way in rather than merely the recommended one.
- Tests covering all of the above, driven by synthetic Parquet fixtures.

### Explicitly out of scope

No vendor API clients, no realized-measure computation, no models, no walk-forward
harness, no MLflow, no Prefect or Dagster, no LangGraph agent, no dashboard. Those are
Phases 1 through 5. Phase 0 reads only data that its own tests write.

### Exit criteria

1. CI is green on `main`: `ruff check`, `ruff format --check`, `mypy src`, `pytest`.
2. A test proves `load_panel()` raises `HoldoutViolation` when `end` falls after
   `splits.dev_end`.
3. A test proves one unlock key alone is insufficient.
4. A test proves a legitimate unlocked read writes an audit record.

## 3. Decisions taken

| Decision | Choice | Reasoning |
|---|---|---|
| Environment manager | `uv` | Fast, lockfile-based, single tool for env and dependency management; the parent document assumes it in the CI line item. |
| Python version | 3.12 (pinned) | Best wheel coverage for `arch`, `statsmodels`, `LightGBM` and `pyarrow`, all of which arrive in Phases 2–3. The machine's system Python 3.14 is too new for several of those. |
| Dev/holdout boundary | A fixed date, `splits.dev_end`, in config | Reproducible and trivially testable. A fraction-of-sample boundary moves as data grows, silently re-sealing and un-sealing rows. |
| Repo location | `vol-lab/` subfolder, no spaces in the name | The parent directory path contains a space, which breaks some tooling; keeping the repo root clean avoids it propagating into CI scripts. |
| Universe | `SPY`, `QQQ`, `IWM` | Enough for cross-asset Model Confidence Set results later, small enough to stay inside free data tiers. Schema is keyed by `(symbol, date)` so it generalizes without migration. |
| Storage format | Parquet + DuckDB | No licensing questions (unlike ArcticDB's Business Source License), zero server to operate, and DuckDB reads Hive-partitioned Parquet directly. |
| Config format | YAML, loaded into a typed model | Human-diffable in review; typed at the boundary so a malformed split cannot reach `load_panel()`. |

## 4. Repository layout

```
vol-lab/
  pyproject.toml              # deps, ruff, mypy, pytest config
  uv.lock
  .python-version             # 3.12
  .gitignore
  README.md
  .github/workflows/ci.yml
  config/
    base.yaml                 # universe, calendar, splits, storage root
  src/volab/
    __init__.py
    errors.py
    config.py
    paths.py
    store.py
  tests/
    conftest.py               # synthetic panel fixture + holdout-unlock policing
    test_config.py
    test_paths.py
    test_store_guard.py
  data/                       # contents gitignored
    raw/.gitkeep
    curated/.gitkeep
    features/.gitkeep
  docs/superpowers/specs/
  reference/                  # the three pre-existing project files
```

`src/` layout rather than a flat package, so tests exercise the installed package and
cannot accidentally import a half-configured working copy.

## 5. Configuration contract

`config/base.yaml`:

```yaml
universe: [SPY, QQQ, IWM]
calendar: XNYS
splits:
  dev_end: "2023-12-31"       # last date development code may observe
  holdout_start: "2024-01-02" # first sealed date
storage:
  root: "data"
```

### Loader behaviour

`volab.config.load_settings(path: Path | None = None) -> Settings` returns a frozen,
typed object. Validation rules, each raising `ConfigError` on failure:

1. `universe` is non-empty and contains no duplicates.
2. `dev_end` and `holdout_start` parse as ISO dates.
3. `holdout_start > dev_end`.
4. No trading session exists strictly between `dev_end` and `holdout_start` on
   `calendar`. This closes an orphan gap where rows would belong to neither window and so
   be governed by nothing. Neither endpoint need itself be a session — the default
   `dev_end` of 2023-12-31 is a Sunday, which is fine, because what matters is that no
   *observable* date falls between the two.
5. `storage.root` resolves to a path inside the repository.

Rule 4 needs a trading calendar. `exchange_calendars` is the dependency; if its API proves
awkward, the fallback is a weekday-and-fixed-holiday check, with the compromise recorded
here rather than left implicit.

Environment variables may override single values with a `VOLAB_` prefix, so CI and the
future holdout-reveal job can vary configuration without editing the YAML.

## 6. Parquet layout

Three tiers, all Hive-partitioned by date so DuckDB can prune on the partition key:

| Tier | Path template |
|---|---|
| Raw | `data/raw/{source}/{symbol}/date=YYYY-MM-DD/part-{ingested_at}.parquet` |
| Curated | `data/curated/{dataset}/{symbol}/date=YYYY-MM-DD/part.parquet` |
| Features | `data/features/{feature_set}/{symbol}/date=YYYY-MM-DD/part.parquet` |

Raw files are immutable and may accumulate several parts per date, one per ingestion;
`ingested_at` in the filename keeps them from colliding. Curated and feature files are
single-part and rewritten by their producing job.

### Mandatory columns

Every file in every tier carries:

| Column | Type | Meaning |
|---|---|---|
| `symbol` | string | Instrument identifier. |
| `ts` | timestamp, UTC | The observation's own timestamp. |
| `as_of` | date | The date on which this value first became knowable. |
| `ingested_at` | timestamp, UTC | Wall-clock time the row was written. |

`as_of` carries the point-in-time semantics and is the column the guard filters and
asserts on. `ts` and `as_of` differ whenever a value is revised or published late — a
macro series released with a lag has `ts` in the past and `as_of` at release. Filtering on
`ts` would admit revisions that were not knowable at the time, so the guard never uses it.

### `volab.paths`

Pure functions, no filesystem access:

- `raw_path(source, symbol, date, ingested_at) -> Path`
- `curated_path(dataset, symbol, date) -> Path`
- `features_path(feature_set, symbol, date) -> Path`
- `parse_path(path) -> PathInfo` — recovers tier, dataset, symbol and date.
- `dataset_glob(tier, dataset, symbols) -> str` — the DuckDB scan pattern.

`Tier` is a string enum (`RAW`, `CURATED`, `FEATURES`) defined here, since the tier is a
property of the layout; `volab.store` imports it rather than defining a parallel one.

`parse_path(build(...)) == inputs` for every builder, property-tested over generated
symbols and dates.

## 7. `load_panel()` — the holdout guard

```python
def load_panel(
    dataset: str,
    *,
    end: date,
    symbols: Sequence[str] | None = None,
    start: date | None = None,
    columns: Sequence[str] | None = None,
    tier: Tier = Tier.CURATED,
    allow_holdout: bool = False,
    settings: Settings | None = None,
) -> pd.DataFrame: ...
```

`end` is keyword-only and required. There is no default and no sentinel meaning "all
available data": the caller must always state the last date it claims the right to see.

### Guard rules

**a. Explicit horizon.** `end` is required. An open-ended read is the most common way
leakage enters, so the API makes it unexpressible.

**b. Boundary check.** If `end > settings.splits.dev_end` and the read is not unlocked,
raise `HoldoutViolation` before touching the filesystem. The message names the requested
`end`, the configured `dev_end`, and how to unlock — a useful error, not a riddle.

**c. Two independent keys.** Unlocking requires **both**:
- the call passes `allow_holdout=True`, and
- the environment sets `VOLAB_HOLDOUT_UNLOCK=1`.

Either alone fails with `HoldoutViolation`. Two keys because each defeats a different
failure: a flag flipped while debugging and forgotten cannot open the holdout without an
environment change, and an environment variable left set in a shell cannot open it without
a deliberate call-site edit. A single key would make either mistake sufficient.

**d. Audit trail.** Every *successful* unlocked read appends one JSON line to
`data/holdout_access.jsonl`: timestamp, dataset, tier, `end`, symbols, calling module and
line, git SHA, and whether the tree was dirty. This is the mechanism behind the parent
document's "a single logged holdout check": the holdout is not forbidden, it is
*expensive and recorded*, and the count of lines in this file is a number the final
write-up must report.

**e. Post-read assertion.** Rule (b) validates the argument; this validates the data
actually returned. It catches a mislabelled partition, a bad `as_of` write upstream, or a
path bug — failures no amount of argument-checking can see. Two assertions, both raising
`HoldoutViolation` regardless of cause:

- **e1.** Every row's `as_of` equals the Hive `date` partition value its file sits under.
  This is the assertion that does the work. A file parked in `date=2023-06-15` whose rows
  carry `as_of = 2024-03-01` is a fault no window filter can see, because filtering would
  simply discard the row and report no data — a plausible answer from corrupt storage.
  Checking consistency instead of plausibility is what makes the fault visible.
- **e2.** `max(as_of) <= end` on the frame about to be returned. Unreachable while e1 and
  the window filter are both correct, and kept for exactly that reason: it costs one
  comparison and is the last thing standing if either is later broken.

**f. Test-suite policing.** A session-scoped autouse fixture fails any test that unlocks
the holdout unless that test is marked `@pytest.mark.holdout`. Without this, a single
careless fixture could unlock the holdout for the whole suite and every later test would
inherit it.

### Failure philosophy

Violations raise. `load_panel()` never silently clips the requested window to `dev_end`,
because a silent clip produces plausible numbers from the wrong data, which is worse than
a crash and much harder to notice. The same applies to a missing dataset
(`DatasetNotFound`) and an unknown symbol — no empty frame returned where the caller
expected rows.

Rows with null `as_of` are a violation, not a row to skip: a null cannot be proven to
precede `end`.

## 8. Error taxonomy

`volab.errors`, all deriving from `VolabError`:

| Exception | Raised when |
|---|---|
| `HoldoutViolation` | Any attempted or detected read past `dev_end` without both keys, including the post-read assertion. |
| `ConfigError` | Configuration is missing, unparseable, or internally inconsistent. |
| `DatasetNotFound` | The requested tier/dataset pair, or a requested symbol within it, matches no files. |
| `UnknownSymbol` | A requested symbol is absent from `settings.universe`. |
| `SchemaError` | A file lacks a mandatory column or has the wrong type for one. |

`UnknownSymbol` and `DatasetNotFound` are distinct on purpose: the first means the request
was wrong, the second means the data is missing. Collapsing them would make a typo
indistinguishable from a failed ingestion.

One base class so callers can catch everything from the data layer, and distinct
subclasses so CI can assert on the specific failure rather than on a message string.

## 9. CI

`.github/workflows/ci.yml`, triggered on push and pull request:

1. `actions/checkout`
2. `astral-sh/setup-uv` with the cache enabled
3. `uv sync --locked` — fails if `uv.lock` is stale, so a dependency cannot drift in
   unlocked
4. `uv run --locked ruff check .`
5. `uv run --locked ruff format --check .`
6. `uv run --locked mypy src`
7. `uv run --locked pytest -q`

`--locked`, not `--frozen`: `--frozen` installs from the lockfile *without* comparing it
against `pyproject.toml`, so a stale lock passes silently — the opposite of what this step
is for. `--locked` asserts the lock is current and fails otherwise. The flag is repeated on
each `uv run` because a bare `uv run` re-locks and re-syncs by default, which would quietly
repair a stale lock inside the runner and report success.

`VOLAB_HOLDOUT_UNLOCK` is never set in this workflow. The future holdout-reveal job is a
separate, manually dispatched workflow, which is also what makes its runs individually
visible.

`mypy` runs in strict mode over `src`. The data layer is where types buy the most: a
`date` where a `str` was meant is exactly the class of bug that produces a wrong window.

## 10. Testing strategy

Test-driven: each test below is written and seen to fail before the code that satisfies it.

### Fixtures

`conftest.py` provides `synthetic_store`, which writes a small Parquet panel to a
`tmp_path` and returns `Settings` pointing at it. The panel spans dates on both sides of
`dev_end` for all three symbols, with correct mandatory columns. Tests needing a broken
store derive from it rather than hand-rolling their own.

### Cases

**Config**
- A valid file loads; fields have the expected types.
- Empty or duplicated `universe` raises `ConfigError`.
- `holdout_start <= dev_end` raises `ConfigError`.
- A trading day between `dev_end` and `holdout_start` raises `ConfigError`.
- An unparseable date raises `ConfigError`, not `ValueError`.

**Paths**
- Round-trip: `parse_path(build(...))` recovers the inputs, for all three builders.
- A malformed path raises rather than returning a partly populated `PathInfo`.

**`load_panel()`**
- A dev-window read returns only rows with `as_of <= end`.
- `symbols` and `columns` filter as specified.
- A symbol outside `universe` raises `UnknownSymbol`; a symbol in `universe` with no files
  raises `DatasetNotFound`.
- `end > dev_end` with no keys raises `HoldoutViolation`.
- `allow_holdout=True` with no environment variable raises.
- `VOLAB_HOLDOUT_UNLOCK=1` with `allow_holdout=False` raises.
- Both keys (test marked `holdout`): returns holdout rows **and** appends exactly one
  audit line, whose fields match the request.
- A fixture file whose `as_of` values disagree with its partition date raises
  `HoldoutViolation` from assertion e1, even within the dev window. This is the test that
  proves rule (e) is live rather than decorative. Assertion e2 has no test, because
  reaching it requires e1 or the filter to be broken first; that is noted rather than
  worked around with a mock.
- A file missing `as_of` raises `SchemaError`.
- Null `as_of` raises `HoldoutViolation`.
- An empty dataset directory raises `DatasetNotFound`.

**Suite policing**
- An unmarked test that unlocks the holdout fails. Verified with pytest's own `pytester`
  fixture, so the check is itself tested rather than assumed.

## 11. Risks and open points

| Risk | Mitigation |
|---|---|
| `exchange_calendars` API friction on the adjacency rule | Fall back to a weekday-plus-holidays check and record the compromise in this document. |
| `dev_end` of 2023-12-31 may leave too little holdout once real history arrives | The date is one config value and the audit log is empty until it is used, so it can be moved in Phase 1 before any score exists to be influenced by the choice. |
| DuckDB partition pruning may not apply to every scan pattern | Correctness never depends on pruning; the post-read assertion filters regardless. Performance is a Phase 1 concern with real data volumes. |
| The guard protects `load_panel()`, not `pd.read_parquet` | Phase 0 ships a lint rule banning direct Parquet reads outside `volab.store`, which is also what makes the agent's lack of a `read_parquet` tool enforceable later. |
| On this machine, every entry in `.venv/lib/python3.12/site-packages` acquires the macOS `hidden` flag (`UF_HIDDEN`), and CPython skips a `.pth` file carrying it — so the editable install silently stops working and `import volab` fails | `pythonpath = ["src"]` under `[tool.pytest.ini_options]`, so the suite does not depend on the `.pth` being readable. `chflags nohidden` is the manual escape if an interactive `python` also fails. The cause is likely hardlinks from a flagged `~/.cache/uv`; it does not affect CI, which syncs fresh on Linux. |

## 12. What Phase 0 earns

At the end of Phase 0 there are no forecasts and no leaderboard. What exists is a data
layer that can be pointed at real data in Phase 1 without re-litigating what a split
means, and a test suite that fails loudly if anyone — a future Dhruv, a future agent —
tries to look at the answer early.
