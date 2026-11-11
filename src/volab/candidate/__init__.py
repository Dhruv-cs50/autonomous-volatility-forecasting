"""The evaluation child: one agent proposal, one fresh interpreter, its own two ledgers.

Separate from ``volab.agent`` because the agent must not reach a read path and this module must
(R192). It is the only writer of ``data/agent_trials.jsonl`` and ``data/agent_comparisons.jsonl``,
and it reaches them through the ``append`` callable ``run_configuration`` already takes (R97) --
so **the agent's runs cannot append to the reported trial ledger, because they are handed a
different ``append``**, which is a fact about the wiring rather than a rule anyone follows.
Nothing in ``volab.walkforward`` was changed to allow it, and nothing here writes
``data/trials.jsonl`` or ``data/comparisons.jsonl`` (R183: R139 is a *user* decision that nothing
appends to the reported ledgers before the holdout reveal).

A fresh interpreter per proposal is not a preference: ``volab.daily``'s docstring records that a
memoised full-sample statistic filled by one build and reused by the next is invisible to
truncation equivalence *within* a process, and a sprint evaluating several proposals in sequence
would reintroduce that across proposals, in a loop. This module is what one such interpreter runs;
``python -m volab.candidate`` (``__main__``) is its only entry point and launching it is the
sprint runner's job, not this module's.

**The window is fixed here and is not a parameter of anything public (R184).** ``FIRST_ORIGIN``,
``STRIDE`` and ``MINIMUM_TRAINING`` are the walk-forward CLI's defaults with the compare CLI's
stride, and the last origin is ``settings.splits.dev_end``. Choosing the window or the stride *is*
selection, so no function here takes either. They are module attributes rather than literals
inside a function only so that a test can move them onto a seven-month toy panel; the shipped
values are pinned by a test of their own.

**Coverage is decided before any model is fitted, and that is what protects the cohort.**
``run_configuration`` calls ``coverage_refusal`` ahead of ``evaluate`` for a cohort that declares a
requirement, and ``HarX`` declares its extras in ``Domain.requires``. If a sparse extra reached
``evaluate`` it would drop those origins for the **whole cohort**: ``Domain``'s docstring in
``volab.models.base`` spells out the three routes that read ``cohort.requires_at``, and every
reference contestant's origins before the extra's first qualifying one would be intersected out of
its loss by ``Evaluation.comparable``. A ``ModelSkip`` would only absorb that (one contestant at one
origin); refusing the window up front prevents it, and writes no trial line. Measured on the real
panel, ``iv_h1`` and ``iv_var_h1`` are non-null on 912 of 2,312 development rows, the first on
2022-05-13, against a window that begins 2021-01-04, so a proposal naming one is refused. A
refusal is read off the driver's own report, so its reason is ``coverage_refusal``'s text verbatim.

**Cohort names live in a reserved namespace.** :func:`evaluate_cohort` refuses a cohort whose name
does not match ``AGENT_COHORT_PATTERN`` before it reads or writes anything. ``evaluate`` itself
guards only ``phase2-baseline``, so without this an agent run could write ledger lines under a
registered cohort's name.

**The proposal file** the child reads (``__main__``) is a JSON object with the five fields of
``volab.agent.vocabulary.Proposal`` plus ``proposal_id`` (a sprint id, a hyphen and a two-digit
index). It is re-validated, because the gate's verdict does not travel with the file.

What this module does **not** do: it does not read the environment, scrub it or pass any key to
the store (R193: the seal is that no code path asks for the sealed window); it does not choose
which proposals to run; and it does not compute a verdict on whether a candidate is good -- it
records the comparison and the trials, and the promotion decision is someone else's.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import pandas as pd

from volab.agent.vocabulary import AGENT_COHORT_PATTERN, Proposal, agent_cohort_name
from volab.compare import analyse
from volab.compare.series import loss_series
from volab.compare.sink import Comparison
from volab.config import Settings
from volab.errors import DatasetNotFound
from volab.models.base import Cohort, Model
from volab.models.harx import harx
from volab.models.recalibrated import har_levels_mz
from volab.paths import Tier
from volab.sessions import get_calendar
from volab.store import load_panel
from volab.walkforward import (
    BASELINE_COHORT,
    LOSSES,
    Evaluation,
    Trial,
    load_for,
    run_configuration,
)

AGENT_TRIAL_LEDGER: Path = Path("data") / "agent_trials.jsonl"
AGENT_COMPARISON_LEDGER: Path = Path("data") / "agent_comparisons.jsonl"

FEATURE_SET = "har_daily"

# R184: the window, fixed by the child. The first origin and the minimum training are the
# walk-forward CLI's defaults; the stride is the compare CLI's, the one R94's table was derived at.
FIRST_ORIGIN: dt.date = dt.date(2021, 1, 4)
STRIDE: int = 5
MINIMUM_TRAINING: int = 250


class CohortNameRefused(ValueError):
    """A cohort name outside ``AGENT_COHORT_PATTERN``: it would be filed under someone else's."""


