"""The pseudo-champion: the current **dev-window leaderboard winner**, and not the registered one.

Before the holdout reveal there is no registered champion, so the daily forecast is made by the
best contestant the development window has produced so far (R158 Q2, decided by the user). **That
is a dev winner, selected by looking at dev results** -- it is exactly the kind of choice the
reveal's pre-registration exists to prevent from being presented as a finding. So it is labelled
wherever a reader meets it: ``kind="pseudo"`` on every forecast line (the field itself), the
stage's printed output says "NOT the registered champion", and this docstring says what it is.
Nothing under ``volab.daily`` writes the other kind: ``kind`` is set here from ``PSEUDO_KIND`` and
nowhere from a parameter, and ``tests/test_daily_forecast.py`` scans the package for a code path
that could.

**It is derived, not named.** ``pseudo_champion`` reads the trial ledger through
``volab.leaderboard.read_trials`` and ranks with the leaderboard's own ``_ranked`` (a private
name, imported because the leaderboard exposes no public winner and ``volab.leaderboard`` is not
this story's to change). It does not re-rank, does not read the store, and does not import the
code that writes a ledger. The rule is this module's own choice and is written down so it can be
overruled:

- **the loss is QLIKE**, the loss the pre-registration's decision rule names for the registered
  champion (``docs/holdout-preregistration.md`` section 3). That rule also restricts to the 10%
  confidence set at SPY h=1; the pseudo-champion does not read the comparison ledger and takes
  the lowest QLIKE of the run, which is a weaker, plainly dev-only selection;
- **the run is the latest** one the ledger holds for the symbol and horizon, by its ``at``. Runs
  are never ranked against each other (R86): a baseline run and a shootout run are different
  experiments, and this ledger holds several per configuration;
- **first place must be unique**. A tie, an empty ledger, a symbol or horizon the ledger does not
  hold, two runs at the same latest instant, or a run with no defined QLIKE each raise
  ``NoChampionError`` with the reason. An arbitrary pick is never made.

The champion does not depend on the forecast date: the ledger is not indexed by one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from volab.errors import VolabError
from volab.leaderboard import TrialLine, _ranked, read_trials

# The only kind this package writes. ``volab.daily.forecast`` owns the vocabulary and validates it.
PSEUDO_KIND = "pseudo"

# The loss the registered champion is chosen on (pre-registration, section 3).
CHAMPION_LOSS = "qlike"

# What makes up a champion's definition besides the configuration: everything a forecast is
# computed from. Directories are hashed whole, not file by file.
_DEFINITION_DIRECTORIES = (
    Path("src") / "volab" / "models",
    Path("src") / "volab" / "walkforward",
    Path("src") / "volab" / "features",
)
_DEFINITION_FILES = (Path("config") / "base.yaml",)


class NoChampionError(VolabError):
    """Raised when the ledger names no unique winner for a configuration, with the reason."""


@dataclass(frozen=True)
class PseudoChampion:
    """The dev winner for one symbol and horizon, and the ledger run it won."""

    symbol: str
    horizon: int
    loss: str
    model: str
    value: float
    origins: int
    cohort: str
    run_at: str
    run_sha: str

    def configuration(self) -> dict[str, object]:
        """What identifies this champion and the run that elected it; it is hashed."""
        return {
            "model": self.model,
            "symbol": self.symbol,
            "horizon": self.horizon,
            "loss": self.loss,
            "cohort": self.cohort,
            "run_at": self.run_at,
            "run_sha": self.run_sha,
        }


def pseudo_champion(root: Path, symbol: str, horizon: int) -> PseudoChampion:
    """The lowest-QLIKE contestant of the latest ledger run for ``symbol`` at ``horizon``.

    ``root`` is the repository root holding ``data/``. Raises ``NoChampionError`` rather than pick
    when the ledger is empty, does not cover the configuration, holds no QLIKE for it, has two
    runs at its latest instant, has no defined QLIKE in the run, or has a tie for first.
    """
    trials, _ = read_trials(root)
    if not trials:
        raise NoChampionError("the trial ledger is empty, so there is no winner to take")
    covered = [t for t in trials if (t.symbol, t.horizon) == (symbol, horizon)]
    if not covered:
        raise NoChampionError(f"the ledger does not cover {symbol} h{horizon}")
    scored = [t for t in covered if t.loss == CHAMPION_LOSS]
    if not scored:
        raise NoChampionError(f"the ledger holds no {CHAMPION_LOSS} trial for {symbol} h{horizon}")
    latest = max(dt.datetime.fromisoformat(t.at) for t in scored)
    runs = {t.run for t in scored if dt.datetime.fromisoformat(t.at) == latest}
    if len(runs) > 1:
        raise NoChampionError(
            f"two runs share the latest instant {latest.isoformat()} for {symbol} h{horizon} "
            f"({sorted(run[2] for run in runs)}), and runs are never ranked against each other"
        )
    (run,) = runs
    ranked = _ranked([t for t in scored if t.run == run])
    first: list[TrialLine] = [t for rank, t in ranked if rank == "1"]
    if not first:
        raise NoChampionError(
            f"no model has a defined {CHAMPION_LOSS} in the latest {symbol} h{horizon} run"
        )
    if len(first) > 1:
        raise NoChampionError(
            f"a tie for first place in the latest {symbol} h{horizon} {CHAMPION_LOSS} run: "
            f"{sorted(t.model for t in first)}"
        )
    (winner,) = first
    assert winner.value is not None  # rank "1" is only ever given to a defined value
    return PseudoChampion(
        symbol=symbol,
        horizon=horizon,
        loss=CHAMPION_LOSS,
        model=winner.model,
        value=winner.value,
        origins=winner.origins,
        cohort=winner.cohort,
        run_at=winner.at,
        run_sha=winner.sha,
    )


def definition_sources(repo_root: Path) -> list[Path]:
    """The files whose bytes are hashed into a champion's fingerprint, sorted.

    ``config/base.yaml`` and every ``.py`` under ``src/volab/models``, ``src/volab/walkforward``
    and ``src/volab/features``: the contestants, the walk-forward that fits and scores them, and
    the features they are fitted on, which is everything a forecast is computed from. It is a
    **coarse** definition on purpose. Deriving which file defines a given model name needs a
    registry that does not exist (the cohorts live in ``volab.compare``, which writes ledgers and
    is not imported here), and a hand-kept name-to-file map is a literal that rots silently. Over
    coverage means a harmless edit elsewhere in those directories changes the hash; under
    coverage would let a changed model keep its hash, which is the failure the hash exists for.
    The directories are walked at call time, so a new model file is covered without an edit here.
    """
    files = [repo_root / relative for relative in _DEFINITION_FILES]
    for directory in _DEFINITION_DIRECTORIES:
        files.extend(sorted((repo_root / directory).rglob("*.py")))
    return sorted(files)
