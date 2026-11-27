"""The Phase 5 exit criterion (P5-E3): a whole weekly sprint, never touching the sealed window.

The framework's criterion is *"one full weekly sprint runs end to end without touching the
holdout"*, and this module is the evidence for it. It runs ``volab.agent.run_sprint`` with a
scripted ``FakeCompleter``, a ``FileCheckpointer``, the sprint log, and an injected ``submit`` that
launches ``tests/isolated_candidate.py`` in a **fresh interpreter** per proposal; that shim calls
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
* The store plants a signal (P5-L14): ``VVIX`` leads the variance of the next return, and the
  shocks are light-tailed so 35 origins can show it. That is why one real evaluation reaches
  ``awaiting_approval`` (it beats ``har_levels``, R228) while a second, naming a noise series, only
  ties. It says nothing about whether any regressor beats ``har_levels`` on real data.
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
from volab.agent import (
    PROPOSALS_PER_SPRINT,
    SprintResult,
    canary_placement,
    evidence_hash,
    run_sprint,
)
from volab.agent._llm import FakeCompleter
from volab.agent._runner import ChildRun
from volab.agent.memory import FileCheckpointer
from volab.agent.sprintlog import events, fingerprint_of
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
EXTRA = "vvix"  # the planted signal: the proposal naming it must beat ``har_levels``
NOISE = "dgs10"  # seeded noise: the proposal naming it must tie with ``har_levels``

VIX_FAMILY = ("VIX", "VIX9D", "VIX1D")
PLANTED = "VVIX"
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
# The planted signal (P5-L14).
# ---------------------------------------------------------------------------

# ``VVIX`` (the ``vvix`` feature) leads the variance of SPY's next return. Its level is drawn
# independently each session from a generator of its own, and the variance of the return *into*
# session ``i`` is linear in its level at session ``i - 1``. A levels HAR-X naming ``vvix`` can fit
# that relationship exactly in expectation; ``har_levels`` cannot, because the draws are
# independent, so past squared returns say nothing about the next one. ``COUPLING`` is the one
# knob: at 0 the returns are i.i.d. and the store is noise again (the mutation check).
SIGNAL_SEED = 20261126
VVIX_CENTER = 95.0
VVIX_SPREAD = 30.0
COUPLING = 0.9
DRIFT = 0.0003
BASE_VARIANCE = 0.011**2
SHOCK_DEGREES = 40


def planted_vvix(count: int) -> np.ndarray:
    """``VVIX``'s level at each of ``count`` sessions, uniform on ``VVIX_CENTER +/- VVIX_SPREAD``.

    A function of the session count and its own seed only: it takes no return, so it cannot carry
    a future one. Its value at session ``t`` is the ``t``-th draw of its generator, so the series
    for fewer sessions is a prefix of the series for more (``test_the_planted_signal_...``).
    """
    rng = np.random.default_rng(SIGNAL_SEED)
    return np.round(VVIX_CENTER + VVIX_SPREAD * rng.uniform(-1.0, 1.0, count), 4)


def returns_led_by(vvix: np.ndarray, shocks: np.ndarray, coupling: float = COUPLING) -> np.ndarray:
    """Daily log returns whose variance at session ``i`` is set by ``vvix`` at session ``i - 1``.

    The first session has no predecessor and takes the centre. ``1 + coupling * u`` with ``u`` in
    ``[-1, 1]`` stays positive for ``coupling < 1``; at 0.9 the variance ranges over 19x.
    """
    lagged = np.concatenate(([VVIX_CENTER], vvix[:-1]))
    variance = BASE_VARIANCE * (1.0 + coupling * (lagged - VVIX_CENTER) / VVIX_SPREAD)
    return DRIFT + np.sqrt(variance) * shocks


def light_tailed_shocks(rng: np.random.Generator, count: int) -> np.ndarray:
    """Unit-variance shocks of random sign and size ``sqrt(chi2_k / k)``, ``k = SHOCK_DEGREES``.

    A Gaussian shock would make a day's squared return ``sigma**2 * chi2_1``, and noise that heavy
    buries a 19x variance signal over the 35 origins the toy window holds. MEASURED on this
    harness (2026-11-26): with Gaussian shocks the planted candidate still tied, ``har_levels``'
    MCS p-value 0.14; at ``k = 40``, where a squared return is within about 22% (one sd) of the
    variance ``VVIX`` set, it is 0.0, and 1.0 again with ``COUPLING = 0`` (a tie). The shocks are
    drawn independently of ``VVIX``, so they carry no signal of their own.
    """
    signs = np.where(rng.uniform(0.0, 1.0, count) < 0.5, -1.0, 1.0)
    return signs * np.sqrt(rng.gamma(SHOCK_DEGREES / 2, 2 / SHOCK_DEGREES, count))


def test_the_planted_signal_is_point_in_time_and_leads_the_returns() -> None:
    """The regressor at ``t`` uses only draws up to ``t``, and moves returns after ``t`` only.

    The store writes ``planted_vvix`` as the ``VVIX`` close with ``as_of`` the session itself, and
    the feature engine joins on ``as_of``; nothing here shifts it. So the leakage question is
    whether the value at ``t`` can depend on a return after ``t``. It cannot: it is a prefix-stable
    draw that takes no return, and the causal arrow runs the other way, one session later.
    """
    count, cut = 120, 70
    vvix = planted_vvix(count)
    for prefix in (1, cut, count - 1):
        assert np.array_equal(planted_vvix(prefix), vvix[:prefix])
    assert float(vvix.min()) >= VVIX_CENTER - VVIX_SPREAD
    assert float(vvix.max()) <= VVIX_CENTER + VVIX_SPREAD

    shocks = np.random.default_rng(1).normal(0.0, 1.0, count)
    returns = returns_led_by(vvix, shocks)
    # Moving the regressor at ``cut`` moves the return into ``cut + 1`` and nothing up to ``cut``.
    moved = vvix.copy()
    moved[cut] += 10.0
    shifted = returns_led_by(moved, shocks)
    assert np.array_equal(shifted[: cut + 1], returns[: cut + 1])
    assert shifted[cut + 1] != returns[cut + 1]
    assert np.array_equal(shifted[cut + 2 :], returns[cut + 2 :])
    # With no coupling the regressor moves nothing at all.
    assert np.array_equal(returns_led_by(moved, shocks, 0.0), returns_led_by(vvix, shocks, 0.0))


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

    ``VVIX`` is the planted signal (``planted_vvix``), written as its close with ``as_of`` the
    session of the draw; the returns are built from it with a one-session lag (``returns_led_by``).
    Every other exogenous series is seeded noise.
    """
    calendar = get_calendar(settings)
    sessions = sessions_between(calendar, FIRST_SESSION, DEV_END)
    rng = np.random.default_rng(20261110)
    vvix = planted_vvix(len(sessions))
    closes = 100.0 * np.exp(
        np.cumsum(returns_led_by(vvix, light_tailed_shocks(rng, len(sessions))))
    )

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

    def frame(name: str, column: str, values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": name,
                "ts": [pd.Timestamp(session_close_utc(d)) - pd.Timedelta(days=1) for d in sessions],
                "as_of": sessions,
                "ingested_at": INGESTED,
                column: values,
            }
        )

    def series(name: str, column: str, base: float) -> pd.DataFrame:
        noise = rng.normal(0.0, 0.4, len(sessions))
        return frame(name, column, np.round(base + noise + 0.05 * np.arange(len(sessions)) % 3, 4))

    for offset, name in enumerate(VIX_FAMILY):
        _write_partitions(series(name, "close", 12.0 + 6.0 * offset), VIX_DATASET, name, settings)
    # The planted signal, value at ``as_of == t`` being the draw that sets the return into t + 1.
    _write_partitions(frame(PLANTED, "close", vvix), VIX_DATASET, PLANTED, settings)
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


