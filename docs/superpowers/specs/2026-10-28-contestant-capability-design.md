# US-3.9 — a contestant declares what it can answer, and the harness acts on the declaration

**Status: design, not built.** Architect story. Written 2026-10-28 on `phase-2-completion-groundwork`,
against `4222f33`. Nothing here is implemented; the seams below are the contract an implementer
builds to.

## The decision, in one paragraph

A contestant declares a **`Domain`**: the largest horizon it has a defined answer for, the feature
columns it needs, and whether its answer is a Monte-Carlo estimate. The declaration is a read-only
property on the `Model` protocol, so it cannot be omitted and cannot live anywhere but on the
contestant. When a cohort is asked for a horizon some member cannot answer, the harness **refuses
the configuration** — it does not drop the member — because `Trial.cohort` is the only record of
which contestants a ledger line was scored against, and `evaluate` already raises `ValueError` to
defend exactly that claim. The inflexibility that refusal buys is paid back by **deriving** the
cohort's legal horizons and the multistep cohort's membership from the declarations, defaulting
`--horizon` to the cohort's own set, and making the refusal name the cohort to run instead. Form 2
(a contestant with a shorter history) is the same refusal keyed on measured coverage rather than on
a declared date, so nothing can go stale. Neither ledger's schema changes, `Evaluation.contestants`
and `trials()` do not change, and the registered contestant sets — seven at h=1, six above — come
out of the derivation unmoved.

## What this is a fix for

Two defects that are one defect.

**Form 1, live today.** `compare`'s CLI accepts `--cohort phase2-shootout --horizon 22`. R114
established that EGARCH(1,1) with skew-t errors has **no finite multi-step conditional variance** —
`sigma2_{t+1} = C*(sigma2_t)**beta*exp(alpha|z_t| + gamma*z_t)` against Hansen's polynomial tails,
so `E[exp(alpha|z| + gamma*z)]` diverges for any `alpha > 0`. Every h>=2 figure it produced was an
estimate of infinity; the dev run read 1.70e+12 at h=22 with one path of 100,000 carrying
99.99999997% of one origin. The correct pairing is `phase2-shootout` at h=1 and
`phase2-shootout-multistep` at h>=2, and today that is a convention an operator must remember.
Verified: `src/volab/compare/__main__.py` has no check, and `cohorts.py`'s own docstring says so —
*"Nothing enforces that pairing"*.

**Form 2, arriving with Phase 3.** `Evaluation.comparable()` intersects the defined-origin set
across all contestants (R56), so a contestant that cannot answer at some origins removes those
origins from **every** contestant's loss. That is correct behaviour and is why adding contestants
moves the baselines. Phase 3's HAR-RV-IV contestant would read the ingested IV family, and the
measurements below show what one of those series would cost the whole leaderboard.

Both failures share one signature: **the output looks entirely ordinary while being meaningless.**
Neither is detectable from the result. So the fix cannot be a check on the result; it has to be a
declaration acted on before any fit, which is the shape R114 already specified — *"the refusal must
be contestant-level and a priori — available from the specification alone, using no draw and no
target"*.

## The measurements this design rests on

Measured on the committed store at `dev_end` = 2025-12-31 via `load_panel` and `build_features`,
SPY, scripts in the session scratchpad. **IV coverage, per series:**

| series | curated rows | first `as_of` | last `as_of` | non-null |
|---|---|---|---|---|
| `VIX` | 2,389 | 2016-07-01 | 2025-12-31 | 2,389 |
| `VIX9D` | 2,389 | 2016-07-01 | 2025-12-31 | 2,389 |
| `VVIX` | 2,389 | 2016-07-01 | 2025-12-31 | 2,389 |
| **`VIX1D`** | **912** | **2022-05-13** | 2025-12-31 | 912 |

**The same coverage as the feature columns a contestant actually sees**, over 2,312 built rows:

| column | source | non-null | first | **interior nulls** |
|---|---|---|---|---|
| `iv_h1`, `iv_var_h1` | VIX1D | 912 | 2022-05-13 | **0** |
| `iv_h5`, `iv_h22`, `vvix` | VIX9D/VIX/VVIX | 2,312 | 2016-10-20 | **0** |
| `baa10y` | FRED | 2,293 | 2016-10-20 | **19** |
| `dgs10`, `t10y2y` | FRED | 2,295 | 2016-10-20 | **17** |

**What a VIX1D contestant would cost every other contestant**, over the shootout window
`--from 2021-01-04 --to 2025-12-31 --every 5`:

| horizon | scoreable origins | kept, column non-null | kept, 250 rows **and the target lag** | **origins lost** |
|---|---|---|---|---|
| 1 | 251 | 182 | 132 | **119 (47.4%)** |
| 5 | 250 | 181 | 130 | **120 (48.0%)** |
| 22 | 247 | 178 | 124 | **123 (49.8%)** |
| 66 | 238 | 169 | 106 | **132 (55.5%)** |

**The last column carries the target lag, and the first draft of this table did not.** `evaluate`
counts a training row only if its target had completed by the origin, so a base session `b` counts
at origin `t` only when `b + h <= t`. The qualifying origin is therefore `h` sessions **after** the
250th non-null session of the column, not the 250th session itself: VIX1D's 250th session is
2023-05-11, and the first runnable origin is 2023-05-12 at h=1, 2023-05-18 at h=5, 2023-06-13 at
h=22 and **2023-08-16 at h=66**. Ignoring the lag understates the cost by 1, 4 and 13 origins at
h=5, 22 and 66 — a flat 119 at every horizon, which was the tell that the first measurement was
wrong. At h=22, 69 of the 247 origins have a null `iv_h1` and **every one of them precedes
2022-05-13**; a further 54 are lost to training sufficiency. By contrast a FRED
column costs **3 of 247** origins, scattered interior (bond holidays with XNYS open: 2022-11-14 and
2025-10-14 are two). **So the two kinds of gap are structurally different and the design treats them
differently:** a leading boundary is a window error and refuses; an interior gap is a data accident
and skips the origin for everyone with a recorded reason.

For contrast, R121 measured that removing EGARCH from the seven-contestant cohort changed **no**
surviving contestant's origin count — the intersection was not silently paying for R114. 119 of 251
is not zero, and that difference is the whole reason Form 2 needs a mechanism and Form 1's
contestant did not reveal one.

