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
    REPEATED_PROPOSAL,
    VOCABULARY,
    GateVerdict,
    Proposal,
    RefusalCode,
    validate,
)
from volab.config import Settings, Splits
from volab.errors import ProposalRefused
from volab.promote import main as promote_main

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
        splits=Splits(
            dev_end=dt.date(2025, 12, 31),
            holdout_start=dt.date(2026, 1, 2),
            holdout_end=dt.date(2026, 10, 30),
        ),
        # The repository's real shape: ``storage.root`` is the ``data`` directory itself, and every
        # ledger path (``data/agent_trials.jsonl``) is joined to its *parent*. An earlier fixture
        # set ``storage_root=tmp_path``, which made a read from the wrong root land on the same
        # file as a write from the right one -- so the sprint read ``data/data/agent_trials.jsonl``
        # on the real configuration and no test could see it. See
        # ``test_the_sprint_reads_the_ledger_the_evaluation_child_writes``.
        storage_root=tmp_path / "data",
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
    ``member`` puts the candidate in the qlike MCS at 10%; ``baseline_member`` puts ``har_levels``
    there too. By default the candidate is in and ``har_levels`` is out -- the candidate beat it,
    which is what promotable means since R228 -- and a tie is asked for by name.
    """

    def __init__(
        self,
        root: Path,
        *,
        verdict: str = "evaluated",
        member: bool = True,
        baseline_member: bool = False,
        ledger_lines: bool = True,
        fail_on_call: int | None = None,
    ) -> None:
        self.root = root
        self.verdict = verdict
        self.member = member
        self.baseline_member = baseline_member
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
        # The 10% set is never empty: when the candidate is out, the reference is in.
        members = (
            [n for n in names if n != "har_levels" or self.baseline_member]
            if self.member
            else list(proposal.reference)
        )
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
        settings=dataclasses.replace(settings, storage_root=tmp_path / "data"),
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
            storage_root=root / "data",
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


@pytest.mark.parametrize(
    ("reply", "verdict"),
    [
        # The first live sprint (R224): Haiku opened with a bolded verdict, logged as unparsed.
        ("**REJECT** (as written; the spec is close)", "reject"),
        ("## APPROVE\nThey follow.", "approve"),
        ("*Approve*: they follow.", "approve"),
        ("`REJECT`", "reject"),
        ("**Hmm**, hard to say.", "unparsed"),
    ],
)
def test_a_critic_verdict_wrapped_in_markdown_is_still_read(
    tmp_path: Path, settings: Settings, reply: str, verdict: str
) -> None:
    result, _ = run_with_critic(tmp_path, settings, reply)
    (critique,) = [e for e in log_of(tmp_path, "report") if e["role"] == "critic"]
    assert critique["verdict"] == verdict
    assert critique["text"] == reply
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


def test_a_candidate_tied_with_har_levels_in_the_mcs_is_not_promotable(
    tmp_path: Path, settings: Settings
) -> None:
    """R228: both in the qlike MCS at 10% is a tie, and a tie does not beat the baseline."""
    result = sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script()),
        submit=StubSubmit(tmp_path, baseline_member=True),
    )
    assert result.outcome == "no_candidate"
    assert result.pending is None
    assert not log_of(tmp_path, "awaiting_approval")
    assert "tied with har_levels" in result.reason
    assert "not in the qlike MCS" not in result.reason


def test_a_candidate_in_the_mcs_without_har_levels_is_promotable(
    tmp_path: Path, settings: Settings
) -> None:
    """R228, the other direction: the candidate in the set and ``har_levels`` eliminated."""
    result = sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script()),
        submit=StubSubmit(tmp_path, member=True, baseline_member=False),
    )
    assert result.outcome == "awaiting_approval"
    assert result.pending is not None
    assert log_of(tmp_path, "awaiting_approval")


def test_an_mcs_whose_comparison_lacks_har_levels_is_not_reported_and_not_promotable() -> None:
    """R228 needs ``har_levels`` among the contestants; without it the answer is ``None``."""
    proposal = {"reference": ["har_levels_mz"]}
    summary = {
        "verdict": "evaluated",
        "comparisons": [
            {
                "loss": "qlike",
                "contestants": ["har_levels_mz", "harx+rv_d"],
                "refusal": None,
                "mcs_sizes": {"0.1": {"members": ["harx+rv_d"]}},
            }
        ],
    }
    assert agent._mcs_membership(summary, proposal) is None


@pytest.mark.parametrize(
    ("baseline_member", "said"),
    [(True, "member, tied with har_levels"), (False, "member, har_levels not a member")],
)
def test_the_reporters_figures_say_whether_har_levels_is_in_the_mcs(
    tmp_path: Path, settings: Settings, baseline_member: bool, said: str
) -> None:
    """R228: a tie reads as a tie in the figures, not a bare "member" a Reporter could oversell."""
    sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script()),
        submit=StubSubmit(tmp_path, baseline_member=baseline_member),
    )
    (report,) = [e for e in log_of(tmp_path, "report") if e["role"] == "reporter"]
    runs = [line for line in report["figures"].splitlines() if line.startswith("run ")]
    assert len(runs) == PROPOSALS_PER_SPRINT - 1
    assert all(line.endswith(f"qlike MCS at 10%: {said}") for line in runs), runs


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


def test_a_sprint_that_dies_between_the_approvals_and_the_completion_resumes_to_one_approval_each(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R214: a resumed ``finish`` must not write a second ``awaiting_approval`` for one id.

    ``volab.promote`` refuses an id with two such events, so a duplicate would strand the candidate
    until an operator edited the log. The crash is placed after the approvals and before the
    ``complete`` event by making the completion writer raise once.
    """
    memory = FileCheckpointer(tmp_path / "memory")
    completer = FakeCompleter(script())
    submit = StubSubmit(tmp_path)
    real_completion = agent.record_completion

    def die(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the process died before the complete event")

    monkeypatch.setattr(agent, "record_completion", die)
    with pytest.raises(RuntimeError, match="died before the complete"):
        sprint(tmp_path, settings, completer=completer, submit=submit, checkpointer=memory)
    assert log_of(tmp_path, "complete") == []
    assert log_of(tmp_path, "awaiting_approval")  # the window is open: approvals, no completion

    monkeypatch.setattr(agent, "record_completion", real_completion)
    result = sprint(tmp_path, settings, completer=completer, submit=submit, checkpointer=memory)

    assert result.outcome == "awaiting_approval"
    assert len(log_of(tmp_path, "complete")) == 1
    ids = [e["proposal_id"] for e in log_of(tmp_path, "awaiting_approval")]
    assert len(ids) == len(set(ids)) > 0
    assert promote_main(["--proposal-id", ids[0], "--root", str(tmp_path)], now=AT) == 0


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


def test_the_configurator_prompt_asks_for_json_arrays_and_its_example_passes_the_gate() -> None:
    """The first live sprint (R224): Haiku sent ``reference`` as a string, and the gate refused all
    three proposals. The prompt now names the JSON type of each list field and shows the reference
    shape, and that shown example is itself gate-valid, so the prompt cannot drift from the gate.
    """
    completer = FakeCompleter(["{}"] * PROPOSALS_PER_SPRINT)
    roles_module.configurator(
        {"hypothesis": "h"}, completer=completer, slots=PROPOSALS_PER_SPRINT, universe=("SPY",)
    )
    # Slots differ only by their number and the earlier slots' emissions, neither of which depends
    # on where the canary goes: see test_no_configurator_prompt_depends_on_where_the_canary_goes.
    lines = {line.split(":", 1)[0]: line for line in completer.prompts[0].user.splitlines()}
    assert "a JSON array" in lines["extra_regressors"]
    assert "a JSON array" in lines["reference"]
    assert "an integer" in lines["horizon"]
    example = json.loads(lines["reference"].split("e.g. ", 1)[1])
    assert isinstance(example, list)
    raw = {
        "family": MODEL_FAMILIES[0],
        "extra_regressors": [VOCABULARY[0]],
        "symbol": "SPY",
        "horizon": 1,
        "reference": example,
    }
    proposal = validate(raw, sprint_id=SPRINT, index=0, universe=("SPY",))
    assert list(proposal.reference) == example


def test_the_configurator_prompt_states_every_identity_groups_limit() -> None:
    """The first real-store sprint (R225) named three of one identity group in every slot. The
    prompt now states each group's limit, derived from the gate's own groups."""
    from volab.agent.vocabulary import _IDENTITY_GROUPS

    completer = FakeCompleter(["{}"] * PROPOSALS_PER_SPRINT)
    roles_module.configurator(
        {"hypothesis": "h"}, completer=completer, slots=PROPOSALS_PER_SPRINT, universe=("SPY",)
    )
    user = completer.prompts[0].user
    for members, allowed in _IDENTITY_GROUPS:
        assert f"at most {allowed} of {', '.join(members)}" in user


# --- the report's one possible validation ------------------------------------------------------


def test_the_digit_check_rejects_an_invented_figure_and_passes_a_faithful_report() -> None:
    figures = "gate: 3 accepted, 0 refused\nagent ledger: 12 trials evaluated"
    assert invented_numbers("3 proposals passed and 12 trials exist", figures) == ()
    assert invented_numbers("The candidate gained 47 basis points", figures) == ("47",)
    assert invented_numbers("12 12 99 99 7", figures) == ("99", "7")
    # a decimal and a thousands separator are single literals, on both sides
    assert invented_numbers("the loss was 0.5", "loss 0.5") == ()
    assert invented_numbers("the loss was 0.5", "loss 5") == ("0.5",)


def test_the_digit_check_exempts_digits_that_are_part_of_a_name() -> None:
    """R229: a digit run preceded by a letter or underscore, or inside a sprint/proposal id, is
    part of a name and is not a figure; a bare number beside such a name is still checked."""
    figures = "agent ledger: 12 trials evaluated"
    # a feature name, a horizon-tagged name, a FRED series id, a mid-run digit of d22
    assert invented_numbers("`semivar_neg_proxy_d1`, `rv_proxy_d66`, iv_h22, baa10y", figures) == ()
    assert invented_numbers("the d5 and d22 measures", figures) == ()
    # sprint ids: full, with a proposal suffix, and the short form
    assert invented_numbers("sprint 2026-11-13T054207Z", figures) == ()
    assert invented_numbers("run 2026-11-14T004243Z-02 was evaluated", figures) == ()
    assert invented_numbers("the 054207Z idea and (122201Z)", figures) == ()
    # an invented figure in the same sentence as names is still caught
    assert invented_numbers(
        "`rv_proxy_d66` from 054207Z gained 47 basis points over 12 trials", figures
    ) == ("47",)
    # a number with a unit after it is a figure, not a name
    assert invented_numbers("a 22% gain, or 5bp", figures) == ("22", "5")
    # six digits not shaped like an id, a negative decimal, a number opening a line
    assert invented_numbers("054207 trials", figures) == ("054207",)
    assert invented_numbers("the loss moved by -0.5", figures) == ("0.5",)
    assert invented_numbers("12 trials.\n47 basis points.", figures) == ("47",)


def test_the_digit_check_on_the_first_real_sprint_reports() -> None:
    """Snippets of the three real reports that failed on identifier digits (R229)."""
    # 2026-11-13T054207Z flagged ["1", "22", "66"]: all three were digits inside names.
    figures = (
        "sprint id (run_sprint): 2026-11-13T054207Z\n"
        "run 2026-11-13T054207Z-01 (volab.candidate): evaluated; "
        "cohort agent-2026-11-13-b33bf6e5-01; qlike MCS at 10%: not reported"
    )
    draft = (
        "**Sprint log: 2026-11-13T054207Z** ... `semivar_neg_proxy_d5`, `signed_jump_proxy_d5` "
        "... daily RV defined as `semivar_neg_proxy_d1 + semivar_pos_proxy_d1` ... Weekly and "
        "monthly terms come from the d5 and d22 measures. `rv_proxy_d66` is a candidate control."
    )
    assert invented_numbers(draft, figures) == ()

    # 2026-11-13T122201Z flagged ["22", "1", "5", "66", "054207", "02", "2"]. The names are
    # exempt; the horizons in prose, run -02 (absent from the figures) and the list marker
    # "2." are numbers the figures do not contain, so they stay flagged.
    figures = (
        "sprint id (run_sprint): 2026-11-13T122201Z\n"
        "proposal slots (run_sprint): 4, one of them the canary\n"
        "agent ledger (volab.agent.ledger, data/agent_trials.jsonl): 12 trials evaluated\n"
        "run 2026-11-13T122201Z-00 (volab.candidate): evaluated; "
        "cohort agent-2026-11-13-1731f8f3-00; qlike MCS at 10%: member\n"
        "run 2026-11-13T122201Z-01 (volab.candidate): evaluated\n"
        "run 2026-11-13T122201Z-03 (volab.candidate): evaluated"
    )
    draft = (
        "adding (`signed_jump_proxy_d22`) and (`iv_h22`) should improve 22-session-ahead "
        "forecasts ... (`semivar_neg_proxy_d1`), the weekly component (d5), the monthly "
        "component (d22), with `rv_proxy_d66` as a control ... without degrading the 1- and "
        "5-session forecasts. The hypothesis moves the 054207Z idea to the monthly scale. "
        "Run -00 (cohort agent-2026-11-13-1731f8f3-00) ... Run -02 does not appear.\n"
        "1. Fix the index symbol\n2. Check the ledger\n3. Reconcile\n4. Obtain the tables"
    )
    assert invented_numbers(draft, figures) == ("22", "1", "5", "02", "2")

    # 2026-11-14T004243Z flagged ["66", "1", "22", "054207", "122201", "2"]. The names and the
    # short sprint ids are exempt; h=66, h=1, 22 and the "(2)" marker stay flagged. ("5" was
    # never flagged here: the cohort hash ebf5f969 puts a 5 among the figures.)
    figures = (
        "sprint id (run_sprint): 2026-11-14T004243Z\n"
        "agent ledger (volab.agent.ledger, data/agent_trials.jsonl): 24 trials evaluated\n"
        "run 2026-11-14T004243Z-01 (volab.candidate): evaluated; "
        "cohort agent-2026-11-14-ebf5f969-01; qlike MCS at 10%: not reported"
    )
    draft = (
        "**Sprint 2026-11-14T004243Z** improves 66-session-ahead forecasts. The baseline uses "
        "(`semivar_neg_proxy_d1 + semivar_pos_proxy_d1`), weekly (d5), monthly (d22), and "
        "(`rv_proxy_d66`) components ... QLIKE at h=66 ... at h=1, 5, or 22. None of the 24 "
        "trials ... the d5 semivariance split (054207Z) and the d22 jump plus `iv_h22` idea "
        "(122201Z) ... - 2026-11-14T004243Z-01, cohort agent-2026-11-14-ebf5f969-01\n"
        "(1) check the 24 ledger trials; (2) reconcile the gate counts"
    )
    assert invented_numbers(draft, figures) == ("66", "1", "22", "2")


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


def test_a_ledger_line_past_dev_end_fails_the_sprint_before_any_model_is_asked(
    tmp_path: Path, settings: Settings
) -> None:
    """What this guarantees: a line past ``dev_end`` halts the sprint at ``research`` and the
    operator is told the count. The halt comes **before the first model call**, so ``prompts`` is
    empty and this test says nothing about what a prompt would have carried. It asserts that
    emptiness rather than checking absences against an empty string, which would pass whatever the
    roles did. What reaches a prompt is witnessed by
    ``test_no_field_of_a_childs_outcome_that_must_stay_out_of_a_prompt_reaches_one``.
    """
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
    assert completer.prompts == ()


class _PlantingSubmit(StubSubmit):
    """A child that, once it has run, leaves a line past ``dev_end`` on the ledger.

    That is the case ``research`` cannot see: the line appears *after* research read the ledger.
    """

    def __call__(self, proposal: Proposal) -> ChildRun:
        run = super().__call__(proposal)
        _plant_lines_past_dev_end(self.root, 7)
        return run


def test_a_line_past_dev_end_that_appears_after_research_fails_the_sprint_at_finish(
    tmp_path: Path, settings: Settings
) -> None:
    completer = FakeCompleter(script())
    result = sprint(tmp_path, settings, completer=completer, submit=_PlantingSubmit(tmp_path))

    assert result.outcome == "failed"
    assert result.pending is None
    assert "past dev_end" in result.reason
    assert not log_of(tmp_path, "awaiting_approval")
    anomalies = [e for e in log_of(tmp_path, "report") if e.get("role") == "integrity"]
    assert len(anomalies) == 1
    # Seven lines per child, three children; the operator is told, a prompt is not.
    assert anomalies[0]["excluded"] == 7 * 3
    assert log_of(tmp_path)[-1]["kind"] == "complete"

    seen = "\n".join(f"{p.system}\n{p.user}" for p in completer.prompts)
    for trace in ("planted", "qzx", "zzq", "2026-03-02", "excluded", "dropped", "integrity"):
        assert trace not in seen
    assert not re.search(r"(?<![\w.])(7|21)(?![\w.])", seen)


# Values that cannot occur in a prompt by accident: long, unlike any date, id or count the roles
# print, and each tied to the field it sits in.
SENTINEL_PID = 918273645
SENTINEL_TRIALS_WRITTEN = 7314159
SENTINEL_EXIT_CODE = 219
SENTINEL_SHA = "sha-sentinel-c0ffee77"
SENTINEL_AT = "1999-12-31T23:59:59+00:00"
SENTINEL_LOSS = "loss-sentinel-q9z"
SENTINEL_REFUSAL = "refusal-sentinel-k4m"
SENTINEL_TAIL = "tail-sentinel-w8x"
MUST_STAY_OUT: tuple[str, ...] = (
    str(SENTINEL_PID),
    str(SENTINEL_TRIALS_WRITTEN),
    str(SENTINEL_EXIT_CODE),
    SENTINEL_SHA,
    SENTINEL_AT,
    SENTINEL_LOSS,
    SENTINEL_REFUSAL,
    SENTINEL_TAIL,
)


class _SentinelSubmit(StubSubmit):
    """A child whose every field that must not reach a prompt carries a sentinel.

    Three proposals reach it: the first is ``evaluated``, the second ``coverage_refused`` (so
    ``reason`` is populated and *should* reach the Critic), the third dies with an exit code no
    verdict names and a sentinel output tail (the one case in which the Runner keeps the tail).
    """

    def __call__(self, proposal: Proposal) -> ChildRun:
        self.verdict = ("evaluated", "coverage_refused", "crashed")[len(self.calls)]
        run = super().__call__(proposal)
        if self.verdict == "crashed":
            return dataclasses.replace(run, exit_code=SENTINEL_EXIT_CODE, output_tail=SENTINEL_TAIL)
        assert run.result is not None
        result = dict(run.result)
        result.update(
            pid=SENTINEL_PID,
            trials_written=SENTINEL_TRIALS_WRITTEN,
            git_sha=SENTINEL_SHA,
            at=SENTINEL_AT,
            comparisons=[
                {**c, "loss": SENTINEL_LOSS, "refusal": SENTINEL_REFUSAL}
                for c in result["comparisons"]
            ],
        )
        return dataclasses.replace(run, pid=SENTINEL_PID, result=result, output_tail=SENTINEL_TAIL)


def test_no_field_of_a_childs_outcome_that_must_stay_out_of_a_prompt_reaches_one(
    tmp_path: Path, settings: Settings
) -> None:
    """R208's inventory, checked mechanically (R214). It held only because someone read the code.

    A whole sprint runs with sentinels in ``pid``, ``exit_code``, ``trials_written``, the stamps,
    the comparison records and ``output_tail``. No recorded prompt may contain one. Non-vacuity is
    asserted first and on purpose: the earlier planted-line test halted before any model call, so
    every absence it checked was true of an empty string. Here prompts exist for every role, and
    the fields that *should* reach a model are shown to.
    """
    ledger = tmp_path / "data" / "agent_trials.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        "".join(
            json.dumps({"cohort": f"agent-2025-06-0{n}-0123abcd-00", "last_origin": "2025-06-30"})
            + "\n"
            for n in range(1, 6)
        ),
        encoding="utf-8",
    )
    completer = FakeCompleter(script())
    submit = _SentinelSubmit(tmp_path)
    sprint(tmp_path, settings, completer=completer, submit=submit)

    # Non-vacuity. Every role that calls a model was asked, and the sentinels were really emitted.
    assert len(completer.prompts) == len(script())
    assert len(submit.calls) == 3
    seen = "\n".join(f"{p.system}\n{p.user}" for p in completer.prompts)
    critic_prompt = completer.prompts[-2].user
    # What should reach a model does: the hypothesis, the kept-line count, the child's reason and
    # each proposal's id and verdict name.
    assert "Extra regressors may help a HAR model." in seen
    assert "already holds 5 evaluated trial lines" in seen
    assert "Agent ledger lines before this sprint: 5" in critic_prompt
    assert "iv_h1 has no coverage" in critic_prompt
    for proposal, verdict in zip(
        submit.calls, ("evaluated", "coverage_refused", "unrecognised"), strict=True
    ):
        assert f"- {proposal.proposal_id}: {verdict}" in critic_prompt

    for sentinel in MUST_STAY_OUT:
        assert sentinel not in seen, sentinel


