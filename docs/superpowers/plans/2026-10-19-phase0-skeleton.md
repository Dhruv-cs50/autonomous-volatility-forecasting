# Phase 0 Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `volab` data layer so that development code cannot read past the configured holdout boundary, and prove it with tests in CI.

**Architecture:** Three pure-ish modules under `src/volab/` — `paths` (layout algebra, no I/O), `config` (typed settings with split validation), `store` (the single sanctioned read path, carrying the holdout guard) — over a shared `errors` taxonomy. Reads go through DuckDB against Hive-partitioned Parquet. Every test runs against synthetic Parquet the suite writes itself; no network, no vendor data.

**Tech Stack:** Python 3.12, uv, DuckDB, pandas, pyarrow, PyYAML, exchange_calendars, pytest, ruff, mypy, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-19-phase0-skeleton-design.md`

## Global Constraints

- Python 3.12, pinned in `.python-version`; CI uses the same version.
- `uv` for all dependency and environment management; `uv sync --locked` in CI so a stale lockfile fails the build, and `--locked` repeated on every `uv run` step so none of them re-locks.
- `mypy` runs in strict mode over `src`.
- Mandatory columns on every Parquet file, in every tier: `symbol` (string), `ts` (timestamp, UTC-aware), `as_of` (date), `ingested_at` (timestamp, UTC-aware).
- Point-in-time filtering and all guard assertions use `as_of`, never `ts`.
- `end` in `load_panel()` is keyword-only and required; there is no default and no "all data" sentinel.
- Unlocking the holdout requires **both** `allow_holdout=True` and environment `VOLAB_HOLDOUT_UNLOCK=1`.
- `VOLAB_HOLDOUT_UNLOCK` is never set in the CI workflow.
- Default config values: `universe: [SPY, QQQ, IWM]`, `calendar: XNYS`, `dev_end: 2023-12-31`, `holdout_start: 2024-01-02`, `storage.root: data`.
- All exceptions raised by `volab` derive from `VolabError`.
- Never silently clip a requested window, and never return an empty frame where the caller expected rows — raise instead.

## Review Focus

Five input classes the spec implies but does not pin, ordered by how likely each is to produce a wrong number rather than an error. Each has a test assigned to the task owning the code.

1. **`end < start`** — a reversed window would return an empty frame that reads as "no data" rather than "bad request". Raise `ValueError`. (Task 5)
2. **Duplicate rows across raw parts** — the same `(symbol, ts)` ingested twice would double-count in every downstream mean and variance. Deduplicate, keeping the row with the greatest `ingested_at`. (Task 5)
3. **A window that legitimately matches no rows** — returning empty invites a silent zero-observation fit. Raise `DatasetNotFound` naming the window. (Task 5)
4. **Timezone-naive `ts`** — naive and aware timestamps compare inconsistently and would misorder a panel. Raise `SchemaError`. (Task 5)
5. **A requested column that does not exist** — a bare `KeyError` from pandas gives no hint which dataset lacked it. Raise `SchemaError` naming the column and dataset. (Task 5)

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Dependencies, ruff/mypy/pytest configuration, package metadata. |
| `.python-version` | Pins 3.12. |
| `.gitignore` | Ignores `data/` contents, keeps `.gitkeep` and the holdout audit log. |
| `README.md` | What the repo is, how to run tests, what the holdout rule is. |
| `.github/workflows/ci.yml` | Lint, format check, type check, test. |
| `config/base.yaml` | Universe, calendar, splits, storage root. |
| `src/volab/errors.py` | Exception taxonomy. No imports from the rest of the package. |
| `src/volab/paths.py` | `Tier`, path builders, `parse_path`, `dataset_glob`. Pure functions, no filesystem access. |
| `src/volab/config.py` | `Settings`, `Splits`, `load_settings`, split validation. |
| `src/volab/store.py` | `load_panel` and the holdout guard. The only module permitted to read Parquet. |
| `tests/conftest.py` | `synthetic_store` fixture; autouse fixture policing holdout unlocks. |
| `tests/test_errors.py` | Taxonomy shape. |
| `tests/test_paths.py` | Builder/parser round-trips. |
| `tests/test_config.py` | Loading and split validation. |
| `tests/test_store_guard.py` | `load_panel` behaviour and all guard rules. |
| `tests/test_import_discipline.py` | No Parquet reads outside `store.py`. |

---

## Task 1: Project bootstrap, error taxonomy and CI

Scaffolding is folded in here because CI needs a package to check and a test to run, and `errors` is the one module with no dependencies.

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `README.md`, `.github/workflows/ci.yml`
- Create: `src/volab/__init__.py`, `src/volab/errors.py`
- Create: `data/raw/.gitkeep`, `data/curated/.gitkeep`, `data/features/.gitkeep`
- Test: `tests/test_errors.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `volab.errors.VolabError`, and subclasses `HoldoutViolation`, `ConfigError`, `DatasetNotFound`, `UnknownSymbol`, `SchemaError`.

