"""The six roles and the seven-step sprint machine, exercised end to end from fakes (P5-D4).

Every test here runs a whole sprint, or a role, with a ``FakeCompleter``, a ``FileCheckpointer`` in
``tmp_path``, a log under ``tmp_path`` and a **stub** ``submit``: no key, no network, no child
interpreter and no panel. That is the framework's own exit criterion for Phase 5 and the property
that makes the credential boundary real.

**What a stub ``submit`` can and cannot show.** It shows what the hub does with a child's outcome
-- what it logs, what it checkpoints, what it will and will not promote. It shows nothing about
whether the real child produces outcomes of that shape; ``tests/test_agent_runner.py`` and
``tests/test_candidate_cli.py`` own that. The stub's comparison records are the shape the child's
result file holds, written by hand, so a drift in the child's shape would not be caught here.

Three rules are tested in both directions because a test of one direction is half a witness: the
Critic's verdict changes no run (a rejection does not stop one, an approval does not cause one); a
gate that accepts everything voids the sprint (without which the canary tests prove only that the
validator works); and the report's digit check rejects an invented figure and passes a faithful
one.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

import volab.agent as agent
from volab.agent import (
    PROPOSALS_PER_SPRINT,
    STEPS,
    SprintAlreadyComplete,
    SprintResult,
    canary_placement,
    evidence_hash,
    run_sprint,
)
from volab.agent import roles as roles_module
from volab.agent._llm import FakeCompleter
from volab.agent._runner import ChildRun
from volab.agent.memory import FileCheckpointer
from volab.agent.roles import (
    MODEL_FAMILIES,
    ROLES,
    invented_numbers,
    list_models,
    parse_payload,
    supervisor,
)
from volab.agent.sprintlog import events, fingerprint_of, pending_approvals, record_event
from volab.agent.vocabulary import (
    CANARY_PAYLOADS,
    VOCABULARY,
    GateVerdict,
    Proposal,
    RefusalCode,
    validate,
)
from volab.config import Settings, Splits
from volab.errors import ProposalRefused

SPRINT = "2026-11-10-weekly"
AT = dt.datetime(2026, 11, 10, 6, 0, tzinfo=dt.UTC)
SHA = "0123abc"
SRC = Path(__file__).resolve().parents[1] / "src" / "volab" / "agent"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        universe=("SPY",),
        exogenous=(),
        calendar="XNYS",
        splits=Splits(dev_end=dt.date(2025, 12, 31), holdout_start=dt.date(2026, 1, 2)),
        storage_root=tmp_path,
    )


def valid_reply(slot: int) -> str:
    """A well-formed Configurator reply, distinct per slot."""
    return json.dumps(
        {
            "family": "harx",
            "extra_regressors": [VOCABULARY[slot]],
            "symbol": "SPY",
            "horizon": 5,
            "reference": ["har_levels"],
        }
    )


def script(
    *,
    configurator: Sequence[str] | None = None,
    critic: str = "APPROVE the configurations follow from the hypothesis.",
    report: str = "The sprint ran and the gate refused the canary.",
) -> list[str]:
    """The completer's replies in call order: researcher, N configurator, critic, reporter."""
    replies = list(configurator or [valid_reply(i) for i in range(PROPOSALS_PER_SPRINT)])
    return ["Extra regressors may help a HAR model.", *replies, critic, report]


