"""The leaderboard: a reader of the two ledgers that computes nothing of its own (US-3.5).

``python -m volab.leaderboard`` renders ``data/trials.jsonl`` (the losses) and
``data/comparisons.jsonl`` (MCS, SPA and DM). If it recomputed a mean or a ranking statistic it
could disagree with the thing it claims to report, and there would be two numbers and no way to
know which was the result. So these tests do not check statistics -- ``test_compare.py`` does --
they check that **what is on the ledgers is what is printed**, and that the five things the
project most cares about are never dropped on the way:

* the headline trial count is every ledger line, with the cohort breakdown beside it (R59);
* a line with no ``cohort`` key is ``phase2-baseline`` (R86), tested against the committed 72;
* a refusal, a withheld claim and a dirty tree each print as themselves (R94, R108, R70);
* a Monte-Carlo error never prints without the series it was measured on (R102);
* nothing is ever written (R95, R109).

The synthetic ledgers are written by the **real writers** (``JsonlSink``), not typed by hand: a
fixture typed from memory encodes the author's guess about the keys, and the reader would pass
against it while failing on the file the sink actually writes. Nothing here touches the real
``data/`` except one read-only test, and no test sets ``allow_holdout``.
"""

from __future__ import annotations

import ast
import datetime as dt
import functools
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import volab.compare.multiple as multiple
import volab.compare.sink as sink
import volab.leaderboard as leaderboard
import volab.leaderboard.__main__ as cli
from volab.compare import analyse
from volab.compare.series import loss_series
from volab.compare.sink import JsonlSink
from volab.errors import VolabError
from volab.walkforward import (
    BASELINE_COHORT_NAME,
    LOSSES,
    TRIAL_LEDGER,
    Evaluation,
    OriginResult,
    count_trials,
)

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "volab" / "leaderboard"
SOURCE = "synthetic probe series, not SPY"
# The series ``_evaluation`` compares by default, which is what ``compare.series_source`` names: a
# figure carrying exactly this source is the only kind that can authorise a claim (R102).
ON_SERIES = "SPY h22"
AT = dt.datetime(2026, 10, 27, 9, 30, tzinfo=dt.UTC)
LATER = dt.datetime(2026, 11, 3, 9, 30, tzinfo=dt.UTC)
BASELINE = ("har_levels", "har_semilog", "random_walk")


def _evaluation(
    *,
    symbol: str = "SPY",
    horizon: int = 22,
    n: int = 40,
    every: int = 5,
    cohort: str = "unit",
    extra: tuple[str, ...] = ("rival",),
) -> Evaluation:
    """``n`` origins; contestant ``j`` forecasts the truth ``10 * (j + 1)`` percent too high, so
    the ranking on either loss is the contestant order and a verdict is there to be stated."""
    rng = np.random.default_rng(21)
    names = (*BASELINE, *extra)
    origins = [dt.date(2021, 1, 4) + dt.timedelta(days=7 * i) for i in range(n)]
    truth = rng.uniform(1.0, 2.0, size=n)
    results = tuple(
        OriginResult(
            origin=origins[i],
            realized=float(truth[i]),
            forecasts={
                name: float(truth[i] * (1 + 0.1 * (j + 1)) + 0.05 * rng.normal())
                for j, name in enumerate(names)
            },
            training_rows=100,
        )
        for i in range(n)
    )
    return Evaluation(
        symbol=symbol,
        horizon=horizon,
        results=results,
        features=pd.DataFrame(),
        skipped=(),
        requested=n,
        every=every,
        contestants=names,
        cohort=cohort,
    )


@functools.cache
def _comparisons(
    symbol: str,
    horizon: int,
    n: int,
    cohort: str,
    errors: tuple[tuple[str, float | None], ...],
    source: str = SOURCE,
):
    """The comparison for each loss, cached: the bootstrap is slow and is not what is tested."""
    evaluation = _evaluation(symbol=symbol, horizon=horizon, n=n, cohort=cohort)
    return evaluation, [
        analyse(
            loss_series(evaluation, loss, cohort=cohort),
            reps=200,
            monte_carlo_error=dict(errors) or None,
            monte_carlo_source=source if errors else None,
        )
        for loss in sorted(LOSSES)
    ]


def _run(
    root: Path,
    *,
    symbol: str = "SPY",
    horizon: int = 22,
    n: int = 40,
    cohort: str = "unit",
    at: dt.datetime = AT,
    sha: str = "abcdef0123456789",
    dirty: bool | None = False,
    errors: dict[str, float | None] | None = None,
    source: str = SOURCE,
    comparisons: bool = True,
) -> None:
    """One run through the real sink: trials and (optionally) comparisons under one join key."""
    evaluation, records = _comparisons(
        symbol, horizon, n, cohort, tuple((errors or {}).items()), source
    )
    writer = JsonlSink(root)
    writer.append_trials(evaluation.trials(), at=at, sha=sha, dirty=dirty)
    if comparisons:
        writer.append_comparisons(records, at=at, sha=sha, dirty=dirty)


def _text(root: Path) -> str:
    lines, _consistent = leaderboard.render(root)
    return "\n".join(lines)


