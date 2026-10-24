"""E2's class, closed: each build gets a process, so cross-call state cannot survive between them.

Truncation equivalence is the primary point-in-time test, and it does not catch E2. It cannot,
in-process: a memoised full-sample statistic filled on the full-panel run is reused by the
truncated run, so the two builds agree by sharing an answer rather than by both being honest.
Phase 1 measured that escape at **0 of 100** point-in-time failures.

This file closes it by running each side of the comparison in a fresh interpreter. The claim is
not that a particular spelling is forbidden; it is that **no state can reach the second build
from the first**, whatever the spelling, because there is no first build in that process.

**Why this rather than an AST ban on module-level mutable state in the feature package**, which
was the cheaper candidate and was rejected on a measurement rather than on taste. The ban is
blind to state parked on an object the engine is *handed*. ``get_calendar`` is
``lru_cache(maxsize=8)``d and returns the same calendar to every caller, and that object accepts
arbitrary attributes:

```
attribute set on calendar: OK -> {'scale': 1.234}
same object from a second get_calendar: True
memo survives the second call: {'scale': 1.234}
__slots__? ()
```

So a memo written by ``build_features`` survives into the next call with no module-level name in
``src/volab/features/`` at all, and ``test_a_memo_on_the_calendar_defeats_a_package_scoped_ast_ban``
below demonstrates exactly that. A construct scan is sound about the construct it names; it is
the scope that fails here, and widening the scope to "every object any feature function can
reach" is not an AST question any more. Subprocess isolation is behavioural and does not care
where the state was parked.

What it costs: two interpreter starts per cutoff, measured at roughly a second each, so the
sweep here is a handful of cutoffs rather than the hundreds truncation equivalence sweeps
in-process. The two tests are complementary in exactly that way -- breadth in-process, depth
across processes.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import memo_plant
import pytest
from test_truncation_equivalence import Synthetic, _synthetic, informative_cutoffs

import volab.features.build as build_module
from volab.config import load_settings
from volab.panel import truncate
from volab.sessions import get_calendar

CHILD = Path(__file__).resolve().parent / "isolated_build.py"

# Three cutoffs, spread across the fixture's window. Few, because each costs two interpreter
# starts; enough that a plant visible only at one position cannot slip through all of them.
CUTOFF_COUNT = 3


@pytest.fixture(scope="module")
def synthetic() -> Synthetic:
    return _synthetic(get_calendar(load_settings()))


def _cutoffs(synthetic: Synthetic) -> list[dt.date]:
    informative = informative_cutoffs(synthetic.panel, synthetic.sessions)
    step = len(informative) // (CUTOFF_COUNT + 1)
    return [informative[step * (index + 1)] for index in range(CUTOFF_COUNT)]


def _build_in_a_child(
    panel_path: Path, cutoff: dt.date, side: str, plant: str
) -> dict[str, object]:
    """One build, in a process of its own. ``-W error`` because pytest's filters do not reach it."""
    finished = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            str(CHILD),
            str(panel_path),
            cutoff.isoformat(),
            side,
            f"--plant={plant}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, (
        f"the isolated build failed for {side} at {cutoff}:\n{finished.stderr}"
    )
    payload: dict[str, object] = json.loads(finished.stdout)
    return payload