def require_agent_cohort(name: str) -> str:
    """``name`` if it is in the reserved agent namespace, else :class:`CohortNameRefused`.

    ``re.fullmatch`` and not ``match``: the pattern is anchored with ``$``, which also matches
    before a trailing newline, so ``match`` would accept ``"agent-...-03\\n"``.
    """
    if not re.fullmatch(AGENT_COHORT_PATTERN, name):
        raise CohortNameRefused(
            f"cohort name {name!r} is outside the reserved agent namespace "
            f"{AGENT_COHORT_PATTERN!r}; an agent run never writes under another cohort's name"
        )
    return name


def resolve_reference(names: Sequence[str]) -> tuple[Model, ...]:
    """The reference contestants named, in the order named, or ``ValueError``.

    The names are those of ``volab.agent.vocabulary.REFERENCE_CONTESTANTS``: the three baseline
    models and the recalibrated HAR. The order is kept because the first contestant's
    ``observations`` is ``OriginResult.training_rows`` (R87), so reordering would change a number.
    A repeated name is refused: two contestants of one name would share a ledger key.
    """
    available: dict[str, Model] = {model.name: model for model in BASELINE_COHORT.models}
    mz = har_levels_mz()
    available[mz.name] = mz
    unknown = [name for name in names if name not in available]
    if unknown:
        raise ValueError(f"unknown reference contestant(s) {unknown!r}; known: {sorted(available)}")
    if len(set(names)) != len(names):
        raise ValueError(f"reference repeats a contestant: {list(names)!r}")
    return tuple(available[name] for name in names)


def cohort_name_for(proposal: Proposal) -> str:
    """The reserved-namespace cohort name of ``proposal``, from its ``proposal_id``.

    ``proposal_id`` is ``{sprint_id}-{index:02d}`` (``volab.agent.vocabulary.validate``), so it is
    split at its last hyphen. ``ValueError`` if it is not of that shape.
    """
    sprint, _, index = proposal.proposal_id.rpartition("-")
    if not sprint or not index.isdigit():
        raise ValueError(f"proposal id {proposal.proposal_id!r} is not '<sprint id>-<index>'")
    return agent_cohort_name(sprint, int(index))


# ---------------------------------------------------------------------------
# The two ledgers.
# ---------------------------------------------------------------------------


def append_agent_trials(
    trials: Sequence[Trial], *, root: Path, at: dt.datetime, sha: str, dirty: bool | None
) -> int:
    """Append one line per trial to ``data/agent_trials.jsonl`` and return how many were written.

    **The bytes are those of** ``volab.walkforward.append_trials`` -- the same prefix and
    ``Trial.record()`` -- so that if the user later rules the ledgers should be one, the migration
    is ``cat`` (R183). It is restated and not delegated, because ``append_trials`` writes a path
    that is a constant in ``walkforward``, which this story may not change.
    ``test_the_agent_ledger_lines_are_byte_identical_to_what_append_trials_writes`` is the witness:
    it writes one trial through both and compares the files.
    """
    path = root / AGENT_TRIAL_LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for trial in trials:
            handle.write(
                json.dumps(
                    {"at": at.isoformat(), "git_sha": sha, "git_dirty": dirty, **trial.record()}
                )
                + "\n"
            )
    return len(trials)