def _strip_cohort(root: Path) -> None:
    """Rewrite a ledger as it was before the ``cohort`` key existed (the committed 72 lines)."""
    path = root / TRIAL_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row.pop("cohort", None)
        row.pop("refused", None)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _rows(text: str, loss: str) -> list[tuple[str, str, str]]:
    """``(rank, model, value)`` for the first ranking of ``loss`` in ``text``."""
    out: list[tuple[str, str, str]] = []
    inside = False
    for line in text.splitlines():
        if not inside:
            inside = bool(re.match(rf"^  {loss} \(lower is better\)", line))
        elif match := re.match(r"^\s+(\d+|--)\.\s+(\S+)\s+(\S+)", line):
            out.append((match.group(1), match.group(2), match.group(3)))
        else:
            break
    return out


# ---------------------------------------------------------------------------
# The headline: every line, and the cohort breakdown beside it (R59)
# ---------------------------------------------------------------------------


def test_the_headline_is_every_ledger_line_with_the_cohort_breakdown_beside_it(tmp_path) -> None:
    _run(tmp_path, cohort="alpha")
    _run(tmp_path, cohort="beta", at=LATER, horizon=5)
    text = _text(tmp_path)
    total = count_trials(tmp_path)
    assert total == 16, "two runs of four contestants on two losses"
    assert f"HEADLINE: {total} trial(s) on the ledger" in text
    assert "by cohort: alpha 8, beta 8" in text


def test_adding_a_cohort_cannot_reset_the_headline(tmp_path) -> None:
    """R59: a number that restarts per cohort is a number that hides the earlier cohorts' trials."""
    _run(tmp_path, cohort="alpha")
    before = count_trials(tmp_path)
    assert f"HEADLINE: {before} trial(s)" in _text(tmp_path)

    _run(tmp_path, cohort="beta", at=LATER)
    after = count_trials(tmp_path)
    assert after == before * 2
    text = _text(tmp_path)
    assert f"HEADLINE: {after} trial(s)" in text
    assert f"HEADLINE: {before} trial(s)" not in text


def test_every_ranking_carries_the_ledger_wide_count_not_its_own(tmp_path) -> None:
    """ "The trial count beside it": a ranking of four contestants on one loss is supported by
    every trial the project ran, and says so in its own header."""
    _run(tmp_path, cohort="alpha")
    _run(tmp_path, cohort="beta", at=LATER)
    headers = [
        line
        for line in _text(tmp_path).splitlines()
        if re.match(r"^  \w+ \(lower is better\)", line)
    ]
    assert len(headers) == 2 * len(LOSSES)
    for header in headers:
        assert "16 trial(s) on the ledger" in header and "alpha 8, beta 8" in header, header


def test_the_breakdown_always_sums_to_the_headline(tmp_path) -> None:
    for cohort, at in (("a", AT), ("b", LATER), ("c", LATER + dt.timedelta(days=1))):
        _run(tmp_path, cohort=cohort, at=at)
    text = _text(tmp_path)
    breakdown = re.search(r"by cohort: (.*)", text)
    assert breakdown is not None
    counts = [int(part.rsplit(" ", 1)[1]) for part in breakdown.group(1).split(", ")]
    assert sum(counts) == count_trials(tmp_path)


def test_the_headline_counts_a_trial_whose_value_is_undefined(tmp_path) -> None:
    """A configuration that produced nothing is still a trial (``Evaluation.trials``); a leaderboard
    that skipped its null value would undercount the one number the write-up reports."""
    _run(tmp_path)
    path = tmp_path / TRIAL_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["value"] = None
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    text = _text(tmp_path)
    assert f"HEADLINE: {len(rows)} trial(s)" in text
    ranked = {model: (rank, value) for rank, model, value in _rows(text, rows[0]["loss"])}
    assert ranked[rows[0]["model"]] == ("--", "n/a"), "printed unranked, not omitted"


# ---------------------------------------------------------------------------
# A line with no ``cohort`` key is the baseline (R86)
# ---------------------------------------------------------------------------


def test_a_line_with_no_cohort_key_is_the_baseline_cohort(tmp_path) -> None:
    _run(tmp_path, cohort="alpha", comparisons=False)
    _strip_cohort(tmp_path)
    stored = (tmp_path / TRIAL_LEDGER).read_text(encoding="utf-8").splitlines()
    assert all("cohort" not in json.loads(line) for line in stored)
    text = _text(tmp_path)
    assert f"by cohort: {BASELINE_COHORT_NAME} 8" in text
    assert "8 line(s) carry no cohort key" in text
    assert f"[{BASELINE_COHORT_NAME}]" in text


