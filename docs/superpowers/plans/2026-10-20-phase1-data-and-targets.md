# Phase 1 — Data and Targets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest free daily bars, the Cboe VIX family and FRED macro series into the immutable
store, derive trailing-window realized measures and forward targets from them, and prove by
mutation that no feature value at `t` moves when data after `t` changes.

**Architecture:** Fetchers return DataFrames and never touch Parquet; `store.py` gains the only
write path, mirroring its position as the only read path. Curation turns faithful raw into a
typed panel. The feature engine takes an already-loaded panel plus a separate evaluation
cutoff, so a forward-peeking feature is detectable — which it would not be if the engine let
`load_panel` filter for it.

**Tech Stack:** Python 3.12, pandas, numpy, exchange_calendars, pyarrow (inside `store.py`
only), stdlib `urllib`, pytest, ruff, mypy, uv.

**Spec:** `docs/superpowers/specs/2026-10-20-phase1-data-and-targets-design.md`

## Global Constraints

- Every command runs as `uv run --locked ...`. Never bare `uv run`, never `--frozen`.
- **No new dependencies.** pandas, numpy and exchange_calendars are present and sufficient;
  HTTP uses stdlib `urllib.request`.
- ruff line-length 100; `select = ["E","F","I","UP","B","SIM"]`; mypy `strict` over `src`.
- pytest runs with `filterwarnings = ["error"]`. A warning is a failure.
- **No module under `src/volab/` other than top-level `store.py` may import `duckdb` or
  `pyarrow`, or name `read_parquet`, `scan_parquet`, `read_table`, `ParquetFile`,
  `ParquetDataset`, `_scan`, `_matching_files`.** `ALLOWED` in
  `tests/test_import_discipline.py` stays exactly `{"store.py"}`.
- All reads go through `store.load_panel()`; `end` is required on every call.
- `dev_end = 2025-12-31`, `holdout_start = 2026-01-02`, pinned absolute literals. **Nothing may
  compute a split boundary from `today()`, `now()` or a trailing offset.**
- No code path passes `end > dev_end`. `allow_holdout` is never set and
  `VOLAB_HOLDOUT_UNLOCK` is never read.
- Every synthetic date in `tests/test_point_in_time.py` is `<= 2025-12-31`, or the autouse
  `_forbid_holdout_unlock` fixture fails the test.
- Store inception is **2016-10-19**; ingestion starts at **2016-07-01** for exogenous series.
- Daily-return estimators are named `*_proxy_*`. Never "realized semivariance" or "bipower
  variation" unqualified.
- No CI job makes a network call.

## Review Focus

Five input classes the spec implies that no task's own happy path exercises. Each has a test
added to the task that owns the code.

1. **FRED serves `.` as its missing-value marker**, not an empty field. Parsed naively it
   becomes a crash or a silent string column. A reader expects the row to be absent or null,
   never a zero. → Task 4.
2. **Nasdaq serves prices as `"$123.45"` strings**, and will also serve `"N/A"` and
   thousands separators (`"$1,234.56"`). Curation must parse the separators and **fail loudly**
   on anything it does not recognise, never coerce to NaN and continue. → Task 5.
3. **Missing sessions inside the panel.** A halt or an outage leaves a session with no bar. A
   row-indexed `.rolling(22)` would then span 23+ sessions and quietly mislabel the window. A
   reader expects a 22-session window to mean 22 sessions. → Task 8.
4. **A vendor date that is not an XNYS session.** Ingesting it would put a row in a partition
   the calendar does not recognise and misalign every window built on the session index. A
   reader expects it rejected, not absorbed. → Task 5.
5. **Re-running the backfill on the same day.** The raw filename stamp has whole-second
   precision, so a second run collides. A reader expects a clear refusal naming the path, not a
   clobber and not a half-written file. → Task 2.

---

## File Structure

**Modify:**
- `config/base.yaml` — new split dates; new `exogenous` key
- `src/volab/config.py` — `Settings.exogenous`, validation, env override
- `src/volab/store.py` — three `write_*` functions; step-1 symbol check widened
- `tests/test_config.py` — repo-default date assertions; `exogenous` cases

**Create:**
- `src/volab/sessions.py` — session arithmetic shared by ingest and features
- `src/volab/ingest/{__init__,_http,bars,vix,fred,curate,__main__}.py`
- `src/volab/features/{__init__,realized,har,targets,build,__main__}.py`
- `tests/fixtures/` — synthetic vendor payloads, hand-written, no vendor data
- `tests/{test_sessions,test_store_write,test_ingest_bars,test_ingest_vix,test_ingest_fred,test_curate,test_realized,test_targets,test_features_build,test_point_in_time}.py`
- `docs/superpowers/phase1-decisions.md`

---

## Task 1: The split moves, and `exogenous` arrives

