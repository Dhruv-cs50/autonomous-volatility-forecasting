"""The reveal itself: the gates, the fingerprint, the multi-read span and the scoring (HR-4).

This package is the machinery that can take the one confirmatory read of the holdout window, and
it is written so that taking it is a deliberate act of a human at a shell. Two keys open the
window: ``allow_holdout=True``, which the stage functions below pass, and the unlock
variable (``VOLAB_HOLDOUT_UNLOCK=1``), which a human exports and which **nothing in this
package ever sets** (witness W17). The package reads no Parquet, opens no path and names no
audit log; it calls the code that does.

**Pure here, effects at one seam.** ``gates`` and ``champion_from`` are pure and table-testable;
``__main__`` supplies the clock, ``argparse`` and ``sys.exit``. This module contains no ``print``.

**What is computed is §1-§3 of ``docs/holdout-preregistration.md`` and nothing else.** Every
number comes from code that already computes it (``run_configuration``, ``analyse``,
``loss_series``, ``ranked_trials``); the only operation this package applies to a loss is
ordering stored values (W15).

**R216 and R217** (``docs/superpowers/holdout-reveal-decisions.md``), both the user's, both taken
before any holdout number existed. R216: a contestant whose significance R115 withholds is still
eligible to be champion -- section 3 is read literally, and the withholding restricts what may be
*claimed about* the champion, not whether it can be *named*. R217: the reveal may be taken once
the window has closed (gate ``window_open``).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import glob
import hashlib
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd
from pandas.testing import assert_frame_equal

from volab.compare import analyse
from volab.compare.cohorts import MULTISTEP, SHOOTOUT, monte_carlo_error
from volab.compare.series import loss_series
from volab.compare.sink import Comparison, Sink
from volab.config import Settings
from volab.errors import DatasetNotFound
from volab.features.__main__ import _target_panel, _write_features
from volab.features.build import build_features
from volab.features.targets import HORIZONS, build_targets, targets_dataset
from volab.features.targets import write_targets as write_target_partitions
from volab.ingest.curate import (
    BARS_DATASET,
    FRED_DATASET,
    VIX_DATASET,
    curate_bars,
    curate_fred,
    curate_vix,
)
from volab.models.base import Cohort
from volab.panel import (
    EXOGENOUS_READS,
    FRED_SERIES_NAMES,
    VIX_SERIES,
    as_of_dates,
    assemble,
    load_bars,
    load_exogenous,
)
from volab.paths import Tier, dataset_glob
from volab.sessions import get_calendar, nth_session_after, sessions_between
from volab.store import AuditTally, load_panel
from volab.walkforward import (
    LOSSES,
    Evaluation,
    Trial,
    load_targets,
    run_configuration,
)

# ---- the registered literals, each pinned to its source by a witness (W4) ----------------------
PREREGISTRATION: Path = Path("docs", "holdout-preregistration.md")
REGISTERED_SHA256: str = "c0a095ab3a00711fd435eded709bde4dcdf9f1ab87ab016e92dc7c5cd7323779"
REGISTERED_BLOB: str = "4b5c04e38280cbff591d353b1ccf8a91cb3d517b"
REGISTERING_COMMIT: str = "65617eea2e1c95b8cf0b90555810bf64944c9153"
REGISTERED_TRIALS: int = 414
REGISTERED_COMPARISONS: int = 52
# Section 1's predictions of the usable origins per horizon. Predictions and not commitments
# (section 5): a mismatch is reported beside the actual counts, never refused.
REGISTERED_ORIGINS: dict[int, int] = {1: 208, 5: 204, 22: 187, 66: 143}
STRIDE: int = 1  # section 1, "Origins at stride 1"
MINIMUM_TRAINING: int = 250  # NOT registered; the CLI default. It provably cannot bind here (OQ3)
BENCHMARKS: tuple[str, ...] = ()  # SPA is not in section 2's claimed list
CHAMPION: tuple[str, int, str, float] = ("SPY", 1, "qlike", 0.10)  # section 3's configuration
# The persisted feature table's name in the store; pinned equal to the features CLI's by a test.
FEATURE_SET: str = "har_daily"
# The two cohort runs, with the horizons each is registered at (section 1's table). Tests replace
# this to keep the carved rehearsal light; nothing the operator types can.
RUNS: tuple[tuple[Cohort, tuple[int, ...]], ...] = ((SHOOTOUT, (1,)), (MULTISTEP, (5, 22, 66)))

THE_RULE = (
    "The champion is the contestant in the 10% model confidence set with the lowest QLIKE, at "
    "h=1 on SPY, chosen in advance as the primary configuration. If the 10% set has one member, "
    "that is the champion; if several, the lowest QLIKE among them; if the set is refused for "
    "thinness, there is no champion and that is the result."
)
WITHHELD_SENTENCE = (
    "R115 withholds every significance claim about this champion (R216: it is still eligible to "
    "be named), so its lead is a ranking and carries no claim of significance."
)

# ---- gates --------------------------------------------------------------------------------------
USAGE = 2  # argparse's own; nothing in this package may use it


@dataclass(frozen=True)
class Gate:
    code: int  # the process exit code, 3..15; never 0, 1 or 2
    name: str  # a stable identifier, e.g. "dirty_tree"
    message: str  # the whole sentence the operator reads; no further formatting


def gates(
    settings: Settings,
    *,
    today: dt.date,
    repo_root: Path,
    git_sha: str,
    git_dirty: bool | None,
    preregistration_sha256: str | None,
    preregistration_blob: str | None,
    already_revealed: bool,
) -> tuple[Gate, ...]:
    """Every pre-read refusal that applies, in the order they are checked. Empty means proceed.

    Pure and total: it reads no file, runs no subprocess and asks no clock. The caller measures
    the world; this decides. ``today`` is the caller's, on ``daily._today``'s precedent.
    """
    found: list[Gate] = []
    if git_dirty is True:
        found.append(
            Gate(
                3,
                "dirty_tree",
                f"the tree is dirty (git {git_sha[:12]}, repository scope): the reveal records a "
                "fingerprint that must identify the code that produced the numbers, and a dirty "
                "tree cannot. Commit or stash, then run again.",
            )
        )
    # An unknown flag with a known sha is as unusable as an unknown sha: "clean" was not seen.
    if git_sha == "unknown" or git_dirty is None:
        found.append(
            Gate(
                4,
                "git_unknown",
                "git cannot name this checkout's HEAD and its state, so no fingerprint can be "
                'taken. The reveal refuses rather than recording "unknown" as a provenance claim.',
            )
        )
    registered_path = repo_root.joinpath(PREREGISTRATION).as_posix()
    if preregistration_sha256 is None:
        found.append(
            Gate(
                5,
                "preregistration_absent",
                f"{PREREGISTRATION.as_posix()} cannot be read ({registered_path} is absent or "
                "unreadable). The reveal is defined by that file and will not run without it.",
            )
        )
    elif preregistration_sha256 != REGISTERED_SHA256:
        found.append(
            Gate(
                6,
                "preregistration_content",
                f"{PREREGISTRATION.as_posix()} hashes {preregistration_sha256} and R130 "
                f"registered {REGISTERED_SHA256}: the registered document has changed. A change "
                "means a NEW registration and a new look, both counted; it does not mean this "
                "reveal proceeds.",
            )
        )
    if preregistration_blob != REGISTERED_BLOB:
        found.append(
            Gate(
                7,
                "preregistration_blob",
                f"git rev-parse HEAD:{PREREGISTRATION.as_posix()} gives "
                f"{preregistration_blob or 'nothing'} and R130 registered "
                f"{REGISTERED_BLOB[:7]}...: the file on disk may be right but this checkout's "
                "history is not the one it was registered in.",
            )
        )
    # R217 (the user's): the window closing is enough. The registration's closing paragraph says
    # "plus each horizon's tail", which contradicts its own section 1; the choice is
    # figure-neutral, because every read is bounded at ``holdout_end`` and ``load_panel`` filters
    # on ``as_of``, so a target stamped later is invisible to this read whenever it is taken.
    if not today > settings.splits.holdout_end:
        found.append(
            Gate(
                8,
                "window_open",
                f"holdout_end {settings.splits.holdout_end} is not in the past (today {today}): "
                "the registered window has not finished, so there is nothing complete to read.",
            )
        )
    if already_revealed:
        found.append(
            Gate(
                9,
                "already_revealed",
                "a completed reveal record is already committed at HEAD: the holdout is ordinary "
                "history and this read is not the one-time reveal. "
                "Re-scoring it is an ordinary run, not a reveal.",
            )
        )
    return tuple(found)


def keys_not_held() -> Gate:
    return Gate(
        10,
        "keys_not_held",
        "the first read of the window was refused: the unlock variable is not exported in this "
        "shell. This attempt is counted (a reveal_open line exists) and no look was taken; "
        "the next attempt is a new reveal_id.",
    )


def window_not_ingested(short: dict[str, dt.date], expected: dt.date) -> Gate:
    named = ", ".join(f"{symbol} ends {day}" for symbol, day in sorted(short.items()))
    return Gate(
        11,
        "window_not_ingested",
        f"the curated bars do not reach the last session at or before holdout_end ({expected}): "
        f"{named}. Ingest the window first; a short window is not the registered read.",
    )


def canary_disagrees(differences: Sequence[str]) -> Gate:
    return Gate(
        12,
        "canary_disagrees",
        "a curated frame read at dev_end changed across the raw-to-curated rewrite, so the dev "
        f"figures may no longer reproduce: {'; '.join(differences)}",
    )


def champion_ambiguous(champion: Champion) -> Gate:
    return Gate(
        13,
        "champion_ambiguous",
        "the champion is not determined: the registered rule does not break a tie in QLIKE. "
        f"Candidates: {', '.join(champion.candidates)}. The machine names nobody.",
    )


def truncation_disagreed(configurations: Sequence[str]) -> Gate:
    return Gate(
        14,
        "truncation_disagreed",
        "the truncation-equivalence check disagreed with the persisted features on "
        f"{', '.join(configurations)}: a leakage guard failed, which section 3 counts as a "
        "failed run.",
    )


def configuration_refused(configurations: Sequence[str], kept: int) -> Gate:
    return Gate(
        15,
        "configuration_refused",
        f"the driver refused {', '.join(configurations)}: either the cohort's declared columns "
        "do not cover the window or a call read past its cutoff; the refusal line printed "
        "under that configuration's heading above says which. The reveal completed its reads. "
        f"The {kept} trial(s) already appended to the ledger stay on the ledger: they are real "
        "trials and every configuration appends as it finishes (R74). Nothing was withdrawn.",
    )


def content_sha256(path: Path) -> str | None:
    """The SHA-256 of the file's bytes, or ``None`` when it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def committed_blob(repo_root: Path, relative: str) -> str | None:
    """``git rev-parse HEAD:<relative>``, or ``None`` when git cannot name it.

    ``--no-optional-locks`` and the timeout are copied from ``store._git_state``, whose comment
    says what a missing lock flag cost this project; failing closed to ``None`` trips the gate.
    """
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo_root), "rev-parse", f"HEAD:{relative}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def tail_completion(settings: Settings, horizon: int) -> dt.date:
    """The session ``horizon`` sessions after ``holdout_end``: when that horizon's tail completes.

    Printed beside gate ``window_open`` and used by nothing else (R217).
    """
    return nth_session_after(get_calendar(settings), settings.splits.holdout_end, horizon)


