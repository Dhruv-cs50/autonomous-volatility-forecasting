"""The CLI and the carved rehearsal of ``take`` (W7-W12, W22-W24 and the exit table).

Every ``take`` here runs against a pseudo-holdout carved inside dev, in a throwaway git repository
under ``tmp_path`` (``tests/rehearsal.py``), with the key simulated by a stub that forwards its
argument. Nothing holds a key against ``data/`` and no record is written outside ``tmp_path``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from rehearsal import TODAY, World, build_world, git
from test_reveal import _comparison, _trials

import volab.reveal as reveal
import volab.reveal.__main__ as cli
from volab import store
from volab.compare.cohorts import SHOOTOUT
from volab.leaderboard import read_comparisons, read_trials
from volab.walkforward import LOSSES, ConfigurationOutcome, count_trials


def run(world: World, *argv: str, today: dt.date = TODAY) -> int:
    return cli.main(list(argv), settings=world.settings, repo_root=world.root, today=today)


def records(world: World) -> list[dict[str, Any]]:
    path = world.audit()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def last_line(out: str) -> str:
    return out.strip().splitlines()[-1]


def expected_sentence(world: World) -> str:
    """The seal line the record calls for, derived here from the log itself and not from
    ``seal_state``, so the CLI's wording is checked against an independent reading."""
    log = records(world)
    kinds = [(r["kind"], r["reveal_id"]) for r in log if r["kind"].startswith("reveal_")]
    looks = sum(1 for r in log if r["kind"] == "look_open")
    opened = {i for k, i in kinds if k == "reveal_open"}
    completed = {i for k, i in kinds if k == "reveal_complete" and i in opened}
    if cli._committed():
        state = reveal.SealState.LIFTED
    elif completed:
        state = reveal.SealState.READ_UNCOMMITTED
    elif opened and looks:
        state = reveal.SealState.COUNTED_WITH_LOOKS
    elif opened:
        state = reveal.SealState.COUNTED
    else:
        state = reveal.SealState.UNTOUCHED
    return said(state, looks)


def said(state: reveal.SealState, looks: int) -> str:
    """The sentence for ``state``; only the looks-taken one names a number."""
    return reveal.SEAL_SENTENCES[state].format(looks=looks)


# ---- the completed rehearsal: W7, W9, W24 ------------------------------------------------------


def _completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[World, int, str]:
    world = build_world(tmp_path, monkeypatch)
    code = run(world, "take")
    return world, code, capsys.readouterr().out


def test_the_carved_reveal_logs_exactly_the_planned_looks_before_it_takes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world, code, _ = _completed(tmp_path, monkeypatch, capsys)

    log = records(world)
    attempt = next(r["reveal_id"] for r in log if r["kind"] == "reveal_open")
    opens = [r for r in log if r["kind"] == "look_open"]
    closes = [r for r in log if r["kind"] == "look_close"]
    planned = [r for r in reveal.plan(world.settings) if r.sealed]

    assert code == 0
    assert store.audit_tally(world.settings).looks == reveal.expected_looks(world.settings) == 38
    assert len(opens) == len(closes) == len(planned)
    assert {r["reveal_id"] for r in opens + closes} == {attempt}
    assert {r["end"] for r in opens} == {world.settings.splits.holdout_end.isoformat()}
    # Each look is the one the plan declared, in the order it declared it.
    assert [(r["dataset"], r["tier"], tuple(r["symbols"])) for r in opens] == [
        (p.dataset, p.tier.value, p.symbols) for p in planned
    ]
    # Each look_open precedes its own look_close in the file, and the log opens with reveal_open.
    position = {(r["kind"], r.get("look_id")): i for i, r in enumerate(log)}
    for r in opens:
        assert position[("look_open", r["look_id"])] < position[("look_close", r["look_id"])]
    assert log[0]["kind"] == "reveal_open" and log[-1]["kind"] == "reveal_complete"
    assert log[0]["details"]["planned_looks"] == 38
    assert log[-1]["details"]["champion"]["status"] == "none"
    # The dividends reads are looks even though the dataset is absent (a refused read is a look).
    dividends = [r for r in opens if r["dataset"] == "nasdaq_dividends"]
    outcomes = {r["look_id"]: r["outcome"] for r in closes}
    assert {outcomes[r["look_id"]] for r in dividends} == {"DatasetNotFound"}
    assert os.environ.get("VOLAB_HOLDOUT_UNLOCK") is None