class StubSubmit:
    """A stand-in for ``volab.agent._runner.submit`` that records calls and writes no panel.

    ``verdict`` is the child outcome to fake. It also writes the cohort's line to the agent trial
    ledger under ``root`` for an ``evaluated`` child, since the hub reads that ledger as evidence.
    """

    def __init__(
        self,
        root: Path,
        *,
        verdict: str = "evaluated",
        member: bool = True,
        ledger_lines: bool = True,
        fail_on_call: int | None = None,
    ) -> None:
        self.root = root
        self.verdict = verdict
        self.member = member
        self.ledger_lines = ledger_lines
        self.fail_on_call = fail_on_call
        self.calls: list[Proposal] = []

    def __call__(self, proposal: Proposal) -> ChildRun:
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError("the stub was told to die here")
        self.calls.append(proposal)
        sprint, _, index = proposal.proposal_id.rpartition("-")
        cohort = agent.agent_cohort_name(sprint, int(index))
        candidate = "harx+" + "+".join(proposal.extra_regressors)
        names = [*proposal.reference, candidate]
        members = names if self.member else list(proposal.reference)
        comparison = {
            "loss": "qlike",
            "contestants": names,
            "refusal": None,
            "mcs": {"sizes": {"0.1": {"members": members}, "0.25": {"members": names}}},
        }
        code = {"evaluated": 0, "coverage_refused": 3, "crashed": 1, "disagrees": 1}[self.verdict]
        result: dict[str, Any] | None = None
        if self.verdict != "crashed":
            result = {
                "pid": 4000 + len(self.calls),
                "status": "refused" if self.verdict == "coverage_refused" else "ok",
                "reason": "iv_h1 has no coverage" if self.verdict == "coverage_refused" else None,
                "cohort": cohort,
                "at": "2026-11-10T06:00:01+00:00",
                "git_sha": "childsha",
                "trials_written": 0 if self.verdict == "coverage_refused" else 1,
                "comparisons": [] if self.verdict == "coverage_refused" else [comparison],
            }
        if self.ledger_lines and self.verdict in ("evaluated", "disagrees"):
            ledger = self.root / "data" / "agent_trials.jsonl"
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "at": "2026-11-10T06:00:01+00:00",
                            "git_sha": "childsha",
                            "cohort": cohort,
                            "model": candidate,
                            "last_origin": "2025-12-30",
                        }
                    )
                    + "\n"
                )
        return ChildRun(
            proposal_id=proposal.proposal_id,
            exit_code=code,
            pid=None if result is None else result["pid"],
            result=result,
            output_tail="boom" if self.verdict == "crashed" else "",
        )


def sprint(
    tmp_path: Path,
    settings: Settings,
    *,
    completer: Any,
    submit: Any,
    sprint_id: str = SPRINT,
    checkpointer: FileCheckpointer | None = None,
    dirty: bool | None = False,
) -> SprintResult:
    return run_sprint(
        sprint_id=sprint_id,
        settings=dataclasses.replace(settings, storage_root=tmp_path),
        completer=completer,
        checkpointer=checkpointer or FileCheckpointer(tmp_path / "memory"),
        log_path=tmp_path / "data" / "agent_sprints.jsonl",
        submit=submit,
        at=AT,
        sha=SHA,
        dirty=dirty,
    )


def log_of(tmp_path: Path, kind: str | None = None) -> list[dict[str, Any]]:
    found = events(tmp_path / "data" / "agent_sprints.jsonl", SPRINT)
    return [e for e in found if kind is None or e["kind"] == kind]


# --- the full sprint ---------------------------------------------------------------------------


def test_a_full_sprint_runs_end_to_end_from_fakes_and_writes_the_events_the_spec_names(
    tmp_path: Path, settings: Settings
) -> None:
    submit = StubSubmit(tmp_path)
    result = sprint(tmp_path, settings, completer=FakeCompleter(script()), submit=submit)

    kinds = [e["kind"] for e in log_of(tmp_path)]
    assert kinds[0] == "start"
    assert kinds.count("start") == 1
    assert kinds.count("canary") == 1
    assert kinds.count("proposal") == PROPOSALS_PER_SPRINT - 1  # the canary slot is not a proposal
    assert kinds.count("refused") == 1  # the canary
    assert kinds[-1] == "complete"
    assert kinds.count("complete") == 1
    assert result.outcome == "awaiting_approval"
    assert (result.proposals, result.accepted, result.refused) == (3, 3, 0)
    assert len(submit.calls) == 3


def test_the_sprint_checkpoints_each_step_under_its_own_name_in_order(
    tmp_path: Path, settings: Settings
) -> None:
    memory = FileCheckpointer(tmp_path / "memory")
    sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script()),
        submit=StubSubmit(tmp_path),
        checkpointer=memory,
    )
    finished = [step for step, _ in memory.history(SPRINT) if step in STEPS]
    assert finished == list(STEPS)


def test_the_steps_are_the_seven_of_the_framework_in_order() -> None:
    assert STEPS == ("supervise", "research", "configure", "gate", "run", "critique", "report")


# --- the canary --------------------------------------------------------------------------------