def count_agent_trials(root: Path) -> int:
    """Lines in ``data/agent_trials.jsonl``, or ``0`` if the file does not exist.

    ``root`` is the **parent** of the storage root, i.e. ``settings.storage_root.parent``, because
    ``AGENT_TRIAL_LEDGER`` already begins with ``data/``. Passing ``settings.storage_root`` itself
    reads ``data/data/agent_trials.jsonl``, an empty file, and returns ``0`` (R211).
    """
    path = root / AGENT_TRIAL_LEDGER
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


@dataclass(frozen=True)
class AgentComparison:
    """One ``Comparison`` and the span of origins it rests on.

    ``comparisons.jsonl`` records carry ``origins`` (a count) and ``stride`` but no origin dates,
    which is why the agent cannot safely read that file at all (M7). The span is the first and last
    of the *comparable* origins of this loss -- the ones the statistics used, which is the set a
    promotion rests on -- and is ``None`` for both when that set is empty.
    """

    comparison: Comparison
    first_origin: dt.date | None
    last_origin: dt.date | None

    def record(self) -> dict[str, object]:
        return {
            **self.comparison.record(),
            "first_origin": None if self.first_origin is None else self.first_origin.isoformat(),
            "last_origin": None if self.last_origin is None else self.last_origin.isoformat(),
        }


def append_agent_comparisons(
    comparisons: Sequence[AgentComparison],
    *,
    root: Path,
    at: dt.datetime,
    sha: str,
    dirty: bool | None,
) -> int:
    """Append one line per comparison to ``data/agent_comparisons.jsonl``; return the count.

    The prefix is ``volab.compare.sink.JsonlSink.append_comparisons``'s, and the dump uses
    ``allow_nan=False`` for the reason it gives. It is **not** delegated to that sink, whose path
    is ``data/comparisons.jsonl``: the reported file is never opened from here.
    """
    path = root / AGENT_COMPARISON_LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for item in comparisons:
            line = {"at": at.isoformat(), "git_sha": sha, "git_dirty": dirty, **item.record()}
            handle.write(json.dumps(line, allow_nan=False) + "\n")
    return len(comparisons)


# ---------------------------------------------------------------------------
# One evaluation.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationOutcome:
    """What one proposal's evaluation did."""

    cohort: str
    # The trial lines written to the agent ledger, empty on a refusal.
    trials: tuple[Trial, ...]
    written: int
    refused: bool
    # ``"coverage"`` (the window cannot support a column; no model was fitted) or ``"audit"``
    # (a build read past its cutoff; models were fitted and nothing was appended). ``None`` unless
    # refused.
    refusal_kind: str | None
    # The reason, verbatim from the driver's report; ``None`` unless refused.
    reason: str | None
    # One per loss, as ``comparisons.jsonl`` holds one per loss. Empty on a refusal.
    comparisons: tuple[AgentComparison, ...]
    # The rows built per origin differ from the persisted table. Not a refusal.
    disagrees: bool


def _persisted(settings: Settings, symbol: str) -> pd.DataFrame | None:
    """The feature table as the feature CLI wrote it, or ``None`` if the store holds none.

    ``compare/__main__`` and ``walkforward/__main__`` each restate this and so does this module:
    it is private to a ``__main__`` there. Bounded by ``dev_end`` exactly as theirs is. When it
    returns ``None``, ``run_configuration`` reports that coverage was NOT CHECKED and falls through
    to ``evaluate``'s per-origin skips; that path is not a refusal here either.
    """
    try:
        frame = load_panel(
            FEATURE_SET,
            end=settings.splits.dev_end,
            symbols=[symbol],
            tier=Tier.FEATURES,
            settings=settings,
        )
    except DatasetNotFound:
        return None
    return frame.sort_values("as_of").reset_index(drop=True)


def evaluate_proposal(
    proposal: Proposal, *, settings: Settings, at: dt.datetime, sha: str, dirty: bool | None
) -> EvaluationOutcome:
    """Evaluate ``proposal`` in a cohort of its own: the reference contestants and the candidate.

    The cohort is ``(*resolve_reference(proposal.reference), harx(proposal.extra_regressors))``
    under the reserved name derived from ``proposal.proposal_id``, reference first (R87). ``at``,
    ``sha`` and ``dirty`` are the caller's, captured before any number was produced (R70).
    """
    models = (*resolve_reference(proposal.reference), harx(proposal.extra_regressors))
    return evaluate_cohort(
        proposal,
        Cohort(cohort_name_for(proposal), models),
        settings=settings,
        at=at,
        sha=sha,
        dirty=dirty,
    )