- [ ] **Step 1: Install uv if absent**

Run: `command -v uv || brew install uv`

- [ ] **Step 2: Create the project and pin Python 3.12**

Run from the repo root:

```bash
uv init --package --name volab --python 3.12 --no-workspace
uv add duckdb pandas pyarrow pyyaml exchange-calendars
uv add --dev pytest ruff mypy pandas-stubs types-PyYAML
```

`uv init --package` produces the `src/volab/` layout. Keep lower bounds only; `uv.lock` pins the resolved versions.

- [ ] **Step 3: Configure ruff, mypy and pytest in `pyproject.toml`**

Add: ruff `line-length = 100`, lint rules `["E", "F", "I", "UP", "B", "SIM"]`; `[tool.mypy]` with `strict = true` and `files = ["src"]`; `[tool.pytest.ini_options]` registering the marker `holdout: unlocks the sealed holdout window` and setting `testpaths = ["tests"]`.

- [ ] **Step 4: Write `.gitignore` and `README.md`**

`.gitignore` ignores `data/**` with `!data/**/.gitkeep` and `!data/holdout_access.jsonl` exceptions, plus `.venv/`, `__pycache__/`, `.mypy_cache/` and `.pytest_cache/`. The audit log is deliberately *not* ignored: its line count is a number the write-up must report.

`README.md` states what the project is, `uv sync` then `uv run pytest` to get started, and the holdout rule in two sentences: reads go through `load_panel()`, and passing `end` beyond `dev_end` needs both keys.

- [ ] **Step 5: Write the failing test**

`tests/test_errors.py`:

```python
import pytest

from volab.errors import (
    ConfigError,
    DatasetNotFound,
    HoldoutViolation,
    SchemaError,
    UnknownSymbol,
    VolabError,
)


@pytest.mark.parametrize(
    "exc",
    [HoldoutViolation, ConfigError, DatasetNotFound, UnknownSymbol, SchemaError],
)
def test_every_error_derives_from_volab_error(exc: type[Exception]) -> None:
    assert issubclass(exc, VolabError)


def test_volab_error_derives_from_exception() -> None:
    assert issubclass(VolabError, Exception)
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'volab.errors'`

- [ ] **Step 7: Implement `src/volab/errors.py`**

Six classes, bodies are docstrings only: `VolabError(Exception)`, then `HoldoutViolation`, `ConfigError`, `DatasetNotFound`, `UnknownSymbol`, `SchemaError`, each deriving from `VolabError`. Each docstring says when it is raised, copied from spec §8.

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_errors.py -v`
Expected: PASS, 6 passed

- [ ] **Step 9: Write `.github/workflows/ci.yml`**

Trigger on `push` and `pull_request`. One job on `ubuntu-latest`: `actions/checkout@v4`; `astral-sh/setup-uv@v5` with `enable-cache: true` and `python-version: "3.12"`; then steps running `uv sync --locked`, `uv run --locked ruff check .`, `uv run --locked ruff format --check .`, `uv run --locked mypy src`, `uv run --locked pytest -q`. Set no environment variables.

`--locked` throughout, not `--frozen`: `--frozen` skips the staleness comparison entirely, and a bare `uv run` re-locks by default. Either one would let a lockfile that no longer matches `pyproject.toml` sail through green.

- [ ] **Step 10: Verify the full CI command set passes locally**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`
Expected: all four succeed; mypy reports no issues in `src`.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore README.md .github src tests data
git commit -m "feat: bootstrap uv project, error taxonomy and CI"
```

---

## Task 2: Path layout algebra

**Files:**
- Create: `src/volab/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `Tier(StrEnum)` with members `RAW = "raw"`, `CURATED = "curated"`, `FEATURES = "features"`.
  - `PathInfo` — frozen dataclass: `tier: Tier`, `dataset: str`, `symbol: str`, `date: datetime.date`, `ingested_at: datetime | None`.
  - `raw_path(root: Path, source: str, symbol: str, date: date, ingested_at: datetime) -> Path`
  - `curated_path(root: Path, dataset: str, symbol: str, date: date) -> Path`
  - `features_path(root: Path, feature_set: str, symbol: str, date: date) -> Path`
  - `parse_path(path: Path, root: Path) -> PathInfo`
  - `dataset_glob(root: Path, tier: Tier, dataset: str, symbols: Sequence[str] | None = None) -> list[str]`
  - `MANDATORY_COLUMNS: tuple[str, ...] = ("symbol", "ts", "as_of", "ingested_at")`