## 1. The shape of the declaration

**Decision: a read-only `domain` property on the `Model` protocol, returning a frozen `Domain`
dataclass defined in `src/volab/models/base.py`.**

```python
# src/volab/models/base.py -- added. Pure stdlib and pandas, as the whole module is.

@dataclass(frozen=True)
class Measurement:
    """One Monte-Carlo error, measured once, with exactly what it was measured on.

    ``source`` is compared **by equality** to ``volab.compare.series_source``, i.e.
    ``f"{symbol} h{horizon}"``. A figure whose source does not match can only withhold a claim,
    never authorise one (R115), so a mistyped source fails safe.
    """

    percent: float
    source: str
    horizon: int
    # The path count it was measured at. A figure measured at another count does not apply.
    paths: int


@dataclass(frozen=True)
class Sampling:
    """Declared by a contestant whose forecast is a Monte-Carlo estimate rather than a value.

    ``__post_init__`` refuses *this contestant's own* measurements if they disagree on ``source``.
    The **cross-contestant** rule cannot live here -- a ``Sampling`` sees only one contestant -- so
    it lives in ``monte_carlo_error``, which is the first place that holds a whole cohort's figures.
    """

    paths: int
    measured: tuple[Measurement, ...] = ()

    def __post_init__(self) -> None: ...

    def error(self, horizon: int) -> float | None:
        """The measured error in percent at ``horizon`` **for the path count in use**, else None."""

    @property
    def source(self) -> str | None:
        """The one series every measurement was made on; ``None`` when there are none."""


@dataclass(frozen=True)
class Domain:
    """What a contestant declares it is able to answer. Read by the harness, never inferred.

    ``max_horizon`` ``None`` means unbounded. ``requires`` names **feature columns**, the names
    ``volab.features.build`` writes, because a contestant sees columns and never the panel.
    """

    max_horizon: int | None = None
    requires: tuple[str, ...] = ()
    sampling: Sampling | None = None

    def answers(self, horizon: int) -> bool:
        return self.max_horizon is None or horizon <= self.max_horizon


UNRESTRICTED = Domain()


@runtime_checkable
class Model(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def fit_depends_on_horizon(self) -> bool: ...
    # NEW. Read-only for the reason already recorded on ``name`` and ``Forecaster.observations``:
    # a settable Protocol member is satisfied only by a mutable attribute, which ``mypy --strict``
    # refuses for a frozen dataclass -- and frozen dataclasses are this project's idiom.
    @property
    def domain(self) -> Domain: ...
    def fit(self, view: TrainingView) -> Forecaster: ...
```

**Why a property on `Model` and not the alternatives.**

- **Not a dataclass field on the protocol.** Impossible, and `base.py` already records why: a
  settable member is satisfied only by a mutable attribute, so `mypy --strict` rejects every frozen
  dataclass contestant with *"expected settable variable, got read-only attribute"*. A read-only
  member accepts a frozen dataclass, a class attribute and a property alike.
- **Not a method `answers(horizon) -> bool` on `Model`.** A predicate cannot explain itself. The
  refusal message has to say *"`egarch11_skewt` answers h<=1"*, and a bound can be printed while a
  closure cannot. A predicate also cannot be enumerated, so `Cohort.horizons` would have to
  probe it horizon by horizon, which means `models/base.py` importing the configured horizon set
  it currently knows nothing about. `Domain.answers` exists as the single reader of `max_horizon`
  precisely so that a future non-prefix domain changes one method and nothing else.
- **Not held by the `Cohort`.** This is R119's failure exactly: `MONTE_CARLO_ERROR_PERCENT` is a
  hand-maintained table keyed by `(name, horizon)` sitting one module away from the knowledge, and
  it *"went stale the moment a real measurement existed"*. A cohort-held horizon map would be the
  same artifact with the same failure mode. Everything the cohort knows is **derived** below.
- **Not "let the contestant raise at every origin".** This needs no interface change at all, which
  is why it is tempting, and it is ruled out by a fact in the record rather than by taste. A
  `ModelSkip` at every origin writes NaN for that contestant everywhere, its `scored()` set is
  empty, and `Evaluation.comparable()` intersects to the **empty set** — so all seven contestants
  get `origins=0` and a null value. R114 names this: *"a per-origin skip would empty every one of
  the seven contestants' sample at that horizon."* Confirmed by reading `comparable()`.

**The price, stated.** `max_horizon` expresses only a **prefix** of the configured horizons
`(1, 5, 22, 66)` — it can say `{1}`, `{1,5}`, `{1,5,22}` or all of them, and cannot say `{1,22}` or
"h>=5 only". No contestant today or in Phase 3's named plans needs a non-prefix domain, and
EGARCH's limit is intrinsically an upper bound (h=1 takes no expectation; every h>=2 does). The day
a non-prefix domain exists, `max_horizon` becomes `horizons: frozenset[int] | None` and
`Domain.answers` is the only body that changes, because it is already the only reader.

**Second price.** `Model` gaining a required member means every implementor declares one. That is
4 contestant classes in `src/` and roughly 18 spy classes across `tests/model_conformance.py`,
`tests/test_model_interface.py`, `tests/test_recalibrated.py` and `tests/test_compare.py`. Each is
a one-line `domain = UNRESTRICTED`, which is why `UNRESTRICTED` is shipped. `mypy` runs
`files = ["src"]` so there is no type churn in tests; the binding check in tests is
`conformance_suite`'s `assert isinstance(model, Model)` against the `runtime_checkable` protocol.
This cost is accepted rather than avoided: a declaration that can be omitted defaults to the
permissive answer, which is the dangerous direction.

**Where `GarchModel`'s bound comes from: derived, not declared.**

```python
# src/volab/models/garch.py
    @property
    def domain(self) -> Domain:
        return Domain(
            # R114, from the specification and the error law alone. EGARCH(1,1) with a
            # polynomial-tailed error law and alpha > 0 has no finite multi-step conditional
            # variance, so only h=1 is credible.
            max_horizon=1 if self.volatility == "EGARCH" else None,
            sampling=(
                Sampling(paths=self.simulations) if self.method == "simulation" else None
            ),
        )
```

