"""The research agent: it proposes experiments and never touches the evaluation path.

**This package imports nothing that reaches ``volab.store``, and that is the whole design.** Not a
convention and not a prompt instruction -- an import-graph fact, asserted transitively by
``tests/test_agent_discipline.py``. The project exists because an LLM reviewer in a published
framework approved a feature built from future intraday bars, so a refusal that lives in a sentence
of instruction is the one defence this repository will not accept (R192).

The consequences are visible in what is *absent* here. There is no ``end`` parameter, no ``start``,
no stride, and no date-typed field of any kind on any tool: the harness fixes the window, so a peek
is unrepresentable rather than forbidden (R184). The agent authors no code -- it selects from a
closed vocabulary derived from the feature engine -- so "a feature built from future bars" is not
something it can express (R185). Its evaluations run in a fresh interpreter each
(``volab.candidate``) and append to their own ledgers, never to ``data/trials.jsonl``, because R139
is a user decision (R183). Promotion lives in ``volab.promote``, a separate package reached by a
separate process, so a sprint cannot promote what it just proposed (R188).

What could *not* be made structural is listed in the design under "What is not structural" --
overfitting through many trials is counted rather than refused, report tone cannot be validated, and
"a human approved" is really "a different process ran". Said there rather than papered over here.

**The sprint (P5-D4).** :func:`run_sprint` is the hub of a hub-and-spoke machine (framework lines
74-86): the six roles of :mod:`volab.agent.roles` never call one another, and the hub routes every
handoff through seven steps, :data:`STEPS`, checkpointing after each. **Everything that reaches the
disk, the clock, the network or a subprocess is passed in**, in the shape ``run_configuration``
already uses: the completer, the checkpointer, the log path, ``submit``, the time, the git sha. So a
sprint runs end to end from fakes with no key and no network, which is the framework's own exit
criterion and the whole reason the credential boundary works. The sprint never calls ``load_panel``
and never promotes; every panel read happens in the ``volab.candidate`` grandchild that ``submit``
launches (R191, R192).

**The gate is the only thing that gates a run.** The Critic's verdict is written to the sprint log
and consulted by nothing (see :mod:`volab.agent.roles`); an LLM judging text against a
configuration is the project's founding negative result. A proposal runs if and only if
:func:`volab.agent.vocabulary.gate` accepted it.

**The canary is injected at the parse boundary and is exactly one per sprint (R189).** The
Configurator is asked for every slot, then the raw payload of one slot is *replaced* by one of
:data:`~volab.agent.vocabulary.CANARY_PAYLOADS` before the gate sees it. :func:`canary_placement`
derives the slot and the payload from ``sha256(sprint_id)``, so a sprint replays exactly. If the
gate ever accepts a canary the sprint stops there: it writes ``canary_accepted``, returns
``voided_by_canary`` and promotes nothing. Because the gate step precedes the run step, every
payload is gated before any proposal is run, so within one sprint no proposal has been evaluated
when a canary is found accepted; the rule that a trial which ran keeps its ledger line is honoured
by this module writing to no ledger at all. A sprint the supervisor stops runs no gate and so has
no canary.

**A promotable candidate is a pause, not a promotion (R188).** The sprint writes an
``awaiting_approval`` event per promotable candidate, keyed on ``proposal_id`` (R201), and returns
``awaiting_approval`` with the first as ``SprintResult.pending``; promotion is another package in
another process. Promotable means, mechanically: the child evaluated cleanly (verdict
``evaluated``), the candidate contestant is in the 10% MCS of the ``qlike`` comparison, and the
agent ledger holds at least one line of that cohort to fingerprint. MCS membership says the
candidate was not significantly beaten on the development window; it does not say it won, and the
human decides what it is worth.

**Resumption.** Each finished step is checkpointed under its own name, and the run step also
checkpoints after every proposal (``run:progress``), so a sprint that dies resumes at the next
unfinished step, or the next unrun proposal, without repeating a step that finished or a proposal
whose result was checkpointed. A death *inside* ``configure``, ``critique`` or ``report`` re-asks
the model for that whole step, since only ``run`` checkpoints within a step; and a death between a
child returning and its ``run:progress`` checkpoint resubmits that proposal, so its cohort gets a
second set of ledger lines (counted, and all of them land in its evidence). That window is not
covered. The start line is written once. A sprint whose log already holds a
``complete`` line is refused (:class:`SprintAlreadyComplete`), as a sprint id is used once.

**Nothing computed over data past ``dev_end`` reaches a model, counts included.** The agent
ledger is read through :func:`volab.agent.ledger.read_agent_trials`, which drops lines past
``dev_end`` and counts them; that count is an operator-only quantity. If it is non-zero the sprint
fails (``outcome="failed"``) with an ``integrity`` report on the sprint log and asks no model,
because the child never evaluates past ``dev_end`` and such a line means something else wrote
there. Only the count of kept lines is ever put in a prompt.

**What is not covered.** Events written by a step that then dies before checkpointing are written
again on resume (only ``proposal`` events are de-duplicated); the sprint reads the agent trial
ledger but not the reported one, and no comparison file (M7); report tone is unvalidated and the
digit check on the report is described in :mod:`volab.agent.roles`; and "a human approved" is
still only "a different process ran".
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from volab.agent._llm import Completer
from volab.agent._runner import ChildRun
from volab.agent.ledger import TrialView, read_agent_trials
from volab.agent.memory import Checkpointer
from volab.agent.roles import (
    configurator,
    critic,
    list_models,
    proposal_from_state,
    proposal_to_state,
    reporter,
    researcher,
    runner,
    supervisor,
)
from volab.agent.sprintlog import (
    PendingApproval,
    events,
    fingerprint_of,
    record_completion,
    record_event,
    record_start,
    sprint_ids,
)
from volab.agent.vocabulary import CANARY_PAYLOADS, Proposal, agent_cohort_name, gate
from volab.config import Settings
from volab.errors import VolabError

STEPS: tuple[str, ...] = (
    "supervise",
    "research",
    "configure",
    "gate",
    "run",
    "critique",
    "report",
)

# Slots the Configurator fills, one of which the canary takes (R189: "one proposal slot per
# sprint" is the stated cost). Three real proposals per sprint.
PROPOSALS_PER_SPRINT: int = 4

# How many earlier sprints the Researcher is shown.
_MEMORY_SPRINTS = 5

# The MCS size and loss a candidate must be a member of to be promotable (see the docstring).
_PROMOTION_LOSS = "qlike"
_PROMOTION_MCS_SIZE = "0.1"

# Verdicts (R203) that mean the harness, not the proposal, went wrong.
_FAILED_VERDICTS = frozenset(
    {"crashed", "usage_error", "unrecognised", "audit_refused", "proposal_refused", "disagrees"}
)

# ``completed`` was in this type and never returned; removed rather than left as a value a reader
# would go looking for (R209). A sprint that runs to the end is one of the last three.
Outcome = Literal["voided_by_canary", "no_candidate", "awaiting_approval", "failed"]


class SprintAlreadyComplete(VolabError):
    """The sprint id already has a ``complete`` line in the log; a sprint id is used once."""


@dataclass(frozen=True)
class SprintResult:
    """What one sprint did.

    ``proposals``, ``accepted`` and ``refused`` count the *model's* proposals that reached the gate:
    the canary is excluded from all three and reported by ``canary_refused``, which is true only
    when the gate ran and refused it. A sprint that runs to the end is ``no_candidate``,
    ``awaiting_approval`` or ``failed``; one whose gate accepts a canary is ``voided_by_canary``.
    """

    sprint_id: str
    outcome: Outcome
    reason: str
    proposals: int
    accepted: int
    refused: int
    canary_refused: bool
    pending: PendingApproval | None


def canary_placement(sprint_id: str, slots: int) -> tuple[int, int]:
    """``(slot, payload)`` for ``sprint_id``: where the canary goes and which payload it is.

    The first eight bytes of ``sha256(sprint_id)`` as an integer, modulo ``slots``, give the slot;
    the next eight, modulo the number of payloads, give the payload. A pure function of its two
    arguments, so a sprint replays exactly; and exactly one per sprint, never a rate.
    """
    digest = hashlib.sha256(sprint_id.encode("utf-8")).digest()
    return (
        int.from_bytes(digest[:8], "big") % slots,
        int.from_bytes(digest[8:16], "big") % len(CANARY_PAYLOADS),
    )


def evidence_hash(line: Mapping[str, Any]) -> str:
    """sha256 of one agent-ledger line as canonical JSON (sorted keys, no whitespace).

    The evidence a pending approval rests on is the set of these over the cohort's lines, so the
    promoting process can recompute it from the ledger and see whether the ledger moved. **The
    contract a recomputing process must reproduce byte for byte, or every fingerprint will
    mismatch:** take the *parsed* line (not its raw text), only from the lines that survive
    :func:`volab.agent.ledger.read_agent_trials` (``last_origin`` inside the development window),
    keep those whose ``cohort`` is the approval's, hash each as above, and pass the hashes to
    :func:`volab.agent.sprintlog.fingerprint_of`, which sorts them.
    """
    text = json.dumps(line, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mcs_member(summary: Mapping[str, Any], proposal: Mapping[str, Any]) -> bool | None:
    """Whether the candidate is in the promotion MCS, or ``None`` when that is not reported.

    The candidate is the one contestant of the ``qlike`` comparison that is not a reference
    contestant; if there is not exactly one such, or the comparison is refused or lacks the size,
    the answer is ``None``, which is never promotable.
    """
    for comparison in summary.get("comparisons", []):
        if comparison.get("loss") != _PROMOTION_LOSS or comparison.get("refusal"):
            continue
        names = [c for c in comparison.get("contestants") or [] if c not in proposal["reference"]]
        size = (comparison.get("mcs_sizes") or {}).get(_PROMOTION_MCS_SIZE) or {}
        if len(names) != 1 or "members" not in size:
            return None
        return names[0] in size["members"]
    return None


def _promotable(
    summary: Mapping[str, Any], proposal: Mapping[str, Any], view: TrialView
) -> tuple[tuple[str, ...] | None, str]:
    """``(evidence, reason)``: the evidence hashes if promotable, else ``None`` and why not."""
    if summary["verdict"] != "evaluated":
        return None, f"the child's verdict was {summary['verdict']}"
    member = _mcs_member(summary, proposal)
    if member is None:
        return None, "no usable qlike MCS at 10% was reported"
    if not member:
        return None, "the candidate is not in the qlike MCS at 10%"
    evidence = tuple(
        evidence_hash(line) for line in view.lines if line.get("cohort") == summary["cohort"]
    )
    if not evidence:
        return None, "the agent ledger holds no line of that cohort to rest an approval on"
    return evidence, "promotable"


def render_figures(state: Mapping[str, Any]) -> str:
    """The figures the Reporter may quote, each line naming the thing that produced it (R32).

    Rendered by the harness from the sprint's state, not by a model. The report's digits are
    checked against exactly this text.
    """
    gated = state.get("gated", [])
    real = [g for g in gated if not g["canary"]]
    accepted = sum(1 for g in real if g["accepted"])
    canary = [g for g in gated if g["canary"]]
    ledger = state["ledger"]
    lines = [
        f"sprint id (run_sprint): {state['sprint_id']}",
        f"proposal slots (run_sprint): {PROPOSALS_PER_SPRINT}, one of them the canary",
        f"gate (volab.agent.vocabulary.gate): {accepted} accepted, {len(real) - accepted} refused"
        f"; canary refused: {'yes' if canary and not canary[0]['accepted'] else 'no'}",
        f"agent ledger (volab.agent.ledger, data/agent_trials.jsonl): {ledger['agent_trials']} "
        f"trials evaluated before this sprint",
    ]
    for entry in real:
        summary = state.get("runs", {}).get(entry["proposal_id"])
        if summary is None:
            continue
        member = _mcs_member(summary, entry["proposal"]) if entry["proposal"] else None
        lines.append(
            f"run {entry['proposal_id']} (volab.candidate): {summary['verdict']}; "
            f"cohort {summary['cohort']}; qlike MCS at 10%: "
            f"{'not reported' if member is None else 'member' if member else 'not a member'}"
        )
    return "\n".join(lines)


class _Sprint:
    """One sprint's collaborators and its seven steps; the hub, so it alone calls the roles."""

    def __init__(
        self,
        *,
        sprint_id: str,
        settings: Settings,
        completer: Completer,
        checkpointer: Checkpointer,
        log_path: Path,
        submit: Callable[[Proposal], ChildRun],
        at: dt.datetime,
        sha: str,
        dirty: bool | None,
    ) -> None:
        self.sprint_id = sprint_id
        self.settings = settings
        self.completer = completer
        self.checkpointer = checkpointer
        self.log_path = log_path
        self.submit = submit
        self.at = at
        self.sha = sha
        self.dirty = dirty

    # -- reads ---------------------------------------------------------------------------------

    def _ledger(self) -> TrialView:
        # ``storage_root.parent``, not ``storage_root``: ``storage.root`` is the ``data`` directory
        # and ``AGENT_TRIAL_LEDGER`` is already ``data/agent_trials.jsonl``, so the ledger lives at
        # the root's parent -- where ``volab.candidate`` writes it and ``volab.promote`` reads it.
        # Reading from ``storage_root`` named ``data/data/agent_trials.jsonl``, an empty file on the
        # real configuration, so the sprint saw no trials and its integrity check (R208) checked
        # nothing. Witness: ``test_the_sprint_reads_the_ledger_the_evaluation_child_writes``.
        return read_agent_trials(
            self.settings.storage_root.parent, dev_end=self.settings.splits.dev_end
        )

    def _memory(self) -> list[dict[str, Any]]:
        """Earlier sprints' hypotheses and what the gate said of them (``read_memory``)."""
        remembered: list[dict[str, Any]] = []
        for other in self.checkpointer.sprints():
            if other == self.sprint_id:
                continue
            latest = self.checkpointer.get(other)
            if latest is None or "hypothesis" not in latest[1]:
                continue
            gated = latest[1].get("gated", [])
            real = [g for g in gated if not g["canary"]]
            accepted = sum(1 for g in real if g["accepted"])
            remembered.append(
                {
                    "sprint_id": other,
                    "hypothesis": latest[1]["hypothesis"],
                    "gate": f"{accepted} accepted, {len(real) - accepted} refused",
                }
            )
            if len(remembered) == _MEMORY_SPRINTS:
                break
        return remembered

    def _record_integrity(self, view: TrialView) -> None:
        """Report to the operator that the ledger holds lines past ``dev_end`` (R208).

        An integrity anomaly: the child evaluates only up to dev_end, so this ledger should never
        hold a line past it. Operator-only: the count goes to the sprint log, and to no state field
        and no prompt, because a count over data past dev_end is itself a quantity computed beyond
        the seal. Shared by ``research`` and ``finish``, since a line can appear between the two.
        """
        record_event(
            self.log_path,
            sprint_id=self.sprint_id,
            kind="report",
            role="integrity",
            excluded=view.excluded,
            source=view.source.as_posix(),
            reason="the agent ledger holds lines past dev_end, which no evaluation writes",
            at=self.at,
        )

    # -- the steps -----------------------------------------------------------------------------

    def supervise(self, state: dict[str, Any]) -> dict[str, Any]:
        return supervisor(state, models=list_models())

    def research(self, state: dict[str, Any]) -> dict[str, Any]:
        view = self._ledger()
        if view.excluded:
            self._record_integrity(view)
            # A bare marker, not the reason: the checkpointed state is what a later sprint's
            # memory reads, and a later prompt must carry no trace of why this one failed. The
            # memory feed omits a sprint with no hypothesis, which this one never reaches.
            return {"anomaly": True}
        # The agent's read surface holds nothing computed over data past dev_end, *including
        # counts*: only the kept lines are counted below, and the excluded count (zero here, by
        # the check above) is operator-only by construction.
        ledger = {"agent_trials": len(view.lines)}
        update = researcher(
            state,
            completer=self.completer,
            memory=self._memory(),
            agent_trials=ledger["agent_trials"],
        )
        return {**update, "ledger": ledger}

    def configure(self, state: dict[str, Any]) -> dict[str, Any]:
        emitted = configurator(
            state,
            completer=self.completer,
            slots=PROPOSALS_PER_SPRINT,
            universe=self.settings.universe,
        )["emitted"]
        slot, which = canary_placement(self.sprint_id, PROPOSALS_PER_SPRINT)
        raw = list(emitted)
        raw[slot] = CANARY_PAYLOADS[which]  # replaced at the parse boundary, before the gate
        return {
            "emitted": emitted,
            "raw": json.loads(json.dumps(raw)),
            "canary": {"index": slot, "payload_index": which},
        }

    def gate(self, state: dict[str, Any]) -> dict[str, Any]:
        canary = state["canary"]
        record_event(
            self.log_path,
            sprint_id=self.sprint_id,
            kind="canary",
            index=canary["index"],
            payload_index=canary["payload_index"],
            payload=json.loads(json.dumps(CANARY_PAYLOADS[canary["payload_index"]])),
            at=self.at,
        )
        verdicts: list[dict[str, Any]] = []
        for index, raw in enumerate(state["raw"]):
            is_canary = index == canary["index"]
            verdict = gate(
                raw,
                sprint_id=self.sprint_id,
                index=index,
                universe=self.settings.universe,
                canary=is_canary,
            )
            proposal_id = f"{self.sprint_id}-{index:02d}"
            verdicts.append(
                {
                    "index": index,
                    "proposal_id": proposal_id,
                    "accepted": verdict.accepted,
                    "reason": verdict.reason,
                    "code": None if verdict.code is None else verdict.code.value,
                    "canary": verdict.canary,
                    "proposal": None
                    if verdict.proposal is None
                    else proposal_to_state(verdict.proposal),
                }
            )
            if not verdict.accepted:
                record_event(
                    self.log_path,
                    sprint_id=self.sprint_id,
                    kind="refused",
                    proposal_id=proposal_id,
                    canary=verdict.canary,
                    code=verdict.code.value if verdict.code else None,
                    reason=verdict.reason,
                    at=self.at,
                )
            elif verdict.canary:
                record_event(
                    self.log_path,
                    sprint_id=self.sprint_id,
                    kind="canary_accepted",
                    proposal_id=proposal_id,
                    index=index,
                    reason=verdict.reason,
                    at=self.at,
                )
                break  # aborts here; nothing after a gate that accepted a canary is trusted
        return {"gated": verdicts}

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        runs: dict[str, Any] = dict(state.get("runs", {}))
        logged = {
            e.get("proposal_id")
            for e in events(self.log_path, self.sprint_id)
            if e.get("kind") == "proposal"
        }
        for entry in state["gated"]:
            if not entry["accepted"] or entry["canary"]:
                continue
            proposal_id = entry["proposal_id"]
            if proposal_id not in runs:
                runs[proposal_id] = runner(
                    proposal_from_state(entry["proposal"]), submit=self.submit
                )
                self.checkpointer.put(
                    self.sprint_id, "run:progress", {**state, "runs": json.loads(json.dumps(runs))}
                )
            if proposal_id not in logged:
                self._log_run(runs[proposal_id])
        return {"runs": json.loads(json.dumps(runs))}

    def _log_run(self, summary: Mapping[str, Any]) -> None:
        """The ``proposal`` event, keyed by the child's own ``(cohort, at, git_sha)`` stamps."""
        at = summary["at"] if isinstance(summary.get("at"), str) else self.at.isoformat()
        git_sha = summary["git_sha"] if isinstance(summary.get("git_sha"), str) else self.sha
        record_event(
            self.log_path,
            sprint_id=self.sprint_id,
            kind="proposal",
            proposal_id=summary["proposal_id"],
            cohort=summary.get("cohort")
            or agent_cohort_name(self.sprint_id, int(summary["proposal_id"].rsplit("-", 1)[1])),
            at=at,
            git_sha=git_sha,
            verdict=summary["verdict"],
            exit_code=summary["exit_code"],
            trials_written=summary["trials_written"],
        )
        if summary["verdict"] in ("coverage_refused", "audit_refused", "proposal_refused"):
            record_event(
                self.log_path,
                sprint_id=self.sprint_id,
                kind="refused",
                proposal_id=summary["proposal_id"],
                canary=False,
                code=None,
                source="candidate",
                reason=summary.get("reason"),
                at=self.at,
            )

    def critique(self, state: dict[str, Any]) -> dict[str, Any]:
        metrics = [state["runs"][k] for k in state.get("runs", {})]
        update = critic(state, completer=self.completer, dev_metrics=metrics)
        record_event(
            self.log_path,
            sprint_id=self.sprint_id,
            kind="report",
            role="critic",
            gates=False,
            verdict=update["critique"]["verdict"],
            text=update["critique"]["text"],
            at=self.at,
        )
        return update

    def report(self, state: dict[str, Any]) -> dict[str, Any]:
        figures = render_figures(state)
        update = reporter(state, completer=self.completer, figures=figures)
        body = update["report"]
        record_event(
            self.log_path,
            sprint_id=self.sprint_id,
            kind="report",
            role="reporter",
            validated=body["validated"],
            invented=body["invented"],
            figures=figures,
            draft=body["draft"],
            text=body["draft"] if body["validated"] else figures,
            at=self.at,
        )
        return update

    # -- the end -------------------------------------------------------------------------------

    def finish(self, state: dict[str, Any]) -> SprintResult:
        gated = state.get("gated", [])
        real = [g for g in gated if not g["canary"]]
        accepted = sum(1 for g in real if g["accepted"])
        canary = [g for g in gated if g["canary"]]
        canary_refused = bool(canary) and not canary[0]["accepted"]
        counts = (len(real), accepted, len(real) - accepted, canary_refused)

        def result(
            outcome: Outcome, reason: str, pending: PendingApproval | None = None
        ) -> SprintResult:
            record_completion(self.log_path, sprint_id=self.sprint_id, outcome=outcome, at=self.at)
            return SprintResult(self.sprint_id, outcome, reason, *counts, pending)

        if any(g["canary"] and g["accepted"] for g in gated):
            return result("voided_by_canary", "the gate accepted the canary; nothing was promoted")
        if "anomaly" in state:
            return result(
                "failed",
                "the agent ledger holds lines past dev_end; see the integrity report on the log",
            )
        if state["supervisor"]["action"] == "stop":
            return result("no_candidate", f"the supervisor stopped: {state['supervisor']['note']}")

        view = self._ledger()
        if view.excluded:
            # The same integrity failure ``research`` reports (R208), for a line that appeared
            # after research read the ledger -- a child wrote it, or someone edited the file. The
            # fingerprint below would silently drop it, so no approval may be built on this view.
            # Operator-only; nothing derived from the count is returned or reaches a prompt.
            self._record_integrity(view)
            return result(
                "failed",
                "the agent ledger holds lines past dev_end; see the integrity report on the log",
            )
        candidates: list[PendingApproval] = []
        not_promotable: list[str] = []
        failures: list[str] = []
        for entry in real:
            summary = state.get("runs", {}).get(entry["proposal_id"])
            if summary is None:
                continue
            if summary["verdict"] in _FAILED_VERDICTS:
                failures.append(f"{entry['proposal_id']} {summary['verdict']}")
            evidence, why = _promotable(summary, entry["proposal"], view)
            if evidence is None:
                not_promotable.append(f"{entry['proposal_id']}: {why}")
                continue
            candidates.append(
                PendingApproval(
                    sprint_id=self.sprint_id,
                    proposal_id=entry["proposal_id"],
                    cohort=summary["cohort"],
                    proposal=entry["proposal"],
                    created_at=self.at,
                    git_sha=self.sha,
                    git_dirty=self.dirty,
                    evidence=evidence,
                    fingerprint=fingerprint_of(entry["proposal"], evidence),
                )
            )
        for approval in candidates:
            record_event(
                self.log_path,
                sprint_id=self.sprint_id,
                kind="awaiting_approval",
                proposal_id=approval.proposal_id,
                cohort=approval.cohort,
                proposal=approval.proposal,
                created_at=approval.created_at,
                git_sha=approval.git_sha,
                git_dirty=approval.git_dirty,
                evidence=list(approval.evidence),
                fingerprint=approval.fingerprint,
            )
        if candidates:
            ids = ", ".join(c.proposal_id for c in candidates)
            noted = f"; harness failures alongside: {'; '.join(failures)}" if failures else ""
            return result(
                "awaiting_approval",
                f"{len(candidates)} candidate(s) await approval in a separate process: {ids}"
                + noted,
                candidates[0],
            )
        if failures:
            return result("failed", "; ".join(failures))
        return result("no_candidate", "; ".join(not_promotable) or "no proposal reached the run")