def test_a_clean_ledger_gives_the_models_the_kept_count_and_no_mention_of_exclusion(
    tmp_path: Path, settings: Settings
) -> None:
    completer = FakeCompleter(script())
    sprint(tmp_path, settings, completer=completer, submit=StubSubmit(tmp_path))
    seen = "\n".join(f"{p.system}\n{p.user}" for p in completer.prompts)
    assert "excluded" not in seen
    assert "trials evaluated before this sprint" in seen


def test_the_anomaly_leaves_no_trace_in_a_later_sprints_prompts(
    tmp_path: Path, settings: Settings
) -> None:
    memory = FileCheckpointer(tmp_path / "memory")
    _plant_lines_past_dev_end(tmp_path, 7)
    failed = sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script()),
        submit=StubSubmit(tmp_path),
        checkpointer=memory,
    )
    assert failed.outcome == "failed"
    (tmp_path / "data" / "agent_trials.jsonl").unlink()  # the operator repaired the ledger

    later = FakeCompleter(script())
    result = sprint(
        tmp_path,
        settings,
        completer=later,
        submit=StubSubmit(tmp_path),
        checkpointer=memory,
        sprint_id="2026-11-17-weekly",
    )
    assert result.outcome == "awaiting_approval"
    assert len(later.prompts) == len(script())
    seen = "\n".join(f"{p.system}\n{p.user}" for p in later.prompts)
    for trace in ("past dev_end", "integrity", "anomaly", "planted", "2026-11-10-weekly"):
        assert trace not in seen
    assert not re.search(r"(?<![\w.])7(?![\w.])", seen)
    assert "anomaly" not in json.dumps(memory.get("2026-11-17-weekly"))


