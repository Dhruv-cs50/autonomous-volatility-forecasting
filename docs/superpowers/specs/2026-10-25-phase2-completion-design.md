# Phase 2 completion — the model zoo and the comparison layer

Status: design, 2026-10-25. Supersedes nothing; builds on `2026-10-20-phase1-data-and-targets-design.md`
and on the harness recorded in `docs/superpowers/phase2-decisions.md`.

Framework row: *"GARCH(1,1), GJR/EGARCH with skew-t, HAR, recalibrated HAR; walk-forward;
QLIKE/MSE; DM plus MCS"*. Component 6 of the same document fixes the interface at line 63:

> | 6 | Model zoo | Fit each family against one interface: `fit(train) -> forecast(h)` | Features,
> config | Forecast series per model, horizon and fold | `arch` (GARCH, GJR, EGARCH, skew-t) … |

This is US-3.1 on `docs/phase2-completion-sprint-board.md`: the design that has to land before
US-3.2 and US-3.3 can be built in parallel. It writes no implementation code. Rulings are
proposed here as **R85** onwards for the coordinator to fold into
`docs/superpowers/phase2-completion-decisions.md`; R82-R84 are not edited here.

Every mechanical claim below names an executable witness, per **R32**. Where a witness does not
exist yet, the test's name is given and the story that owns it is named with it. Every number
quoted from `arch` was measured during this design; section 11 names the probe that reproduces
each one, because three of them contradicted the design's first draft.


## 1. The three decisions, in one paragraph each

1. **The interface** is `Model.fit(view) -> Forecaster`, `Forecaster.forecast(h) -> Forecast`,
   over a frozen `TrainingView` that carries *everything knowable at the origin* — the training
   feature rows with each row's target `as_of`, the origin's own row, the response column name,
   **and the daily log-return series ending at the origin**. GARCH needs the last field; the
   three existing models ignore it. The existing three keep their current arithmetic untouched
   and are reached through thin adapters, and the recorded figures are protected by freezing
   `MODELS` and making the **contestant cohort** a named parameter rather than a module global
   (section 3). That is where the numbers would have moved, and it is not where it looks.

2. **The h-step aggregation** is the sum of the per-step conditional variances of the
   **×100** return series over steps 1…h, divided by `1e4` once. It is derived from
   `features/targets.py`'s definition, quoted in section 4, which is an *uncentred* sum of
   squared daily returns — so the mean specification is `ZeroMean`, not `ConstantMean`, and
   `.variance` is then the right quantity rather than a quantity needing `+ h·μ̂²`.

3. **DM, MCS and SPA** use a rectangular HAC estimator truncated at exactly `q = ⌊(h−1)/s⌋` with
   the Harvey correction in **sampling units** (`h_eff = q + 1`, never the daily `h`), a
   Bartlett–Newey–West column beside it for robustness, and a stationary-bootstrap block length
   of `max(q+1, ⌈Politis–White on the loss levels⌉)`. The effective sample size `n_eff = n/(q+1)`
   is 17 at h=66 and is **stride-invariant for h ≥ s**, so MCS membership at 10% is refused there
   and reported at 25% only (section 5).


## 2. Scope

### In scope

- `src/volab/models/` — the interface (US-3.1b), the GARCH family (US-3.2), the recalibrated
  HAR (US-3.3). numpy, scipy and `arch` are admitted **here** and in `compare/`, per R82.
- `src/volab/compare/` — Diebold-Mariano with Harvey, MCS and SPA through
  `arch.bootstrap`, a `Sink` boundary, and the shootout entry point (US-3.4).
- `src/volab/leaderboard/` — the reporting command, which reads and computes nothing (US-3.5).
- One edit to `src/volab/walkforward/__init__.py`: the seam. It is the only change in this
  phase that can move a recorded number, so it is its own story and its own golden test.
- `tools/measure_garch_forecast_error.py` — the probe that prints section 4's three measured
  figures, because two of them are at the limit of what this project calls meaningful and a
  figure without a command is an unverified claim (R32, R79).

### Out of scope

- Anything in `src/volab/features/`, `panel.py`, `store.py`, or `solve`/`fit_ols`. R82 narrows
  R4/R57 but does not lift them: **no design decision here requires touching those files**, and
  the one place it was tempting (moving `fit_ols` into the model zoo) is rejected in section 3.5.
- Relaxing `pyarrow`. R84 is resolved: the ledger is the sink, MLflow is refused for a stated
  reason, and section 6 keeps a later MLflow sink reachable without a rewrite.