**Files:**
- Modify: `config/base.yaml`, `src/volab/config.py`, `src/volab/store.py`, `tests/test_config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.exogenous: tuple[str, ...]`; `load_panel` accepts symbols from
  `universe | exogenous` while `symbols=None` still scopes to `universe` alone.

- [ ] **Step 1: Write the failing tests**

```python
def test_repo_default_config_loads() -> None:            # edit the two existing assertions
    settings = load_settings()
    assert settings.splits.dev_end == dt.date(2025, 12, 31)
    assert settings.splits.holdout_start == dt.date(2026, 1, 2)

def test_repo_default_exogenous() -> None:
    assert load_settings().exogenous == (
        "VIX", "VIX9D", "VIX1D", "VVIX", "BAA10Y", "DGS10", "T10Y2Y",
    )

def test_exogenous_may_not_overlap_universe(tmp_path: Path) -> None:
    text = VALID.replace("exogenous: [VIX]", "exogenous: [SPY]")
    with pytest.raises(ConfigError, match="both universe and exogenous"):
        load_settings(_write(tmp_path, text))

@pytest.mark.parametrize("value", ["[]", "[VIX, VIX]", '[" VIX"]', "[1]", "VIX"])
def test_exogenous_is_validated_like_universe(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="exogenous"):
        load_settings(_write(tmp_path, VALID.replace("exogenous: [VIX]", f"exogenous: {value}")))

def test_exogenous_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOLAB_EXOGENOUS", "VIX, VVIX")
    assert load_settings(_write(tmp_path, VALID)).exogenous == ("VIX", "VVIX")

def test_load_panel_accepts_an_exogenous_symbol(synthetic_store) -> None:
    synthetic_store.write("vix_family", "VIX", [dt.date(2023, 6, 15)])
    frame = load_panel("vix_family", symbols=["VIX"], end=dt.date(2023, 6, 15),
                       settings=synthetic_store.settings)
    assert list(frame["symbol"]) == ["VIX"]

def test_load_panel_none_symbols_still_scopes_to_universe(synthetic_store) -> None:
    synthetic_store.write("vix_family", "VIX", [dt.date(2023, 6, 15)])
    with pytest.raises(DatasetNotFound):
        load_panel("vix_family", end=dt.date(2023, 6, 15), settings=synthetic_store.settings)

def test_load_panel_still_rejects_an_unknown_symbol(synthetic_store) -> None:
    with pytest.raises(UnknownSymbol, match="TLT"):
        load_panel("bars_daily", symbols=["TLT"], end=dt.date(2023, 6, 15),
                   settings=synthetic_store.settings)
```

Add `exogenous: [VIX]` to `test_config.py`'s `VALID` fixture. Leave every other `VALID`-driven
case, including `test_sunday_dev_end_is_accepted`, on its existing dates.

`synthetic_store`'s `Settings` in `tests/conftest.py` needs `exogenous=("VIX","VIX9D","VIX1D","VVIX","BAA10Y","DGS10","T10Y2Y")`; its `dev_end` stays `2023-12-31`, which is now *earlier*
than the repository's and so still passes `load_panel` step 0.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/test_config.py -v`
Expected: FAIL — `Settings` has no attribute `exogenous`; date assertions mismatch.

- [ ] **Step 3: Add `exogenous` to the configuration**

In `config/base.yaml`: set the two dates; add
`exogenous: [VIX, VIX9D, VIX1D, VVIX, BAA10Y, DGS10, T10Y2Y]`.

In `src/volab/config.py`: add `exogenous: tuple[str, ...]` to `Settings`; add
`"exogenous": "VOLAB_EXOGENOUS"` to `_ENV_OVERRIDES`; apply the same comma-split env handling
`universe` gets. Reuse `_validate_universe`'s body for both by renaming it to
`_validate_symbol_list(field: str, value: object) -> tuple[str, ...]` and passing the field name
into its messages — the rules are identical and duplicating them would let them drift. Then
reject any symbol present in both lists with a message containing
`"both universe and exogenous"`.

- [ ] **Step 4: Widen `load_panel`'s step 1**

In `src/volab/store.py` step 1, validate `symbols` against `set(universe) | set(exogenous)`
instead of `universe` alone, and name both in the `UnknownSymbol` message. Leave the
`scope_symbols = resolved.universe if symbols is None else symbols` line in step 4 **unchanged**
— that is what keeps `symbols=None` scoped to the universe.

- [ ] **Step 5: Run the full suite**

Run: `uv run --locked pytest -q`
Expected: PASS, all 198 existing tests plus the new ones. Any other failure is a test to fix,
never the rule.

- [ ] **Step 6: Run the gates and commit**

```bash
uv run --locked ruff check . && uv run --locked ruff format --check . && uv run --locked mypy src
git add config/base.yaml src/volab/config.py src/volab/store.py tests/test_config.py tests/conftest.py
git commit -m "feat: move the split to 2025-12-31 and add the exogenous symbol class"
```