def test_the_committed_ledger_reads_correctly_and_is_not_touched() -> None:
    """The real pre-cohort lines, read-only. The count is deliberately **not** pinned, because
    shootouts keep appending: 72 baseline, then 42 from the crashed run, then 150 and 150 from the
    two full runs, so the file is at 414 and will grow again. What is pinned is that **every line
    with no ``cohort`` key** is attributed to ``phase2-baseline`` and none is lost.

    The original said "the next story appends 168", which R114 made unreachable before any story
    did it (R129). Pinning a predicted total here would have been the wrong witness anyway: the
    thing worth protecting is the attribution rule, not the arithmetic of one planned run."""
    ledger = REPO / TRIAL_LEDGER
    before = ledger.read_bytes()
    rows = [json.loads(line) for line in before.decode().splitlines() if line.strip()]
    legacy = [row for row in rows if "cohort" not in row]
    assert legacy, "the committed ledger no longer holds the pre-cohort lines this test is for"

    lines, consistent = leaderboard.render(REPO)
    text = "\n".join(lines)
    assert consistent
    assert f"HEADLINE: {count_trials(REPO)} trial(s) on the ledger" in text
    counts = re.search(r"by cohort: (.*)", text)
    assert counts is not None
    by_cohort = {
        part.rsplit(" ", 1)[0]: int(part.rsplit(" ", 1)[1]) for part in counts.group(1).split(", ")
    }
    assert sum(by_cohort.values()) == len(rows)
    assert by_cohort[BASELINE_COHORT_NAME] == len(legacy) + sum(
        row.get("cohort") == BASELINE_COHORT_NAME for row in rows
    )
    assert f"{len(legacy)} line(s) carry no cohort key" in text

    # Every legacy (symbol, horizon, loss) ranks its three contestants, with no comparison record:
    # no comparison was ever written for them, and the leaderboard says so instead of omitting it.
    keys = {(row["symbol"], row["horizon"], row["loss"]) for row in legacy}
    assert len(keys) == 24
    blocks = {
        (m.group(1), int(m.group(2))): block
        for block in text.split("\n== ")[1:]
        if (m := re.match(rf"(\w+) h(\d+) \[{BASELINE_COHORT_NAME}\]", block))
    }
    for symbol, horizon, loss in keys:
        ranked = _rows(blocks[(symbol, horizon)], loss)
        expected = {
            r["model"]
            for r in legacy
            if (r["symbol"], r["horizon"], r["loss"]) == (symbol, horizon, loss)
        }
        assert {model for _rank, model, _value in ranked} == expected and len(ranked) == 3
    assert text.count("no comparison record") >= 12
    assert "above the floor" not in text, "R123: that reason was false for every record printed"
    for row in legacy:
        assert row["model"] in text
    assert ledger.read_bytes() == before, "the leaderboard changed the committed ledger"


def test_a_value_is_printed_as_the_ledger_holds_it(tmp_path) -> None:
    """Rendering only: each printed value is the ledger's own, to the digits printed."""
    _run(tmp_path, comparisons=False)
    text = _text(tmp_path)
    for line in (tmp_path / TRIAL_LEDGER).read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        printed = {m: v for _r, m, v in _rows(text, row["loss"])}
        assert float(printed[row["model"]]) == float(format(row["value"], ".6g"))


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_contestants_are_ranked_best_loss_first_on_each_loss(tmp_path) -> None:
    _run(tmp_path, comparisons=False)
    text = _text(tmp_path)
    for loss in LOSSES:
        ranked = _rows(text, loss)
        assert [rank for rank, _m, _v in ranked] == ["1", "2", "3", "4"]
        values = [float(value) for _r, _m, value in ranked]
        assert values == sorted(values)
        assert [model for _r, model, _v in ranked] == ["har_levels", *BASELINE[1:], "rival"]


def test_equal_losses_share_a_rank_rather_than_inventing_an_order(tmp_path) -> None:
    _run(tmp_path, comparisons=False)
    path = tmp_path / TRIAL_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    mse = [row for row in rows if row["loss"] == "mse"]
    mse[1]["value"] = mse[0]["value"]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert [rank for rank, _m, _v in _rows(_text(tmp_path), "mse")][:3] == ["1", "1", "3"]


def test_a_loss_the_leaderboard_cannot_orient_is_refused_not_guessed(tmp_path) -> None:
    """Every known loss is lower-is-better; an unknown one has no stated direction."""
    _run(tmp_path, comparisons=False)
    path = tmp_path / TRIAL_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["loss"] = "hit_rate"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(leaderboard.LeaderboardError, match="hit_rate"):
        leaderboard.render(tmp_path)


def test_runs_of_one_configuration_are_ranked_apart(tmp_path) -> None:
    """The join key includes ``at`` and the cohort: a baseline trial and a shootout trial of the
    same model are different experiments (R86), so they are not ranked against each other."""
    _run(tmp_path, cohort="alpha")
    _run(tmp_path, cohort="beta", at=LATER)
    text = _text(tmp_path)
    assert len(re.findall(r"^== SPY h22 \[alpha\]", text, re.M)) == 1
    assert len(re.findall(r"^== SPY h22 \[beta\]", text, re.M)) == 1
    assert [len(_rows(text, loss)) for loss in LOSSES] == [4, 4]


def test_groups_are_ordered_by_symbol_then_horizon(tmp_path) -> None:
    _run(tmp_path, symbol="QQQ", horizon=22)
    _run(tmp_path, symbol="IWM", horizon=22)
    _run(tmp_path, symbol="IWM", horizon=5)
    heads = re.findall(r"^== (\w+) h(\d+)", _text(tmp_path), re.M)
    assert heads == [("IWM", "5"), ("IWM", "22"), ("QQQ", "22")]


# ---------------------------------------------------------------------------
# Dirty trees (R70)
# ---------------------------------------------------------------------------


def test_a_trial_from_a_dirty_tree_is_marked_and_a_clean_one_is_not(tmp_path) -> None:
    _run(tmp_path, dirty=True, sha="dirty000", comparisons=False)
    _run(tmp_path, dirty=False, sha="clean000", at=LATER, comparisons=False)
    text = _text(tmp_path)
    dirty_group = text.split("sha clean000")[0]
    clean_group = text.split("sha clean000")[1]
    assert "DIRTY TREE" in dirty_group and "DIRTY TREE" not in clean_group
    assert "8 of 16 trial(s) were produced on a dirty tree" in text


def test_an_unknown_tree_state_is_not_reported_as_clean(tmp_path) -> None:
    _run(tmp_path, dirty=None, comparisons=False)
    text = _text(tmp_path)
    assert "tree state unknown" in text and "DIRTY TREE" not in text
    assert "8 of 8 trial(s) have an unknown tree state" in text