def test_the_sprint_reads_the_ledger_the_evaluation_child_writes(settings: Settings) -> None:
    """The sprint's ledger read and the child's ledger write name the same file.

    ``volab.candidate.evaluate_proposal`` writes to ``settings.storage_root.parent /
    AGENT_TRIAL_LEDGER``, because ``storage.root`` is the ``data`` directory and the ledger path is
    already prefixed with ``data/``; ``volab.promote`` and ``volab.leaderboard`` join the same
    parent. The sprint once read from ``settings.storage_root`` instead, i.e.
    ``data/data/agent_trials.jsonl`` on the real configuration -- an empty file -- so it would have
    seen no trials, raised no candidate, and run its integrity check against nothing. Found by the
    human-gate story, not by this module's own tests, because the old fixture set
    ``storage_root=tmp_path`` and the two roots then coincided.

    This writes one line where the child writes it and asserts the sprint sees it. It reaches the
    sprint's read through ``_Sprint._ledger`` rather than a full ``run_sprint``, so a failure names
    the path and not a downstream symptom.
    """
    from volab.agent import _Sprint
    from volab.candidate import AGENT_TRIAL_LEDGER as CHILD_LEDGER

    written = settings.storage_root.parent / CHILD_LEDGER
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text(
        json.dumps({"cohort": "agent-2025-12-01-0123abcd-00", "last_origin": "2025-06-30"}) + "\n",
        encoding="utf-8",
    )
    sprint = _Sprint(
        sprint_id=SPRINT,
        settings=settings,
        completer=FakeCompleter([]),
        checkpointer=FileCheckpointer(settings.storage_root.parent / "ckpt"),
        log_path=settings.storage_root / "agent_sprints.jsonl",
        submit=lambda proposal: (_ for _ in ()).throw(AssertionError("no run expected")),
        at=AT,
        sha=SHA,
        dirty=False,
    )

    view = sprint._ledger()

    # The line the child wrote is the line the sprint read. ``TrialView.source`` holds the
    # *relative* ledger path, so it cannot tell two roots apart; the content can. Against the old
    # read from ``storage_root`` this saw zero lines: it opened ``data/data/agent_trials.jsonl``.
    assert view.lines == ({"cohort": "agent-2025-12-01-0123abcd-00", "last_origin": "2025-06-30"},)