---

## Task 2: The write path

**Files:**
- Modify: `src/volab/store.py`
- Test: `tests/test_store_write.py`

**Interfaces:**
- Consumes: Task 1's `Settings`.
- Produces:
  ```python
  def write_raw(frame: pd.DataFrame, *, source: str, symbol: str, as_of: dt.date,
                ingested_at: dt.datetime, settings: Settings | None = None) -> Path
  def write_curated(frame: pd.DataFrame, *, dataset: str, symbol: str, as_of: dt.date,
                    settings: Settings | None = None) -> Path
  def write_features(frame: pd.DataFrame, *, feature_set: str, symbol: str, as_of: dt.date,
                     settings: Settings | None = None) -> Path
  ```
  Each returns the path written. Arrow types are pinned: `as_of` → `date32`, `ts` →
  `timestamp("us", tz=...)`, `ingested_at` → `timestamp("us", tz="UTC")`, matching what
  `tests/conftest.py` writes and what `load_panel`'s `_as_dates` expects.

- [ ] **Step 1: Write the failing tests**

```python
def test_write_curated_roundtrips_through_load_panel(synthetic_store) -> None: ...
def test_write_raw_filename_carries_the_ingested_at_stamp(synthetic_store) -> None: ...
def test_write_features_roundtrips(synthetic_store) -> None: ...

@pytest.mark.parametrize("missing", ["symbol", "ts", "as_of", "ingested_at"])
def test_write_requires_every_mandatory_column(synthetic_store, missing: str) -> None:
    with pytest.raises(SchemaError, match=missing): ...

def test_write_rejects_a_naive_ts(synthetic_store) -> None:
    with pytest.raises(SchemaError, match="timezone-aware"): ...

def test_write_rejects_a_row_whose_as_of_differs_from_the_partition(synthetic_store) -> None:
    with pytest.raises(SchemaError, match="contradicts"): ...

def test_write_rejects_an_empty_frame(synthetic_store) -> None:
    with pytest.raises(SchemaError, match="no rows"): ...

def test_write_rejects_a_null_as_of(synthetic_store) -> None:
    with pytest.raises(SchemaError, match="null as_of"): ...

# Review Focus 5
def test_write_raw_refuses_to_overwrite_an_existing_file(synthetic_store) -> None:
    kwargs = dict(source="nasdaq_bars", symbol="SPY", as_of=dt.date(2023, 6, 15),
                  ingested_at=DEFAULT_INGESTED_AT)
    write_raw(frame, **kwargs)
    with pytest.raises(VolabError, match="already exists"):
        write_raw(frame, **kwargs)

def test_write_curated_overwrites_idempotently(synthetic_store) -> None:
    # same args twice must succeed and leave one file
def test_write_leaves_no_temp_file_behind(synthetic_store) -> None:
    # after a successful write, the partition directory holds exactly one file
def test_a_failed_write_leaves_no_partial_file(synthetic_store, monkeypatch) -> None:
    # make the Arrow write raise; assert the destination path does not exist
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/test_store_write.py -v`
Expected: FAIL — `write_raw` is not defined.

- [ ] **Step 3: Implement the three functions in `src/volab/store.py`**

One private `_write(frame, path, *, as_of, allow_overwrite) -> Path` does the work; the three
public functions differ only in which `paths` builder they call and whether overwrite is
allowed (`False` for raw, `True` for curated and features). Validation order: mandatory columns,
`ts` tz-awareness, non-empty, null `as_of`, then every row's `as_of == as_of`. Write to a
`NamedTemporaryFile` in the destination directory, then `os.replace` onto the final path; on any
exception remove the temp file. `write_raw` checks `path.exists()` before writing and raises
with the path in the message.

Add a `RawOverwrite(VolabError)` to `src/volab/errors.py` with a docstring saying raw files are
immutable and that a same-second re-run is the expected cause.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --locked pytest tests/test_store_write.py -v`
Expected: PASS

- [ ] **Step 5: Confirm the ratchet is untouched**

Run: `uv run --locked pytest tests/test_import_discipline.py -q`
Expected: PASS, with `ALLOWED == {"store.py"}` unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/volab/store.py src/volab/errors.py tests/test_store_write.py
git commit -m "feat: add the sanctioned write path, with immutable raw"
```

---

## Task 3: Session arithmetic

**Files:**
- Create: `src/volab/sessions.py`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Produces:
  ```python
  def next_session_after(calendar: xcals.ExchangeCalendar, day: dt.date) -> dt.date
  def nth_session_after(calendar: xcals.ExchangeCalendar, day: dt.date, n: int) -> dt.date
  def sessions_between(calendar: xcals.ExchangeCalendar, start: dt.date, end: dt.date) -> list[dt.date]
  def is_session(calendar: xcals.ExchangeCalendar, day: dt.date) -> bool
  ```
  All take and return plain `dt.date`. `day` need not itself be a session. `n >= 1`.