For the raw tier, `PathInfo.dataset` holds the `source` segment — raw has no separate dataset name. `ingested_at` is populated only for raw paths and is `None` otherwise. The raw filename stamp format is `%Y%m%dT%H%M%SZ`, as in `part-20261019T143000Z.parquet`.

- [ ] **Step 1: Write the failing test**

`tests/test_paths.py`:

```python
import datetime as dt
from pathlib import Path

import pytest

from volab.paths import (
    PathInfo,
    Tier,
    curated_path,
    dataset_glob,
    features_path,
    parse_path,
    raw_path,
)

ROOT = Path("/tmp/store")
DAY = dt.date(2023, 6, 15)
STAMP = dt.datetime(2026, 10, 19, 14, 30, 0, tzinfo=dt.UTC)


def test_raw_path_layout() -> None:
    assert raw_path(ROOT, "massive", "SPY", DAY, STAMP) == (
        ROOT / "raw/massive/SPY/date=2023-06-15/part-20261019T143000Z.parquet"
    )


def test_curated_path_layout() -> None:
    assert curated_path(ROOT, "bars_daily", "SPY", DAY) == (
        ROOT / "curated/bars_daily/SPY/date=2023-06-15/part.parquet"
    )


def test_features_path_layout() -> None:
    assert features_path(ROOT, "har_lags", "QQQ", DAY) == (
        ROOT / "features/har_lags/QQQ/date=2023-06-15/part.parquet"
    )


def test_raw_round_trip() -> None:
    assert parse_path(raw_path(ROOT, "massive", "SPY", DAY, STAMP), ROOT) == PathInfo(
        tier=Tier.RAW, dataset="massive", symbol="SPY", date=DAY, ingested_at=STAMP
    )


@pytest.mark.parametrize(
    ("builder", "tier", "dataset"),
    [(curated_path, Tier.CURATED, "bars_daily"), (features_path, Tier.FEATURES, "har_lags")],
)
def test_single_part_round_trip(builder, tier: Tier, dataset: str) -> None:
    assert parse_path(builder(ROOT, dataset, "IWM", DAY), ROOT) == PathInfo(
        tier=tier, dataset=dataset, symbol="IWM", date=DAY, ingested_at=None
    )


@pytest.mark.parametrize(
    "bad",
    [
        ROOT / "curated/bars_daily/SPY/part.parquet",          # no date partition
        ROOT / "curated/bars_daily/SPY/date=not-a-date/part.parquet",
        ROOT / "nosuchtier/bars_daily/SPY/date=2023-06-15/part.parquet",
        Path("/elsewhere/curated/bars_daily/SPY/date=2023-06-15/part.parquet"),
    ],
)
def test_malformed_path_raises(bad: Path) -> None:
    with pytest.raises(ValueError):
        parse_path(bad, ROOT)


def test_dataset_glob_one_pattern_per_symbol() -> None:
    assert dataset_glob(ROOT, Tier.CURATED, "bars_daily", ["SPY", "QQQ"]) == [
        f"{ROOT}/curated/bars_daily/SPY/date=*/*.parquet",
        f"{ROOT}/curated/bars_daily/QQQ/date=*/*.parquet",
    ]


def test_dataset_glob_wildcards_symbols_when_none() -> None:
    assert dataset_glob(ROOT, Tier.CURATED, "bars_daily") == [
        f"{ROOT}/curated/bars_daily/*/date=*/*.parquet"
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'volab.paths'`

- [ ] **Step 3: Implement `src/volab/paths.py`**

Signatures as in the Interfaces block. `parse_path` resolves both arguments, takes the relative part, and raises `ValueError` when the path is not under `root`, when the tier segment is not a `Tier` member, when the segment count is wrong, when the date segment does not match `date=<ISO date>`, or when a raw filename does not match `part-<stamp>.parquet`. Raise `ValueError` rather than a `VolabError` subclass: a malformed path is a programming error inside the package, not a data or configuration condition the taxonomy describes.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_paths.py -v`
Expected: PASS, 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/volab/paths.py tests/test_paths.py
git commit -m "feat: add Parquet path layout algebra"
```