# --- one configuration once per sprint (R227) --------------------------------------------------


def test_each_configurator_slot_lists_exactly_the_earlier_slots_mapping_emissions() -> None:
    """The second real-store sprint emitted one configuration in all three real slots, because
    every slot's prompt was the same. Slot k now lists what slots 0..k-1 emitted, as canonical
    JSON; a reply that is not a mapping is not listed, and slot 0 says ``none`` so every prompt
    has the same shape."""
    replies = [valid_reply(0), "not json", "{}", valid_reply(3)]
    completer = FakeCompleter(replies)
    roles_module.configurator(
        {"hypothesis": "h"}, completer=completer, slots=PROPOSALS_PER_SPRINT, universe=("SPY",)
    )
    listed = [
        next(line for line in p.user.splitlines() if line.startswith("Already proposed"))
        for p in completer.prompts
    ]
    canonical = [json.dumps(json.loads(r), sort_keys=True) for r in (valid_reply(0), "{}")]
    assert listed[0].endswith(": none")
    assert listed[1].endswith(": " + canonical[0])
    assert listed[2].endswith(": " + canonical[0])  # "not json" is not a mapping, so not listed
    assert listed[3].endswith(": " + "; ".join(canonical))


def test_no_configurator_prompt_depends_on_where_the_canary_goes(
    tmp_path: Path, settings: Settings
) -> None:
    """What the "same body" assertions stood for: nothing upstream of the gate can tell which
    slot the canary replaces. Two sprints whose canaries sit in different slots, run on one
    script, send byte-identical Configurator prompts."""
    ids = ("2026-11-10-repeat-a", "2026-11-10-repeat-c")
    assert (
        canary_placement(ids[0], PROPOSALS_PER_SPRINT)[0]
        != canary_placement(ids[1], PROPOSALS_PER_SPRINT)[0]
    )
    sent = []
    for n, sprint_id in enumerate(ids):
        completer = FakeCompleter(script())
        sprint(
            tmp_path / f"run{n}",
            settings,
            completer=completer,
            submit=StubSubmit(tmp_path / f"run{n}"),
            sprint_id=sprint_id,
        )
        sent.append(completer.prompts[1 : 1 + PROPOSALS_PER_SPRINT])
    assert sent[0] == sent[1]