- [ ] **Step 1: Write the failing tests**

```python
def test_next_session_after_a_session() -> None:
    assert next_session_after(CAL, dt.date(2023, 12, 29)) == dt.date(2024, 1, 2)

def test_next_session_after_a_non_session() -> None:
    assert next_session_after(CAL, dt.date(2023, 12, 31)) == dt.date(2024, 1, 2)

def test_next_session_skips_the_new_year_holiday() -> None:
    assert next_session_after(CAL, dt.date(2025, 12, 31)) == dt.date(2026, 1, 2)

def test_nth_session_after_crosses_a_holiday_and_a_year_boundary() -> None:
    assert nth_session_after(CAL, dt.date(2023, 12, 29), 1) == dt.date(2024, 1, 2)
    assert nth_session_after(CAL, dt.date(2023, 12, 29), 2) == dt.date(2024, 1, 3)

def test_nth_session_after_rejects_a_non_positive_n() -> None:
    with pytest.raises(ValueError): nth_session_after(CAL, dt.date(2023, 6, 15), 0)

def test_sessions_between_is_inclusive_and_excludes_weekends() -> None: ...
def test_is_session_on_a_holiday() -> None:
    assert not is_session(CAL, dt.date(2026, 1, 1))
```

Module-level `CAL = xcals.get_calendar("XNYS")`, built once — constructing it per test is slow
enough to matter.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/test_sessions.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `src/volab/sessions.py`**

Thin wrappers over `exchange_calendars`, converting its `Timestamp` returns to `dt.date`. Which
underlying call to use is the implementer's choice; the assertions above fix the behaviour. Raise
`ValueError` for `n < 1`. Let `exchange_calendars`' own out-of-bounds error propagate.

- [ ] **Step 4: Run the tests to verify they pass, then commit**

```bash
uv run --locked pytest tests/test_sessions.py -v
git add src/volab/sessions.py tests/test_sessions.py
git commit -m "feat: add session arithmetic for as_of lags and target horizons"
```

---

## Task 4: Fetchers, and the bars backfill

**Files:**
- Create: `src/volab/ingest/__init__.py`, `_http.py`, `bars.py`, `vix.py`, `fred.py`
- Create: `tests/fixtures/` (synthetic payloads), `tests/test_ingest_bars.py`,
  `tests/test_ingest_vix.py`, `tests/test_ingest_fred.py`
- Modify: `docs/superpowers/phase1-decisions.md`

**Interfaces:**
- Consumes: Task 2's `write_raw`, Task 3's `next_session_after`.
- Produces:
  ```python
  Transport = Callable[[str], bytes]
  def default_transport(url: str) -> bytes            # _http.py; sets the browser User-Agent
  def fetch_bars(symbol: str, *, start: dt.date, end: dt.date, transport: Transport) -> pd.DataFrame
  def fetch_dividends(symbol: str, *, transport: Transport) -> pd.DataFrame
  def fetch_vix(series: str, *, transport: Transport) -> pd.DataFrame
  def fetch_fred(series: str, *, transport: Transport) -> pd.DataFrame
  FRED_LAG_SESSIONS: dict[str, int]                   # fred.py; default 1
  ```
  Each `fetch_*` returns the vendor's fields **as served** — Nasdaq prices stay `"$123.45"`
  strings — plus `symbol`, `ts`, `as_of`, `ingested_at`, `source_url`, `vendor_date_text`.
  ```python
  def archive_payload(body: bytes, *, source: str, symbol: str, fetched_at: dt.datetime,
                      extension: str, settings: Settings | None = None) -> Path
  def build_raw(source: str, *, symbols: Sequence[str], since: dt.date = INGEST_FLOOR) -> int
  INGEST_FLOOR: dt.date = dt.date(2016, 7, 1)
  ```
  `fetch_*` only fetches and parses. `archive_payload` writes the untouched response body to
  `data/vendor_payloads/<source>/<symbol>/<fetched_at>.<ext>.gz`; `build_raw` replays those
  archived payloads into raw Parquet via `write_raw`, dropping every row with `as_of <
  INGEST_FLOOR`. The two-step split is what makes a rebuild possible once Nasdaq's trailing
  window has moved past the data, and it is the only reason `fetch` and `build-raw` are separate
  CLI verbs.

- [ ] **Step 1: Write the fixtures and the failing tests**

Fixtures are hand-written, ~5 rows each, committed: `nasdaq_bars_spy.json`,
`nasdaq_dividends_qqq.json`, `cboe_vix.csv`, `cboe_vvix.csv` (two-column, unlike the other
three), `fred_baa10y.csv`.

