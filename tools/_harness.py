"""Shared plumbing for the probes in ``tools/``, and nothing a probe would not need.

**This is not production code and cannot become it.** ``tools/`` sits outside ``src/``, carries
no ``__init__.py``, and is not part of the ``volab`` distribution, so nothing here is importable
by the package. ``tests/test_import_discipline.py`` globs ``Path(volab.__file__).parent`` --
``src/volab`` and nothing else -- which is why adding this directory cannot widen the import
ratchet, and ``ALLOWED`` stays ``{"store.py"}``. Every read below goes through
``volab.store.load_panel`` like any other caller: no probe here names Parquet.

**Why the probes import from ``tests/``.** A figure in the decisions log should come from the
code the log cites, not from a second implementation that happens to agree today. So the sweep,
the cutoff sets and the plants are imported from ``tests/test_truncation_equivalence.py`` rather
than restated here. ``tests/`` is not a package, hence ``test_module``.
"""

from __future__ import annotations

import datetime as dt
import importlib
import sys
from pathlib import Path
from types import ModuleType

REPO: Path = Path(__file__).resolve().parents[1]
TESTS: Path = REPO / "tests"

# The three cutoffs M1 was measured at, kept as the default so a re-run diffs against the log.
RECORDED_CUTOFFS: tuple[dt.date, ...] = (
    dt.date(2019, 4, 18),
    dt.date(2023, 6, 15),
    dt.date(2025, 1, 9),
)


def test_module(name: str) -> ModuleType:
    """A module from ``tests/`` by name, with ``tests/`` put on ``sys.path`` first."""
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    return importlib.import_module(name)


def heading(text: str) -> None:
    """A section banner, on stdout, in the form the decisions log quotes."""
    print(f"=== {text} ===")


def note(text: str) -> None:
    """A line that is **not** part of the diffable output: timings, paths, progress.

    On stderr deliberately. Wall clock is the one number in this project that has gone stale
    three times (R38), and a timing taken while two of these probes share a machine is not a
    measurement anyone should quote. Redirect stdout alone and what remains is comparable.
    """
    print(text, file=sys.stderr)


def date_argument(value: str) -> dt.date:
    return dt.date.fromisoformat(value)