# ---- the seal's state, derived from the record and from nothing else -----------------------------


class SealState(StrEnum):
    UNTOUCHED = "untouched"  # neither line exists
    COUNTED = "counted"  # a reveal_open without its reveal_complete, and no look lines (R220)
    COUNTED_WITH_LOOKS = "counted_with_looks"  # the same, but sealed looks are in the log (R220)
    READ_UNCOMMITTED = "read_uncommitted"  # both lines on disk, not yet committed
    LIFTED = "lifted"  # both lines, committed at HEAD


SEAL_SENTENCES: dict[SealState, str] = {
    SealState.UNTOUCHED: (
        "seal: no reveal line exists. Nothing was written; the reveal is still available."
    ),
    SealState.COUNTED: (
        "seal: INTACT. A reveal attempt is on record without its completion, so it is counted "
        "and lifts nothing; nothing was read. The next take is a new reveal_id."
    ),
    # The one template: ``{looks}`` is filled by ``seal_sentence`` from the tally, never typed.
    SealState.COUNTED_WITH_LOOKS: (
        "seal: INTACT at HEAD, and this attempt is counted, but sealed looks were taken and "
        "holdout data has been seen: the log holds {looks} sealed looks and no completion. "
        "A later run is not a first look."
    ),
    SealState.READ_UNCOMMITTED: (
        "seal: the holdout HAS BEEN READ and this run was the reveal; the figures are in the "
        "reveal_complete record. The seal lifts when data/holdout_access.jsonl is committed. "
        "Commit it; do not run take again."
    ),
    SealState.LIFTED: (
        "seal: LIFTED. A completed reveal record is committed at HEAD; the holdout is ordinary "
        "history."
    ),
}