---

## Task 3: Typed configuration with split validation

**Files:**
- Create: `src/volab/config.py`, `config/base.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `volab.errors.ConfigError`.
- Produces:
  - `Splits` — frozen dataclass: `dev_end: date`, `holdout_start: date`.
  - `Settings` — frozen dataclass: `universe: tuple[str, ...]`, `calendar: str`, `splits: Splits`, `storage_root: Path`.
  - `load_settings(path: Path | None = None) -> Settings` — defaults to `config/base.yaml` relative to the repository root.
  - `DEFAULT_CONFIG_PATH: Path`

Environment overrides are an explicit map, not generic introspection: `VOLAB_UNIVERSE` (comma-separated), `VOLAB_CALENDAR`, `VOLAB_SPLITS_DEV_END`, `VOLAB_SPLITS_HOLDOUT_START`, `VOLAB_STORAGE_ROOT`. An unrecognised `VOLAB_`-prefixed variable is ignored, since `VOLAB_HOLDOUT_UNLOCK` is one.

- [ ] **Step 1: Write `config/base.yaml`**

```yaml
universe: [SPY, QQQ, IWM]
calendar: XNYS
splits:
  dev_end: "2023-12-31"
  holdout_start: "2024-01-02"
storage:
  root: "data"
```

- [ ] **Step 2: Write the failing test**

`tests/test_config.py`. Each invalid case writes a YAML file to `tmp_path` and asserts `ConfigError`:

```python
import datetime as dt
from pathlib import Path

import pytest

from volab.config import Settings, load_settings
from volab.errors import ConfigError