def evaluate_cohort(
    proposal: Proposal,
    cohort: Cohort,
    *,
    settings: Settings,
    at: dt.datetime,
    sha: str,
    dirty: bool | None,
) -> EvaluationOutcome:
    """Run ``cohort`` over ``proposal``'s symbol and horizon and write the two agent ledgers.

    Refuses a cohort name outside the reserved namespace first, before the panel is read. The
    trial lines go through ``run_configuration``'s ``append`` seam (R97), appended per
    configuration as it finishes (R74); the comparison records are appended after, one per loss.
    A refusal returns with nothing written to either ledger.
    """
    require_agent_cohort(cohort.name)
    # Both read at call time, so the module attributes stay the single place the window lives.
    first, every, minimum = FIRST_ORIGIN, STRIDE, MINIMUM_TRAINING
    root = settings.storage_root.parent
    calendar = get_calendar(settings)
    lines: list[str] = []

    def report(line: str) -> None:
        lines.append(line)
        print(line)

    panel, targets = load_for(settings, proposal.symbol, proposal.horizon, report=report)
    outcome = run_configuration(
        settings,
        proposal.symbol,
        proposal.horizon,
        panel,
        targets,
        calendar=calendar,
        first=first,
        last=settings.splits.dev_end,
        every=every,
        minimum_training=minimum,
        cohort=cohort,
        persisted=partial(_persisted, settings, proposal.symbol),
        append=lambda trials: append_agent_trials(trials, root=root, at=at, sha=sha, dirty=dirty),
        report=report,
    )

    if outcome.refused:
        # ``evaluation`` is ``None`` exactly when ``coverage_refusal`` refused before any fit; an
        # audit refusal comes after ``evaluate`` has run and returns it.
        kind = "coverage" if outcome.evaluation is None else "audit"
        return EvaluationOutcome(
            cohort=cohort.name,
            trials=(),
            written=0,
            refused=True,
            refusal_kind=kind,
            reason=_reason_from(lines),
            comparisons=(),
            disagrees=False,
        )

    evaluation = outcome.evaluation or Evaluation(
        symbol=proposal.symbol,
        horizon=proposal.horizon,
        results=(),
        features=pd.DataFrame(),
        skipped=(),
        requested=0,
        every=every,
        contestants=cohort.contestants,
        cohort=cohort.name,
    )
    comparisons: list[AgentComparison] = []
    for loss in LOSSES:
        series = loss_series(evaluation, loss, cohort=cohort.name)
        comparisons.append(
            AgentComparison(
                analyse(series),
                series.origins[0] if series.origins else None,
                series.origins[-1] if series.origins else None,
            )
        )
    append_agent_comparisons(comparisons, root=root, at=at, sha=sha, dirty=dirty)
    return EvaluationOutcome(
        cohort=cohort.name,
        trials=outcome.trials,
        written=outcome.written,
        refused=False,
        refusal_kind=None,
        reason=None,
        comparisons=tuple(comparisons),
        disagrees=outcome.disagrees,
    )


def _reason_from(lines: Sequence[str]) -> str | None:
    """The text after the driver's ``REFUSED:`` marker, with any continuation line joined on.

    ``run_configuration`` reports a refusal as one line (coverage) or two (audit), and returns
    straight after, so every line from the marker on belongs to the reason. The marker is the one
    string this relies on; ``None`` if it is absent, which the caller treats as a defect.
    """
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("REFUSED: "):
            rest = [stripped.removeprefix("REFUSED: ")]
            rest.extend(item.strip() for item in lines[index + 1 :])
            return " ".join(rest)
    return None


__all__ = [
    "AGENT_COMPARISON_LEDGER",
    "AGENT_TRIAL_LEDGER",
    "FIRST_ORIGIN",
    "MINIMUM_TRAINING",
    "STRIDE",
    "AgentComparison",
    "CohortNameRefused",
    "EvaluationOutcome",
    "append_agent_comparisons",
    "append_agent_trials",
    "count_agent_trials",
    "cohort_name_for",
    "evaluate_cohort",
    "evaluate_proposal",
    "require_agent_cohort",
    "resolve_reference",
]