def seal_state(tally: AuditTally, *, committed: bool) -> SealState:
    """What the record says, a pure function of the tally and of what HEAD holds. Not of an exit
    code and not of which gate fired: an exit code is an enumeration that goes stale."""
    if committed:
        return SealState.LIFTED
    if tally.completed:
        return SealState.READ_UNCOMMITTED
    if tally.attempts:
        return SealState.COUNTED_WITH_LOOKS if tally.looks else SealState.COUNTED
    return SealState.UNTOUCHED


def seal_sentence(tally: AuditTally, *, committed: bool) -> str:
    """The line the operator is left with: the state's sentence, with the log's own look count."""
    return SEAL_SENTENCES[seal_state(tally, committed=committed)].format(looks=tally.looks)


# ---- the span, declared before it is taken -----------------------------------------------------


@dataclass(frozen=True)
class Read:
    stage: str  # "canary-before" | "A" | "canary-after" | "B" | "C" | "D"
    dataset: str
    tier: Tier
    symbols: tuple[str, ...]
    end: dt.date  # settings.splits.holdout_end for a sealed read, dev_end for the canary
    sealed: bool


def canary_keys(settings: Settings) -> tuple[tuple[str, str], ...]:
    """The ten ``(dataset, symbol)`` pairs the canary reads at ``dev_end``."""
    return (
        *((BARS_DATASET, symbol) for symbol in settings.universe),
        *((VIX_DATASET, name) for name in VIX_SERIES),
        *((FRED_DATASET, name) for name in FRED_SERIES_NAMES),
    )