def test_a_clean_ledger_makes_no_dirty_claim(tmp_path) -> None:
    _run(tmp_path, comparisons=False)
    text = _text(tmp_path)
    assert "dirty" not in text.lower() and "unknown" not in text.lower()


# ---------------------------------------------------------------------------
# MCS, with n_eff, and a refusal printed as a refusal (R94)
# ---------------------------------------------------------------------------


def test_mcs_membership_is_printed_at_both_sizes_with_n_eff(tmp_path) -> None:
    _run(tmp_path, n=100)  # h=22 at stride 5: q=4, n_eff = 100/5 = 20
    text = _text(tmp_path)
    stored = [
        json.loads(line)
        for line in (tmp_path / sink.COMPARISON_LEDGER).read_text(encoding="utf-8").splitlines()
    ]
    for record in stored:
        assert record["n_eff"] == 20.0
        for size, entry in record["mcs"]["sizes"].items():
            assert "members" in entry, "the fixture must have both sizes answer"
            line = next(
                x for x in text.splitlines() if x.strip().startswith(f"MCS {float(size):.0%}:")
            )
            assert ", ".join(entry["members"]) in line and "n_eff=20.0" in line
    assert text.count("MCS 10%:") == len(LOSSES) and text.count("MCS 25%:") == len(LOSSES)


def test_a_refused_mcs_is_printed_with_its_reason_not_omitted(tmp_path) -> None:
    """n=40 at q=4 is n_eff=8: the 25% set answers and the 10% set is refused. An absent 10% row
    would read as "no result"; a printed refusal reads as "we declined to claim"."""
    _run(tmp_path, n=40)
    text = _text(tmp_path)
    stored = json.loads(
        (tmp_path / sink.COMPARISON_LEDGER).read_text(encoding="utf-8").splitlines()[0]
    )
    refusal = stored["mcs"]["sizes"]["0.1"]["refused"]
    assert refusal and "members" in stored["mcs"]["sizes"]["0.25"]
    lines = [x.strip() for x in text.splitlines() if x.strip().startswith("MCS 10%:")]
    assert len(lines) == len(LOSSES)
    for line in lines:
        assert "REFUSED" in line and refusal in line and "n_eff=8.0" in line, line
    assert text.count("MCS 25%:") == len(LOSSES)


def test_a_size_the_record_does_not_hold_is_printed_as_not_recorded(tmp_path) -> None:
    _run(tmp_path)
    path = tmp_path / sink.COMPARISON_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        del row["mcs"]["sizes"]["0.25"]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    text = _text(tmp_path)
    assert text.count("MCS 25%: not recorded") == len(LOSSES)


def test_the_sizes_the_leaderboard_expects_are_the_ones_the_comparison_runs() -> None:
    assert tuple(f"{size:g}" for size in multiple.SIZES) == leaderboard.MCS_SIZES


def test_a_whole_record_refusal_is_printed(tmp_path) -> None:
    _run(tmp_path)
    path = tmp_path / sink.COMPARISON_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["refusal"] = "no origin is comparable on this loss: nothing to compare"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    text = _text(tmp_path)
    assert text.count("REFUSED: no origin is comparable") == len(LOSSES)


def test_a_run_with_no_comparison_says_so_rather_than_printing_nothing(tmp_path) -> None:
    _run(tmp_path, comparisons=False)
    text = _text(tmp_path)
    assert text.count("no comparison record") == len(LOSSES)
    assert "MCS 10%:" not in text and "SPA (benchmark" not in text and " vs " not in text


# ---------------------------------------------------------------------------
# Withheld claims (R94, R108): shown as withheld, never silently ranked as significant
# ---------------------------------------------------------------------------


def _pair_lines(text: str) -> list[str]:
    return [x for x in text.splitlines() if re.match(r"^\s+\w+ vs \w+:", x)]


def test_a_thin_sample_withholds_dm_and_spa_and_says_why(tmp_path) -> None:
    _run(tmp_path, n=40)
    text = _text(tmp_path)
    pairs = _pair_lines(text)
    assert len(pairs) == 6 * len(LOSSES)
    for line in pairs:
        assert "WITHHELD" in line and "n_eff=8.0" in line, line
        assert "significantly" not in line and "=>" not in line, line
    spa = [x for x in text.splitlines() if x.strip().startswith("SPA")]
    assert len(spa) == 2 * len(LOSSES)
    for line in spa:
        assert "WITHHELD" in line and "n_eff=8.0" in line, line


def test_a_supported_sample_states_the_stored_verdict(tmp_path) -> None:
    _run(tmp_path, n=100)
    text = _text(tmp_path)
    pairs = _pair_lines(text)
    assert pairs and not any("WITHHELD" in line for line in pairs)
    stored = [
        json.loads(line)
        for line in (tmp_path / sink.COMPARISON_LEDGER).read_text(encoding="utf-8").splitlines()
    ]
    verdicts = [
        (pair["left"], pair["right"], pair["headline"]["verdict"])
        for record in stored
        for pair in record["dm"]
    ]
    assert any(v != 0 for _l, _r, v in verdicts), "the fixture must hold a significant pair"
    for left, right, verdict in verdicts:
        line = next(x for x in pairs if f"{left} vs {right}:" in x)
        if verdict == 0:
            assert "no significant difference at 5%" in line
        else:
            better = left if verdict == -1 else right
            assert f"{better} significantly better at 5%" in line