def _halt(state: Mapping[str, Any]) -> bool:
    """Whether the sprint has stopped: supervisor stop, an accepted canary, or a ledger anomaly."""
    if state.get("supervisor", {}).get("action") == "stop" or "anomaly" in state:
        return True
    return any(g["canary"] and g["accepted"] for g in state.get("gated", []))


def run_sprint(
    *,
    sprint_id: str,
    settings: Settings,
    completer: Completer,
    checkpointer: Checkpointer,
    log_path: Path,
    submit: Callable[[Proposal], ChildRun],
    at: dt.datetime,
    sha: str,
    dirty: bool | None,
) -> SprintResult:
    """Run one research sprint through :data:`STEPS`, or resume it from the checkpointer.

    **Everything that reaches the disk, the clock, the network or a subprocess is a parameter**:
    ``completer`` is the only door to a model, ``submit`` the only door to an evaluation, ``at``
    and ``sha`` and ``dirty`` are the caller's captures (R70; ``dirty=None`` means git was
    unreachable and is recorded as such), ``log_path`` and ``checkpointer`` are where the account
    and the state go. Nothing here reads the clock, the environment or a panel, and this function
    never promotes.

    The start line is written, flushed and fsynced before the first step, once per sprint id. A
    ``submit`` that raises propagates, leaving the start line without a completion and the
    checkpoint at the last good point; calling again with the same ``sprint_id`` and a working
    ``submit`` resumes there. Raises :class:`SprintAlreadyComplete` if the sprint already has a
    ``complete`` line.

    Three rules the structure carries rather than asserts: the Critic's verdict is logged and
    never read; the canary is substituted before the gate and exactly one is placed; and a pending
    approval is written, never acted on (R188). See the package docstring for what none of that
    covers.
    """
    hub = _Sprint(
        sprint_id=sprint_id,
        settings=settings,
        completer=completer,
        checkpointer=checkpointer,
        log_path=log_path,
        submit=submit,
        at=at,
        sha=sha,
        dirty=dirty,
    )
    if any(e.get("kind") == "complete" for e in events(log_path, sprint_id)):
        raise SprintAlreadyComplete(f"sprint {sprint_id!r} already has a complete line")
    if sprint_id not in sprint_ids(log_path):
        record_start(log_path, sprint_id=sprint_id, at=at, sha=sha, dirty=dirty)

    latest = checkpointer.get(sprint_id)
    state: dict[str, Any] = {"sprint_id": sprint_id}
    first = 0
    if latest is not None:
        name, state = latest
        in_progress = name.endswith(":progress")
        first = STEPS.index(name.removesuffix(":progress")) + (0 if in_progress else 1)

    steps: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "supervise": hub.supervise,
        "research": hub.research,
        "configure": hub.configure,
        "gate": hub.gate,
        "run": hub.run,
        "critique": hub.critique,
        "report": hub.report,
    }
    for name in STEPS[first:]:
        if _halt(state):
            break
        state = {**state, **steps[name](state)}
        checkpointer.put(sprint_id, name, state)
    return hub.finish(state)
