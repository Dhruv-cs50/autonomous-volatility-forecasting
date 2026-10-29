# `gjr11_skewt`'s exact multistep forecast — design

**Status:** awaiting approval. Nothing here is built.
**Date:** 2026-10-28
**Branch:** `phase-2-completion-groundwork`
**Blocks:** registration (R120's open question). Does not block Phase 2's close.
**Scope:** the *estimator*. Not the contestant, not arch's stationarity constraint (R118, Phase 3).

---

## Intent

`gjr11_skewt` forecasts by simulating 100,000 skew-t paths. R115 refuses significance for any
simulated contestant whose Monte-Carlo error has not been measured **on the series being
evaluated**. At the holdout reveal those are holdout fits, and measuring the error needs an exact
value to compare against — a seed spread is not one, because it understates the true sd by a
median factor of 58.8 at h=66 (R119). So while GJR simulates, R115 withholds its significance at
*every* horizon, including h=1 and h=5 where it leads QLIKE.

The multi-step conditional mean exists in closed form (R117). Computing it removes GJR from
R115's rule entirely, and it is also *cheaper*: measured below, 14 microseconds against 3.2-4.0
seconds per origin at h=66.

What this does **not** do: it does not make GJR's forecast good. The fitted process is explosive
at 238/238 SPY and QQQ origins because arch's feasible region assumes `kappa = 1/2` (R118), and
the exact value is *worse* than the simulated one on loss (exact 6.74e-4 against 6.19e-4 at h=66,
R120). Correcting the estimator makes an honest number out of an unmeasurable one. That is the
whole claim.

## Non-goals

- A `kappa(eta, lambda)`-aware stationarity constraint replacing arch's `0.5`. That changes the
  fit. Refused before registration (R118), recorded as a Phase 3 candidate.
- Excluding GJR at any horizon. R120 settled this: the quantity exists, so there is nothing
  non-existent being estimated, and the explosive fit is the most informative result in the
  shootout.
- Changing EGARCH. One flag is raised in §6 and deliberately left undesigned.
- Re-measuring the Monte-Carlo table on SPY. §6 shows that instruction (R119) is superseded for
  GJR and inapplicable for EGARCH.

---

## What was measured for this design

All of it on this branch, against the live tree.

**Provenance, and a debt this design incurs.** The scripts live in the session scratchpad
(`arch2/k1.py` … `arch2/k6.py`, with `probe/gmom.py` the previously verified Simpson quadrature)
and are **uncommitted by the task's own instruction**, so they will vanish. R32 and R79 require a
cited figure to have a re-runnable probe in `tools/`. The implementing story therefore owes
`tools/measure_garch_forecast_error.py` a new block that reproduces M8-M12 and M14 — arch's
analytic path, the exact path and the simulated path side by side, per horizon — and until that
block exists M8-M14 are measurements recorded in this document rather than figures the repository
can regenerate. That is a real gap and is named here rather than left to be discovered.

| # | Measurement | Result |
|---|---|---|
| M1 | Closed-form `kappa` vs Simpson quadrature, 14 `(eta, lambda)` points with `eta >= 4` | max abs diff **5.6e-12** |
| M2 | Same, on four real SPY GJR fits (`eta` 5.09-6.44, `lambda` -0.19 to -0.21) | abs diff **1.8e-12**; propagated into the h=66 path sum, **1.4e-11 to 1.7e-11** relative |
| M3 | Simpson at `eta = 2.05, lambda = -0.9` and `eta = 2.10, lambda = 0` | **wrong by 0.228 and 0.0245** — at `lambda = 0` it returns 0.4755 where the answer is exactly 1/2 |
| M4 | `E[z^2]` from the same closed-form machinery, all 14 points | **1.000000000000** |
| M5 | Mirror identity `kappa(eta, lambda) + kappa(eta, -lambda) - 1`, 30 points over the full bounds | max **1.2e-13** (at `eta = 300`); 0 to 2e-16 for `eta <= 6` |
| M6 | `kappa(eta, 0) - 1/2` | 0 at `eta` = 3, 6; **1.3e-15** at 30; **7.5e-15** at 300 |
| M7 | Cost of `kappa`: closed form vs Simpson | **14-16 us** vs **11-24 ms** (~1000x) |
| M8 | Our iteration vs **arch's analytic** path sum, GARCH(1,1), 4 SPY origins x h in {1,5,22,66} | max **3.0e-15** relative |
| M9 | Our iteration at `phi = alpha + gamma/2 + beta` vs **arch's analytic GJR** path sum, same grid | max **3.3e-15** relative — arch's analytic GJR *is* the `kappa = 1/2` recursion |
| M10 | Exact vs arch's analytic GJR on SPY | exact/analytic = **1.033-1.039** (h=5), **1.17-1.21** (h=22), **1.61-1.81** (h=66) |
| M11 | Geometric closed form vs iteration where `alpha + beta` is 1 within an ulp | geometric is **6.0e-2 / 2.5e-1 / 5.1e-1** relative *wrong* at h = 5 / 22 / 66 |
| M12 | Iteration vs an 80-bit `longdouble` reference, same grid | **0.0** at every point |
| M13 | S=1e5 simulation cost per origin on SPY | **32-36 ms** (h=1), **176-199 ms** (h=5), **892-978 ms** (h=22), **3.2-4.0 s** (h=66) |
| M14 | `f_sim / f_exact` at the four probed SPY origins | h=5 **0.99930-1.00068**; h=22 **0.99521-1.00012**; h=66 **0.99629-1.04125** |
| M15 | h=1 simulated vs exact, bit for bit | **equal at one origin, 1 ulp apart (2.2e-16) at another** |
| M16 | Per-contestant origin counts in `data/trials.jsonl`, **all three symbols** (SPY, QQQ, IWM) x all four horizons x both losses | **251 / 250 / 247 / 238** at h = 1 / 5 / 22 / 66, **identical for every contestant of every symbol**, `skipped = 0` and `dropped = 0` in all 24 configurations, zero disagreements |
| M17 | The suite's existing `_returns()` fixture, fitted | `lambda = -0.0419`, `kappa = 0.51302`, `phi_true = 0.93561` vs `phi_half = 0.93501` — a **6e-4** gap |
| M18 | A skew-t toy fixture (below), fitted | `lambda = -0.5322`, `kappa = 0.62948`, `phi_true = 0.96502` vs `phi_half = 0.94363` — a **0.0214** gap |
| M19 | `scipy.special.stdtr` under `warnings.simplefilter("error")`, `df` in {0.05 … 300}, `x` in {+-1e8, +-5, 0, +-inf} | clean, finite everywhere |
| M20 | `mypy --strict` on `float(stdtr(df, x))` and `assert_never` over a 3-member `Literal` | passes |

Three of these contradict a premise and are called out in §8.

---

## The quantity

From R117, with `A_k = beta + (alpha + gamma*1{z_k < 0}) z_k^2` independent of `sigma2_{t+k}`:

```
sigma2_{t+k+1} = omega + sigma2_{t+k} * A_{t+k}
  =>  m_{k+1} = omega + phi * m_k ,   phi = alpha*E[z^2] + gamma*kappa + beta
```

`E[z^2] = 1` by arch's standardization, so

```
phi = alpha + gamma*kappa + beta ,      kappa = E[z^2 1{z<0}]
m_1 = sigma2_{t+1}                       (known at the origin; no expectation is taken)
f_h(t) = ( sum_{k=1..h} m_k ) / SCALE**2
```

`m_1` is **not** an expectation and must not be computed as one. The aggregation, the single
`/ SCALE**2` after the sum, and `mean="Zero"` making `.variance` the uncentred second moment are
unchanged from `garch.py`'s module docstring (R88).

Existence needs only `E[z^2] < inf`, i.e. `eta > 2`, which arch's bounds `eta in [2.05, 300]`
guarantee at every origin at every finite h. `phi < 1` governs boundedness as `h -> inf`, not
existence — and on SPY `phi > 1` at 238/238 origins, which is the result being reported, not a
bug to be guarded against.

---

## Decision 1 — how the method is selected

**Decision.** `GarchModel.method` becomes
`Method = Literal["analytic", "simulation", "exact"]`, the three arms are dispatched in
`_Fitted.forecast` with a `typing.assert_never` fallthrough, and the legal
`(volatility, o, method)` combinations are refused at construction in `__post_init__`.

```python
Method = Literal["analytic", "simulation", "exact"]

@dataclass(frozen=True)
class GarchModel:
    name: str
    volatility: Literal["GARCH", "EGARCH"]
    o: int
    method: Method
    simulations: int = SIMULATIONS
    maxiter: int | None = None

    def __post_init__(self) -> None:
        """The method and the variance process must agree, refused here rather than at forecast.

        R90 was a comment plus an AST test counting a string literal. It is a property of the
        pairing and belongs where the pairing is made.
        """
        # "exact" is GJR's recursion: it needs the leverage term, and for o == 0 "analytic" is
        # already the true mean (R118).
        # "analytic" is arch's recursion, which hard-codes kappa = 1/2 (M9) and is therefore the
        # true mean only for o == 0.
        # EGARCH has no analytic recursion and no finite multi-step mean (R114), so h=1 aside it
        # can only simulate.
```

Exactly four pairings are legal: the three shipped specs `("GARCH", 0, "analytic")`,
`("GARCH", 1, "exact")`, `("EGARCH", 1, "simulation")`, and `("GARCH", 1, "simulation")`, which
must stay legal because `tests/test_compare.py` builds it to prove that `monte_carlo_error` finds
a contestant by its method and not by its name. Everything else raises `ValueError` at
construction.

**Why a `Literal` and not an enum.** `compare/cohorts.py` tests `model.method == "simulation"`.
A plain `Enum` makes that comparison silently `False` and drops EGARCH out of the Monte-Carlo
table with no error anywhere. A `StrEnum` would survive it, but a `Literal` costs no new type,
keeps the AST tests parsing plain string constants, and gives the same exhaustiveness through
`assert_never` — which `mypy --strict` in the gate enforces (M20). A fourth method added to
`Method` without a branch is then a **gate failure**, not a silent fall into the analytic path.

**The seam in `_Fitted`.** One arch-calling helper, three arms, one place that divides by
`SCALE**2`:

```python
def _from_arch(
    self, horizon: int, options: dict[str, Any]
) -> tuple[float, list[warnings.WarningMessage]]:
    """``sum`` of arch's ``.variance`` over steps 1..horizon, with warnings recorded not raised.

    ``skipna=False``: pandas would otherwise sum a NaN step as zero and answer too low.
    """

def _exact(self, horizon: int) -> tuple[float, list[warnings.WarningMessage]]:
    """GJR's exact path sum: arch for step one, this module's recursion for steps 2..horizon."""

def _simulated(self, horizon: int) -> tuple[float, list[warnings.WarningMessage]]: ...

def forecast(self, horizon: int) -> Forecast:
    if horizon < 1:
        raise ValueError(f"horizon must be at least 1, got {horizon}")
    if self.spec.method == "exact":
        total, caught = self._exact(horizon)
    elif self.spec.method == "simulation":
        total, caught = self._simulated(horizon)
    elif self.spec.method == "analytic":
        total, caught = self._from_arch(horizon, {"method": self.spec.method})
    else:
        assert_never(self.spec.method)
    # ... unchanged from here: re-raise non-RuntimeWarnings, / SCALE**2, refuse non-finite
```

**Where `m_1` comes from: arch, at `horizon=1`, with no `method` passed.**
`_exact` calls `self._from_arch(1, {})`. Step one takes no expectation, so every method agrees on
it; arch's own backcast and recursion initialization are reused rather than re-derived, which is
the part a hand-rolled `m_1` would get subtly wrong. Measured: at h=1 the simulated and exact
values are equal at one SPY origin and 1 ulp apart at another (M15) — the only difference is the
rounding of arch's mean over 100,000 identical paths.

**The cost of relying on arch's default `method="analytic"`**, which that call does. Paid with two
witnesses rather than a comment: a behavioural test that the one-step forecast is
method-invariant, and one line asserting
`inspect.signature(ARCHModelResult.forecast).parameters["method"].default == "analytic"`, so an
arch upgrade that changes the default fails by name instead of by a last-digit drift in the
ledger.

### What `test_the_analytic_method_is_used_only_for_the_symmetric_specification` becomes

It is **deleted** and replaced by three checks. The literal count cannot survive: the `elif` arm
above spells `"analytic"` a second time, and dodging that (ordering the arms so analytic is a bare
`else`) would buy the test's survival by giving up `assert_never` — the wrong trade, since a
fourth method silently taking arch's analytic path is precisely R90's failure mode.

| Replacement | Form | What it asserts |
|---|---|---|
| `test_no_asymmetric_specification_asks_arch_for_a_multistep_analytic_forecast` | behavioural, `_FakeResult` records the calls | for every spec with `o != 0` and every h in (1, 5, 22, 66), every recorded arch call satisfies `call["horizon"] == 1 or call.get("method") == "simulation"` |
| `test_the_method_and_the_variance_process_must_agree_at_construction` | red cases on `__post_init__` | `("GARCH", 1, "analytic")`, `("EGARCH", 1, "exact")`, `("GARCH", 0, "exact")` all raise; the three shipped specs construct |
| `test_the_method_literal_names_exactly_the_three_supported_methods` | `typing.get_args(Method)` | the Literal is `("analytic", "simulation", "exact")`, so `assert_never`'s exhaustiveness has a named membership witness |

Two further tests in `tests/test_garch.py` break and must change with them:

- **`test_the_three_specs_are_exactly_what_r91_names`** asserts
  `[("GARCH", 0, "analytic"), ("GARCH", 1, "simulation"), ("EGARCH", 1, "simulation")]`. The middle
  entry becomes `("GARCH", 1, "exact")`. This test **is** R91's witness, so editing it amends a
  recorded decision rather than fixing a test: R91's "GJR and EGARCH are simulated" becomes "GJR is
  exact and EGARCH is simulated", and the decisions log needs that line.
- **`test_the_simulated_specs_ask_for_simulation_with_the_stated_count`** reads `call["method"]`
  for every spec. The exact branch passes **no** `method` to arch, so for GJR that is a `KeyError`,
  not an assertion failure. It becomes the per-spec table in the first row above:
  `method == "analytic"` and `horizon == h` for GARCH(1,1); `"method" not in call` and
  `horizon == 1` for GJR; `method == "simulation"`, `horizon == h`, `simulations == 100_000` and a
  callable `rng` for EGARCH. Keep its existing assertion that `random_state` is never passed.

And one probe, which the task's constraints did not mention and which `garch.py`'s own docstring
cites:

- **`tools/measure_garch_forecast_error.py`** builds `dataclasses.replace(fitted.spec,
  method="analytic")` for *every* specification, including GJR, to produce R90's headline figure.
  `__post_init__` makes that construction raise `ValueError` — which the probe's existing
  `except ValueError` would swallow into "analytic unavailable", silently deleting the measurement
  R90 rests on. `block_analytic_against_simulation` must instead reach the analytic path
  **without** mislabelling the spec, by calling `_path_sum(omega, alpha + gamma/2 + beta, m_1, h)`
  for arch's recursion and `_path_sum(omega, phi, m_1, h)` for the exact one. That is the same
  comparison W2 asserts, and it is where the §"what was measured" debt above is discharged.

This is strictly stronger than what it replaces. The literal count checked *syntax* and would have
passed a spec whose method string was assembled at runtime; the first replacement checks the
*behaviour* and covers EGARCH, which the literal count never did.

---

## Decision 2 — where `kappa` comes from, and how

**Decision. A closed form, exact up to the Student-t CDF. No quadrature in production.**

Hansen's skew-t as arch implements it is a **two-piece scaled unit-variance Student t**, which is
what makes the closed form available. With

```
c = Gamma((eta+1)/2) / ( sqrt(pi*(eta-2)) * Gamma(eta/2) )
a = 4*lambda*c*(eta-2)/(eta-1)
b = sqrt(1 + 3*lambda**2 - a**2)
```

the substitution `y = b*z + a` gives

```
g(y) = s( y/(1-lambda) )   for y < 0
g(y) = s( y/(1+lambda) )   for y >= 0
s(w) = c * (1 + w**2/(eta-2))**(-(eta+1)/2)      the unit-variance t(eta) density
```

(Mass check: `(1-lambda)/2 + (1+lambda)/2 = 1`.) The truncated moments of `s` are elementary:

```
S0(x) = T_eta( x * sqrt(eta/(eta-2)) )
S1(x) = -c * ((eta-2)/(eta-1)) * (1 + x**2/(eta-2))**(-(eta-1)/2)
S2(x) = (eta-2) * ( ((eta-1)/(eta-2)) * T_{eta-2}(x) - S0(x) )
```

where `T_nu` is the Student-t CDF with `nu` degrees of freedom. The constant `(eta-1)/(eta-2)` in
`S2` is the ratio `c / K'` to the `t_{eta-2}` normalizer, and it is **exact**, not fitted:
`Gamma((eta+1)/2)*Gamma((eta-2)/2) / (Gamma(eta/2)*Gamma((eta-1)/2)) = (eta-1)/(eta-2)`. The
identity `S2(inf) = (eta-2)*((eta-1)/(eta-2) - 1) = 1` is the unit-variance check and is the
reason M4 reads exactly 1.

Then, with `z < 0  <=>  y < a` and `sign(a) = sign(lambda)`:

```
J(d; y0, y1) = ( d**3 * (S2(y1/d) - S2(y0/d))
              - 2*a * d**2 * (S1(y1/d) - S1(y0/d))
              +   a**2 * d * (S0(y1/d) - S0(y0/d)) ) / b**2

kappa = J(1-lambda; -inf, min(a, 0))  +  ( J(1+lambda; 0, a)  if a > 0 else 0 )
```

with `S0(-inf) = S1(-inf) = S2(-inf) = 0`, and **a branch whose scale `d` is zero contributes
zero** — its mass is `d/2`. That case is reachable: arch's bounds are the *closed* interval
`lambda in [-1, 1]`, and at `lambda = +-1` one scale is exactly 0 and `y/d` would be a NaN. The
guard is one `if`, and it needs a test.

**Why closed form over quadrature, which was the expected answer.** Three reasons, in order of
weight:

1. **The quadrature is wrong where arch allows the fit to go.** Simpson over `z = sinh(u)`
   truncated at `|u| <= 30` is accurate to 5.6e-12 for `eta >= 4`, but at `eta = 2.10, lambda = 0`
   it returns `kappa = 0.4755` where the mirror symmetry forces exactly 1/2, and at
   `eta = 2.05, lambda = -0.9` it is off by 0.228 (M3). The `z^2` tail is too heavy to truncate.
   `eta` is bounded below by 2.05 and SPY fits land at 5.0-7.0, so this is not a live failure
   today — but it is a silent one, dependent on data, in a number that multiplies `h` times.
2. **Exact beats 1000x cheaper, and it is also 1000x cheaper** (M7).
3. The closed form carries its own checkable identities (M4, M5, M6) that need no second
   implementation.

**What the closed form costs.** A direct `scipy.special.stdtr` import, and one place where
floating point can lose digits: `S2` multiplies `(eta-2)` by a difference of two CDF values whose
leading terms cancel. The relative loss grows like `eta * eps`, bounded at `eta = 300` by about
3e-14 absolute in `kappa` (visible as M6's 7.5e-15 at `lambda = 0`). That is 12 orders below
anything in this project and sets the test tolerance: identity assertions use `abs=1e-12`, never
exact equality.

**Where the quadrature survives: as a test oracle only**, parametrized over `eta >= 4` where M1
shows it holds, and never below. Writing those parameters down matters — if the oracle were
parametrized to `eta = 2.05` the test would fail and someone would loosen the tolerance, blaming
the closed form for the quadrature's truncation. The full-bounds coverage comes from the
identities instead.

---

## Decision 3 — which module it lives in

**Decision. `src/volab/models/garch.py`. No new module.** Three private functions:

```python
def _kappa(eta: float, lam: float) -> float:
    """``E[z**2 1{z<0}]`` for arch's standardized Hansen skew-t, in closed form (R118's kappa)."""

def _persistence(params: pd.Series, *, name: str) -> float:
    """GJR's mean multiplier ``phi = alpha + gamma*kappa + beta``, read from a fitted result."""

def _path_sum(omega: float, phi: float, first: float, horizon: int) -> float:
    """``sum_{k=1..h} m_k`` for ``m_1 = first``, ``m_{k+1} = omega + phi*m_k``, by iteration."""
```

**Rejected: a new `src/volab/models/skewt.py`.** It reads better — the skew-t's moments are not
GARCH's business, and the identity tests want a target with no arch and no pandas in scope. Three
things rule it out:

1. **It opens a new seal surface.** `tests/test_import_discipline.py`'s numeric ban is
   **import-name based and therefore not transitive**: it checks that `models/base.py` names no
   numpy or scipy import, not that nothing it imports does. `garch.py` already imports numpy and
   is already the module `base.py` must not import (R85); putting `kappa` there creates no new way
   for the sealed path to reach scipy. A `skewt.py` would create exactly one.
2. **`test_only_cohorts_imports_the_model_modules` names `volab.models.garch` and
   `volab.models.recalibrated` as literals.** A third model module is invisible to it the day it
   is written, and `volab.compare`'s "no contestant through a side door" guarantee would quietly
   stop covering the new file.
3. `models/` is one module per contestant family plus the interface. `skewt.py` would be the first
   exception, and `_model_modules()` would scan it as a contestant.

**What must not import `garch.py`** — unchanged, and already stated in `models/base.py`'s
docstring and R85: `models/base.py`, `walkforward/__init__.py`, `panel.py`, `store.py`, anything
under `features/`, and every module under `compare/` except `cohorts.py`.

**One hardening, in scope because it is one line.** The sealed scan's non-transitivity is a
pre-existing gap, not one this change opens, but it is now load-bearing. Add to
`tests/test_import_discipline.py`:

```python
# First-party modules that reach numpy or scipy. A sealed module importing one of these reaches
# them too, and the import-name scan above would not see it.
SEALED_FIRST_PARTY_BAN = {"volab.models.garch", "volab.models.recalibrated", "volab.compare"}

def test_the_point_in_time_path_imports_no_module_that_reaches_numpy(module: Path) -> None: ...
```

---

## Decision 4 — how the parameters are read

**Decision. By label, from a module-level tuple of label constants, through one helper that
raises `ValueError` on a missing label.** Never by position.

```python
# The parameter labels arch gives a Zero-mean GARCH/GJR fit with skew-t errors. Verified against
# a live SPY fit: ``omega, alpha[1], gamma[1], beta[1], eta, lambda`` for o=1, the same without
# ``gamma[1]`` for o=0.
_OMEGA, _ALPHA, _GAMMA, _BETA = "omega", "alpha[1]", "gamma[1]", "beta[1]"
_ETA, _LAMBDA = "eta", "lambda"

def _read(params: pd.Series, label: str, *, name: str) -> float:
    """``params[label]`` as a float, or a ``ValueError`` naming what arch actually returned."""
    if label not in params.index:
        raise ValueError(
            f"{name}: the fit has no {label!r} parameter; arch returned "
            f"{list(params.index)}. Reading by position would have answered a number."
        )
    return float(params[label])
```

**A missing label raises `ValueError`, not `ModelSkip`.** Exactly the distinction
`models/base.py` already draws for a horizon mismatch: `evaluate` files a `ModelSkip` under "this
contestant refused this origin" and a `SchemaError` under "the window was unfittable", so a
wiring bug filed as either would pass as a property of the data. `evaluate` catches neither
`ValueError`, so it aborts the run — which is correct, because every origin would be equally
broken.

**Why label and not the existing `params.to_numpy()[-shape.num_params:]` slice.** That slice is
*safe* where it is used: it takes the last two entries, and arch puts the distribution's shape
parameters last. It stays. The new read is of `omega, alpha[1], gamma[1], beta[1]`, which are
positionally first — and `o = 0` has no `gamma[1]` at all, so a positional read of the leverage
term on a symmetric fit would silently return `beta`. `__post_init__` (Decision 1) already makes
that unreachable by refusing `("GARCH", 0, "exact")`, so the label read and the construction guard
are two independent defences against the same mistake.

**The trap this closes that labels alone do not.** EGARCH's parameters carry the **same labels**
`omega, alpha[1], gamma[1], beta[1]` with entirely different meanings — `beta` is a log-variance
AR coefficient, not a variance weight. Reading by label would succeed and produce a confident
wrong number. Only `__post_init__`'s refusal of `("EGARCH", *, "exact")` catches that, which is
why Decision 1 is a construction-time check and not a comment.

**One consequence for the test suite.** `tests/test_garch.py::_FakeResult.params` is
`pd.Series([0.05, 0.05, 0.90, 8.0, -0.3])` with a default `RangeIndex`. It must become a labelled
six-entry Series for the exact path to read it:

```python
params = pd.Series(
    [0.05, 0.05, 0.05, 0.90, 8.0, -0.3],
    index=["omega", "alpha[1]", "gamma[1]", "beta[1]", "eta", "lambda"],
)
```

`eta, lambda` stay last, so the simulated path's positional `[-shape.num_params:]` read and
`test_the_generator_handed_to_arch_is_seeded_from_the_forecast_horizon`'s `[-2:]` keep working.

---

## Decision 5 — numerics: iterate, do not use the geometric sum

**Decision. Iterate the recursion, at most 66 scalar steps. The closed-form geometric sum is
rejected outright.**

This is the one place the measurement changed the answer. The geometric form is

```
S_h = m_1 * G + omega/(1-phi) * (h - G),    G = (1 - phi**h)/(1 - phi)
```

with a branch at `phi == 1`. On SPY's GARCH(1,1) fits it is **wrong by 6.0%, 25.2% and 51.0%** at
h = 5, 22, 66 (M11) — and the `phi == 1.0` branch **does not fire**, because `alpha + beta` comes
out as `1 - O(eps)` and not as exactly 1.0. `1 - phi` is then one or two ulps, `1 - phi**h` is
quantized to the same granularity, and `G` is a ratio of two numbers that have each lost all but a
digit or two. This is not a corner case: R118 records `alpha + beta` pinned to 1 within 1e-9 at
192/250 SPY origins, so **the broken branch is the modal case**.

Iteration, by contrast, reproduces an 80-bit `longdouble` evaluation of the same recursion to
**0.0** relative at every origin and horizon measured (M12), and arch's own analytic path to 3e-15
(M8). It costs at most 65 multiply-adds per origin against an arch call of ~30 ms.

A `h <= 66` bound is not assumed: iteration is `O(h)` and correct for any `h >= 1`, which is also
why `_path_sum` takes `horizon` rather than reading a module constant.

---

## Decision 6 — validation

Five independent witnesses, each aimed at one thing the others cannot see. The division matters:
the recursion machinery and the parameter wiring are validated **without** `kappa`, `kappa` is
validated **without** the recursion, and only the end-to-end agreement test needs both.

### W1 — the symmetric cross-check: two code paths, one answer, no `kappa`

For `o = 0` the leverage term is absent, so `phi = alpha*E[z^2] + beta = alpha + beta` with no
`kappa` in it, and arch's analytic recursion is then the true mean (R118). So `_path_sum(omega, alpha + beta, m_1, h)` must
reproduce `result.forecast(horizon=h, method="analytic").variance.iloc[-1].sum()`. Measured at
**3.0e-15** relative worst case over 4 SPY origins x 4 horizons (M8). In the suite it runs on the
toy fixture, at h in (1, 5, 22, 66), tolerance `rel=1e-11`.

This validates: the recursion, the `omega` / `alpha[1]` / `beta[1]` label reads, the `m_1`
handoff, the summation, and the fact that `m_1` is not an expectation. It cannot validate `gamma`
or `kappa`.

### W2 — the `kappa = 1/2` cross-check: arch's analytic GJR is the symmetric recursion

New, and the strongest finding of the design pass. arch's analytic GJR path sum equals
`_path_sum(omega, alpha + gamma/2 + beta, m_1, h)` to **3.3e-15** relative (M9), because arch
hard-codes `kappa = 1/2` in `GARCH.constraints` *and* in its analytic recursion. So:

```
assert _path_sum(omega, alpha + gamma / 2 + beta, m_1, h) == pytest.approx(
    arch_gjr_analytic_sum, rel=1e-11
)
```

is a second two-path witness, and this one **does** cover the `gamma[1]` label read and the
leverage term, which W1 structurally cannot. It is also an executable witness for R118's root
cause: the claim "arch's feasible region assumes a symmetric error law" stops being a reading of
library source and becomes a test. Run it on the toy fixture at h in (1, 5, 22, 66).

As a by-product it corrects a figure: against the exact value, arch's analytic GJR is
**38-45% low at h=66** on SPY (M10), not the 17.7% `garch.py`'s docstring records — that number
was measured on the probe series against simulation, and it is now superseded.

### W3 — the `kappa` identities, over the full parameter bounds

Four assertions, parametrized over `eta in {2.05, 2.5, 4, 6, 30, 300}` x
`lambda in {-0.999, -0.5, -0.0419, 0, 0.35, 0.999}` and the four corners of the bounds:

| Identity | Tolerance | Why it is independent |
|---|---|---|
| `E[z^2] = 1` from the same `S0/S1/S2` machinery over the whole line | `abs=1e-12` | the normalization `S2(inf) = 1`; fails if the `(eta-1)/(eta-2)` constant is wrong |
| `kappa(eta, 0) = 1/2` | `abs=1e-12`, **not** exact equality (M6 reads 7.5e-15 at `eta = 300`) | a symmetric law's only possible answer |
| `kappa(eta, lambda) + kappa(eta, -lambda) = 1` | `abs=1e-12` (M5 worst case 1.2e-13) | the mirror `z -> -z, lambda -> -lambda`; needs no oracle at all |
| `0 < kappa < 1` | strict | a truncated second moment of a unit-variance law |
| `lambda = +-1` returns a finite number | `math.isfinite` | the degenerate-branch guard; arch's bounds are closed |

Plus `test_the_closed_form_matches_an_independent_quadrature`, **parametrized to `eta >= 4`
only**, `abs=1e-10`. The restriction is deliberate and must be commented: at `eta = 2.1` Simpson
is wrong by 0.0245 (M3), and a wider parametrization would be "fixed" by loosening the tolerance,
which would hide the closed form's real error budget.

**The oracle is written inline in `tests/test_garch.py`, about fifteen lines, and reads its density
from arch rather than from this module** — that independence is the whole value of the test:

```python
def _kappa_by_quadrature(eta: float, lam: float, *, u_max: float = 30.0, n: int = 200_000) -> float:
    """``E[z**2 1{z<0}]`` by composite Simpson over ``z = sinh(u)``, from arch's own density.

    Valid for ``eta >= 4`` only: the ``z**2`` tail is too heavy to truncate below that, and at
    ``eta = 2.1`` this integral answers 0.4755 where the mirror symmetry forces exactly 1/2.
    ``sinh`` turns the polynomial tail into exponential decay; Simpson's ``du**4`` error is then
    far below the 1e-10 asserted. Never ``leggauss`` at large n: an O(n**3) eigensolve that
    allocates 3.2 GB at n=20,000.
    """
    u = np.linspace(-u_max, u_max, n + 1)              # n even
    weights = np.ones(n + 1)
    weights[1:-1:2], weights[2:-1:2] = 4.0, 2.0
    weights *= (u[1] - u[0]) / 3.0
    z = np.sinh(u)
    density = np.exp(
        SkewStudent().loglikelihood([eta, lam], z, np.ones_like(z), individual=True)
    ) * np.cosh(u) * weights
    return float((z[z < 0] ** 2 * density[z < 0]).sum())
```

`np` and `SkewStudent` are already imported in that file. The same helper's `density.sum()` and
`(z * density).sum()` give mass and `E[z]`, which is why no separate quadrature module is needed.

### W4 — agreement with simulation where simulation is trustworthy, and a red case

**The existing `_returns()` fixture cannot carry this test.** It fits to `lambda = -0.0419`, so
`kappa = 0.513` and the entire `kappa` term moves `phi` by 6e-4 (M17): replacing the closed form
with the constant 1/2 would change the h=5 forecast by far less than any usable tolerance. A test
with no power against the bug it exists to catch is worse than no test, and this is the third time
in this project a gentle fixture has flattered an estimator (R102's probe, R119's `top1_share`).

**A second fixture, with real skew.** Generate from a skew-t DGP rather than a symmetric
`standard_t`:

```python
def _skewed_returns(n: int = 500, *, seed: int = 11) -> pd.Series:
    """A leveraged GJR series with genuinely skewed innovations, so ``kappa`` is load-bearing.

    DGP: z ~ SkewStudent(eta=6, lambda=-0.5); sigma2_t = (1-a-g/2-b)*sigma**2
    + (a + g*1{r<0})*r**2 + b*sigma2_{t-1}, a=0.08, g=0.16, b=0.80, sigma=0.01.
    """
```

Measured (M18): it fits to `lambda = -0.5322`, `kappa = 0.62948`, `phi_true = 0.96502` against
`phi_half = 0.94363` — a gap of **0.0214** — and the fit converges cleanly under
`filterwarnings = ["error"]`.

On that fixture, at **h=5** with `S = 100_000`:

| quantity | measured |
|---|---|
| `f_sim / f_exact`, 6 seeds | **0.99766 … 1.00109** (max deviation 0.234%) |
| theoretical relative sd of the mean | 0.139% |
| the `kappa = 1/2` forecast vs exact | **-3.93%** |
| cost of one such forecast | **~0.23 s** |

So the test is:

```
assert f_sim  == pytest.approx(f_exact, rel=0.01)      # agrees: 0.234% observed, 7 sigma of head-room
assert f_half != pytest.approx(f_exact, rel=0.01)      # red case: 3.93% off, 4x the tolerance
```

A 1% band separates the two by a factor of four in both directions. **Derive the tolerance from
this fixture's own theoretical sd, not from SPY's 0.21-0.40%** (R119's figures are a different
process). This is the one test in `test_garch.py` that needs the full simulation count, and the
module docstring's claim that "nothing in this file needs the full simulation count" must be
amended rather than left to rot.

**h=66 must not be tested for disagreement.** The task asked for a witness that at h=66 the
closed form and the simulation do *not* agree. **It cannot honestly be written as a test**, and
the design refuses it: on the toy fixture, h=66 at `S = 100_000` agrees to within 1.6% across six
seeds, because disagreement is a property of SPY's `psi/phi^2` of 1.26-1.79, not of the code.
Writing a `!=` assertion against a gentle fixture would be a test that passes for the wrong reason
and would break on an innocent change. The h=66 evidence is R119's measured SPY sweep
(`f_sim/f_exact` in [0.8178, 1.5434], 238/238 origins over the 1.5% ceiling) and it belongs in the
decisions log, which already holds it, with `tools/measure_garch_forecast_error.py` as the
re-runnable probe. What the suite *can* assert at h=66 is W2 — that the three recursions are
distinguishable there — and the `kappa = 1/2` gap at h=66 on this fixture is **-26.8%**, which is
the sharpest form of that claim.

### W5 — determinism, and the absence of a seed

Three assertions, two of which change meaning:

1. `test_the_same_view_forecasts_the_same_number_twice` keeps GJR and gains strength: the exact
   forecast is deterministic by construction, not by a fixed seed.
2. **`test_a_different_origin_draws_different_paths` must stop covering GJR.** It builds the same
   return series under two origin labels and asserts the forecasts differ, which is true only for
   a seeded simulation. Restrict it to `EGARCH11_SKEWT` and add its converse for GJR:
   `test_the_exact_forecast_reads_no_seed` — same returns, two origin labels, two symbols, and the
   forecast is **bit-identical**. That is the positive statement of "an exact forecast needs no
   seed at all".
3. `test_the_generator_handed_to_arch_is_seeded_from_the_forecast_horizon` moves from
   `GJR11_SKEWT` to `EGARCH11_SKEWT`; the `_seed` key tests are untouched, because `_seed` is
   still live for EGARCH and its ledger-reproducing literal must not move.

Two pieces of prose rot with this change and must be amended, not left:
`tests/test_garch.py`'s module docstring says "nothing in this file needs the full simulation
count" (W4 now does) and that the two simulated specs run the conformance suite "at horizon 22 and
not 1, because at h=1 a simulation that ignored its seed would still pass clauses 5 and 6
vacuously" — which stops being GJR's reason, since GJR reads no seed at any horizon. `_few` also
becomes a no-op for GJR; leave it applied, so the call sites stay uniform, and say why.

### The refusal guard, and `fit_depends_on_horizon`

**The non-finite / warning guard in `_Fitted.forecast` stays, unchanged.** Three reasons:

- It still has something to catch. EGARCH still simulates, and `_SKIP_WARNINGS` plus the
  `RuntimeWarning` overflow path are exercised by planted fakes
  (`_forecaster(EGARCH11_SKEWT, _FakeResult(..., emit=overflow))`), so it is not a guard nobody has
  watched fail. EGARCH is also still a member of `SHOOTOUT`, and `cohorts.py` records that
  **nothing enforces the cohort-to-horizon pairing** — a `SHOOTOUT` run at h=22 would reach the
  overflow path again.
- The exact branch needs the finiteness half. `_from_arch(1, {})`'s `skipna=False` sum can be NaN,
  and `_kappa` would return NaN for a NaN shape parameter. Neither can happen after a converged
  fit, which is exactly why the check must stay: it is the assertion that it cannot.
- Removing it buys four lines and loses a refusal.

What it loses is its *live trigger for GJR*: with no `exp` on a simulated path, GJR's exact branch
has no overflow route, bounded by `phi <= ~1.03` over `h <= 66`. Record that, do not delete the
branch.

**`fit_depends_on_horizon` stays `False`,** and the claim gets stronger. `_exact` reads
`self.result` and `horizon`; the *fit* reads only `returns`, `symbol`, `origin` and
`minimum_training`, as before. The exact branch does not even read `horizon` into a seed, so
conformance clause 6 passes for a more direct reason than it did. No change to
`tests/model_conformance.py`.

---

## Decision 7 — the blast radius on the comparison layer

### `monte_carlo_error()` stops seeing GJR at every horizon

`cohorts.monte_carlo_error` includes a contestant only when `model.method == "simulation"`, so
`method = "exact"` removes GJR from the map at **every** horizon. Consequences, exactly:

| call | today | after |
|---|---|---|
| `monte_carlo_error(SHOOTOUT, 1)` | `{"gjr11_skewt": None, "egarch11_skewt": None}` | `{"egarch11_skewt": None}` |
| `monte_carlo_error(SHOOTOUT, 22)` | `{"gjr11_skewt": 0.207, "egarch11_skewt": 0.163}` | `{"egarch11_skewt": 0.163}` |
| `monte_carlo_error(MULTISTEP, 5 / 22 / 66)` | `{"gjr11_skewt": None / 0.207 / 0.514}` | **`{}`** — EGARCH is not in `MULTISTEP` (R114) |

In `analyse`, `errors` becomes falsy for the whole multistep cohort, so `monte_carlo_source` is
stored as `None`, `unclaimed` is `()`, and `_error_text` returns `""`. **At h=5, 22 and 66 the
Monte-Carlo apparatus prints nothing at all** — correct, because at those horizons the cohort
contains no simulated contestant. `__main__._report` keys on `words[0] in errors`, which is simply
never true, so the per-trial lines lose their `[MC error …]` suffix. At h=1 EGARCH keeps
`not measured, no claim`.

**And GJR leaves `unclaimed`.** That is the point of the whole exercise: GJR's h=1 and h=5
rankings can carry significance for the first time. Pre-registration §2 still refuses all
significance at h=22 and h=66 on `n_eff` grounds, so nothing changes there.

### The figure table

`("gjr11_skewt", 22): 0.207` and `("gjr11_skewt", 66): 0.514` become **unreachable** — no code path
ever looks them up again. **Decision: delete both.** A dict entry nothing can read is a number a
reader will mistake for live data, and R119 has already established that these two in particular
are optimistic by one to six orders of magnitude.

EGARCH's two entries **stay**. They are unreachable in the *registered* pairing (`MULTISTEP` at
h >= 2 has no EGARCH) but reachable by construction — `monte_carlo_error(SHOOTOUT, 66)` is a legal
call and `tests/test_compare.py` makes it — and their job is to stop a figure being invented for a
path count or horizon nobody measured.

**R119's instruction that "the table must carry the SPY figures" is superseded**, and this design
records why rather than quietly not doing it: for GJR the table no longer has a reader, and for
EGARCH there is no finite mean whose Monte-Carlo error could be measured (R114), so a SPY figure
for it would be the same category error R115 was written to stop. The SPY distribution stays in
R119, as prose, where it is already correct.

**One new invariant, because this table just went half-dead silently.**

```python
def test_every_monte_carlo_figure_is_reachable() -> None:
    """A figure keyed to a contestant that no cohort simulates can never be printed or withheld.

    Without this, retiring a simulated contestant leaves its figures behind as live-looking data.
    """
    simulated = {
        model.name
        for cohort in COHORTS.values()
        for model in cohort.models
        if isinstance(model, GarchModel) and model.method == "simulation"
    }
    assert {name for name, _ in MONTE_CARLO_ERROR_PERCENT} <= simulated
```

It is a partial check: horizon-level reachability cannot be tested until a contestant declares the
horizons it can answer, which `cohorts.py` already names as the durable fix for R114 and which
stays out of scope here.

### Tests in `compare/` that must change

| test | change |
|---|---|
| `test_the_monte_carlo_error_is_the_recorded_figure_for_each_simulated_contestant` | GJR disappears from every expected dict; `"gjr11_skewt" not in …` joins `"garch11_skewt" not in …`; the `3.273 > CEILING > 0.514` anchor loses its lower term and becomes `3.273 > CEILING > 0.163` |
| `test_a_simulated_contestant_is_found_by_its_method_and_its_figure_is_not_invented` | `dataclasses.replace(GJR11_SKEWT, name="my_gjr")` becomes `dataclasses.replace(GJR11_SKEWT, method="simulation", name="my_gjr")`, which `__post_init__` must keep legal; the test's claim — found by method, not by name — is preserved exactly |
| `test_only_cohorts_imports_the_model_modules` | unchanged, given Decision 3's no-new-module ruling |

### Flagged, not designed: EGARCH at h=1

At h=1 `sigma2_{t+1}` is known at the origin, so all 100,000 EGARCH paths are identical and the
simulation is **exact but wasteful** — ~32 ms per origin (M13) to reproduce a number arch already
has, to within a last-ulp rounding of the mean (M15). The cost is not the time. It is that EGARCH
stays inside R115's withholding rule, so its h=1 result — second best on MSE (R114) — is printed as
`not measured, no claim` against a forecast whose Monte-Carlo error is identically zero.

Making it exact would hand EGARCH a significance claim at h=1, which is a change to what the
leaderboard asserts and therefore the user's call, not a builder's. It is also **coupled**: an
`"exact"` EGARCH would have to *refuse* h >= 2, since the quantity does not exist there, and a
contestant that refuses horizons is the design change `cohorts.py` already identifies as R114's
durable fix. Flagged here; not designed; not in this story.

---

## Decision 8 — what must be re-run, and what must not

### Scope: GJR's rows, at all four horizons

`Evaluation.comparable()` intersects *defined-origin* sets. Measured in the live ledger across
**every symbol**, not SPY standing in for the others (M16): on SPY, QQQ and IWM alike, every
contestant has 251 / 250 / 247 / 238 origins at h = 1 / 5 / 22 / 66, with `skipped = 0` and
`dropped = 0` in all 24 symbol-horizon-loss configurations and no disagreement anywhere. **GJR has
never skipped a single origin on any symbol**, so it never constrained the intersection; the counts
are set by target availability, and switching GJR's forecast from simulated to exact **cannot move
any other contestant's sample on any symbol**. The exact branch is also strictly less likely to
refuse than the simulated one — it has no overflow route — so it cannot shrink GJR's own set
either.

This gives the re-run its acceptance check, not merely its scope: **every non-GJR
`(symbol, horizon, loss, model)` value must come out bit-identical to the existing line**. That is
R121's 38-of-38 overlap test applied once more, and it is the only thing that proves the claim
rather than asserting it.

**h=1 is in scope, which contradicts the obvious assumption.** M15: the h=1 exact and simulated
values are equal at one SPY origin and 1 ulp apart at another, because arch's mean over 100,000
identical paths rounds differently from reading step one. GJR's h=1 MSE (3.51972e-07) will move in
its last digits. All four horizons, both losses, every symbol GJR is scored on.

### `comparisons.jsonl` is **not** "only GJR's rows"

The task's premise holds for `trials.jsonl` and fails for `comparisons.jsonl`. The MCS and SPA are
**joint over contestants**: GJR's loss column enters every elimination step's statistic and every
SPA alternative set, which is precisely R116's mechanism for why EGARCH at h=5 floored four other
contestants' p-values. So after the re-run:

- every contestant's **MCS membership and p-value** *may* change, at every horizon, and none of
  them can be assumed unchanged. At h=1 the input difference is a 1-ulp shift at some origins, so
  the bootstrap exceedance counts will very likely come out identical; at h=22 and h=66 the shift
  is percent-scale and they will not. The *procedure* does not depend on which: every comparison
  record is re-made either way;
- both **SPA** runs likewise, at every horizon;
- **pairwise DMs not involving GJR are unaffected**, because `pairwise()` runs before
  `model_confidence_set()` and is genuinely pairwise.

Every comparison record must be re-made and the old ones are **superseded, not supplemented**.

### Telling the new GJR lines from the old — the user's call

R121 established that a sha alone misleads: a ledger line's sha is the tree when that
configuration *started*, and the re-run appended under a dirty tree. Three options, with prices:

1. **Boundary sha plus a required clean tree.** Commit the change, run with `git_dirty: false`, and
   record in the decisions log that GJR lines at or after sha `X` are exact. *Price:* the JSONL is
   not self-describing; a reader needs the log. *Cures* R121's failure mode by requiring the clean
   tree.
2. **Rename the contestant** to `gjr11_skewt_exact`. *Price:* conflicts with the pre-registered
   name and with the six-contestant multistep roster; `Evaluation.comparable()` would treat it as a
   new contestant — which, arguably, it is.
3. **Add a `method` field to `Trial`.** *Price:* a schema change to an append-only ledger; every
   existing line lacks it, and a reader must read "absent" as "the pre-change method".

**Recommendation: (1).** It needs no schema change, no rename, and no exception to the
pre-registration, and R121's specific defect was the dirty tree, which (1) forbids. But only (3)
makes the ledger self-describing, and that is a judgement about what a future reader is owed.
**I cannot settle this and have not.**

---

## What this design deliberately leaves to the builder

- The exact wording of docstrings and refusal messages, except where a message is quoted above.
- The internal shape of `_kappa` — whether `S0/S1/S2` is one helper returning a tuple or three
  functions, and whether the two branches share a closure. The formulas, the degenerate-`d` guard
  and the tolerances are fixed; the factoring is not.
- Which `(eta, lambda)` points beyond the ones named in W3 to parametrize.
- Whether `_skewed_returns` is a new helper or a parameter on the existing `_returns`.
- The order of the story's commits. Suggested cut, because the pieces are independently
  verifiable: (a) `_kappa` plus W3; (b) `_path_sum`, `_persistence`, `_read` plus W1 and W2;
  (c) the `Method` literal, `__post_init__`, the `_Fitted` dispatch, and Decision 1's three
  replacement tests plus W4 and W5; (d) `cohorts.py` and the comparison-layer tests. (a) and (b)
  touch no caller and cannot change a ledger line.

## Open questions only the user can settle

1. **Ledger provenance** (Decision 8): option 1, 2 or 3. Blocks the re-run, not the build.
2. **EGARCH at h=1** (Decision 7): leave it simulating and withheld, or make it exact and let its
   h=1 result carry significance. Coupled to a contestant declaring its horizons.
3. **Does the re-run happen before registration or after?** The design holds either way, but R120
   says the estimator question blocks registration, and the shootout currently appending to
   `data/trials.jsonl` must finish first regardless.

## Premises this design contradicts

Stated plainly, because three of the task's premises did not survive measurement; the full list is
in the hand-back report.

1. **The verified Simpson quadrature is not verified across arch's `eta` bounds** (M3). It is a
   sound test oracle for `eta >= 4` and wrong below `eta ~ 2.5`. This is why Decision 2 chose a
   closed form, and why W3 parametrizes the oracle rather than the identities.
2. **The geometric form's `phi == 1` branch does not protect it** (M11): `phi` arrives as
   `1 - O(eps)`, so the branch never fires and the error reaches 51% at h=66 in the modal case.
3. **"Only GJR's rows change" is false for `comparisons.jsonl`** (Decision 8) and **h=1 is not
   unchanged** (M15).
4. `garch.py`'s "17.7% low at h=66" for arch's analytic GJR understates it: against the exact
   value it is 38-45% low on SPY (M10). Not a contradiction — the 17.7% was the probe series
   against simulation — but the docstring now has a better number available.