VALID = """
universe: [SPY, QQQ, IWM]
calendar: XNYS
splits:
  dev_end: "2023-12-31"
  holdout_start: "2024-01-02"
storage:
  root: "data"
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(text)
    return path


def test_repo_default_config_loads() -> None:
    settings = load_settings()
    assert settings.universe == ("SPY", "QQQ", "IWM")
    assert settings.calendar == "XNYS"
    assert settings.splits.dev_end == dt.date(2023, 12, 31)
    assert settings.splits.holdout_start == dt.date(2024, 1, 2)
    assert isinstance(settings, Settings)


def test_sunday_dev_end_is_accepted(tmp_path: Path) -> None:
    # 2023-12-31 is a Sunday and 2024-01-01 a holiday, so no session falls between.
    assert load_settings(_write(tmp_path, VALID)).splits.dev_end == dt.date(2023, 12, 31)


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (VALID.replace("[SPY, QQQ, IWM]", "[]"), "empty universe"),
        (VALID.replace("[SPY, QQQ, IWM]", "[SPY, SPY]"), "duplicate symbol"),
        (VALID.replace('"2024-01-02"', '"2023-12-30"'), "holdout_start before dev_end"),
        (VALID.replace('"2024-01-02"', '"2023-12-31"'), "holdout_start equals dev_end"),
        (VALID.replace('"2024-01-02"', '"2024-01-10"'), "sessions fall in the gap"),
        (VALID.replace('"2023-12-31"', '"not-a-date"'), "unparseable date"),
        (VALID.replace('root: "data"', 'root: "/etc"'), "storage root outside repo"),
    ],
)
def test_invalid_config_raises_config_error(tmp_path: Path, bad: str, reason: str) -> None:
    with pytest.raises(ConfigError):
        load_settings(_write(tmp_path, bad))


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "absent.yaml")


def test_env_override_applies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLAB_CALENDAR", "XLON")
    assert load_settings(_write(tmp_path, VALID)).calendar == "XLON"


def test_unknown_volab_env_var_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLAB_HOLDOUT_UNLOCK", "1")
    assert load_settings(_write(tmp_path, VALID)).calendar == "XNYS"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'volab.config'`

- [ ] **Step 4: Implement `src/volab/config.py`**

`load_settings` reads YAML, applies environment overrides, then validates in the spec §5 order, wrapping every failure — including `yaml.YAMLError`, `OSError` and date-parsing `ValueError` — in `ConfigError` with a message naming the offending field.

The gap check uses `exchange_calendars`:

```python
calendar = xcals.get_calendar(name)
gap_start = splits.dev_end + dt.timedelta(days=1)
gap_end = splits.holdout_start - dt.timedelta(days=1)
if gap_start <= gap_end and len(calendar.sessions_in_range(gap_start, gap_end)) > 0:
    raise ConfigError(...)
```

An unknown calendar name surfaces as `ConfigError` too. If `sessions_in_range` proves awkward across `exchange_calendars` versions, fall back to a weekday-and-fixed-holiday check and record the substitution in spec §11's risk table.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS, 12 passed

- [ ] **Step 6: Commit**

```bash
git add src/volab/config.py config/base.yaml tests/test_config.py
git commit -m "feat: add typed config with split validation"
```

---

## Task 4: Test fixtures and holdout-unlock policing

The policing fixture (spec §7f) ships before `store.py` so no later test can unlock the holdout unnoticed, and it is itself tested with `pytester` rather than assumed to work.

**Files:**
- Create: `tests/conftest.py`
- Test: `tests/test_conftest_policy.py`

**Interfaces:**
- Consumes: `volab.config.Settings`, `volab.config.Splits`, `volab.paths` builders and `Tier`.
- Produces:
  - `SyntheticStore` — dataclass with `root: Path`, `settings: Settings`, and
    `write(dataset, symbol, dates, *, tier=Tier.CURATED, as_of=None, drop_columns=(), ts_naive=False, ingested_at=None) -> list[Path]`.
    `as_of` is `Callable[[date], date | None] | None`, defaulting to identity; returning `None` writes a null `as_of`. `drop_columns` omits mandatory columns to provoke `SchemaError`. `ts_naive=True` writes timezone-naive `ts`.
  - `synthetic_store` — fixture returning a `SyntheticStore` under `tmp_path`, with `settings.universe == ("SPY", "QQQ", "IWM")` and the default split dates.
  - `_forbid_holdout_unlock` — autouse fixture.

`store.py` (Task 5) must expose `_unlock_granted(allow_holdout: bool) -> bool` for this fixture to wrap. That coupling is deliberate: the policy needs one function to intercept, and naming it in both tasks keeps it from drifting.

- [ ] **Step 1: Write the failing test**

`tests/test_conftest_policy.py`:

```python
import datetime as dt

import pytest

from volab.paths import Tier, curated_path

pytest_plugins = ["pytester"]


def test_synthetic_store_writes_discoverable_files(synthetic_store) -> None:
    day = dt.date(2023, 6, 15)
    written = synthetic_store.write("bars_daily", "SPY", [day])
    assert written == [curated_path(synthetic_store.root, "bars_daily", "SPY", day)]
    assert written[0].exists()


def test_synthetic_store_settings_match_repo_defaults(synthetic_store) -> None:
    assert synthetic_store.settings.universe == ("SPY", "QQQ", "IWM")
    assert synthetic_store.settings.splits.dev_end == dt.date(2023, 12, 31)
    assert synthetic_store.root == synthetic_store.settings.storage_root


def test_unmarked_test_that_unlocks_holdout_fails(pytester: pytest.Pytester) -> None:
    pytester.makeconftest((pytester.path.parent / "conftest.py").read_text())
    pytester.makepyfile(
        """
        import volab.store as store

        def test_sneaks_past_the_guard(monkeypatch):
            monkeypatch.setenv("VOLAB_HOLDOUT_UNLOCK", "1")
            assert store._unlock_granted(allow_holdout=True)
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*@pytest.mark.holdout*"])


@pytest.mark.holdout
def test_marked_test_may_unlock(monkeypatch: pytest.MonkeyPatch) -> None:
    import volab.store as store

    monkeypatch.setenv("VOLAB_HOLDOUT_UNLOCK", "1")
    assert store._unlock_granted(allow_holdout=True) is True
```

The last two tests import `volab.store`, which Task 5 creates. Expect them to fail on import until then; the first two must pass at the end of this task.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_conftest_policy.py -v`
Expected: FAIL — `fixture 'synthetic_store' not found`

- [ ] **Step 3: Implement `tests/conftest.py`**

`SyntheticStore.write` builds a `pandas.DataFrame` with the mandatory columns, minus `drop_columns`, and writes it with `pyarrow` to the path from the matching builder, creating parent directories. `ts` is midnight UTC on each date unless `ts_naive`; `ingested_at` defaults to a fixed aware timestamp so tests are deterministic.

`_forbid_holdout_unlock` is autouse and session-independent. For a test without the `holdout` marker it deletes `VOLAB_HOLDOUT_UNLOCK` from the environment and monkeypatches `volab.store._unlock_granted` with a wrapper that calls `pytest.fail("test unlocked the holdout without @pytest.mark.holdout")` when the original would return `True`, and otherwise returns `False`. For a marked test it does nothing. Import `volab.store` lazily inside the fixture so Task 4's own tests can run before Task 5 exists.