def test_the_canary_is_refused_in_the_normal_case_by_its_own_code(
    tmp_path: Path, settings: Settings
) -> None:
    result = sprint(
        tmp_path, settings, completer=FakeCompleter(script()), submit=StubSubmit(tmp_path)
    )
    assert result.canary_refused is True

    slot, which = canary_placement(SPRINT, PROPOSALS_PER_SPRINT)
    (refused,) = log_of(tmp_path, "refused")
    assert refused["canary"] is True
    assert refused["proposal_id"] == f"{SPRINT}-{slot:02d}"
    with pytest.raises(ProposalRefused, match=refused["code"]) as expected:
        validate(CANARY_PAYLOADS[which], sprint_id=SPRINT, index=slot, universe=("SPY",))
    assert expected.value.code == RefusalCode(refused["code"])


def test_the_canary_replaces_the_configurators_raw_payload_before_the_gate_sees_it(
    tmp_path: Path, settings: Settings
) -> None:
    memory = FileCheckpointer(tmp_path / "memory")
    sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script()),
        submit=StubSubmit(tmp_path),
        checkpointer=memory,
    )
    slot, which = canary_placement(SPRINT, PROPOSALS_PER_SPRINT)
    state = dict(memory.history(SPRINT))["configure"]
    assert json.loads(json.dumps(state["raw"][slot])) == json.loads(
        json.dumps(CANARY_PAYLOADS[which])
    )
    # What the model really said is kept beside it, and is the legal payload it emitted.
    assert state["emitted"][slot] == json.loads(valid_reply(slot))
    assert state["canary"] == {"index": slot, "payload_index": which}
    # Every other slot is the model's own emission, untouched.
    assert [r for i, r in enumerate(state["raw"]) if i != slot] == [
        r for i, r in enumerate(state["emitted"]) if i != slot
    ]


def test_the_canary_placement_is_deterministic_and_spreads_over_every_slot_and_payload() -> None:
    assert canary_placement(SPRINT, 4) == canary_placement(SPRINT, 4)
    placements = [canary_placement(f"2026-11-10-s{n}", 4) for n in range(400)]
    assert {slot for slot, _ in placements} == {0, 1, 2, 3}
    assert {which for _, which in placements} == set(range(len(CANARY_PAYLOADS)))
    assert all(0 <= slot < 4 for slot, _ in placements)


def test_exactly_one_canary_event_per_sprint_for_every_slot_the_hash_can_choose(
    tmp_path: Path, settings: Settings
) -> None:
    for n in range(12):
        sprint_id = f"2026-11-10-s{n}"
        root = tmp_path / f"run{n}"
        local = Settings(
            universe=settings.universe,
            exogenous=(),
            calendar="XNYS",
            splits=settings.splits,
            storage_root=root,
        )
        result = run_sprint(
            sprint_id=sprint_id,
            settings=local,
            completer=FakeCompleter(script()),
            checkpointer=FileCheckpointer(root / "memory"),
            log_path=root / "log.jsonl",
            submit=StubSubmit(root),
            at=AT,
            sha=SHA,
            dirty=False,
        )
        found = events(root / "log.jsonl", sprint_id)
        assert [e["kind"] for e in found].count("canary") == 1
        assert result.canary_refused is True