@pytest.fixture(scope="module")
def panel_file(synthetic: Synthetic, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The panel, handed to the children as a file.

    Written by the parent on purpose: a child that read the store itself would be reading without
    ``conftest``'s autouse holdout police, which does not exist in that process. Pickle rather
    than Parquet so the panel's dtypes cross unchanged -- the comparison downstream is byte-exact
    and a Parquet round trip has its own documented asymmetries.
    """
    path = tmp_path_factory.mktemp("isolation") / "panel.pkl"
    synthetic.panel.to_pickle(path)
    return path


def test_the_isolated_build_agrees_with_an_in_process_one_on_clean_code(
    synthetic: Synthetic, panel_file: Path
) -> None:
    """Anti-vacuity: the child must compute the same table the suite does.

    Without this, a child that silently built something else -- a different calendar, a panel
    mangled by the pickle, an import of some other ``volab`` -- would make every comparison below
    pass by comparing two wrong answers to each other.
    """
    cutoff = _cutoffs(synthetic)[0]
    from volab.identity import canonical_bytes

    table = build_module.build_features(
        truncate(synthetic.panel, cutoff), through=cutoff, calendar=get_calendar(load_settings())
    )
    import hashlib

    here = {
        str(column): hashlib.sha256(canonical_bytes(table[column])).hexdigest()
        for column in table.columns
    }
    there = _build_in_a_child(panel_file, cutoff, "truncated", "none")
    assert there["rows"] == len(table)
    assert there["columns"] == here, "the isolated build is not computing the suite's table"


def test_truncation_equivalence_holds_across_process_boundaries(
    synthetic: Synthetic, panel_file: Path
) -> None:
    """The clean row: honest code agrees whether or not the two builds share a process."""
    for cutoff in _cutoffs(synthetic):
        full = _build_in_a_child(panel_file, cutoff, "full", "none")
        cut = _build_in_a_child(panel_file, cutoff, "truncated", "none")
        assert full == cut, f"clean code disagreed across processes at {cutoff}"


def test_a_memoised_full_sample_statistic_escapes_an_in_process_comparison(
    synthetic: Synthetic,
) -> None:
    """E2 escaping, measured here rather than quoted from Phase 1.

    Both builds in one process, full panel first. The second build hits the cache the first one
    filled, so it is normalised by a statistic computed over rows it was never handed -- and the
    two tables agree, which is precisely how the escape passes for honest.

    This test asserts the escape, so it fails the day the escape stops working. That is
    deliberate: the subprocess test below is only worth its cost while this one holds, and a
    guarantee whose justification has quietly evaporated is the thing this project refuses.
    """
    from volab.identity import first_difference

    calendar = get_calendar(load_settings())
    cutoff = _cutoffs(synthetic)[0]

    memo_plant.reset()
    original = build_module.rv_proxy
    try:
        build_module.rv_proxy = memo_plant.memoised_rv_proxy
        # Full panel first: the cache is filled from the whole span.
        full = build_module.build_features(synthetic.panel, through=cutoff, calendar=calendar)
        # Truncated second, in the same process: the cache is reused.
        cut = build_module.build_features(
            truncate(synthetic.panel, cutoff), through=cutoff, calendar=calendar
        )
    finally:
        build_module.rv_proxy = original
        memo_plant.reset()

    assert first_difference(full, cut) is None, (
        "the memoised statistic no longer escapes an in-process comparison, so the subprocess "
        "test below is no longer justified by it; re-measure before relying on either"
    )


def test_process_isolation_catches_the_memoised_statistic_that_escapes_in_process(
    synthetic: Synthetic, panel_file: Path
) -> None:
    """The red case for E2's whole class. Same plant, one process each, caught.

    Nothing about the plant changed between this test and the one above it. The only difference
    is how many interpreters the comparison spans, which is the entire claim.
    """
    caught: list[dt.date] = []
    for cutoff in _cutoffs(synthetic):
        full = _build_in_a_child(panel_file, cutoff, "full", "memo")
        cut = _build_in_a_child(panel_file, cutoff, "truncated", "memo")
        if full != cut:
            caught.append(cutoff)
    assert caught == _cutoffs(synthetic), (
        f"the memoised statistic survived process isolation at "
        f"{sorted(set(_cutoffs(synthetic)) - set(caught))}"
    )


def test_a_memo_on_the_calendar_defeats_a_package_scoped_ast_ban() -> None:
    """Why the AST ban was rejected: cross-call state need not live in the package at all.

    ``get_calendar`` is memoised and hands every caller the same object, and that object takes
    arbitrary attributes. A scan over ``src/volab/features/`` cannot see a memo parked there,
    because there is no module-level name in the package to see. Run rather than reasoned about,
    since ``__slots__`` being ``()`` on the class makes the opposite look plausible.

    Nothing is left behind: the attribute is removed again, and the three dicts a package-scoped
    ban would have had to outlaw -- ``IV_SERIES``, ``FRED_SERIES`` and ``HAR_WINDOWS``, all
    constants -- are named here so the cost side of that rejected option is on the record too.
    """
    settings = load_settings()
    calendar = get_calendar(settings)
    assert getattr(type(calendar), "__slots__", None) == (), "the premise of this test changed"

    marker = "_volab_isolation_probe"
    assert not hasattr(calendar, marker)
    try:
        setattr(calendar, marker, {"scale": 1.234})
        again = get_calendar(settings)
        assert again is calendar, "get_calendar stopped handing back one shared object"
        assert getattr(again, marker) == {"scale": 1.234}, (
            "state no longer survives on the calendar, so a package-scoped AST ban may now be "
            "sufficient; re-measure before swapping the mechanism"
        )
    finally:
        delattr(calendar, marker)

    from volab.features.har import HAR_WINDOWS

    assert isinstance(build_module.IV_SERIES, dict)
    assert isinstance(build_module.FRED_SERIES, dict)
    assert isinstance(HAR_WINDOWS, dict)