def test_a_carved_reveal_writes_the_ledger_footprint_its_shape_predicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world, code, _ = _completed(tmp_path, monkeypatch, capsys)

    symbols = len(world.settings.universe)
    expected_trials = sum(
        len(cohort.contestants) * symbols * len(horizons) * len(LOSSES)
        for cohort, horizons in reveal.RUNS
    )
    expected_comparisons = sum(symbols * len(horizons) * len(LOSSES) for _, horizons in reveal.RUNS)
    lines, _ = read_trials(world.root)

    assert code == 0
    assert (len(lines), len(read_comparisons(world.root))) == (
        expected_trials,
        expected_comparisons,
    )
    assert (expected_trials, expected_comparisons) == (18, 6)
    # One run, one timestamp: the two ledgers join on it.
    assert len({line.at for line in lines}) == 1
    assert {c["git_sha"] for c in read_comparisons(world.root)} == {lines[0].sha}


def test_the_write_up_reports_both_counts_and_names_its_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world, code, out = _completed(tmp_path, monkeypatch, capsys)

    assert code == 0
    for needle in (
        "38 sealed reads logged (38 closed)",
        "reveal attempts k = 1; the k-th completed: True",
        "trials: registered 414, at read 0 (delta -414), now 18",
        "comparisons: registered 52, at read 0 (delta -52), now 6",
        f"trials, superset across all trial ledgers ({reveal.SUPERSET_REASON}): "
        "at read 0 (delta -414 against the registered 414), now 18",
        "agent ledgers: 0 agent trial(s) in data/agent_trials.jsonl",
        "champion: none",
        "rule: The champion is the contestant in the 10% model confidence set",
        "command: python -m volab.reveal take",
        "origins SPY h1: 9 (registered prediction 208)",
        "ranking SPY h1 qlike",
    ):
        assert needle in out, needle
    assert (
        last_line(out)
        == expected_sentence(world)
        == reveal.SEAL_SENTENCES[reveal.SealState.READ_UNCOMMITTED]
    )


# ---- R216 through the CLI ---------------------------------------------------------------------


def test_the_printed_result_states_a_withheld_champions_lead_carries_no_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    named = reveal.champion_from(
        _comparison(("egarch11_skewt", "har_levels"), unclaimed=("egarch11_skewt",)),
        _trials({"egarch11_skewt": -8.9, "har_levels": -8.5}),
    )
    world = build_world(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "champion_from", lambda *a, **k: named)

    code = run(world, "take")
    out = capsys.readouterr().out

    assert code == 0, "a withheld champion is NAMED, so it is not exit 13"
    assert "champion: named" in out
    assert "egarch11_skewt is the champion under the registered rule." in out
    assert reveal.WITHHELD_SENTENCE in out
    complete = records(world)[-1]
    assert complete["details"]["champion"]["name"] == "egarch11_skewt"
    assert complete["details"]["champion"]["unclaimed"] == ["egarch11_skewt"]
    assert complete["details"]["champion"]["cohort"] == reveal.RUNS[0][0].name


# ---- the exit table ---------------------------------------------------------------------------

Setup = Callable[[World, pytest.MonkeyPatch], None]
UNTOUCHED, COUNTED = reveal.SealState.UNTOUCHED, reveal.SealState.COUNTED
LOOKED = reveal.SealState.COUNTED_WITH_LOOKS
READ, LIFTED = reveal.SealState.READ_UNCOMMITTED, reveal.SealState.LIFTED


def _dirty(world: World, mp: pytest.MonkeyPatch) -> None:
    world.root.joinpath("stray.txt").write_text("x", encoding="utf-8")


def _no_git(world: World, mp: pytest.MonkeyPatch) -> None:
    nowhere = world.root.parent.joinpath(f"{world.root.name}-nowhere")
    nowhere.mkdir()
    mp.setattr(cli, "_REPO_ROOT", nowhere)
    mp.setattr(world, "root", nowhere)


def _rewrite(text: str | None) -> Setup:
    def apply(world: World, mp: pytest.MonkeyPatch) -> None:
        document = world.root.joinpath(reveal.PREREGISTRATION)
        if text is None:
            git(world.root, "rm", "-q", reveal.PREREGISTRATION.as_posix())
        else:
            document.write_text(text, encoding="utf-8")
            git(world.root, "add", reveal.PREREGISTRATION.as_posix())
        git(world.root, "commit", "-q", "-m", "change")

    return apply


