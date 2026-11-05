"""The forecast log: one append-only line per day per configuration, each carrying a fingerprint.

``data/forecasts.jsonl`` is the holdout-reveal design's protection 3 -- the daily run's own record
of what it forecast -- and the fingerprint on every line is that design's protection 4: the
champion's configuration, the git sha, the dirty flag, and a content hash of the champion's
definition. The hash is there *as well as* the sha so that a dirty tree is still identifiable: a
sha alone names a commit whose contents are not what ran.

**No count from this file is a reported number.** It is gitignored (``data/**`` already is, and no
exception was added for it), and it has to be: until the reveal, the champion on these lines is a
*pseudo*-champion -- the current dev winner, labelled ``kind="pseudo"`` rather than the registered
one (R158 Q2) -- and a tracked file would put unregistered numbers into git. The reported counts
remain the trial and comparison ledgers, and **this module writes nothing else**: not those two,
and not the evaluators' decision sink. Three tests pin that: no code string names one of the
three files, a write creates no file but this one, and a run with the ledger writer replaced by a
tripwire leaves every ledger byte-identical.

**One line per day per configuration is enforced here, not hoped for.** A configuration is
``(as_of, symbol, horizon)``. ``append_forecasts`` refuses a batch that repeats a key, or that
repeats one already in the file, and it refuses before writing anything -- a half-written batch
would be a day with some of its configurations recorded and no way to tell. It returns how many
lines it wrote, so a caller can assert that N configurations produced exactly N lines. The
reveal design's "attempts are counted, not forbidden" governs the *run* record; a forecast is a
claim about a day, and two claims for one day would leave a reader unable to say which stood.

**The timestamp, the sha and the dirty flag are the caller's.** ``at`` is injected -- on the
command line, as ``--at`` -- because nothing under ``volab.daily`` may reach a clock but ``_now``
(``tests/test_daily.py`` derives that), and for the reason ``append_trials`` states: read here, the
sha would describe the tree as it is when the writing happens rather than the tree that produced
the numbers (R70). The ``run`` verb passes the instant it took for its own start line, so a line's
``at`` joins to the run record. ``main`` takes each champion's fingerprint, with
``take_fingerprint``, before it fits that champion and not when the line is written.

**The region of the forecast date is not a field.** It is a pure function of ``as_of`` and the
configured ``Splits`` (``Splits.region_of``), and a copy stored on the line could only go stale
if ``holdout_end`` later moves, while adding a settings dependency to a writer that needs none.

**The champion is a dev winner, and the stage says so.** ``volab.daily.champion`` derives it from
the trial ledger through the leaderboard; ``pseudo_fingerprint`` fingerprints it with
``kind="pseudo"`` and has no parameter to say otherwise. ``main`` prints each configuration's
champion under a heading that calls it the dev-window leaderboard winner and not the registered
champion.

**``main`` fits each champion and writes its line.** For every configuration it derives the
pseudo-champion, finds the model the ledger names (``champion_model``), reads the panel and the
targets from the store **to the earlier of ``--as-of`` and ``dev_end``** and nothing later, and
fits that one model at ``as_of`` through ``volab.walkforward.evaluate`` -- the walk-forward's own
fit at an origin, handed a cohort of one -- so the panel truncation, the knowable-target slice, the
thin-training and null-regressor skips and a model's own refusal are the walk-forward's and there
is no second fitting path. The bound needs no key and the names of the two keys do
not appear under this package (a derived scan asserts it): a read at or before ``dev_end`` is
ordinary. A date past ``dev_end`` is therefore read to ``dev_end``, finds a panel that ends before
it, and is refused at every configuration; the plan refuses such a date before any stage runs, so
that path is reached only by running this module by hand.

**A configuration that cannot be forecast is refused with its reason, and the others still
write.** The reasons: no champion, a cohort or model that cannot be rebuilt, a champion that does
not answer the horizon, a store that cannot be read, a panel that ends before the date, too few
training rows, a champion that skips its own fit, a forecast that is not finite. A line already in
the log for the day is not rewritten and not an error, so a retry after a partial day fills in
what is missing. Exit 0 only if every configuration has its line, whether written now or before;
otherwise exit 1, because a stage that exited 0 with a configuration missing would be recorded as
a completed day. Exit 2, from ``argparse``, when ``--as-of`` or ``--at`` is missing or malformed:
neither has a default, because a default would need a clock.

"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import exchange_calendars as xcals
import pandas as pd

from volab.config import Settings, load_settings
from volab.daily.champion import (
    PSEUDO_KIND,
    NoChampionError,
    PseudoChampion,
    champion_model,
    definition_sources,
    pseudo_champion,
)
from volab.daily.runlog import append_text
from volab.errors import VolabError
from volab.features.targets import HORIZONS
from volab.models.base import Cohort, Model
from volab.panel import as_of_dates, load_curated_panel
from volab.sessions import get_calendar
from volab.store import git_state
from volab.walkforward import evaluate, load_targets

FORECAST_LOG = Path("data") / "forecasts.jsonl"

# ``src/volab/daily/forecast.py`` -> the repository root, as every CLI in this package spells it.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# ``pseudo`` is the pre-reveal dev winner; ``registered`` is the frozen champion after the reveal.
# Required on every fingerprint with no default, so a line cannot be mistaken for the registered
# kind by omission.
_KINDS = ("pseudo", "registered")

# (as_of, symbol, horizon). The horizon is read back from JSON, so it is typed as what it is.
_Key = tuple[str, str, object]

# The default of ``--minimum-training`` in both the walk-forward and the shootout CLIs (the two that
# write the trial ledger), repeated rather than imported because it is an argparse default there and
# not a name. The ledger does not record the value a given run used, so this is the default and not
# a verified match to the run that elected a champion. A forecast from a thinner fit is refused.
MINIMUM_TRAINING = 250

# What the single-model cohort the fit is run in is called. It is never written to a ledger.
_FIT_COHORT = "pseudo-champion-fit"


class ForecastLogError(VolabError):
    """Raised when an append would put a second line for one day and configuration in the log."""


class ForecastRefused(VolabError):
    """Raised when one configuration cannot be forecast; the message is the reason."""


@dataclass(frozen=True)
class Fingerprint:
    """What identifies the champion that produced a forecast. Carried on every line."""

    # The champion's configuration name, e.g. the model it is.
    model: str
    # ``pseudo`` or ``registered``; see ``_KINDS``.
    kind: str
    # ``("unknown", None)`` is recorded as such when git cannot be reached, never dropped.
    git_sha: str
    git_dirty: bool | None
    # ``definition_hash``'s digest: identifies the code and configuration even on a dirty tree.
    definition_hash: str

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"champion kind must be one of {_KINDS}, got {self.kind!r}")
        if not self.definition_hash:
            raise ValueError("a fingerprint needs a non-empty definition_hash")

    def record(self) -> dict[str, object]:
        return {
            "model": self.model,
            "kind": self.kind,
            "git_sha": self.git_sha,
            "git_dirty": self.git_dirty,
            "definition_hash": self.definition_hash,
        }


@dataclass(frozen=True)
class ForecastLine:
    """One forecast for one configuration on one day. ``value`` is on the variance scale."""

    as_of: dt.date
    symbol: str
    horizon: int
    value: float
    # Rows the fit consumed, reported so a thin fit is visible rather than inferred.
    observations: int
    champion: Fingerprint

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError(
                f"a forecast must be finite, got {self.value}: a line that cannot be read back "
                "as a number must not look like a forecast"
            )

    def key(self) -> _Key:
        return (self.as_of.isoformat(), self.symbol, self.horizon)

    def record(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "symbol": self.symbol,
            "horizon": self.horizon,
            "value": self.value,
            "observations": self.observations,
            "champion": self.champion.record(),
        }


def forecast_log_path(root: Path) -> Path:
    """Where the log lives under ``root``, which is the repository root.

    Takes a root rather than reading the configuration, so a rehearsal writes elsewhere by being
    handed a different root and not by a mode flag (the reveal design's call #6).
    """
    return root / FORECAST_LOG


def definition_hash(
    configuration: Mapping[str, object], sources: Sequence[Path], *, root: Path
) -> str:
    """SHA-256 of the champion's configuration and the bytes of its source files.

    The digest covers canonical JSON (sorted keys, fixed separators) of ``configuration`` and, for
    each source, its path **relative to ``root``** and the SHA-256 of its bytes, sorted by that
    path. Hashing each file and then the pairs, rather than concatenating contents, means a byte
    moved from one file to the next changes the digest. Because it reads the files as they are on
    disk, an edited-but-uncommitted champion hashes differently from the committed one, which is
    the whole reason it exists beside the sha. A source missing from disk raises
    ``FileNotFoundError``, and one outside ``root`` raises ``ValueError``: hashing the absence of a
    file would fingerprint a champion that is not there. A configuration that is not JSON raises
    ``TypeError``.

    Which files make up a champion is the caller's to say; this does not guess.
    """
    resolved_root = root.resolve()
    digests: list[list[str]] = []
    for source in sources:
        resolved = source.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise ValueError(f"{source} is outside the root {root}")
        digests.append(
            [
                resolved.relative_to(resolved_root).as_posix(),
                hashlib.sha256(resolved.read_bytes()).hexdigest(),
            ]
        )
    payload = json.dumps(
        {"configuration": configuration, "sources": sorted(digests)},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def take_fingerprint(
    *,
    model: str,
    kind: str,
    configuration: Mapping[str, object],
    sources: Sequence[Path],
    repo_root: Path,
) -> Fingerprint:
    """Fingerprint the champion now: ``git_state`` of the **repository root**, plus the hash.

    The root is the scope, not the package: ``volab.walkforward.git_state`` documents that with
    the package as scope an edit to ``config/base.yaml`` or ``tests/`` reads as clean. Call this
    before the champion is fitted, not when its line is written, so the fingerprint describes the
    tree that produced the number (R70).
    """
    sha, dirty = git_state(repo_root)
    return Fingerprint(
        model=model,
        kind=kind,
        git_sha=sha,
        git_dirty=dirty,
        definition_hash=definition_hash(configuration, sources, root=repo_root),
    )


def _existing_keys(path: Path) -> set[_Key]:
    """The ``(as_of, symbol, horizon)`` of every readable line already in the log.

    A torn final line is skipped rather than raised on, as ``volab.daily.runlog`` skips one: a
    job killed mid-write must not leave a log nothing can append to. The bytes stay in the file.
    Because ``append_forecasts`` starts its next write on a fresh line, a key written after a torn
    line is visible here, so a second attempt at it is still refused. The torn line itself
    contributes no key, and what it was meant to record is not recoverable from it.
    """
    if not path.exists():
        return set()
    keys: set[_Key] = set()
    for text in path.read_text(encoding="utf-8").splitlines():
        try:
            parsed: Any = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            keys.add((str(parsed.get("as_of")), str(parsed.get("symbol")), parsed.get("horizon")))
    return keys


def append_forecasts(lines: Sequence[ForecastLine], *, root: Path, at: dt.datetime) -> int:
    """Append one line per forecast and return how many were written. Never rewrites.

    All-or-nothing: duplicates, within the batch or against the file, are refused before a byte is
    written. The batch goes out in one write, flushed and fsynced -- the log is a once-a-day
    record whose claim is that it predates the reader, and a line still in a buffer is the
    cheapest way for that to be false. If the file ends in a torn line with no newline, the batch
    starts on a fresh line rather than onto the fragment (see ``volab.daily.runlog.append_text``,
    which this shares with the run log); the fragment is left in place and is still not a record.
    Durability against a power cut is not claimed.
    """
    path = forecast_log_path(root)
    seen = _existing_keys(path)
    batch: set[_Key] = set()
    for line in lines:
        key = line.key()
        if key in batch:
            raise ForecastLogError(f"{key} appears twice in this batch")
        if key in seen:
            raise ForecastLogError(f"{key} is already in the log at {path}")
        batch.add(key)
    if not lines:
        return 0
    text = "".join(
        json.dumps({"at": at.isoformat(), **line.record()}, allow_nan=False) + "\n"
        for line in lines
    )
    append_text(path, text)
    return len(lines)


def pseudo_fingerprint(champion: PseudoChampion, *, repo_root: Path) -> Fingerprint:
    """The fingerprint of a pseudo-champion. Its ``kind`` is ``PSEUDO_KIND`` and cannot be chosen.

    The hash covers ``definition_sources(repo_root)`` and the champion's own configuration, which
    includes the ledger run that elected it; see ``definition_sources`` for why those files.
    """
    return take_fingerprint(
        model=champion.model,
        kind=PSEUDO_KIND,
        configuration=champion.configuration(),
        sources=definition_sources(repo_root),
        repo_root=repo_root,
    )


def fit_champion(
    model: Model,
    panel: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    symbol: str,
    horizon: int,
    origin: dt.date,
    calendar: xcals.ExchangeCalendar,
) -> tuple[float, int]:
    """``model``'s forecast at ``origin`` and the observations the fit consumed.

    Runs ``volab.walkforward.evaluate`` for the single origin with a cohort of one, so the fit is
    the walk-forward's own: the panel is truncated at ``origin``, the training targets are those
    knowable then, and an origin with too few training rows or a null regressor is skipped, not
    fitted. Raises ``ForecastRefused`` with the reason for each of those, for a champion that does
    not answer ``horizon``, for a panel that does not reach ``origin``, for a champion that skips
    its own fit, and for a forecast that is not finite.
    """
    if not model.domain.answers(horizon):
        raise ForecastRefused(
            f"{model.name!r} answers h<={model.domain.max_horizon} and was asked h{horizon}"
        )
    bars = panel[panel["symbol"] == symbol]
    if bars.empty:
        raise ForecastRefused(f"the panel holds no bars for {symbol}")
    last = max(as_of_dates(bars))
    if last < origin:
        raise ForecastRefused(
            f"the panel for {symbol} ends at {last}, before the as-of {origin}: a forecast for a "
            "day the data does not reach would be made from an earlier one"
        )
    try:
        evaluation = evaluate(
            panel,
            targets,
            [origin],
            symbol=symbol,
            horizon=horizon,
            calendar=calendar,
            minimum_training=MINIMUM_TRAINING,
            cohort=Cohort(_FIT_COHORT, (model,)),
        )
    except VolabError as error:
        raise ForecastRefused(str(error)) from error
    if evaluation.skipped:
        raise ForecastRefused(evaluation.skipped[0][1])
    if evaluation.refusals:
        raise ForecastRefused(evaluation.refusals[0][2])
    (result,) = evaluation.results
    value = result.forecasts[model.name]
    if not math.isfinite(value):
        raise ForecastRefused(f"the fit gave a non-finite forecast ({value})")
    return value, result.training_rows


def _forecast(
    model: Model,
    settings: Settings,
    calendar: xcals.ExchangeCalendar,
    panels: dict[str, pd.DataFrame | ForecastRefused],
    symbol: str,
    horizon: int,
    as_of: dt.date,
    end: dt.date,
) -> tuple[float, int]:
    """Read the panel (once per symbol, kept in ``panels``) and targets to ``end``, then fit.

    ``end`` is the caller's ``min(as_of, dev_end)``; nothing here widens it. A store that cannot be
    read is a refusal for the configuration, not a crash of the stage.
    """
    if symbol not in panels:
        try:
            panels[symbol] = load_curated_panel(settings, symbol, end=end)
        except VolabError as error:
            panels[symbol] = ForecastRefused(str(error))
    panel = panels[symbol]
    if isinstance(panel, ForecastRefused):
        raise panel
    try:
        targets = load_targets(settings, symbol, horizon, end=end)
    except VolabError as error:
        raise ForecastRefused(str(error)) from error
    return fit_champion(
        model, panel, targets, symbol=symbol, horizon=horizon, origin=as_of, calendar=calendar
    )


def _aware_instant(text: str) -> dt.datetime:
    """An ISO instant that carries a UTC offset, for ``--at``."""
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an ISO instant") from None
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{text!r} must carry a UTC offset")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    """Fit each configuration's pseudo-champion at ``--as-of`` and append one line apiece.

    Prints every champion and every refusal with its reason, then how many lines it wrote. Exit 0
    when every configuration has its line (written now, or already in the log); exit 1 when any
    was refused. Exit 2, from ``argparse``, when ``--as-of`` or ``--at`` is missing or malformed.
    ``--at`` is the instant the lines are stamped with, which the ``run`` verb passes. ``--root``
    is the directory holding ``data/``: the ledger is read from it and the log written to it. The
    fingerprint is always taken of the repository itself, so a rehearsal root changes where the
    ledger is read and the log written, not what is hashed. The store is read to
    ``min(--as-of, dev_end)``.
    """
    parser = argparse.ArgumentParser(prog="python -m volab.daily.forecast", description=__doc__)
    parser.add_argument(
        "--as-of",
        dest="as_of",
        type=dt.date.fromisoformat,
        required=True,
        help="the date to forecast for (ISO); required, there is no default and no clock here",
    )
    parser.add_argument(
        "--at",
        type=_aware_instant,
        required=True,
        help="the instant to stamp the lines with (ISO, with a UTC offset); required, no clock",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_REPO_ROOT,
        help="the directory holding data/ (default: the repository's own)",
    )
    arguments = parser.parse_args(argv)
    settings = load_settings()
    # The earlier of the two. A read at or before dev_end is ordinary and needs no key; a date past
    # it is read to dev_end and nothing later, and ``fit_champion`` then refuses it.
    end = min(arguments.as_of, settings.splits.dev_end)
    calendar = get_calendar(settings)
    log = forecast_log_path(arguments.root)
    already = _existing_keys(log)
    print(f"as_of {arguments.as_of}")
    print(
        "PSEUDO-CHAMPION: each model below is the dev-window leaderboard winner, NOT the "
        "registered champion; the reveal has not happened"
    )
    panels: dict[str, pd.DataFrame | ForecastRefused] = {}
    lines: list[ForecastLine] = []
    refused = 0
    for symbol in settings.universe:
        for horizon in HORIZONS:
            try:
                champion = pseudo_champion(arguments.root, symbol, horizon)
                model = champion_model(champion)
            except NoChampionError as error:
                refused += 1
                print(f"refused {symbol} h{horizon}: {error}")
                continue
            taken = pseudo_fingerprint(champion, repo_root=_REPO_ROOT)
            print(
                f"{symbol} h{horizon}: {champion.model} ({champion.loss} {champion.value:.6g}, "
                f"{champion.cohort} run {champion.run_at}) kind={taken.kind} "
                f"definition_hash={taken.definition_hash[:12]}"
            )
            if (arguments.as_of.isoformat(), symbol, horizon) in already:
                print(f"{symbol} h{horizon}: already in the log, not rewritten")
                continue
            try:
                value, observations = _forecast(
                    model, settings, calendar, panels, symbol, horizon, arguments.as_of, end
                )
            except ForecastRefused as error:
                refused += 1
                print(f"refused {symbol} h{horizon}: {error}")
                continue
            lines.append(
                ForecastLine(
                    as_of=arguments.as_of,
                    symbol=symbol,
                    horizon=horizon,
                    value=value,
                    observations=observations,
                    champion=taken,
                )
            )
    written = append_forecasts(lines, root=arguments.root, at=arguments.at)
    print(f"wrote {written} forecast line(s)")
    if refused:
        print(
            f"refusing: {refused} configuration(s) could not be forecast, so the day is incomplete"
        )
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