def plan(settings: Settings) -> tuple[Read, ...]:
    """Every read ``take`` makes, in execution order: 38 sealed looks and the canary's free reads.

    A declaration the stage functions are asserted against (W7), not a script they execute.
    """
    end = settings.splits.holdout_end
    dev = settings.splits.dev_end
    canary = canary_keys(settings)

    def reads(
        stage: str, items: Sequence[tuple[str, Tier, str]], when: dt.date, sealed: bool
    ) -> list[Read]:
        return [Read(stage, d, t, (s,), when, sealed) for d, t, s in items]

    out: list[Read] = []
    out.extend(reads("canary-before", [(d, Tier.CURATED, s) for d, s in canary], dev, False))
    stage_a: list[tuple[str, Tier, str]] = []
    for symbol in settings.universe:
        stage_a.extend([("nasdaq_bars", Tier.RAW, symbol), ("nasdaq_dividends", Tier.RAW, symbol)])
    stage_a.extend([("cboe_vix", Tier.RAW, name) for name in VIX_SERIES])
    stage_a.extend([("fred", Tier.RAW, name) for name in FRED_SERIES_NAMES])
    out.extend(reads("A", stage_a, end, True))
    out.extend(reads("canary-after", [(d, Tier.CURATED, s) for d, s in canary], dev, False))
    out.extend(reads("B", [(d, Tier.CURATED, s) for d, s in canary], end, True))
    targets = [
        (targets_dataset(horizon), Tier.FEATURES, symbol)
        for symbol in settings.universe
        for horizon in HORIZONS
    ]
    out.extend(reads("C", targets, end, True))
    persisted = [(FEATURE_SET, Tier.FEATURES, symbol) for symbol in settings.universe]
    out.extend(reads("D", persisted, end, True))
    return tuple(out)


