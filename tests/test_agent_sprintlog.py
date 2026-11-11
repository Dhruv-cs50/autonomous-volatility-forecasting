"""The sprint event log and the pending approval (P5-C2, R183, R188).

What these tests are for. The log is the only account of what a sprint did, and it is gitignored,
so what it must guarantee is narrow and each guarantee gets its own witness: the start line is on
disk before anything else happens, the two halves of a sprint are separate lines joined by an id,
a torn final line does not make the log unreadable, an unknown event kind is refused rather than
recorded where nobody will look, and a ``proposal`` event carries the ``(cohort, at, git_sha)``
key by which the agent trial ledger's lines are found.

Every test writes under ``tmp_path``; none touches the repository's ``data/``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

import pytest

import volab.agent.sprintlog as sprintlog
from volab.agent.sprintlog import (
    EVENT_KINDS,
    SPRINT_LOG,
    PendingApproval,
    events,
    fingerprint_of,
    pending_approvals,
    record_completion,
    record_event,
    record_start,
    sprint_ids,
    sprint_log_path,
)

_AT = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.UTC)
_LATER = dt.datetime(2026, 11, 10, 9, 5, tzinfo=dt.UTC)
_PROPOSAL: dict[str, Any] = {"cohort": "agent-x", "horizon": 5, "extras": ["a", "b"]}
_EVIDENCE = ("h2", "h1", "h3")


def _start(path: Path, sprint_id: str, at: dt.datetime = _AT) -> None:
    record_start(path, sprint_id=sprint_id, at=at, sha="abc123", dirty=False)


def _awaiting(
    path: Path,
    sprint_id: str = "s1",
    proposal_id: str = "p1",
    *,
    dirty: bool | None = False,
) -> None:
    record_event(
        path,
        sprint_id=sprint_id,
        kind="awaiting_approval",
        proposal_id=proposal_id,
        cohort="agent-x",
        proposal=_PROPOSAL,
        created_at=_LATER,
        git_sha="abc123",
        git_dirty=dirty,
        evidence=list(_EVIDENCE),
        fingerprint=fingerprint_of(_PROPOSAL, _EVIDENCE),
    )


def test_the_log_path_is_the_gitignored_sprint_log_under_the_given_root(tmp_path: Path) -> None:
    assert Path("data") / "agent_sprints.jsonl" == SPRINT_LOG
    assert sprint_log_path(tmp_path) == tmp_path / "data" / "agent_sprints.jsonl"


def test_an_absent_log_yields_empty_results_rather_than_raising(tmp_path: Path) -> None:
    path = sprint_log_path(tmp_path)

    assert events(path) == ()
    assert sprint_ids(path) == ()
    assert pending_approvals(path) == ()


def test_the_start_line_is_on_disk_with_no_completion_after_a_simulated_crash(
    tmp_path: Path,
) -> None:
    """A sprint that is killed after its start still leaves a visible line, and only that line."""
    path = sprint_log_path(tmp_path)

    _start(path, "s1")  # ... and then the process dies: no completion is ever written.

    seen = events(path, "s1")
    assert [event["kind"] for event in seen] == ["start"]
    assert seen[0]["git_sha"] == "abc123"
    assert seen[0]["started_at"] == _AT.isoformat()
    assert sprint_ids(path) == ("s1",)


def test_the_start_and_completion_are_two_lines_and_the_start_is_left_untouched(
    tmp_path: Path,
) -> None:
    path = sprint_log_path(tmp_path)
    _start(path, "s1")
    before = path.read_text(encoding="utf-8")

    record_completion(path, sprint_id="s1", outcome="done", at=_LATER)

    after = path.read_text(encoding="utf-8")
    assert after.startswith(before)
    assert len(after.splitlines()) == 2
    completion = events(path, "s1")[1]
    assert completion["kind"] == "complete"
    assert completion["sprint_id"] == "s1"
    assert completion["outcome"] == "done"
    assert completion["ended_at"] == _LATER.isoformat()


def test_the_start_line_is_fsynced_and_not_merely_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``fsync`` is the only part of "on the disk" a unit test can observe, so it is counted.

    Closing the handle flushes it anyway, so every test that reads the file afterwards would stay
    green if the ``flush``/``fsync`` pair were deleted. Durability past a power cut is not tested.
    """
    synced: list[int] = []
    real_fsync = sprintlog.os.fsync
    monkeypatch.setattr(
        sprintlog.os,
        "fsync",
        lambda descriptor: (synced.append(descriptor), real_fsync(descriptor))[1],
    )
    path = sprint_log_path(tmp_path)

    _start(path, "s1")
    assert len(synced) == 1
    record_event(path, sprint_id="s1", kind="report", text="x")
    record_completion(path, sprint_id="s1", outcome="done", at=_LATER)

    assert len(synced) == 3


