"""Evaluate one proposal in a fresh interpreter and log every ``load_panel`` call. A script.

The child half of ``tests/test_agent_rehearsal.py`` (P5-E3). Nothing here is collected by pytest;
it is run as ``[sys.executable, "-W", "error", __file__, ...]``, so each evaluation is the first in
its process, as ``volab.agent._runner`` makes it.

**Why a shim and not ``python -m volab.candidate`` (R206).** ``volab.config`` refuses a storage
root outside the repository and ``volab.candidate.__main__`` loads its settings from there, so the
real entry point cannot be aimed at a synthetic store under ``tmp_path``. This script builds a
``Settings`` by hand instead, which ``load_panel`` accepts so long as ``dev_end`` is not later than
the repository's, and calls ``evaluate_proposal`` with it. **The real entry point's argument
parsing and exit-code mapping are therefore not exercised here**; ``tests/test_candidate_cli.py``
and ``tests/test_agent_runner.py`` own them. The proposal check and the result-file shape are
borrowed from ``volab.candidate.__main__`` rather than restated, so they cannot drift from it.

**The recorder wraps ``load_panel``; it does not replace it.** It writes one line to the log, then
calls through to the real function, so the real read path runs against the synthetic store and the
log is a record of real reads. That is what keeps the witness from being vacuous (R191): a stand-in
for ``load_for`` would mean no read, no log line, and "every call was sealed" true of nothing.

**``load_panel`` is bound by name in several modules** (``volab.panel``, ``volab.walkforward``,
``volab.candidate``, ...), and patching ``volab.store.load_panel`` alone would miss every one of
them. The recorder is installed by the identity sweep ``tests/conftest.py`` uses: every name in
``sys.modules`` whose value *is* the function is rebound. The number of names rebound, and which,
is the first line of the log so the test can assert the recorder was really installed.

**The window is shrunk here and nowhere else.** ``FIRST_ORIGIN``, ``STRIDE`` and
``MINIMUM_TRAINING`` are assigned on the ``volab.candidate`` module, in this process only, to fit a
synthetic store of a few hundred sessions. There is no production flag for it (R184).

The log records an attempt **before** calling through, so a read that the store then refuses is
still logged. This script reads the flag a caller passed and never passes one itself.

``--probe-past-dev-end`` reads one session past ``dev_end`` through the walk-forward module's own
``load_targets`` and then exits without evaluating. It exists to show the witness bites: the call
is refused by the store, because the flag is not passed, and the log still holds the bad ``end``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for entry in (str(ROOT / "src"), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import volab.candidate as candidate  # noqa: E402
import volab.candidate.__main__ as entry_point  # noqa: E402
import volab.store as store  # noqa: E402
import volab.walkforward as walkforward  # noqa: E402
from volab.config import Settings, Splits, load_settings  # noqa: E402
from volab.errors import HoldoutViolation  # noqa: E402

PROBE_EXIT = 0


def names_bound_to(function: object) -> list[tuple[ModuleType, str]]:
    """Every module-level name, in every imported module, whose value *is* ``function``.

    ``tests/conftest.py``'s sweep, restated because that module is pytest's and imports it. Matched
    on identity, so an alias and a reference captured earlier are found as well as the plain name.
    """
    found: list[tuple[ModuleType, str]] = []
    for module in list(sys.modules.values()):
        try:
            items = list(vars(module).items())
        except Exception:  # a module that will not show its namespace has nothing to rebind
            continue
        if vars(module) is globals():
            continue
        found.extend((module, name) for name, value in items if value is function)
    return found


def install_recorder(log: Callable[[dict[str, Any]], None]) -> list[tuple[ModuleType, str]]:
    """Wrap ``volab.store.load_panel`` in a recorder and rebind every name that holds it.

    Returns the ``(module, name)`` pairs rebound. The wrapper reads ``end`` and the unlock flag
    the caller passed (both keyword-only on ``load_panel``), records them, and then calls through
    unchanged, so a read the store refuses is recorded and then refused.
    """
    original = store.load_panel

    def recording(*args: Any, **kwargs: Any) -> Any:
        end = kwargs.get("end")
        log(
            {
                "kind": "call",
                "dataset": args[0] if args else kwargs.get("dataset"),
                "end": end.isoformat() if isinstance(end, dt.date) else None,
                "unlock_flag": kwargs.get("allow_holdout", False),
                "pid": os.getpid(),
            }
        )
        return original(*args, **kwargs)

    bound = names_bound_to(original)
    for module, name in bound:
        module.__dict__[name] = recording
    return bound


def build_settings(config: dict[str, Any]) -> Settings:
    """The hand-built ``Settings`` pointing at the synthetic store (R206, R211).

    Starts from the repository's configuration and replaces the storage root and the splits, so
    the universe and calendar are the real ones. ``dev_end`` is the synthetic store's own, which
    is earlier than the repository's: ``load_panel`` allows a more conservative boundary.
    """
    base = load_settings()
    return replace(
        base,
        storage_root=Path(config["storage_root"]),
        splits=Splits(
            dev_end=dt.date.fromisoformat(config["dev_end"]),
            holdout_start=dt.date.fromisoformat(config["holdout_start"]),
            holdout_end=dt.date.fromisoformat(config["holdout_end"]),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True, help="the store and the window")
    parser.add_argument("--log", type=Path, required=True, help="where the recorder appends")
    parser.add_argument("--probe-past-dev-end", action="store_true")
    arguments = parser.parse_args(argv)

    config = json.loads(arguments.config.read_text(encoding="utf-8"))
    settings = build_settings(config)

    def log(record: dict[str, Any]) -> None:
        with arguments.log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    bound = install_recorder(log)
    log(
        {
            "kind": "installed",
            "rebound": len(bound),
            "names": sorted(f"{module.__name__}.{name}" for module, name in bound),
            "dev_end": settings.splits.dev_end.isoformat(),
            "pid": os.getpid(),
        }
    )

    if arguments.probe_past_dev_end:
        # Through walkforward's own binding, so the probe also shows the sweep reached it.
        past = settings.splits.dev_end + dt.timedelta(days=1)
        try:
            walkforward.load_targets(settings, "SPY", 1, end=past)
        except HoldoutViolation:
            log({"kind": "probe_refused", "end": past.isoformat()})
        return PROBE_EXIT

    candidate.FIRST_ORIGIN = dt.date.fromisoformat(config["first_origin"])
    candidate.STRIDE = int(config["stride"])
    candidate.MINIMUM_TRAINING = int(config["minimum_training"])

    print(f"candidate pid: {os.getpid()}", flush=True)
    proposal = entry_point._read_proposal(arguments.proposal, settings.universe)
    outcome = candidate.evaluate_proposal(
        proposal,
        settings=settings,
        at=dt.datetime(2026, 11, 10, 6, 0, 1, tzinfo=dt.UTC),
        sha="rehearsal",
        dirty=False,
    )
    result = entry_point._result(
        outcome,
        status="refused" if outcome.refused else "ok",
        kind=outcome.refusal_kind,
        reason=outcome.reason,
    )
    result.update(git_sha="rehearsal", git_dirty=False, at="2026-11-10T06:00:01+00:00")
    entry_point._write_result(arguments.result, result)

    print(f"cohort {outcome.cohort}")
    if outcome.refused:
        print(f"REFUSED ({outcome.refusal_kind}): {outcome.reason}")
        return (
            entry_point.EXIT_COVERAGE_REFUSED
            if outcome.refusal_kind == "coverage"
            else entry_point.EXIT_AUDIT_REFUSED
        )
    print(f"trials: {outcome.written} appended, {len(outcome.comparisons)} comparison record(s)")
    return entry_point.EXIT_FAILED if outcome.disagrees else entry_point.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