def expected_looks(settings: Settings) -> int:
    return len([read for read in plan(settings) if read.sealed])


# ---- the fingerprint ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Fingerprint:
    """What makes the result attributable. Taken before the first sealed read, never after."""

    preregistration_path: str
    preregistration_sha256: str
    preregistration_blob: str
    registering_commit: str
    git_sha: str
    git_dirty: bool
    dev_end: str
    holdout_start: str
    holdout_end: str
    stride: int
    minimum_training: int
    cohorts: tuple[str, ...]
    losses: tuple[str, ...]
    benchmarks: tuple[str, ...]
    trials_registered: int
    trials_at_read: int
    trials_delta: int
    comparisons_registered: int
    comparisons_at_read: int
    comparisons_delta: int
    planned_looks: int
    # None on the open line; filled on the complete line.
    champion: dict[str, object] | None = None

    def record(self) -> dict[str, object]:
        record = dataclasses.asdict(self)
        for key in ("cohorts", "losses", "benchmarks"):
            record[key] = list(getattr(self, key))
        return record


def fingerprint(
    settings: Settings,
    *,
    sha: str,
    dirty: bool,
    sha256: str,
    blob: str,
    trials_at_read: int,
    comparisons_at_read: int,
) -> Fingerprint:
    return Fingerprint(
        preregistration_path=PREREGISTRATION.as_posix(),
        preregistration_sha256=sha256,
        preregistration_blob=blob,
        registering_commit=REGISTERING_COMMIT,
        git_sha=sha,
        git_dirty=dirty,
        dev_end=settings.splits.dev_end.isoformat(),
        holdout_start=settings.splits.holdout_start.isoformat(),
        holdout_end=settings.splits.holdout_end.isoformat(),
        stride=STRIDE,
        minimum_training=MINIMUM_TRAINING,
        cohorts=tuple(cohort.name for cohort, _ in RUNS),
        losses=tuple(LOSSES),
        benchmarks=BENCHMARKS,
        trials_registered=REGISTERED_TRIALS,
        trials_at_read=trials_at_read,
        trials_delta=trials_at_read - REGISTERED_TRIALS,
        comparisons_registered=REGISTERED_COMPARISONS,
        comparisons_at_read=comparisons_at_read,
        comparisons_delta=comparisons_at_read - REGISTERED_COMPARISONS,
        planned_looks=expected_looks(settings),
    )


# ---- the champion -------------------------------------------------------------------------------


class ChampionStatus(StrEnum):
    NAMED = "named"  # one contestant: the lowest QLIKE in the 10% set
    NONE = "none"  # the 10% set was refused: section 3's permitted outcome, and a result
    AMBIGUOUS = "ambiguous"  # an exact tie in QLIKE, which section 3 gives no way to break


@dataclass(frozen=True)
class Champion:
    status: ChampionStatus
    name: str | None  # None unless NAMED
    qlike: float | None
    members: tuple[str, ...]  # the 10% set in cohort order, or ()
    qlike_by_member: dict[str, float]
    unclaimed: tuple[str, ...]  # members whose significance R115 withholds
    candidates: tuple[str, ...]  # the tied contestants when AMBIGUOUS
    refusal: str | None  # the confidence set's own sentence when NONE
    rule: str  # the verbatim sentence from section 3
    statement: str  # what the write-up says about the champion, in the same breath

    def record(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "name": self.name,
            "qlike": self.qlike,
            "members": list(self.members),
            "qlike_by_member": dict(self.qlike_by_member),
            "unclaimed": list(self.unclaimed),
            "candidates": list(self.candidates),
            "refusal": self.refusal,
            "rule": self.rule,
            "statement": self.statement,
        }