Derived from `volatility` rather than added as a field, for a measured reason: `GarchModel`'s
`__post_init__` already enforces a `(volatility, o, method)` legality table, and
`tests/test_garch.py:303-317` constructs `GarchModel("x", volatility, o, method)` positionally over
every pairing. A new `max_horizon` field with a pairing rule would break that test and give
something a future caller could set wrongly. A derivation can only be *expressed* one way, which is
strictly better on the staleness axis than a field that must be set right.

The derivation is sound **only because `dist="skewt"` is hard-coded** at `garch.py:411` — R114's
result is about the error law, not about the word EGARCH. That dependency is load-bearing and
invisible, so it gets a witness: see W10.

## 2. What the harness does with a contestant outside its domain

**Decision: refuse the configuration. Do not drop the contestant.** This is the heart of the design
and the reasoning is a chain of constraints the repository already enforces.

1. **The contestant set is part of the number.** `Evaluation.comparable()` intersects the
   defined-origin set across the cohort, so a contestant's presence changes every other
   contestant's loss. R75 made this argument about the stride and R86 extended it to the cohort.
2. **`Trial.cohort` is the only record of that set on a ledger line.** `data/trials.jsonl` carries
   `cohort` as a name and nothing else about membership. Its own docstring: *"A HAR trial scored
   over a seven-contestant intersection is a different experiment from one scored over three."*
3. **`evaluate` already raises `ValueError` to defend precisely this claim.** The reserved-name
   guard refuses a cohort calling itself `phase2-baseline` with other members, because *"a cohort
   name is a claim about which experiment produced a ledger line"*.
4. **So dropping under the same name breaks an invariant the code enforces today — and the
   collision is already in the committed data, not hypothetical.** Counted from
   `data/trials.jsonl` (414 lines):

   | cohort | h=1 | h=5 | h=22 | h=66 |
   |---|---|---|---|---|
   | `phase2-baseline` | 18 | 18 | 18 | 18 |
   | `phase2-shootout` | 98 | **14** | **14** | 0 |
   | `phase2-shootout-multistep` | 0 | 72 | 72 | 72 |

   **There are already 14 `phase2-shootout` lines at h=5 and 14 at h=22, and they mean seven
   contestants** — the crashed run R112 counted rather than discarded, at 7 x 1 symbol x 2 losses.
   So a drop design writing six-contestant lines under `phase2-shootout` at h=22 would put two
   different contestant sets under one name **at the same horizon in the same file**, 14 lines
   meaning seven and the new ones meaning six, with nothing on either to tell them apart. That is
   not a risk to weigh; it is a contradiction the ledger would already contain on the first run.
5. **Repairing drop requires a ledger schema change, and R124 already refused a smaller one.** The
   repair is a `contestants` array on every trial line. R124 refused adding a single `method` string
   because the leaderboard *"already partitions by `(at, git_sha)`"* so per-trial self-description is
   redundant at the level anyone reads, and because a field meaningless to four of seven contestants
   is dead weight on 264 lines. A seven-element array on all 414 lines is worse on both counts: it is
   redundant with `cohort` for every existing line, and an absent field on the old lines means
   "whatever the cohort name meant then", which a reader cannot recover. `data/trials.jsonl` is
   append-only, git-tracked, and its line count (414) is a **registered** number.
6. **Refusal is what is left, and it reproduces the registered pairing exactly.** The
   pre-registration commits "seven at h=1; **six at h=5, 22 and 66**". Under refusal the operator
   runs `phase2-shootout` at h=1 and `phase2-shootout-multistep` above, which is what produced the
   414 lines. **Nothing the registered read compares changes.**

### What refusal costs, and how the cost is paid down

The cost is inflexibility: the operator cannot cover four horizons in one invocation. Three
mechanisms pay it down, and all three are **data derived from the declarations**, never a second
copy of them.

```python
# src/volab/models/base.py -- added to Cohort.

    def refusal(self, horizon: int) -> str | None:
        """Why this cohort cannot be evaluated at ``horizon``, or ``None`` when it can.

        One function, so the CLI's guard and ``evaluate``'s cannot word the same refusal
        differently or disagree about when it fires.
        """

    def horizons(self, configured: Sequence[int]) -> tuple[int, ...]:
        """Those of ``configured`` this cohort can be evaluated at. The caller supplies the
        configured set, so this module stays free of a dependency on the horizon configuration."""

    @property
    def requires(self) -> tuple[str, ...]:
        """Every feature column some member declares it needs, de-duplicated, order stable."""
```

- **`Cohort.horizons` is derived**, so `SHOOTOUT.horizons(HORIZONS) == (1,)` falls out of EGARCH's
  declaration and nobody types it. There is no cohort-level horizon list to go stale.
- **`MULTISTEP`'s membership is derived too**, replacing the name filter in `cohorts.py`:

  ```python
  # was: tuple(m for m in SHOOTOUT.models if m.name != EGARCH11_SKEWT.name)
  MULTISTEP = Cohort(
      "phase2-shootout-multistep",
      tuple(m for m in SHOOTOUT.models if all(m.domain.answers(h) for h in HORIZONS if h >= 2)),
  )
  ```

  Today this yields the same six contestants — `test_the_multistep_cohort_is_the_shootout_without_
  egarch_and_is_runnable_by_name` must pass **unchanged**, and that is the witness that the
  registered set is unmoved. Tomorrow a second horizon-limited contestant stays out of it
  automatically instead of by someone remembering to extend a name filter.
- **`--horizon` defaults to the cohort's own set**, so the trap's most likely form — the bare
  `python -m volab.compare run`, which today runs the seven-contestant cohort at all four
  horizons — becomes a correct h=1 run rather than an error. An **explicit** out-of-domain
  `--horizon` is refused, because an operator who named a horizon meant it.
- **The refusal is actionable**: it names a registered cohort that does answer the horizon, via a
  small `cohorts.answering(horizon) -> tuple[str, ...]`. The operator never has to remember the
  pairing; they are told it at the moment they get it wrong.

### Where the two guards live, and why not in one place

**`evaluate` raises `ValueError`** when `cohort.refusal(horizon)` is not `None`, immediately after
the existing reserved-name guard. Same class of invariant, same function, same exception type — and
`ValueError` rather than `SchemaError` for the reason `Forecaster.forecast` already records: a
`SchemaError` would be filed under "the window was unfittable" and a wiring bug would pass as a data
problem.