- [ ] **Step 4: Run tests to verify the fixture tests pass**

Run: `uv run pytest tests/test_conftest_policy.py -v -k "synthetic_store"`
Expected: PASS, 2 passed

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_conftest_policy.py
git commit -m "test: add synthetic store fixture and holdout-unlock policy"
```

---

## Task 5: `load_panel()` and the holdout guard

**Files:**
- Create: `src/volab/store.py`
- Test: `tests/test_store_guard.py`

**Interfaces:**
- Consumes: `volab.errors` (all), `volab.config.Settings`/`load_settings`, `volab.paths.Tier`/`dataset_glob`/`MANDATORY_COLUMNS`, and the `synthetic_store` fixture.
- Produces:
  - `load_panel(dataset: str, *, end: date, symbols: Sequence[str] | None = None, start: date | None = None, columns: Sequence[str] | None = None, tier: Tier = Tier.CURATED, allow_holdout: bool = False, settings: Settings | None = None) -> pd.DataFrame`
  - `_unlock_granted(allow_holdout: bool) -> bool` — `allow_holdout and os.environ.get("VOLAB_HOLDOUT_UNLOCK") == "1"`
  - `AUDIT_FILENAME: str = "holdout_access.jsonl"`

**Check order**, fixed, because which error surfaces first is part of the contract:

1. `symbols` not a subset of `settings.universe` → `UnknownSymbol`
2. `start` present and `end < start` → `ValueError`
3. `end > settings.splits.dev_end` and not `_unlock_granted(...)` → `HoldoutViolation`, raised before any filesystem access
4. the glob matches no files → `DatasetNotFound`
5. read via DuckDB
6. a mandatory column, or a requested `columns` entry, is absent → `SchemaError`
7. `ts` is timezone-naive → `SchemaError`
8. any `as_of` is null → `HoldoutViolation`
9. any row's `as_of` differs from its Hive `date` partition value → `HoldoutViolation` (assertion e1)
10. filter to `as_of <= end`, and `>= start` when given; then deduplicate on `(symbol, ts)` keeping the greatest `(as_of, ingested_at)` — the latest *vintage* in the window wins, with `ingested_at` breaking ties inside one vintage and nothing more. Rows tying on all of `(symbol, ts, as_of, ingested_at)` are irreducible, so they raise `SchemaError` rather than having one of them dropped
11. the filtered frame is empty → `DatasetNotFound`
12. `max(as_of) > end` → `HoldoutViolation` (assertion e2)
13. if `end` crossed `dev_end`, append one audit line — the trigger is crossing the boundary, not holding the two keys, matching the amended spec §7d. The line is written whether the read succeeded or was refused, and its `outcome` field says which: a refused holdout read is still an attempt on the sealed window, and recording only successes would leave unlimited unrecorded probing of it. The exception is re-raised untouched.

Step 9 must precede step 10, and that ordering is the whole substance of spec rule (e). A file parked under the wrong partition date would otherwise be *discarded* by the filter and reported as `DatasetNotFound` — a plausible-looking answer derived from corrupt storage. Checking partition consistency before filtering turns a silent discard into a loud failure.

Step 12 is unreachable while step 9 and the filter are both correct. Keep it anyway; it costs one comparison. It has no test, for the same reason — reaching it requires first breaking step 9.

Read all rows matching the glob rather than pruning partitions on `end`: step 9 needs to see rows that the window would exclude. Correctness never depends on pruning (spec §11).

The audit record is one JSON object per line in `settings.storage_root / AUDIT_FILENAME`, with keys `ts`, `dataset`, `tier`, `end`, `start`, `symbols`, `caller_module`, `caller_lineno`, `git_sha`, `git_dirty`. The caller is the first frame in `inspect.stack()` whose file is outside the `volab` package directory. `git_sha` and `git_dirty` come from `git rev-parse HEAD` and `git status --porcelain`, both falling back to `"unknown"` and `None` when git is unavailable — an audit line that cannot name the commit is still worth more than no line.

- [ ] **Step 1: Write the failing test**

`tests/test_store_guard.py`. `DEV_DAY = date(2023, 6, 15)`, `HOLDOUT_DAY = date(2024, 3, 1)`.

```python
def test_dev_window_read_returns_rows(synthetic_store) -> None: ...
    # writes DEV_DAY and DEV_DAY - 1 day; load_panel(end=DEV_DAY) returns 2 rows