def champion_from(comparison: Comparison, trials: Sequence[Trial]) -> Champion:
    """Section 3's rule, as read by R216, applied to the (SPY, h=1, qlike) record and its trials.

    Pure, and handed the two objects rather than reading a ledger. ``trials`` are the in-memory
    ``Trial`` objects this run produced, so the champion comes from the same objects the
    confidence set was built from. Membership and the lowest QLIKE decide it; a member whose
    significance is withheld (``unclaimed``) is **named** all the same and the statement says,
    in the same breath, that its lead carries no claim. An exact tie is a tie.
    """
    size = CHAMPION[3]
    refusal = comparison.mcs.refusals.get(size)
    if refusal is not None or size not in comparison.mcs.members:
        return Champion(
            ChampionStatus.NONE,
            None,
            None,
            (),
            {},
            (),
            (),
            refusal,
            THE_RULE,
            "There is no champion: the registered rule says so, and that is the result.",
        )
    members = comparison.mcs.members[size]
    qlike_by_member = {
        trial.model: trial.value
        for trial in trials
        if trial.loss == CHAMPION[2] and trial.model in members
    }
    missing = [name for name in members if name not in qlike_by_member]
    if missing:
        raise ValueError(f"set members {missing} have no {CHAMPION[2]} trial to rank them by")
    unclaimed = tuple(name for name in members if name in comparison.unclaimed)
    lowest = min(qlike_by_member.values())
    best = tuple(name for name in members if qlike_by_member[name] == lowest)
    if len(best) > 1:
        return Champion(
            ChampionStatus.AMBIGUOUS,
            None,
            lowest,
            members,
            qlike_by_member,
            unclaimed,
            best,
            None,
            THE_RULE,
            f"{' and '.join(best)} tie exactly on QLIKE and the rule gives no way to break it, "
            "so the machine names nobody.",
        )
    name = best[0]
    statement = f"{name} is the champion under the registered rule."
    if name in unclaimed:
        statement = f"{statement} {WITHHELD_SENTENCE}"
    return Champion(
        ChampionStatus.NAMED,
        name,
        lowest,
        members,
        qlike_by_member,
        unclaimed,
        (),
        None,
        THE_RULE,
        statement,
    )


def champion_block(champion: Champion, cohort: Cohort, sha: str) -> dict[str, object]:
    """The champion's configuration: what was named, among whom, and the tree that built it."""
    block = champion.record()
    block["cohort"] = cohort.name
    block["contestants"] = [model.name for model in cohort.models]
    block["domains"] = {model.name: repr(model.domain) for model in cohort.models}
    block["git_sha"] = sha
    return block


# ---- the stages ---------------------------------------------------------------------------------

Report = Callable[[str], None]


@dataclass(frozen=True)
class Snapshot:
    frames: dict[tuple[str, str], pd.DataFrame]


def snapshot(settings: Settings) -> Snapshot:
    """The canary's ten curated frames at ``dev_end``: free reads, never looks."""
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    for dataset, symbol in canary_keys(settings):
        frame = load_panel(
            dataset, end=settings.splits.dev_end, symbols=[symbol], settings=settings
        )
        frames[(dataset, symbol)] = frame.sort_values(["ts", "as_of"]).reset_index(drop=True)
    return Snapshot(frames)


def compare_snapshots(before: Snapshot, after: Snapshot) -> tuple[str, ...]:
    """The ``dataset/symbol`` pairs whose frames differ, each with the first difference."""
    differences: list[str] = []
    for key in sorted(before.frames):
        try:
            assert_frame_equal(before.frames[key], after.frames[key])
        except AssertionError as error:
            differences.append(f"{key[0]}/{key[1]}: {str(error).splitlines()[0]}")
    return tuple(differences)


def derive_curated(settings: Settings, *, end: dt.date, report: Report) -> dict[str, int]:
    """Stage A, raw to curated: the existing ``curate_*`` called, not reimplemented. 13 looks."""
    written: dict[str, int] = {}
    for symbol in settings.universe:
        written[f"{BARS_DATASET}/{symbol}"] = curate_bars(
            symbol, end=end, settings=settings, allow_holdout=True
        )
    for name in VIX_SERIES:
        written[f"{VIX_DATASET}/{name}"] = curate_vix(
            name, end=end, settings=settings, allow_holdout=True
        )
    for name in FRED_SERIES_NAMES:
        written[f"{FRED_DATASET}/{name}"] = curate_fred(
            name, end=end, settings=settings, allow_holdout=True
        )
    for key, count in written.items():
        report(f"  curated {key}: {count} partitions")
    return written