**The CLI checks before any work** and returns 1, printing `cohort.refusal(horizon)` plus the
alternative. Placed in `main()` right after the `--to` past-`dev_end` guard, which is the model for
it (R62: refused loudly rather than clamped). It is a guard, not a second rule: a test asserts the
printed text **is** `cohort.refusal(h)`, so they cannot drift.

**One existing test breaks, and it must not be weakened.**
`tests/test_garch.py:673::test_egarch11_conforms_at_a_horizon_where_the_seed_matters` runs the full
conformance suite on EGARCH at `make_toy(22)`, and `conformance_suite` reaches `evaluate` through
`run_cohort`. It exists *because* the seed matters above h=1 — at h=1 every path is identical, so
moving it to h=1 would delete a real witness about seeding and determinism. The resolution:

```python
# tests/model_conformance.py -- a named adapter, used explicitly at that one call site.
@dataclass(frozen=True)
class OutsideItsDomain:
    """A contestant with its horizon bound lifted, so the suite can ask a question a *run* refuses.

    The conformance suite legitimately interrogates a contestant beyond what any run would ask --
    clause 6 already demands a ValueError from a horizon the fit was not made for, and ``_few()``
    builds specs at path counts nothing ships. This wrapper makes that intent visible by name
    instead of disarming the guard. ``name`` and ``fit`` delegate, so the forecasts are the real
    contestant's.
    """
```

**Not auto-applied inside `alone()`**, which would silently disarm the domain guard for every
conformance run. It is used at exactly one call site, and the red case W4 asserts that the same
suite **without** the adapter raises the domain `ValueError` — which proves the guard reaches the
test path as well as the CLI.

### The alternative I am rejecting, priced

**Drop and relabel** — drop the member, then label the line with the registered cohort whose
membership equals the reduced set, refusing when no such name exists — is the only form of drop that
survives point 4. It is genuinely good: convenient, ledger-honest, reproducible, no schema change.
It is rejected because it buys nothing refusal does not. It still requires the reduced cohort to be
registered, so the operator's registry burden is identical; it adds a membership-to-name resolution
that needs its own uniqueness invariant; and it hands the operator a run under a cohort name they
did not ask for. If the user wants the convenience later, it is a strictly additive follow-on on top
of this design, because refusal is what guarantees the registry is complete. Recorded, not built.

## 3. Form 2 — a contestant that can answer, but only over a shorter history

**Decision: the contestant declares the feature columns it needs; the harness measures the coverage
of those columns from data it already holds and refuses the configuration when the window starts
before the contestant could answer. No declared date, and no threshold.**

The refusal rule, stated exactly:

> A configuration is refused when, at the **first requested origin**, a column in
> `cohort.requires` has fewer than `minimum_training` sessions that are both non-null **and have a
> completed target** — the predicate `evaluate` itself counts on. The message names the column, the
> first origin that does qualify, and how many of the requested origins precede it.

**Keyed to the `minimum_training`-th session, not the first.** This is the part the obvious design
gets wrong, and the measurements show it: a rule keyed to the first non-null session catches 69 of
the 119 lost origins, and `--from 2022-05-13` would then **pass** while the contestant `ModelSkip`s
on thin training until 2023-05-11 and `comparable()` quietly intersects a further 50 origins out of
everyone's loss. That is the original trap at 20% of the window. Keyed to the 250th session the
refusal reports 119 and the hole is closed.

**Why refuse rather than declare a date.** A `first_origin: dt.date` on the `Domain` is the obvious
shape and it is wrong for a reason this project has already paid for: it is a hard-coded fact about
ingested coverage sitting in a model module, which is `MONTE_CARLO_ERROR_PERCENT` again — correct the
day it is written, stale the day the vendor backfills or the store is rebuilt. Keeping it honest
would need a test that reads the committed store. **That is possible** — I
checked, having first written the opposite: `tests/test_recalibrated.py:605::real_frames` reads the
real curated panel through `wf.load_for`, from inside a test body so the autouse holdout fixture
polices it, at about 3 s per horizon. So "it could not be checked" is **not** the disqualifier.

The disqualifier is that a declared date is a **second copy of a fact the harness can measure from a
frame it already holds**, and this project has a ruling about second copies: R119's table was
correct when written and stale the moment the knowledge moved. Declaring the **columns** and
measuring the coverage per run means there is no date to go stale, no new real-data test to keep in
step with the store, and the coverage is computed from the same frame the forecasts come from.

**Why refuse rather than only report.** Reporting is already half-built and would not have closed
the trap: `Trial.dropped` carries `len(attempted - usable)` and `Trial.refused` carries the
contestant's own `ModelSkip` count, so a VIX1D contestant today would write `dropped=119` on every
line. That is visible and nobody reads 119 as *"adding this contestant halved the comparison"*.
Reporting is kept — it is how the cost is seen once the run is legitimate — but it is not the
action.

**Why not simply put such a contestant in its own cohort.** That **is** the answer, and refusal is
what makes it the only legal one. The harness cannot choose a cohort for the operator; it can
refuse the illegitimate comparison and say what the legitimate ones are. The operator's two
remedies are named in the refusal: move `--from`, or run the contestant in a cohort of its own.

**Interior gaps are a different thing and are not refused.** Measured: the IV family has **zero**
interior nulls, the FRED columns have 17-19, of which **3 of 247** land on shootout origins. A
zero-tolerance rule would make a FRED-dependent contestant unrunnable over any window because of a
bond holiday, so interior gaps stay on the per-origin path, generalising a check `evaluate` already
performs:

```python
# src/volab/walkforward/__init__.py, inside evaluate's origin loop -- generalised.
# was: null_regressors = [name for name in HAR_REGRESSORS if pd.isna(row[name])]
required = (*HAR_REGRESSORS, *cohort.requires)
missing = [name for name in required if pd.isna(row[name])]
```

and, per the same argument, the training-sufficiency count at `walkforward/__init__.py:954` extends
from `[*HAR_REGRESSORS, column]` to include `*cohort.requires`, so the sufficiency boundary becomes a
**recorded skip for everyone with a reason** instead of a silent intersection. Both are
backward-compatible today because `cohort.requires` is empty for every named cohort.

