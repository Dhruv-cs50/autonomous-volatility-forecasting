"""A read-only view of the trial ledgers for the agent, with post-``dev_end`` lines removed (R183).

Phase 6 will write holdout scores into ``data/trials.jsonl``. Every line there carries
``last_origin``, so this reader drops any line whose ``last_origin`` is later than ``dev_end`` and
**reports how many it dropped** in :attr:`TrialView.excluded`: a silent drop would understate the
trial count. ``comparisons.jsonl`` records carry no origin dates, so no comparison file is read
here at all.

**A line with no ``last_origin`` is excluded and counted.** Without the key a line cannot be shown
to lie inside the development window, and the cost of keeping a line that is not is a leak while
the cost of excluding one that is is a visible count. A ``last_origin`` that is present but is not
an ISO date, a line that is not JSON, and a line that is not a JSON object raise
:class:`LedgerReadError` naming the line: skipping one would shrink the view without saying so.
Blank lines are ignored. An absent file is an empty view and is not created.

``AGENT_TRIAL_LEDGER`` and ``TRIAL_LEDGER`` are repeated as literals and pinned to
``volab.candidate`` and ``volab.walkforward`` by ``tests/test_counts.py``: ``volab.candidate``
reads the panel by design, so importing it here would cross the R192 import fence.

This module only reads. It does not cover what an agent could read by another route, and it
enforces nothing about the ledgers' contents beyond ``last_origin``.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from volab.errors import VolabError

AGENT_TRIAL_LEDGER: Path = Path("data") / "agent_trials.jsonl"
TRIAL_LEDGER: Path = Path("data") / "trials.jsonl"


class LedgerReadError(VolabError):
    """A ledger line that cannot be read as a JSON object with an ISO ``last_origin``."""


@dataclass(frozen=True)
class TrialView:
    """The lines kept, how many were dropped for ``last_origin`` past ``dev_end`` (or absent), and
    the ledger path they came from, relative to the root."""

    lines: tuple[dict[str, Any], ...]
    excluded: int
    source: Path


def _read(root: Path, source: Path, dev_end: dt.date) -> TrialView:
    path = root / source
    if not path.exists():
        return TrialView(lines=(), excluded=0, source=source)
    kept: list[dict[str, Any]] = []
    excluded = 0
    for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not text.strip():
            continue
        where = f"{source.as_posix()} line {number}"
        try:
            row = json.loads(text)
        except json.JSONDecodeError as error:
            raise LedgerReadError(f"{where} is not JSON: {error}") from error
        if not isinstance(row, dict):
            raise LedgerReadError(f"{where} is not a JSON object")
        if "last_origin" not in row:
            excluded += 1
            continue
        try:
            last_origin = dt.date.fromisoformat(row["last_origin"])
        except (TypeError, ValueError) as error:
            raise LedgerReadError(
                f"{where}: last_origin {row['last_origin']!r} is not an ISO date"
            ) from error
        if last_origin > dev_end:
            excluded += 1
        else:
            kept.append(row)
    return TrialView(lines=tuple(kept), excluded=excluded, source=source)


def read_trials(root: Path, *, dev_end: dt.date) -> TrialView:
    """``data/trials.jsonl`` without any line whose ``last_origin`` is after ``dev_end``."""
    return _read(root, TRIAL_LEDGER, dev_end)


def read_agent_trials(root: Path, *, dev_end: dt.date) -> TrialView:
    """``data/agent_trials.jsonl`` filtered the same way as :func:`read_trials`."""
    return _read(root, AGENT_TRIAL_LEDGER, dev_end)
