"""The Phase 5 exit criterion (P5-E3): a whole weekly sprint, never touching the sealed window.

The framework's criterion is *"one full weekly sprint runs end to end without touching the
holdout"*, and this module is the evidence for it. It runs ``volab.agent.run_sprint`` with a
scripted ``FakeCompleter``, a ``FileCheckpointer``, the sprint log, and an injected ``submit`` that
launches ``tests/isolated_candidate.py`` in a **fresh interpreter**; that shim calls
``volab.candidate.evaluate_proposal`` over a **synthetic store** and logs every ``load_panel`` call
it makes.

**The witness (R191).** "No new line in the audit file" is not evidence: the store checks the
boundary before the audit scope opens, so a *refused* read writes no line and an absent line is
consistent with both "no attempt" and "a refused attempt". What is evidence is positive and
comes from the process that reads: every ``load_panel`` call in the grandchild carried
``end == settings.splits.dev_end`` and the unlock flag unset.

**It is only a witness if the log is not empty.** A test that replaced ``load_for`` with a toy-panel
function would call ``load_panel`` never, the recorder would log nothing, and "every call was
sealed" would be true of zero calls. So the real read path runs here against the synthetic store,
the recorder *wraps* ``load_panel`` and calls through, and the tests assert non-vacuity directly:
the log holds calls, to every dataset the evaluation needs, and the recorder rebound names in the
modules that import the function (the identity sweep; patching ``volab.store`` alone would miss
them). ``test_the_witness_fails_on_a_read_past_dev_end`` shows it bites: the shim reads one session
past ``dev_end`` through the walk-forward module's own binding, the store refuses it, and the
logged bad ``end`` is what the witness catches -- not the exception.

**What this does not show (R191, R206).**

* It runs on a *synthetic* store of one symbol over about 190 sessions. It proves the machinery
  runs end to end and never asks for a date past ``dev_end``. It does **not** show that real
  development data yields a sensible candidate; that run is held on a user decision (R194).
* It exercises ``evaluate_proposal`` through the shim, **not** the child's ``__main__``, so the
  real entry point's argument parsing and exit-code mapping are not covered here. They are covered
  by ``tests/test_candidate_cli.py`` and ``tests/test_agent_runner.py``. The proposal check and the
  result-file shape are the entry point's own functions, called by the shim.
* The window is shrunk inside the shim by assigning the ``volab.candidate`` constants in that
  process, because a toy store cannot carry 250 sessions of training. The shipped values are pinned
  by ``tests/test_candidate_cli.py``.
* The sealed window is not read by anything here, so nothing is shown about what happens when it
  is; the store's refusal is pinned by its own tests.

The store uses the repository's real shape, ``storage_root = <root>/data`` (R211), so the sprint
reads the ledger where the child writes it. Nothing here writes under the repository's ``data/``,
and the reported ledgers are compared by content hash before and after.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from isolated_candidate import names_bound_to
from test_agent_sprint import script

import volab.store as store
from volab.agent import PROPOSALS_PER_SPRINT, SprintResult, canary_placement, run_sprint
from volab.agent._llm import FakeCompleter
from volab.agent._runner import ChildRun
from volab.agent.memory import FileCheckpointer
from volab.agent.sprintlog import events
from volab.agent.vocabulary import Proposal
from volab.candidate import FEATURE_SET
from volab.config import Settings, Splits, load_settings
from volab.features.targets import build_targets, target_column, targets_dataset
from volab.ingest.curate import BARS_DATASET, FRED_DATASET, VIX_DATASET
from volab.sessions import get_calendar, session_close_utc, sessions_between
from volab.store import write_curated, write_features
from volab.walkforward import target_panel_for

REPO = Path(__file__).resolve().parents[1]
SHIM = Path(__file__).resolve().parent / "isolated_candidate.py"

SPRINT = "2026-11-10-rehearsal"
AT = dt.datetime(2026, 11, 10, 6, 0, tzinfo=dt.UTC)
SHA = "rehearsal-sha"

# The synthetic store: about 190 sessions of 2023. ``dev_end`` is earlier than the repository's,
# which ``load_panel`` allows (it refuses only a *later* one), and the window is shrunk to fit.
FIRST_SESSION = dt.date(2023, 1, 3)
DEV_END = dt.date(2023, 9, 29)
FIRST_ORIGIN = dt.date(2023, 6, 1)
MINIMUM_TRAINING = 20
STRIDE = 2
HORIZON = 1
EXTRA = "vvix"

VIX_FAMILY = ("VIX", "VIX9D", "VIX1D", "VVIX")
FRED_NAMES = ("BAA10Y", "DGS10", "T10Y2Y")
INGESTED = pd.Timestamp("2023-10-02T00:00:00Z")

# What the evaluation must read: the bars, both exogenous families, the horizon's targets, and the
# persisted feature table. The last is the one read Phase 5 itself authors (``_persisted`` in
# ``volab.candidate``, with ``end`` chosen there, R214); every other dataset is read by Phase 2-4
# code that was reviewed before this layer existed.
EXPECTED_DATASETS = {
    BARS_DATASET,
    VIX_DATASET,
    FRED_DATASET,
    targets_dataset(HORIZON),
    FEATURE_SET,
}


# ---------------------------------------------------------------------------
# The synthetic store.
# ---------------------------------------------------------------------------


def _settings(tmp_path: Path) -> Settings:
    """Hand-built, as R206 requires, in the repository's real shape (R211)."""
    return dataclasses.replace(
        load_settings(),
        storage_root=tmp_path / "data",
        splits=Splits(
            dev_end=DEV_END,
            holdout_start=dt.date(2023, 10, 2),
            holdout_end=dt.date(2023, 12, 29),
        ),
    )