def test_a_dirty_of_none_is_recorded_as_null_and_never_as_clean(tmp_path: Path) -> None:
    path = sprint_log_path(tmp_path)

    record_start(path, sprint_id="s1", at=_AT, sha="abc123", dirty=None)

    start = events(path, "s1")[0]
    assert "git_dirty" in start
    assert start["git_dirty"] is None
    assert start["git_dirty"] is not False


def test_a_torn_final_line_is_skipped_and_the_preceding_lines_still_read(tmp_path: Path) -> None:
    path = sprint_log_path(tmp_path)
    _start(path, "s1")
    record_event(path, sprint_id="s1", kind="report", text="x")
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "complete", "sprint_id": "s1", "outc')  # killed mid-write

    seen = events(path)

    assert [event["kind"] for event in seen] == ["start", "report"]
    assert sprint_ids(path) == ("s1",)


def test_a_non_object_line_is_skipped_like_a_torn_one(tmp_path: Path) -> None:
    path = sprint_log_path(tmp_path)
    _start(path, "s1")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("[1, 2]\n\n")

    assert [event["kind"] for event in events(path)] == ["start"]


def test_events_filter_by_sprint_id_and_keep_file_order(tmp_path: Path) -> None:
    path = sprint_log_path(tmp_path)
    _start(path, "s1")
    _start(path, "s2", _LATER)
    record_event(path, sprint_id="s1", kind="report", text="first")
    record_event(path, sprint_id="s2", kind="report", text="other")
    record_event(path, sprint_id="s1", kind="report", text="second")

    only_s1 = events(path, "s1")

    assert [event.get("text") for event in only_s1] == [None, "first", "second"]
    assert len(events(path)) == 5
    assert events(path, "nobody") == ()


def test_sprint_ids_are_most_recent_first(tmp_path: Path) -> None:
    path = sprint_log_path(tmp_path)
    _start(path, "old")
    _start(path, "middle", _LATER)
    _start(path, "new", _LATER + dt.timedelta(minutes=1))
    record_event(path, sprint_id="old", kind="report", text="late event, still the oldest sprint")

    assert sprint_ids(path) == ("new", "middle", "old")


def test_an_unknown_event_kind_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    path = sprint_log_path(tmp_path)
    _start(path, "s1")
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="propsal"):
        record_event(path, sprint_id="s1", kind="propsal")

    assert path.read_text(encoding="utf-8") == before


def test_every_declared_event_kind_is_accepted_by_record_event(tmp_path: Path) -> None:
    path = sprint_log_path(tmp_path)
    fields = {"cohort": "agent-x", "at": _AT, "git_sha": "abc123"}

    for kind in EVENT_KINDS:
        record_event(path, sprint_id="s1", kind=kind, **fields)

    assert [event["kind"] for event in events(path, "s1")] == list(EVENT_KINDS)


def test_a_proposal_event_carries_the_cohort_at_and_sha_that_find_its_trial_lines(
    tmp_path: Path,
) -> None:
    """The join key. Without it the agent would need a shadow count of its own trials."""
    path = sprint_log_path(tmp_path)
    _start(path, "s1")

    record_event(
        path,
        sprint_id="s1",
        kind="proposal",
        cohort="agent-x",
        at=_AT,
        git_sha="abc123",
        proposal=_PROPOSAL,
    )

    proposal = events(path, "s1")[1]
    assert proposal["cohort"] == "agent-x"
    assert proposal["at"] == _AT.isoformat()
    assert proposal["git_sha"] == "abc123"


@pytest.mark.parametrize("missing", ["cohort", "at", "git_sha"])
def test_a_proposal_event_missing_any_part_of_the_join_key_is_refused(
    tmp_path: Path, missing: str
) -> None:
    path = sprint_log_path(tmp_path)
    fields: dict[str, Any] = {"cohort": "agent-x", "at": _AT, "git_sha": "abc123"}
    del fields[missing]

    with pytest.raises(ValueError, match=missing):
        record_event(path, sprint_id="s1", kind="proposal", **fields)

    assert not path.exists()