def test_a_pair_the_record_does_not_claim_is_withheld_even_on_a_thick_sample(tmp_path) -> None:
    """The leaderboard reads the per-pair ``claimed`` flag; it does not re-derive it. A stored
    ``claimed: false`` -- here because the columns disagree -- is withheld whatever the statistic
    says, and a stored verdict is not stated."""
    _run(tmp_path, n=100)
    path = tmp_path / sink.COMPARISON_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        for pair in row["dm"]:
            pair["claimed"] = False
            pair["disagree"] = True
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    pairs = _pair_lines(_text(tmp_path))
    assert pairs
    for line in pairs:
        assert "WITHHELD" in line and "columns disagree" in line, line
        assert "significantly" not in line


def test_a_contestant_above_the_error_floor_is_withheld_and_marked_wherever_it_appears(
    tmp_path,
) -> None:
    _run(tmp_path, n=100, errors={"rival": 3.273})
    text = _text(tmp_path)
    rival_pairs = [x for x in _pair_lines(text) if "rival" in x]
    assert len(rival_pairs) == 3 * len(LOSSES)
    for line in rival_pairs:
        assert "WITHHELD" in line and "rival" in line and "Monte Carlo error" in line, line
        assert "3.273%" in line and SOURCE in line
        # The fixture's source is another series, so the cause is source, whatever the value (R123).
        assert "measured on another series" in line, line
    clean = [x for x in _pair_lines(text) if "rival" not in x]
    assert clean and not any("WITHHELD" in line for line in clean)
    spa = [x for x in text.splitlines() if x.strip().startswith("SPA")]
    assert spa and all("WITHHELD" in line for line in spa), "SPA tests the whole cohort"
    assert all("no claim" in row for row in text.splitlines() if "rival" in row and "3.273" in row)


def test_a_claimed_spa_prints_its_p_values_and_the_verdict_the_record_stores(tmp_path) -> None:
    """The sink stores SPA's 5% reading beside its p-values, so the line states what the record
    states. Before US-3.8 the record held no reading and the line said only "claim permitted",
    because reading the p-value against 0.05 here would have been recomputing the claim."""
    _run(tmp_path, n=100)
    text = _text(tmp_path)
    stored = [
        json.loads(line)
        for line in (tmp_path / sink.COMPARISON_LEDGER).read_text(encoding="utf-8").splitlines()
    ]
    spa = [x for x in text.splitlines() if x.strip().startswith("SPA")]
    assert len(spa) == 2 * len(LOSSES)
    for line in spa:
        assert "WITHHELD" not in line and "n_eff=20.0" in line
    stored_spa = [entry for record in stored for entry in record["spa"]]
    assert all(entry["claimed"] and entry["statement"] for entry in stored_spa)
    for line, entry in zip(spa, stored_spa, strict=True):
        assert f"consistent={entry['consistent']:.4f}" in line
        assert line.endswith(f"  => {entry['statement']}"), line


def test_the_spa_reading_printed_is_the_stored_one_not_a_rereading_of_the_p_value(
    tmp_path,
) -> None:
    """Planted defect: change only the stored statement. A leaderboard that read the p-value
    against 0.05 would not notice."""
    _run(tmp_path, n=100)
    path = tmp_path / sink.COMPARISON_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        for entry in row["spa"]:
            entry["statement"] = "STORED-SPA-READING"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    spa = [x for x in _text(tmp_path).splitlines() if x.strip().startswith("SPA")]
    assert spa and all(line.endswith("  => STORED-SPA-READING") for line in spa)


def test_a_claimed_spa_whose_reading_is_not_stored_prints_none_of_its_own(tmp_path) -> None:
    """A record from before the reading was stored (the committed ledger's) still permits the
    claim; the line says so and states no reading, rather than deriving one."""
    _run(tmp_path, n=100)
    path = tmp_path / sink.COMPARISON_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        for entry in row["spa"]:
            del entry["statement"]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    spa = [x for x in _text(tmp_path).splitlines() if x.strip().startswith("SPA")]
    assert spa
    for line in spa:
        assert "claim permitted" in line and "=>" not in line and "beats" not in line, line


def test_the_dm_wording_printed_is_the_stored_one_and_the_leaderboard_holds_none(tmp_path) -> None:
    """The wording lives in the sink (``Comparison.pair_verdict``), which ``compare``'s own
    console output also uses. The leaderboard cannot import ``compare`` (see its docstring), so
    it prints what the record stores. Planted defect: change only the stored statement."""
    _run(tmp_path, n=100)
    path = tmp_path / sink.COMPARISON_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert all(pair["statement"] for row in rows for pair in row["dm"] if pair["claimed"])
    for row in rows:
        for pair in row["dm"]:
            pair["statement"] = f"STORED-{pair['left']}-{pair['right']}"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    pairs = _pair_lines(_text(tmp_path))
    assert pairs
    for line in pairs:
        left, right = re.match(r"^\s+(\w+) vs (\w+):", line).groups()  # type: ignore[union-attr]
        assert line.endswith(f"  => STORED-{left}-{right}"), line
    source = (SRC / "__init__.py").read_text(encoding="utf-8")
    for phrase in ("significantly better", "no significant difference", "beats"):
        assert phrase not in source, f"the leaderboard words a verdict itself: {phrase!r}"


def test_a_claimed_pair_whose_statement_is_not_stored_shows_the_stored_code_not_words(
    tmp_path,
) -> None:
    _run(tmp_path, n=100)
    path = tmp_path / sink.COMPARISON_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        for pair in row["dm"]:
            del pair["statement"]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    pairs = _pair_lines(_text(tmp_path))
    assert pairs
    for line in pairs:
        assert "WITHHELD" not in line and "significant" not in line, line
        assert "stored 5% verdict" in line and "predates" in line, line