**`HAR_REGRESSORS` stays an unconditional floor** rather than becoming the HARs' declared
`requires`. It is the view's own contract — `TrainingView`'s usable-row count is *defined* on those
columns at line 954 — and making the check derived would stop a GARCH-only or spy-only cohort
skipping null-regressor origins, changing origin counts for a dozen existing tests to buy nothing.
The asymmetry is deliberate and stated here so nobody tidies it away.

### Where the coverage guard lives

In `run_configuration`, not in `evaluate`, and the seam is a new pure function beside it:

```python
# src/volab/walkforward/__init__.py -- added. Pure pandas; the sealed path stays pure.
def coverage_refusal(
    stored: pd.DataFrame,
    targets: pd.DataFrame,
    required: Sequence[str],
    origins: Sequence[dt.date],
    *,
    horizon: int,
    minimum_training: int,
) -> str | None:
    """Why ``required`` cannot support a run over ``origins``, or ``None`` when it can.

    ``stored`` is the persisted feature table ``run_configuration`` already holds for the
    truncation comparison and ``targets`` is the frame it already passes to ``evaluate``, so this
    opens no read path and takes no ``end`` of its own.

    ``targets`` and ``horizon`` are **required, not conveniences**: the qualifying origin depends on
    the target lag, and a signature without them can only compute the wrong boundary. The row
    predicate here must be the same one ``evaluate`` counts -- non-null in ``required`` and
    ``target_as_of <= origin`` -- so the two mechanisms cannot disagree about which origin is the
    first runnable one. W9 asserts that agreement.
    """
```

Three reasons for that placement. `run_configuration` is the **single driver loop** both CLIs call
(R97), so every path to a ledger line is covered. It already receives `persisted()`, the feature
table loaded to `dev_end`, so the coverage comes from a frame in hand and **no new read path or
`end` parameter is introduced** — `load_panel` stays the only read path. And `evaluate` keeps its
per-origin skip as the fallback, so a caller reaching `evaluate` directly degrades to "69 origins
skipped, each with a reason" rather than to silence.

**The check runs before `evaluate`, not after**, or the fits it exists to prevent have already
run: it sits immediately after `scoreable_origins` returns and before the `RunAudit` is built.

**`persisted()` may be `None`**, and this project's rule is that nothing to check is not a pass. The
driver reports `coverage: NOT CHECKED, the store holds no built table` in the same voice as the
existing `truncation equivalence: NOT COMPARED` line, and falls through to the per-origin skips.

**`ConfigurationOutcome.refused` is reused** rather than extended with a second flag. Its two causes
— an audit violation (R63) and a coverage refusal — have identical consequences: nothing appended,
caller exits non-zero. Its docstring widens to say so.

**One thing to be explicit about**: `coverage_refusal` reads the whole persisted table, not a
truncation of it, so the decision depends on sessions after the first origin. That is a
configuration-level decision and not a forecast — the exact standing of `scoreable_origins`, which
already reads the whole target frame to choose origins. No value from it reaches a fit, and
`RunAudit` continues to police the per-origin builds. Said here because an unexplained full-panel
read on this path is the kind of thing a later reviewer must be able to find a ruling for.

## 4. Blast radius, file by file

### What changes

| file | change |
|---|---|
| `src/volab/models/base.py` | add `Measurement`, `Sampling`, `Domain`, `UNRESTRICTED`; add `domain` to `Model`; add `Cohort.refusal`, `Cohort.horizons`, `Cohort.requires` |
| `src/volab/walkforward/__init__.py` | `evaluate` raises on `cohort.refusal(horizon)`; the origin loop's null check and the sufficiency count extend by `cohort.requires`; add `coverage_refusal`; `run_configuration` calls it and reports; three baseline model classes declare `domain`; `ConfigurationOutcome.refused` docstring widens |
| `src/volab/models/garch.py` | `GarchModel.domain` property, derived from `volatility` and `method` |
| `src/volab/models/recalibrated.py` | `RecalibratedHar.domain` -> `UNRESTRICTED` |
| `src/volab/compare/cohorts.py` | `MULTISTEP` membership derived from declarations; `MONTE_CARLO_ERROR_PERCENT` and `MONTE_CARLO_ERROR_SOURCE` deleted; `monte_carlo_error` rewritten; add `answering(horizon)`; drop the `GarchModel`/`SIMULATIONS` imports; the long docstring loses the table's three paragraphs and gains the derivation |
| `src/volab/compare/__main__.py` | `--horizon` defaults to `cohort.horizons(HORIZONS)`; refuse an explicit out-of-domain horizon and return 1; print the derivation; adapt to `monte_carlo_error`'s return type |

### What does not change, and this is the design's main claim

- **`Evaluation.contestants` — unchanged.** It already holds the actual set, and under refusal the
  actual set is always the cohort's full membership.
- **`Evaluation.trials()`, `Trial`, `data/trials.jsonl` — unchanged.** Nothing is dropped, so there
  is nothing new to report. No schema change, and the registered line count of 414 is untouched.
- **`Comparison`, `src/volab/compare/sink.py`, `data/comparisons.jsonl` — unchanged.** Note for the
  record: `Comparison.record()` **already** writes `"contestants"`, so the comparison record already
  self-describes its contestant set. `monte_carlo_error_source` stays a single string per record,
  and `Sampling.__post_init__` refuses the one case that would need it to become a mapping.
- **`src/volab/leaderboard/` — unchanged.** It reads the two ledgers and computes nothing; neither
  ledger's shape moves, so it cannot disagree with anything.
- **`src/volab/panel.py`, `src/volab/store.py`, `src/volab/features/` — unchanged.** No new read
  path, no new `end`, nothing added to `ALLOWED`.
- **`tests/test_import_discipline.py` — unchanged, and it must stay unchanged.** `models/base.py` is
  in `SEALED` and everything added to it is `dataclasses`, `typing` and `datetime`, all already
  imported there. The transitive walk (R127/R129) still finds no numeric edge: `base.py` imports no
  first-party module at all, and the direction of every new import is model -> base, never
  base -> garch. **If an implementer finds themselves adding anything to `SEALED`, `ALLOWED` or
  `NUMERIC_ROOTS`, the design has been misread.**