```python
def test_fetch_bars_parses_the_fixture() -> None:
    frame = fetch_bars("SPY", start=..., end=..., transport=fixture_transport)
    assert list(frame.columns) >= ["symbol", "ts", "as_of", "ingested_at", "close"]
    assert frame["close"].iloc[0] == "$123.45"          # as served, unparsed

def test_fetch_bars_sets_as_of_to_the_session_date() -> None: ...
def test_fetch_bars_ts_is_timezone_aware() -> None: ...
def test_fetch_vix_parses_the_mm_dd_yyyy_dates() -> None: ...
def test_fetch_vvix_handles_its_two_column_shape() -> None: ...

def test_fetch_fred_lags_as_of_by_one_session() -> None:
    # an observation dated 2023-12-29 carries as_of 2024-01-02
def test_fred_lag_table_defaults_to_one_session() -> None:
    assert FRED_LAG_SESSIONS.get("DGS10", 1) == 1

# Review Focus 1
def test_fetch_fred_treats_a_dot_as_missing() -> None:
    # the fixture carries a "." value; that row is absent or null, never 0.0
    assert frame["value"].isna().sum() == 1

def test_default_transport_is_never_called_in_tests() -> None:
    # guards the suite against making a network call

def test_archive_payload_writes_the_body_untouched(tmp_path) -> None:
    # the gz round-trips to exactly the bytes handed in
def test_build_raw_replays_an_archived_payload_without_the_network(synthetic_store) -> None:
    # transport raises if called; raw is still written from the archive
def test_build_raw_drops_rows_before_the_ingest_floor(synthetic_store) -> None:
    # a 2016-06-30 observation is dropped; 2016-07-01 is kept
    assert INGEST_FLOOR == dt.date(2016, 7, 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/test_ingest_bars.py tests/test_ingest_vix.py tests/test_ingest_fred.py -v`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement the fetchers**

`_http.py`: `default_transport` wraps `urllib.request.urlopen` with the browser `User-Agent`
Nasdaq requires, a timeout, and a bounded retry with backoff. Nothing else in `ingest/` calls
`urllib` directly.

`archive_payload` and `build_raw` live in `__init__.py`, since both are source-agnostic.
Cboe and FRED serve their whole history on every request and take no date arguments, so the
`INGEST_FLOOR` cut happens in `build_raw` rather than in any fetcher — one place, applied to
every source, including bars.

`bars.py`, `vix.py`, `fred.py`: parse their own payload shape into a DataFrame, attach the four
mandatory columns and provenance, and leave every vendor value as served. `as_of` is the session
date for bars and VIX, and `next_session_after(cal, observation_date)` repeated
`FRED_LAG_SESSIONS.get(series, 1)` times for FRED. FRED's `.` becomes `pd.NA`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --locked pytest tests/test_ingest_bars.py tests/test_ingest_vix.py tests/test_ingest_fred.py -v`
Expected: PASS

- [ ] **Step 5: Back up and run the bars backfill for ONE symbol, then measure**

This is the step the spec's section 13 urgency is about, and it runs before the other ~86,000
files exist.

```bash
uv run --locked python -m volab.ingest fetch --source nasdaq_bars --symbols SPY
uv run --locked python -m volab.ingest build-raw --source nasdaq_bars --symbols SPY
```

Then time a `load_panel` over the result and record both numbers.

**Budget: a `load_panel` of one symbol's curated bars under 30 seconds.** If it is missed,
**STOP** and propose lever (b) from spec section 12 — partition by `date=YYYY-MM` and weaken
step 9 from equality to containment — rather than writing the rest of the store.

Record in `docs/superpowers/phase1-decisions.md`: the file count, the wall-clock numbers, and
the store inception date actually observed (expected 2016-10-19; it drifts one session per day,
so note the real one).

- [ ] **Step 6: Run the remaining backfill and commit**

```bash
uv run --locked python -m volab.ingest backfill          # all symbols, all sources
git add src/volab/ingest tests/fixtures tests/test_ingest_*.py docs/superpowers/phase1-decisions.md
git commit -m "feat: add keyless fetchers for bars, the VIX family and FRED"
```

`data/` stays untracked. The payload archive is deliberately not committed — spec section 13.

---

## Task 5: Curation

**Files:**
- Create: `src/volab/ingest/curate.py`, `src/volab/ingest/__main__.py`
- Test: `tests/test_curate.py`

**Interfaces:**
- Consumes: Task 2's `write_curated`, Task 3's `is_session`, Task 4's raw datasets.
- Produces: `curated/bars_daily` with typed `open/high/low/close/volume` floats and a boolean
  `has_distribution`; `curated/vix_family` with `open/high/low/close`; `curated/fred_daily` with
  `value`. Plus:
  ```python
  def curate_bars(symbol: str, *, end: dt.date, settings: Settings | None = None) -> int
  def curate_vix(series: str, *, end: dt.date, settings: Settings | None = None) -> int
  def curate_fred(series: str, *, end: dt.date, settings: Settings | None = None) -> int
  ```
  Each returns the number of partitions written. `end` is always `settings.splits.dev_end`.