# ---------------------------------------------------------------------------
# Why a claim is withheld: one stated reason per cause (R123)
# ---------------------------------------------------------------------------

# What a reader is told, per cause. Spelled out here and not imported, so a reworded reason is a
# deliberate edit to this table.
# The figure is not in the sentence: it is printed only beside its source (R102).
ABOVE = "rival's Monte Carlo error is above the 1.5% ceiling"
ELSEWHERE = (
    "rival's Monte Carlo error was measured on another series "
    f"({SOURCE}), not on {ON_SERIES}, so it cannot authorise a claim whatever its value"
)
UNMEASURED = "rival's Monte Carlo error was not measured, so no precision is established"
# (figure, source it was measured on) -> the sentences that must be printed
CAUSES: dict[str, tuple[float | None, str, list[str]]] = {
    "above the ceiling": (3.273, ON_SERIES, [ABOVE]),
    "measured elsewhere, under the ceiling": (0.207, SOURCE, [ELSEWHERE]),
    "not measured": (None, ON_SERIES, [UNMEASURED]),
    # One figure can fire two rules at once; both are stated, not the first one matched.
    "above the ceiling and elsewhere": (3.273, SOURCE, [ABOVE, ELSEWHERE]),
}


def _rival_withheld_lines(text: str) -> list[str]:
    pairs = [x for x in _pair_lines(text) if "rival" in x]
    spa = [x for x in text.splitlines() if x.strip().startswith("SPA")]
    return pairs + spa


@pytest.mark.parametrize("cause", sorted(CAUSES))
def test_each_withheld_reason_states_its_own_cause_and_only_that_one(tmp_path, cause) -> None:
    figure, source, expected = CAUSES[cause]
    _run(tmp_path, n=100, errors={"rival": figure}, source=source)
    text = _text(tmp_path)
    lines = _rival_withheld_lines(text)
    assert len(lines) == 3 * len(LOSSES) + 2 * len(LOSSES)
    everything = {ABOVE, ELSEWHERE, UNMEASURED}
    for line in lines:
        assert "WITHHELD" in line, line
        for sentence in expected:
            assert sentence in line, (cause, line)
        for sentence in everything - set(expected):
            assert sentence not in line, (cause, line)
    assert "above the floor" not in text


def test_a_figure_that_clears_every_rule_is_not_withheld_and_states_no_reason(tmp_path) -> None:
    _run(tmp_path, n=100, errors={"rival": 0.207}, source=ON_SERIES)
    text = _text(tmp_path)
    assert not any("WITHHELD" in line for line in _rival_withheld_lines(text))
    assert "ceiling" not in text
    assert "cannot authorise" not in text and "was not measured" not in text


def test_the_stored_reasons_are_exactly_the_unclaimed_contestants() -> None:
    """The sink restates ``analyse``'s three-way rule to word it, so this is the drift witness: for
    any figure and source, a contestant has a stored reason if and only if it is unclaimed. The
    ceiling itself (1.5) is not above it."""
    evaluation = _evaluation(n=60)
    grid = [
        (figure, source)
        for figure in (None, 0.0, 0.207, 1.5, 1.5001, 3.273)
        for source in (ON_SERIES, SOURCE)
    ]
    for figure, source in grid:
        for loss in sorted(LOSSES):
            comparison = analyse(
                loss_series(evaluation, loss, cohort="unit"),
                reps=50,
                monte_carlo_error={"rival": figure, "har_semilog": 0.1},
                monte_carlo_source=source,
            )
            record = comparison.record()
            reasons = record["unclaimed_reasons"]
            assert isinstance(reasons, dict)
            assert {name for name, why in reasons.items() if why} == set(
                record["unclaimed"]  # type: ignore[arg-type]
            ), (figure, source)
            assert set(reasons) == set(record["unclaimed"]), (figure, source)  # type: ignore[arg-type]
            # And the causes stored are exactly the ones that apply, the boundary included: 1.5
            # is the ceiling and not above it, so on the compared series it states nothing.
            stated = reasons.get("rival", [])
            assert ("was not measured" in " ".join(stated)) == (figure is None), (figure, source)
            assert ("another series" in " ".join(stated)) == (
                figure is not None and source != ON_SERIES
            ), (figure, source)
            assert ("above the 1.5% ceiling" in " ".join(stated)) == (
                figure is not None and figure > 1.5
            ), (figure, source)


def test_a_record_that_stores_no_reasons_says_so_rather_than_naming_a_cause(tmp_path) -> None:
    """The committed ledger's records predate the stored reason, and the cause cannot be recovered
    without re-deriving ``analyse``'s rule here. The old sentence was false for every one of
    them, so it is not the fallback."""
    _run(tmp_path, n=100, errors={"rival": 3.273})
    path = tmp_path / sink.COMPARISON_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        del row["unclaimed_reasons"]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    text = _text(tmp_path)
    lines = _rival_withheld_lines(text)
    assert lines
    for line in lines:
        assert "WITHHELD" in line and "rival" in line, line
        assert "stores no reason" in line, line
        assert "above the floor" not in line and "ceiling" not in line, line


# ---------------------------------------------------------------------------
# The Monte-Carlo error carries its series (R102 and the final ruling)
# ---------------------------------------------------------------------------