def _harx(extra: str) -> str:
    return json.dumps(
        {
            "family": "harx",
            "extra_regressors": [extra],
            "symbol": "SPY",
            "horizon": HORIZON,
            "reference": ["har_levels", "random_walk"],
        }
    )


def _scripted_replies() -> list[str]:
    """Two valid proposals in the two slots after the canary's; the remaining slot is refused.

    The first names the planted signal and the second a noise series, so one sprint witnesses
    both branches of R228 on real evaluations: a candidate that beats ``har_levels`` and one that
    only ties with it.
    """
    canary_slot, _ = canary_placement(SPRINT, PROPOSALS_PER_SPRINT)
    valid = {
        (canary_slot + 1) % PROPOSALS_PER_SPRINT: _harx(EXTRA),
        (canary_slot + 2) % PROPOSALS_PER_SPRINT: _harx(NOISE),
    }
    refused = json.dumps({"family": "not-a-family"})
    return script(configurator=[valid.get(slot, refused) for slot in range(4)])


def _run_rehearsal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """One sprint, run once, with everything the checks below look at.

    A function and not a fixture, and the checks are one test and not five: a sprint costs about
    ten seconds (two fresh interpreters, each a store read and an evaluation), and a
    function-scoped fixture would pay it once per check. A wider-scoped fixture is not an option,
    since ``tests/conftest.py`` refuses any store read during a wider-scoped setup.
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
    assert len(installs) == 2, "two evaluations ran, so the recorder was installed twice"
    for install in installs:
        assert install["rebound"] >= 1, "the identity sweep rebound no name; nothing is recorded"
        assert install["dev_end"] == settings.splits.dev_end.isoformat()
        # The names the read path actually calls through, not just the store's own.
        assert {"volab.store.load_panel", "volab.panel.load_panel"} <= set(install["names"])
        assert any(name.startswith("volab.walkforward.") for name in install["names"])
        # ``volab.candidate._persisted`` is the only ``load_panel`` call whose ``end`` Phase 5
        # chooses. If the sweep did not rebind its module's name, that read would be invisible to
        # the recorder and the sealed-reads check below would be true without having looked at it
        # (R214).
        assert "volab.candidate.load_panel" in install["names"]
        # Per evaluation, so one child's reads cannot stand in for the other's.
        mine = [call for call in calls if call["pid"] == install["pid"]]
        assert mine, "the recorder logged no load_panel call; the witness would be vacuous"
        assert {call["dataset"] for call in mine} >= EXPECTED_DATASETS, (
            "the real read path did not run against the synthetic store"
        )

    # The witness. The counts are printed so a run's evidence is visible with ``-rP``.
    rebound = [install["rebound"] for install in installs]
    print(f"load_panel calls recorded: {len(calls)}; names rebound per evaluation: {rebound}")
    assert unsealed_reads(calls, settings.splits.dev_end) == []
    assert {call["end"] for call in calls} == {settings.splits.dev_end.isoformat()}

    # Everything the recorder saw was seen by a grandchild, not by the sprint process.
    assert {call["pid"] for call in calls} == {install["pid"] for install in installs}
    assert os.getpid() not in {install["pid"] for install in installs}


def _qlike_record(summary: dict[str, Any]) -> dict[str, Any]:
    """The child's one qlike comparison record: the one R228's promotion rule reads."""
    records = [c for c in summary["comparisons"] if c["loss"] == "qlike"]
    assert len(records) == 1, [c["loss"] for c in summary["comparisons"]]
    assert records[0]["refusal"] is None, records[0]["refusal"]
    return records[0]


def _the_candidate(record: dict[str, Any]) -> str:
    """The one contestant that is not a reference contestant."""
    names = [c for c in record["contestants"] if c not in ("har_levels", "random_walk")]
    assert len(names) == 1, record["contestants"]
    return names[0]


def _mcs_at_ten_percent(run: ChildRun) -> tuple[str, list[str], dict[str, float]]:
    """``(candidate, members, p-values)`` of a run's qlike MCS, printed for ``-rP``."""
    assert run.verdict == "evaluated", run.output_tail
    assert run.result is not None
    assert run.result["status"] == "ok"
    assert run.result["trials_written"] > 0
    qlike = _qlike_record(run.result)
    assert qlike["cohort"] == run.result["cohort"]
    candidate = _the_candidate(qlike)
    members = qlike["mcs"]["sizes"]["0.1"]["members"]
    pvalues = qlike["mcs"]["pvalues"]
    dm = [p for p in qlike["dm"] if {p["left"], p["right"]} == {candidate, "har_levels"}]
    print(
        f"{run.proposal_id} {candidate}: {qlike['origins']} origins, n_eff {qlike['n_eff']}; "
        f"qlike MCS at 10% {members}; MCS p-values {pvalues}; DM against har_levels "
        f"{[(p['left'], p['right'], p['mean_difference'], p['headline']['p_value']) for p in dm]}"
    )
    return candidate, members, pvalues


