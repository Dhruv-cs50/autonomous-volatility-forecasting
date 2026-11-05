"""The forecast log: one append-only line per day per configuration, each carrying a fingerprint.

``data/forecasts.jsonl`` is the holdout-reveal design's protection 3 -- the daily run's own record
of what it forecast -- and the fingerprint on every line is that design's protection 4: the
champion's configuration, the git sha, the dirty flag, and a content hash of the champion's
definition. The hash is there *as well as* the sha so that a dirty tree is still identifiable: a
sha alone names a commit whose contents are not what ran.

**No count from this file is a reported number.** It is gitignored (``data/**`` already is, and no
exception was added for it), and it has to be: until the reveal, the champion on these lines is a
*pseudo*-champion -- the current dev winner, labelled ``kind="pseudo"`` rather than the registered
one (R158 Q2) -- and a tracked file would put unregistered numbers into git. The reported counts
remain the trial and comparison ledgers, and **this module writes nothing else**: not those two,
and not the evaluators' decision sink. Two tests pin that: no code string names one of the
three files, and a write creates no file but this one.

**One line per day per configuration is enforced here, not hoped for.** A configuration is
``(as_of, symbol, horizon)``. ``append_forecasts`` refuses a batch that repeats a key, or that
repeats one already in the file, and it refuses before writing anything -- a half-written batch
would be a day with some of its configurations recorded and no way to tell. It returns how many
lines it wrote, so a caller can assert that N configurations produced exactly N lines. The
reveal design's "attempts are counted, not forbidden" governs the *run* record; a forecast is a
claim about a day, and two claims for one day would leave a reader unable to say which stood.

**The timestamp, the sha and the dirty flag are the caller's.** ``at`` is injected because
nothing under ``volab.daily`` may reach a clock but ``_today`` (``tests/test_daily.py`` derives
that), and for the reason ``append_trials`` states: read here, the sha would describe the tree as
it is when the writing happens rather than the tree that produced the numbers (R70). The caller
takes the fingerprint once, at the start of its run, with ``take_fingerprint``.

**The region of the forecast date is not a field.** It is a pure function of ``as_of`` and the
configured ``Splits`` (``Splits.region_of``), and a copy stored on the line could only go stale
if ``holdout_end`` later moves, while adding a settings dependency to a writer that needs none.

There is deliberately no function here that chooses a champion. That is P4-06b's, and until it
lands ``main`` refuses.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from volab.errors import VolabError
from volab.store import git_state

FORECAST_LOG = Path("data") / "forecasts.jsonl"

# ``src/volab/daily/forecast.py`` -> the repository root, as every CLI in this package spells it.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# ``pseudo`` is the pre-reveal dev winner; ``registered`` is the frozen champion after the reveal.
# Required on every fingerprint with no default, so a line cannot be mistaken for the registered
# kind by omission.
_KINDS = ("pseudo", "registered")

# (as_of, symbol, horizon). The horizon is read back from JSON, so it is typed as what it is.
_Key = tuple[str, str, object]


class ForecastLogError(VolabError):
    """Raised when an append would put a second line for one day and configuration in the log."""


@dataclass(frozen=True)
class Fingerprint:
    """What identifies the champion that produced a forecast. Carried on every line."""

    # The champion's configuration name, e.g. the model it is.
    model: str
    # ``pseudo`` or ``registered``; see ``_KINDS``.
    kind: str
    # ``("unknown", None)`` is recorded as such when git cannot be reached, never dropped.
    git_sha: str
    git_dirty: bool | None
    # ``definition_hash``'s digest: identifies the code and configuration even on a dirty tree.
    definition_hash: str

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"champion kind must be one of {_KINDS}, got {self.kind!r}")
        if not self.definition_hash:
            raise ValueError("a fingerprint needs a non-empty definition_hash")

    def record(self) -> dict[str, object]:
        return {
            "model": self.model,
            "kind": self.kind,
            "git_sha": self.git_sha,
            "git_dirty": self.git_dirty,
            "definition_hash": self.definition_hash,
        }


@dataclass(frozen=True)
class ForecastLine:
    """One forecast for one configuration on one day. ``value`` is on the variance scale."""

    as_of: dt.date
    symbol: str
    horizon: int
    value: float
    # Rows the fit consumed, reported so a thin fit is visible rather than inferred.
    observations: int
    champion: Fingerprint

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError(
                f"a forecast must be finite, got {self.value}: a line that cannot be read back "
                "as a number must not look like a forecast"
            )

    def key(self) -> _Key:
        return (self.as_of.isoformat(), self.symbol, self.horizon)

    def record(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "symbol": self.symbol,
            "horizon": self.horizon,
            "value": self.value,
            "observations": self.observations,
            "champion": self.champion.record(),
        }


def forecast_log_path(root: Path) -> Path:
    """Where the log lives under ``root``, which is the repository root.

    Takes a root rather than reading the configuration, so a rehearsal writes elsewhere by being
    handed a different root and not by a mode flag (the reveal design's call #6).
    """
    return root / FORECAST_LOG


def definition_hash(
    configuration: Mapping[str, object], sources: Sequence[Path], *, root: Path
) -> str:
    """SHA-256 of the champion's configuration and the bytes of its source files.

    The digest covers canonical JSON (sorted keys, fixed separators) of ``configuration`` and, for
    each source, its path **relative to ``root``** and the SHA-256 of its bytes, sorted by that
    path. Hashing each file and then the pairs, rather than concatenating contents, means a byte
    moved from one file to the next changes the digest. Because it reads the files as they are on
    disk, an edited-but-uncommitted champion hashes differently from the committed one, which is
    the whole reason it exists beside the sha. A source missing from disk raises
    ``FileNotFoundError``, and one outside ``root`` raises ``ValueError``: hashing the absence of a
    file would fingerprint a champion that is not there. A configuration that is not JSON raises
    ``TypeError``.

    Which files make up a champion is the caller's to say; this does not guess.
    """
    resolved_root = root.resolve()
    digests: list[list[str]] = []
    for source in sources:
        resolved = source.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise ValueError(f"{source} is outside the root {root}")
        digests.append(
            [
                resolved.relative_to(resolved_root).as_posix(),
                hashlib.sha256(resolved.read_bytes()).hexdigest(),
            ]
        )
    payload = json.dumps(
        {"configuration": configuration, "sources": sorted(digests)},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def take_fingerprint(
    *,
    model: str,
    kind: str,
    configuration: Mapping[str, object],
    sources: Sequence[Path],
    repo_root: Path,
) -> Fingerprint:
    """Fingerprint the champion now: ``git_state`` of the **repository root**, plus the hash.

    The root is the scope, not the package: ``volab.walkforward.git_state`` documents that with
    the package as scope an edit to ``config/base.yaml`` or ``tests/`` reads as clean. Call this
    once at the start of a run and hand the result to every line, so the fingerprint describes
    the tree that produced the numbers (R70).
    """
    sha, dirty = git_state(repo_root)
    return Fingerprint(
        model=model,
        kind=kind,
        git_sha=sha,
        git_dirty=dirty,
        definition_hash=definition_hash(configuration, sources, root=repo_root),
    )


def _existing_keys(path: Path) -> set[_Key]:
    """The ``(as_of, symbol, horizon)`` of every readable line already in the log.

    A torn final line is skipped rather than raised on, as ``volab.daily.runlog`` skips one: a
    job killed mid-write must not leave a log nothing can append to. The bytes stay in the file.
    """
    if not path.exists():
        return set()
    keys: set[_Key] = set()
    for text in path.read_text(encoding="utf-8").splitlines():
        try:
            parsed: Any = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            keys.add((str(parsed.get("as_of")), str(parsed.get("symbol")), parsed.get("horizon")))
    return keys


def append_forecasts(lines: Sequence[ForecastLine], *, root: Path, at: dt.datetime) -> int:
    """Append one line per forecast and return how many were written. Never rewrites.

    All-or-nothing: duplicates, within the batch or against the file, are refused before a byte is
    written. The batch goes out in one write, flushed and fsynced -- the log is a once-a-day
    record whose claim is that it predates the reader, and a line still in a buffer is the
    cheapest way for that to be false.
    """
    path = forecast_log_path(root)
    seen = _existing_keys(path)
    batch: set[_Key] = set()
    for line in lines:
        key = line.key()
        if key in batch:
            raise ForecastLogError(f"{key} appears twice in this batch")
        if key in seen:
            raise ForecastLogError(f"{key} is already in the log at {path}")
        batch.add(key)
    if not lines:
        return 0
    text = "".join(
        json.dumps({"at": at.isoformat(), **line.record()}, allow_nan=False) + "\n"
        for line in lines
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    return len(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Refuse, until P4-06b supplies a champion. Writes nothing and exits 1.

    The exit code is 1, the refusal code ``volab.daily`` uses elsewhere: a stage that exits 0
    having forecast nothing would be recorded as a completed day. ``--root`` is accepted so that
    the refusal can be exercised against a directory that is not the repository's.
    """
    parser = argparse.ArgumentParser(prog="python -m volab.daily.forecast", description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=_REPO_ROOT,
        help="the directory holding data/forecasts.jsonl (default: the repository's own)",
    )
    parser.parse_args(argv)
    print(
        "no champion: none is defined until P4-06b lands, so there is nothing to forecast from. "
        "Refusing rather than inventing one or writing a line with an empty fingerprint."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