- **`docs/holdout-preregistration.md` — unchanged and unchangeable.** The registered sets, seven at
  h=1 and six above, come out of the derivation identically.
- **`tools/` — unchanged.** Checked, because a probe that broke would strand a cited figure (R129).
  `tools/measure_calibration.py:89` calls `wf.evaluate` with **no** `cohort`, so it gets
  `BASELINE_COHORT`, which is unrestricted and never refused.
  `tools/measure_garch_forecast_error.py` drives EGARCH at every horizon but goes through
  `_Fitted._simulated` directly, never through `evaluate`, and its
  `dataclasses.replace(spec, simulations=paths)` keeps working because `domain` is derived rather
  than stored.
- **`docs/superpowers/specs/2026-10-27-holdout-reveal-design.md` — unchanged.** Checked for seams:
  it names none of `monte_carlo_error`, `MONTE_CARLO_ERROR_SOURCE`, the cohort names, or a
  `compare` invocation relying on the `--horizon` default. Deleting one symbol and changing
  another's return type therefore reaches no registered artifact.

### Tests that must change, and what each change may not weaken

| test | change |
|---|---|
| ~18 spy classes in `tests/{model_conformance,test_model_interface,test_recalibrated,test_compare}.py` | one line, `domain = UNRESTRICTED`. `_Scaled` in `test_compare.py:1586` delegates `domain` as it delegates `name` |
| `tests/test_garch.py:673::test_egarch11_conforms_at_a_horizon_where_the_seed_matters` | wrap in `OutsideItsDomain`. **The horizon stays 22** — moving it to h=1 would delete the seeding witness |
| `tests/test_compare.py:1430-1462` (the `monte_carlo_error` tests) | follow the new return type; the `fewer_paths` case must keep reading `None` |
| `tests/test_compare.py:1464::test_every_monte_carlo_figure_is_reachable` | **deleted with its reason recorded**, see section 5 |
| `tests/test_compare.py:45-46` | the imports of `MONTE_CARLO_ERROR_PERCENT` and `MONTE_CARLO_ERROR_SOURCE` go; both symbols are deleted |
| `tests/test_compare.py:1063`, `:1121`, `:1831`, `:1870` | four more readers of `MONTE_CARLO_ERROR_SOURCE`, including `test_every_measured_cohort_figure_is_withheld_while_it_comes_from_the_probe_series` (1109) and `test_every_printed_monte_carlo_error_names_the_series_it_was_measured_on` (1852). Each keeps its property and takes the source from the cohort instead of the constant; `:1870`'s `"not SPY" in …` assertion is the one that must be **restated**, not ported |
| `tests/test_compare.py:1478` | asserts every table key is a simulated contestant; goes with the table |

`tests/test_leaderboard.py:54` defines the probe string as its **own local literal** rather than
importing it, so the leaderboard's tests are unaffected by the deletion — they check that a stored
record is rendered faithfully, whatever the source string says. That is the import discipline of
R126 paying off, and it is the reason section 4 can claim the leaderboard is untouched.

Tests that must pass **unchanged**, because they are the witnesses that nothing recorded moved:
`test_the_baseline_cohort_reproduces_the_committed_goldens`,
`test_the_shootout_cohort_is_the_seven_contestants_and_begins_with_har_levels`,
`test_the_multistep_cohort_is_the_shootout_without_egarch_and_is_runnable_by_name`,
`test_every_named_cohort_begins_with_har_levels`,
`test_a_cohort_that_is_not_the_baseline_cannot_carry_the_baseline_name`,
`test_the_method_and_the_variance_process_must_agree_at_construction` (`test_garch.py:303-317`,
which is why `domain` is derived), and every test in `tests/test_leaderboard.py`.

## 5. The Monte-Carlo table

**Decision: yes, the declaration subsumes it. The table is deleted, and EGARCH's two figures are
deleted with it rather than migrated.**

The table holds two things. *Which contestants simulate* is subsumed outright: `monte_carlo_error`
tests `isinstance(model, GarchModel) and model.method == "simulation"`, and under the declaration it
tests `model.domain.sampling is not None` — which drops the `isinstance`, drops `cohorts.py`'s
imports of `GarchModel` and `SIMULATIONS`, and lets a non-GARCH simulated contestant be found
without editing anything.

*The figures* are a different matter, and the answer is deletion. `("egarch11_skewt", 22): 0.163`
and `(…, 66): 3.273` are already described in `cohorts.py` as *"unreachable in the registered
pairing but reachable by construction"*. **The horizon guard makes them unreachable by
construction**: no run can ask EGARCH for h>=2 any more. R125 removed GJR's two rows on exactly that
ground — *"no reader for an SPY figure … deleted rather than left as live-looking data nothing could
read"* — and the same rule now reaches EGARCH's. So:

```python
# src/volab/compare/cohorts.py
@dataclass(frozen=True)
class MonteCarlo:
    """The measured error of every simulated contestant in a cohort at one horizon."""

    percent: dict[str, float | None]
    # The one series every figure was measured on; ``None`` when no member simulates.
    source: str | None


def monte_carlo_error(cohort: Cohort, horizon: int) -> MonteCarlo:
    """Built from each contestant's own declaration. No table, and no key that can name a
    contestant that does not exist."""
```

`Measurement` **ships with zero instances**, and that is deliberate rather than an oversight: the
type exists so the next measured figure has a place to live that cannot outlive its contestant, and
`Sampling(paths=self.simulations)` with no measurements is what EGARCH declares. `monte_carlo_error` is also where the cross-contestant source rule lives, because it is the first
place that sees a whole cohort's figures: two simulated members whose measurements name different
series would need the comparison record to carry a source *per contestant*, which is a schema change
to `data/comparisons.jsonl`, so it raises instead (W15). `MonteCarlo.source` is otherwise the
sentinel `"no measurement exists"` — never equal to `series_source(series)`, so the
figure is withheld — and `analyse`'s existing rule puts `egarch11_skewt` in `unclaimed` with the
reason *"not measured, so no precision is established"*. **One display string changes and nothing else does.** At h=1, the only horizon EGARCH now prints
at, today's line reads `[MC error not measured (synthetic probe series, not SPY)]` and the new one
reads `[MC error not measured (no measurement exists)]`; new comparison records store the new string
in `monte_carlo_error_source`. There is **no schema change**, `egarch11_skewt` stays in `unclaimed`
for the same reason with the same wording, and the registered read compares and claims exactly what
it did. Earlier records keep their own string, as R126 ruled for the 28 older ones. Calling this
byte-identical would be an overclaim; it is a source label that stops naming a measurement that no
longer exists.