def test_a_gate_that_accepts_everything_voids_the_sprint_and_promotes_nothing(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def accept_everything(
        raw: object, *, sprint_id: str, index: int, universe: tuple[str, ...], canary: bool
    ) -> GateVerdict:
        proposal = Proposal(
            f"{sprint_id}-{index:02d}", "harx", (VOCABULARY[0],), "SPY", 5, ("har_levels",)
        )
        return GateVerdict(True, proposal, "accepted", None, canary)

    monkeypatch.setattr(agent, "gate", accept_everything)
    submit = StubSubmit(tmp_path)
    result = sprint(tmp_path, settings, completer=FakeCompleter(script()), submit=submit)

    assert result.outcome == "voided_by_canary"
    assert result.pending is None
    assert result.canary_refused is False
    assert submit.calls == []
    kinds = [e["kind"] for e in log_of(tmp_path)]
    assert kinds.count("canary_accepted") == 1
    assert "awaiting_approval" not in kinds
    assert kinds[-1] == "complete"
    assert log_of(tmp_path, "complete")[0]["outcome"] == "voided_by_canary"
    assert pending_approvals(tmp_path / "data" / "agent_sprints.jsonl") == ()


def test_a_voided_sprint_stops_gating_at_the_accepted_canary_and_asks_nobody_further(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    def accept_everything(
        raw: object, *, sprint_id: str, index: int, universe: tuple[str, ...], canary: bool
    ) -> GateVerdict:
        seen.append(index)
        proposal = Proposal(
            f"{sprint_id}-{index:02d}", "harx", (VOCABULARY[0],), "SPY", 5, ("har_levels",)
        )
        return GateVerdict(True, proposal, "accepted", None, canary)

    monkeypatch.setattr(agent, "gate", accept_everything)
    completer = FakeCompleter(script())
    sprint(tmp_path, settings, completer=completer, submit=StubSubmit(tmp_path))
    slot, _ = canary_placement(SPRINT, PROPOSALS_PER_SPRINT)
    assert seen == list(range(slot + 1))
    # Researcher and the four Configurator calls were made; the Critic and Reporter never were.
    assert len(completer.prompts) == 1 + PROPOSALS_PER_SPRINT


# --- the Critic gates nothing ------------------------------------------------------------------


def run_with_critic(
    tmp_path: Path, settings: Settings, critic: str
) -> tuple[SprintResult, StubSubmit]:
    submit = StubSubmit(tmp_path)
    result = sprint(
        tmp_path, settings, completer=FakeCompleter(script(critic=critic)), submit=submit
    )
    return result, submit


def test_a_critic_rejection_does_not_prevent_a_run(tmp_path: Path, settings: Settings) -> None:
    rejected, rejecting = run_with_critic(
        tmp_path / "a", settings, "REJECT: the configurations do not follow from the hypothesis."
    )
    approved, approving = run_with_critic(tmp_path / "b", settings, "APPROVE: they follow.")
    assert [p.proposal_id for p in rejecting.calls] == [p.proposal_id for p in approving.calls]
    assert len(rejecting.calls) == 3
    assert rejected.outcome == approved.outcome == "awaiting_approval"
    assert rejected.pending is not None
    assert approved.pending is not None
    assert rejected.pending.proposal_id == approved.pending.proposal_id


def test_a_critic_approval_does_not_cause_a_run_the_gate_refused(
    tmp_path: Path, settings: Settings
) -> None:
    garbage = ["not json at all"] * PROPOSALS_PER_SPRINT
    submit = StubSubmit(tmp_path)
    result = sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script(configurator=garbage, critic="APPROVE everything, truly.")),
        submit=submit,
    )
    assert submit.calls == []
    assert result.outcome == "no_candidate"
    assert (result.proposals, result.accepted, result.refused) == (3, 0, 3)
    assert result.canary_refused is True
    codes = {e["code"] for e in log_of(tmp_path, "refused") if not e["canary"]}
    assert codes == {RefusalCode.NOT_A_MAPPING.value}


def test_a_critic_approval_does_not_make_an_unpromotable_candidate_promotable(
    tmp_path: Path, settings: Settings
) -> None:
    submit = StubSubmit(tmp_path, member=False)
    result = sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script(critic="APPROVE: ship it.")),
        submit=submit,
    )
    assert result.outcome == "no_candidate"
    assert result.pending is None
    assert "not in the qlike MCS" in result.reason


def test_the_critics_text_is_logged_as_a_report_that_says_it_gates_nothing(
    tmp_path: Path, settings: Settings
) -> None:
    sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script(critic="REJECT: thin evidence.")),
        submit=StubSubmit(tmp_path),
    )
    (critique,) = [e for e in log_of(tmp_path, "report") if e["role"] == "critic"]
    assert critique["verdict"] == "reject"
    assert critique["gates"] is False
    assert critique["text"] == "REJECT: thin evidence."


def test_an_unparseable_critic_reply_is_recorded_as_unparsed_and_changes_nothing(
    tmp_path: Path, settings: Settings
) -> None:
    result, _ = run_with_critic(tmp_path, settings, "Hmm, hard to say.")
    (critique,) = [e for e in log_of(tmp_path, "report") if e["role"] == "critic"]
    assert critique["verdict"] == "unparsed"
    assert result.outcome == "awaiting_approval"


