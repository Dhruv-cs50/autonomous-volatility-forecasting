"""The only sanctioned path in or out of the Parquet store, and the guard that seals the holdout.

Statistical corrections do not catch leakage: a strategy built on a future-looking feature can
pass every deflated-Sharpe and PBO check and still be worthless. Only the data layer can refuse,
so this module refuses. ``load_panel`` is the sole way volab reads data, and this module is the
sole place permitted to touch Parquet at all -- in either direction.

The ``write_*`` functions at the bottom are the writing half. They exist mainly to make step 9
below unreachable: every row they accept must carry an ``as_of`` equal to the partition it is
being written into, so a file that contradicts its own ``date=`` partition cannot be produced by
this package. They do not consult the holdout boundary, and must not. Ingestion has to capture
post-``dev_end`` rows or Phase 6 could never score them; the seal is on reads.

The checks run in a fixed order, because which error surfaces first is part of the contract:

0. a passed ``Settings`` whose ``dev_end`` is later than the repository's, 1. unknown symbols,
2. a reversed window, 3. the holdout boundary -- before any filesystem access, 4. no matching
files, 5. the read, 6. absent columns, 7. a naive ``ts``, 8. a null ``as_of``, 9. partition
consistency, 10. the point-in-time filter, the irreducible-duplicate check and deduplication,
11. an empty result, 12. the post-filter ``as_of`` assertion, 13. the audit line.

Step 0 exists because every rule below it reads ``dev_end`` from the settings it was handed. A
caller who hands over a later boundary is not unlocking the holdout, it is moving it, and the
two keys are never asked for -- so nothing downstream would ever see it.

Steps 4 to 12 run inside the audit's scope, so a holdout read that is refused leaves a line
behind too, with its ``outcome`` naming the refusal. Recording only successes would leave the
sealed window open to unlimited unrecorded probing.

Step 9 deliberately precedes step 10. A file parked under the wrong ``date=`` partition would
otherwise be discarded by the filter and reported as missing data -- a plausible-looking "no
data" answer derived from corrupt storage. Checking partition consistency first turns a silent
discard into a loud failure.

Step 12 is unreachable while step 9 and the filter are both correct. It costs one comparison and
is the last thing standing if either is ever broken.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import glob
import inspect
import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import volab
from volab.config import HOLDOUT_UNLOCK_ENV, Settings, load_settings
from volab.errors import (
    DatasetNotFound,
    HoldoutViolation,
    RawOverwrite,
    SchemaError,
    UnknownSymbol,
)
from volab.paths import (
    MANDATORY_COLUMNS,
    Tier,
    curated_path,
    dataset_glob,
    features_path,
    raw_path,
)

AUDIT_FILENAME: str = "holdout_access.jsonl"

# Re-bound under the private name this module has always used; ``config`` owns the string,
# because its own refusal has to point at the same unlock.
_UNLOCK_ENV = HOLDOUT_UNLOCK_ENV
# Supplied by DuckDB's Hive partitioning, not by the file: step 9 compares the two.
_PARTITION_COLUMN = "date"
_REQUIRED_COLUMNS: tuple[str, ...] = (*MANDATORY_COLUMNS, _PARTITION_COLUMN)
# The finest key the store can tell rows apart by. Two rows tying on all of it are
# indistinguishable, so neither can be called the current one.
_FULL_KEY: tuple[str, ...] = ("symbol", "ts", "as_of", "ingested_at")
_PACKAGE_DIR = Path(volab.__file__).resolve().parent
_GIT_TIMEOUT_SECONDS = 5.0


def _unlock_granted(allow_holdout: bool) -> bool:
    """Both keys, or nothing: an explicit argument *and* an explicit environment variable.

    Deliberately a plain module-level function, called by name rather than bound or aliased
    anywhere, so that the test suite's policing fixture can replace it and be sure every
    unlock attempt goes through its replacement.
    """
    return allow_holdout and os.environ.get(_UNLOCK_ENV) == "1"


def _matching_files(patterns: list[str]) -> list[str]:
    """Expand the scan patterns into the files to read.

    DuckDB raises on a pattern matching nothing; we'd rather say ``DatasetNotFound``, so the
    existence question is asked first and separately. The expansion is then what gets scanned,
    which also keeps one scope spanning several per-symbol patterns from aborting on the symbols
    that happen to have no files.

    A set, because overlapping patterns must not read a file twice. ``symbols=["SPY", "SPY"]``
    yields two identical patterns from ``dataset_glob``, DuckDB does not collapse a repeated
    path, and every row would then tie on the whole key -- accusing the store of a corruption
    that is really a slip in the caller's list. De-duplicated here rather than at the symbol
    check, so no other route to overlapping patterns can get around it.
    """
    return sorted({path for pattern in patterns for path in glob.glob(pattern)})


def _scan(patterns: list[str]) -> pd.DataFrame:
    """Read every row under ``patterns``, with the Hive ``date`` partition value attached."""
    relation = duckdb.sql(
        "SELECT * FROM read_parquet($patterns, hive_partitioning := true)",
        params={"patterns": patterns},
    )
    return relation.df()


def _as_dates(column: pd.Series[Any]) -> pd.Series[Any]:
    """Normalise a date-ish column to ``datetime.date`` objects.

    ``as_of`` arrives from Parquet as ``date32`` while the Hive ``date`` value arrives from
    DuckDB's own partition-type inference. Both are converted here, in one place, so that a
    dtype mismatch can never make the step 9 comparison vacuously true.
    """
    return pd.to_datetime(column).dt.date


def _caller_frame() -> tuple[str, int]:
    """The first frame outside the volab package: who asked for the holdout, and where."""
    for frame in inspect.stack():
        if not Path(frame.filename).resolve().is_relative_to(_PACKAGE_DIR):
            module = str(frame.frame.f_globals.get("__name__", "unknown"))
            return module, frame.lineno
    return "unknown", 0


def _git_state() -> tuple[str, bool | None]:
    """The commit the read happened at and whether the tree was dirty.

    Falls back to ``("unknown", None)``: an audit line that cannot name the commit still beats
    no audit line.
    """

    def run(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                # ``--no-optional-locks`` is load-bearing, not tidiness. ``git status`` otherwise
                # takes ``index.lock`` to refresh the index, and this runs on every audited read
                # -- so a suite run would contend with a developer's own commit. Worse, the
                # timeout below kills git mid-refresh and leaves a zero-byte ``index.lock``
                # behind, which blocks every later commit until someone removes it by hand.
                # Measured in this repository: ``git status --porcelain`` takes 3.06s against a
                # 5.0s timeout, so the margin is 1.6x on 24,218 raw partitions over an
                # iCloud-synced directory. That lock cost this project roughly an hour before the
                # flag was traced as the cause. The flag makes the call read-only.
                ["git", "--no-optional-locks", *args],
                cwd=_PACKAGE_DIR,
                capture_output=True,
                text=True,
                check=False,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return None if completed.returncode != 0 else completed.stdout.strip()

    sha = run("rev-parse", "HEAD")
    if sha is None:
        return "unknown", None
    # The pathspec is a cost fix, not a correctness one. The untracked walk is the whole
    # expense -- ``!data/**/`` in .gitignore forces git across 80,742 files -- and the derivable
    # tiers cannot tell us anything about reproducibility that the commit does not. Measured
    # here: 2.70s unscoped against the 5.0s timeout, 0.018s scoped, and both still report a
    # tracked modification and an untracked file. ``-uno`` would be wrong: it drops the
    # untracked signal entirely.
    status = run(
        "status",
        "--porcelain",
        "--",
        ".",
        ":(exclude)data/raw",
        ":(exclude)data/curated",
        ":(exclude)data/features",
    )
    return sha, None if status is None else bool(status)


def _append_audit(
    settings: Settings,
    *,
    dataset: str,
    tier: Tier,
    end: dt.date,
    start: dt.date | None,
    symbols: Sequence[str] | None,
    outcome: str,
) -> None:
    """Append one JSON object describing an unlocked read. Append-only, one line per attempt.

    ``outcome`` is ``"success"`` or the class name of the exception that refused the read. A
    refusal is still an attempt on the sealed window: recording only successes would let a
    key-holder probe the existence, emptiness and schema of post-``dev_end`` data without end
    while the log's line count stayed clean.
    """
    caller_module, caller_lineno = _caller_frame()
    git_sha, git_dirty = _git_state()
    record: dict[str, Any] = {
        "ts": dt.datetime.now(dt.UTC).isoformat(),
        "outcome": outcome,
        "dataset": dataset,
        "tier": tier.value,
        "end": end.isoformat(),
        "start": None if start is None else start.isoformat(),
        "symbols": None if symbols is None else list(symbols),
        "caller_module": caller_module,
        "caller_lineno": caller_lineno,
        "git_sha": git_sha,
        "git_dirty": git_dirty,
    }
    path = settings.storage_root / AUDIT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def load_panel(
    dataset: str,
    *,
    end: dt.date,
    symbols: Sequence[str] | None = None,
    start: dt.date | None = None,
    columns: Sequence[str] | None = None,
    tier: Tier = Tier.CURATED,
    allow_holdout: bool = False,
    settings: Settings | None = None,
) -> pd.DataFrame:
    """Read a point-in-time panel, refusing anything that could leak the future.

    ``end`` is required and keyword-only: there is no "all data" sentinel, because the one
    request this layer exists to interrogate is "how far forward may I see?". Filtering keys on
    ``as_of``, never on ``ts``. The window is never silently clipped and an unexpectedly empty
    result is raised rather than returned.

    Reading past ``settings.splits.dev_end`` needs both ``allow_holdout=True`` and
    ``VOLAB_HOLDOUT_UNLOCK=1``, and leaves an audit line behind.

    A ``settings`` argument may be more conservative than the repository's configuration but
    never less: a ``dev_end`` later than the configured one is refused outright.
    """
    # 0. The guard's premise is that ``dev_end`` is the repository's own boundary. A hand-built
    #    Settings that moves it forward would walk straight past the seal without asking for a
    #    key, so ``_unlock_granted`` would never be called and nothing -- not this module, not
    #    the suite's policing fixture -- would intercept it. Checked before every other rule,
    #    so no path can reach the boundary test with a boundary the caller chose. An earlier
    #    ``dev_end`` is more conservative, and allowed.
    if settings is None:
        resolved = load_settings()
    else:
        resolved = settings
        configured = load_settings().splits.dev_end
        if resolved.splits.dev_end > configured:
            raise HoldoutViolation(
                f"the settings passed to load_panel carry splits.dev_end "
                f"{resolved.splits.dev_end}, later than the repository's configured dev_end "
                f"{configured}: moving the holdout boundary is not a way to read past it. "
                f"Unlock the window with allow_holdout=True and {_UNLOCK_ENV}=1 instead."
            )

    # 1. Symbols must name the configured universe or the exogenous series. A bare string would
    #    otherwise be iterated into single characters here instead of reaching dataset_glob's own
    #    refusal.
    if isinstance(symbols, str):
        raise ValueError(f"symbols must be a sequence of names, not a bare string: {symbols!r}")
    if symbols is not None:
        unknown = sorted(set(symbols) - set(resolved.universe) - set(resolved.exogenous))
        if unknown:
            raise UnknownSymbol(
                f"symbol(s) {unknown} are in neither the configured universe "
                f"{list(resolved.universe)} nor the exogenous series {list(resolved.exogenous)}"
            )

    # The same shape rules for columns: a bare string would be iterated into single characters
    # and an empty selection would return a frame with no columns. ``len`` rather than
    # truthiness, so a numpy array or ``pd.Index`` is judged by size, not by an ambiguous bool.
    if isinstance(columns, str):
        raise ValueError(f"columns must be a sequence of names, not a bare string: {columns!r}")
    if columns is not None and len(columns) == 0:
        raise ValueError("columns must not be empty; pass None to return every column")

    # 2. A reversed window is the caller's own mistake, not a data fault: a plain ValueError.
    if start is not None and end < start:
        raise ValueError(f"end {end} precedes start {start}")

    # 3. The holdout boundary, before any filesystem access whatsoever.
    past_dev_end = end > resolved.splits.dev_end
    if past_dev_end and not _unlock_granted(allow_holdout):
        raise HoldoutViolation(
            f"end {end} is past dev_end {resolved.splits.dev_end}; reading the holdout window "
            f"needs both allow_holdout=True and {_UNLOCK_ENV}=1"
        )

    # Steps 4 to 12 run inside the audit's scope: once the two keys have opened the sealed
    # window, the attempt is recorded whether it succeeds or fails. The exception is re-raised
    # untouched; nothing here swallows or rewrites a refusal.
    try:
        # 4. Nothing to read is a missing dataset, not an empty answer. ``symbols=None`` means
        #    the configured universe, not every directory on disk: a typo'd or decommissioned
        #    ticker directory must not silently join the panel. Finding files for only some of
        #    the universe is not an error; finding them for none of it is.
        scope_symbols = resolved.universe if symbols is None else symbols
        patterns = dataset_glob(resolved.storage_root, tier, dataset, scope_symbols)
        matched = _matching_files(patterns)
        if not matched:
            scope = (
                f"universe {list(resolved.universe)}"
                if symbols is None
                else f"symbol(s) {list(symbols)}"
            )
            raise DatasetNotFound(
                f"no files match {tier.value}/{dataset} for {scope} under {resolved.storage_root}"
            )

        # 5. The read. No projection or partition pushdown, deliberately: steps 6 to 12 need
        #    ``ts``, ``as_of``, ``ingested_at`` and ``date`` whatever the caller asked for, so
        #    honouring a narrow projection here would quietly disable the checks the caller
        #    relies on, and step 9 must see rows the window excludes, so partitions are not
        #    pruned on ``end`` either. ``columns`` is applied at the very end instead.
        #    Correctness never depends on pruning.
        #    The matched files are handed over rather than the patterns: one scope now spans
        #    several per-symbol patterns, and DuckDB raises on any single pattern that matches
        #    nothing, so a universe symbol with no files would abort a scan the union answered.
        frame = _scan(matched)

        # 6. Every mandatory column, the partition value, and whatever the caller asked for.
        missing = [name for name in _REQUIRED_COLUMNS if name not in frame.columns]
        if missing:
            raise SchemaError(f"{tier.value}/{dataset} is missing mandatory column(s) {missing}")
        if columns is not None:
            absent = [name for name in columns if name not in frame.columns]
            if absent:
                raise SchemaError(f"{tier.value}/{dataset} has no requested column(s) {absent}")

        # 7. A naive ts cannot be placed on a timeline, so it cannot be trusted on one.
        if not isinstance(frame["ts"].dtype, pd.DatetimeTZDtype):
            raise SchemaError(
                f"{tier.value}/{dataset} column 'ts' must be timezone-aware, "
                f"got dtype {frame['ts'].dtype}"
            )

        # 8. as_of is what the guard asserts on; a null one cannot be asserted on at all.
        null_as_of = int(frame["as_of"].isna().sum())
        if null_as_of:
            raise HoldoutViolation(
                f"{tier.value}/{dataset} has {null_as_of} row(s) with a null as_of; "
                "point-in-time filtering cannot be verified"
            )

        # 9. Assertion e1, BEFORE the filter: a row whose as_of contradicts its own partition
        #    means the store is corrupt. Filtering first would discard the evidence and report
        #    no data.
        as_of = _as_dates(frame["as_of"])
        partition = _as_dates(frame[_PARTITION_COLUMN])
        mismatched = as_of != partition
        if bool(mismatched.any()):
            first = frame.loc[mismatched, ["as_of", _PARTITION_COLUMN]].iloc[0]
            raise HoldoutViolation(
                f"{int(mismatched.sum())} row(s) in {tier.value}/{dataset} carry an as_of that "
                f"contradicts their date= partition, first as_of {first['as_of']} under "
                f"date={first[_PARTITION_COLUMN]}"
            )

        # 10. Point-in-time filter on as_of, then one row per (symbol, ts): the latest vintage
        #     at or before ``end`` wins. ``as_of`` leads the sort because it, not
        #     ``ingested_at``, is the point-in-time axis -- two rows sharing (symbol, ts) with
        #     different as_of are a designed-for case, one file per vintage in its own date=
        #     partition, and a revision backfilled after a later vintage was ingested must not
        #     supersede it. ``ingested_at`` breaks ties within one vintage, and nothing else.
        window = as_of <= end
        if start is not None:
            window &= as_of >= start
        frame = frame[window]

        # Rows tying on the whole key are indistinguishable, so no rule can name the current
        # one. Dropping one silently would be truncation for a dataset whose true grain is
        # finer than (symbol, ts) -- a silently wrong number, which this layer refuses to
        # produce. The store is declared corrupt instead.
        tied = frame.duplicated(list(_FULL_KEY), keep=False)
        if bool(tied.any()):
            first = frame.loc[tied, list(_FULL_KEY)].iloc[0]
            raise SchemaError(
                f"{int(tied.sum())} row(s) in {tier.value}/{dataset} tie on all of "
                f"{list(_FULL_KEY)}, first symbol {first['symbol']!r} ts {first['ts']} "
                f"as_of {first['as_of']} ingested_at {first['ingested_at']}: the store cannot "
                "say which of them is current"
            )

        frame = frame.sort_values(["as_of", "ingested_at"], kind="stable").drop_duplicates(
            ["symbol", "ts"], keep="last"
        )
        frame = frame.sort_values(["symbol", "ts"]).reset_index(drop=True)

        # 11. An empty frame is never returned where the caller expected rows.
        if frame.empty:
            window_text = f"up to {end}" if start is None else f"{start} to {end}"
            raise DatasetNotFound(f"no rows in {tier.value}/{dataset} have an as_of {window_text}")

        # 12. Assertion e2: what is about to be returned cannot see past end.
        maximum = _as_dates(frame["as_of"]).max()
        if maximum > end:
            raise HoldoutViolation(
                f"post-filter assertion failed: max(as_of) {maximum} exceeds end {end}"
            )
    except Exception as exc:
        # An unwritable audit log must not displace the refusal it was recording: the caller
        # has to see why the read was refused. On the success path below there is no exception
        # to protect, so a failure to record surfaces there instead of being swallowed.
        if past_dev_end:
            with contextlib.suppress(Exception):
                _append_audit(
                    resolved,
                    dataset=dataset,
                    tier=tier,
                    end=end,
                    start=start,
                    symbols=symbols,
                    outcome=type(exc).__name__,
                )
        raise

    # 13. An unlocked read is recorded before the rows leave this function.
    if past_dev_end:
        _append_audit(
            resolved,
            dataset=dataset,
            tier=tier,
            end=end,
            start=start,
            symbols=symbols,
            outcome="success",
        )

    # Timestamps come back in UTC whatever the host's timezone is. DuckDB renders a TIMESTAMP
    # WITH TIME ZONE in its session timezone, which defaults to the system's, so the same read on
    # a laptop in America/Los_Angeles and on CI in UTC would otherwise return identical instants
    # under different dtypes -- ``datetime64[us, America/Los_Angeles]`` against
    # ``datetime64[us, UTC]``. The instants were always right; the dtype was not reproducible,
    # and for a project whose claim rests on reproducibility that is the same kind of defect. A
    # naive column is left alone rather than localised, because assuming a naive timestamp is UTC
    # is exactly the silent repair this layer refuses to make.
    aware = {
        name: frame[name].dt.tz_convert("UTC")
        for name in ("ts", "ingested_at")
        if isinstance(frame[name].dtype, pd.DatetimeTZDtype)
    }
    if aware:
        frame = frame.assign(**aware)

    # Last operation before returning, so every check above saw the full schema. The Hive
    # ``date`` column is a partition artefact duplicating ``as_of``; it is not part of the contract.
    if columns is None:
        return frame.drop(columns=[_PARTITION_COLUMN])
    return frame[list(columns)]


def _pinned_schema(frame: pd.DataFrame) -> pa.Schema:
    """The frame's own Arrow schema with the mandatory columns' types pinned.

    ``as_of`` must land as ``date32`` and the two timestamps as microsecond timestamps, because
    that is what ``tests/conftest.py`` writes and what ``_as_dates`` expects to read back. Left
    to inference, a column of ``datetime.date`` objects arrives as a string or an object column
    and step 9's comparison would be reading a different type than it was written as. Payload
    columns keep whatever Arrow infers.
    """
    inferred = pa.Schema.from_pandas(frame, preserve_index=False)
    zone = frame["ts"].dt.tz
    pinned = {
        "as_of": pa.date32(),
        "ts": pa.timestamp("us", tz=str(zone)),
        "ingested_at": pa.timestamp("us", tz="UTC"),
    }
    return pa.schema(
        [pa.field(field.name, pinned.get(field.name, field.type)) for field in inferred]
    )


def _write(frame: pd.DataFrame, path: Path, *, as_of: dt.date, allow_overwrite: bool) -> Path:
    """Validate ``frame`` against ``as_of`` and write it to ``path``, atomically.

    The checks run in a fixed order, for the same reason ``load_panel``'s do: which error a
    caller sees is part of the contract. Emptiness is judged before any dtype, because an empty
    frame's columns carry no usable dtype -- pandas gives ``float64`` to an empty list -- so a
    dtype complaint about a frame with no rows would name the wrong fault.
    """
    # ``datetime`` is a subclass of ``date``, so mypy accepts one here and the path builders
    # would silently emit ``date=2023-06-15T12:00:00`` -- a partition ``parse_path`` rejects and
    # Hive partitioning misreads. Phase 0 deferred this as a latent builder defect; it was
    # unreachable while nothing under ``src/`` wrote files, and this function is what makes it
    # reachable, so it is refused here rather than left to produce a corrupt partition.
    if isinstance(as_of, dt.datetime):
        raise SchemaError(f"as_of must be a plain date, not a datetime: {as_of!r}")

    missing = [name for name in MANDATORY_COLUMNS if name not in frame.columns]
    if missing:
        raise SchemaError(f"cannot write {path}: missing mandatory column(s) {missing}")

    if frame.empty:
        raise SchemaError(f"cannot write {path}: the frame has no rows")

    if not isinstance(frame["ts"].dtype, pd.DatetimeTZDtype):
        raise SchemaError(
            f"cannot write {path}: column 'ts' must be timezone-aware, "
            f"got dtype {frame['ts'].dtype}"
        )

    null_as_of = int(frame["as_of"].isna().sum())
    if null_as_of:
        raise SchemaError(f"cannot write {path}: {null_as_of} row(s) have a null as_of")

    # The whole reason this function exists. A row whose as_of disagrees with the partition it is
    # being written into is what load_panel step 9 raises on at read time; refusing it here means
    # that step can never fire on data this package wrote.
    disagreeing = _as_dates(frame["as_of"]) != as_of
    if bool(disagreeing.any()):
        first = frame.loc[disagreeing, "as_of"].iloc[0]
        raise SchemaError(
            f"cannot write {path}: {int(disagreeing.sum())} row(s) carry an as_of that "
            f"contradicts the partition being written, first {first} under date={as_of}"
        )

    # Checked after validation, deliberately: a schema fault is a code defect, while a collision
    # is often expected operational state -- a resumed backfill meeting work it already did. The
    # defect is the more useful thing to report first, and nothing has reached disk either way,
    # because the write below goes to a temp file and is renamed into place.
    if path.exists() and not allow_overwrite:
        raise RawOverwrite(
            f"{path} already exists and raw files are immutable; a second ingest of the same "
            "source, symbol and date within one second collides on the filename stamp"
        )

    table = pa.Table.from_pandas(frame, schema=_pinned_schema(frame), preserve_index=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the destination and moved into place, so a crash or a full disk leaves the
    # partition either absent or complete -- never a truncated Parquet file for a later read to
    # trip over. os.replace is atomic within a filesystem, and the temp file shares the directory
    # precisely to guarantee that.
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".part-", suffix=".parquet.tmp")
    os.close(descriptor)
    temporary = Path(name)
    try:
        pq.write_table(table, temporary)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def write_raw(
    frame: pd.DataFrame,
    *,
    source: str,
    symbol: str,
    as_of: dt.date,
    ingested_at: dt.datetime,
    settings: Settings | None = None,
) -> Path:
    """Write one immutable raw partition and return its path.

    Refuses to replace an existing file: raw is the archive of what a vendor served, and for a
    trailing-window source it is the only copy of sessions that can no longer be refetched.
    """
    resolved = load_settings() if settings is None else settings
    path = raw_path(resolved.storage_root, source, symbol, as_of, ingested_at)
    return _write(frame, path, as_of=as_of, allow_overwrite=False)


def write_curated(
    frame: pd.DataFrame,
    *,
    dataset: str,
    symbol: str,
    as_of: dt.date,
    settings: Settings | None = None,
) -> Path:
    """Write one curated partition and return its path, replacing any existing file.

    Curated data is a deterministic derivation of raw, so overwriting is what makes a rebuild
    idempotent rather than a conflict.
    """
    resolved = load_settings() if settings is None else settings
    path = curated_path(resolved.storage_root, dataset, symbol, as_of)
    return _write(frame, path, as_of=as_of, allow_overwrite=True)


def write_features(
    frame: pd.DataFrame,
    *,
    feature_set: str,
    symbol: str,
    as_of: dt.date,
    settings: Settings | None = None,
) -> Path:
    """Write one feature partition and return its path, replacing any existing file."""
    resolved = load_settings() if settings is None else settings
    path = features_path(resolved.storage_root, feature_set, symbol, as_of)
    return _write(frame, path, as_of=as_of, allow_overwrite=True)