`test_every_monte_carlo_figure_is_reachable` is **deleted, not weakened**, and the reason is the
strongest available: it exists to catch a key naming a contestant that is no longer in a cohort, and
a figure attached to the instance that measured it **cannot** outlive that instance. The staleness
class is eliminated rather than tested for. This follows R124's precedent for deleting the AST
literal-count test — a test whose property has become structural is a citation to chase, not a
ratchet. Replacement witnesses are W11 and W12.

**Explicitly out of scope, and a one-line change away.** EGARCH's h=1 Monte-Carlo error is
*identically zero* — every one of the 100,000 paths is the same number, because `sigma2_{t+1}` is
known at `t`. Declaring `Measurement(percent=0.0, source="SPY h1", horizon=1, paths=100_000)` would
lift `egarch11_skewt` out of `unclaimed` and hand a significance claim to **the contestant leading
h=1 QLIKE at -7.38769**. R124 refused exactly this, by name, as post-hoc selection: *"there is no
defect to fix, only a claim to upgrade, for a contestant we have already watched win."* It would
also change what the registered read claims, which puts it out of scope twice over. W13 is the
witness that it has not been done.

## 6. The witnesses

### The trap is closed — Form 1

- **W1, the red case, and the one that matters most.**
  `test_the_compare_cli_refuses_the_shootout_above_h1_and_writes_nothing`. Invoke
  `main(["run", "--cohort", …, "--horizon", "22", *WINDOW], sink=sink)`. Assert it returns 1, that
  the printed text names the contestant and its bound, and that **the sink received no trial and no
  comparison**. On the real cohort that invocation today produces 7 contestants x 3 symbols x 2
  losses = **42** ledger lines of ordinary-looking numbers, one of them an estimate of infinity (14
  with `--symbol SPY`, which is the shape of the 14 already in the ledger). The assertion that
  nothing was written is the red case: the trap is not "a wrong number" but "a plausible number", so
  the test has to check that no number was produced at all.

  **Harness note the implementer needs:** `test_compare.py`'s `_Entry` patches the module's
  `COHORTS` to `{TOY_COHORT.name: TOY_COHORT}`, and `TOY_COHORT`'s members are all unrestricted. So
  this test must register its **own** horizon-limited cohort in that patched mapping rather than
  reaching for `phase2-shootout`, which the harness has replaced.
- **W2.** `test_the_refusal_names_a_cohort_that_can_answer_the_horizon` — the message contains
  `phase2-shootout-multistep`. A refusal nobody can act on is a worse trap than the original.
- **W3.** `test_evaluate_refuses_a_cohort_at_a_horizon_a_member_cannot_answer` — `ValueError` from
  `evaluate` itself, so the guard does not live only in the CLI.
- **W4, the red case for the conformance adapter.**
  `test_the_conformance_suite_refuses_egarch_above_h1_without_the_adapter` — the same
  `conformance_suite(EGARCH11_SKEWT, toy=make_toy(22), …)` call raises the domain `ValueError`
  when it is not wrapped. This is what stops `OutsideItsDomain` from quietly becoming the default.
- **W5.** `test_the_cohort_horizons_are_derived_from_the_declarations` —
  `SHOOTOUT.horizons(HORIZONS) == (1,)` and `MULTISTEP.horizons(HORIZONS) == (1, 5, 22, 66)`, by
  value.
- **W6.** `test_the_default_horizons_are_the_cohorts_own` — a bare `run` over the shootout evaluates
  h=1 only, and prints why.
- **W7, the anti-staleness witness for the cohort.**
  `test_a_second_horizon_limited_contestant_stays_out_of_the_multistep_cohort` — build a spy
  declaring `Domain(max_horizon=5)`, put it in a shootout-shaped cohort, and assert the derived
  multistep membership excludes it. Under the old name filter this needed someone to remember.

### The trap is closed — Form 2

The toy fixture already supports both: `tests/test_truncation_equivalence._synthetic` builds SPY's
bars **beside every VIX-family and FRED series** over 2024-01-02 to 2024-07-31, so a late-starting
series is a row filter on the panel and needs no fixture surgery.

A **real-data** witness is also available and is the implementer's option rather than a requirement:
`tests/test_recalibrated.py:605::real_frames` is the established idiom for reading the committed
store from inside a test body, where the autouse holdout fixture polices it, and VIX1D's real
boundary at 2022-05-13 would exercise W8 and W9 on the measured 119 origins rather than on a
fixture. It costs about 3 s per horizon and it couples a test to the store's contents, which is why
the toy version is the one the design requires.

- **W8, the red case.** `test_a_contestant_whose_required_column_starts_mid_window_refuses_the_run`
  — a spy declaring `requires=("iv_h1",)`, a panel with VIX1D's rows dropped before a mid-window
  session, and `run_configuration` refusing with the column, the qualifying session and the count
  of origins that would have been lost. Assert **nothing was appended**.
- **W9, and it is the subtle one.**
  `test_the_coverage_refusal_agrees_with_the_origin_evaluate_stops_skipping`. Two assertions at a
  **long** horizon, where the lag bites hardest. First: with the window starting exactly at the
  column's first non-null session the run is **still** refused — that is the 69-versus-119 hole, and
  without it the rule looks right while leaking 50 origins. Second: the first qualifying origin the
  refusal names **equals** the first origin `evaluate` does not skip for sufficiency, computed by
  running `evaluate` over the earlier window and reading `Evaluation.skipped`. Without the second
  assertion the refusal and the skip can disagree by `h` sessions — which is exactly the error the
  first draft of this design made, and it survived until someone re-measured.
- **W10.** `test_an_interior_gap_in_a_required_column_skips_the_origin_for_everyone_with_a_reason`
  — one nulled mid-window session: the configuration is **not** refused, the origin appears in
  `Evaluation.skipped` with the column named, and every contestant's trial `origins` drops by
  exactly one. The FRED case, measured at 3 of 247 origins.