# --- the pause, not a promotion ----------------------------------------------------------------


def test_a_promotable_candidate_persists_a_pending_approval_and_promotes_nothing(
    tmp_path: Path, settings: Settings
) -> None:
    result = sprint(
        tmp_path, settings, completer=FakeCompleter(script()), submit=StubSubmit(tmp_path)
    )
    log = tmp_path / "data" / "agent_sprints.jsonl"
    assert result.outcome == "awaiting_approval"
    assert "promoted" not in [e["kind"] for e in log_of(tmp_path)]
    pending = pending_approvals(log)
    assert [p.proposal_id for p in pending] == [
        f"{SPRINT}-{i:02d}" for i in range(4) if i != canary_placement(SPRINT, 4)[0]
    ]
    assert result.pending == pending[0]


def test_the_awaiting_event_carries_the_proposal_id_and_a_fingerprint_that_recomputes(
    tmp_path: Path, settings: Settings
) -> None:
    result = sprint(
        tmp_path, settings, completer=FakeCompleter(script()), submit=StubSubmit(tmp_path)
    )
    for event in log_of(tmp_path, "awaiting_approval"):
        assert event["proposal_id"].startswith(SPRINT)
        assert event["fingerprint"] == fingerprint_of(event["proposal"], event["evidence"])
        assert event["git_sha"] == SHA
        assert event["git_dirty"] is False
        assert len(event["evidence"]) == 1
    assert result.pending is not None
    assert result.pending.fingerprint == fingerprint_of(
        result.pending.proposal, result.pending.evidence
    )


def test_the_evidence_is_the_hash_of_the_cohorts_own_ledger_lines(
    tmp_path: Path, settings: Settings
) -> None:
    result = sprint(
        tmp_path, settings, completer=FakeCompleter(script()), submit=StubSubmit(tmp_path)
    )
    assert result.pending is not None
    ledger = tmp_path / "data" / "agent_trials.jsonl"
    lines = [json.loads(x) for x in ledger.read_text().splitlines()]
    mine = [x for x in lines if x["cohort"] == result.pending.cohort]
    assert result.pending.evidence == tuple(evidence_hash(x) for x in mine)
    assert len(mine) == 1 and len(lines) == 3


def test_one_sprint_may_raise_several_candidates_and_clearing_one_clears_only_that_one(
    tmp_path: Path, settings: Settings
) -> None:
    sprint(tmp_path, settings, completer=FakeCompleter(script()), submit=StubSubmit(tmp_path))
    log = tmp_path / "data" / "agent_sprints.jsonl"
    before = pending_approvals(log)
    assert len(before) == 3
    record_event(log, sprint_id=SPRINT, kind="promoted", proposal_id=before[0].proposal_id)
    after = pending_approvals(log)
    assert [p.proposal_id for p in after] == [p.proposal_id for p in before[1:]]


def test_an_unreachable_git_state_is_carried_to_the_approval_as_none_never_as_clean(
    tmp_path: Path, settings: Settings
) -> None:
    result = sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script()),
        submit=StubSubmit(tmp_path),
        dirty=None,
    )
    assert result.pending is not None
    assert result.pending.git_dirty is None
    assert log_of(tmp_path, "start")[0]["git_dirty"] is None


def test_a_candidate_with_no_ledger_line_to_rest_on_is_not_promotable(
    tmp_path: Path, settings: Settings
) -> None:
    result = sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script()),
        submit=StubSubmit(tmp_path, ledger_lines=False),
    )
    assert result.outcome == "no_candidate"
    assert "holds no line of that cohort" in result.reason


def test_a_child_coverage_refusal_is_logged_as_a_refusal_and_is_not_a_failure(
    tmp_path: Path, settings: Settings
) -> None:
    result = sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script()),
        submit=StubSubmit(tmp_path, verdict="coverage_refused"),
    )
    assert result.outcome == "no_candidate"
    child_refusals = [e for e in log_of(tmp_path, "refused") if e.get("source") == "candidate"]
    assert len(child_refusals) == 3
    assert child_refusals[0]["reason"] == "iv_h1 has no coverage"