def read_bars(settings: Settings, *, end: dt.date, report: Report) -> dict[str, pd.DataFrame]:
    """Stage B's first three looks, deliberately first so a short window is found at look 16."""
    bars = {
        symbol: load_bars(settings, symbol, end=end, allow_holdout=True)
        for symbol in settings.universe
    }
    report(f"  read curated bars for {len(bars)} symbols")
    return bars


def last_sessions(bars: dict[str, pd.DataFrame]) -> dict[str, dt.date]:
    return {symbol: max(as_of_dates(frame)) for symbol, frame in bars.items()}


def expected_last_session(settings: Settings) -> dt.date:
    """The last XNYS session at or before ``holdout_end``."""
    end = settings.splits.holdout_end
    return sessions_between(get_calendar(settings), end - dt.timedelta(days=10), end)[-1]


def short_window(settings: Settings, bars: dict[str, pd.DataFrame]) -> Gate | None:
    """Gate ``window_not_ingested``: a symbol whose curated bars stop short of the window's end."""
    expected = expected_last_session(settings)
    short = {s: day for s, day in last_sessions(bars).items() if day < expected}
    return window_not_ingested(short, expected) if short else None


# The raw dataset stage A curates the bars from; ``curate_bars`` reads it by this literal name.
_RAW_BARS_DATASET = "nasdaq_bars"


def raw_bars_frames(settings: Settings) -> dict[str, pd.DataFrame]:
    """Each symbol's raw bars as a frame of nothing but ``as_of``, from partition names alone.

    ``check`` has to forecast ``window_not_ingested`` and may not read: any raw read past
    ``dev_end`` is a counted look. The partition is named for its session, so the extent is
    knowable without opening a file. The frames are shaped to go through ``short_window`` itself,
    so the forecast is the gate's own comparison and not a second one. A symbol with no
    partitions is absent from the result.
    """
    frames: dict[str, pd.DataFrame] = {}
    for symbol in settings.universe:
        (pattern,) = dataset_glob(settings.storage_root, Tier.RAW, _RAW_BARS_DATASET, [symbol])
        days = {Path(path).parent.name.removeprefix("date=") for path in glob.glob(pattern)}
        if days:
            frames[symbol] = pd.DataFrame({"as_of": sorted(days)})
    return frames


@dataclass(frozen=True)
class Inputs:
    panels: dict[str, pd.DataFrame]
    targets: dict[tuple[str, int], pd.DataFrame]
    persisted: dict[str, pd.DataFrame | None]
    last_session: dict[str, dt.date]


def derive_and_read(
    settings: Settings,
    bars: dict[str, pd.DataFrame],
    *,
    end: dt.date,
    calendar: xcals.ExchangeCalendar,
    report: Report,
) -> Inputs:
    """Stage B's remaining ten looks less the bars, then stage C and stage D.

    Features and targets are written for **only the rows whose ``as_of`` is after ``dev_end``**:
    no dev-dated partition of either is ever opened, so every recorded dev figure stays
    byte-reproducible by construction.
    """
    dev_end = settings.splits.dev_end
    exogenous = [
        frame
        for dataset, series, column in EXOGENOUS_READS
        if (
            frame := load_exogenous(
                settings, dataset, series, column, end=end, report=report, allow_holdout=True
            )
        )
        is not None
    ]
    panels: dict[str, pd.DataFrame] = {}
    for symbol in settings.universe:
        panel = assemble(bars[symbol], exogenous)
        panels[symbol] = panel
        table = build_features(panel, through=end, calendar=calendar)
        fresh = table[[day > dev_end for day in as_of_dates(table)]]
        report(f"  {FEATURE_SET}/{symbol}: {_write_features(fresh, symbol, settings)} partitions")
        returns = _target_panel(bars[symbol], settings)
        for horizon in HORIZONS:
            built = build_targets(returns, horizon=horizon, calendar=calendar)
            kept = built[[day > dev_end for day in as_of_dates(built)]]
            if kept.empty:
                report(f"  {targets_dataset(horizon)}/{symbol}: no holdout-dated row")
                continue
            paths = write_target_partitions(kept, horizon=horizon, symbol=symbol, settings=settings)
            report(f"  {targets_dataset(horizon)}/{symbol}: {len(paths)} partitions")
    targets = {
        (symbol, horizon): load_targets(settings, symbol, horizon, end=end, allow_holdout=True)
        for symbol in settings.universe
        for horizon in HORIZONS
    }
    persisted: dict[str, pd.DataFrame | None] = {}
    for symbol in settings.universe:
        try:
            stored = load_panel(
                FEATURE_SET,
                end=end,
                symbols=[symbol],
                tier=Tier.FEATURES,
                allow_holdout=True,
                settings=settings,
            )
            persisted[symbol] = stored.sort_values("as_of").reset_index(drop=True)
        except DatasetNotFound:
            persisted[symbol] = None
    return Inputs(panels, targets, persisted, last_sessions(bars))