def test_returns_only_rows_at_or_before_end(synthetic_store) -> None: ...
    # writes three consecutive days; end = middle day; asserts max(as_of) == end and len == 2

def test_start_filters_lower_bound(synthetic_store) -> None: ...

def test_columns_subsets_the_frame(synthetic_store) -> None: ...
    # columns=["symbol", "as_of"]; asserts exactly those columns

def test_symbol_outside_universe_raises_unknown_symbol(synthetic_store) -> None: ...
    # symbols=["TSLA"] -> UnknownSymbol

def test_symbol_in_universe_without_files_raises_dataset_not_found(synthetic_store) -> None: ...
    # writes SPY only, requests QQQ -> DatasetNotFound

def test_end_after_dev_end_raises_without_keys(synthetic_store) -> None: ...
    # end=HOLDOUT_DAY -> HoldoutViolation

def test_allow_holdout_alone_raises(synthetic_store, monkeypatch) -> None: ...
    # delenv VOLAB_HOLDOUT_UNLOCK; allow_holdout=True -> HoldoutViolation

def test_env_var_alone_raises(synthetic_store, monkeypatch) -> None: ...
    # setenv 1; allow_holdout=False -> HoldoutViolation

def test_holdout_guard_raises_before_touching_filesystem(synthetic_store, monkeypatch) -> None: ...
    # monkeypatch volab.store._scan to raise AssertionError; expects HoldoutViolation, not AssertionError

def test_mislabelled_partition_raises_even_inside_dev_window(synthetic_store) -> None: ...
    # write("bars_daily", "SPY", [DEV_DAY], as_of=lambda d: HOLDOUT_DAY) writes a file at
    # date=2023-06-15 whose rows claim as_of 2024-03-01; load_panel(end=DEV_DAY) must
    # raise HoldoutViolation from step 9, NOT DatasetNotFound from step 11

def test_missing_mandatory_column_raises_schema_error(synthetic_store) -> None: ...
    # drop_columns=("as_of",) -> SchemaError

def test_null_as_of_raises_holdout_violation(synthetic_store) -> None: ...
    # as_of=lambda d: None -> HoldoutViolation

def test_naive_ts_raises_schema_error(synthetic_store) -> None: ...
    # ts_naive=True -> SchemaError

def test_unknown_requested_column_raises_schema_error(synthetic_store) -> None: ...
    # columns=["nope"] -> SchemaError naming "nope"

def test_reversed_window_raises_value_error(synthetic_store) -> None: ...
    # start=DEV_DAY, end=DEV_DAY - 10 days -> ValueError

def test_empty_result_raises_dataset_not_found(synthetic_store) -> None: ...
    # writes DEV_DAY; end = DEV_DAY - 30 days -> DatasetNotFound

def test_duplicate_rows_are_deduplicated_keeping_latest_ingest(synthetic_store) -> None: ...
    # two write(..., tier=Tier.RAW, ingested_at=...) calls for the same (SPY, DEV_DAY)
    # with different ingested_at, producing two part files in one partition;
    # load_panel(..., tier=Tier.RAW) returns one row, carrying the later ingested_at

def test_empty_dataset_directory_raises_dataset_not_found(synthetic_store) -> None: ...

@pytest.mark.holdout
def test_both_keys_return_holdout_rows_and_write_one_audit_line(
    synthetic_store, monkeypatch
) -> None: ...
    # setenv 1; allow_holdout=True; end=HOLDOUT_DAY
    # asserts rows returned, audit file has exactly 1 line, and its
    # "end" and "dataset" fields match the request

@pytest.mark.holdout
def test_dev_window_read_writes_no_audit_line(synthetic_store) -> None: ...
    # a locked, in-window read leaves no audit file
```

Fill in each body following the comment. Every test obtains `Settings` from `synthetic_store.settings` and passes it explicitly.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'volab.store'`

- [ ] **Step 3: Implement `src/volab/store.py`**

Follow the check order above exactly. Factor the DuckDB read into `_scan(patterns: list[str]) -> pd.DataFrame` so the test at step 1 can monkeypatch it to prove rule (b) precedes any I/O. It takes no `columns`: the read is always of every column, for the reason two paragraphs down, so there is nothing to pass. Keep `_unlock_granted`, `_append_audit` and `_caller_frame` module-level and prefixed with an underscore.

Query shape: `duckdb.sql("SELECT * FROM read_parquet($patterns, hive_partitioning := true)")` with the pattern list bound as a parameter, then `.df()`. `hive_partitioning := true` supplies the `date` column that step 9 compares against.

