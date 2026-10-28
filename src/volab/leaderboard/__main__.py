"""``python -m volab.leaderboard`` -- print the two ledgers as a leaderboard.

Read-only, with no flag that writes (``test_the_entry_point_has_no_flag_that_writes_or_unlocks``):
``compare``'s sink is the only writer (R95, R109), and a real
shootout is ``python -m volab.compare run``, not this. The default root is the same one the sink
writes under (``settings.storage_root.parent``); ``--root`` points it at another, which is how the
tests run it over a ``tmp_path``.

Exit status is 0 for a consistent pair of ledgers and 1 if a comparison record has no trial line
to join to (see :func:`volab.leaderboard.render`;
``test_a_comparison_with_no_trials_is_reported_and_exits_non_zero``). A ledger line that cannot be
read makes :func:`volab.leaderboard.render` raise
:class:`~volab.leaderboard.LeaderboardError` rather than skip it, because a skipped line is a
trial missing from the headline
(``test_a_malformed_trial_line_raises_rather_than_being_skipped``); **this entry point reports
that refusal and returns 1 rather than letting a traceback out**, which is what ``compare``'s own
CLI does. A stack trace is the wrong output for a data problem: it cannot be told apart from a bug
in the reader. Refuse-never-skip is unchanged; only the presentation is
(``test_the_cli_reports_a_malformed_line_and_exits_1_without_a_traceback``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from volab.config import load_settings
from volab.leaderboard import LeaderboardError, render


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
    try:
        lines, consistent = render(root)
    except LeaderboardError as unreadable:
        print(unreadable)
        return 1
    for line in lines:
        print(line)
    return 0 if consistent else 1


if __name__ == "__main__":
    sys.exit(main())