@pytest.mark.parametrize("verdict", ["crashed", "disagrees"])
def test_a_crashed_or_disagreeing_child_makes_the_sprint_failed_not_promotable(
    tmp_path: Path, settings: Settings, verdict: str
) -> None:
    result = sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script()),
        submit=StubSubmit(tmp_path, verdict=verdict),
    )
    assert result.outcome == "failed"
    assert result.pending is None
    assert verdict in result.reason


def test_a_candidate_is_not_buried_by_a_harness_failure_beside_it(
    tmp_path: Path, settings: Settings
) -> None:
    class Mixed(StubSubmit):
        def __call__(self, proposal: Proposal) -> ChildRun:
            self.verdict = "evaluated" if not self.calls else "crashed"
            return super().__call__(proposal)

    result = sprint(tmp_path, settings, completer=FakeCompleter(script()), submit=Mixed(tmp_path))
    assert result.outcome == "awaiting_approval"
    assert result.pending is not None
    assert "harness failures alongside" in result.reason
    assert "crashed" in result.reason


def test_a_proposal_event_carries_the_childs_own_join_key(
    tmp_path: Path, settings: Settings
) -> None:
    sprint(tmp_path, settings, completer=FakeCompleter(script()), submit=StubSubmit(tmp_path))
    for event in log_of(tmp_path, "proposal"):
        assert event["at"] == "2026-11-10T06:00:01+00:00"
        assert event["git_sha"] == "childsha"
        assert event["cohort"].startswith("agent-2026-11-10-")
        assert event["cohort"].endswith(event["proposal_id"][-2:])


# --- resumption --------------------------------------------------------------------------------


def test_a_sprint_that_dies_mid_run_resumes_at_the_unrun_proposal_without_asking_a_model_again(
    tmp_path: Path, settings: Settings
) -> None:
    memory = FileCheckpointer(tmp_path / "memory")
    completer = FakeCompleter(script())
    dying = StubSubmit(tmp_path, fail_on_call=1)
    with pytest.raises(RuntimeError, match="told to die"):
        sprint(tmp_path, settings, completer=completer, submit=dying, checkpointer=memory)
    assert len(dying.calls) == 1
    step, _ = memory.history(SPRINT)[-1]
    assert step == "run:progress"
    asked_before = len(completer.prompts)
    assert asked_before == 1 + PROPOSALS_PER_SPRINT
    assert log_of(tmp_path, "complete") == []

    resumed = StubSubmit(tmp_path)
    result = sprint(tmp_path, settings, completer=completer, submit=resumed, checkpointer=memory)

    assert result.outcome == "awaiting_approval"
    already = dying.calls[0].proposal_id
    assert already not in [p.proposal_id for p in resumed.calls]
    assert len(resumed.calls) == 2
    assert len(completer.prompts) == asked_before + 2  # only the Critic and the Reporter
    kinds = [e["kind"] for e in log_of(tmp_path)]
    assert kinds.count("start") == 1
    assert sorted(e["proposal_id"] for e in log_of(tmp_path, "proposal")) == sorted(
        [already, *[p.proposal_id for p in resumed.calls]]
    )
    assert kinds.count("canary") == 1


def test_a_sprint_resumes_at_the_step_after_the_last_one_recorded(
    tmp_path: Path, settings: Settings
) -> None:
    memory = FileCheckpointer(tmp_path / "memory")
    submit = StubSubmit(tmp_path)
    short = FakeCompleter(script()[: 1 + PROPOSALS_PER_SPRINT])  # no Critic reply: dies there
    with pytest.raises(RuntimeError, match="exhausted"):
        sprint(tmp_path, settings, completer=short, submit=submit, checkpointer=memory)
    assert memory.get(SPRINT) is not None
    assert memory.get(SPRINT)[0] == "run"  # type: ignore[index]
    submitted = len(submit.calls)

    rest = FakeCompleter(script()[1 + PROPOSALS_PER_SPRINT :])
    result = sprint(tmp_path, settings, completer=rest, submit=submit, checkpointer=memory)
    assert result.outcome == "awaiting_approval"
    assert len(submit.calls) == submitted  # nothing was submitted a second time
    assert rest.prompts[0].system.startswith("You are the critic")


def test_a_finished_sprint_is_not_run_again_under_the_same_id(
    tmp_path: Path, settings: Settings
) -> None:
    sprint(tmp_path, settings, completer=FakeCompleter(script()), submit=StubSubmit(tmp_path))
    with pytest.raises(SprintAlreadyComplete):
        sprint(tmp_path, settings, completer=FakeCompleter(script()), submit=StubSubmit(tmp_path))