def test_a_configuration_already_accepted_this_sprint_is_refused_and_never_run(
    tmp_path: Path, settings: Settings
) -> None:
    same = valid_reply(0)
    submit = StubSubmit(tmp_path)
    memory = FileCheckpointer(tmp_path / "memory")
    sprint(
        tmp_path,
        settings,
        completer=FakeCompleter(script(configurator=[same] * PROPOSALS_PER_SPRINT)),
        submit=submit,
        checkpointer=memory,
    )
    slot, _ = canary_placement(SPRINT, PROPOSALS_PER_SPRINT)
    real = [i for i in range(PROPOSALS_PER_SPRINT) if i != slot]
    first, repeats = f"{SPRINT}-{real[0]:02d}", [f"{SPRINT}-{i:02d}" for i in real[1:]]

    assert [p.proposal_id for p in submit.calls] == [first]
    refused = {e["proposal_id"]: e for e in log_of(tmp_path, "refused") if not e["canary"]}
    assert sorted(refused) == repeats
    for event in refused.values():
        assert event["code"] == REPEATED_PROPOSAL
        assert first in event["reason"]
    gated = dict(memory.history(SPRINT))["gate"]["gated"]
    assert [g["accepted"] for g in gated if not g["canary"]] == [True] + [False] * len(repeats)
    assert all(g["proposal"] is None for g in gated if g["code"] == REPEATED_PROPOSAL)
    lines = (tmp_path / "data" / "agent_trials.jsonl").read_text().splitlines()
    assert {json.loads(line)["cohort"] for line in lines} == {
        agent.agent_cohort_name(SPRINT, real[0])
    }


