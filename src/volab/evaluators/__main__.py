"""``python -m volab.evaluators run`` -- score a cohort's forecasts as decisions, and record them.

For each ``(symbol, horizon)`` it evaluates the cohort, then judges what the forecasts license: the
Fissler-Ziegel loss of each contestant's Gaussian (VaR, ES) at each tail level, whether its VaR was
breached as often as it claimed and independently (Kupiec, Christoffersen), and the P&L of a
volatility-targeted position sized from it. Each ``(configuration, alpha)`` is one line of
``data/decisions.jsonl`` (``volab.evaluators.sink``).

**It calls** :func:`volab.walkforward.run_configuration` and does not reimplement it (R97), so the
audit refusal (R63), the append-per-configuration (R74), the empty-configuration count and the
truncation-equivalence report are the walk-forward CLI's own. **That means a run appends trials**:
one per contestant per loss per configuration, exactly what the walk-forward writes and no more.
They are real trials -- a walk-forward ran -- and ``count_trials`` should include them. What this
module adds to ``data/trials.jsonl`` is nothing: decisions go to their own file, and no decision
loss is registered in ``walkforward.LOSSES``
(``tests/test_evaluators.py::test_analyse_accepts_a_decision_series_without_losses_gaining_an_entry``).
``tests/test_evaluators_cli.py`` pins the count of trial lines a run writes, the shape of each, and
that every decision went to ``decisions.jsonl``.

The seal (R62) and the provenance stamp (R70) are kept as the other two entry points keep them:
there is no ``--end`` and no unlock flag, ``--to`` past ``dev_end`` is refused rather than clamped,
and the git sha and dirty flag are captured once, at the start, on every line of both files.

**The cohort is named, not defaulted.** Phase 3's recorded run is held until the holdout reveal
(R139), and a bare command that quietly picked a cohort and appended to the real ledgers would be
how it moved. A cohort that cannot answer a requested horizon is refused before any work, with
``Cohort.refusal``'s own text -- the string ``evaluate`` raises.

**The stride defaults to 5**, as ``volab.compare`` does. Coverage is refused where the stride is
shorter than the horizon (overlapping returns break the independence both tests assume), so at
h=22 the coverage columns of a default run carry that refusal; pass ``--every 22`` to test them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from volab.compare import analyse, describe
from volab.compare.cohorts import COHORTS, MonteCarlo, answering, monte_carlo_error
from volab.config import Settings, load_settings
from volab.errors import DatasetNotFound
from volab.evaluators import (
    ALPHAS,
    CoverageResult,
    VolTargetResult,
    coverage,
    decision_series,
    horizon_returns,
    var_es,
    vol_target,
)
from volab.evaluators.sink import Decision, JsonlSink
from volab.features.targets import HORIZONS
from volab.models.base import Cohort
from volab.panel import as_of_dates
from volab.paths import Tier
from volab.sessions import get_calendar, sessions_between
from volab.store import load_panel
from volab.walkforward import (
    Evaluation,
    count_trials,
    git_state,
    load_for,
    run_configuration,
)

FEATURE_SET = "har_daily"

# The repository root, which is the scope the dirty flag must ask about (R70).
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _persisted(settings: Settings, symbol: str) -> pd.DataFrame | None:
    """The feature table as the CLI wrote it, or ``None`` if the store holds none of it.

    Restated from ``volab.compare.__main__``, where it is private to a ``__main__`` module; the
    read is bounded by ``dev_end`` exactly as it is there.
    """
    try:
        frame = load_panel(
            FEATURE_SET,
            end=settings.splits.dev_end,
            symbols=[symbol],
            tier=Tier.FEATURES,
            settings=settings,
        )
    except DatasetNotFound:
        return None
    return frame.sort_values("as_of").reset_index(drop=True)


# The raw tier's name for the dividend source, as ``volab.ingest.curate`` reads it.
DIVIDENDS_SOURCE = "nasdaq_dividends"


@dataclass(frozen=True)
class DistributionWindow:
    """The ``has_distribution`` evidence for a set of holdings."""

    # The flag of every session in the union of the holdings, each session once.
    flags: list[bool]
    # How many holdings contain at least one flagged session.
    holdings_affected: int


def distribution_flags_known(settings: Settings, symbol: str) -> bool:
    """Whether ``has_distribution`` could have been ``True`` for ``symbol`` at all.

    The flag is derived from the dividend source (``volab.ingest.curate``), and the vendor serves
    distributions for QQQ alone: SPY's and IWM's are ``False`` by absence, which a panel cannot be
    told apart from "no ex-date occurred". So the question is put to the source: does it hold a
    row for ``symbol`` up to ``dev_end``? If not, a count of zero is a count of nothing recorded,
    and the decision says so (``distribution_flags_known: false``). That covers three states, which
    this does not separate: no source for the symbol, a source with no rows, and a source whose
    only rows fall after ``dev_end`` (``load_panel`` filters them out and then finds nothing).
    Reading those as unknown errs the safe way.

    **``True`` is weaker than it sounds.** It means a dividend source exists for ``symbol`` with at
    least one row at or before the seal -- not that the source's history covers the window being
    scored, and not that the ex-date count is complete for it. Coverage is not checked: a source
    holding a single old row reads known. Vendor history is a trailing window (Nasdaq serves ten
    years), so a window older than that is the case this cannot see.
    """
    try:
        load_panel(
            DIVIDENDS_SOURCE,
            end=settings.splits.dev_end,
            symbols=[symbol],
            tier=Tier.RAW,
            settings=settings,
        )
    except DatasetNotFound:
        return False
    return True


def distribution_window(
    panel: pd.DataFrame,
    symbol: str,
    calendar: xcals.ExchangeCalendar,
    origins: Sequence[dt.date],
    *,
    horizon: int,
    end: dt.date,
) -> DistributionWindow:
    """The store's ``has_distribution`` flags over the holdings of ``origins``.

    This is what ``vol_target``'s ``distributions`` argument is supplied from (spec 7.4): the flag
    is read from the curated bars already in ``panel`` -- which ``load_for`` read to ``dev_end`` --
    and not invented, so the ex-date count printed beside the P&L is a fact about the store.

    **A holding is ``(t, t + horizon]``.** A position opened at the close of ``t`` earns the return
    to the close ``horizon`` sessions on, so an ex-date on ``t`` itself fell before the holding
    began and one between two holdings (when the stride exceeds the horizon) falls in none.

    **Two counts, because they answer different questions.** ``flags`` covers the union of the
    holdings with each session once: how many ex-dates there were. ``holdings_affected`` counts
    the holdings that contain one: how much of the P&L is wrong. With overlapping holdings an
    ex-date sits in several -- an isolated one in about four or five at h=22 and stride 5 -- so
    the session count understates the biased share of the P&L by that factor; without overlap
    (stride >= horizon) each ex-date is in one holding. The length of ``flags`` is the number of
    sessions looked at.

    **Zero is not "no ex-dates"** where ``distribution_flags_known`` is false; this function
    cannot tell, and does not try.

    Refuses an origin that is not a session on or before ``end``, a holding that runs past ``end``
    (shortening it would shrink the window the count is read against), and a session in a holding
    that has no bar in ``panel`` (no flag to read is not a flag of ``False``). None of the three is
    reachable from ``_run``: its origins come from targets, which need a close at ``t`` and at
    ``t + horizon`` and a bar on every session between, all at or before ``dev_end``. They guard a
    caller that did not get its origins that way, and are tested directly.
    """
    if not origins:
        return DistributionWindow([], 0)
    bars = panel[panel["symbol"] == symbol]
    flagged: dict[dt.date, bool] = {}
    for day, flag in zip(as_of_dates(bars), bars["has_distribution"], strict=True):
        flagged[day] = flagged.get(day, False) or bool(flag)
    sessions = sessions_between(calendar, min(origins), end)
    position = {day: index for index, day in enumerate(sessions)}
    covered: set[int] = set()
    affected = 0
    for origin in origins:
        start = position.get(origin)
        if start is None:
            raise ValueError(f"origin {origin} is not a session on or before {end}")
        if start + horizon >= len(sessions):
            raise ValueError(
                f"the holding from {origin} runs past {end}: {horizon} sessions on is not "
                "a session this read may see"
            )
        holding = range(start + 1, start + horizon + 1)
        for index in holding:
            if sessions[index] not in flagged:
                raise ValueError(
                    f"session {sessions[index]} in the holding from {origin} has no bar for "
                    f"{symbol} in the panel, so its has_distribution flag cannot be read"
                )
        covered.update(holding)
        affected += any(flagged[sessions[index]] for index in holding)
    return DistributionWindow([flagged[sessions[index]] for index in sorted(covered)], affected)


def decide(
    evaluation: Evaluation,
    *,
    cohort: str,
    returns: Mapping[dt.date, float],
    distributions: DistributionWindow,
    distribution_flags_known: bool,
    errors: MonteCarlo,
) -> list[Decision]:
    """One :class:`Decision` per tail level for a finished evaluation.

    The FZ0 series, every contestant's breach sequence and its position are all built on
    ``series.origins`` -- ``evaluation.comparable("qlike")``, the origins the QLIKE trial beside
    them is computed over -- so the three measures rest on one origin set. ``coverage`` is handed
    the evaluation's own ``every`` and ``horizon`` for its stride and horizon: they decide whether
    it refuses an overlapping window, and a literal would answer for a run that did not happen.

    ``vol_target([], [])`` raises where ``decision_series`` accepts an empty evaluation, so an
    empty origin set is guarded here and recorded as a refusal with its reason.
    ``distributions`` is passed through (:func:`distribution_window`): its flags go to
    ``vol_target``, and its session count, affected-holdings count and ``distribution_flags_known``
    are kept on the decision. ``cohort`` is the name the caller **ran**, not read back from the
    evaluation, so ``decision_series`` compares two different things.
    """
    forecasts = {
        name: {origin: forecast for origin, _, forecast in evaluation.scored(name)}
        for name in evaluation.contestants
    }
    decisions: list[Decision] = []
    for alpha in ALPHAS:
        series = decision_series(evaluation, alpha=alpha, cohort=cohort, returns=returns)
        fz0 = analyse(series, monte_carlo_error=errors.percent, monte_carlo_source=errors.source)
        covered: dict[str, CoverageResult] = {}
        for name in evaluation.contestants:
            hits = [
                returns[origin] < -var_es(forecasts[name][origin], alpha)[0]
                for origin in series.origins
            ]
            covered[name] = coverage(
                hits, alpha, horizon=evaluation.horizon, stride=evaluation.every
            )
        sized: dict[str, VolTargetResult] | None = None
        refusal: str | None = None
        if series.n == 0:
            refusal = "no comparable origin: there is nothing to size a position on"
        else:
            sized = {
                name: vol_target(
                    [forecasts[name][origin] for origin in series.origins],
                    [returns[origin] for origin in series.origins],
                    horizon=evaluation.horizon,
                    distributions=distributions.flags,
                )
                for name in evaluation.contestants
            }
        decisions.append(
            Decision(
                symbol=evaluation.symbol,
                horizon=evaluation.horizon,
                cohort=cohort,
                alpha=alpha,
                stride=evaluation.every,
                origins=series.n,
                contestants=evaluation.contestants,
                fz0=fz0,
                coverage=covered,
                vol_target=sized,
                vol_target_refusal=refusal,
                distribution_window=len(distributions.flags),
                distribution_holdings_affected=distributions.holdings_affected,
                distribution_flags_known=distribution_flags_known,
            )
        )
    return decisions


def _refuse_horizons(cohort: Cohort, horizons: Sequence[int]) -> bool:
    """Print ``cohort.refusal(h)`` for each horizon it cannot answer; ``True`` if any.

    The text is the function ``evaluate`` raises from, so this is not a second rule that could word
    it or fire differently; refusing here costs the operator nothing, where ``evaluate``'s
    ``ValueError`` would arrive mid-run as a traceback.
    """
    refused = False
    for horizon in horizons:
        reason = cohort.refusal(horizon)
        if reason is None:
            continue
        refused = True
        print(reason)
        alternatives = answering(horizon, COHORTS)
        if alternatives:
            print(
                f"  run instead: --cohort {' or --cohort '.join(alternatives)} --horizon {horizon}"
            )
        else:
            print(f"  no registered cohort can be evaluated at h={horizon}")
    return refused


def _report(errors: MonteCarlo, line: str) -> None:
    """``print``, with the measured Monte Carlo error appended to a simulated contestant's line.

    ``run_configuration`` prints one line per trial that begins with the contestant's name, and
    R102 puts the error beside every simulated figure.
    """
    words = line.split()
    if words and words[0] in errors.percent:
        value = errors.percent[words[0]]
        shown = "not measured" if value is None else f"{value:.3f}%"
        line += f"  [MC error {shown} ({errors.source})]"
    print(line)


def _shown(value: float | None) -> str:
    return "undefined" if value is None else f"{value:.4g}"


def _describe(decision: Decision) -> list[str]:
    """The console lines for one decision: its coverage and P&L, per contestant."""
    lines = [f"  alpha {decision.alpha:g}: {decision.origins} origin(s)"]
    for name in decision.contestants:
        result = decision.coverage[name]
        if result.refusal is not None:
            lines.append(f"    {name:14s} coverage REFUSED: {result.refusal}")
        else:
            independence = (
                _shown(result.christoffersen_p)
                if result.christoffersen_p is not None
                else f"withheld ({result.christoffersen_withheld})"
            )
            lines.append(
                f"    {name:14s} {result.breaches} breach(es), {result.expected:.2f} expected; "
                f"Kupiec p {_shown(result.kupiec_p)}, Christoffersen p {independence}"
            )
    if decision.vol_target is None:
        lines.append(f"    vol target: {decision.vol_target_refusal}")
        return lines
    for name in decision.contestants:
        target = decision.vol_target[name]
        lines.append(
            f"    {name:14s} vol-target mean {_shown(target.mean_return)}, "
            f"vol {_shown(target.realized_vol)}, sharpe {_shown(target.sharpe)}, "
            f"turnover {_shown(target.turnover)}, capped at {target.capped_origins} origin(s)"
        )
    if decision.distribution_flags_known:
        lines.append(
            f"    ex-dates: {decision.vol_target[decision.contestants[0]].distribution_sessions} "
            f"session(s) of {decision.distribution_window}; "
            f"{decision.distribution_holdings_affected} of {decision.origins} holding(s) affected "
            "(close-to-close P&L is wrong on those)"
        )
    else:
        lines.append(
            "    ex-dates: NOT KNOWN -- the dividend source holds no row for this symbol at or "
            "before dev_end (no source, an empty one, or rows only after the seal), so a count "
            f"of 0 ({decision.distribution_window} session(s) looked at) is not evidence of none"
        )
    return lines


def _run(settings: Settings, arguments: argparse.Namespace, sink: JsonlSink) -> int:
    started = time.perf_counter()
    cohort = COHORTS[arguments.cohort]
    # Captured now, before a single number is produced (R70).
    sha, dirty = git_state(_REPO_ROOT)
    if dirty:
        print(f"git {sha[:12]} with a DIRTY tree: these trials are not reproducible from it")
    elif dirty is None:
        print(f"git {sha}: the tree's state is UNKNOWN, so these trials cannot be located")
    else:
        print(f"git {sha[:12]}, clean")
    calendar = get_calendar(settings)
    sessions = sessions_between(calendar, arguments.start, arguments.stop)
    if not sessions:
        print(f"no {calendar.name} sessions between {arguments.start} and {arguments.stop}")
        return 1

    # One timestamp for the run, stamped at the start: it is half the join key.
    started_at = dt.datetime.now(dt.UTC)
    trials_written = 0
    decisions_written = 0
    failures = 0
    print(f"cohort {cohort.name}: {', '.join(cohort.contestants)}")

    # Once per symbol, before any work on it: whether a count of zero ex-dates could mean anything.
    known = {symbol: distribution_flags_known(settings, symbol) for symbol in arguments.symbols}
    for symbol in arguments.symbols:
        for horizon in arguments.horizons:
            print(f"{symbol} h{horizon}:")
            panel, targets = load_for(settings, symbol, horizon, report=print)
            errors = monte_carlo_error(cohort, horizon)
            outcome = run_configuration(
                settings,
                symbol,
                horizon,
                panel,
                targets,
                calendar=calendar,
                first=sessions[0],
                last=sessions[-1],
                every=arguments.every,
                minimum_training=arguments.minimum_training,
                cohort=cohort,
                persisted=partial(_persisted, settings, symbol),
                # The walk-forward's own trials, through the existing writer: R74 appends them per
                # configuration, and they are the only lines this run puts on that ledger.
                append=lambda trials: sink.append_trials(
                    trials, at=started_at, sha=sha, dirty=dirty
                ),
                report=partial(_report, errors),
            )
            trials_written += outcome.written
            if outcome.refused:
                # Nothing to flush (R99): earlier configurations appended as they finished.
                return 2
            failures += outcome.disagrees

            evaluation = outcome.evaluation or Evaluation(
                symbol=symbol,
                horizon=horizon,
                results=(),
                features=pd.DataFrame(),
                skipped=(),
                requested=0,
                every=arguments.every,
                contestants=cohort.contestants,
                cohort=cohort.name,
            )
            # The signed return is a scoring read, taken after the forecasts exist and to dev_end.
            origins = [result.origin for result in evaluation.results]
            returns = horizon_returns(settings, symbol, origins, horizon=horizon) if origins else {}
            window = distribution_window(
                panel,
                symbol,
                calendar,
                sorted(evaluation.comparable("qlike")),
                horizon=horizon,
                end=settings.splits.dev_end,
            )
            decisions = decide(
                evaluation,
                cohort=cohort.name,
                returns=returns,
                distributions=window,
                distribution_flags_known=known[symbol],
                errors=errors,
            )
            decisions_written += sink.append_decisions(
                decisions, at=started_at, sha=sha, dirty=dirty
            )
            ledger = count_trials(sink.root)
            for decision in decisions:
                for line in describe(decision.fz0, trials=ledger):
                    print(line)
                for line in _describe(decision):
                    print(line)

    print(f"{trials_written} trial line(s) and {decisions_written} decision record(s) appended")
    print(f"ledger now {count_trials(sink.root)} trial line(s)")
    print(f"ran in {time.perf_counter() - started:.1f}s")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(prog="python -m volab.evaluators", description=__doc__)
    parser.add_argument("command", choices=("run",), help="what to run")
    parser.add_argument(
        "--cohort",
        choices=list(COHORTS),
        required=True,
        help="the contestants to score; there is no default (see the module docstring)",
    )
    parser.add_argument(
        "--symbol",
        dest="symbols",
        action="append",
        choices=list(settings.universe),
        help="repeatable; defaults to the whole configured universe",
    )
    parser.add_argument(
        "--horizon",
        dest="horizons",
        action="append",
        type=int,
        choices=list(HORIZONS),
        help="repeatable; defaults to the horizons the cohort can answer",
    )
    parser.add_argument(
        "--from",
        dest="start",
        type=dt.date.fromisoformat,
        default=dt.date(2021, 1, 4),
        help="first forecast origin (default 2021-01-04, leaving four years of training)",
    )
    parser.add_argument(
        "--to",
        dest="stop",
        type=dt.date.fromisoformat,
        default=settings.splits.dev_end,
        help="last forecast origin; never read past dev_end whatever is passed",
    )
    parser.add_argument(
        "--every",
        type=int,
        default=5,
        help="use every Nth session as an origin (default 5); coverage needs N >= the horizon",
    )
    parser.add_argument(
        "--minimum-training",
        type=int,
        default=250,
        help="refuse to fit an origin with fewer usable training rows (default 250)",
    )
    arguments = parser.parse_args(argv)
    arguments.symbols = arguments.symbols or list(settings.universe)

    # The seal, enforced rather than documented (R62).
    if arguments.stop > settings.splits.dev_end:
        print(
            f"--to {arguments.stop} is past dev_end {settings.splits.dev_end}; "
            "the holdout is sealed and this entry point has no key"
        )
        return 1

    # Before the git state and every load: an explicit horizon the cohort cannot answer is refused
    # whole, and the default is the cohort's own set so the bare command is correct, not refused.
    cohort = COHORTS[arguments.cohort]
    if arguments.horizons:
        if _refuse_horizons(cohort, arguments.horizons):
            return 1
    else:
        arguments.horizons = list(cohort.horizons(HORIZONS))
        if not arguments.horizons:
            print(f"cohort {cohort.name!r} answers none of the configured horizons {HORIZONS}")
            return 1
        left_out = [h for h in HORIZONS if h not in arguments.horizons]
        if left_out:
            running = ", ".join(map(str, arguments.horizons))
            print(
                f"--horizon not given: running h={running}, the horizons cohort {cohort.name!r} "
                f"can answer; left out: {cohort.refusal(left_out[0])}"
            )

    print(f"storage root {settings.storage_root}")
    print(f"reads stop at dev_end {settings.splits.dev_end}")
    return _run(settings, arguments, JsonlSink(settings.storage_root.parent))


if __name__ == "__main__":
    sys.exit(main())