def test_the_researcher_is_shown_what_an_earlier_sprint_hypothesised(
    tmp_path: Path, settings: Settings
) -> None:
    memory = FileCheckpointer(tmp_path / "memory")
    sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script()),
        submit=StubSubmit(tmp_path),
        checkpointer=memory,
    )
    second = FakeCompleter(script())
    sprint(
        tmp_path,
        settings,
        completer=second,
        submit=StubSubmit(tmp_path),
        checkpointer=memory,
        sprint_id="2026-11-17-weekly",
    )
    assert "Extra regressors may help a HAR model." in second.prompts[0].user
    assert "2026-11-10-weekly" in second.prompts[0].user


def test_a_supervisor_that_stops_runs_no_gate_and_asks_no_model(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "list_models", lambda: ())
    completer = FakeCompleter(script())
    submit = StubSubmit(tmp_path)
    result = sprint(tmp_path, settings, completer=completer, submit=submit)
    assert result.outcome == "no_candidate"
    assert "supervisor stopped" in result.reason
    assert completer.prompts == ()
    assert submit.calls == []
    assert "canary" not in [e["kind"] for e in log_of(tmp_path)]
    assert log_of(tmp_path)[-1]["kind"] == "complete"


# --- never reads a panel, never promotes -------------------------------------------------------


def _names_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.rpartition(".")[2])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.update(node.module.split("."))
    return found


@pytest.mark.parametrize("module", sorted(SRC.glob("*.py")), ids=lambda p: p.name)
def test_no_agent_module_names_load_panel_or_imports_promote_or_the_child(module: Path) -> None:
    names = _names_in(module)
    assert "load_panel" not in names
    assert "promote" not in names
    assert "candidate" not in names


def test_the_scan_that_says_so_is_not_vacuous(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text("from volab.store import load_panel\nload_panel('x')\n")
    assert "load_panel" in _names_in(planted)
    assert len(list(SRC.glob("*.py"))) >= 8


# --- hub and spoke -----------------------------------------------------------------------------


def _role_calls() -> dict[str, set[str]]:
    tree = ast.parse((SRC / "roles.py").read_text(encoding="utf-8"))
    calls: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ROLES:
            calls[node.name] = {
                c.func.id
                for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            }
    return calls


def test_there_are_six_roles_and_each_is_a_function_in_the_roles_module() -> None:
    assert ROLES == ("supervisor", "researcher", "configurator", "runner", "critic", "reporter")
    assert set(_role_calls()) == set(ROLES)


def test_no_role_calls_another_role() -> None:
    for role, called in _role_calls().items():
        assert not called & (set(ROLES) - {role}), f"{role} calls {called & set(ROLES)}"


def test_the_role_scan_bites_on_a_role_that_calls_another() -> None:
    tree = ast.parse("def critic(s):\n    return reporter(s)\n")
    (node,) = tree.body
    assert isinstance(node, ast.FunctionDef)
    called = {
        c.func.id
        for c in ast.walk(node)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }
    assert called & set(ROLES)


def test_the_roles_module_does_not_import_the_hub_that_calls_it() -> None:
    tree = ast.parse((SRC / "roles.py").read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "volab.agent" not in imported
    assert roles_module.ROLES is ROLES


def test_the_model_families_are_what_validate_accepts() -> None:
    assert list_models() == MODEL_FAMILIES
    for family in MODEL_FAMILIES:
        raw = json.loads(valid_reply(0))
        raw["family"] = family
        validate(raw, sprint_id=SPRINT, index=0, universe=("SPY",))
    with pytest.raises(ProposalRefused, match="unknown_family"):
        validate(
            {**json.loads(valid_reply(0)), "family": "garch"},
            sprint_id=SPRINT,
            index=0,
            universe=("SPY",),
        )


def test_the_supervisor_stops_exactly_when_there_is_nothing_to_propose_against() -> None:
    assert supervisor({}, models=())["supervisor"]["action"] == "stop"
    assert supervisor({}, models=("harx",))["supervisor"]["action"] == "add_features"


def test_a_configurator_reply_that_is_not_json_reaches_the_gate_as_the_text_it_was() -> None:
    assert parse_payload('{"a": 1}') == {"a": 1}
    assert parse_payload('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_payload("sure, here you go") == "sure, here you go"


# --- the report's one possible validation ------------------------------------------------------


def test_the_digit_check_rejects_an_invented_figure_and_passes_a_faithful_report() -> None:
    figures = "gate: 3 accepted, 0 refused\nagent ledger: 12 trials evaluated"
    assert invented_numbers("3 proposals passed and 12 trials exist", figures) == ()
    assert invented_numbers("The candidate gained 47 basis points", figures) == ("47",)
    assert invented_numbers("12 12 99 99 7", figures) == ("99", "7")
    # a decimal and a thousands separator are single literals, on both sides
    assert invented_numbers("the loss was 0.5", "loss 0.5") == ()
    assert invented_numbers("the loss was 0.5", "loss 5") == ("0.5",)


def test_the_digit_check_does_not_cover_spelled_numbers_or_emphasis() -> None:
    figures = "agent ledger: 12 trials evaluated"
    assert invented_numbers("forty trials, and a spectacular result", figures) == ()


def test_a_report_inventing_a_figure_is_not_filed_as_the_report(
    tmp_path: Path, settings: Settings
) -> None:
    sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script(report="The best candidate cut QLIKE by 31 percent.")),
        submit=StubSubmit(tmp_path),
    )
    (report,) = [e for e in log_of(tmp_path, "report") if e["role"] == "reporter"]
    assert report["validated"] is False
    assert report["invented"] == ["31"]
    assert report["draft"] == "The best candidate cut QLIKE by 31 percent."
    assert report["text"] == report["figures"]


def test_a_report_quoting_only_the_figures_it_was_given_is_filed(
    tmp_path: Path, settings: Settings
) -> None:
    sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script(report="The gate refused the canary; 3 were accepted.")),
        submit=StubSubmit(tmp_path),
    )
    (report,) = [e for e in log_of(tmp_path, "report") if e["role"] == "reporter"]
    assert report["validated"] is True
    assert report["text"] == report["draft"]
    assert "agent ledger (volab.agent.ledger" in report["figures"]
    assert "gate (volab.agent.vocabulary.gate)" in report["figures"]