- [ ] **Step 1: Write the failing tests**

```python
def test_curate_bars_parses_dollar_strings(synthetic_store) -> None:
    # "$123.45" -> 123.45 ; "$1,234.56" -> 1234.56

# Review Focus 2
@pytest.mark.parametrize("bad", ["N/A", "", "--", "1.2.3"])
def test_curate_bars_fails_loudly_on_an_unparseable_price(synthetic_store, bad: str) -> None:
    with pytest.raises(SchemaError, match="could not parse"): ...

# Review Focus 4
def test_curate_bars_rejects_a_non_session_date(synthetic_store) -> None:
    with pytest.raises(SchemaError, match="not an XNYS session"): ...

def test_curate_bars_sets_has_distribution_for_qqq(synthetic_store) -> None: ...
def test_curate_bars_has_distribution_is_false_for_spy(synthetic_store) -> None:
    # no keyless source exists; the column is present and uniformly False
def test_curate_never_passes_end_past_dev_end(synthetic_store) -> None:
    # monkeypatch load_panel and assert every observed end <= dev_end
def test_curate_is_idempotent(synthetic_store) -> None:
    # running twice writes the same partitions and does not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/test_curate.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `curate.py` and the CLI**

Read raw through `load_panel(tier=Tier.RAW, ...)`, coerce types, and write with
`write_curated`. Price parsing strips `$` and `,` and then requires a successful float
conversion — anything else raises `SchemaError` naming the value and the column. Reject any row
whose `as_of` is not an XNYS session. `has_distribution` comes from the `nasdaq_dividends` raw
dataset where it exists (QQQ) and is `False` elsewhere; **never infer it from the bars**.

`__main__.py` exposes `fetch`, `build-raw`, `curate` and `backfill` (all three in order). No
unlock flag, and no `--end` that can exceed `dev_end`.

- [ ] **Step 4: Run the tests to verify they pass, then commit**

```bash
uv run --locked pytest tests/test_curate.py -v
git add src/volab/ingest/curate.py src/volab/ingest/__main__.py tests/test_curate.py
git commit -m "feat: curate raw into a typed panel, flagging distributions"
```

---

## Task 6: Realized measures and HAR components

**Files:**
- Create: `src/volab/features/__init__.py`, `realized.py`, `har.py`
- Test: `tests/test_realized.py`

**Interfaces:**
- Consumes: nothing. These are pure array functions — no dates, no I/O.
- Produces, each taking `returns: npt.NDArray[np.float64]` and returning a same-length
  `NDArray[np.float64]` with `np.nan` wherever the trailing window is incomplete:
  ```python
  def rv_proxy(returns, *, window: int)
  def semivariance_proxy(returns, *, window: int, sign: Literal["neg", "pos"])
  def signed_jump_proxy(returns, *, window: int)
  def bipower_proxy(returns, *, window: int)          # window >= 2
  def har_components(returns) -> dict[str, NDArray]   # keys "har_d", "har_w", "har_m"
  ```

- [ ] **Step 1: Write the failing tests**

Use a short hand-computed series, `r = [0.01, -0.02, 0.03, -0.01, 0.02]`, and assert exact
values with `np.testing.assert_allclose`:

```python
def test_rv_proxy_window_1_is_the_squared_return() -> None: ...
def test_rv_proxy_window_3_sums_the_trailing_three() -> None:
    # at i=2: 0.01**2 + 0.02**2 + 0.03**2
def test_rv_proxy_is_nan_before_the_window_fills() -> None:
    # window=3 -> indices 0 and 1 are nan
def test_semivariance_neg_keeps_only_negative_returns() -> None:
    # at i=2, window=3: 0.02**2 only
def test_semivariance_pos_and_neg_sum_to_rv() -> None: ...
def test_signed_jump_is_pos_minus_neg() -> None: ...
def test_bipower_proxy_matches_the_hand_computation() -> None:
    # window=3 at i=2: (pi/2) * (|0.01*-0.02| + |-0.02*0.03|)
def test_bipower_proxy_rejects_window_below_2() -> None:
    with pytest.raises(ValueError): bipower_proxy(r, window=1)
def test_har_components_are_means_not_sums() -> None:
    # har_w at i>=4 is the mean of the last 5 squared returns
def test_a_nan_return_propagates_to_its_windows() -> None:
    # a nan return makes every window containing it nan, never a partial sum
def test_window_longer_than_the_series_is_all_nan() -> None: ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/test_realized.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `realized.py` and `har.py`**

Trailing windows only. `.rolling(window)` is right-closed and is the intended mechanism;
`center=True`, a negative `shift` and forward indexing are forbidden and Task 9's scan enforces
it. HAR components are **means** (`har_d` = the squared return itself, `har_w` over 5, `har_m`
over 22), which differ from the `rv_proxy` **sums** — that asymmetry is the standard HAR
specification and is deliberate.

