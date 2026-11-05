"""The pseudo-champion: the current **dev-window leaderboard winner**, and not the registered one.

Before the holdout reveal there is no registered champion, so the daily forecast is made by the
best contestant the development window has produced so far (R158 Q2, decided by the user). **That
is a dev winner, selected by looking at dev results** -- it is exactly the kind of choice the
reveal's pre-registration exists to prevent from being presented as a finding. So it is labelled
wherever a reader meets it: ``kind="pseudo"`` on every forecast line (the field itself), the
stage's printed output says "NOT the registered champion", and this docstring says what it is.
Nothing under ``volab.daily`` can write the other kind, and that is a property of the types and
not of the spelling of the source: ``volab.daily.forecast.Fingerprint`` has no ``kind`` parameter
(the field is fixed to ``PSEUDO_KIND`` and is not an ``__init__`` argument), neither
``take_fingerprint`` nor ``pseudo_fingerprint`` takes one, and the vocabulary holds no other
member, so a computed string such as ``"reg" + "istered"`` or ``take_fingerprint(kind=var)`` is a
``TypeError`` and not a registered line. ``tests/test_daily_forecast.py`` also scans the package
for a code path that spells the word, as a second line of defence. Only deliberate subversion
(``object.__setattr__`` on a frozen instance, forging a pickle) gets past the types.

**It is derived, not named.** ``pseudo_champion`` reads the trial ledger through
``volab.leaderboard.read_trials`` and ranks with the leaderboard's own ``ranked_trials``, its
public accessor for the ranking its table prints. It does not re-rank and does not read the store.
``champion_model`` then finds the contestant the ledger names, as a model object, so the forecast
stage can fit it; that is the one place this module reaches code that can write a ledger, and it
does so only to *read* a cohort's roster (``volab.walkforward.BASELINE_COHORT``, and
``volab.compare.cohorts.COHORTS`` for the rest, imported on demand because it costs three seconds
of ``arch``). The rule is this module's own choice and is written down so it can be overruled:

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
from volab.leaderboard import TrialLine, ranked_trials, read_trials
from volab.models.base import Cohort, Model
from volab.walkforward import BASELINE_COHORT, BASELINE_COHORT_NAME

# The only kind this package writes. ``volab.daily.forecast`` owns the vocabulary and validates it.
PSEUDO_KIND = "pseudo"

# The loss the registered champion is chosen on (pre-registration, section 3).
CHAMPION_LOSS = "qlike"

# What makes up a champion's definition besides the configuration: the code and settings a forecast
# is computed from. Directories are hashed whole, not file by file, and walked at call time.
_DEFINITION_DIRECTORIES = (
    Path("src") / "volab" / "models",
    Path("src") / "volab" / "walkforward",
    Path("src") / "volab" / "features",
)
# Single files, each named because a forecast is computed through it: the settings and where the
# data is read from (``config``, ``paths``, ``store``), the panel assembly and truncation
# (``panel``), the session calendar (``sessions``), the registry that supplies the model object
# (``compare/cohorts``), this package's own champion and fit (``champion``, ``forecast`` -- whose
# ``MINIMUM_TRAINING`` decides which origins fit), and the lockfile that fixes the numerical
# libraries. Over-coverage is the policy; see ``definition_sources``.
_DEFINITION_FILES = (
    Path("config") / "base.yaml",
    Path("uv.lock"),
    Path("src") / "volab" / "config.py",
    Path("src") / "volab" / "paths.py",
    Path("src") / "volab" / "store.py",
    Path("src") / "volab" / "panel.py",
    Path("src") / "volab" / "sessions.py",
    Path("src") / "volab" / "compare" / "cohorts.py",
    Path("src") / "volab" / "daily" / "champion.py",
    Path("src") / "volab" / "daily" / "forecast.py",
)


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
    ranked = ranked_trials([t for t in scored if t.run == run])
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

    Every ``.py`` under ``src/volab/models``, ``src/volab/walkforward`` and ``src/volab/features``
    (the contestants, the walk-forward that fits and scores them, and the features they are fitted
    on), plus the named files in ``_DEFINITION_FILES``: ``config/base.yaml``, ``uv.lock``, and the
    modules the forecast stage reaches through -- ``config``, ``paths``, ``store``, ``panel``,
    ``sessions``, ``compare/cohorts`` and this package's ``champion`` and ``forecast``. That is the
    **code and settings** a forecast is computed from.

    **Not covered, and so not claimed:** the data (the curated store's bytes are not hashed, so the
    same fingerprint can sit on a forecast from a different panel; the line's ``as_of`` and the
    store's own manifest are what pin that), ``ingest/curate.py`` (the dataset names ``panel``
    reads), ``compare/__init__.py`` and ``identity.py``, and every library not fixed by ``uv.lock``
    (the interpreter itself). An edit to one of those changes a forecast without changing the hash.

    It is a **coarse** definition on purpose. Deriving which file defines a given model name needs a
    registry that does not exist (the cohorts live in ``volab.compare``, which writes ledgers and
    is not imported here), and a hand-kept name-to-file map is a literal that rots silently. Over
    coverage means a harmless edit elsewhere in those directories changes the hash; under
    coverage would let a changed model keep its hash, which is the failure the hash exists for.
    The directories are walked at call time, so a new model file is covered without an edit here;
    the single files are named, and ``tests/test_daily_forecast.py`` pins each one by a literal
    list of its own, so a path dropping out of ``_DEFINITION_FILES`` fails a test.
    """
    files = [repo_root / relative for relative in _DEFINITION_FILES]
    for directory in _DEFINITION_DIRECTORIES:
        files.extend(sorted((repo_root / directory).rglob("*.py")))
    return sorted(files)


def _cohort_named(name: str) -> Cohort | None:
    """The cohort a ledger line's ``cohort`` field names, or ``None`` if none is known by it.

    The baseline is the walk-forward's own (it is deliberately absent from ``COHORTS``, which is
    what the shootout CLI resolves names from). The others come from ``volab.compare.cohorts``,
    imported here and not at the top: it costs three seconds of ``arch`` and a champion from the
    baseline never needs it.
    """
    if name == BASELINE_COHORT_NAME:
        return BASELINE_COHORT
    from volab.compare.cohorts import COHORTS

    return COHORTS.get(name)


def champion_model(champion: PseudoChampion) -> Model:
    """The contestant ``champion`` names, taken from the cohort the ledger run was scored in.

    The cohort is looked up by the name on the ledger line, so the model returned is the one that
    was scored, with its own domain, and not a model that merely shares a name. Raises
    ``NoChampionError`` when the cohort is not one this package can rebuild or the cohort holds no
    model of that name: a ledger naming a contestant that no longer exists cannot be fitted, and
    naming a different one in its place would be the arbitrary pick this module refuses.
    """
    cohort = _cohort_named(champion.cohort)
    if cohort is None:
        raise NoChampionError(
            f"cohort {champion.cohort!r} is not one this package can rebuild a model from, so "
            f"{champion.model!r}, its winner at {champion.symbol} h{champion.horizon}, "
            "cannot be fitted"
        )
    for model in cohort.models:
        if model.name == champion.model:
            return model
    raise NoChampionError(
        f"cohort {champion.cohort!r} holds no model {champion.model!r}; it holds "
        f"{list(cohort.contestants)}"
    )
