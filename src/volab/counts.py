"""The ledgers' line counts, side by side (P5-C3, R183, R186).

``data/trials.jsonl`` is the project's trial ledger. R183 gives the agent its own ledgers,
``data/agent_trials.jsonl`` and ``data/agent_comparisons.jsonl``, so a trial an agent ran is not
in the first file's count. :func:`count_all` counts all three and keeps them separate, with
``total`` the sum of the two *trial* ledgers: ``agent_comparisons`` is comparison records, not
trials, and is reported beside the total rather than inside it.

**This module does not import ``volab.candidate``**, although that module defines the agent
ledgers' paths. It resolves reference models and loads ``arch``, which costs three seconds, and
``volab.leaderboard`` imports this module and was engineered not to pay that
(``leaderboard/__init__.py``). The two agent paths are therefore repeated as literals, and
``tests/test_counts.py`` pins each to ``volab.candidate``'s constant. The trial ledger's count is
``volab.walkforward.count_trials``, the writers' own function.

**What this claims (R186).** ``count_trials`` is reachable only from its definition and this
module: ``tests/test_counts.py`` derives that by walking ``src/volab``, so every surface that prints
a trial count holds the agent ledgers' counts and prints :func:`agent_ledgers_line` beside it.
``volab.agent.__main__`` keeps its own pinned copy because the R192 fence forbids it importing this
module (R212).

An absent ledger counts as zero and is not created.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from volab.walkforward import count_trials

# Repeated from ``volab.candidate``, which is not imported (see the module docstring).
AGENT_TRIAL_LEDGER: Path = Path("data") / "agent_trials.jsonl"
AGENT_COMPARISON_LEDGER: Path = Path("data") / "agent_comparisons.jsonl"


@dataclass(frozen=True)
class LedgerCounts:
    """Line counts of the three ledgers; ``total`` is ``trials + agent_trials`` only."""

    trials: int
    agent_trials: int
    agent_comparisons: int
    total: int


def agent_ledgers_line(counts: LedgerCounts) -> str:
    """The one line every surface printing a trial count prints beside it (R186).

    Zeros are printed when the agent ledgers are absent: an omitted line cannot be read as "no
    agent trials". The wording is the leaderboard's, which builds the line from this function.
    """
    return (
        f"  agent ledgers: {counts.agent_trials} agent trial(s) in "
        f"{AGENT_TRIAL_LEDGER.as_posix()}, {counts.agent_comparisons} agent comparison "
        f"record(s) in {AGENT_COMPARISON_LEDGER.as_posix()}; {counts.total} trial(s) across "
        "both trial ledgers"
    )


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def count_all(root: Path) -> LedgerCounts:
    """The non-blank line count of each ledger under ``root``; an absent file is ``0``.

    ``root`` is the **parent** of the storage root, i.e. ``settings.storage_root.parent``, since
    every ledger path (``data/trials.jsonl``, ``data/agent_trials.jsonl``, ...) already begins with
    ``data/``. Passing ``settings.storage_root`` itself finds no file and reports zeros (R211).
    """
    trials = count_trials(root)
    agent_trials = _count_lines(root / AGENT_TRIAL_LEDGER)
    return LedgerCounts(
        trials=trials,
        agent_trials=agent_trials,
        agent_comparisons=_count_lines(root / AGENT_COMPARISON_LEDGER),
        total=trials + agent_trials,
    )
