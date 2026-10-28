"""``python -m volab.leaderboard`` -- print the two ledgers as a leaderboard.

Read-only, with no flag that writes: ``compare``'s sink is the only writer (R95, R109), and a real
shootout is ``python -m volab.compare run``, not this. The default root is the same one the sink
writes under (``settings.storage_root.parent``); ``--root`` points it at another, which is how the
tests run it over a ``tmp_path``.

Exit status is 0 for a consistent pair of ledgers and 1 if a comparison record has no trial line
to join to (see :func:`volab.leaderboard.render`). A ledger line that cannot be read raises
:class:`~volab.leaderboard.LeaderboardError` rather than being skipped, because a skipped line is
a trial missing from the headline.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from volab.config import load_settings
from volab.leaderboard import render


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m volab.leaderboard", description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="the directory holding data/trials.jsonl (default: the repository's own)",
    )
    arguments = parser.parse_args(argv)
    root: Path = arguments.root or load_settings().storage_root.parent
    lines, consistent = render(root)
    for line in lines:
        print(line)
    return 0 if consistent else 1


if __name__ == "__main__":
    sys.exit(main())