- [ ] **Step 4: Run the tests to verify they pass, then commit**

```bash
uv run --locked pytest tests/test_realized.py -v
git add src/volab/features/__init__.py src/volab/features/realized.py src/volab/features/har.py tests/test_realized.py
git commit -m "feat: add trailing realized-measure and HAR proxies"
```

---

## Task 7: Targets

**Files:**
- Create: `src/volab/features/targets.py`
- Test: `tests/test_targets.py`

**Interfaces:**
- Consumes: Task 3's `nth_session_after`, Task 2's `write_features`.
- Produces:
  ```python
  HORIZONS: tuple[int, ...] = (1, 5, 22, 66)
  def forward_rv_proxy(returns, *, horizon: int)   # value at i sums returns i+1..i+horizon
  def build_targets(panel: pd.DataFrame, *, horizon: int, calendar) -> pd.DataFrame
  ```
  `build_targets` returns rows with `ts` = the base session and `as_of` =
  `nth_session_after(calendar, ts, horizon)`. A base session whose horizon is incomplete is
  omitted.

- [ ] **Step 1: Write the failing tests**

```python
def test_forward_rv_sums_the_following_returns() -> None:
    # at i=0, horizon=2: r[1]**2 + r[2]**2
def test_forward_rv_is_nan_at_the_tail() -> None: ...
def test_target_as_of_is_the_nth_session_after_ts() -> None:
    # ts 2023-12-29, horizon 1 -> as_of 2024-01-02, across the holiday
def test_target_as_of_differs_from_ts() -> None:
    # the property that keeps targets out of the feature table
def test_incomplete_horizon_rows_are_omitted() -> None: ...
def test_horizons_are_1_5_22_66() -> None:
    assert HORIZONS == (1, 5, 22, 66)
def test_load_panel_at_dev_end_hides_unrealised_targets(synthetic_store) -> None:
    # write targets whose as_of straddles the cutoff; reading with end=cutoff
    # returns no base date later than `horizon` sessions before it
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/test_targets.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `targets.py`**

`forward_rv_proxy` is the only place a forward offset is legitimate, because a target is
forward-looking by definition. Mark it with a comment saying so and naming Task 9's scan as the
reason it needs an explicit exemption.

- [ ] **Step 4: Run the tests to verify they pass, then commit**

```bash
uv run --locked pytest tests/test_targets.py -v
git add src/volab/features/targets.py tests/test_targets.py
git commit -m "feat: add forward RV targets stamped with their realisation date"
```

---

## Task 8: The feature table

**Files:**
- Create: `src/volab/features/build.py`, `src/volab/features/__main__.py`
- Test: `tests/test_features_build.py`

**Interfaces:**
- Consumes: Tasks 3, 6, 7; `load_panel`; `write_features`.
- Produces:
  ```python
  def build_features(panel: pd.DataFrame, *, through: dt.date, calendar) -> pd.DataFrame
  ```
  Emits one row per session `t <= through`. **The panel may extend past `through`.** Columns are
  exactly spec section 9's table. Only `__main__.py` touches the store.

- [ ] **Step 1: Write the failing tests**

```python
def test_build_features_emits_one_row_per_session_through_the_cutoff() -> None: ...
def test_build_features_columns_match_the_spec() -> None:
    # exact column set, including has_distribution and every *_proxy_* name
def test_proxy_columns_are_named_proxy() -> None:
    assert all("_proxy_" in c for c in frame.columns if "semivar" in c or "bipower" in c)
def test_incomplete_windows_are_null_not_dropped() -> None:
    # the first 65 rows have a null rv_proxy_d66 but are present

# Review Focus 3
def test_a_missing_session_does_not_shorten_a_window() -> None:
    # drop one session's bar from the panel; a 5-session window spanning the gap
    # is null rather than silently computed from 5 surviving rows
def test_iv_columns_are_null_before_vix1d_inception() -> None:
    # iv_h1 is null before 2022-05-13 and present after
def test_build_features_never_calls_load_panel() -> None:
    # build_features is pure; monkeypatch load_panel to raise and assert it still works
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/test_features_build.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `build.py`**

Reindex the panel onto `sessions_between(calendar, first, last)` **before** computing anything,
so a session with no bar becomes an explicit null row and every `.rolling(n)` means `n`
sessions rather than `n` surviving rows — Review Focus 3. Join the exogenous series on `as_of`.
Compute returns, then the Task 6 measures, then the IV columns. Slice to `t <= through` as the
**last** step, so no computation ever depends on where the cutoff fell.

`__main__.py`: `load_panel(..., end=settings.splits.dev_end)` →
`build_features(panel, through=settings.splits.dev_end)` → `write_features`, plus the four
target datasets via Task 7.

