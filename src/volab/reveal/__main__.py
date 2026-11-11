"""``python -m volab.reveal check`` and ``python -m volab.reveal take``.

``check`` runs every gate that needs no key, prints each, **writes nothing at all** and exits 0
only if every gate passes. It is the command that is safe to type repeatedly. ``take`` is the one
confirmatory read: the same gates, then the span of ``volab.reveal.plan``.

**The human gate is the environment, and this module never touches it.** ``take`` needs both
keys: the stage functions pass ``allow_holdout=True`` and ``VOLAB_HOLDOUT_UNLOCK=1`` must already
be exported by a human. A CLI that set the variable itself would make the reveal one typed command
with no key held.

**Every exit prints what the record says about the seal, and nothing else decides it.** The last
line is a pure function of the audit log on disk and of what HEAD holds (``seal_state``), never of
the exit code: exit 1 is any unexpected exception, so it can mean "nothing happened" or "the
holdout was read and only the printing failed", and only the record can tell those apart.

**Why ``reveal_complete`` is written before the write-up and before exits 13 and 14.** By then the
holdout has been read -- the sealed looks are logged and the figures are in hand. The seal's claim
is that nobody has seen the data, and that claim is false once the reads happened, whatever is
written afterwards. Leaving ``reveal_open`` unpaired would keep the machinery sealed while the data
was seen, and let a later run present itself as the clean first read. Lifting is the honest state.

Exit codes: 0 done; 1 unexpected error; 2 usage (argparse's); 3..9 the pre-read gates, which write
nothing; 10..12 refusals that cost a counted attempt and lift nothing; 13 and 14 completed with
something the reader must be told; 15 the reads completed and a configuration was refused.
The mapping lives in ``volab.reveal``, by gate name.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from volab import reveal, store
from volab.compare import describe
from volab.compare.sink import JsonlSink
from volab.config import Settings, load_settings
from volab.errors import HoldoutViolation
from volab.leaderboard import ranked_trials, read_comparisons, read_trials
from volab.reveal import (
    CHAMPION,
    PREREGISTRATION,
    REGISTERED_COMPARISONS,
    REGISTERED_ORIGINS,
    REGISTERED_TRIALS,
    ChampionStatus,
    Gate,
    canary_disagrees,
    champion_ambiguous,
    champion_block,
    champion_from,
    committed_blob,
    compare_snapshots,
    configuration_refused,
    content_sha256,
    derive_and_read,
    derive_curated,
    expected_looks,
    fingerprint,
    gates,
    keys_not_held,
    last_sessions,
    raw_bars_frames,
    read_bars,
    score,
    seal_sentence,
    short_window,
    snapshot,
    tail_completion,
    truncation_disagreed,
)
from volab.sessions import get_calendar
from volab.walkforward import count_trials

# The repository root: ``src/volab/reveal/__main__.py`` -> three parents up. The dirtiness question
# is asked about it and not about the package (R70).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXCHANGE_TZ = ZoneInfo("America/New_York")


def _now() -> dt.datetime:
    """The one clock call in this package."""
    return dt.datetime.now(tz=dt.UTC)


def _today() -> dt.date:
    return _now().astimezone(_EXCHANGE_TZ).date()


def _committed() -> bool:
    """Whether HEAD holds a completed reveal record. A seam so tests can stand at either end."""
    return store.reveal_is_committed()


def _facts(settings: Settings, repo_root: Path) -> dict[str, object]:
    sha, dirty = store.git_state(repo_root)
    return {
        "git_sha": sha,
        "git_dirty": dirty,
        "preregistration_sha256": content_sha256(repo_root.joinpath(PREREGISTRATION)),
        "preregistration_blob": committed_blob(repo_root, PREREGISTRATION.as_posix()),
        "already_revealed": _committed(),
    }


def _refusals(settings: Settings, repo_root: Path, today: dt.date) -> tuple[Gate, ...]:
    facts = _facts(settings, repo_root)
    return gates(settings, today=today, repo_root=repo_root, **facts)  # type: ignore[arg-type]


def _unchecked(settings: Settings) -> list[str]:
    """What a green ``check`` does not know, in the order the gates would fire in a ``take``."""
    frames = raw_bars_frames(settings)
    end = settings.splits.holdout_end
    lines = [
        "not checked: keys_not_held (10), window_not_ingested (11) and canary_disagrees (12).",
        "  Each is discoverable only by a read, and every read past dev_end is a counted look:",
        "  a reveal_open line plus up to 16 looks, which is a counted attempt. check writes",
        "  nothing, so it cannot ask, and a take that finds one of the three spends that attempt.",
    ]
    gate = short_window(settings, frames) if frames else None
    missing = sorted(set(settings.universe) - set(frames))
    if gate is None and not missing:
        latest = max(last_sessions(frames).values())
        lines.append(
            f"  window_not_ingested is not expected to fire: the latest raw bars session is "
            f"{latest}, holdout_end {end}."
        )
        lines.append("  keys_not_held and canary_disagrees are not forecast either way.")
        return lines
    seen = max(last_sessions(frames).values()) if frames else None
    lines.append(
        f"  window_not_ingested is expected to fire: the latest raw bars session is "
        f"{seen if seen else 'absent'}, holdout_end {end}"
        + (f"; no raw bars at all for {', '.join(missing)}" if missing else "")
        + "."
    )
    lines.append("  The first take may therefore cost a counted attempt; ingest the window first.")
    return lines


def _check(settings: Settings, repo_root: Path, today: dt.date) -> int:
    found = _refusals(settings, repo_root, today)
    for gate in found:
        print(gate.message)
    if found:
        return found[0].code
    print("the gates that need no key and no read pass (3..9)")
    for line in _unchecked(settings):
        print(line)
    print(f"this attempt plans {expected_looks(settings)} sealed looks")
    for horizon in sorted(REGISTERED_ORIGINS):
        print(
            f"  h={horizon} tail completes {tail_completion(settings, horizon)} (R217: not a gate)"
        )
    return 0


def _take(settings: Settings, repo_root: Path, today: dt.date, argv: Sequence[str]) -> int:
    found = _refusals(settings, repo_root, today)
    if found:
        for gate in found:
            print(gate.message)
        return found[0].code
    facts = _facts(settings, repo_root)
    sha = str(facts["git_sha"])
    calendar = get_calendar(settings)
    end = settings.splits.holdout_end
    root = settings.storage_root.parent
    sink = JsonlSink(root)
    print(f"this attempt plans {expected_looks(settings)} sealed looks")

    before = snapshot(settings)
    tally_before = store.audit_tally(settings)
    fp = fingerprint(
        settings,
        sha=sha,
        dirty=False,
        sha256=str(facts["preregistration_sha256"]),
        blob=str(facts["preregistration_blob"]),
        trials_at_read=count_trials(root),
        comparisons_at_read=len(read_comparisons(root)),
    )
    reveal_id = store.record_reveal_opened(settings, details=fp.record())
    with store.reveal_scope(reveal_id):
        try:
            derive_curated(settings, end=end, report=print)
        except HoldoutViolation:
            if store.audit_tally(settings).looks == tally_before.looks:
                gate = keys_not_held()
                print(gate.message)
                return gate.code
            raise
        differences = compare_snapshots(before, snapshot(settings))
        if differences:
            gate = canary_disagrees(differences)
            print(gate.message)
            return gate.code
        bars = read_bars(settings, end=end, report=print)
        gate_or_none = short_window(settings, bars)
        if gate_or_none is not None:
            print(gate_or_none.message)
            return gate_or_none.code
        inputs = derive_and_read(settings, bars, end=end, calendar=calendar, report=print)

    at = _now()
    scored = score(
        settings, inputs, sink=sink, at=at, sha=sha, dirty=False, calendar=calendar, report=print
    )
    if scored.refused:
        gate = configuration_refused(scored.refused, count_trials(root) - fp.trials_at_read)
        print(gate.message)
        return gate.code
    cohort = reveal.RUNS[0][0]
    primary = next(
        c
        for c in scored.comparisons
        if (c.symbol, c.horizon, c.loss, c.cohort) == (*CHAMPION[:3], cohort.name)
    )
    champion = champion_from(primary, scored.trials)
    ledger, _ = read_trials(root)
    mine = [line for line in ledger if line.at == at.isoformat()]
    tally = store.audit_tally(settings)
    complete = dataclasses.replace(fp, champion=champion_block(champion, cohort, sha))
    details = {
        **complete.record(),
        "tally": {"looks": tally.looks, "looks_closed": tally.looks_closed},
        "attempts": len(tally.attempts),
        "origins": {
            f"{symbol} h{horizon}": {"actual": n, "registered": REGISTERED_ORIGINS.get(horizon)}
            for (symbol, horizon), n in scored.origins.items()
        },
        "disagreed": list(scored.disagreed),
    }
    store.record_reveal_completed(settings, reveal_id, details=details)

    # From here the holdout has been read and the record is complete: nothing below may be read as
    # "the reveal did not happen", and ``main`` says what the record holds whatever happens next.
    code = 0
    if champion.status is ChampionStatus.AMBIGUOUS:
        gate = champion_ambiguous(champion)
        print(gate.message)
        code = gate.code
    elif scored.disagreed:
        gate = truncation_disagreed(scored.disagreed)
        print(gate.message)
        code = gate.code
    final = store.audit_tally(settings)
    print(f"{final.looks} sealed reads logged ({final.looks_closed} closed)")
    print(
        f"reveal attempts k = {len(final.attempts)}; the k-th completed: "
        f"{bool(final.attempts) and final.attempts[-1] in final.completed}"
    )
    after_trials = count_trials(root)
    print(
        f"trials: registered {REGISTERED_TRIALS}, at read {fp.trials_at_read} "
        f"(delta {fp.trials_delta}), now {after_trials}"
    )
    print(
        f"comparisons: registered {REGISTERED_COMPARISONS}, at read {fp.comparisons_at_read} "
        f"(delta {fp.comparisons_delta}), now {len(read_comparisons(root))}"
    )
    for (symbol, horizon), n in sorted(scored.origins.items()):
        predicted = REGISTERED_ORIGINS.get(horizon)
        print(f"origins {symbol} h{horizon}: {n} (registered prediction {predicted})")
    for comparison in scored.comparisons:
        for line in describe(comparison, trials=after_trials):
            print(line)
    groups = sorted({(t.symbol, t.horizon, t.cohort, t.loss) for t in mine})
    for symbol, horizon, name, loss in groups:
        group = [
            t
            for t in mine
            if (t.symbol, t.horizon, t.cohort, t.loss) == (symbol, horizon, name, loss)
        ]
        print(f"ranking {symbol} h{horizon} {loss} [{name}] (lower is better):")
        for rank, trial in ranked_trials(group):
            value = "n/a" if trial.value is None else format(trial.value, ".6g")
            print(f"  {rank:>2}. {trial.model:<18} {value}")
    print(f"champion: {champion.status.value}")
    print(champion.statement)
    print(f"rule: {champion.rule}")
    if champion.members:
        print(f"set members: {', '.join(champion.members)}")
    if champion.refusal:
        print(champion.refusal)
    print(f"command: python -m volab.reveal {' '.join(argv)}")
    return code


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    repo_root: Path | None = None,
    today: dt.date | None = None,
) -> int:
    resolved = load_settings() if settings is None else settings
    arguments_list = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="python -m volab.reveal", description=__doc__)
    parser.add_argument("command", choices=("check", "take"), help="check is safe to repeat")
    root = _REPO_ROOT if repo_root is None else repo_root
    try:
        arguments = parser.parse_args(arguments_list)
        day = _today() if today is None else today
        if arguments.command == "check":
            code = _check(resolved, root, day)
        else:
            code = _take(resolved, root, day, arguments_list)
    except SystemExit as stop:
        code = stop.code if isinstance(stop.code, int) else 0
    except Exception:
        traceback.print_exc()
        code = 1
    print(seal_sentence(store.audit_tally(resolved), committed=_committed()))
    return code


if __name__ == "__main__":
    sys.exit(main())