@dataclass(frozen=True)
class Scored:
    comparisons: tuple[Comparison, ...]
    trials: tuple[Trial, ...]
    refused: tuple[str, ...]
    disagreed: tuple[str, ...]
    origins: dict[tuple[str, int], int]


def _stored(inputs: Inputs, symbol: str) -> pd.DataFrame | None:
    return inputs.persisted[symbol]


def score(
    settings: Settings,
    inputs: Inputs,
    *,
    sink: Sink,
    at: dt.datetime,
    sha: str,
    dirty: bool | None,
    calendar: xcals.ExchangeCalendar,
    report: Report,
) -> Scored:
    """Both cohort runs under one ``at``. No reads: everything it needs is in ``inputs``.

    The driver loop is ``compare.__main__._run``'s, restated for the reason ``_persisted`` is
    restated there (that entry point refuses a date past ``dev_end`` and gets its frames from
    ``load_for``, which stays pinned). Every piece that computes anything is called.
    """
    comparisons: list[Comparison] = []
    trials: list[Trial] = []
    refused: list[str] = []
    disagreed: list[str] = []
    origins: dict[tuple[str, int], int] = {}
    for cohort, horizons in RUNS:
        for symbol in settings.universe:
            for horizon in horizons:
                label = f"{cohort.name} {symbol} h{horizon}"
                report(f"{label}:")
                errors = monte_carlo_error(cohort, horizon)
                outcome = run_configuration(
                    settings,
                    symbol,
                    horizon,
                    inputs.panels[symbol],
                    inputs.targets[(symbol, horizon)],
                    calendar=calendar,
                    first=settings.splits.holdout_start,
                    last=settings.splits.holdout_end,
                    every=STRIDE,
                    minimum_training=MINIMUM_TRAINING,
                    cohort=cohort,
                    persisted=partial(_stored, inputs, symbol),
                    append=lambda batch: sink.append_trials(batch, at=at, sha=sha, dirty=dirty),
                    report=report,
                )
                trials.extend(outcome.trials)
                if outcome.refused:
                    refused.append(label)
                    continue
                if outcome.disagrees:
                    disagreed.append(label)
                evaluation = outcome.evaluation or Evaluation(
                    symbol=symbol,
                    horizon=horizon,
                    results=(),
                    features=pd.DataFrame(),
                    skipped=(),
                    requested=0,
                    every=STRIDE,
                    contestants=cohort.contestants,
                    cohort=cohort.name,
                )
                batch_of_comparisons = [
                    analyse(
                        loss_series(evaluation, loss, cohort=cohort.name),
                        monte_carlo_error=errors.percent,
                        monte_carlo_source=errors.source,
                        benchmarks=BENCHMARKS,
                    )
                    for loss in LOSSES
                ]
                sink.append_comparisons(batch_of_comparisons, at=at, sha=sha, dirty=dirty)
                comparisons.extend(batch_of_comparisons)
                origins[(symbol, horizon)] = batch_of_comparisons[0].origins
    return Scored(tuple(comparisons), tuple(trials), tuple(refused), tuple(disagreed), origins)