- `allow_holdout`, `VOLAB_HOLDOUT_UNLOCK`, `@pytest.mark.holdout`, and any new
  `filterwarnings` entry. Nothing below needs one; three things below depend on a warning
  **still being an error** (R89, R91, and R86's red cases).
- Intraday RV, options, LightGBM, TTM, MS-HAR. The framework's later rows.

### Exit criteria for the sprint

1. `uv run --locked python -m volab.compare run` produces, per symbol and horizon, a complete
   loss series for every contestant, appends one trial per (contestant, loss) to
   `data/trials.jsonl` and one comparison record to `data/comparisons.jsonl`, under one `at`
   and one git sha.
2. `uv run --locked python -m volab.leaderboard` prints the per-horizon leaderboard from those
   two files and computes nothing of its own.
3. `uv run --locked python tools/measure_calibration.py --symbol SPY --horizon 1 --every 5`
   still prints `mse 4.295799e-07` for `har_semilog corrected`. This is the regression gate on
   the whole phase, not a nice-to-have.
4. `uv run --locked pytest`, `ruff check .`, `ruff format --check .` and `mypy` all clean, with
   `filterwarnings = ["error"]` untouched and `ALLOWED == {"store.py"}` untouched.


## 3. Decision 1 — the model-zoo interface

### 3.1 What the existing three signatures actually are

```python
def _har_levels_forecast(training: pd.DataFrame, row: pd.Series, response: str) -> tuple[float, int]
def _har_semilog_forecast(training: pd.DataFrame, row: pd.Series, response: str) -> tuple[float, int]
def _random_walk_forecast(row: pd.Series, horizon: int) -> float
```

Three different shapes already, called from a hard-coded block inside `evaluate`. GARCH needs a
fourth input that none of them mention: the subject's daily return series up to the origin.
Widening the tuple would make four shapes.

### 3.2 R85 — the interface

Proposed ruling:

> **R85 — one interface, `fit(view) -> forecast(h)`, over a view of everything knowable at the
> origin.** A model is handed a frozen `TrainingView` rather than a selection of arguments,
> because the set of things a contestant legitimately knows at an origin is a property of the
> *origin*, not of the contestant. GARCH reads `returns`; the HARs read `training` and `row`;
> the random walk reads `row` and `horizon`; the recalibrated HAR reads `training`,
> `target_as_of` and `stride`. Nobody is special-cased and nothing is passed that was not
> knowable at the origin.

`src/volab/models/base.py` — pure stdlib and pandas, **no numpy import**, so
`volab.walkforward` can import it without admitting numpy to the solver's module:

```python
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass(frozen=True)
class TrainingView:
    """Everything knowable at one forecast origin, for one symbol and horizon.

    Built once per origin by ``volab.walkforward.evaluate`` and handed to every contestant, so
    no contestant can be handed more than another.
    """

    symbol: str
    origin: dt.date
    horizon: int
    # The response column name, ``features.targets.target_column(horizon)``.
    response: str
    # One row per base session up to the origin whose target had completed by the origin,
    # carrying HAR_REGRESSORS, ``response``, and ``target_as_of`` (section 3.6).
    training: pd.DataFrame
    # The origin's own feature row. Its target is NOT known and is not present.
    row: pd.Series
    # Daily log returns indexed by ``datetime.date``, one entry per session, ending at the
    # origin. The identical series ``features.targets.build_targets`` squares (section 4.2).
    returns: pd.Series
    # The harness's refusal threshold, so a model can apply its own minimum consistently.
    minimum_training: int
    # The origin stride the run is using. Only the recalibrated HAR reads it.
    stride: int


@dataclass(frozen=True)
class Forecast:
    """One contestant's answer at one origin, on the variance scale the losses use."""

    value: float
    # Rows or returns the fit consumed. Reported so a thin fit is visible rather than inferred.
    observations: int


@runtime_checkable
class Forecaster(Protocol):
    observations: int

    def forecast(self, horizon: int) -> Forecast: ...


@runtime_checkable
class Model(Protocol):
    name: str
    # False only for a model whose fit reads no part of the target side (GARCH). Section 3.7
    # turns this into a test rather than a comment.
    fit_depends_on_horizon: bool

    def fit(self, view: TrainingView) -> Forecaster: ...


@dataclass(frozen=True)
class Cohort:
    """A named set of contestants. The name is what reaches the ledger."""

    name: str
    models: tuple[Model, ...]

    @property
    def contestants(self) -> tuple[str, ...]:
        return tuple(model.name for model in self.models)
```

`Forecaster.forecast(horizon)` raises **`ValueError`** when `horizon != view.horizon` and the
model's fit was horizon-dependent. `ValueError` deliberately, not `SchemaError`: `evaluate`
catches `SchemaError` and records the origin as *"a fit refused the window"*, so a wiring bug
would be filed under a data problem. The two-stage shape is the framework's, and it licenses the
obvious later optimisation — fit once per origin, forecast all four horizons — without the
interface changing when someone takes it.

*Cost if wrong:* a view with eight fields is wider than any single model needs, so a model can
read a field it has no business reading. The mitigation is that **every field is knowable at the
origin by construction**: `training` is `knowable_targets`' own output, `returns` and `row` come
from the panel physically truncated at the origin, and `RunAudit` records all three (section 3.6).
Width inside the knowable set costs nothing; the alternative — a narrower view per model family —
is the special-casing R85 exists to refuse.

### 3.3 R86 — how the recorded numbers are kept, and where they would have moved

The refactor hazard is **not** in the forecast functions. It is here:

```python
    def comparable(self, loss: str) -> set[dt.date]:
        sets = [
            {origin for origin, _, forecast in self.scored(model) if defined(forecast)}
            for model in MODELS            # <- the module global
        ]
        return set.intersection(*sets) if sets else set()
```

`Evaluation.comparable` and `Evaluation.trials` intersect over the module-level `MODELS`, and
`tools/measure_calibration.py` iterates `wf.MODELS` as well. Append `garch11_skewt` to that
tuple and **one** non-converged origin shrinks the intersection for every model on both losses:
251 origins becomes 250, and `har_semilog` h=1 MSE moves off `4.295799e-07`. The forecast
functions would be byte-identical and the headline figure would still have changed.

Proposed ruling:

> **R86 — `MODELS` is frozen at the three baseline names, and the contestant cohort is a named
> parameter of the run and a field on the trial.** `Evaluation` carries
> `contestants: tuple[str, ...] = MODELS` and `comparable`/`trials` read **that**, never the
> global. `evaluate` takes `cohort: Cohort = BASELINE_COHORT` as a keyword with a default, so
> every existing caller — the walk-forward CLI, `tools/measure_calibration.py`, all 32
> walk-forward tests — is unchanged and reproduces its recorded figures exactly. `Trial` carries
> `cohort: str`, taken from `Cohort.name`, because a HAR trial scored over a seven-contestant
> intersection is a different experiment from one scored over three, by exactly the argument R75
> made about the stride. **The 72 existing ledger lines carry no `cohort` key, and its absence
> means `"phase2-baseline"`** — stated here so a reader of the ledger does not have to guess.
>
> **The baseline name is reserved.** `evaluate` raises if `cohort.name == "phase2-baseline"`
> while `cohort.contestants != MODELS`. A cohort name is a claim about which experiment produced
> a line, and the one thing that must not happen is 168 shootout lines labelled as the recorded
> baseline run.

So the recorded figures are preserved **as measurements of the baseline cohort**, and that is
the only sense in which they could be preserved. Under the seven-contestant cohort the baseline
models' *losses* will differ wherever a new contestant refuses an origin, and that is not a
regression — it is what `comparable` is for (R56). Said plainly because rounding it up would be
the adjacent truth this project has caught itself in twice.

Witnesses, owned by US-3.1b:

- `test_the_baseline_cohort_reproduces_the_committed_goldens` — per-origin forecasts and
  `trials()` values for the three models on the toy fixture, captured **on current HEAD before
  `evaluate` is touched**, committed as `tests/goldens/baseline_toy_forecasts.json`, asserted
  with `==` and not `approx`.
- `test_adding_a_contestant_cannot_change_a_baseline_forecast` — `evaluate` twice over the same
  origins, cohort of three and cohort of three-plus-one, asserting the three models'
  per-origin forecasts are bit-identical while documenting that `trials()` values may differ.
- `test_comparable_intersects_over_the_cohort_and_not_the_global_tuple` — a cohort whose names
  are not in `MODELS` still produces an intersection; planting an extra name in `MODELS` does
  not change a cohort run.
- `test_a_cohort_that_is_not_the_baseline_cannot_carry_the_baseline_name` — the red case for the
  reserved name.
- `test_forcing_the_smearing_factor_still_reaches_the_baseline_cohort` —
  `tools/measure_calibration.py` works by assigning `wf.fit_ols = _forced_smearing(...)`, a
  **module-attribute** monkeypatch. The adapters must therefore call `fit_ols`,
  `_har_levels_forecast`, `_har_semilog_forecast` and `knowable_targets` through their module
  globals and must never capture them as dataclass fields or default arguments. Same rule, same
  reason, as R72's planted `knowable_targets`.
- `test_the_cohort_name_is_on_every_ledger_line_and_absence_means_the_baseline`.
- The real-tier gate: exit criterion 3, `mse 4.295799e-07`.

### 3.4 R87 — two kinds of refusal, and neither is silent

`evaluate` today has one refusal shape: a `SchemaError` from a HAR fit, or a null regressor, or
too few rows, **skips the whole origin** with a recorded reason. A GARCH convergence failure
must not use that shape, because it would delete the origin from the HARs as well and conflate a
GARCH problem with a window problem.

Proposed ruling:

> **R87 — a structural refusal skips the origin; a model's own refusal is recorded against
> `(origin, model)` and leaves the origin in `results`.** `SchemaError` keeps its current
> meaning: the *window* is unfittable (singular design, too few usable rows), and the origin is
> skipped for everyone, exactly as today. A new exception `volab.errors.ModelSkip` means *this
> contestant* cannot answer here; the harness records `(origin, model, reason)` on
> `Evaluation.refusals`, writes `float("nan")` into `forecasts[model]`, and leaves the
> `OriginResult` in place. **Every new contestant's NaN carries a recorded reason** — that is
> R56 verbatim, one level down.

Consequences, priced rather than discovered:

- A refused origin still leaves the comparable intersection for **every** contestant on both
  losses, because `comparable` intersects `scored(...)` sets and the refusing model has no entry
  there. So a refusal costs the whole cohort that origin. That is the same mechanism that
  already drops a non-positive `har_levels` forecast, and `Trial.dropped` already reports it.
- `Trial` gains `refused: int`, the count of origins this contestant itself refused, so a
  reader can tell "my model refused 40 origins" from "someone else's model refused 40 origins".
- **The two baseline NaNs are grandfathered, deliberately.** `har_semilog` returns a bare NaN
  when `Fit.predict` hits a null regressor, and `random_walk` returns one on a null
  `rv_proxy_d1`. Converting those to `ModelSkip` would not move a single forecast value, but it
  *would* move `refused` from 0 on baseline-cohort ledger lines, and the recorded run's lines
  have no such field to be consistent with. So R87 binds new contestants, the inconsistency is
  recorded here rather than tidied, and `test_the_baseline_nans_are_not_counted_as_refusals`
  pins the behaviour so it cannot change by accident.
- **The empty-cohort case.** A contestant that refuses *every* origin empties the intersection
  and zeroes every trial in the configuration. The rule is that this is reported, not guarded
  against: `origins=0`, `value=null`, `refused=n`, one line per contestant, which is exactly
  R74's "a configuration that produced nothing is still counted".
- `OriginResult.training_rows` is defined as the **first cohort entry's** `observations`. In
  every named cohort the first entry is `har_levels`, so the field keeps its current value.
  Witness: `test_every_named_cohort_begins_with_har_levels`.

### 3.5 Where the adapters live, and the one import rule

The three baseline adapters live **inside `src/volab/walkforward/__init__.py`**, wrapping the
existing private functions with no arithmetic change:

```python
BASELINE_COHORT: Cohort          # name "phase2-baseline", (har_levels, har_semilog, random_walk)
MODELS: tuple[str, ...] = ("har_levels", "har_semilog", "random_walk")   # frozen, unchanged
```

Rejected alternative: put the adapters in `src/volab/models/baseline.py`. That makes
`volab.walkforward` import `volab.models.baseline`, which imports `volab.walkforward` for
`fit_ols` — a cycle — and more importantly it would put a model module on the walk-forward's
import path, so `import volab.walkforward` would eventually pull `arch`. The rule instead:

> **`volab.walkforward` imports `volab.models.base` and nothing else from `volab.models`.
> Contestants are passed in, never imported.**

Witness: `test_importing_the_walkforward_does_not_import_arch_or_scipy` — a subprocess asserting
`"arch" not in sys.modules and "scipy" not in sys.modules` after `import volab.walkforward`. Not
numpy: pandas loads numpy, so for numpy only a direct-import AST scan means anything, which is
section 11's separate witness for R82.

`src/volab/models/recalibrated.py` imports `fit_ols` from `volab.walkforward`. That direction is
allowed and the alternative — moving `fit_ols`/`solve` into a new `volab/linalg.py` — is
rejected: it churns the one function whose per-column pivot threshold took two attempts (R58),
moves the module R57 is written against, and buys nothing this phase needs.

### 3.6 What changes in `evaluate`, exactly

Inside the origin loop, after the existing `minimum_training` and null-regressor gates (which run
**first and unchanged**), the hard-coded forecast block is replaced by:

```python
        # Built from ``knowable``, not from a dict over the whole target frame: R77's rule
        # applies to the as_of lookup for exactly the reason it applied to the value lookup.
        usable["target_as_of"] = [as_of_by_base[day] for day in as_of_dates(usable)]
        view = TrainingView(
            symbol=symbol, origin=origin, horizon=horizon, response=column,
            training=usable, row=row,
            returns=returns_through(stopped, origin, calendar),
            minimum_training=minimum_training, stride=every,
        )
        if audit is not None:
            audit.record_returns(origin, view.returns.index[-1])
        forecasts, observations = {}, 0
        try:
            for index, model in enumerate(cohort.models):
                try:
                    answer = model.fit(view).forecast(horizon)
                except ModelSkip as refused:
                    forecasts[model.name] = float("nan")
                    refusals.append((origin, model.name, str(refused)))
                    continue
                forecasts[model.name] = answer.value
                if index == 0:
                    observations = answer.observations
        except SchemaError as refused:
            skipped.append((origin, f"a fit refused the window: {refused}"))
            continue
```

Four additive changes beside it:

- `returns_through(panel, origin, calendar) -> pd.Series` — a new module-level helper reusing
  `target_panel_for`'s derivation verbatim, so the series handed to GARCH is the series
  `build_targets` squares (section 4.2). One derivation, not two.
- `RunAudit.returns: list[tuple[dt.date, dt.date]]` plus `record_returns`, included in
  `violations()` tagged `"returns"`. A third way into the future now exists, and R52's standard
  is that each one is recorded rather than reasoned about. The CLI's printed
  `"N build(s) and N training set(s)"` line is unchanged, because the returns record is a
  separate list.
- `Evaluation.refusals: tuple[tuple[dt.date, str, str], ...] = ()` and
  `Evaluation.contestants: tuple[str, ...] = MODELS`.
- `Trial.cohort: str = "phase2-baseline"` and `Trial.refused: int = 0`, both in `record()`.

`target_as_of` is an extra column on the training frame. `fit_ols` selects
`frame[[*regressors, response]]`, so neither HAR sees it and neither moves. The goldens of
section 3.3 are what proves that rather than this paragraph.

### 3.7 The conformance harness both model stories plug into

`tests/model_conformance.py` — a helper module, not a `test_*` file, in the manner of
`tests/forward_scan.py`. It exports one parametrized suite that US-3.2 and US-3.3 each
instantiate for their own contestant:

```python
def conformance_suite(model: Model, *, toy: Toy) -> None
```

asserting, for that contestant:

1. **Per-origin truncation equivalence** — the per-origin feature rows the run built are
   byte-identical to the persisted table over the origins visited (the existing comparison,
   re-asserted with the contestant in the cohort).
2. **R78 target-side equivalence** — the forecast is unchanged when future targets are removed,
   compared with `==`. For a horizon-free fit this is trivially true and is asserted anyway.
3. **A `RunAudit`-clean fit at every origin**, including the new `"returns"` record.
4. **Every configuration counted**, including one that forecast nothing: `origins=0`,
   `value=null`, `refused=n`.
5. **Determinism** — two `fit().forecast()` calls on the same view return `==` values. This is
   the clause that makes a simulated forecast admissible at all (R90).
6. **`forecast(h)` for `h != view.horizon`** raises `ValueError` when `fit_depends_on_horizon`,
   and for a horizon-free fit, `fit` on a view with `horizon`, `response`, `training` and `row`
   replaced by sentinels returns the identical forecast — which proves GARCH's fit never touches
   the target side, by construction rather than by reading it.


## 4. Decision 2 — the h-step aggregation

### 4.1 The target definition, quoted

`src/volab/features/targets.py`, module docstring:

> For a base session `t` and horizon `h` the target is the sum of the next `h` squared daily
> returns. That is a daily-return proxy for realized variance, not an intraday measure, and must
> not be mistaken for one when compared against the literature.

and `forward_rv_proxy`, which is the code that computes it:

> At position `i`, the sum of `returns[i+1] ** 2` through `returns[i+horizon] ** 2`.
> `NaN` wherever the forward window is incomplete — the last `horizon` positions, and any
> position whose window holds a `NaN` return. Never a partial sum.

So, writing `r` for `log_return`:

```
target_rv_proxy_h{h}(t) = Σ_{j=1..h} r_{t+j}²
```

Three properties of that definition decide the aggregation, and each one rules something out:

- It is a **sum over exactly h steps**, not an average and not a per-day rate. So no `/h` and
  no `×h`; the random walk's `value * horizon` is on the same scale for the same reason.
- It is **uncentred**. `r²`, not `(r − μ̂)²`. So the quantity to forecast is `E_t[r²]`, the
  second moment, not the central second moment.
- Its window is `t+1 … t+h`, which is **exactly** `arch`'s forecast steps 1…h from an origin
  whose last observation is `t`. There is no overlap *inside* one forecast, and no offset to
  reconcile. Overlap is a property of consecutive *origins*, and it is section 5's problem.

### 4.2 R88 — the aggregation

Proposed ruling:

> **R88 — the GARCH-family h-step forecast is the sum of the per-step conditional variances of
> the ×100 return series over steps 1…h, divided by `1e4` once, from a `ZeroMean` specification.**
>
> ```
> f_h(t) = ( Σ_{j=1..h} ν_{t+j|t} ) / 1e4        where ν is arch's forecast .variance of 100·r
> ```
>
> `ZeroMean` rather than `ConstantMean` **because the target is uncentred.** Under a zero-mean
> specification arch's `.variance` *is* `E_t[(100 r)²]`, so the sum is the target's own quantity
> and nothing has to be added. Under `ConstantMean` the correct forecast would be
> `Σ_j (ν_{t+j|t} + μ̂²)` and `.variance` alone would be the wrong quantity — a silent
> mis-specification of the kind that looks right in review.

The ÷1e4 composes with the sum the only way it can: variance scales by the square of a scale
factor, the sum is linear, so dividing once after summing and dividing per step are the same
number up to float ordering. Divide **once, after summing**, so the constant appears exactly
once in the source.

*Cost if wrong:* R83 already priced it — four orders of magnitude, which both losses show
immediately. The subtler cost is `ZeroMean`: a non-zero drift is absorbed into the variance
process rather than modelled. On SPY, daily `μ ≈ 5e-4` against `σ ≈ 1e-2`, so `μ²/σ² ≈ 2.5e-3`
— a 0.25% effect, which is the same order as the R72 leak this project calls invisible. It is
therefore stated here rather than left implicit: the drift is not modelled, and the reason is
that the *target* does not remove it either.

Witnesses, owned by US-3.2:

- `test_the_return_series_handed_to_a_model_squares_to_the_target` — for base sessions in the
  fixture, `sum(view.returns[t+1 : t+h] ** 2) == target_rv_proxy_h{h}(t)` to `rel=1e-12`. Not
  exact: `rolling(h).sum()` and a Python `sum` differ in summation order in the last bits. This
  test is the whole justification for `returns_through` reusing `target_panel_for`'s derivation.
- `test_a_constant_variance_series_aggregates_to_h_times_the_step_variance` — the linearity and
  the ÷1e4, on a synthetic series.
- `test_the_h1_forecast_is_the_first_step_variance_over_1e4` — the composition, exactly.
- `test_the_forecast_is_within_an_order_of_magnitude_of_the_daily_proxy` — the dimensional
  check that catches a 1e4 in the wrong direction, which is R83's named failure mode.

### 4.3 R89 — `rescale` is left at its default, and this was measured

The obvious-looking choice is `rescale=False`, on the grounds that we have already rescaled and
arch should not do it again. **It is wrong**, and the measurement says so. On a raw-scale series
(`σ ≈ 0.011`) under `-W error`:

```
rescale=None   RAISES DataScaleWarning  y is poorly scaled, which may affect convergence …
rescale=True   no warning, scale = 100.0
rescale=False  no warning at all
```

Proposed ruling:

> **R89 — `rescale` is left at its default (`None`), and `True` and `False` are both forbidden.**
> `False` *silences the scale check entirely*, which would delete R83's red case: a future
> change that dropped the ×100 would fit a badly scaled series in silence. `True` rescales
> internally by a factor arch chooses, compounding ours and reporting parameters on a third
> scale. `None` keeps the behaviour R83 relies on: in-band, nothing happens; out of band,
> `DataScaleWarning` under `filterwarnings = ["error"]` is a failing test.
>
> **The warning is raised by `fit()`, not by the constructor**, and `show_warning=False` does
> not suppress it — both measured. So R91's scoped warning filter needs to cover the `fit` call
> and nothing else.

Witnesses, owned by US-3.2:

- `test_a_raw_scale_series_raises_rather_than_fitting_quietly` — the red case. Without it, R83
  is a rescaling nobody has watched fail.
- `test_the_model_never_passes_rescale` — an AST assertion over `src/volab/models/garch.py`
  that the keyword does not appear. A rule a test enforces, not one a reviewer remembers.

### 4.4 R90 — which forecast method each spec uses, and why the first answer was wrong

This design's first draft said GARCH **and GJR** forecast analytically, on the strength of a
probe run on iid Gaussian returns where GJR's analytic forecast matched a 200,000-path simulation
to 0.0147%. That probe was worthless: a GARCH-family model fitted to iid data is nearly
homoskedastic, and a symmetric distribution is precisely the case where the analytic leverage
recursion is correct. Re-measured on a **persistent, leveraged, skewed** series — simulated from
a GJR with `α=0.05, γ=0.10, β=0.87` and skew-t `(η=8, λ=−0.30)`, fitted `λ̂=−0.346`, h=66:

```
GARCH(1,1)  analytic 64.879   sim 200k 64.677   rel  -0.31 %   (within the simulation's own error)
GJR         analytic 39.737   sim 200k 48.288   rel +21.52 %   <- analytic is 17.7 % LOW
EGARCH      analytic unavailable: ValueError "Analytic forecasts not available for horizon > 1"
```

and the Monte Carlo error of the simulation path, 6 seeds, same series:

```
            h=22  S=10,000  0.768 %      S=100,000  0.289 %   (1.00 s/call)
GJR         h=66  S=10,000  1.404 %      S=100,000  0.239 %   (2.91 s/call)
EGARCH      h=22  S=10,000  0.620 %      S=100,000  0.272 %   (0.76 s/call)
            h=66  S=10,000  0.268 %      S=100,000  0.192 %   (2.37 s/call)
```

Proposed ruling:

> **R90 — GARCH(1,1) forecasts analytically; GJR and EGARCH forecast by simulation with
> `simulations=100_000` and a deterministic seed.** Three separate reasons, each measured:
>
> 1. `arch` has **no** analytic multi-step forecast for EGARCH. A fact about the library.
> 2. `arch`'s analytic GJR recursion weights the leverage indicator by its symmetric probability.
>    Under a fitted skew-t that is wrong, and the error is **17.7% low at h=66** — two orders of
>    magnitude larger than anything else in this design, and invisible to a probe on symmetric
>    data. The symmetric GARCH(1,1) recursion involves only `E[e²] = 1` and so stays exact,
>    which the −0.31% line confirms.
> 3. The residual Monte Carlo error at `S=100,000` is **0.19%–0.29%**, which is *inside* the
>    0.2%–1.5% band R72 established as this project's floor for "a difference that matters". It
>    is therefore recorded on the comparison record and printed by the leaderboard, not waved
>    through. `S=10,000` would have been ten times cheaper and up to 1.4% uncertain, which is
>    worse than the leak this project exists to catch.
>
> **The seed is `zlib.crc32` of `f"{name}|{symbol}|{origin.isoformat()}|{horizon}"`**, passed as
> `random_state=np.random.RandomState(seed)` — the keyword that was measured working under
> `-W error`. Never the built-in `hash()` of a string: it is salted per process, so a ledger line
> would not reproduce and an `==` comparison would fail for a reason that had nothing to do with
> forecasting. The seed depends on no property of the data, so it is identical in the
> full-target and truncated-target runs of the R78 test.

Rejected alternative: ship only GARCH(1,1), which is exact and 200× faster. The framework's row
names the leverage specifications, and a measured 17.7% bias in a library's analytic forecast is
a *finding* worth having on the record rather than a reason to retreat from the row.

*Cost if wrong:* the shootout run costs roughly 2.5 hours instead of 15 minutes (section 9), and
two of seven contestants carry a ~0.2% irreducible uncertainty that the other five do not. Both
are stated beside every figure those contestants appear in.

Witnesses, owned by US-3.2:

- `test_the_analytic_method_is_used_only_for_the_symmetric_specification` — an AST assertion that
  `method="analytic"` appears on the GARCH(1,1) path and nowhere else. This is the one that would
  have caught the first draft.
- `test_the_seed_is_derived_without_the_salted_builtin_hash` — AST: `hash(` does not appear;
  `zlib.crc32` does.
- `test_the_same_view_forecasts_the_same_number_twice` (also clause 5 of the conformance suite).
- `tools/measure_garch_forecast_error.py` — prints all three figure blocks above. R32: the 17.7%
  and the 0.19%–0.29% are the two numbers a reader is most likely to doubt, so they name the
  command that reproduces them.

### 4.5 R91 — the three GARCH specs, and the convergence refusal

The framework's phrase *"GARCH(1,1), GJR/EGARCH with skew-t"* leaves it ambiguous whether skew-t
attaches to GARCH(1,1) as well. Resolved explicitly, so the trial count is not a surprise:

| contestant | mean | volatility | distribution | forecast |
|---|---|---|---|---|
| `garch11_skewt` | `ZeroMean` | `GARCH(p=1, q=1)` | `SkewStudent` | analytic |
| `gjr11_skewt` | `ZeroMean` | `GARCH(p=1, o=1, q=1)` | `SkewStudent` | simulation, S=100,000 |
| `egarch11_skewt` | `ZeroMean` | `EGARCH(p=1, o=1, q=1)` | `SkewStudent` | simulation, S=100,000 |

Skew-t across all three, so the only thing differing between them is the variance recursion,
which is the comparison the row is asking for. A normal-errors GARCH(1,1) is one constructor
argument away and its only cost is two more ledger lines per configuration; it is not shipped,
and that is a scope decision rather than a finding.

> **R91 — non-convergence is detected by `res.convergence_flag != 0`, and a scoped warning
> filter makes the model behave identically under pytest and under the CLI.** Measured:
> `options={"maxiter": 1}` returns `convergence_flag == 9`, and emits a `ConvergenceWarning`
> *only when `show_warning=False` is not passed*. So:
>
> - `fit` is **not** passed `show_warning=False`. The warning is wanted.
> - The `fit` call is wrapped in `warnings.catch_warnings()` + `simplefilter("error")`, so a
>   warning is an exception regardless of the caller's filters. The CLI has no `-W error`, and
>   without this wrapper it would print a warning and score a non-converged fit while pytest
>   raised — the same behaviour under both is the whole point.
> - Exactly two categories become `ModelSkip`:
>   `arch.utility.exceptions.ConvergenceWarning` and `StartingValueWarning`.
>   `DataScaleWarning` is **deliberately not** in that set: it means the ×100 is wrong, which is
>   a bug and must fail the suite (R89). Every other warning propagates.
> - **And** `res.convergence_flag != 0` raises `ModelSkip` independently, because the flag is the
>   optimiser's own status and does not depend on a warning having been emitted. Two detectors
>   for one condition, deliberately: the warning can be configured away, the flag cannot.

Witnesses, owned by US-3.2: `test_a_non_converging_origin_is_a_recorded_skip_not_a_forecast`
(planted with `options={"maxiter": 1}`, asserting `convergence_flag == 9` reaches a `ModelSkip`),
`test_a_data_scale_warning_is_not_converted_into_a_skip`,
`test_an_unexpected_warning_propagates_rather_than_becoming_a_skip`,
`test_the_refusal_is_identical_with_and_without_the_callers_warning_filters`.

### 4.6 Missing bars, and the window GARCH actually fits

`log_returns`' docstring refuses to bridge a gap: a session with no bar poisons the two returns
spanning it. Dropping those NaNs would bridge it.

> **The GARCH fit uses the contiguous NaN-free tail of `view.returns` ending at the origin.** If
> that tail is shorter than `view.minimum_training`, the contestant raises `ModelSkip` with the
> length in the message. NaNs are never dropped from the middle, because that reintroduces the
> bridge `log_returns` exists to prevent.

*Cost:* a missing bar discards every return before it for that origin, so one gap early in the
sample is free and one gap late is expensive. Witness:
`test_a_gap_truncates_the_fit_window_rather_than_being_bridged`.

One asymmetry to record rather than fix: at h=66 the HAR training set ends 66 sessions before the
origin, because a target has to have completed; GARCH's return series runs right up to the
origin. Both are point-in-time correct — returns at `t` are knowable at `t`, targets are not —
but the GARCH family conditions on 65 sessions of information the HARs' *fit* structurally
cannot use. That is a real difference between the contestants and belongs in the write-up, not
in a footnote.


## 5. Decision 3 — DM, MCS and SPA on overlapping targets

### 5.1 The overlap, derived

Origins come at a stride of `s` sessions. The loss at origin `t` depends on returns
`r_{t+1} … r_{t+h}`. Two origins `m` strides apart share at least one return iff

```
m·s ≤ h − 1      ⇔      m ≤ ⌊(h − 1)/s⌋ ≡ q
```

so the loss-differential series is, by construction, **MA(q) in origin index**. At `s = 5`:

| h | q = ⌊(h−1)/s⌋ | h_eff = q+1 | n (SPY, recorded run) | n_eff = n/(q+1) |
|---|---|---|---|---|
| 1 | 0 | 1 | 251 | 251 |
| 5 | 0 | 1 | 250 | 250 |
| 22 | 4 | 5 | 247 | 49.4 |
| 66 | 13 | 14 | 238 | 17.0 |

h=5 at stride 5 has **no overlap at all**: the windows `t+1…t+5` and `t+6…t+10` are disjoint.
That is worth stating because "h=5 overlaps" is the intuitive and wrong answer.

`q` is an **upper bound** on the realised overlap, because dropped origins only remove shared
returns. Using the bound is the conservative direction.

### 5.2 R92 — the HAC estimator and the Harvey correction, matched to each other

> **R92 — the headline DM statistic uses a rectangular HAC estimator truncated at exactly `q`,
> with the Harvey-Leybourne-Newbold correction at `h_eff = q + 1` against `t(n−1)`. A
> Bartlett-Newey-West statistic at `L = max(q, ⌊4(n/100)^(2/9)⌋)` is reported beside it, with no
> Harvey factor and a standard-normal reference.**
>
> The pairing is the point. The HLN factor is derived for the **rectangular** estimator truncated
> at the forecast horizon's own MA order; applying it to a Bartlett estimator at a longer
> bandwidth is a correction for a variance nobody computed. The board requires Harvey, so the
> headline is the combination Harvey is derived for:
>
> ```
> d_i = L_left,i − L_right,i                   aligned on comparable(loss), in origin order
> γ_k = (1/n) Σ_{i=k+1..n} (d_i − d̄)(d_{i−k} − d̄)
> V   = γ_0 + 2 Σ_{k=1..q} γ_k                 rectangular, truncated at q
> DM  = d̄ / sqrt(V/n)
> DM* = DM · sqrt( (n + 1 − 2·h_eff + h_eff·(h_eff − 1)/n) / n )        compared to t(n−1)
> ```
>
> **With `h_eff`, never the daily `h`.** At h=66 with n=238, the daily `h` gives a factor of
> 0.725 and `h_eff = 14` gives 0.943. Passing 66 to a parameter that means "horizon in units of
> the loss series" is the default that looks fine and is wrong.
>
> `V ≤ 0` is a **recorded refusal**, checked before any `sqrt`. The rectangular estimator is not
> positive semi-definite and this is a real possibility, not a defensive branch; `numpy` would
> emit a `RuntimeWarning`, which under `filterwarnings = ["error"]` is a failing test rather
> than a NaN. `V == 0` — a constant differential, i.e. two contestants with identical losses —
> is refused separately, with that as its stated reason.
>
> The second column exists because volatility clustering makes QLIKE and squared-error losses
> autocorrelated well beyond the overlap, which the rectangular estimator at `q` ignores
> entirely — at h=1 and h=5, `q = 0`, so the headline statistic uses the plain sample variance.
> Bartlett-NW with `1/n` autocovariances is positive semi-definite (Newey-West 1987), so it
> cannot be refused for a non-positive variance, and `⌊4(n/100)^(2/9)⌋` is Newey-West's own
> automatic truncation; at n=247 it is 4. **Where the two columns disagree about a sign at the
> 5% level, the leaderboard prints both and claims neither.** That is the honest reading of a
> disagreement between two variance estimators on 17 to 251 effective observations.

`scipy.stats.t.sf` and `scipy.stats.norm.sf` supply the p-values. No `dieboldmariano` package and
no statsmodels HAC: statsmodels is a transitive dependency of `arch`, and reaching into a
transitive pin is exactly what R82 declined to do for numpy.

### 5.3 R93 — the stationary-bootstrap block length

> **R93 — the block length is `max(q + 1, ⌈Politis–White on the loss levels⌉)`, computed with
> `arch.bootstrap.optimal_block_length` on the **levels** matrix that MCS and SPA actually
> consume, never on cross-model differentials.** Measured, n=238:
>
> ```
> MA(14)-overlapping loss, levels                14.21  (clean pair: 4.33 and 10.47)
> the same pair's cross-model differential        0.20
> iid loss, levels                                1.26
> iid loss, cross-model differential              1.79
> ```
>
> The 14.21 is why the estimator is trusted: on an overlapping series it recovers the overlap
> order. The 0.20 is why it must see levels: two contestants that *share* the same overlapping
> target differ mostly by idiosyncratic noise, so the common dependence **cancels in the
> differential** and the estimated block collapses towards iid — which would resample the
> overlapping losses as though they were independent, the single error this whole section exists
> to avoid. MCS and SPA difference internally; the block length describes the dependence in the
> data they are given, which is the levels.
>
> The `q + 1` floor matters at h=1 and h=5, where Politis-White returns a block near 1 —
> effectively iid resampling, which would destroy the volatility clustering the losses have even
> without overlap.
>
> `reps = 10_000`, `bootstrap="stationary"`, `seed = 20261025`, all three fixed and all three on
> the comparison record, so membership is reproducible from the file.

(A separate note, because it is an easy mistake and not the one above: estimating the block from
a *time*-differenced series returns a spurious 47.0 on iid data, because over-differencing
produces a non-invertible MA(1). Neither levels nor cross-model differentials are that, but the
distinction cost this design one wrong rationale.)

### 5.4 R94 — what is unusable, and why no stride rescues it

> **R94 — `n_eff = n/(q+1)` is printed beside every multiple-comparison figure, and MCS
> membership is refused where `n_eff < 2/α`.**
>
> The threshold is a **chosen** one, not a derivation, and is labelled as such: the bootstrap
> distribution's tail is over replications, not observations, so no theorem fixes the number.
> The reasoning is that a confidence set at size α needs the sample to be able to place an
> observation in an α tail at all (`1/α`), with a factor of two for margin. So **20 at
> α = 0.10 and 8 at α = 0.25**:
>
> | h | n_eff | 10% MCS | 25% MCS | DM |
> |---|---|---|---|---|
> | 1 | 251 | reported | reported | reported |
> | 5 | 250 | reported | reported | reported |
> | 22 | 49.4 | reported, flagged thin | reported | reported |
> | 66 | **17.0** | **refused, with the reason printed** | reported | reported with `n_eff` beside it and no significance claimed |
>
> *Cost of the threshold:* at h=66 the 10% set is not reported at all, where the alternative
> would be reporting a very wide set and letting the reader see `n_eff = 17`. The refusal is
> preferred because a membership list is read as a conclusion and a footnote is not; but if the
> coordinator prefers the wide set with its `n_eff`, that is a defensible reversal of exactly one
> line of code.
>
> **A smaller stride does not help, for h ≥ s.** With a window of `W` sessions, `n ≈ W/s` and
> `q + 1 ≈ h/s`, so `n_eff ≈ W/h` **independently of `s`**. At h=66 over 2021–2025 that is
> `1253/66 ≈ 19` at stride 1 against 17 at stride 5 — the stride is not the problem, the window
> is. Extending `--from` back across the whole dev window (≈2,245 sessions) would raise it to
> ≈30, still thin. So **h=66 is underpowered for multiple comparison at any stride available to
> this project**, and that is a property of five years of daily data, not of the harness. For
> `h < s` the invariance does not hold and a smaller stride does buy observations: at h=1,
> stride 1 gives roughly five times stride 5's `n_eff`.

Witnesses, owned by US-3.4:

- `test_the_overlap_lag_is_derived_from_the_horizon_and_the_stride` — asserts the exact table
  `{(1,5): 0, (5,5): 0, (22,5): 4, (66,5): 13}`, and `(22,1): 21`, `(66,1): 65`.
- `test_the_harvey_correction_uses_sampling_units_not_the_daily_horizon` — asserts 0.943 rather
  than 0.725 at `(h=66, s=5, n=238)`, which is the specific wrong answer this ruling exists to
  prevent.
- `test_a_non_positive_rectangular_hac_variance_is_refused_before_the_square_root`, and
  `test_an_identical_pair_of_contestants_is_refused_rather_than_divided_by_zero`.
- `test_the_block_length_is_floored_by_the_overlap` and
  `test_the_block_length_is_estimated_from_loss_levels_not_from_cross_model_differentials` —
  the second asserts the 14.21-against-0.20 contrast, so the rationale is in a test and not only
  in this document.
- `test_effective_observations_are_invariant_to_the_stride_when_the_horizon_exceeds_it` —
  scoped to `h >= s`, because at h=1 it is false.
- `test_the_model_confidence_set_is_refused_where_the_effective_sample_is_too_small` — h=66 at
  10%, with the reason on the record.
- `test_the_bootstrap_is_reproducible_from_the_recorded_seed`.

### 5.5 MCS, SPA and multiplicity

- **One bootstrap, two sizes.** Running `MCS(..., size=0.10)` and `MCS(..., size=0.25)`
  separately can produce sets that are not nested. Run the bootstrap once and read membership at
  both sizes from the same `MCS.pvalues`, which `arch` exposes directly.
- **SPA's benchmark is `har_levels`.** SPA answers "does anything beat the benchmark", and the
  question this project is organised around is whether anything beats HAR — its stated
  contribution is that it does *not* claim to. A second SPA against `random_walk` is reported
  beside it, because a contestant that cannot beat a scaled squared return is worth seeing.
- **DM is reported for all pairs with the count of tests beside it.** Seven contestants is 21
  pairs, times 4 horizons, 2 losses and 3 symbols: **504 DM tests**, each with two variance
  columns. No selection is made on those p-values, and the MCS is the multiplicity-aware
  statement. The count goes on the comparison record and on the leaderboard, for the same reason
  the trial count does.


## 6. The sink boundary, and US-3.5

R84 is resolved for the ledger and says explicitly that *"nothing in the leaderboard's design
should assume the ledger is the only possible sink"*. Two facts force the shape:

- **MCS membership cannot go in `trials.jsonl`.** `count_trials` counts lines, and a comparison
  record is not a trial. A second file is required, not preferred.
- **The comparison layer cannot read the ledger.** MCS needs per-origin loss series; the ledger
  holds means. So `compare` consumes `Evaluation` objects and *writes* to a sink.

> **R95 — the comparison layer computes and hands its results to a `Sink`; it never opens a
> path.** One protocol, two methods, one implementation now and MLflow later:
>
> ```python
> # src/volab/compare/sink.py
> class Sink(Protocol):
>     def append_trials(self, trials: Sequence[Trial], *, at, sha, dirty) -> int: ...
>     def append_comparisons(self, records: Sequence[Comparison], *, at, sha, dirty) -> int: ...
>
> class JsonlSink:   # data/trials.jsonl and data/comparisons.jsonl
>     ...
> # class MlflowSink: not written. Reachable behind a pyarrow bump without touching compare/.
> ```
>
> `JsonlSink.append_trials` delegates to the existing `volab.walkforward.append_trials`, so
> there is one ledger writer and not two. Witness: `test_only_the_sink_opens_a_path` — an AST
> assertion over `src/volab/compare/` that no module but `sink.py` names `open`, `Path.write_*`
> or `to_json`.
>
> **The join key is `(git_sha, at, symbol, horizon, loss, cohort)`**, and both files are written
> in one run under one `at`, so the leaderboard can join them without guessing. A comparison
> record whose `(sha, at)` has no trials is a bug, and the leaderboard says so rather than
> printing a ranking with no counts.

`Comparison` carries, per `(symbol, horizon, loss, cohort)`: the stride, `origins`, `n_eff`, the
rectangular lag `q` and the Bartlett lag `L`, the block length with both of its inputs, `reps`,
`seed`, the per-pair DM results in both columns, the MCS p-values with membership at both sizes
or the refusal reason, both SPA results, the number of DM tests performed, and the Monte Carlo
error band for any simulated contestant in the cohort (R90).

`data/comparisons.jsonl` needs a `.gitignore` exception in the manner of `data/trials.jsonl` and
for the identical reason — an MCS membership `git clean -xdf` erases cannot be the membership a
write-up reports. **That one-line edit is the coordinator's**, since US-3.4 and US-3.5 both
depend on the file existing.

US-3.5 reads both files and computes nothing. Three definitions it must pin down rather than
imply, because "how many trials were evaluated" is the board's acceptance criterion:

- **The headline count is every line in the ledger**, cumulative across runs and cohorts, with
  the per-cohort breakdown beside it. Counting only the current cohort would let a new cohort
  reset the number, which is R59's forking-paths point turned on the reporting layer.
- **Dirty lines are shown, never silently ranked.** `git_dirty: true` or `null` prints a marker
  on the row, per the board.
- **A line with no `cohort` key is `"phase2-baseline"`** (R86). Witness, owned by US-3.5:
  `test_a_ledger_line_without_a_cohort_reads_as_the_phase2_baseline` — the 72 existing lines
  are the fixture.


## 7. R97 — one driver loop, not two

`python -m volab.compare run` would be a second per-configuration loop, and R76 deleted the last
one of those for diverging from `evaluate`'s. Four rulings live in the walk-forward CLI's loop
and all four are load-bearing: **R62** (no `--end`, `--to` past `dev_end` refused), **R63** (an
audit violation exits non-zero), **R70** (sha and dirty flag captured at the start, scoped to the
repository root), **R74** (append per configuration, with a flush on the refusal path). A second
entry point that takes a date argument is precisely where the holdout seal reopens.

> **R97 — the per-configuration driver is extracted into
> `volab.walkforward.run_configuration(...)` and both entry points call it.**
> `src/volab/walkforward/__main__.py` is reduced to argument parsing plus that call, so its
> printed console shape and all of its existing tests are unchanged; `volab.compare.__main__`
> parses the same arguments, passes a different cohort and a `Sink`, and inherits R62, R63, R70
> and R74 by delegation rather than by re-implementation. Reads go through `load_for` and
> `scoreable_origins` only — neither takes an `end` later than `dev_end`, and no new function
> here does either.

The extraction belongs to US-3.1b, with `__main__.py` in its file set. US-3.4's acceptance
includes the existing CLI tests parametrized over **both** entry points:
`test_the_entry_point_refuses_a_to_past_dev_end`,
`test_an_audit_violation_exits_non_zero`,
`test_the_refusal_path_still_flushes_the_trials_already_counted`,
`test_every_ledger_line_names_the_tree_that_produced_it`.

*Cost:* `__main__.py` joins the seam's edited file set, so the extraction and the cohort change
land in one story rather than two. That is the intended trade: R76's lesson is that two loops
with one of them unexercised is where the tested and the believed come apart.


## 8. File sets: disjoint, and the shared seam named

### US-3.1b — the seam (new story; see section 10)

| path | state |
|---|---|
| `src/volab/walkforward/__init__.py` | edit — the view, the cohort, the refusals, `run_configuration` |
| `src/volab/walkforward/__main__.py` | edit — delegate to `run_configuration` (R97) |
| `src/volab/models/__init__.py` | new, docstring only — no imports, so `import volab.models` cannot pull `arch` |
| `src/volab/models/base.py` | new |
| `src/volab/errors.py` | edit — `ModelSkip` |
| `tests/model_conformance.py` | new — helper module, not collected |
| `tests/test_model_interface.py` | new |
| `tests/goldens/baseline_toy_forecasts.json` | new — captured on HEAD **before** the edit |
| `pyproject.toml`, `uv.lock` | edit — declare `scipy`, add its mypy override (section 11) |

### US-3.2 — the GARCH family

| path | state |
|---|---|
| `src/volab/models/garch.py` | new |
| `tests/test_garch.py` | new |
| `tools/measure_garch_forecast_error.py` | new — the probe behind R90's three figure blocks |

### US-3.3 — recalibrated HAR

| path | state |
|---|---|
| `src/volab/models/recalibrated.py` | new |
| `tests/test_recalibrated.py` | new |

### US-3.4 — the comparison layer

| path | state |
|---|---|
| `src/volab/compare/__init__.py` | new |
| `src/volab/compare/series.py` | new — `LossSeries`, the one way losses enter |
| `src/volab/compare/dm.py` | new — `overlap_lag`, `newey_west_lag`, `harvey_factor`, `diebold_mariano` |
| `src/volab/compare/multiple.py` | new — `block_length`, `model_confidence_set`, `superior_predictive_ability` |
| `src/volab/compare/sink.py` | new — `Sink`, `JsonlSink`, `Comparison` |
| `src/volab/compare/cohorts.py` | new — the named cohorts; the only module importing both model modules |
| `src/volab/compare/__main__.py` | new — `python -m volab.compare run` |
| `tests/test_compare.py` | new |

`LossSeries` is the seam between the harness and the statistics, and it exists so a ragged matrix
can never reach `MCS`:

```python
@dataclass(frozen=True)
class LossSeries:
    symbol: str
    horizon: int
    loss: str
    cohort: str
    stride: int
    origins: tuple[dt.date, ...]              # sorted, exactly evaluation.comparable(loss)
    values: dict[str, tuple[float, ...]]      # contestant -> loss per origin, aligned to origins

def loss_series(evaluation: Evaluation, loss: str, *, cohort: str) -> LossSeries
```

Witness: `test_every_loss_series_is_rectangular_and_on_comparable_origins`.

### US-3.5 — the leaderboard

| path | state |
|---|---|
| `src/volab/leaderboard/__init__.py` | new |
| `src/volab/leaderboard/__main__.py` | new |
| `tests/test_leaderboard.py` | new |

### Coordinator-owned, because more than one story needs them

- **Everything in US-3.1b.** It is listed as a story rather than a pre-dispatch stub because
  `src/volab/walkforward/__init__.py` is not a stub: it is the single edit in this phase that
  can move `4.295799e-07`, and it needs the golden capture done *before* it is touched. Creating
  it by hand and handing the halves to two implementers is the one way to lose that.
- `.gitignore` — the `!data/comparisons.jsonl` exception (section 6).
- `pyproject.toml` / `uv.lock` — declaring `scipy`. A lock edit is not an implementer's to make.

**Disjointness.** US-3.2 and US-3.3 share no file, and neither touches anything US-3.4 or US-3.5
owns. `src/volab/compare/cohorts.py` is the only module that imports both `garch.py` and
`recalibrated.py`, and it belongs to US-3.4, which serialises after both — so no story imports a
module that does not yet exist.


## 9. Recalibrated HAR — the design US-3.3 has to hit

The board's acceptance is that *"the MZ regression is fitted on training-window forecasts only,
so recalibration cannot see the origin it is scoring"*. The framework's line 27 is the source:

> A 2026 study used VOLARE data on 50 assets with QLIKE, DM tests and the MCS. … After
> Mincer-Zarnowitz recalibration, most of its edge at 1 and 5 days disappeared.

So: `y_t = a + b·f_t + e_t`, fitted on past (forecast, realized) pairs; the recalibrated forecast
is `â + b̂·f(origin)`.

> **R96 — the MZ sample is an inner walk-forward at the outer stride, taken entirely from
> `view.training`, and the scored origin is excluded structurally rather than by a filter.**
>
> At outer origin `t`, for each inner origin `c` on the stride lattice within the trailing
> `calibration_points = 60` lattice points:
>
> - the inner fit is `fit_ols` over `view.training[view.training.target_as_of <= c]`, which is a
>   *further truncation* of an already-truncated frame;
> - the inner forecast is that fit applied to `view.training.loc[c]`;
> - the inner realized value is `view.training.loc[c, view.response]`.
>
> Fewer than 40 usable pairs is a `ModelSkip` with the count in the message.
>
> **The scored origin cannot enter the sample.** `view.training` holds only base sessions whose
> target had completed by `t`, and the origin's own target completes `h` sessions *after* `t`, so
> the origin's row is not in the frame at all. The exclusion is the same `as_of` slice the whole
> harness rests on, not a condition someone remembered to write.
>
> **`target_as_of` is on the view for this reason**: without it the model would have to reach
> back into the target frame to find out when each training row became knowable, which is the
> R78 bypass class — the audit would pass and the fit would be reading the future.

Shipped contestant: `har_levels_mz`. The class takes `base: Model` so `har_semilog_mz` is a
constructor argument away; only one is on the roster, to keep the trial count honest.

Three prices, stated:

- **No floor.** `â + b̂·f` can be non-positive, so QLIKE is undefined at those origins and they
  leave the comparable set for everyone. Identical to `har_levels` (R54 as amended), and
  identical reasoning: a floor is a free parameter chosen to make a loss computable.
- **It depends on truncation equivalence, and it is the only contestant that does.** The inner
  regressor rows come from a table built through the *outer* origin, not rebuilt at `c`. The
  outer forecast — the thing being scored — still never leans on that, and the dependency has
  the strongest witness in the repository: the run's own exhaustive per-origin byte comparison
  (`test_the_per_origin_rows_are_byte_identical_to_the_persisted_table`, plus the CLI's
  "N of N origins identical" line). Rebuilding at every inner origin instead would cost ~350
  extra feature builds per configuration against a property the run already checks at every
  origin it visits.
- **No memoisation of inner forecasts.** `f(c)` is recomputed at each outer origin. It would be
  safe to cache — the key determines the value — but a cross-origin cache is R37's memoisation
  class, and 60 `fit_ols` calls per origin is ~0.3 s, which is affordable.

Witnesses, owned by US-3.3:

- `test_an_in_sample_mincer_zarnowitz_scores_better_and_is_refused` — the red case. It has to
  *manufacture* the origin's realized target to build the in-sample variant at all, which is the
  strongest available statement of the refusal; it then shows the in-sample loss is better and
  that the shipped model reports the worse one.
- `test_the_calibration_sample_never_contains_a_target_that_completed_after_the_origin` —
  `max(target_as_of)` over the MZ sample is `<= origin`, through the model, not re-derived in the
  test (R72's lesson).
- `test_too_few_calibration_points_is_a_recorded_skip`.
- `test_the_recalibrated_forecast_reduces_to_the_base_forecast_when_a_is_zero_and_b_is_one`.


## 10. What this costs, and the board changes it implies

- **A new story, US-3.1b**, between US-3.1 and the fan-out, owning the seam, the goldens and
  R97's extraction. US-3.2 and US-3.3 are blocked on it rather than on US-3.1.
- **Trials roughly double and then some.** 7 contestants × 3 symbols × 4 horizons × 2 losses =
  **168 trial lines** per shootout run, against the 72 already on the ledger. Plus 504 DM tests
  in two variance columns, 24 MCS computations (of which the six h=66 10% ones are refusals) and
  48 SPA computations per run. The board already carries "more contestants is more trials" as a
  risk; this is the number.
- **Runtime, and it is the real cost of R90.** Measured per fit on 1,250 observations: GARCH
  61 ms, GJR 23 ms, EGARCH 46 ms. Forecasts at `S=100,000`: 1.0 s at h=22 and 2.9 s at h=66 for
  GJR, 0.76 s and 2.4 s for EGARCH. With the recalibrated model's ~0.3 s of inner fits, the
  shootout is roughly **2.5 hours** against the recorded baseline run's 400 s. Acceptable for a
  once-per-phase run. The knobs if it is not are `calibration_points` and `simulations`, and
  both change numbers, so both belong on the ledger rather than in a flag someone passes.
- **The baseline models' losses change under the seven-contestant cohort**, wherever a new
  contestant refuses an origin. Not a regression (section 3.3), but it means the leaderboard's
  headline figures are not the recorded run's figures, and the write-up must say which cohort
  each number belongs to.


## 11. What I found that contradicts the board or the decisions logs

Reported rather than fixed, since none of it is this story's to change:

1. **R82 says the numpy ban "keeps a witness rather than becoming a convention". There is no
   witness.** `grep -rn "numpy\|scipy" tests/` finds three comments and no test. Nothing
   mechanically stops `import numpy` appearing in `src/volab/features/realized.py` tomorrow.
   Proposed: an AST scan in `tests/test_import_discipline.py` over
   `{features/*, panel.py, store.py, walkforward/__init__.py}` for a direct `numpy`/`scipy`/`arch`
   import, with its own red case — additive, and `ALLOWED` is untouched. It has to be a
   direct-import scan rather than a `sys.modules` check, because pandas loads numpy anyway.
2. **Two source comments are now false.** `src/volab/walkforward/__init__.py`'s linear-algebra
   banner says *"numpy is a transitive dependency of pandas and is absent from pyproject.toml,
   so importing it is forbidden here as everywhere else in this package"*, and
   `tests/test_walkforward.py:67` says *"numpy is absent from pyproject.toml, so the solve is
   ours"*. `pyproject.toml` declares `numpy>=2.5.4` as of commit `c4508ae`. The *rule* still
   holds under R82; its stated *reason* does not. Both lines should point at R82 instead — the
   first is in the seam's file and can go with that edit.
3. **scipy is admitted by R82 but not declared.** `pyproject.toml` lists `arch`, `numpy`,
   `duckdb`, `exchange-calendars`, `pandas`, `pyarrow`, `pyyaml`. scipy is in `uv.lock` only as
   `arch`'s transitive dependency, so `from scipy import stats` in `compare/dm.py` would depend
   on a pin nobody declared. **And `mypy --strict` fails on it today**, measured:
   `error: Library stubs not installed for "scipy" [import-untyped]`. So declaring scipy *and*
   adding a `[[tool.mypy.overrides]]` entry (or `scipy-stubs` to the dev group) is a prerequisite
   for US-3.4, and it is a lock edit. `arch` itself type-checks clean — it ships `py.typed`.
4. **The board's US-3.1 row names `docs/superpowers/phase2-completion-decisions.md` as this
   story's file.** My brief says a new spec under `docs/superpowers/specs/` and explicitly not to
   edit the decisions log. I followed the brief; the board's Files cell for US-3.1 should be
   corrected to this path, with R85-R97 folded into the decisions log by the coordinator.
5. **The board's US-3.5 row says the leaderboard "reads `data/trials.jsonl`" and reports MCS
   membership at 10% and 25%.** Those two cannot both be true: the ledger holds per-trial means,
   and MCS membership can only come from the comparison records (section 6). The row should name
   both files, with "computes nothing of its own" preserved — it is the *computation*, not the
   single file, that the criterion is about.
6. **`rescale=False` is the plausible-looking choice and it silences R83's own red case**
   (section 4.3, measured). Worth folding into R83 as an amendment, since R83 is the ruling a
   future reader will consult before writing an `arch_model(...)` call.
7. **Recorded in the Phase 2 log as known and still open**, and this phase inherits all of it
   unchanged: `evaluate` does not reject duplicate or unsorted origins; the skip message counts
   rows before `dropna`, so "only N knowable rows" is an upper bound; `(realized - forecast) ** 2`
   can raise `OverflowError` on a diverged fit; a dirty run records no diff hash.


## 12. The probes behind every measured figure

R32 applies to a design document too. Each figure above came from a probe run during this story;
`tools/measure_garch_forecast_error.py` (US-3.2) is the committed home for the first three, and
the rest are one-off and recorded here with enough detail to re-run.

| figure | §| how it was measured |
|---|---|---|
| GJR analytic 17.7% low at h=66 | 4.4 | `ZeroMean` + `GARCH(1,1,1)` + skew-t fitted to a series simulated from `[ω=0.03, α=0.05, γ=0.10, β=0.87, η=8, λ=−0.30]`; analytic 39.737 against 200,000-path simulation 48.288 |
| GARCH(1,1) analytic exact | 4.4 | same series, analytic 64.879 against 200,000-path 64.677, −0.31%, inside the simulation's own error |
| EGARCH has no analytic multistep | 4.4 | `ValueError: Analytic forecasts not available for horizon > 1` |
| MC spread 0.19–0.29% at S=100,000 | 4.4 | 6 seeds × {GJR, EGARCH} × {h=22, h=66}, same series; and 0.27–1.40% at S=10,000 |
| forecast cost 0.76–2.91 s/call | 4.4, 10 | same grid, wall clock per call |
| fit cost 23–61 ms | 10 | 1,250 observations, each spec |
| `rescale`'s three-way behaviour | 4.3 | σ≈0.011 series under `-W error`: `None` raises, `True` sets `scale=100.0`, `False` silent |
| `DataScaleWarning` comes from `fit()` | 4.3 | `catch_warnings(record=True)` around construction (empty) then around `fit` (fires, even with `show_warning=False`) |
| `convergence_flag == 9`, warning only without `show_warning=False` | 4.5 | `options={"maxiter": 1}` |
| Politis-White 14.21 / 0.20 / 1.26 / 1.79 / 47.0 | 5.3 | `optimal_block_length` on MA(14) levels, that pair's cross-model differential, iid levels, iid cross-model differential, and a time-differenced iid series |
| Newey-West lag 4 at n=247 | 5.2 | `⌊4·(247/100)^(2/9)⌋ = ⌊4.89⌋` |
| Harvey 0.943 against 0.725 | 5.2, 5.4 | the HLN factor at `(n=238, h_eff=14)` and at `(n=238, h=66)` |
| `n_eff` 251 / 250 / 49.4 / 17.0 | 5.1 | `n/(q+1)` on the recorded run's SPY origin counts 251 / 250 / 247 / 238 |
