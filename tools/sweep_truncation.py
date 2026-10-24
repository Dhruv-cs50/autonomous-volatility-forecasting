"""The truncation-equivalence sweep, printed rather than asserted.

Reproduces the tier table and the planted counts recorded in
``docs/superpowers/phase2-decisions.md`` under "The clean row, which matters as much as the red
ones" and "Proof that it bites".

**Why this exists when the sweep already has tests.** A passing pytest run prints "1 passed", not
"0 of 2245 cutoffs disagree". The clean row is a *number* the decisions log quotes, and a number
nobody can print is not evidence. So this script calls ``sweep`` and ``informative_cutoffs``
imported from ``tests/test_truncation_equivalence.py`` -- the cited code, not a second copy -- and
prints the count in that file's own format.

Three tiers, matching the log's table:

* ``--tier synthetic`` -- the fixture present on every checkout, swept exhaustively (79 cutoffs).
* ``--tier real`` -- the curated panel, sampled: a stride plus the derived awkward positions.
* ``--tier real --exhaustive`` -- every informative cutoff in the dev window (2,245). **Minutes,
  not seconds**, and how many depends on the machine; this is the tier that is not per-commit.

**Two ways to install E1**, and the difference matters:

* ``--plant e1`` monkeypatches ``volab.features.build.semivariance_proxy`` in this process. Cheap,
  and enough to reproduce the sweep's counts.
* ``--plant none`` inside a tree that has ``tools/e1_plant.patch`` applied sweeps the **real
  planted source**. That is what the log's figures were taken from, and it is the only form that
  can also show the Phase 1 suite passing: the Phase 1 AST scan reads ``src/volab/features/*.py``
  off disk, so against a monkeypatch it would scan clean source and pass vacuously.
  ``tools/reproduce_e1.py`` drives that route end to end.

    uv run --locked python tools/sweep_truncation.py --tier synthetic --plant e1
    uv run --locked python tools/sweep_truncation.py --tier real --exhaustive
"""

from __future__ import annotations

import argparse
import inspect
import time
from collections.abc import Sequence

from _harness import heading, note, test_module
from measure_e1_power import detectable_cutoffs, returns_of

import volab.features.build as build_module
from volab.config import load_settings
from volab.features import realized
from volab.panel import as_of_dates, load_curated_panel
from volab.sessions import get_calendar, sessions_between

PLANTS = ("none", "e1", "unconditional")

# The marker ``tools/e1_plant.patch`` writes into the source it plants.
PLANT_MARKER = "E1 PLANT"


def planted_in_source() -> bool:
    """Whether the **source on disk** carries the plant, as opposed to this process patching it.

    Anti-vacuity. A sweep run against a clean tree while the operator believes it is planted
    would report the clean numbers and look like a refutation of the whole measurement.
    """
    return PLANT_MARKER in inspect.getsource(realized)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("synthetic", "real"), default="real")
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="every informative cutoff, not the sampled stride (real tier: minutes, not seconds)",
    )
    parser.add_argument(
        "--plant",
        choices=PLANTS,
        default="none",
        help="install a forward peek over build's own binding; 'none' sweeps the tree as it is",
    )
    parser.add_argument("--symbol", default="SPY", help="the subject symbol (default SPY)")
    arguments = parser.parse_args(argv)

    equivalence = test_module("test_truncation_equivalence")
    settings = load_settings()
    calendar = get_calendar(settings)
    note(f"storage root {settings.storage_root}")
    note(f"plant {arguments.plant}, tier {arguments.tier}, exhaustive {arguments.exhaustive}")
    note(f"volab from {build_module.__file__}")
    note(f"source on disk carries the plant: {planted_in_source()}")

    if arguments.plant == "e1":
        build_module.semivariance_proxy = equivalence._e1_semivariance
    elif arguments.plant == "unconditional":
        build_module.semivariance_proxy = equivalence._unconditional_semivariance

    if arguments.tier == "synthetic":
        fixture = equivalence._synthetic(calendar)
        panel, sessions = fixture.panel, list(fixture.sessions)
        cutoffs = equivalence.informative_cutoffs(panel, sessions)
    else:
        panel = load_curated_panel(settings, arguments.symbol, end=settings.splits.dev_end)
        bars = panel[panel["symbol"] == arguments.symbol]
        sessions = list(sessions_between(calendar, min(as_of_dates(bars)), max(as_of_dates(bars))))
        informative = equivalence.informative_cutoffs(panel, sessions)
        if arguments.exhaustive:
            cutoffs = informative
        else:
            # The sampled set is assembled exactly as ``real_cutoffs`` assembles it. The fixture
            # itself cannot be called from outside pytest, so its dataclass is rebuilt from the
            # same three fields rather than reimplementing what it feeds.
            real = equivalence.Real(panel=panel, sessions=tuple(sessions), settings=settings)
            chosen = set(equivalence.strided(informative, equivalence.SAMPLED_CUTOFFS)) | set(
                equivalence.awkward_cutoffs(real).values()
            )
            cutoffs = sorted(chosen)

    # The synthetic tier has no sampled mode: ``informative_cutoffs`` over the fixture *is* the
    # whole set, which is why the log's table calls that row exhaustive.
    whole = arguments.exhaustive or arguments.tier == "synthetic"
    label = f"{arguments.tier.upper()}, {'EXHAUSTIVE' if whole else 'SAMPLED'}"
    heading(label if arguments.plant == "none" else f"{label}, PLANT {arguments.plant.upper()}")
    started = time.perf_counter()
    found = equivalence.sweep(panel, cutoffs, calendar)
    note(f"swept {len(cutoffs)} cutoff(s) in {time.perf_counter() - started:.1f}s")

    # The test file's own wording, so the line diffs against what the log quotes.
    print(f"{len(found)} of {len(cutoffs)} cutoffs disagree: {found[:3]}")

    # The derived check M2 makes possible, and the reason a count alone is weaker evidence: the
    # caught set is compared against the predicted one by set equality. Fewer would mean the
    # sweep is weaker than the leak; more would mean it fires for a reason nobody accounts for.
    if arguments.plant == "e1" or planted_in_source():
        returns = returns_of(panel, arguments.symbol, sessions)
        predicted = detectable_cutoffs(returns, sessions, cutoffs, equivalence.BAND)
        caught = {cutoff for cutoff, _ in found}
        print(f"predicted by M2: {len(predicted)}; caught: {len(caught)}")
        print(f"caught set == predicted set: {caught == predicted}")
        if caught != predicted:
            print(f"  escaped: {sorted(predicted - caught)[:5]}")
            print(f"  unexpected: {sorted(caught - predicted)[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