def _check_the_sprint_reached_a_pinned_outcome_after_two_real_evaluations(
    rehearsal: dict[str, Any],
) -> None:
    result: SprintResult = rehearsal["result"]
    submit: ShimSubmit = rehearsal["submit"]
    tmp_path: Path = rehearsal["tmp_path"]

    # Pinned, not derived from the output: two proposals reached the gate and ran, one was
    # refused, and the canary was refused.
    assert (result.proposals, result.accepted, result.refused) == (3, 2, 1)
    assert result.canary_refused is True
    assert len(submit.runs) == 2
    signal, noise = submit.runs
    signal_cohort = signal.result["cohort"] if signal.result else None
    noise_cohort = noise.result["cohort"] if noise.result else None

    # The comparison records R228 reads, asserted on directly and not only through the outcome.
    # The candidate naming the planted signal beats ``har_levels``: it is in the real qlike MCS at
    # 10% and ``har_levels`` is not (``build_store``).
    candidate, members, pvalues = _mcs_at_ten_percent(signal)
    assert candidate == f"harx+{EXTRA}"
    assert candidate in members
    assert "har_levels" not in members
    assert pvalues["har_levels"] < 0.1 <= pvalues[candidate]
    # The candidate naming a noise series only ties with it: both are in the set, which R228
    # refuses. Its refusal reason is not in ``result.reason`` (the sprint reports only the
    # promotable candidates when there is one), so the tie is pinned on its record and on the
    # absence of an approval for it below.
    tied, members, pvalues = _mcs_at_ten_percent(noise)
    assert tied == f"harx+{NOISE}"
    assert {tied, "har_levels"} <= set(members)
    assert pvalues["har_levels"] >= 0.1 and pvalues[tied] >= 0.1

    # So the sprint reaches the approval path on a real evaluation, for the signal's proposal only.
    assert result.outcome == "awaiting_approval", result.reason
    pending = result.pending
    assert pending is not None
    assert pending.proposal_id == signal.proposal_id
    assert pending.cohort == signal_cohort
    assert (
        result.reason
        == f"1 candidate(s) await approval in a separate process: {signal.proposal_id}"
    )

    # The evidence is the real agent-ledger lines the child wrote, recomputed here from the file
    # itself rather than from anything the sprint returned; the tie's lines are beside them in the
    # same file and are not part of it.
    lines = _records(tmp_path / "data" / "agent_trials.jsonl")
    evidence = [evidence_hash(line) for line in lines if line["cohort"] == pending.cohort]
    assert signal.result is not None
    assert len(evidence) == signal.result["trials_written"]
    tie_lines = [line for line in lines if line["cohort"] == noise_cohort]
    assert tie_lines and noise_cohort != pending.cohort
    assert len(lines) == len(evidence) + len(tie_lines)
    assert sorted(pending.evidence) == sorted(evidence)
    assert pending.fingerprint == fingerprint_of(pending.proposal, evidence)

    log = events(tmp_path / "data" / "agent_sprints.jsonl", SPRINT)
    kinds = [e["kind"] for e in log]
    assert kinds[0] == "start"
    assert kinds.count("proposal") == 2
    assert kinds[-1] == "complete"
    approvals = [e for e in log if e["kind"] == "awaiting_approval"]
    assert len(approvals) == 1
    assert approvals[0]["proposal_id"] == pending.proposal_id
    assert approvals[0]["cohort"] == pending.cohort
    assert approvals[0]["fingerprint"] == pending.fingerprint
    assert sorted(approvals[0]["evidence"]) == sorted(evidence)


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
    _check_the_sprint_reached_a_pinned_outcome_after_two_real_evaluations(rehearsal)
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