def test_a_monte_carlo_error_is_never_printed_without_its_source(tmp_path) -> None:
    _run(tmp_path, n=100, errors={"rival": 0.207})
    text = _text(tmp_path)
    figure = [line for line in text.splitlines() if "0.207%" in line]
    assert figure, "the measured error must reach the output"
    for line in figure:
        assert SOURCE in line, f"a bare figure beside a result: {line!r}"


def test_every_line_that_prints_the_error_names_the_series_it_was_measured_on(tmp_path) -> None:
    _run(tmp_path, n=100, errors={"rival": 3.273, "har_semilog": 0.5})
    for line in _text(tmp_path).splitlines():
        if re.search(r"\d\.\d{3}%", line):
            assert SOURCE in line, line


def test_a_stored_error_with_no_source_is_not_printed_bare(tmp_path) -> None:
    """``analyse`` raises on an error without its source. A record that carries one anyway -- from
    an older writer, or by hand -- must not have that undone at the point of display."""
    _run(tmp_path, n=100, errors={"rival": 3.273})
    path = tmp_path / sink.COMPARISON_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["monte_carlo_error_source"] = None
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    text = _text(tmp_path)
    assert "3.273" not in text
    assert text.count("NOT SHOWN") >= len(LOSSES)
    assert "no source" in text


def test_an_unmeasured_simulated_contestant_is_printed_as_not_measured(tmp_path) -> None:
    evaluation = _evaluation(n=100)
    records = [
        analyse(
            loss_series(evaluation, loss, cohort="unit"),
            reps=200,
            monte_carlo_error={"rival": None},
            monte_carlo_source=SOURCE,
        )
        for loss in sorted(LOSSES)
    ]
    JsonlSink(tmp_path).append_trials(evaluation.trials(), at=AT, sha="abcdef01", dirty=False)
    JsonlSink(tmp_path).append_comparisons(records, at=AT, sha="abcdef01", dirty=False)
    text = _text(tmp_path)
    assert "rival not measured" in text and SOURCE in text


# ---------------------------------------------------------------------------
# Inconsistent ledgers fail loudly
# ---------------------------------------------------------------------------


def test_a_comparison_with_no_trials_is_reported_and_exits_non_zero(tmp_path, capsys) -> None:
    """The sink's docstring: a comparison whose key has no trial is a bug, not a ranking."""
    _run(tmp_path)
    path = tmp_path / sink.COMPARISON_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["git_sha"] = "ffffffffffffffff"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    lines, consistent = leaderboard.render(tmp_path)
    assert not consistent
    assert "ORPHANED COMPARISON" in "\n".join(lines) and "ffffffff" in "\n".join(lines)
    assert cli.main(["--root", str(tmp_path)]) == 1
    assert "ORPHANED COMPARISON" in capsys.readouterr().out


def test_a_malformed_trial_line_raises_rather_than_being_skipped(tmp_path) -> None:
    """Skipping it would undercount the headline."""
    _run(tmp_path, comparisons=False)
    with (tmp_path / TRIAL_LEDGER).open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    with pytest.raises(leaderboard.LeaderboardError, match=r"trials\.jsonl line 9"):
        leaderboard.render(tmp_path)
    assert issubclass(leaderboard.LeaderboardError, VolabError)


def test_the_cli_reports_a_malformed_line_and_exits_1_without_a_traceback(tmp_path, capsys) -> None:
    """``render`` still *raises* -- refuse, never skip -- and the CLI turns that into a report.

    A traceback is the wrong output for a data problem: ``compare``'s own CLI prints the message
    and returns 1, and an operator reading a stack trace cannot tell a corrupt ledger from a bug
    in the reader. The refusal semantics are unchanged; only the presentation is.
    """
    _run(tmp_path, comparisons=False)
    with (tmp_path / TRIAL_LEDGER).open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    assert cli.main(["--root", str(tmp_path)]) == 1
    printed = capsys.readouterr().out
    assert "trials.jsonl line 9" in printed
    assert "Traceback" not in printed


def test_a_trial_line_missing_a_field_names_the_field(tmp_path) -> None:
    _run(tmp_path, comparisons=False)
    path = tmp_path / TRIAL_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    del rows[2]["model"]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(leaderboard.LeaderboardError, match=r"line 3.*model"):
        leaderboard.render(tmp_path)


def test_a_comparison_line_missing_a_field_names_the_field(tmp_path) -> None:
    _run(tmp_path)
    path = tmp_path / sink.COMPARISON_LEDGER
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    del rows[1]["mcs"]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(leaderboard.LeaderboardError, match=r"comparisons\.jsonl line 2.*mcs"):
        leaderboard.render(tmp_path)


def test_blank_lines_are_not_trials(tmp_path) -> None:
    _run(tmp_path, comparisons=False)
    with (tmp_path / TRIAL_LEDGER).open("a", encoding="utf-8") as handle:
        handle.write("\n   \n")
    assert "HEADLINE: 8 trial(s)" in _text(tmp_path)


def test_duplicate_comparison_records_are_all_shown_and_counted(tmp_path) -> None:
    """Two records for one key means one run was appended twice. Choosing one would hide it."""
    _run(tmp_path)
    path = tmp_path / sink.COMPARISON_LEDGER
    path.write_text(path.read_text(encoding="utf-8") * 2, encoding="utf-8")
    text = _text(tmp_path)
    assert "2 comparison records share this key" in text
    assert text.count("MCS 25%:") == 2 * len(LOSSES)


# ---------------------------------------------------------------------------
# Reading only (R95, R109)
# ---------------------------------------------------------------------------