def test_a_non_finite_number_is_refused_rather_than_written_as_a_non_json_token(
    tmp_path: Path,
) -> None:
    path = sprint_log_path(tmp_path)

    with pytest.raises(ValueError, match="JSON"):
        record_event(path, sprint_id="s1", kind="report", value=float("nan"))


def test_the_fingerprint_is_the_sha256_of_canonical_proposal_and_sorted_evidence() -> None:
    canonical = '{"cohort":"agent-x","extras":["a","b"],"horizon":5}'
    expected = hashlib.sha256((canonical + "h1\nh2\nh3").encode("utf-8")).hexdigest()

    assert fingerprint_of(_PROPOSAL, _EVIDENCE) == expected


def test_reordering_the_evidence_or_the_proposal_keys_does_not_change_the_fingerprint() -> None:
    reordered = {"extras": ["a", "b"], "horizon": 5, "cohort": "agent-x"}

    assert fingerprint_of(_PROPOSAL, ("h1", "h2", "h3")) == fingerprint_of(_PROPOSAL, _EVIDENCE)
    assert fingerprint_of(reordered, _EVIDENCE) == fingerprint_of(_PROPOSAL, _EVIDENCE)


def test_changing_any_proposal_field_or_any_evidence_line_changes_the_fingerprint() -> None:
    base = fingerprint_of(_PROPOSAL, _EVIDENCE)

    for key, value in (("cohort", "agent-y"), ("horizon", 22), ("extras", ["a"]), ("new", 1)):
        assert fingerprint_of({**_PROPOSAL, key: value}, _EVIDENCE) != base
    assert fingerprint_of(_PROPOSAL, ("h1", "h2", "h4")) != base
    assert fingerprint_of(_PROPOSAL, ("h1", "h2")) != base


def test_a_pending_approval_round_trips_every_field_through_the_log(tmp_path: Path) -> None:
    path = sprint_log_path(tmp_path)
    _start(path, "s1")
    _awaiting(path)

    (pending,) = pending_approvals(path)

    assert pending == PendingApproval(
        sprint_id="s1",
        proposal_id="p1",
        cohort="agent-x",
        proposal=_PROPOSAL,
        created_at=_LATER,
        git_sha="abc123",
        git_dirty=False,
        evidence=_EVIDENCE,
        fingerprint=fingerprint_of(_PROPOSAL, _EVIDENCE),
    )


def test_a_pending_approval_keeps_unreachable_git_as_none_and_not_as_clean(
    tmp_path: Path,
) -> None:
    path = sprint_log_path(tmp_path)
    _awaiting(path, dirty=None)

    (pending,) = pending_approvals(path)

    assert pending.git_dirty is None


def test_pending_approvals_exclude_sprints_with_no_awaiting_event_and_promoted_proposals(
    tmp_path: Path,
) -> None:
    path = sprint_log_path(tmp_path)
    _start(path, "no-candidate")
    record_event(path, sprint_id="no-candidate", kind="refused", reason="gate")
    _start(path, "waiting", _LATER)
    _awaiting(path, "waiting", "p-waiting")
    _start(path, "done", _LATER)
    _awaiting(path, "done", "p-done")
    record_event(path, sprint_id="done", kind="promoted", proposal_id="p-done")

    pending = pending_approvals(path)

    assert [approval.proposal_id for approval in pending] == ["p-waiting"]


def test_an_awaiting_event_missing_a_field_is_skipped_and_does_not_hide_the_others(
    tmp_path: Path,
) -> None:
    path = sprint_log_path(tmp_path)
    record_event(path, sprint_id="bad", kind="awaiting_approval", proposal_id="p-bad")
    _awaiting(path, "s1", "p1")

    assert [approval.proposal_id for approval in pending_approvals(path)] == ["p1"]


def test_this_module_never_writes_the_two_strings_a_scan_forbids_in_this_package() -> None:
    """The import fence is asserted by ``test_agent_discipline``; this is only the strings."""
    source = Path(sprintlog.__file__).read_text(encoding="utf-8")

    assert "allow_holdout" not in source
    assert "VOLAB_HOLDOUT_UNLOCK" not in source
