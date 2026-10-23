"""Build one feature table in a process of its own and print its digest. Run as a script.

The child half of ``tests/test_process_isolation.py``. Nothing here is collected by pytest; it is
executed with ``[sys.executable, "-W", "error", __file__, ...]`` so that each build is the
**first** build in its process and no cross-call state can reach it from an earlier one.

Three details are deliberate.

* **``-W error`` is passed by the parent, not configured here.** ``pyproject.toml``'s
  ``filterwarnings = ["error"]`` is pytest's, and pytest is not running in this process, so
  without the flag a warning in a child would pass silently where the same warning in the suite
  is a failure.
* **This script never calls ``load_panel``.** ``conftest``'s autouse ``_forbid_holdout_unlock``
  does not exist here, so a read in this process would be unpoliced. The parent does the reading,
  under the policing, and hands the panel over as a file.
* **``sys.path`` is derived from ``__file__``** rather than inherited, so the child needs no
  environment from the parent and cannot accidentally import a different ``volab``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for entry in (str(ROOT / "src"), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import pandas as pd  # noqa: E402
from byte_identity import canonical_bytes  # noqa: E402

import volab.features.build as build_module  # noqa: E402
from volab.config import load_settings  # noqa: E402
from volab.panel import truncate  # noqa: E402
from volab.sessions import get_calendar  # noqa: E402


def digest(frame: pd.DataFrame) -> dict[str, str]:
    """One sha256 per column over the same canonical bytes the suite compares with.

    Hashed rather than returned whole because this crosses a process boundary as text, and
    hashed with *the comparator's own* canonicaliser so that a digest mismatch means exactly
    what ``assert_byte_identical`` would have meant. A second reduction written for the pipe
    would be free to disagree with the one the rest of the suite uses.
    """
    return {
        str(column): hashlib.sha256(canonical_bytes(frame[column])).hexdigest()
        for column in frame.columns
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("panel", type=Path, help="pickled panel written by the parent")
    parser.add_argument("cutoff", help="the evaluation cutoff, ISO date")
    parser.add_argument("side", choices=("full", "truncated"), help="which panel to build from")
    parser.add_argument(
        "--plant",
        choices=("none", "memo"),
        default="none",
        help="install E2's memoised full-sample statistic before building",
    )
    arguments = parser.parse_args(argv)

    cutoff = dt.date.fromisoformat(arguments.cutoff)
    panel = pd.read_pickle(arguments.panel)

    if arguments.plant == "memo":
        import memo_plant

        memo_plant.reset()
        build_module.rv_proxy = memo_plant.memoised_rv_proxy

    subject = panel if arguments.side == "full" else truncate(panel, cutoff)
    table = build_module.build_features(
        subject, through=cutoff, calendar=get_calendar(load_settings())
    )
    print(json.dumps({"rows": len(table), "columns": digest(table)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