# --- nothing computed over data past dev_end reaches a model -----------------------------------


def _plant_lines_past_dev_end(root: Path, count: int) -> None:
    ledger = root / "data" / "agent_trials.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        for n in range(count):
            line = {
                "at": "2027-01-05T00:00:00+00:00",
                "cohort": "agent-planted-cohort-zzq",
                "model": f"planted-model-{n}-qzx",
                "last_origin": "2026-03-02",
            }
            handle.write(json.dumps(line) + "\n")


def test_a_ledger_line_past_dev_end_fails_the_sprint_and_reaches_no_prompt(
    tmp_path: Path, settings: Settings
) -> None:
    _plant_lines_past_dev_end(tmp_path, 7)
    completer = FakeCompleter(script())
    result = sprint(tmp_path, settings, completer=completer, submit=StubSubmit(tmp_path))

    assert result.outcome == "failed"
    assert result.pending is None
    assert "past dev_end" in result.reason
    anomalies = [e for e in log_of(tmp_path, "report") if e.get("role") == "integrity"]
    assert len(anomalies) == 1
    assert anomalies[0]["excluded"] == 7  # the operator is told the count
    assert log_of(tmp_path)[-1]["kind"] == "complete"

    seen = "\n".join(f"{p.system}\n{p.user}" for p in completer.prompts)
    for trace in ("planted", "qzx", "zzq", "2026-03-02", "excluded", "dropped"):
        assert trace not in seen
    assert not re.search(r"(?<![\w.])7(?![\w.])", seen)


def test_a_clean_ledger_gives_the_models_the_kept_count_and_no_mention_of_exclusion(
    tmp_path: Path, settings: Settings
) -> None:
    completer = FakeCompleter(script())
    sprint(tmp_path, settings, completer=completer, submit=StubSubmit(tmp_path))
    seen = "\n".join(f"{p.system}\n{p.user}" for p in completer.prompts)
    assert "excluded" not in seen
    assert "trials evaluated before this sprint" in seen