Always read the mandatory columns plus `date`, whatever `columns` asks for, and subset to the requested columns as the last operation before returning. Steps 7 through 12 need `ts`, `as_of`, `ingested_at` and `date`; honouring a narrow `columns` at scan time would disable the checks the caller is relying on without knowing it.

Deduplication happens in pandas after the read via `sort_values("ingested_at").drop_duplicates(["symbol", "ts"], keep="last")`, not in SQL, so the ordering rule stays visible next to the filter it guards.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store_guard.py -v`
Expected: PASS, 21 passed

- [ ] **Step 5: Run the previously blocked policy tests**

Run: `uv run pytest tests/test_conftest_policy.py -v`
Expected: PASS, 4 passed — including `test_unmarked_test_that_unlocks_holdout_fails`

- [ ] **Step 6: Commit**

```bash
git add src/volab/store.py tests/test_store_guard.py
git commit -m "feat: add load_panel with two-key holdout guard and audit log"
```

---

## Task 6: Enforce `store.py` as the only Parquet reader

Spec §11: the guard protects `load_panel()`, not `pandas.read_parquet`. A test makes the restriction real, and it is the same restriction that later makes the agent's missing `read_parquet` tool meaningful rather than decorative.

**Files:**
- Create: `tests/test_import_discipline.py`
- Modify: `README.md` — add the rule to the holdout section

**Interfaces:**
- Consumes: nothing; inspects source files as text.
- Produces: nothing importable.

- [ ] **Step 1: Write the failing test**

`tests/test_import_discipline.py` is the source of truth for the ratchet; read it rather than any copy here. An earlier draft of the scan lived in this plan and was superseded during Task 6, which made the plan the most misleading description of it available.

As built, the ratchet parses every `*.py` under the installed `volab` package with `ast` and reports every way a module could reach Parquet other than through `load_panel`:

- It recurses (`rglob`) and exempts the top-level `store.py` by its path relative to the package root, never a `store.py` in a subpackage.
- Banned names cover the third-party readers (`read_parquet`, `scan_parquet`, `read_table`, `ParquetFile`, `ParquetDataset`) *and* store's own private read helpers (`_scan`, `_matching_files`), each caught as an attribute, as a bare name, or as an imported name.
- Banned imports are matched on the **top-level package** (`duckdb`, `pyarrow`), so a submodule nobody anticipated — `pyarrow.dataset` — cannot slip past a list of exact module names.
- Importing *any* private name from the store module, by absolute or relative path, is itself a violation: a private store helper reads Parquet with none of `load_panel`'s guard.

Each rule has its own table-driven case asserting the scan reports it, alongside cases asserting that ordinary code is accepted, so the ratchet's blind spots are visible rather than assumed.

- [ ] **Step 2: Run test to verify it passes already**

Run: `uv run pytest tests/test_import_discipline.py -v`
Expected: PASS — the rule holds at the moment it is written. This test is a ratchet, not a bug fix: it exists to fail on a future change.

- [ ] **Step 3: Verify the ratchet actually bites**

Temporarily add `import duckdb` to `src/volab/config.py`, run the test, confirm it fails naming `config.py`, then revert.

Run: `uv run pytest tests/test_import_discipline.py -v`
Expected: FAIL while the import is present; PASS after reverting.

- [ ] **Step 4: Document the rule in `README.md`**

One sentence: all Parquet reads go through `volab.store`, enforced by `tests/test_import_discipline.py`.

- [ ] **Step 5: Run the whole CI command set**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`
Expected: all four succeed; the suite reports the full count with no failures or skips other than none.

- [ ] **Step 6: Commit**

```bash
git add tests/test_import_discipline.py README.md
git commit -m "test: enforce store.py as the only Parquet reader"
```

---

## Exit Criteria Verification

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q` all pass locally.
- [ ] CI is green on `main` after push.
- [ ] `test_end_after_dev_end_raises_without_keys` passes — spec exit criterion 2.
- [ ] `test_allow_holdout_alone_raises` and `test_env_var_alone_raises` pass — exit criterion 3.
- [ ] `test_both_keys_return_holdout_rows_and_write_one_audit_line` passes — exit criterion 4.
- [ ] `data/holdout_access.jsonl` is absent because no holdout read has occurred, and tracked once one does. It is version-controlled evidence, not scratch: an artefact `git clean -xdf` erases cannot be the line count the write-up has to report (spec §7d).
