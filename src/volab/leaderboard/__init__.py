"""The leaderboard: what the two ledgers hold, rendered, and nothing computed (US-3.5).

``data/trials.jsonl`` holds every trial's loss and ``data/comparisons.jsonl`` holds MCS
membership, SPA and DM. This module reads both and prints them. **It computes nothing of its
own.** A leaderboard that recomputed a mean or a ranking statistic could disagree with the thing
it claims to report, and there would be two numbers and no way to know which was the result. The
only operations here are sorting by a stored loss, counting ledger lines, and formatting.
``test_the_leaderboard_imports_no_statistics`` is the structural witness; ``test_a_value_is_
printed_as_the_ledger_holds_it`` is the behavioural one.

**The headline is every line in the ledger** (R59), and the per-cohort breakdown sits beside it.
The headline is ``count_trials`` -- the function the writers use -- and the breakdown is checked
to sum to it, so a cohort added later grows the number and cannot restart it
(``test_adding_a_cohort_cannot_reset_the_headline``). **A line with no ``cohort`` key is
``phase2-baseline``** (R86): the 72 lines written before the key existed are that run, and
``test_the_committed_ledger_reads_correctly_and_is_not_touched`` reads them.

**What is printed rather than omitted.** An MCS size the comparison refused prints as a refusal
with its reason (R94): an absent row reads as "no result", a printed refusal as "we declined to
claim". A DM pair or SPA test the record does not claim prints as WITHHELD with the stored facts
that explain it, never as a ranking. A trial from a dirty tree is marked, because its sha does
not identify the code that produced it (R70). A run with no comparison on file says so. And a
Monte-Carlo error is printed only beside the series it was measured on (R102 and its final
ruling): ``analyse`` raises on one without the other, and a record that carries one anyway has the
figure withheld here rather than printed bare, where it would read as the neighbouring row's own.

**It opens no path to write** (R95, R109): ``compare``'s sink is the only writer.
``test_no_leaderboard_module_can_write_a_path`` scans for it and
``test_rendering_creates_nothing_where_there_is_no_ledger`` checks it.

This module imports ``volab.walkforward`` for the trial ledger's own name, count and loss table,
and **not** ``volab.compare``: that package costs three seconds of ``arch`` to load, and importing
it would put the code that computes these numbers inside the code that reports them. The one thing
repeated is the comparison ledger's path, with ``test_the_comparison_ledger_path_is_the_sinks``
as the witness against drift.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from volab.errors import VolabError
from volab.walkforward import BASELINE_COHORT_NAME, LOSSES, TRIAL_LEDGER, count_trials

# Repeated from ``volab.compare.sink``, which is not imported (see the module docstring).
COMPARISON_LEDGER = Path("data") / "comparisons.jsonl"

# The MCS sizes as the record keys them (``f"{size:g}"`` in the sink), which is also the order
# they are printed in. ``test_the_sizes_the_leaderboard_expects_are_the_ones_the_comparison_runs``
# pins this to ``compare.multiple.SIZES``.
MCS_SIZES: tuple[str, ...] = ("0.1", "0.25")

_TRIAL_FIELDS = ("at", "git_sha", "model", "symbol", "horizon", "loss", "origins", "value")
_COMPARISON_FIELDS = (
    "at",
    "git_sha",
    "symbol",
    "horizon",
    "loss",
    "cohort",
    "origins",
    "stride",
    "q",
    "bartlett_lag",
    "n_eff",
    "mcs",
    "spa",
    "dm",
    "dm_significance_claimed",
    "monte_carlo_error",
    "monte_carlo_error_source",
    "unclaimed",
    "refusal",
)


class LeaderboardError(VolabError):
    """Raised when a ledger line cannot be read as what it should be.

    A line that is skipped is a trial that is not counted, and the headline is the number the
    write-up reports: so an unreadable line stops the leaderboard instead of shrinking it
    (``test_a_malformed_trial_line_raises_rather_than_being_skipped``,
    ``test_a_trial_line_missing_a_field_names_the_field``).
    """


# ``(symbol, horizon, cohort, at, git_sha)``, and with a loss appended, the sink's join key.
_Run = tuple[str, int, str, str, str]
_JoinKey = tuple[str, int, str, str, str, str]


@dataclass(frozen=True)
class TrialLine:
    """One line of ``data/trials.jsonl``, with the cohort defaulted as R86 says."""

    at: str
    sha: str
    dirty: bool | None
    cohort: str
    symbol: str
    horizon: int
    loss: str
    model: str
    value: float | None
    origins: int
    dropped: int
    refused: int

    @property
    def run(self) -> _Run:
        """The run this line belongs to: the sink's join key without the loss, which is what a
        ranking is made within (a baseline trial and a shootout trial of one model are different
        experiments, R86, and are never ranked against each other;
        ``test_runs_of_one_configuration_are_ranked_apart``)."""
        return (self.symbol, self.horizon, self.cohort, self.at, self.sha)


def _lines(path: Path) -> list[tuple[int, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [(number, line) for number, line in enumerate(text.splitlines(), 1) if line.strip()]


def _parse(path: Path, number: int, line: str, required: Sequence[str]) -> dict[str, Any]:
    try:
        row = json.loads(line)
    except json.JSONDecodeError as error:
        raise LeaderboardError(f"{path.as_posix()} line {number} is not JSON: {error}") from error
    if not isinstance(row, dict):
        raise LeaderboardError(f"{path.as_posix()} line {number} is not a JSON object")
    missing = [key for key in required if key not in row]
    if missing:
        raise LeaderboardError(f"{path.as_posix()} line {number} is missing {missing}")
    return row


def read_trials(root: Path) -> tuple[list[TrialLine], int]:
    """Every line of the trial ledger, and how many of them carry no ``cohort`` key."""
    path = TRIAL_LEDGER
    trials: list[TrialLine] = []
    legacy = 0
    for number, line in _lines(root / path):
        row = _parse(path, number, line, _TRIAL_FIELDS)
        if row["loss"] not in LOSSES:
            raise LeaderboardError(
                f"{path.as_posix()} line {number}: loss {row['loss']!r} is not one of "
                f"{sorted(LOSSES)}, so there is no stated direction to rank it in"
            )
        if "cohort" not in row:
            legacy += 1
        trials.append(
            TrialLine(
                at=row["at"],
                sha=row["git_sha"],
                dirty=row.get("git_dirty"),
                cohort=row.get("cohort", BASELINE_COHORT_NAME),
                symbol=row["symbol"],
                horizon=row["horizon"],
                loss=row["loss"],
                model=row["model"],
                value=row["value"],
                origins=row["origins"],
                dropped=row.get("dropped", 0),
                refused=row.get("refused", 0),
            )
        )
    return trials, legacy


def read_comparisons(root: Path) -> list[dict[str, Any]]:
    path = COMPARISON_LEDGER
    return [_parse(path, number, line, _COMPARISON_FIELDS) for number, line in _lines(root / path)]


# ---------------------------------------------------------------------------
# Formatting. Every figure below is a stored one, formatted.
# ---------------------------------------------------------------------------


def _number(value: float | None, spec: str = ".4f") -> str:
    return "n/a" if value is None else format(value, spec)


def _mc_text(record: Mapping[str, Any], names: Sequence[str]) -> str:
    """The measured Monte Carlo error of each simulated contestant among ``names`` (R102).

    **Never bare.** A figure is printed with the series it was measured on or not at all; a record
    holding figures and no series gets a marker saying so, since a bare ``3.273%`` beside an SPY
    row reads as SPY's own error. Empty where none of ``names`` is simulated.
    Witnesses: ``test_a_monte_carlo_error_is_never_printed_without_its_source`` and
    ``test_a_stored_error_with_no_source_is_not_printed_bare``.
    """
    errors: Mapping[str, float | None] = record["monte_carlo_error"]
    measured = [name for name in names if name in errors]
    if not measured:
        return ""
    source = record["monte_carlo_error_source"]
    if not source:
        return "  [MC error NOT SHOWN: recorded with no source series, so it cannot be read]"
    parts = []
    for name in measured:
        value = errors[name]
        if value is None:
            parts.append(f"{name} not measured")
        elif name in record["unclaimed"]:
            parts.append(f"{name} {value:.3f}%, no claim")
        else:
            parts.append(f"{name} {value:.3f}%")
    return f"  [MC error ({source}): {'; '.join(parts)}]"


def _withheld_reasons(record: Mapping[str, Any], pair: Mapping[str, Any] | None) -> list[str]:
    """Why a claim is withheld, from the facts the record stores and without re-deriving the rule.

    Each reason is a stored flag or list. If none is found the claim is still withheld -- the
    record said so -- and the caller prints it bare rather than guess a reason.
    """
    reasons = []
    if not record["dm_significance_claimed"]:
        reasons.append(f"n_eff={_number(record['n_eff'], '.1f')}, too thin for a claim")
    names = [pair["left"], pair["right"]] if pair is not None else list(record["monte_carlo_error"])
    for name in names:
        if name in record["unclaimed"]:
            reasons.append(f"{name}'s Monte Carlo error is above the floor")
    if pair is not None:
        if pair["headline"]["refusal"] or pair["bartlett"]["refusal"]:
            reasons.append("a column refused")
        if pair["disagree"]:
            reasons.append("columns disagree at 5%")
    return reasons


def _withheld(record: Mapping[str, Any], pair: Mapping[str, Any] | None) -> str:
    reasons = _withheld_reasons(record, pair)
    return "  [WITHHELD: " + "; ".join(reasons) + "]" if reasons else "  [WITHHELD]"


def _pair_line(record: Mapping[str, Any], pair: Mapping[str, Any]) -> str:
    def column(label: str, body: Mapping[str, Any], symbol: str) -> str:
        if body["refusal"] is not None:
            return f"{label} REFUSED ({body['refusal']})"
        return f"{label} {symbol}={_number(body['statistic'], '+.4g')} p={_number(body['p_value'])}"

    text = (
        f"      {pair['left']} vs {pair['right']}: d_bar={_number(pair['mean_difference'], '+.4g')}"
        f" | {column('headline', pair['headline'], 't')}"
        f" | {column('bartlett', pair['bartlett'], 'z')}"
    )
    text += _mc_text(record, (pair["left"], pair["right"]))
    if not pair["claimed"]:
        return text + _withheld(record, pair)
    verdict = pair["headline"]["verdict"]
    if verdict == 0:
        return text + "  => no significant difference at 5% (both columns)"
    better = pair["left"] if verdict == -1 else pair["right"]
    return text + f"  => {better} significantly better at 5% (both columns)"


def _spa_line(record: Mapping[str, Any], spa: Mapping[str, Any], cohort_wide: str) -> str:
    head = f"    SPA (benchmark {spa['benchmark']}):"
    tag = f"[n_eff={_number(spa['n_eff'], '.1f')}]"
    if spa["refusal"] is not None:
        return f"{head} REFUSED {tag} -- {spa['refusal']}{cohort_wide}"
    line = (
        f"{head} p lower={_number(spa['lower'])} consistent={_number(spa['consistent'])} "
        f"upper={_number(spa['upper'])} {tag}{cohort_wide}"
    )
    # The record stores whether SPA may be claimed, not the 5% reading, and reading the p-value
    # against 0.05 here would be recomputing the claim. So a permitted claim prints its p-values.
    return line + (_withheld(record, None) if not spa["claimed"] else "  [claim permitted]")


def _comparison_lines(record: Mapping[str, Any]) -> list[str]:
    tag = f"n_eff={_number(record['n_eff'], '.1f')}"
    out = [
        f"    comparison: n={record['origins']} stride={record['stride']} {tag}"
        + f" q={record['q']} bartlett_lag={record['bartlett_lag']}"
    ]
    if record["refusal"] is not None:
        out.append(f"    REFUSED: {record['refusal']}")
        return out
    cohort_wide = _mc_text(record, list(record["monte_carlo_error"]))
    sizes: Mapping[str, Any] = record["mcs"]["sizes"]
    for size in MCS_SIZES:
        label = f"MCS {float(size):.0%}"
        entry = sizes.get(size)
        if entry is None:
            out.append(f"    {label}: not recorded [{tag}]")
        elif "refused" in entry:
            out.append(f"    {label}: REFUSED [{tag}] -- {entry['refused']}{cohort_wide}")
        else:
            out.append(f"    {label}: {', '.join(entry['members'])} [{tag}]{cohort_wide}")
    pvalues = record["mcs"].get("pvalues")
    if pvalues is not None:
        ordered = sorted(
            pvalues.items(), key=lambda item: -(item[1] if item[1] is not None else -1)
        )
        out.append(
            "    MCS p-values: "
            + ", ".join(f"{name} {_number(p, '.3f')}" for name, p in ordered)
            + f" [{tag}]{cohort_wide}"
        )
    out.extend(_spa_line(record, spa, cohort_wide) for spa in record["spa"])
    out.append(
        f"    DM: {len(record['dm'])} pairwise test(s), no selection made on their p-values; "
        "d = left - right, so a negative statistic favours left"
    )
    out.extend(_pair_line(record, pair) for pair in record["dm"])
    return out


# ---------------------------------------------------------------------------
# The rendering
# ---------------------------------------------------------------------------


def _ranked(trials: Sequence[TrialLine]) -> list[tuple[str, TrialLine]]:
    """``(rank, trial)``, best first. The rank is one more than the number of strictly better
    values, so equal losses share one; an undefined value is listed last and unranked.
    Witness: ``test_equal_losses_share_a_rank_rather_than_inventing_an_order``."""
    defined = sorted(((t.value, t) for t in trials if t.value is not None), key=lambda p: p[0])
    values = [value for value, _ in defined]
    out = [(str(1 + sum(1 for other in values if other < value)), t) for value, t in defined]
    out.extend(("--", t) for t in trials if t.value is None)
    return out


def _row(
    rank: str,
    trial: TrialLine,
    record: Mapping[str, Any] | None,
) -> str:
    value = "n/a" if trial.value is None else format(trial.value, ".6g")
    text = f"    {rank:>2}. {trial.model:<18} {value:<12} origins={trial.origins}"
    if trial.dropped:
        text += f" dropped={trial.dropped}"
    if trial.refused:
        text += f" refused={trial.refused}"
    if record is not None:
        text += _mc_text(record, (trial.model,))
    if trial.dirty is True:
        text += "  [DIRTY TREE: the sha does not identify the code that produced this]"
    elif trial.dirty is None:
        text += "  [tree state unknown: the sha may not identify the code that produced this]"
    return text


def render(root: Path) -> tuple[list[str], bool]:
    """The leaderboard as lines, and whether the two ledgers were consistent.

    Not consistent means a comparison record whose join key has no trial (the sink's docstring
    calls that a bug): it is printed under its own heading rather than dropped, and the entry
    point exits non-zero (``test_a_comparison_with_no_trials_is_reported_and_exits_non_zero``).
    """
    trials, legacy = read_trials(root)
    comparisons = read_comparisons(root)

    headline = count_trials(root)
    by_cohort = Counter(t.cohort for t in trials)
    if sum(by_cohort.values()) != headline:
        raise LeaderboardError(
            f"the cohort breakdown sums to {sum(by_cohort.values())} but the ledger holds "
            f"{headline} line(s): the headline would be wrong, so nothing is printed"
        )
    breakdown = ", ".join(f"{cohort} {by_cohort[cohort]}" for cohort in sorted(by_cohort))
    ledger_note = f"{headline} trial(s) on the ledger ({breakdown})" if trials else "0 trial(s)"

    lines = [
        f"HEADLINE: {headline} trial(s) on the ledger -- every line of "
        f"{TRIAL_LEDGER.as_posix()}, all cohorts",
        f"  by cohort: {breakdown}" if trials else "  by cohort: none",
    ]
    if legacy:
        lines.append(
            f"  {legacy} line(s) carry no cohort key; they are {BASELINE_COHORT_NAME} (R86)"
        )
    dirty = sum(1 for t in trials if t.dirty is True)
    unknown = sum(1 for t in trials if t.dirty is None)
    if dirty:
        lines.append(f"  {dirty} of {headline} trial(s) were produced on a dirty tree (R70)")
    if unknown:
        lines.append(f"  {unknown} of {headline} trial(s) have an unknown tree state (R70)")
    lines.append(
        f"{COMPARISON_LEDGER.as_posix()}: {len(comparisons)} comparison record(s)"
        if (root / COMPARISON_LEDGER).exists()
        else f"{COMPARISON_LEDGER.as_posix()}: absent"
    )

    by_key: dict[_JoinKey, list[dict[str, Any]]] = defaultdict(list)
    for record in comparisons:
        key = (
            record["symbol"],
            record["horizon"],
            record["cohort"],
            record["at"],
            record["git_sha"],
            record["loss"],
        )
        by_key[key].append(record)

    runs: dict[_Run, list[TrialLine]] = defaultdict(list)
    for trial in trials:
        runs[trial.run].append(trial)

    matched: set[_JoinKey] = set()
    for run in sorted(runs):
        symbol, horizon, cohort, at, sha = run
        lines.append("")
        lines.append(f"== {symbol} h{horizon} [{cohort}] run {at} sha {sha[:8]} ==")
        for loss in sorted({t.loss for t in runs[run]}):
            group = [t for t in runs[run] if t.loss == loss]
            lines.append(
                f"  {loss} (lower is better): {len(group)} contestant(s) ranked; {ledger_note}"
            )
            records = by_key.get((*run, loss), [])
            matched.add((*run, loss))
            first = records[0] if records else None
            lines.extend(_row(rank, trial, first) for rank, trial in _ranked(group))
            if not records:
                lines.append(
                    "    no comparison record for this run: MCS, SPA and DM are not available "
                    "(none was written, which is not the same as none being significant)"
                )
            if len(records) > 1:
                lines.append(
                    f"    {len(records)} comparison records share this key (one run appended "
                    "twice?); each is shown"
                )
            for record in records:
                lines.extend(_comparison_lines(record))

    orphans = [
        record
        for key, records in sorted(by_key.items(), key=lambda item: str(item[0]))
        if key not in matched
        for record in records
    ]
    for record in orphans:
        if record is orphans[0]:
            lines.append("")
            lines.append(
                "ORPHANED COMPARISON(S): no trial line shares their join key "
                "(git_sha, at, symbol, horizon, loss, cohort); the ledgers are inconsistent"
            )
        lines.append(
            f"  {record['symbol']} h{record['horizon']} {record['loss']} [{record['cohort']}] "
            f"run {record['at']} sha {str(record['git_sha'])[:8]}"
        )
    return lines, not orphans