def _write_partitions(frame: pd.DataFrame, dataset: str, symbol: str, settings: Settings) -> None:
    for as_of, part in frame.groupby("as_of", sort=True):
        write_curated(
            part.reset_index(drop=True),
            dataset=dataset,
            symbol=symbol,
            as_of=as_of,  # type: ignore[arg-type]
            settings=settings,
        )


def build_store(settings: Settings) -> None:
    """SPY's bars, every exogenous series and the horizon's targets, under ``settings``' root.

    Two details carry over from ``test_truncation_equivalence``: an exogenous row's ``ts`` is a
    day behind its ``as_of`` (a join on the wrong one is otherwise byte-invisible), and one
    session has no bar, so the session index has a hole in it.
    """
    calendar = get_calendar(settings)
    sessions = sessions_between(calendar, FIRST_SESSION, DEV_END)
    rng = np.random.default_rng(20261110)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.011, len(sessions))))

    hole = sessions[len(sessions) // 2]
    keep = [i for i, day in enumerate(sessions) if day != hole]
    bar_days = [sessions[i] for i in keep]
    bars = pd.DataFrame(
        {
            "symbol": "SPY",
            "ts": [pd.Timestamp(session_close_utc(day)) for day in bar_days],
            "as_of": bar_days,
            "ingested_at": INGESTED,
            "close": [round(float(closes[i]), 4) for i in keep],
            "has_distribution": [i % 11 == 0 for i in range(len(bar_days))],
        }
    )
    _write_partitions(bars, BARS_DATASET, "SPY", settings)

    def series(name: str, column: str, base: float) -> pd.DataFrame:
        noise = rng.normal(0.0, 0.4, len(sessions))
        return pd.DataFrame(
            {
                "symbol": name,
                "ts": [pd.Timestamp(session_close_utc(d)) - pd.Timedelta(days=1) for d in sessions],
                "as_of": sessions,
                "ingested_at": INGESTED,
                column: np.round(base + noise + 0.05 * np.arange(len(sessions)) % 3, 4),
            }
        )

    for offset, name in enumerate(VIX_FAMILY):
        _write_partitions(series(name, "close", 12.0 + 6.0 * offset), VIX_DATASET, name, settings)
    for offset, name in enumerate(FRED_NAMES):
        _write_partitions(series(name, "value", 1.5 + offset), FRED_DATASET, name, settings)

    targets = build_targets(target_panel_for(bars, calendar), horizon=HORIZON, calendar=calendar)
    assert not targets.empty, "the synthetic bars must yield targets, or nothing is scoreable"
    assert target_column(HORIZON) in targets.columns
    for as_of, part in targets.groupby("as_of", sort=True):
        write_features(
            part.reset_index(drop=True),
            feature_set=targets_dataset(HORIZON),
            symbol="SPY",
            as_of=as_of,  # type: ignore[arg-type]
            settings=settings,
        )


# ---------------------------------------------------------------------------
# The shim launcher, in the shape ``run_sprint`` calls ``submit``.
# ---------------------------------------------------------------------------


class ShimSubmit:
    """``submit``'s signature: one fresh interpreter per proposal, running the shim.

    The environment is the test process's, which ``conftest`` has cleared of ``VOLAB_*`` overrides;
    the child is handed its store, window and log explicitly, as a file, instead. ``-W error``
    makes a warning in the child fail it, as ``filterwarnings = ["error"]`` does in the suite.
    """

    def __init__(self, scratch: Path, config: dict[str, Any], *, probe: bool = False) -> None:
        self.scratch = scratch
        self.config = config
        self.probe = probe
        self.runs: list[ChildRun] = []
        self.log = scratch / "recorder.jsonl"

    def __call__(self, proposal: Proposal) -> ChildRun:
        directory = self.scratch / f"child-{len(self.runs)}"
        directory.mkdir()
        proposal_path, result_path = directory / "proposal.json", directory / "result.json"
        config_path = directory / "config.json"
        proposal_path.write_text(json.dumps(dataclasses.asdict(proposal)), encoding="utf-8")
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        command = [
            sys.executable,
            "-W",
            "error",
            str(SHIM),
            "--proposal",
            str(proposal_path),
            "--result",
            str(result_path),
            "--config",
            str(config_path),
            "--log",
            str(self.log),
        ]
        if self.probe:
            command.append("--probe-past-dev-end")
        done = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            check=False,
        )
        result: dict[str, Any] | None = None
        if result_path.exists():
            parsed = json.loads(result_path.read_text(encoding="utf-8"))
            result = parsed if isinstance(parsed, dict) else None
        pid = result.get("pid") if result is not None else None
        run = ChildRun(
            proposal_id=proposal.proposal_id,
            exit_code=done.returncode,
            pid=pid if isinstance(pid, int) else None,
            result=result,
            output_tail=(done.stdout + done.stderr)[-4000:],
        )
        self.runs.append(run)
        return run


