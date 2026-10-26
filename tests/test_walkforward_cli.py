"""The walk-forward CLI's own guarantees, which had no test at all until a delivery audit said so.

Three claims in `phase2-decisions.md` were stated with no executable witness, which is exactly
what ruling R32 forbids, and the audit found them by grepping for a test that imports
``volab.walkforward.__main__`` and finding none:

* **R62** -- there is no ``--end`` and no unlock flag, and ``--to`` past ``dev_end`` is refused
  loudly rather than clamped silently.
* **R63** -- an audit violation makes the run exit non-zero rather than warn.
* The **NOT COMPARED** branch -- a truncation comparison with nothing to compare must not print
  "0 of 0 identical" and exit 0, which would be a check that passes by checking nothing.

Nothing here reads the real store: the panel and targets are synthetic and the store root is a
``tmp_path``, so these run on any checkout. Every date is at or before the repository's
``dev_end``; ``conftest``'s autouse ``_forbid_holdout_unlock`` fails an unmarked test that reads
past it, and these tests never ask it to.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

import volab.walkforward as module
import volab.walkforward.__main__ as cli
from volab.config import load_settings
from volab.walkforward import TRIAL_LEDGER, RunAudit, count_trials


def test_there_is_no_end_argument_and_no_unlock_flag() -> None:
    """R62's first half, checked on the parser rather than on the prose.

    An ``--end`` would be the way this entry point became the thing that opened the holdout, so
    its absence is a guarantee and not an oversight.
    """
    with pytest.raises(SystemExit) as refused:
        cli.main(["run", "--end", "2026-06-30"])
    assert refused.value.code == 2, "argparse should reject an unknown argument"

    for forbidden in ("--allow-holdout", "--unlock", "--holdout"):
        with pytest.raises(SystemExit):
            cli.main(["run", forbidden])


def test_a_to_date_past_dev_end_is_refused_rather_than_clamped(capsys) -> None:
    """R62's second half. A silent clamp would let a caller believe they had evaluated a window
    they had not, which is worse than a refusal."""
    settings = load_settings()
    past = settings.splits.dev_end + dt.timedelta(days=1)
    assert cli.main(["run", "--to", past.isoformat()]) == 1
    printed = capsys.readouterr().out
    assert "past dev_end" in printed
    assert "sealed" in printed
    assert str(settings.splits.dev_end) in printed


def test_holdout_start_is_past_dev_end_so_the_refusal_covers_the_real_boundary() -> None:
    """The refusal is only worth anything if the sealed window really begins after ``dev_end``."""
    settings = load_settings()
    assert settings.splits.holdout_start > settings.splits.dev_end
    assert settings.splits.dev_end == dt.date(2025, 12, 31)
    assert settings.splits.holdout_start == dt.date(2026, 1, 2)


def test_an_audit_violation_makes_the_run_exit_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """R63. Reading past a cutoff is the one defect the module exists to prevent, so a run that
    detects one must fail rather than report and carry on.

    Provoked by making ``knowable_targets`` return the whole target frame, which is the leak the
    target audit was added to see. The point is the **exit code and the flush**, not the leak: a
    refusal must not throw away trials the run already counted.
    """
    settings = _settings_with_root(load_settings(), tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_persisted", lambda *_: None)
    panel, targets = _toy_panel_and_targets()
    monkeypatch.setattr(cli, "load_for", lambda *a, **k: (panel, targets))
    monkeypatch.setattr(module, "knowable_targets", lambda frame, origin: frame)

    code = cli.main(
        ["run", "--symbol", "SPY", "--horizon", "1", "--from", "2024-03-01", "--to", "2024-06-28"]
    )
    printed = capsys.readouterr().out
    assert code == 2, f"a detected leak exited {code}, not 2:\n{printed}"
    assert "REFUSED" in printed
    assert "read past their cutoff" in printed


def test_a_run_with_nothing_to_compare_says_so_instead_of_passing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Nothing to compare is not agreement.

    The branch is reachable whenever the store holds no built feature table, so this is an
    ordinary state and not a defensive guard. Printing "0 of 0 origins identical" and exiting 0
    was the earlier behaviour, and it is the vacuous-check shape this project names as its
    anti-goal.
    """
    settings = _settings_with_root(load_settings(), tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_persisted", lambda *_: None)
    panel, targets = _toy_panel_and_targets()
    monkeypatch.setattr(cli, "load_for", lambda *a, **k: (panel, targets))

    code = cli.main(
        [
            "run",
            "--symbol",
            "SPY",
            "--horizon",
            "1",
            "--from",
            "2024-03-01",
            "--to",
            "2024-06-28",
            "--minimum-training",
            "20",
        ]
    )
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "NOT COMPARED" in printed
    assert "0 of 0 origins identical" not in printed


def test_a_run_writes_one_ledger_line_per_configuration_it_evaluated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Every trial is counted, end to end through the CLI rather than through ``append_trials``.

    Also pins the fields a reader needs to tell one experiment from another: the stride, how many
    origins were requested, and whether the tree was dirty.
    """
    settings = _settings_with_root(load_settings(), tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_persisted", lambda *_: None)
    panel, targets = _toy_panel_and_targets()
    monkeypatch.setattr(cli, "load_for", lambda *a, **k: (panel, targets))

    assert count_trials(tmp_path) == 0
    assert (
        cli.main(
            [
                "run",
                "--symbol",
                "SPY",
                "--horizon",
                "1",
                "--from",
                "2024-03-01",
                "--to",
                "2024-06-28",
                "--minimum-training",
                "20",
                "--every",
                "2",
            ]
        )
        == 0
    )
    capsys.readouterr()

    expected = len(module.MODELS) * len(module.LOSSES)
    assert count_trials(tmp_path) == expected
    lines = [
        json.loads(line)
        for line in (tmp_path / TRIAL_LEDGER).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == expected
    for line in lines:
        assert line["every"] == 2, "the stride must be on the line or two runs are confusable"
        assert line["requested"] >= line["origins"]
        assert "git_dirty" in line
        assert line["git_sha"]
    assert {line["model"] for line in lines} == set(module.MODELS)
    assert {line["loss"] for line in lines} == set(module.LOSSES)


def test_a_later_refusal_leaves_the_earlier_trials_counted_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The real property, over **two** configurations, which is where the defect lived.

    A number that was computed must not vanish because a later configuration refused -- that is
    the forking-paths hole the ledger exists to close, and R74 closed it by appending as each
    configuration finishes. But the refusal path kept its earlier last-resort flush, so once R74
    landed the two overlapped: on a run where SPY succeeded and QQQ refused, SPY's six trials
    were appended **twice**, the second time under ``datetime.now`` instead of the run's single
    timestamp. The ledger's one job is to report how many candidates were considered, and it was
    inflating that number on exactly the path R74 was written to protect.

    The previous test for this exercised ``_flush`` in isolation and asserted it appends what it
    is handed, which it did. Nothing drove two configurations, so nothing saw the double count.
    That is the shape this project keeps finding: a test that checks a helper rather than the
    behaviour the helper exists to produce.
    """
    settings = _settings_with_root(load_settings(), tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_persisted", lambda *_: None)
    # Two configurations on ONE symbol, at two horizons. The toy panel carries only SPY bars, so
    # a second *symbol* would fail for want of data and test the wrong thing.
    panel, by_horizon = _toy_panel_and_targets_by_horizon()
    monkeypatch.setattr(
        cli, "load_for", lambda _s, _sym, horizon, **k: (panel, by_horizon[horizon])
    )

    # The first configuration is clean, the second trips the audit. Patched on the audit rather
    # than contrived in the data, so the test says what it means: the refusal is not the subject,
    # the ledger's count across the boundary is.
    seen = {"configurations": 0}
    violation = [("panel", dt.date(2024, 4, 1), dt.date(2024, 4, 30))]

    def violations(self: RunAudit) -> list[tuple[str, dt.date, dt.date]]:
        seen["configurations"] += 1
        return [] if seen["configurations"] == 1 else violation

    monkeypatch.setattr(module.RunAudit, "violations", violations)

    assert count_trials(tmp_path) == 0
    code = cli.main(
        [
            "run",
            "--symbol",
            "SPY",
            "--horizon",
            "1",
            "--horizon",
            "5",
            "--from",
            "2024-03-01",
            "--to",
            "2024-06-28",
            "--minimum-training",
            "20",
            "--every",
            "2",
        ]
    )
    capsys.readouterr()

    assert code == 2, "R63: an audit violation exits non-zero rather than warning"
    assert seen["configurations"] == 2, "the second configuration must have been reached"

    one_configuration = len(module.MODELS) * len(module.LOSSES)
    assert count_trials(tmp_path) == one_configuration, (
        "the first configuration's trials must be on the ledger exactly once: "
        "neither erased by the refusal nor appended twice by a flush R74 superseded"
    )
    lines = [
        json.loads(line)
        for line in (tmp_path / TRIAL_LEDGER).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {line["horizon"] for line in lines} == {1}, "the refused configuration wrote nothing"
    # One run, one timestamp (R70). The duplicate lines carried ``datetime.now`` instead, so this
    # assertion is the one that would have caught the defect even without the count.
    assert len({line["at"] for line in lines}) == 1


def test_an_unknown_git_state_is_not_reported_as_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """``None`` means git could not be reached, which is not ``clean``.

    The console used to say "clean" there while the ledger line correctly recorded null -- a
    false reassurance in the place a reader actually looks.
    """
    settings = _settings_with_root(load_settings(), tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "git_state", lambda *_: ("unknown", None))
    assert cli.main(["run", "--to", (settings.splits.dev_end + dt.timedelta(days=1)).isoformat()])
    monkeypatch.setattr(cli, "_persisted", lambda *_: None)
    panel, targets = _toy_panel_and_targets()
    monkeypatch.setattr(cli, "load_for", lambda *a, **k: (panel, targets))
    cli.main(
        ["run", "--symbol", "SPY", "--horizon", "1", "--from", "2024-03-01", "--to", "2024-06-28"]
    )
    printed = capsys.readouterr().out
    assert "UNKNOWN" in printed
    assert "clean" not in printed.replace("UNKNOWN", "")


# ---------------------------------------------------------------------------
# Fixtures. Synthetic throughout: no real store, so these run on any checkout.
# ---------------------------------------------------------------------------


def _settings_with_root(settings, root: Path):
    """``settings`` with the storage root moved into ``tmp_path``, so the ledger is disposable."""
    from dataclasses import replace

    return replace(settings, storage_root=root / "data")


def _toy_panel_and_targets() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A synthetic panel and its targets, built the way the engine's own helpers build them."""
    from test_truncation_equivalence import _synthetic

    from volab.features.targets import build_targets
    from volab.sessions import get_calendar
    from volab.walkforward import target_panel_for

    calendar = get_calendar(load_settings())
    synthetic = _synthetic(calendar)
    bars = synthetic.panel[synthetic.panel["symbol"] == "SPY"].reset_index(drop=True)
    targets = build_targets(target_panel_for(bars, calendar), horizon=1, calendar=calendar)
    return synthetic.panel, targets


def _toy_panel_and_targets_by_horizon() -> tuple[pd.DataFrame, dict[int, pd.DataFrame]]:
    """The toy panel, plus its targets built for each horizon the CLI may be asked for."""
    from test_truncation_equivalence import _synthetic

    from volab.features.targets import build_targets
    from volab.sessions import get_calendar
    from volab.walkforward import target_panel_for

    calendar = get_calendar(load_settings())
    synthetic = _synthetic(calendar)
    bars = synthetic.panel[synthetic.panel["symbol"] == "SPY"].reset_index(drop=True)
    frame = target_panel_for(bars, calendar)
    return synthetic.panel, {
        horizon: build_targets(frame, horizon=horizon, calendar=calendar) for horizon in (1, 5)
    }


def test_the_audit_object_the_cli_builds_reports_both_kinds() -> None:
    """A guard against the CLI quietly going back to a panel-only audit."""
    audit = RunAudit()
    audit.record(dt.date(2024, 1, 2), dt.date(2024, 1, 5))
    audit.record_training(dt.date(2024, 1, 2), dt.date(2024, 1, 9))
    assert sorted(kind for kind, _, _ in audit.violations()) == ["panel", "target"]