# The spellings that write, create or delete. ``read_text`` is allowed -- it is how the ledgers
# are read -- and bare ``open`` is not, because its mode is invisible to a scan of the call.
WRITING_NAMES = {"open"}
WRITING_ATTRIBUTES = {
    "open",
    "write_text",
    "write_bytes",
    "to_json",
    "to_csv",
    "to_parquet",
    "to_pickle",
    "mkdir",
    "touch",
    "unlink",
    "rename",
    "replace",
    "rmdir",
    "dump",
    "append_trials",
    "append_comparisons",
}


def _writes(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in WRITING_NAMES:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in WRITING_ATTRIBUTES:
            found.append(f".{node.attr}")
    return found


def test_no_leaderboard_module_can_write_a_path() -> None:
    """Structural: ``compare``'s sink is the only writer (R95, R109)."""
    modules = sorted(SRC.glob("*.py"))
    assert {m.name for m in modules} == {"__init__.py", "__main__.py"}
    for module in modules:
        found = _writes(module.read_text(encoding="utf-8"))
        assert not found, f"{module.name} can write ({found}): only the sink may"
    assert ".read_text" in {
        f".{n.attr}"
        for n in ast.walk(ast.parse((SRC / "__init__.py").read_text(encoding="utf-8")))
        if isinstance(n, ast.Attribute)
    }, "the scan is blind: the module no longer reads the way this test expects"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("handle = open('x', 'a')\n", "open"),
        ("path.open('a')\n", ".open"),
        ("path.write_text('x')\n", ".write_text"),
        ("path.write_bytes(b'x')\n", ".write_bytes"),
        ("frame.to_json('x')\n", ".to_json"),
        ("frame.to_csv('x')\n", ".to_csv"),
        ("json.dump(x, handle)\n", ".dump"),
        ("path.parent.mkdir(parents=True)\n", ".mkdir"),
        ("path.touch()\n", ".touch"),
        ("path.unlink()\n", ".unlink"),
        ("sink.append_trials(t)\n", ".append_trials"),
        ("sink.append_comparisons(c)\n", ".append_comparisons"),
    ],
    ids=lambda value: value.strip(),
)
def test_the_write_scan_detects_each_spelling(source: str, expected: str) -> None:
    """The red case: a ban nobody has watched fail is a ban nobody can lean on."""
    assert expected in _writes(source)


def test_the_write_scan_allows_reading() -> None:
    assert _writes("text = path.read_text(encoding='utf-8')\n") == []
    assert _writes('"""opens a file, then write_text and open."""\nx = 1\n') == []


def _listing(root: Path) -> list[tuple[str, int]]:
    return sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*"))


def test_rendering_creates_nothing_where_there_is_no_ledger(tmp_path, capsys) -> None:
    """Behavioural: the tempting bug is a defensive ``mkdir`` or a touch of the comparison file."""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["--root", str(empty)]) == 0
    assert _listing(empty) == []
    assert "HEADLINE: 0 trial(s) on the ledger" in capsys.readouterr().out


def test_rendering_with_trials_and_no_comparison_file_creates_nothing(tmp_path, capsys) -> None:
    _run(tmp_path, comparisons=False)
    before = _listing(tmp_path)
    assert cli.main(["--root", str(tmp_path)]) == 0
    assert _listing(tmp_path) == before
    assert not (tmp_path / sink.COMPARISON_LEDGER).exists()
    assert "no comparison record" in capsys.readouterr().out


def test_rendering_leaves_both_ledgers_byte_identical(tmp_path) -> None:
    _run(tmp_path, n=100, errors={"rival": 3.273})
    paths = [tmp_path / TRIAL_LEDGER, tmp_path / sink.COMPARISON_LEDGER]
    before = [p.read_bytes() for p in paths]
    leaderboard.render(tmp_path)
    assert [p.read_bytes() for p in paths] == before


# ---------------------------------------------------------------------------
# It computes nothing
# ---------------------------------------------------------------------------


def _imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add(node.module or "")
    return roots


def test_the_leaderboard_imports_no_statistics() -> None:
    """The structural half of "computes nothing": no numpy, scipy, arch, and none of ``compare``,
    which is where every number it prints was computed. The other half is the comparison above
    that each printed value equals the ledger's."""
    for module in sorted(SRC.glob("*.py")):
        for name in _imported_roots(module.read_text(encoding="utf-8")):
            top = name.split(".")[0]
            assert top not in {"numpy", "scipy", "arch", "pandas", "statistics"}, (module, name)
            assert not name.startswith("volab.compare"), (module.name, name)


def test_the_comparison_ledger_path_is_the_sinks() -> None:
    """Repeated, not imported: ``compare.sink`` costs three seconds of ``arch`` to load for a
    path. This is the witness that the copy has not drifted."""
    assert leaderboard.COMPARISON_LEDGER == sink.COMPARISON_LEDGER


def test_the_entry_point_has_no_flag_that_writes_or_unlocks() -> None:
    for flag in ("--write", "--append", "--allow-holdout", "--unlock", "--end"):
        with pytest.raises(SystemExit) as refused:
            cli.main([flag])
        assert refused.value.code == 2


def test_the_entry_point_prints_the_rendering_and_exits_zero(tmp_path, capsys) -> None:
    _run(tmp_path, n=100)
    assert cli.main(["--root", str(tmp_path)]) == 0
    lines, _ = leaderboard.render(tmp_path)
    assert capsys.readouterr().out.splitlines() == lines
