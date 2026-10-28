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
        # The 5% verdict (-1 left better, 0 neither, 1 right better), so a reader of the file need
        # not re-derive the rule.
        "verdict": column.verdict(),
    }


def _block(block: BlockLength | None) -> dict[str, object] | None:
    if block is None:
        return None
    return {
        "politis_white": _finite(block.politis_white),
        "overlap_floor": block.overlap_floor,
        "length": block.length,
    }


def _pair(pair: PairResult, claimed: bool, statement: str | None) -> dict[str, object]:
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
        # Whether a verdict may be stated for this pair (see ``Comparison.pair_claimed``).
        "claimed": claimed,
        # The verdict in words, stored so a reader prints it instead of wording it again (US-3.8).
        "statement": statement,
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


def _spa(spa: SpaResult, claimed: bool, statement: str | None) -> dict[str, object]:
    return {
        "benchmark": spa.benchmark,
        "n_eff": _finite(spa.n_eff),
        "block": _block(spa.block),
        "lower": _finite(spa.lower),
        "consistent": _finite(spa.consistent),
        "upper": _finite(spa.upper),
        "refusal": spa.refusal,
        # Whether a verdict may be stated (see ``Comparison.spa_claimed``).
        "claimed": claimed,
        # The 5% reading in words, ``None`` unless ``claimed`` (see ``Comparison.spa_verdict``).
        "statement": statement,
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
    # The series those errors were measured on, printed beside each (``None`` with no errors).
    monte_carlo_source: str | None
    # Contestants whose measured error is above the floor: no significance is claimed for them.
    unclaimed: tuple[str, ...]
    # Why the whole record is empty, if it is.
    refusal: str | None

    @property
    def claims_withheld(self) -> bool:
        """DM significance is withheld where the 10% set is refused: ``n_eff`` is too small to
        support a claim, and R94 says to print it and claim nothing."""
        return self.n_eff < 2 / min(SIZES)

    def pair_claimed(self, pair: PairResult) -> bool:
        """Whether a verdict may be stated for this pair: the effective sample supports one, both
        contestants' simulation error is below the floor, and both columns answered and agree."""
        dm = pair.dm
        return (
            not self.claims_withheld
            and pair.left not in self.unclaimed
            and pair.right not in self.unclaimed
            and dm.headline.refusal is None
            and dm.bartlett.refusal is None
            and not dm.disagree
        )

    def pair_verdict(self, pair: PairResult) -> str | None:
        """The 5% verdict in words, only where :meth:`pair_claimed`; ``None`` claims nothing."""
        if not self.pair_claimed(pair):
            return None
        verdict = pair.dm.headline.verdict()
        if verdict == 0:
            return "no significant difference at 5% (both columns)"
        better = pair.left if verdict == -1 else pair.right
        return f"{better} significantly better at 5% (both columns)"

    def spa_claimed(self, spa: SpaResult) -> bool:
        """SPA bootstraps the same overlapping series DM does, so it is withheld on the same terms:
        where ``n_eff`` is too thin, and where a contestant in the tested cohort has no claim."""
        return spa.refusal is None and not self.claims_withheld and not self.unclaimed

    def spa_verdict(self, spa: SpaResult) -> str | None:
        """The 5% reading of the consistent p-value, only where :meth:`spa_claimed`."""
        if not self.spa_claimed(spa) or spa.consistent is None:
            return None
        if spa.consistent < 0.05:
            return f"some contestant beats {spa.benchmark} at 5% (consistent p)"
        return f"no contestant shown to beat {spa.benchmark} at 5% (consistent p)"

    def unclaimed_reasons(self) -> dict[str, list[str]]:
        """For each unclaimed contestant, one sentence per rule that withholds its claim (R123).

        ``analyse`` puts a contestant in ``unclaimed`` for three distinct causes: a figure above
        ``MC_ERROR_CEILING_PERCENT``, a figure measured on a series other than the one compared
        **whatever its value**, and no figure at all. A reader told the wrong one is pointed at
        the wrong fix (re-measure a figure that was never too large), and a figure can fire more
        than one rule, so every one that applies is stated. This restates ``analyse``'s rule to
        word it, hence ``test_the_stored_reasons_are_exactly_the_unclaimed_contestants``; the rule
        should ultimately emit its own cause.
        """
        # In the function because ``volab.compare`` imports this module before it defines the
        # ceiling, so a module-level import is circular.
        from volab.compare import MC_ERROR_CEILING_PERCENT

        this_series = f"{self.symbol} h{self.horizon}"
        reasons: dict[str, list[str]] = {}
        for name in self.unclaimed:
            value = self.monte_carlo_error.get(name)
            why: list[str] = []
            if value is None:
                why.append(
                    f"{name}'s Monte Carlo error was not measured, so no precision is established"
                )
            else:
                if self.monte_carlo_source != this_series:
                    why.append(
                        f"{name}'s Monte Carlo error was measured on another series "
                        f"({self.monte_carlo_source}), not on {this_series}, so it cannot "
                        "authorise a claim whatever its value"
                    )
                if value > MC_ERROR_CEILING_PERCENT:
                    # The figure itself is not in the sentence: it is printed only beside its
                    # source (R102), and a reason is read without it.
                    why.append(
                        f"{name}'s Monte Carlo error is above the "
                        f"{MC_ERROR_CEILING_PERCENT:g}% ceiling"
                    )
            reasons[name] = why
        return reasons

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
            "dm": [
                _pair(pair, self.pair_claimed(pair), self.pair_verdict(pair)) for pair in self.pairs
            ],
            "mcs": _mcs(self.mcs),
            "spa": [_spa(spa, self.spa_claimed(spa), self.spa_verdict(spa)) for spa in self.spa],
            "monte_carlo_error": {name: _finite(v) for name, v in self.monte_carlo_error.items()},
            "monte_carlo_error_source": self.monte_carlo_source,
            "unclaimed": list(self.unclaimed),
            "unclaimed_reasons": self.unclaimed_reasons(),
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