- [ ] **Step 4: Run the tests, build the real table, then commit**

```bash
uv run --locked pytest tests/test_features_build.py -v
uv run --locked python -m volab.features build
```

Record the wall-clock time in `phase1-decisions.md`. **Budget: under 5 minutes for all three
symbols.** If missed, STOP and propose spec section 12 lever (b).

```bash
git add src/volab/features/build.py src/volab/features/__main__.py tests/test_features_build.py docs/superpowers/phase1-decisions.md
git commit -m "feat: build the dated feature table from trailing windows only"
```

---

## Task 9: The point-in-time suite, proven by mutation

**Files:**
- Create: `tests/test_point_in_time.py`
- Modify: `docs/superpowers/phase1-decisions.md`

**Interfaces:**
- Consumes: Task 8's `build_features`. Calls it **directly**; never routes a panel through
  `load_panel`, which would strip the rows the tests exist to perturb.

- [ ] **Step 1: Write the six tests**

Every synthetic date is `<= 2025-12-31`. Byte-identity is per column: equal dtype and equal
`frame[col].to_numpy().tobytes()`. No float tolerance anywhere in this file. Each perturbation
asserts it actually changed the panel before recomputing.

```python
def test_perturbing_post_t_leaves_features_byte_identical() -> None:
    # panel to T; build_features(panel, through=T0); mutate every row after T0 --
    # scale closes, flip signs, inject NaN, delete rows, append sessions; rebuild; compare
def test_panel_extent_does_not_change_features() -> None:
    # build_features(panel_to_T0, through=T0) == build_features(panel_to_T, through=T0)
def test_shuffled_future_canary() -> None:
    # permute post-T0 close VALUES across their sessions, leaving dates in place
def test_no_feature_module_looks_forward() -> None:
    # AST scan over src/volab/features/: no center=True, no negative shift/diff,
    # no .shift(-n), no forward slicing. targets.py's forward_rv_proxy is the one
    # explicit exemption, named in an ALLOWED_FORWARD set with its reason.
@pytest.mark.parametrize("source,expected", [...])
def test_the_forward_scan_detects_each_bypass(source: str, expected: str) -> None:
    # red-before-fix cases, in the style of tests/test_import_discipline.py
def test_targets_carry_as_of_h_sessions_after_base() -> None: ...
def test_load_panel_at_dev_end_hides_unrealised_targets() -> None: ...
```

- [ ] **Step 2: Run the suite to verify it passes**

Run: `uv run --locked pytest tests/test_point_in_time.py -v`
Expected: PASS

- [ ] **Step 3: Mutation 1 — make a feature peek forward**

Change `semivar_neg_proxy_d5`'s window to `.shift(-1)`. Run
`uv run --locked pytest tests/test_point_in_time.py -v` and **capture the failure output
verbatim**. Confirm **both** test 1 and test 4 fail. If only test 4 fails, test 1 is too weak —
strengthen it and repeat. Revert; confirm green.

- [ ] **Step 4: Mutation 2 — make a feature depend on the panel's extent**

Replace a trailing `.rolling(22).mean()` with a full-sample `.mean()`. Capture the output.
Test 1 will **not** catch this — no individual row changed — and test 2 **must**. If test 2
misses it, the test is wrong, not the mutation. Revert; confirm green.

- [ ] **Step 5: Record the evidence**

Write both captured outputs verbatim into `docs/superpowers/phase1-decisions.md` under
"Mutation evidence", with the exact diff applied for each.

- [ ] **Step 6: Run the full gate set and commit**

```bash
uv run --locked ruff check . && uv run --locked ruff format --check . \
  && uv run --locked mypy src && uv run --locked pytest -q
git add tests/test_point_in_time.py docs/superpowers/phase1-decisions.md
git commit -m "test: prove the point-in-time guard fires, by mutation"
```

---

## Task 10: Decisions log and PR

**Files:**
- Modify: `docs/superpowers/phase1-decisions.md`

- [ ] **Step 1: Complete the decisions log**

Phase 0's `phase0-decisions.md` is the template: every decision taken during execution that the
spec and plan did not settle, each with its reasoning and its cost if wrong. Carry forward any
deferred findings.

- [ ] **Step 2: Confirm the gates are green and push**

```bash
uv run --locked ruff check . && uv run --locked ruff format --check . \
  && uv run --locked mypy src && uv run --locked pytest -q
git push -u origin phase-1-data-and-targets
```

- [ ] **Step 3: Open the PR against main**

The description states: what was ingested and the verified spans; that **no free intraday source
exists** and which were rejected with the reason; why `dev_end` moved and the session arithmetic
behind it; what the point-in-time tests prove and **both mutation outputs**; the
`has_distribution` split (QQQ only) and the SPY/IWM bias that remains; and the measured file
count and timings against the section 12 budget.