def _other_blob(world: World, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(reveal, "REGISTERED_BLOB", "1" * 40)


def _committed_already(world: World, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(cli, "_committed", lambda: True)


def _short_window(world: World, mp: pytest.MonkeyPatch) -> None:
    longer = dataclasses.replace(
        world.settings.splits, holdout_end=world.settings.splits.holdout_end + dt.timedelta(days=7)
    )
    world.settings = dataclasses.replace(world.settings, splits=longer)


def _canary_trips(world: World, mp: pytest.MonkeyPatch) -> None:
    real = reveal.derive_curated

    def rewriting(settings: Any, *, end: dt.date, report: Any) -> dict[str, int]:
        done = real(settings, end=end, report=report)
        day = dt.date(2024, 3, 27)
        frame = store.load_panel(
            "bars_daily", end=settings.splits.dev_end, symbols=["SPY"], settings=settings
        )
        row = frame[frame["as_of"].dt.date == day].copy()
        row["close"] = row["close"] + 1.0
        row["as_of"] = [day]
        store.write_curated(row, dataset="bars_daily", symbol="SPY", as_of=day, settings=settings)
        return done

    mp.setattr(cli, "derive_curated", rewriting)


def _scoring_crashes(world: World, mp: pytest.MonkeyPatch) -> None:
    def crash(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("scoring fell over")

    mp.setattr(cli, "score", crash)


def _ambiguous(world: World, mp: pytest.MonkeyPatch) -> None:
    tie = reveal.champion_from(_comparison(("a", "b")), _trials({"a": -8.0, "b": -8.0}))
    mp.setattr(cli, "champion_from", lambda *a, **k: tie)


def _truncation_disagrees(world: World, mp: pytest.MonkeyPatch) -> None:
    real = reveal.score

    def disagreeing(*args: Any, **kwargs: Any) -> Any:
        return dataclasses.replace(real(*args, **kwargs), disagreed=("phase2-baseline SPY h1",))

    mp.setattr(cli, "score", disagreeing)


def _second_configuration_refuses(world: World, mp: pytest.MonkeyPatch) -> None:
    """The first configuration runs for real and appends its trials; the second is refused."""
    real = reveal.run_configuration
    calls: list[tuple[str, int, str]] = []
    world.refusals = calls  # type: ignore[attr-defined]

    def refusing(settings: Any, symbol: str, horizon: int, *args: Any, **kwargs: Any) -> Any:
        if calls:
            calls.append((symbol, horizon, kwargs["cohort"].name))
            return ConfigurationOutcome(
                evaluation=None, trials=(), written=0, refused=True, disagrees=False
            )
        calls.append((symbol, horizon, kwargs["cohort"].name))
        return real(settings, symbol, horizon, *args, **kwargs)

    mp.setattr(reveal, "run_configuration", refusing)


def _printing_fails(world: World, mp: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the printer fell over")

    mp.setattr(cli, "describe", broken)


# (id, key held, setup, command, today, exit code, expected state, a phrase in the output)
_EXITS: list[tuple[str, bool, Setup | None, str, dt.date, int, reveal.SealState, str]] = [
    ("3 dirty tree", True, _dirty, "take", TODAY, 3, UNTOUCHED, "the tree is dirty"),
    ("4 git unknown", True, _no_git, "take", TODAY, 4, UNTOUCHED, "git cannot name"),
    ("5 absent", True, _rewrite(None), "take", TODAY, 5, UNTOUCHED, "cannot be read"),
    ("6 content", True, _rewrite("changed\n"), "take", TODAY, 6, UNTOUCHED, "has changed"),
    ("7 blob", True, _other_blob, "take", TODAY, 7, UNTOUCHED, "history is not the one"),
    ("8 window open", True, None, "take", dt.date(2024, 4, 12), 8, UNTOUCHED, "not in the past"),
    ("9 revealed", True, _committed_already, "take", TODAY, 9, LIFTED, "already committed"),
    ("10 no key", False, None, "take", TODAY, 10, COUNTED, "not exported"),
    (
        "11 short window",
        True,
        _short_window,
        "take",
        dt.date(2024, 4, 22),
        11,
        LOOKED,
        "do not reach",
    ),
    ("12 canary", True, _canary_trips, "take", TODAY, 12, LOOKED, "changed across"),
    ("1 crash in scoring", True, _scoring_crashes, "take", TODAY, 1, LOOKED, "scoring fell over"),
    ("13 ambiguous", True, _ambiguous, "take", TODAY, 13, READ, "names nobody"),
    ("14 truncation", True, _truncation_disagrees, "take", TODAY, 14, READ, "leakage guard"),
    (
        "15 configuration refused",
        True,
        _second_configuration_refuses,
        "take",
        TODAY,
        15,
        LOOKED,
        "the driver refused",
    ),
    ("1 crash in printing", True, _printing_fails, "take", TODAY, 1, READ, "printer fell over"),
    ("0 take", True, None, "take", TODAY, 0, READ, "champion: none"),
    ("0 check", True, None, "check", TODAY, 0, UNTOUCHED, "the gates that need no key"),
    ("3 check", True, _dirty, "check", TODAY, 3, UNTOUCHED, "the tree is dirty"),
]


# The exits that are decided before any read: they need a repository, not a store.
_PRE_READ = set(range(3, 10))


@pytest.mark.parametrize(
    ("key", "setup", "command", "today", "code", "state", "phrase"),
    [row[1:] for row in _EXITS],
    ids=[row[0] for row in _EXITS],
)
def test_every_exit_says_what_the_record_holds_about_the_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    key: bool,
    setup: Setup | None,
    command: str,
    today: dt.date,
    code: int,
    state: reveal.SealState,
    phrase: str,
) -> None:
    """The last line is the record's, not the exit code's: the same sentence is asserted against
    an independent reading of the log, and against the state this row says the world is in. A
    renumbered exit cannot make the sentence wrong, because no exit decides it."""
    world = build_world(
        tmp_path, monkeypatch, key=key, with_store=command == "take" and code not in _PRE_READ
    )
    if setup is not None:
        setup(world, monkeypatch)

    got = run(world, command, today=today)
    captured = capsys.readouterr()

    assert got == code
    assert phrase in captured.out + captured.err
    looks = sum(1 for r in records(world) if r["kind"] == "look_open")
    if state in (COUNTED, LOOKED):
        assert (looks > 0) == (state is LOOKED), "the row's state agrees with the looks on disk"
    assert last_line(captured.out) == said(state, looks)
    assert last_line(captured.out) == expected_sentence(world)
    # The other four worlds' wording does not appear anywhere in this run's output.
    others = [said(other, looks) for other in reveal.SEAL_SENTENCES if other is not state]
    assert not [s for s in others if s in captured.out]
    if code in range(3, 10):
        assert not records(world), "a pre-read refusal writes nothing at all"
    if code == 1:
        assert "Traceback" in captured.err
    # The unlock variable is never in the environment, before or after.
    assert os.environ.get("VOLAB_HOLDOUT_UNLOCK") is None


def test_an_attempt_that_took_looks_says_so_and_names_how_many(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """R220. ``reveal_open`` with look lines and no completion is not "nothing was read". The
    wording is spelled out here and not read back from ``SEAL_SENTENCES``, so a swap of the two
    sentences cannot pass, and the number is compared with the log's own count."""
    world = build_world(tmp_path, monkeypatch, key=True, with_store=True)
    _canary_trips(world, monkeypatch)

    assert run(world, "take", today=TODAY) == 12
    line = last_line(capsys.readouterr().out)

    taken = sum(1 for r in records(world) if r["kind"] == "look_open")
    assert taken > 0
    assert not [r for r in records(world) if r["kind"] == "reveal_complete"]
    assert f"the log holds {taken} sealed looks" in line
    assert "holdout data has been seen" in line
    assert "not a first look" in line
    assert "nothing was read" not in line


def test_an_attempt_that_took_no_looks_says_nothing_was_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = build_world(tmp_path, monkeypatch, key=False, with_store=True)

    assert run(world, "take", today=TODAY) == 10
    line = last_line(capsys.readouterr().out)

    assert [r for r in records(world) if r["kind"] == "reveal_open"]
    assert not [r for r in records(world) if r["kind"] == "look_open"]
    assert "nothing was read" in line
    assert "sealed looks" not in line
    assert "not a first look" not in line


def test_usage_errors_are_argparses_own_and_say_the_seal_is_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = build_world(tmp_path, monkeypatch, with_store=False)

    code = run(world, "run")
    captured = capsys.readouterr()

    assert code == reveal.USAGE == 2
    assert "invalid choice" in captured.err
    assert last_line(captured.out) == reveal.SEAL_SENTENCES[UNTOUCHED]
    assert not records(world)


# ---- W12, W10, W11 ------------------------------------------------------------------------------


def test_a_missing_key_costs_an_attempt_and_no_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = build_world(tmp_path, monkeypatch, key=False)

    code = run(world, "take")
    capsys.readouterr()
    tally = store.audit_tally(world.settings)

    assert code == 10
    assert [r["kind"] for r in records(world)] == ["reveal_open"]
    assert (tally.looks, len(tally.attempts), tally.completed) == (0, 1, frozenset())


def test_a_crash_during_scoring_leaves_an_open_with_no_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = build_world(tmp_path, monkeypatch)
    _scoring_crashes(world, monkeypatch)

    code = run(world, "take")
    capsys.readouterr()
    tally = store.audit_tally(world.settings)
    text = world.audit().read_text(encoding="utf-8")

    assert code == 1
    assert [r["kind"] for r in records(world)].count("reveal_open") == 1
    assert "reveal_complete" not in text
    assert tally.looks == tally.looks_closed == reveal.expected_looks(world.settings)
    # The assertion that matters: a crashed attempt lifts nothing.
    assert store._completed_reveal_in(text, world.settings.splits) is False


def test_a_second_attempt_needs_a_commit_first_and_is_a_new_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = build_world(tmp_path, monkeypatch)
    splits = world.settings.splits
    with monkeypatch.context() as crashing:
        _scoring_crashes(world, crashing)
        assert run(world, "take") == 1
    capsys.readouterr()

    # The log the crash wrote dirtied the tree, so the next take is refused: not a defect to be
    # narrowed away, but the mechanism that makes every attempt a commit.
    assert run(world, "take") == 3
    refused = capsys.readouterr().out
    assert "the tree is dirty" in refused
    assert len([r for r in records(world) if r["kind"] == "reveal_open"]) == 1

    tracked = [
        p.relative_to(world.root).as_posix()
        for p in (world.audit(), *(world.root.joinpath("data", n) for n in ("trials.jsonl",)))
        if p.exists()
    ]
    git(world.root, "add", "-f", *tracked)
    git(world.root, "commit", "-q", "-m", "attempt 1 crashed")
    assert store._completed_reveal_in(world.audit().read_text(encoding="utf-8"), splits) is False

    assert run(world, "take") == 0
    capsys.readouterr()
    text = world.audit().read_text(encoding="utf-8")
    tally = store.audit_tally(world.settings)

    assert len(tally.attempts) == len(set(tally.attempts)) == 2
    assert tally.completed == frozenset({tally.attempts[1]})
    assert store._completed_reveal_in(text, splits) is True
    opens = [r for r in records(world) if r["kind"] == "reveal_open"]
    assert opens[1]["details"]["trials_at_read"] == count_trials(world.root) - 18


def test_the_registered_pair_is_read_from_its_two_files_alone_and_the_superset_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """HR-9. Agent trials (under ``tmp_path``) move the superset and never the registered pair."""
    world = build_world(tmp_path, monkeypatch)
    agent = world.root / "data" / "agent_trials.jsonl"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text('{"model": "m"}\n' * 5, encoding="utf-8")
    # Committed, so the rehearsal's tree is clean and the dirtiness gate stays out of the way.
    git(world.root, "add", "-f", "data/agent_trials.jsonl")
    git(world.root, "commit", "-q", "-m", "agent trials")
    assert run(world, "take") == 0
    out = capsys.readouterr().out

    assert "trials: registered 414, at read 0 (delta -414), now 18" in out
    assert (
        f"trials, superset across all trial ledgers ({reveal.SUPERSET_REASON}): "
        "at read 5 (delta -409 against the registered 414), now 23"
    ) in out
    assert "agent ledgers: 5 agent trial(s) in data/agent_trials.jsonl" in out
    # The label is spelled out here and not only read back from the constant: an emptied constant
    # would still match its own f-string.
    assert "superset" in out and "R139" in out and "agent trials are trials" in out
    opened = next(r for r in records(world) if r["kind"] == "reveal_open")["details"]
    assert (opened["trials_at_read"], opened["all_ledgers_at_read"]) == (0, 5)
    assert (opened["trials_delta"], opened["all_ledgers_delta"]) == (-414, -409)
    assert reveal.SUPERSET_REASON in opened["all_ledgers_note"]
    # The reveal wrote nothing to an agent ledger.
    assert agent.read_text(encoding="utf-8") == '{"model": "m"}\n' * 5
    assert not world.root.joinpath("data", "agent_comparisons.jsonl").exists()


def test_every_comparison_row_carries_the_last_origin_of_its_trials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """HR-9. The agent's R208 filter then discriminates by date rather than excluding the row."""
    world, code, _ = _completed(tmp_path, monkeypatch, capsys)
    raw = [
        json.loads(line)
        for line in world.root.joinpath("data", "trials.jsonl").read_text("utf-8").splitlines()
    ]
    trial_origin = {
        (t["symbol"], t["horizon"], t["loss"], t["cohort"]): t["last_origin"] for t in raw
    }
    rows = read_comparisons(world.root)

    assert code == 0 and len(rows) == 6
    for row in rows:
        assert (
            row["last_origin"]
            == trial_origin[(row["symbol"], row["horizon"], row["loss"], row["cohort"])]
        )


# ---- W22: check writes nothing ------------------------------------------------------------------


def _tree_state(root: Path) -> dict[str, tuple[int, int]]:
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


@pytest.mark.parametrize("failing", [False, True], ids=["every gate passes", "a gate fails"])
def test_check_writes_nothing_at_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failing: bool,
) -> None:
    world = build_world(tmp_path, monkeypatch)
    if failing:
        _dirty(world, monkeypatch)
    before = _tree_state(world.root)

    code = run(world, "check")
    capsys.readouterr()

    assert code == (3 if failing else 0)
    assert _tree_state(world.root) == before
    assert not world.audit().exists()
    assert not world.root.joinpath("data", "trials.jsonl").exists()
    assert not world.root.joinpath("data", "comparisons.jsonl").exists()


def test_the_registered_runs_are_the_ones_a_real_take_would_make() -> None:
    """Nothing the command line can reach replaces the cohort runs: the knobs are module
    constants, and ``main`` takes only a verb."""
    assert reveal.RUNS[0][0] is SHOOTOUT
    assert reveal.MINIMUM_TRAINING == 250 and reveal.STRIDE == 1


# ---- check does not imply a readiness it cannot verify ----------------------------------------

_READ_GATES = ("keys_not_held", "window_not_ingested", "canary_disagrees")


def test_a_green_check_names_the_three_gates_only_a_read_can_find_and_never_says_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = build_world(tmp_path, monkeypatch)

    code = run(world, "check")
    out = capsys.readouterr().out

    assert code == 0
    for name in _READ_GATES:
        assert name in out, f"the green path must say {name} was not checked"
    assert "not checked" in out and "counted attempt" in out
    assert "ready" not in out.lower()
    # Raw reaches holdout_end in this world, so it does not forecast the gate firing.
    assert "not expected to fire" in out


def test_a_green_check_forecasts_window_not_ingested_from_the_bars_the_gate_would_see(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = build_world(tmp_path, monkeypatch)
    _short_window(world, monkeypatch)
    later = dt.date(2024, 4, 22)
    holdout_end = world.settings.splits.holdout_end

    code = run(world, "check", today=later)
    out = capsys.readouterr().out

    assert code == 0, "the forecast is not a gate: check still exits 0"
    assert "window_not_ingested is expected to fire" in out
    assert "latest raw bars session is 2024-04-12" in out
    assert f"holdout_end {holdout_end}" in out
    assert "may therefore cost a counted attempt" in out
    assert "ready" not in out.lower()
    assert not world.audit().exists()


def test_a_refused_configuration_exits_15_and_keeps_the_trials_already_appended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An expected outcome of a legitimate run is not exit 1. The message names the configuration
    that refused and says the ledger keeps what was appended; the seal line is still the record's
    and is not special-cased."""
    world = build_world(tmp_path, monkeypatch)
    _second_configuration_refuses(world, monkeypatch)
    root = world.root
    before = count_trials(root)

    code = run(world, "take")
    captured = capsys.readouterr()

    symbol, horizon, cohort = world.refusals[1]  # type: ignore[attr-defined]
    assert code == 15
    assert "Traceback" not in captured.err
    assert f"{cohort} {symbol} h{horizon}" in captured.out
    assert "stay on the ledger" in captured.out
    kept = count_trials(root) - before
    assert kept > 0, "the first configuration's trials were appended and are not withdrawn"
    assert last_line(captured.out) == expected_sentence(world)
