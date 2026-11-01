"""The decisions sink: ``data/decisions.jsonl``, the evaluator layer's one writer.

A decision is not a trial. ``count_trials`` counts the lines of ``data/trials.jsonl``, the holdout
pre-registration pins that number, and a record carrying coverage p-values and a P&L is a second
kind of line that would corrupt it -- the argument ``compare/sink.py`` makes for the comparison
ledger, and the reason this is a file of its own. So :class:`JsonlSink` writes decisions here and
**delegates trials to the existing ``volab.walkforward.append_trials``**: there is still exactly
one trial writer, and nothing in this module can put a decision on the trial ledger.

**One line per ``(symbol, horizon, cohort, alpha)``.** Each carries the FZ0 comparison
(``volab.compare.analyse``'s record, whole), a coverage result per contestant and a vol-target
result per contestant. The vol-target result does not depend on ``alpha``, so it repeats across a
configuration's lines; that is a duplicate of one measurement, not two measurements.

Four serialisation contracts, each witnessed in ``tests/test_evaluators_cli.py``:

* **A withheld Christoffersen p-value travels with its reason.** Where the independence statistic
  is zero by construction ``christoffersen_p`` is ``None`` and ``christoffersen_withheld`` says
  why, written immediately after it; ``refusal`` stays ``null`` because the run was fine. A
  ``null`` p-value with no reason is indistinguishable from a measurement that was never made.
* **An undefined statistic is ``null``, never ``0.0``.** ``realized_vol``, ``sharpe`` and
  ``turnover`` are undefined for a single origin or a constant P&L; zero would read as a strategy
  with no risk. ``json.dumps`` would also emit a bare ``NaN`` for a non-finite float, which no
  strict parser reads back, so those become ``null`` too, and the dump runs with
  ``allow_nan=False`` so one that escaped this module fails here and not in a reader.
* **An ex-date count says what it is a count of, and whether it could be non-zero.**
  ``distribution_flags_known`` is ``false`` where the symbol has no dividend source, so a zero is
  not read as "no ex-dates occurred"; ``distribution_holdings_affected`` counts holdings, because
  one ex-date sits in several overlapping holdings and the session count alone understates how
  much of the P&L is wrong.
* **An empty origin set is stated, not implied.** ``vol_target`` is ``null`` with
  ``vol_target_refusal`` carrying why, because the function that sizes the position refuses an
  empty series and a bare ``null`` would be read as a missing measurement.

``data/decisions.jsonl`` is not tracked in git: no count from it reaches the write-up, and the two
ledgers that are tracked (``trials.jsonl``, ``comparisons.jsonl``) are tracked precisely because
their line counts are reported numbers. If a count from this file is ever reported it needs the
same ``.gitignore`` exception, at that point and not before.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from volab.compare.sink import Comparison
from volab.evaluators import CoverageResult, VolTargetResult
from volab.walkforward import Trial, append_trials

DECISION_LEDGER = Path("data") / "decisions.jsonl"


def _finite(value: float | None) -> float | None:
    """``None`` for ``None``, NaN and the infinities; the float otherwise.

    Restated from ``volab.compare.sink``, where it is private. ``None`` stays ``None``: nothing
    here turns an undefined statistic into a number.
    """
    return None if value is None or not math.isfinite(value) else float(value)


def _coverage(result: CoverageResult) -> dict[str, object]:
    return {
        "breaches": result.breaches,
        "expected": _finite(result.expected),
        "kupiec_p": _finite(result.kupiec_p),
        "christoffersen_p": _finite(result.christoffersen_p),
        # Beside the p-value it explains, and ``None`` exactly where the p-value is present or the
        # whole result was refused.
        "christoffersen_withheld": result.christoffersen_withheld,
        "refusal": result.refusal,
    }


def _vol_target(result: VolTargetResult) -> dict[str, object]:
    return {
        "mean_return": _finite(result.mean_return),
        "realized_vol": _finite(result.realized_vol),
        "sharpe": _finite(result.sharpe),
        "turnover": _finite(result.turnover),
        "capped_origins": result.capped_origins,
        # The ex-date count the P&L is wrong about (a disclosure, not a correction).
        "distribution_sessions": result.distribution_sessions,
    }


@dataclass(frozen=True)
class Decision:
    """Everything evaluated for one ``(symbol, horizon, cohort, alpha)``, ready for the sink."""

    symbol: str
    horizon: int
    cohort: str
    alpha: float
    stride: int
    # Origins the decision series (and so the coverage and the P&L) rest on.
    origins: int
    contestants: tuple[str, ...]
    # The FZ0 loss of every contestant's (VaR, ES), analysed by ``volab.compare.analyse``.
    fz0: Comparison
    coverage: dict[str, CoverageResult]
    # ``None`` only when there is no origin to size a position on, in which case
    # ``vol_target_refusal`` says so.
    vol_target: dict[str, VolTargetResult] | None
    vol_target_refusal: str | None
    # How many sessions the ``has_distribution`` flags were read over, so a count of zero is
    # printed beside the size of what was looked at.
    distribution_window: int
    # How many of the ``origins`` holdings contain an ex-date. The session count above counts an
    # ex-date once however many overlapping holdings it sits in; this is the measure of how much
    # of the P&L is biased, and the two differ by up to the overlap factor (about 4-5x at h=22
    # and stride 5).
    distribution_holdings_affected: int
    # Whether the flags could have been anything but ``False``: ``False`` where the symbol has no
    # dividend source, in which case a count of zero is "we had no way to know", not "none".
    distribution_flags_known: bool

    def record(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "horizon": self.horizon,
            "cohort": self.cohort,
            "alpha": self.alpha,
            "stride": self.stride,
            "origins": self.origins,
            "contestants": list(self.contestants),
            "fz0": self.fz0.record(),
            "coverage": {name: _coverage(result) for name, result in self.coverage.items()},
            "vol_target": None
            if self.vol_target is None
            else {name: _vol_target(result) for name, result in self.vol_target.items()},
            "vol_target_refusal": self.vol_target_refusal,
            "distribution_window": self.distribution_window,
            "distribution_holdings_affected": self.distribution_holdings_affected,
            "distribution_flags_known": self.distribution_flags_known,
        }


class JsonlSink:
    """``data/trials.jsonl`` (through the existing writer) and ``data/decisions.jsonl``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def append_trials(
        self, trials: Sequence[Trial], *, at: dt.datetime, sha: str, dirty: bool | None
    ) -> int:
        # Delegated, not copied: one trial writer.
        return append_trials(trials, root=self.root, at=at, sha=sha, dirty=dirty)

    def append_decisions(
        self, records: Sequence[Decision], *, at: dt.datetime, sha: str, dirty: bool | None
    ) -> int:
        """Append one line per decision and return how many were written. Append-only.

        ``at``, ``sha`` and ``dirty`` are the caller's, captured at the start of its run and shared
        with the trials it wrote, so the two files join on ``(git_sha, at, symbol, horizon,
        cohort)`` without guessing (R70, R95).
        """
        path = self.root / DECISION_LEDGER
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for decision in records:
                line: dict[str, Any] = {
                    "at": at.isoformat(),
                    "git_sha": sha,
                    "git_dirty": dirty,
                    **decision.record(),
                }
                handle.write(json.dumps(line, allow_nan=False) + "\n")
        return len(records)