def _config(tmp_path: Path) -> dict[str, Any]:
    return {
        "storage_root": str(tmp_path / "data"),
        "dev_end": DEV_END.isoformat(),
        "holdout_start": "2023-10-02",
        "holdout_end": "2023-12-29",
        "first_origin": FIRST_ORIGIN.isoformat(),
        "stride": STRIDE,
        "minimum_training": MINIMUM_TRAINING,
    }


def _records(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# The witness itself, as a function, so it can be shown to bite.
# ---------------------------------------------------------------------------


def unsealed_reads(calls: Sequence[dict[str, Any]], dev_end: dt.date) -> list[dict[str, Any]]:
    """Every recorded call that did not carry ``end == dev_end`` with the unlock flag unset."""
    return [
        call
        for call in calls
        if call["end"] != dev_end.isoformat() or call["unlock_flag"] is not False
    ]


def _calls(log: Path) -> list[dict[str, Any]]:
    return [r for r in _records(log) if r["kind"] == "call"]


def test_the_witness_function_flags_a_late_end_an_early_end_and_a_set_flag() -> None:
    clean = {"kind": "call", "end": DEV_END.isoformat(), "unlock_flag": False}
    late = {**clean, "end": (DEV_END + dt.timedelta(days=1)).isoformat()}
    early = {**clean, "end": (DEV_END - dt.timedelta(days=1)).isoformat()}
    flagged = {**clean, "unlock_flag": True}
    missing = {**clean, "end": None}
    assert unsealed_reads([clean, clean], DEV_END) == []
    assert unsealed_reads([clean, late, early, flagged, missing], DEV_END) == [
        late,
        early,
        flagged,
        missing,
    ]


# ---------------------------------------------------------------------------
# The rehearsal.
# ---------------------------------------------------------------------------


def _hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _reported_ledgers() -> dict[str, str | None]:
    """Content hashes of every ledger in the repository's ``data/`` that a sprint must not move."""
    names = ("trials.jsonl", "comparisons.jsonl", "agent_trials.jsonl", "agent_comparisons.jsonl")
    return {name: _hash(REPO / "data" / name) for name in names}


def _scripted_replies() -> list[str]:
    """One valid proposal in a slot that is not the canary's; every other slot is refused."""
    canary_slot, _ = canary_placement(SPRINT, PROPOSALS_PER_SPRINT)
    valid_slot = (canary_slot + 1) % PROPOSALS_PER_SPRINT
    valid = json.dumps(
        {
            "family": "harx",
            "extra_regressors": [EXTRA],
            "symbol": "SPY",
            "horizon": HORIZON,
            "reference": ["har_levels", "random_walk"],
        }
    )
    refused = json.dumps({"family": "not-a-family"})
    return script(
        configurator=[valid if slot == valid_slot else refused for slot in range(4)],
    )


def _run_rehearsal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """One sprint, run once, with everything the checks below look at.

    A function and not a fixture, and the checks are one test and not five: a sprint costs about
    ten seconds (a fresh interpreter, a store read, an evaluation), and a function-scoped fixture
    would pay it once per check. A wider-scoped fixture is not an option, since
    ``tests/conftest.py`` refuses any store read during a wider-scoped setup.
    """
    settings = _settings(tmp_path)
    build_store(settings)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    submit = ShimSubmit(scratch, _config(tmp_path))

    # The sprint process itself must never call ``load_panel`` (R192 is structural; this is the
    # cheap behavioural check). Every binding is replaced with a counter that calls through.
    in_process: list[str] = []
    current = store.load_panel
    bound = names_bound_to(current)
    assert bound, "the sprint-side spy found no binding of load_panel to count"

    def counting(*args: Any, **kwargs: Any) -> Any:
        in_process.append(str(args[0]) if args else "?")
        return current(*args, **kwargs)

    for module, name in bound:
        monkeypatch.setattr(module, name, counting)

    reported_before = _reported_ledgers()
    result = run_sprint(
        sprint_id=SPRINT,
        settings=settings,
        completer=FakeCompleter(_scripted_replies()),
        checkpointer=FileCheckpointer(tmp_path / "memory"),
        log_path=tmp_path / "data" / "agent_sprints.jsonl",
        submit=submit,
        at=AT,
        sha=SHA,
        dirty=False,
    )
    return {
        "result": result,
        "submit": submit,
        "settings": settings,
        "tmp_path": tmp_path,
        "in_process_reads": in_process,
        "spied_bindings": len(bound),
        "reported_before": reported_before,
        "reported_after": _reported_ledgers(),
    }


def _check_a_full_weekly_sprint_runs_end_to_end_without_reading_past_dev_end(
    rehearsal: dict[str, Any],
) -> None:
    """The exit criterion: every read the grandchild made was sealed, and there were reads."""
    submit: ShimSubmit = rehearsal["submit"]
    settings: Settings = rehearsal["settings"]
    records = _records(submit.log)
    calls = _calls(submit.log)

    # Non-vacuity, first and explicitly. A witness over zero calls is true of anything.
    installs = [r for r in records if r["kind"] == "installed"]
    assert len(installs) == 1, "one evaluation ran, so the recorder was installed once"
    assert installs[0]["rebound"] >= 1, "the identity sweep rebound no name; nothing is recorded"
    assert installs[0]["dev_end"] == settings.splits.dev_end.isoformat()
    # The names the read path actually calls through, not just the store's own.
    assert {"volab.store.load_panel", "volab.panel.load_panel"} <= set(installs[0]["names"])
    assert any(name.startswith("volab.walkforward.") for name in installs[0]["names"])
    # ``volab.candidate._persisted`` is the only ``load_panel`` call whose ``end`` Phase 5 chooses.
    # If the sweep did not rebind its module's name, that read would be invisible to the recorder
    # and the sealed-reads check below would be true without having looked at it (R214).
    assert "volab.candidate.load_panel" in installs[0]["names"]
    assert len(calls) >= 1, "the recorder logged no load_panel call; the witness would be vacuous"
    assert {call["dataset"] for call in calls} >= EXPECTED_DATASETS, (
        "the real read path did not run against the synthetic store"
    )

    # The witness. The counts are printed so a run's evidence is visible with ``-rP``.
    print(f"load_panel calls recorded: {len(calls)}; names rebound: {installs[0]['rebound']}")
    assert unsealed_reads(calls, settings.splits.dev_end) == []
    assert {call["end"] for call in calls} == {settings.splits.dev_end.isoformat()}

    # Everything the recorder saw was seen by the grandchild, not by the sprint process.
    assert {call["pid"] for call in calls} == {installs[0]["pid"]}
    assert installs[0]["pid"] != os.getpid()


def _check_the_sprint_reached_a_pinned_outcome_after_one_real_evaluation(
    rehearsal: dict[str, Any],
) -> None:
    result: SprintResult = rehearsal["result"]
    submit: ShimSubmit = rehearsal["submit"]
    tmp_path: Path = rehearsal["tmp_path"]

    # Pinned, not derived from the output: one proposal reached the gate and ran, two were refused,
    # and the canary (the fourth slot) was refused.
    assert (result.proposals, result.accepted, result.refused) == (3, 1, 2)
    assert result.canary_refused is True
    assert len(submit.runs) == 1
    run = submit.runs[0]
    assert run.verdict == "evaluated", run.output_tail
    assert run.result is not None
    assert run.result["status"] == "ok"
    assert run.result["trials_written"] > 0
    # The store is noise, so the real qlike MCS at 10% keeps ``har_levels`` beside the candidate:
    # a tie, which R228 refuses. Pinned to that refusal, and to the proposal that actually ran.
    # (Before R228 this pinned ``awaiting_approval``; no real evaluation reaches that path now.)
    assert result.outcome == "no_candidate", result.reason
    assert result.pending is None
    ran = f"{SPRINT}-{int(run.proposal_id.rsplit('-', 1)[1]):02d}"
    assert result.reason.startswith(f"{ran}: "), result.reason
    assert "tied with har_levels" in result.reason

    kinds = [e["kind"] for e in events(tmp_path / "data" / "agent_sprints.jsonl", SPRINT)]
    assert kinds[0] == "start"
    assert kinds.count("proposal") == 1
    assert kinds.count("awaiting_approval") == 0
    assert kinds[-1] == "complete"


def _check_each_evaluation_ran_in_a_fresh_interpreter(rehearsal: dict[str, Any]) -> None:
    submit: ShimSubmit = rehearsal["submit"]
    pids = [run.pid for run in submit.runs]
    assert pids and all(isinstance(pid, int) for pid in pids)
    assert os.getpid() not in pids
    assert len(set(pids)) == len(pids)


def _check_the_sprint_process_never_called_load_panel(rehearsal: dict[str, Any]) -> None:
    assert rehearsal["spied_bindings"] >= 1
    assert rehearsal["in_process_reads"] == []


def _check_the_reported_ledgers_were_not_touched_and_the_agent_ledgers_are_under_tmp_path(
    rehearsal: dict[str, Any],
) -> None:
    tmp_path: Path = rehearsal["tmp_path"]
    before, after = rehearsal["reported_before"], rehearsal["reported_after"]
    # By content hash, not line count: an edit that keeps the count is still a moved number.
    assert before["trials.jsonl"] is not None, "the reported trial ledger should be present"
    assert before["comparisons.jsonl"] is not None
    assert after == before

    # The agent ledgers are where the child writes them: under the synthetic root's parent.
    trials = tmp_path / "data" / "agent_trials.jsonl"
    comparisons = tmp_path / "data" / "agent_comparisons.jsonl"
    assert trials.exists() and trials.read_text(encoding="utf-8").strip()
    assert comparisons.exists() and comparisons.read_text(encoding="utf-8").strip()
    assert not (tmp_path / "data" / "trials.jsonl").exists()
    assert not (tmp_path / "data" / "comparisons.jsonl").exists()


def test_one_full_weekly_sprint_runs_end_to_end_without_touching_the_sealed_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exit criterion, as one sprint and the five things that must be true of it."""
    rehearsal = _run_rehearsal(tmp_path, monkeypatch)
    _check_a_full_weekly_sprint_runs_end_to_end_without_reading_past_dev_end(rehearsal)
    _check_the_sprint_reached_a_pinned_outcome_after_one_real_evaluation(rehearsal)
    _check_each_evaluation_ran_in_a_fresh_interpreter(rehearsal)
    _check_the_sprint_process_never_called_load_panel(rehearsal)
    _check_the_reported_ledgers_were_not_touched_and_the_agent_ledgers_are_under_tmp_path(rehearsal)


# ---------------------------------------------------------------------------
# The witness bites.
# ---------------------------------------------------------------------------


def test_the_witness_fails_on_a_read_past_dev_end(tmp_path: Path) -> None:
    """The shim reads one session past ``dev_end``; the logged bad ``end`` is what is caught.

    The unlock flag is not passed, so the store refuses the read with ``HoldoutViolation`` and the
    shim logs that it was refused. The recorder logs the attempt *before* calling through, so the
    bad ``end`` is in the log either way -- and the assertion below is on the log, not on the
    exception. The read goes through ``volab.walkforward.load_targets``, which holds its own
    binding of ``load_panel``, so this also shows the identity sweep reached it.
    """
    settings = _settings(tmp_path)
    build_store(settings)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    submit = ShimSubmit(scratch, _config(tmp_path), probe=True)
    proposal = Proposal(
        proposal_id=f"{SPRINT}-00",
        family="harx",
        extra_regressors=(EXTRA,),
        symbol="SPY",
        horizon=HORIZON,
        reference=("har_levels",),
    )
    run = submit(proposal)
    assert run.exit_code == 0, run.output_tail

    calls = _calls(submit.log)
    assert len(calls) == 1
    bad = unsealed_reads(calls, settings.splits.dev_end)
    assert bad, "the witness missed a read one session past dev_end"
    assert bad[0]["end"] == (settings.splits.dev_end + dt.timedelta(days=1)).isoformat()
    assert bad[0]["unlock_flag"] is False
    assert bad[0]["dataset"] == targets_dataset(HORIZON)
    # The store did refuse it, and the sweep did reach walkforward's binding.
    assert any(r["kind"] == "probe_refused" for r in _records(submit.log))
    installs = [r for r in _records(submit.log) if r["kind"] == "installed"]
    assert any(name.startswith("volab.walkforward.") for name in installs[0]["names"])