def test_the_repeat_rule_never_turns_an_accepted_canary_into_a_refusal(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The canary check runs first. A broken gate that accepts the canary must void the sprint
    even when the canary's configuration repeats one accepted in an earlier slot; otherwise the
    repeat rule would refuse it and the sprint would look healthy (R227)."""

    def accept_everything(
        raw: object, *, sprint_id: str, index: int, universe: tuple[str, ...], canary: bool
    ) -> GateVerdict:
        proposal = Proposal(
            f"{sprint_id}-{index:02d}", "harx", (VOCABULARY[0],), "SPY", 5, ("har_levels",)
        )
        return GateVerdict(True, proposal, "accepted", None, canary)

    slot, _ = canary_placement(SPRINT, PROPOSALS_PER_SPRINT)
    assert slot > 0  # an accepted real slot precedes the canary, with the same configuration
    monkeypatch.setattr(agent, "gate", accept_everything)
    result = sprint(
        tmp_path, settings, completer=FakeCompleter(script()), submit=StubSubmit(tmp_path)
    )

    assert result.outcome == "voided_by_canary"
    assert [e["index"] for e in log_of(tmp_path, "canary_accepted")] == [slot]
    assert not [e for e in log_of(tmp_path, "refused") if e["code"] == REPEATED_PROPOSAL]