- **W11.** `test_coverage_is_reported_as_not_checked_when_the_store_holds_no_built_table` — nothing
  to check is not a pass.

### The declaration cannot go stale

- **W12.** `test_the_skewt_distribution_is_what_egarchs_bound_is_derived_from` — assert `fit` passes
  the literal `dist="skewt"`, and that `GarchModel`'s field names are exactly the known set. R114's
  exclusion is a fact about the **error law**; the derivation `volatility == "EGARCH" => h<=1` is
  sound only while the law is fixed, so a future `dist` field has to break a test rather than
  silently invalidate the bound.
- **W13.** `test_a_figure_measured_at_another_path_count_reads_as_not_measured` — a `Sampling` whose
  `Measurement.paths` differs from `paths` in use yields `None`. This keeps the path count beside
  the figure, where today it is a sentence in a comment.
- **W14.** `test_a_simulated_contestant_removed_from_a_cohort_takes_its_figures_with_it` —
  `monte_carlo_error(Cohort("probe", ()), 22).percent == {}`. The structural replacement for the
  deleted reachability test.
- **W15.** `test_two_simulated_contestants_measured_on_different_series_are_refused` — the
  construction-time refusal, so the comparison record's single-source assumption fails loudly at
  the moment it stops holding rather than mislabelling a figure.
- **W16.** `test_egarch_is_unclaimed_at_h1_and_its_error_is_not_declared_zero` — the guard on the
  out-of-scope item in section 5. It asserts `egarch11_skewt` is in `unclaimed` at h=1 and that no
  `Measurement` claims a zero error for it.
- **New conformance clause 9**, `clause_9_the_declared_domain_is_exercised`: for every configured
  horizon the contestant declares it **can** answer, the toy must yield at least one answered
  origin. This catches a declaration that is vacuously wide — "unbounded" on a contestant that in
  fact breaks above h=5 — which is the failure mode a bound cannot have.
- **New conformance clause 10**, `clause_10_a_declared_requirement_is_real`: for each column in
  `domain.requires`, null that column in the view and assert the contestant raises `ModelSkip`
  rather than answering. **It passes vacuously today**, because no shipped contestant declares a
  requirement — exactly as R111 shipped the mandatory rebuild factory passing vacuously on purpose —
  so it needs a plant to prove it bites:
  `test_a_contestant_that_ignores_its_declared_requirement_is_caught`.

**The honest limits of all of this, stated rather than discovered later.**

1. **`max_horizon` cannot be checked against behaviour, and no test can close that.** EGARCH at
   h=22 returns `1.70e+12`: finite, ordinary-looking, and wrong for a reason no observation of the
   output reveals. That is *why* R114 required an a-priori contestant-level refusal. What is
   enforced instead is that the declaration **cannot be omitted** (a required protocol member) and
   **cannot be expressed wrongly** (derived from `volatility`, with W12 guarding the premise). That
   is the ceiling for an a-priori mathematical exclusion, and it is a weaker guarantee than the
   Monte-Carlo table's, whose staleness class is eliminated outright.
2. **`requires` is checkable in one direction only.** Clause 10 catches *declared but unused*. A
   column *used but undeclared* is not detectable — a contestant reading `iv_h1` without declaring
   it still gets NaN before 2022-05-13 and still drags the origin out of everyone's comparable set.
   That residual is **exactly today's behaviour**, so the design is strictly better and nowhere
   worse; it is recorded because "the harness enforces what a contestant needs" would otherwise
   over-claim.
3. **I did not make the contestant enforce its own horizon bound** by raising `ModelSkip` above
   `max_horizon`. It would make clause 9 behavioural, and it breaks five tests in
   `tests/test_garch.py` that forecast EGARCH at h=5 and h=22 directly through `_forecaster(...)` —
   including the seeding and determinism witnesses, which have no h=1 form because at h=1 every
   path is identical. The witnesses are worth more than the redundancy.

## 7. Left to the builder

- The exact wording of every refusal, beyond the facts each must contain (the cohort, the horizon or
  column, the contestant, the measured counts, and the alternative to run).
- `Cohort.requires`' de-duplication mechanics, and the bodies of `Domain.answers`,
  `Sampling.error`, `Cohort.refusal` and `coverage_refusal` — the signatures above are the contract.
- Whether `answering(horizon)` returns names or `Cohort` objects.
- Where in `main()` the refusal prints relative to the storage-root and `dev_end` lines, given it
  must precede `git_state` and every load.
- The `OutsideItsDomain` adapter's field layout.

## 8. Sequencing

Two stories, **serialized** — they both touch `models/base.py` and `compare/cohorts.py`, so they
cannot be parallel implementers:

- **US-3.9a** — `Domain` on `Model`, the horizon refusal, the derived cohort horizons and multistep
  membership, the CLI default and guard, Form 2's `requires` plus `coverage_refusal`, conformance
  clauses 9 and 10, W1-W12 and W16.
- **US-3.9b** — retire the Monte-Carlo table onto the declaration: `MonteCarlo`, the deletions,
  W13-W15. Depends on 3.9a.

## 9. Two things only the user can settle

1. **The board's acceptance check for US-3.9 says the harness "drops a contestant at a horizon it
   cannot answer". This design refuses instead**, for the reasons in section 2 — chiefly that the
   ledger already holds 14 `phase2-shootout` lines at h=22 meaning seven contestants, so a drop
   would put two contestant sets under one name at one horizon. The board needs amending to match.
   Flagged rather than silently deviated from, and the amendment is the user's to approve, not an
   implementer's to assume.
2. **`python -m volab.compare run` changes meaning.** Today the bare command runs the
   seven-contestant cohort at all four horizons, which is the trap. Under this design `--horizon`
   defaults to the cohort's own derived set, so the bare command runs **h=1 only** and prints why.
   That is a deliberate CLI default change: it makes the common invocation correct instead of
   refusing it, at the cost of a default that is narrower than it looks. The alternative is to leave
   the default at all four horizons and let the bare command be refused, which is louder and more
   annoying. **Recommended as designed; the user's call**, because it changes what a documented
   command does.
