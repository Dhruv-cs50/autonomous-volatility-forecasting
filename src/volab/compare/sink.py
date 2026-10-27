"""The sink boundary (R95): the comparison layer computes, and this module is the only writer.

Two facts force the shape. **MCS membership cannot go in ``data/trials.jsonl``**: ``count_trials``
counts that file's lines, and a comparison record is not a trial, so a second kind of line would
corrupt the one number the write-up reports. **And the comparison layer cannot read the ledger**:
MCS needs per-origin losses and the ledger holds means. So ``compare`` consumes ``Evaluation``s,
computes, and hands the results to a :class:`Sink`; it never opens a path.

:class:`JsonlSink` writes ``data/comparisons.jsonl`` and **delegates trials to the existing
``volab.walkforward.append_trials``**, so there is exactly one ledger writer
(``test_the_sink_writes_trials_through_the_existing_ledger_writer``). An ``MlflowSink`` is named
and not written: R84 refused MLflow, and the boundary exists so it stays reachable behind a
``pyarrow`` bump without touching anything else in ``compare/``.

**The join key is ``(git_sha, at, symbol, horizon, loss, cohort)``.** Both files are written under
one ``at`` per run, so a reader can join them without guessing, and a comparison whose key has no
trial is a bug rather than a ranking with no counts
(``test_trials_and_comparisons_from_one_run_share_a_join_key``).

Witness that nothing else touches a path: ``test_only_the_sink_opens_a_path``.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from volab.compare.dm import Column, PairResult
from volab.compare.multiple import SIZES, BlockLength, McsResult, SpaResult
from volab.walkforward import Trial, append_trials

# Tracked in git by an explicit ``.gitignore`` exception, like ``data/trials.jsonl`` and for the
# identical reason: an MCS membership ``git clean -xdf`` erases cannot be the one a write-up
# reports.
COMPARISON_LEDGER = Path("data") / "comparisons.jsonl"


def _finite(value: float | None) -> float | None:
    """``None`` for NaN and infinities, which ``json.dumps`` writes and no strict parser reads."""
    return None if value is None or not math.isfinite(value) else float(value)


def _column(column: Column) -> dict[str, object]:
    return {
        "statistic": _finite(column.statistic),
        "p_value": _finite(column.p_value),
        "variance": _finite(column.variance),
        "refusal": column.refusal,
    }


def _block(block: BlockLength | None) -> dict[str, object] | None:
    if block is None:
        return None
    return {
        "politis_white": _finite(block.politis_white),
        "overlap_floor": block.overlap_floor,
        "length": block.length,
    }


def _pair(pair: PairResult) -> dict[str, object]:
    dm = pair.dm
    return {
        "left": pair.left,
        "right": pair.right,
        "n": dm.n,
        "mean_difference": _finite(dm.mean_difference),
        "h_eff": dm.h_eff,
        "harvey": _finite(dm.harvey),
        "headline": _column(dm.headline),
        "bartlett": _column(dm.bartlett),
        "disagree": dm.disagree,
    }


def _mcs(mcs: McsResult) -> dict[str, object]:
    sizes: dict[str, object] = {}
    for size in sorted({*mcs.members, *mcs.refusals}):
        if size in mcs.refusals:
            sizes[f"{size:g}"] = {"refused": mcs.refusals[size]}
        else:
            sizes[f"{size:g}"] = {"members": list(mcs.members[size])}
    return {
        "n_eff": _finite(mcs.n_eff),
        "pvalues": None
        if mcs.pvalues is None
        else {name: _finite(p) for name, p in mcs.pvalues.items()},
        "sizes": sizes,
    }


def _spa(spa: SpaResult) -> dict[str, object]:
    return {
        "benchmark": spa.benchmark,
        "n_eff": _finite(spa.n_eff),
        "block": _block(spa.block),
        "lower": _finite(spa.lower),
        "consistent": _finite(spa.consistent),
        "upper": _finite(spa.upper),
        "refusal": spa.refusal,
    }


@dataclass(frozen=True)
class Comparison:
    """Everything computed for one ``(symbol, horizon, loss, cohort)``, ready for a sink."""

    symbol: str
    horizon: int
    loss: str
    cohort: str
    stride: int
    # Origins in the series, i.e. ``len(evaluation.comparable(loss))``.
    origins: int
    contestants: tuple[str, ...]
    # The rectangular lag and the Bartlett lag.
    q: int
    bartlett_lag: int
    # ``origins / (q + 1)``, printed beside every multiple-comparison figure (R94).
    n_eff: float
    # The block length with both of its inputs; ``None`` if no bootstrap was reachable.
    block: BlockLength | None
    reps: int
    seed: int
    bootstrap: str
    # arch's MCS elimination rule.
    method: str
    pairs: tuple[PairResult, ...]
    # How many pairwise tests this record ran. No selection is made on their p-values.
    dm_tests: int
    mcs: McsResult
    spa: tuple[SpaResult, ...]
    # Percent, for each simulated contestant in the cohort; ``None`` where it was never measured.
    monte_carlo_error: dict[str, float | None]
    # Contestants whose measured error is above the floor: no significance is claimed for them.
    unclaimed: tuple[str, ...]
    # Why the whole record is empty, if it is.
    refusal: str | None

    @property
    def claims_withheld(self) -> bool:
        """DM significance is withheld where the 10% set is refused: ``n_eff`` is too small to
        support a claim, and R94 says to print it and claim nothing."""
        return self.n_eff < 2 / min(SIZES)

    def record(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "horizon": self.horizon,
            "loss": self.loss,
            "cohort": self.cohort,
            "stride": self.stride,
            "origins": self.origins,
            "contestants": list(self.contestants),
            "q": self.q,
            "bartlett_lag": self.bartlett_lag,
            "n_eff": _finite(self.n_eff),
            "block": _block(self.block),
            "reps": self.reps,
            "seed": self.seed,
            "bootstrap": self.bootstrap,
            "mcs_method": self.method,
            "dm_tests": self.dm_tests,
            "dm_significance_claimed": not self.claims_withheld,
            "dm": [_pair(pair) for pair in self.pairs],
            "mcs": _mcs(self.mcs),
            "spa": [_spa(spa) for spa in self.spa],
            "monte_carlo_error": {name: _finite(v) for name, v in self.monte_carlo_error.items()},
            "unclaimed": list(self.unclaimed),
            "refusal": self.refusal,
        }


@runtime_checkable
class Sink(Protocol):
    """Where a run's results go. One protocol, two methods, one implementation now."""

    def append_trials(
        self, trials: Sequence[Trial], *, at: dt.datetime, sha: str, dirty: bool | None
    ) -> int: ...

    def append_comparisons(
        self, records: Sequence[Comparison], *, at: dt.datetime, sha: str, dirty: bool | None
    ) -> int: ...


class JsonlSink:
    """``data/trials.jsonl`` and ``data/comparisons.jsonl`` under ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def append_trials(
        self, trials: Sequence[Trial], *, at: dt.datetime, sha: str, dirty: bool | None
    ) -> int:
        # Delegated, not copied: one ledger writer.
        return append_trials(trials, root=self.root, at=at, sha=sha, dirty=dirty)

    def append_comparisons(
        self, records: Sequence[Comparison], *, at: dt.datetime, sha: str, dirty: bool | None
    ) -> int:
        """Append one line per comparison and return how many were written. Append-only.

        ``at``, ``sha`` and ``dirty`` are the caller's, captured at the start of its run, for the
        reason ``append_trials`` gives (R70): they are what makes this file joinable to the ledger.
        """
        path = self.root / COMPARISON_LEDGER
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for comparison in records:
                line: dict[str, Any] = {
                    "at": at.isoformat(),
                    "git_sha": sha,
                    "git_dirty": dirty,
                    **comparison.record(),
                }
                # ``allow_nan=False`` so a NaN that escaped ``record`` fails here, loudly, rather
                # than writing a line no strict parser can read.
                handle.write(json.dumps(line, allow_nan=False) + "\n")
        return len(records)
