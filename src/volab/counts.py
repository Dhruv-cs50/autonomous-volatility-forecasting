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

**What this does and does not claim (R186).** The only enforcement is that the leaderboard
headline prints both counts. Other entry points -- the walk-forward, compare and evaluators CLIs
-- still call ``count_trials`` directly and can print the first ledger's count alone; no import ban
stops them, and none of them was migrated here.

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


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def count_all(root: Path) -> LedgerCounts:
    """The non-blank line count of each ledger under ``root``; an absent file is ``0``."""
    trials = count_trials(root)
    agent_trials = _count_lines(root / AGENT_TRIAL_LEDGER)
    return LedgerCounts(
        trials=trials,
        agent_trials=agent_trials,
        agent_comparisons=_count_lines(root / AGENT_COMPARISON_LEDGER),
        total=trials + agent_trials,
    )
