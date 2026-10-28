"""The GARCH family as contestants: three specifications, one aggregation, one refusal (US-3.2).

**What needs the full simulation count: one test.** The shipped default is ``simulations=100_000``
(R90) and ``test_the_shipped_simulation_count_is_one_hundred_thousand`` pins it as a number, and
``test_w4_exact_agrees_with_simulation_where_simulation_is_trustworthy`` is the one test that
runs it, because its claim is a 1% agreement that 300 paths could not carry. Every other test
that fits and forecasts builds its model with a few hundred paths, because none of the properties
under test depend on the count: they are about what is passed to ``arch`` (a fake result records
it), about determinism (any count is deterministic under a fixed seed), and about the aggregation
(linear whatever the paths). The expensive claims -- how far the analytic, exact and simulated
forecasts sit apart on a persistent leveraged series -- are measurements and not tests:
``tools/measure_garch_forecast_error.py`` (R32 applied to numbers).

The exact forecast (R117-R123, ``docs/superpowers/specs/2026-10-28-gjr-exact-forecast-design.md``)
is validated by five witnesses that each see something the others cannot: W1 and W2 check the
recursion and the parameter wiring against ``arch`` with no ``kappa`` in play; W3 checks ``kappa``
by identities with no recursion in play; W4 checks them together against simulation on a series
with real skew; W5 checks that an exact forecast reads no seed.

The shared conformance suite is run once per specification. EGARCH, the one remaining simulation,
is run at horizon 22 and not 1: at h=1 the step-one variance is deterministic, so a simulation
that ignored its seed would still pass clauses 5 and 6 vacuously. GJR is exact and reads no seed
at any horizon; it runs at 22 so the recursion, not only step one, is what is conformed.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import importlib.util
import inspect
import math
import re
import warnings
import zlib
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, get_args

import numpy as np
import pandas as pd
import pytest
from arch.univariate.base import ARCHModelResult
from arch.univariate.distribution import SkewStudent
from arch.utility.exceptions import ConvergenceWarning, DataScaleWarning, StartingValueWarning
from model_conformance import alone, conformance_suite, make_toy, run_cohort

import volab.models.garch as garch
from volab.errors import ModelSkip
from volab.models.base import Forecaster, Model, TrainingView
from volab.models.garch import (
    EGARCH11_SKEWT,
    GARCH11_SKEWT,
    GARCH_MODELS,
    GJR11_SKEWT,
    GarchModel,
    Method,
)

SOURCE = Path(garch.__file__)
FEW = 300  # simulated paths: enough to be a simulation, few enough to be fast


def _returns(n: int = 500, *, seed: int = 7, sigma: float = 0.01, drift: float = 0.0) -> pd.Series:
    """A persistent, leveraged daily log-return series, indexed by ``datetime.date``."""
    rng = np.random.default_rng(seed)
    variance = np.empty(n)
    out = np.empty(n)
    variance[0] = sigma**2
    for t in range(n):
        if t:
            shock = out[t - 1] - drift
            variance[t] = (
                0.05 * sigma**2 + (0.05 + 0.10 * (shock < 0)) * shock**2 + 0.85 * variance[t - 1]
            )
        out[t] = drift + math.sqrt(variance[t]) * rng.standard_t(8) * math.sqrt(6 / 8)
    days = [stamp.date() for stamp in pd.bdate_range("2020-01-02", periods=n)]
    return pd.Series(out, index=days)


def _view(
    returns: pd.Series, *, minimum_training: int = 100, symbol: str = "SPY", horizon: int = 22
) -> TrainingView:
    """A view carrying what a GARCH fit reads and nothing it does not."""
    return TrainingView(
        symbol=symbol,
        origin=returns.index[-1],
        horizon=horizon,
        response=f"target_rv_proxy_h{horizon}",
        training=pd.DataFrame(),
        row=pd.Series(dtype="float64"),
        returns=returns,
        minimum_training=minimum_training,
        stride=1,
    )


def _few(spec: GarchModel) -> GarchModel:
    """Fewer paths for a spec that simulates. A no-op for GJR, which is exact and simulates none;
    left applied to it anyway so every call site reads the same."""
    return dataclasses.replace(spec, simulations=FEW)


class _FakeResult:
    """Stands in for ``ARCHModelResult``: records how it was asked, answers a given variance."""

    # Labelled as arch labels a GJR fit, because the exact path reads by label. The last two
    # entries are the skew-t shape ``(eta, lambda)``, as in a real result, so the simulated path's
    # positional ``[-shape.num_params:]`` read still lands on them.
    params = pd.Series(
        [0.05, 0.05, 0.05, 0.90, 8.0, -0.3],
        index=["omega", "alpha[1]", "gamma[1]", "beta[1]", "eta", "lambda"],
    )

    def __init__(self, variance: list[float], emit: Callable[[], None] = lambda: None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._variance = variance
        self._emit = emit

    def forecast(self, horizon: int, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"horizon": horizon, **kwargs})
        self._emit()
        row = pd.DataFrame([self._variance[:horizon]], columns=[f"h.{i}" for i in range(horizon)])
        return SimpleNamespace(variance=row)


def _forecaster(spec: GarchModel, result: object, *, observations: int = 10) -> Forecaster:
    return garch._Fitted(spec, result, observations, "SPY", dt.date(2024, 4, 12))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The three specifications (R91).
# ---------------------------------------------------------------------------


def test_the_three_specs_are_exactly_what_r91_names() -> None:
    """Witness for R91's table: names, volatility process, forecast method, and the count.

    Amended from ``("GARCH", 1, "simulation")`` for GJR: R91's "GJR and EGARCH are simulated"
    became "GJR is exact and EGARCH is simulated" (R117-R123).
    """
    assert [spec.name for spec in GARCH_MODELS] == [
        "garch11_skewt",
        "gjr11_skewt",
        "egarch11_skewt",
    ]
    assert GARCH_MODELS == (GARCH11_SKEWT, GJR11_SKEWT, EGARCH11_SKEWT)
    assert [(s.volatility, s.o, s.method) for s in GARCH_MODELS] == [
        ("GARCH", 0, "analytic"),
        ("GARCH", 1, "exact"),
        ("EGARCH", 1, "simulation"),
    ]


@pytest.mark.parametrize("spec", GARCH_MODELS, ids=lambda spec: spec.name)
def test_each_spec_fits_zero_mean_skew_t_of_the_stated_process(spec: GarchModel) -> None:
    """Read off the fitted ``arch`` model rather than off our own fields."""
    fitted = _few(spec).fit(_view(_returns()))
    model = fitted.result.model  # type: ignore[attr-defined]
    assert type(model.volatility).__name__ == spec.volatility
    assert (model.volatility.p, model.volatility.o, model.volatility.q) == (1, spec.o, 1)
    assert model.distribution.name == "Standardized Skew Student's t"
    assert model.name == "Zero Mean"


def test_the_shipped_simulation_count_is_one_hundred_thousand() -> None:
    """R90: ``S=10,000`` was measured at up to 1.4% uncertain, worse than the leak R72 caught.

    EGARCH is the only shipped spec that simulates; GJR's field is the default and unread.
    """
    assert garch.SIMULATIONS == EGARCH11_SKEWT.simulations == 100_000


def test_the_models_satisfy_the_protocol_and_claim_a_horizon_free_fit() -> None:
    for spec in GARCH_MODELS:
        assert isinstance(spec, Model)
        assert spec.fit_depends_on_horizon is False


# ---------------------------------------------------------------------------
# The aggregation (R88). The return-series-squares-to-the-target witness lives in
# ``test_model_interface.py``, where US-3.1b put it.
# ---------------------------------------------------------------------------


def test_a_constant_variance_series_aggregates_to_h_times_the_step_variance() -> None:
    """The linearity and the ÷1e4 together: ``Σ_{j=1..h} ν / 1e4`` is ``h·ν/1e4``."""
    nu = 2.5  # percent-squared; 2.5e-4 on the variance scale the targets live on
    fitted = _forecaster(GARCH11_SKEWT, _FakeResult([nu] * 66))
    for horizon in (1, 5, 22, 66):
        assert fitted.forecast(horizon).value == pytest.approx(horizon * nu / 1e4, rel=1e-12)


def test_the_unit_conversion_happens_once_after_summing() -> None:
    """A varying step variance, so a per-step ÷1e4 applied twice or not at all cannot match."""
    steps = [1.0, 2.0, 4.0, 8.0]
    fitted = _forecaster(GARCH11_SKEWT, _FakeResult(steps))
    assert fitted.forecast(4).value == pytest.approx(15.0 / 1e4, rel=1e-12)


def test_the_h1_forecast_is_the_first_step_variance_over_1e4() -> None:
    """The composition on a real fit, exactly: nothing but the unit conversion is applied."""
    fitted = GARCH11_SKEWT.fit(_view(_returns()))
    step_one = fitted.result.forecast(horizon=1, reindex=False).variance.iloc[-1, 0]  # type: ignore[attr-defined]
    assert fitted.forecast(1).value == float(step_one) / 1e4


def test_the_forecast_is_within_an_order_of_magnitude_of_the_daily_proxy() -> None:
    """R83's named failure mode: a 1e4 in the wrong direction is off by four orders, not one."""
    returns = _returns(sigma=0.01)
    for spec in (GARCH11_SKEWT, _few(GJR11_SKEWT), _few(EGARCH11_SKEWT)):
        fitted = spec.fit(_view(returns))
        for horizon in (1, 22):
            proxy = float((returns.iloc[-250:] ** 2).mean()) * horizon
            ratio = fitted.forecast(horizon).value / proxy
            assert 0.1 < ratio < 10.0, (spec.name, horizon, ratio)


def test_the_forecast_is_the_uncentred_second_moment_not_the_variance() -> None:
    """``ZeroMean`` is load-bearing: the target is ``Σ r²``, so a drift belongs in the forecast.

    The same series with and without its mean removed. Under ``ConstantMean`` the drift would be
    modelled away and ``.variance`` alone would answer the *centred* quantity, making the two
    forecasts nearly equal; under ``ZeroMean`` the uncentred one is larger by at least half of
    ``mu**2``. (Measured larger by 3-6x that: a zero-mean skew-t absorbs a drift into its
    variance and its skew, which is the "not modelled" cost R88 states.)
    """
    mu = 0.003
    for seed in (1, 2, 3):
        returns = _returns(1000, seed=seed, drift=mu, sigma=0.01)
        uncentred = GARCH11_SKEWT.fit(_view(returns)).forecast(1).value
        centred = GARCH11_SKEWT.fit(_view(returns - returns.mean())).forecast(1).value
        assert uncentred - centred > 0.5 * mu**2, (seed, uncentred, centred)


def test_a_horizon_below_one_is_a_value_error() -> None:
    fitted = GARCH11_SKEWT.fit(_view(_returns()))
    with pytest.raises(ValueError, match="horizon"):
        fitted.forecast(0)


def test_a_fit_answers_every_horizon_and_the_answer_grows_with_the_window() -> None:
    fitted = GARCH11_SKEWT.fit(_view(_returns()))
    values = [fitted.forecast(horizon).value for horizon in (1, 5, 22, 66)]
    assert values == sorted(values) and values[0] > 0
    assert fitted.observations == 500 - 0


def test_a_non_finite_forecast_is_a_recorded_skip_not_a_silent_nan() -> None:
    """R56: an origin is scored or recorded as skipped. A converged fit can still overflow.

    ``pandas`` sums a NaN as zero by default, so a single NaN step would otherwise have shrunk the
    forecast silently; the NaN sits mid-window so only ``skipna=False`` sees it.
    """
    fitted = _forecaster(GARCH11_SKEWT, _FakeResult([1.0, float("nan"), 1.0, 1.0, 1.0]))
    with pytest.raises(ModelSkip, match="finite"):
        fitted.forecast(5)


def test_an_overflow_while_simulating_is_a_skip_and_any_other_warning_propagates() -> None:
    """An explosive EGARCH overflows ``exp`` inside ``arch``; that is the fit's, not a bug here."""

    def overflow() -> None:
        warnings.warn("overflow encountered in exp", RuntimeWarning, stacklevel=2)

    with pytest.raises(ModelSkip, match="overflow"):
        _forecaster(EGARCH11_SKEWT, _FakeResult([1.0] * 5, emit=overflow)).forecast(5)

    def odd() -> None:
        warnings.warn("odd", UserWarning, stacklevel=2)

    with pytest.raises(UserWarning, match="odd"):
        _forecaster(EGARCH11_SKEWT, _FakeResult([1.0] * 5, emit=odd)).forecast(5)


# ---------------------------------------------------------------------------
# Which method, and the seed (R90).
# ---------------------------------------------------------------------------


def _module() -> ast.Module:
    return ast.parse(SOURCE.read_text())


LEGAL = {
    ("GARCH", 0, "analytic"),
    ("GARCH", 1, "exact"),
    ("GARCH", 1, "simulation"),
    ("EGARCH", 1, "simulation"),
}


def test_the_method_literal_names_exactly_the_three_supported_methods() -> None:
    """The membership witness for ``assert_never``'s exhaustiveness in ``_Fitted.forecast``."""
    assert get_args(Method) == ("analytic", "simulation", "exact")


@pytest.mark.parametrize("method", get_args(Method))
@pytest.mark.parametrize("o", [0, 1])
@pytest.mark.parametrize("volatility", ["GARCH", "EGARCH"])
def test_the_method_and_the_variance_process_must_agree_at_construction(
    volatility: Any, o: int, method: Any
) -> None:
    """The full product, so "everything else raises" is what is asserted and not three examples.

    ``("GARCH", 1, "analytic")`` is R90's failure mode (arch's recursion is ``kappa = 1/2``);
    ``("EGARCH", 1, "exact")`` would read EGARCH's parameters by the *same labels* and answer a
    confident wrong number; ``("GARCH", 0, "exact")`` has no ``gamma[1]`` to read.
    """
    if (volatility, o, method) in LEGAL:
        assert GarchModel("x", volatility, o, method).method == method
    else:
        with pytest.raises(ValueError, match="not legal"):
            GarchModel("x", volatility, o, method)


def test_the_three_shipped_specs_construct_and_a_replaced_method_is_checked_again() -> None:
    """``dataclasses.replace`` re-runs ``__post_init__``: the probe cannot mislabel a spec."""
    assert {(s.volatility, s.o, s.method) for s in GARCH_MODELS} <= LEGAL
    assert dataclasses.replace(GJR11_SKEWT, method="simulation").method == "simulation"
    with pytest.raises(ValueError, match="not legal"):
        dataclasses.replace(GJR11_SKEWT, method="analytic")


def test_arch_still_defaults_to_the_analytic_method() -> None:
    """``_exact`` asks arch for step one with no ``method``, relying on this default.

    Step one takes no expectation so every method agrees on it. If an arch upgrade changed the
    default, this fails by name instead of as a last-digit drift in the ledger.
    """
    assert inspect.signature(ARCHModelResult.forecast).parameters["method"].default == "analytic"


@pytest.mark.parametrize("horizon", [1, 5, 22, 66])
def test_no_asymmetric_specification_asks_arch_for_a_multistep_analytic_forecast(
    horizon: int,
) -> None:
    """R90's property, behaviourally: arch's analytic recursion assumes ``kappa = 1/2``.

    Every spec with ``o != 0`` -- including GJR simulated, which the comparison layer builds --
    may ask arch for step one (any method agrees there) or for a simulation, and for nothing else.
    This replaces an AST count of the string literal, which checked syntax, would have passed a
    method string assembled at runtime, and never covered EGARCH.
    """
    asymmetric = [spec for spec in GARCH_MODELS if spec.o != 0]
    asymmetric.append(dataclasses.replace(GJR11_SKEWT, method="simulation"))
    assert {spec.volatility for spec in asymmetric} == {"GARCH", "EGARCH"}
    for spec in asymmetric:
        result = _FakeResult([1.0] * 66)
        _forecaster(spec, result).forecast(horizon)
        assert result.calls, spec.name
        for call in result.calls:
            assert call["horizon"] == 1 or call.get("method") == "simulation", (spec.name, call)


def test_each_spec_asks_arch_for_exactly_what_its_method_needs() -> None:
    """What actually reaches ``arch`` -- recorded by a fake, so no path is simulated here."""
    for spec in GARCH_MODELS:
        result = _FakeResult([1.0] * 22)
        _forecaster(spec, result).forecast(22)
        (call,) = result.calls
        assert call["reindex"] is False
        assert "random_state" not in call, "arch reads random_state only for the bootstrap"
        if spec is GARCH11_SKEWT:
            assert call["method"] == "analytic" and call["horizon"] == 22
            assert "simulations" not in call and "rng" not in call
        elif spec is GJR11_SKEWT:
            # Exact: arch is asked for step one only, and not told a method.
            assert call["horizon"] == 1 and "method" not in call
            assert "simulations" not in call and "rng" not in call
        else:
            assert spec is EGARCH11_SKEWT
            assert call["method"] == "simulation" and call["horizon"] == 22
            assert call["simulations"] == 100_000 and callable(call["rng"])


def test_the_seed_is_derived_without_the_salted_builtin_hash() -> None:
    """AST: no call to the builtin ``hash``; ``zlib.crc32`` is what is called instead."""
    calls = [node for node in ast.walk(_module()) if isinstance(node, ast.Call)]
    assert not [c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "hash"]
    assert any(
        isinstance(c.func, ast.Attribute)
        and c.func.attr == "crc32"
        and isinstance(c.func.value, ast.Name)
        and c.func.value.id == "zlib"
        for c in calls
    )


def test_the_seed_is_the_crc32_of_the_documented_key() -> None:
    """A literal, so a change to the key (and with it every ledger line) fails here by name."""
    seed = garch._seed("garch11_skewt", "SPY", dt.date(2024, 4, 12), 22)
    assert seed == zlib.crc32(b"garch11_skewt|SPY|2024-04-12|22") == 3464873751
    others = {
        garch._seed("gjr11_skewt", "SPY", dt.date(2024, 4, 12), 22),
        garch._seed("garch11_skewt", "QQQ", dt.date(2024, 4, 12), 22),
        garch._seed("garch11_skewt", "SPY", dt.date(2024, 4, 15), 22),
        garch._seed("garch11_skewt", "SPY", dt.date(2024, 4, 12), 5),
    }
    assert seed not in others and len(others) == 4


def test_the_generator_handed_to_arch_is_seeded_from_the_forecast_horizon() -> None:
    """The horizon in the key is the one *asked*, not the view's: the fit is horizon-free.

    ``rng`` and not ``random_state`` -- the design named the latter, and for ``method="simulation"``
    ``arch`` never reads it (only the bootstrap does): the forecast came out different on every
    call. The draws handed over must be exactly the seeded skew-t's.
    """
    result = _FakeResult([1.0] * 22)
    _forecaster(EGARCH11_SKEWT, result).forecast(5)
    seed = garch._seed("egarch11_skewt", "SPY", dt.date(2024, 4, 12), 5)
    shape = SkewStudent(seed=seed)
    expected = shape.simulate(_FakeResult.params.to_numpy()[-2:])(7)
    assert result.calls[0]["rng"](7).tolist() == expected.tolist()


def test_the_same_view_forecasts_the_same_number_twice() -> None:
    """Determinism. EGARCH: any count (the seed fixes the paths, not their number). GJR: exact,
    so deterministic by construction rather than by a fixed seed."""
    view = _view(_returns())
    for spec in (_few(GJR11_SKEWT), _few(EGARCH11_SKEWT)):
        first = spec.fit(view).forecast(22).value
        second = spec.fit(view).forecast(22).value
        assert first == second, spec.name
        fitted = spec.fit(view)
        assert fitted.forecast(22).value == fitted.forecast(22).value


def test_a_different_origin_draws_different_paths() -> None:
    """The seed reaches the draws: same fit window, other origin, other key, other forecast.

    EGARCH only: GJR is exact and draws nothing (see the converse below). Without this the test
    above would pass for a model that ignored its seed *and* its randomness, and the seed is
    exactly what the design's ``random_state`` failed to deliver.
    """
    view = _view(_returns())
    later = dataclasses.replace(view, origin=view.origin + dt.timedelta(days=1))
    spec = _few(EGARCH11_SKEWT)
    assert spec.fit(view).forecast(22).value != spec.fit(later).forecast(22).value


def test_the_exact_forecast_reads_no_seed() -> None:
    """The positive statement of "an exact forecast needs no seed at all".

    The same returns under two origin labels and two symbols -- every field the seed is keyed on
    -- forecast to the bit. A seeded simulation could not do this.
    """
    view = _view(_returns())
    other = dataclasses.replace(
        view, origin=view.origin + dt.timedelta(days=1), symbol="QQQ", horizon=5
    )
    for horizon in (1, 5, 22, 66):
        assert GJR11_SKEWT.fit(view).forecast(horizon) == GJR11_SKEWT.fit(other).forecast(horizon)


# ---------------------------------------------------------------------------
# The scale (R83, R89).
# ---------------------------------------------------------------------------


def _keywords_named(source: str, name: str) -> list[int]:
    """Line numbers of every call keyword spelled ``name`` in ``source``."""
    return [
        node.value.lineno
        for tree_node in ast.walk(ast.parse(source))
        if isinstance(tree_node, ast.Call)
        for node in tree_node.keywords
        if node.arg == name
    ]


def test_the_model_never_passes_rescale() -> None:
    """R89: ``False`` silences the scale check and ``True`` rescales twice."""
    assert _keywords_named(SOURCE.read_text(), "rescale") == []


def test_the_rescale_scan_can_fail() -> None:
    """Its red case: a planted ``rescale=False`` and ``rescale=True`` are both found."""
    planted = "arch_model(y, rescale=False)\nother(rescale=True)\n"
    assert _keywords_named(planted, "rescale") == [1, 2]


def test_a_raw_scale_series_raises_rather_than_fitting_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R83's red case, which R89's default is what keeps alive: drop the ×100 and this fails.

    Not a ``ModelSkip``: a mis-scaled fit is a bug in this module, not a property of the data.
    """
    monkeypatch.setattr(garch, "SCALE", 1.0)
    with pytest.raises(DataScaleWarning):
        GARCH11_SKEWT.fit(_view(_returns(sigma=0.01)))


def test_a_data_scale_warning_is_not_converted_into_a_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not even when the caller's filter says ``ignore``: the scoped capture sits above it."""
    monkeypatch.setattr(garch, "SCALE", 1.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(DataScaleWarning):
            GARCH11_SKEWT.fit(_view(_returns(sigma=0.01)))


def test_the_scaled_series_fits_without_any_warning() -> None:
    """The positive half: at ×100 nothing is emitted, so ``filterwarnings = error`` stays quiet."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for spec in GARCH_MODELS:
            _few(spec).fit(_view(_returns(sigma=0.01)))


# ---------------------------------------------------------------------------
# The convergence refusal (R91).
# ---------------------------------------------------------------------------


def _planted() -> GarchModel:
    return dataclasses.replace(GARCH11_SKEWT, maxiter=1)


def test_a_non_converging_origin_is_a_recorded_skip_not_a_forecast() -> None:
    """``options={"maxiter": 1}`` returns ``convergence_flag == 9``, and that reaches a skip."""
    with pytest.raises(ModelSkip, match="convergence_flag=9"):
        _planted().fit(_view(_returns()))


def test_a_non_converging_fit_is_recorded_against_the_model_and_leaves_the_origin() -> None:
    """The harness half of the name above (R87): not a forecast, but not a deleted origin either.

    Driven through ``evaluate``: every origin stays in ``results`` with a NaN for this model, and
    ``refusals`` carries ``(origin, model, reason)`` with the optimiser's flag in the reason. A
    ``SchemaError`` in its place would drop the origin for everyone, which this asserts against.
    """
    toy = make_toy(1)
    evaluation = run_cohort(toy, alone(_planted()))
    assert evaluation.results, "the origins vanished: a model problem was filed as a data problem"
    # Every origin an honest model is scored at is still there, since the toy's own window drops
    # some origins for everybody.
    assert len(evaluation.results) == len(run_cohort(toy, alone(GARCH11_SKEWT)).results)
    assert all(math.isnan(r.forecasts["garch11_skewt"]) for r in evaluation.results)
    assert len(evaluation.refusals) == len(evaluation.results)
    assert {name for _, name, _ in evaluation.refusals} == {"garch11_skewt"}
    assert all("convergence_flag=9" in reason for _, _, reason in evaluation.refusals)


@pytest.mark.parametrize("caller", ["error", "ignore", "always", "default"])
def test_the_refusal_is_identical_with_and_without_the_callers_warning_filters(caller: str) -> None:
    """Pytest runs under ``error``; the CLI has no ``-W error``. Both must refuse the same way.

    ``arch`` itself prepends an ``always`` filter for ``ConvergenceWarning`` inside ``fit``, so
    a plain ``simplefilter("error")`` around the call -- the design's first draft -- would not
    have turned it into an exception: the warning is *recorded* and classified instead.
    """
    with warnings.catch_warnings():
        warnings.simplefilter(caller)  # type: ignore[arg-type]
        with pytest.raises(ModelSkip) as raised:
            _planted().fit(_view(_returns()))
    assert "convergence_flag=9" in str(raised.value)


class _StubModel:
    """An ``arch_model`` whose ``fit`` does what it is told, to isolate one detector."""

    def __init__(self, *, flag: int = 0, emit: Callable[[], None] = lambda: None) -> None:
        self._flag = flag
        self._emit = emit

    def fit(self, **_: object) -> SimpleNamespace:
        self._emit()
        return SimpleNamespace(convergence_flag=self._flag)


def _stub(monkeypatch: pytest.MonkeyPatch, model: _StubModel) -> None:
    monkeypatch.setattr(garch, "arch_model", lambda *a, **k: model)


def test_the_flag_alone_is_enough_to_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two detectors for one condition: the warning can be configured away, the flag cannot."""
    _stub(monkeypatch, _StubModel(flag=4))
    with pytest.raises(ModelSkip, match="convergence_flag=4"):
        GARCH11_SKEWT.fit(_view(_returns()))


@pytest.mark.parametrize("category", [ConvergenceWarning, StartingValueWarning])
def test_the_two_skip_warnings_become_skips_even_with_a_zero_flag(
    monkeypatch: pytest.MonkeyPatch, category: type[Warning]
) -> None:
    _stub(monkeypatch, _StubModel(emit=lambda: warnings.warn("x", category, stacklevel=2)))
    with pytest.raises(ModelSkip, match=category.__name__):
        GARCH11_SKEWT.fit(_view(_returns()))


def test_an_unexpected_warning_propagates_rather_than_becoming_a_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, _StubModel(emit=lambda: warnings.warn("odd", UserWarning, stacklevel=2)))
    with pytest.raises(UserWarning, match="odd"):
        GARCH11_SKEWT.fit(_view(_returns()))


def test_a_scale_warning_beside_a_convergence_warning_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug outranks the data problem: a skip must not hide a mis-scaled fit."""

    def both() -> None:
        warnings.warn("scale", DataScaleWarning, stacklevel=2)
        warnings.warn("conv", ConvergenceWarning, stacklevel=2)

    _stub(monkeypatch, _StubModel(flag=9, emit=both))
    with pytest.raises(DataScaleWarning):
        GARCH11_SKEWT.fit(_view(_returns()))


def test_the_filter_does_not_leak_out_of_fit() -> None:
    """The capture is scoped: afterwards the caller's filters are what they were."""
    before = list(warnings.filters)
    with pytest.raises(ModelSkip):
        _planted().fit(_view(_returns()))
    assert warnings.filters == before


# ---------------------------------------------------------------------------
# The window (section 4.6).
# ---------------------------------------------------------------------------


def test_a_gap_truncates_the_fit_window_rather_than_being_bridged() -> None:
    """Everything before the last null is ignored: change it and the forecast does not move."""
    returns = _returns(600)
    gapped = returns.copy()
    gapped.iloc[250] = float("nan")
    scrambled = gapped.copy()
    scrambled.iloc[:250] = _returns(250, seed=99, sigma=0.03).to_numpy()  # same index, other data
    fitted = GARCH11_SKEWT.fit(_view(gapped))
    assert fitted.observations == 349
    assert fitted.forecast(22).value == GARCH11_SKEWT.fit(_view(scrambled)).forecast(22).value
    cut = GARCH11_SKEWT.fit(_view(returns.iloc[251:])).forecast(22).value
    assert fitted.forecast(22).value == cut


def test_a_tail_shorter_than_the_minimum_is_a_skip_naming_its_length() -> None:
    returns = _returns(300)
    returns.iloc[250] = float("nan")
    with pytest.raises(ModelSkip, match=r"49 .*100"):
        GARCH11_SKEWT.fit(_view(returns, minimum_training=100))


def test_a_missing_final_return_is_an_empty_window_and_a_skip() -> None:
    returns = _returns(300)
    returns.iloc[-1] = float("nan")
    with pytest.raises(ModelSkip, match=r"0 .*100"):
        GARCH11_SKEWT.fit(_view(returns, minimum_training=100))


# ---------------------------------------------------------------------------
# The shared conformance suite (R85, R86). One pass per specification.
# ---------------------------------------------------------------------------


def test_garch11_conforms() -> None:
    conformance_suite(GARCH11_SKEWT, toy=make_toy(1), rebuild=lambda _: GARCH11_SKEWT)


def test_gjr11_conforms_at_a_multistep_horizon() -> None:
    conformance_suite(_few(GJR11_SKEWT), toy=make_toy(22), rebuild=lambda _: _few(GJR11_SKEWT))


def test_egarch11_conforms_at_a_horizon_where_the_seed_matters() -> None:
    conformance_suite(
        _few(EGARCH11_SKEWT), toy=make_toy(22), rebuild=lambda _: _few(EGARCH11_SKEWT)
    )


# ---------------------------------------------------------------------------
# kappa = E[z**2 1{z<0}] in closed form (R117, R118): the identities, over the full bounds.
# ---------------------------------------------------------------------------

# arch's bounds are eta in [2.05, 300] and lambda in [-1, 1], both closed. The grid reaches the
# edges of both, because the closed form is exact where a quadrature is not.
ETAS = (2.05, 2.5, 4.0, 6.0, 30.0, 300.0)
LAMBDAS = (-0.999, -0.5, -0.0419, 0.0, 0.35, 0.999)
GRID = [(eta, lam) for eta in ETAS for lam in LAMBDAS]
CORNERS = [(eta, lam) for eta in (2.05, 300.0) for lam in (-1.0, 1.0)]


@pytest.mark.parametrize(("eta", "lam"), [*GRID, *CORNERS])
def test_the_standardised_skew_t_integrates_to_mass_one_mean_zero_variance_one(
    eta: float, lam: float
) -> None:
    """``S0/S1/S2`` over the whole line: fails if the ``(eta-1)/(eta-2)`` constant is wrong."""
    mass, mean, second = garch._skewt_moments(eta, lam, negative=False)
    assert mass == pytest.approx(1.0, abs=1e-12)
    assert mean == pytest.approx(0.0, abs=1e-12)
    assert second == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("eta", [*ETAS, 2.1, 3.0])
def test_a_symmetric_law_puts_exactly_half_the_second_moment_below_zero(eta: float) -> None:
    """``kappa(eta, 0) = 1/2``. ``abs=1e-12`` and not equality: 7.5e-15 off at ``eta = 300``."""
    assert garch._kappa(eta, 0.0) == pytest.approx(0.5, abs=1e-12)


@pytest.mark.parametrize(("eta", "lam"), [*GRID, *CORNERS])
def test_the_mirror_image_law_takes_the_other_half(eta: float, lam: float) -> None:
    """``z -> -z, lambda -> -lambda``: ``kappa(lam) + kappa(-lam) = E[z**2] = 1``. No oracle."""
    assert garch._kappa(eta, lam) + garch._kappa(eta, -lam) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize(("eta", "lam"), [*GRID, *CORNERS])
def test_kappa_is_a_strict_fraction_of_the_unit_variance(eta: float, lam: float) -> None:
    """A truncated second moment of a unit-variance law lies in ``[0, 1]``; interior is strict."""
    kappa = garch._kappa(eta, lam)
    assert math.isfinite(kappa)
    if abs(lam) < 1.0:
        assert 0.0 < kappa < 1.0
    else:
        assert 0.0 <= kappa <= 1.0


@pytest.mark.parametrize("lam", [-1.0, 1.0])
@pytest.mark.parametrize("eta", [2.05, 6.0, 300.0])
def test_the_degenerate_scale_at_lambda_plus_or_minus_one_is_skipped_not_divided_by(
    eta: float, lam: float
) -> None:
    """At ``lambda = +-1`` one scale is exactly 0 and ``y / d`` would be a NaN; it has no mass.

    arch's bound is the closed interval, so this is reachable. The value is not 0 or 1 -- the
    shifted origin ``a`` puts mass of the other piece on both sides of ``z = 0`` -- so what is
    asserted is that it is a number, that the whole line still integrates to (1, 0, 1), and that
    the mirror identity holds across the degenerate edge.
    """
    assert math.isfinite(garch._kappa(eta, lam))
    mass, mean, second = garch._skewt_moments(eta, lam, negative=False)
    assert (mass, mean, second) == pytest.approx((1.0, 0.0, 1.0), abs=1e-12)
    assert garch._kappa(eta, lam) + garch._kappa(eta, -lam) == pytest.approx(1.0, abs=1e-12)


def _kappa_by_quadrature(eta: float, lam: float, *, u_max: float = 30.0, n: int = 200_000) -> float:
    """``E[z**2 1{z<0}]`` by composite Simpson over ``z = sinh(u)``, from arch's own density.

    Valid for ``eta >= 4`` only: the ``z**2`` tail is too heavy to truncate below that, and at
    ``eta = 2.1`` this integral answers 0.4755 where the mirror symmetry forces exactly 1/2.
    ``sinh`` turns the polynomial tail into exponential decay; Simpson's ``du**4`` error is then
    far below the 1e-10 asserted. Never ``leggauss`` at large n: an O(n**3) eigensolve that
    allocates 3.2 GB at n=20,000.
    """
    u = np.linspace(-u_max, u_max, n + 1)  # n even
    weights = np.ones(n + 1)
    weights[1:-1:2], weights[2:-1:2] = 4.0, 2.0
    weights *= (u[1] - u[0]) / 3.0
    z = np.sinh(u)
    density = (
        np.exp(SkewStudent().loglikelihood([eta, lam], z, np.ones_like(z), individual=True))
        * np.cosh(u)
        * weights
    )
    return float((z[z < 0] ** 2 * density[z < 0]).sum())


# ``eta >= 4`` ONLY, and the restriction is the point. At ``eta = 2.1`` the Simpson integral is
# wrong by 0.0245 and at ``(2.05, -0.9)`` by 0.228, because the ``z**2`` tail is too heavy to
# truncate; a wider parametrisation would fail, someone would loosen ``abs``, and the closed
# form's real error budget would be hidden behind the oracle's truncation. Full-bounds coverage
# comes from the identities above, which need no oracle.
@pytest.mark.parametrize("eta", [4.0, 5.09, 6.44, 8.0, 30.0])
@pytest.mark.parametrize("lam", [-0.9, -0.5, -0.2, 0.0, 0.35, 0.8])
def test_the_closed_form_matches_an_independent_quadrature(eta: float, lam: float) -> None:
    assert garch._kappa(eta, lam) == pytest.approx(_kappa_by_quadrature(eta, lam), abs=1e-10)


# ---------------------------------------------------------------------------
# The path sum and the parameter wiring (R117, R118): two witnesses that need no kappa.
# ---------------------------------------------------------------------------

LABELS = ["omega", "alpha[1]", "gamma[1]", "beta[1]", "eta", "lambda"]
HORIZONS = (1, 5, 22, 66)


def test_the_path_sum_is_the_recursion_summed() -> None:
    """``m_1 = first``, ``m_{k+1} = omega + phi * m_k``, summed over ``k = 1..h``."""
    assert garch._path_sum(0.5, 0.5, 2.0, 1) == 2.0
    # 2, 1.5, 1.25, 1.125
    assert garch._path_sum(0.5, 0.5, 2.0, 4) == pytest.approx(2.0 + 1.5 + 1.25 + 1.125, rel=1e-15)


@pytest.mark.parametrize("horizon", HORIZONS)
def test_the_path_sum_survives_a_persistence_one_ulp_from_one(horizon: int) -> None:
    """Why the recursion is iterated and the geometric sum is not (M11).

    ``alpha + beta`` arrives as ``1 - O(eps)``, so ``1 - phi`` is an ulp or two and the
    closed-form ``G = (1 - phi**h) / (1 - phi)`` is a ratio of two numbers that have lost all but a
    digit. Exact rational arithmetic on the same float ``phi`` is the reference.
    """
    omega, first = 1e-3, 0.8
    phi = 1.0 - 2.0**-52
    exact, m = Fraction(0), Fraction(first)
    for _ in range(horizon):
        exact += m
        m = Fraction(omega) + Fraction(phi) * m
    assert garch._path_sum(omega, phi, first, horizon) == pytest.approx(float(exact), rel=1e-14)


def test_a_label_that_is_not_in_the_fit_is_a_value_error_naming_what_arch_returned() -> None:
    params = pd.Series([0.05, 0.05, 0.90, 8.0, -0.3], index=[*LABELS[:2], *LABELS[3:]])
    with pytest.raises(ValueError, match=r"gamma\[1\].*omega.*alpha\[1\].*beta\[1\]"):
        garch._read(params, "gamma[1]", name="gjr11_skewt")
    assert garch._read(params, "beta[1]", name="gjr11_skewt") == 0.90


def test_a_symmetric_fit_has_no_leverage_term_and_the_persistence_refuses_it() -> None:
    """Reading by position would have answered ``beta`` for ``gamma``: the label read cannot."""
    params = pd.Series([0.05, 0.05, 0.90, 8.0, -0.3], index=[*LABELS[:2], *LABELS[3:]])
    with pytest.raises(ValueError, match="gamma"):
        garch._persistence(params, name="gjr11_skewt")


def test_the_persistence_is_alpha_plus_gamma_kappa_plus_beta() -> None:
    params = pd.Series([0.05, 0.08, 0.16, 0.80, 6.0, -0.5], index=LABELS)
    expected = 0.08 + 0.16 * garch._kappa(6.0, -0.5) + 0.80
    assert garch._persistence(params, name="gjr11_skewt") == pytest.approx(expected, rel=1e-15)


def _arch_analytic_sum(fitted: Forecaster, horizon: int) -> float:
    """arch's own analytic path sum, in percent-squared, for a result we did not compute."""
    result = fitted.result  # type: ignore[attr-defined]
    steps = result.forecast(horizon=horizon, method="analytic", reindex=False).variance
    return float(steps.iloc[-1].sum(skipna=False))


def _first_step(fitted: Forecaster) -> float:
    return float(fitted.result.forecast(horizon=1, reindex=False).variance.iloc[-1, 0])  # type: ignore[attr-defined]


@pytest.mark.parametrize("horizon", HORIZONS)
def test_w1_the_path_sum_reproduces_arch_analytic_garch_with_no_kappa_in_sight(
    horizon: int,
) -> None:
    """For ``o = 0`` ``phi = alpha + beta`` and arch's analytic recursion is the true mean.

    Validates the recursion, the ``omega / alpha[1] / beta[1]`` label reads, the ``m_1`` handoff
    (not an expectation) and the summation. Cannot validate ``gamma`` or ``kappa``. Measured at
    3.0e-15 relative on four SPY origins; the bound here is 1e-11.
    """
    fitted = GARCH11_SKEWT.fit(_view(_returns()))
    p = fitted.result.params  # type: ignore[attr-defined]
    omega, alpha, beta = (
        garch._read(p, label, name="w1") for label in (garch._OMEGA, garch._ALPHA, garch._BETA)
    )
    ours = garch._path_sum(omega, alpha + beta, _first_step(fitted), horizon)
    assert ours == pytest.approx(_arch_analytic_sum(fitted, horizon), rel=1e-11)


@pytest.mark.parametrize("horizon", HORIZONS)
def test_w2_arch_analytic_gjr_is_the_recursion_at_kappa_one_half(
    horizon: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R118's root cause as a test and not a reading of library source.

    arch hard-codes ``kappa = 1/2`` in ``GARCH.constraints`` *and* in its analytic recursion, so its
    analytic GJR path sum is ``_path_sum`` at ``phi = alpha + gamma/2 + beta`` (3.3e-15 relative on
    SPY). This one, unlike W1, covers the ``gamma[1]`` read and the leverage term.
    """
    fitted = _few(GJR11_SKEWT).fit(_view(_returns()))
    p = fitted.result.params  # type: ignore[attr-defined]
    # The production persistence with kappa pinned to arch's 1/2: so the label reads and the
    # ``alpha + gamma*kappa + beta`` arithmetic under test are ours, not re-typed here.
    monkeypatch.setattr(garch, "_kappa", lambda eta, lam: 0.5)
    phi_half = garch._persistence(p, name="w2")
    assert phi_half == pytest.approx(p["alpha[1]"] + p["gamma[1]"] / 2 + p["beta[1]"], rel=1e-15)
    ours = garch._path_sum(
        garch._read(p, "omega", name="w2"), phi_half, _first_step(fitted), horizon
    )
    assert ours == pytest.approx(_arch_analytic_sum(fitted, horizon), rel=1e-11)


# ---------------------------------------------------------------------------
# The exact forecast, end to end (R117-R120): the aggregation, W4, and the refusal guard.
# ---------------------------------------------------------------------------


def _skewed_returns(n: int = 500, *, seed: int = 11) -> pd.Series:
    """A leveraged GJR series with genuinely skewed innovations, so ``kappa`` is load-bearing.

    The ``_returns`` fixture fits to ``lambda = -0.04``, ``kappa = 0.513``: the whole ``kappa`` term
    moves ``phi`` by 6e-4, so replacing the closed form with 1/2 would change a forecast by far less
    than any usable tolerance, and a test with no power against the bug it exists to catch is worse
    than none. DGP: ``z ~ SkewStudent(eta=6, lambda=-0.5)``;
    ``sigma2_t = (1-a-g/2-b)*sigma**2 + (a + g*1{r<0})*r**2 + b*sigma2_{t-1}``, a=0.08, g=0.16,
    b=0.80, sigma=0.01.
    """
    shocks = SkewStudent(seed=seed).simulate([6.0, -0.5])(n)
    a, g, b, sigma = 0.08, 0.16, 0.80, 0.01
    variance = np.empty(n)
    out = np.empty(n)
    variance[0] = sigma**2
    for t in range(n):
        if t:
            shock = out[t - 1]
            variance[t] = (1 - a - g / 2 - b) * sigma**2 + (a + g * (shock < 0)) * shock**2
            variance[t] += b * variance[t - 1]
        out[t] = math.sqrt(variance[t]) * shocks[t]
    days = [stamp.date() for stamp in pd.bdate_range("2020-01-02", periods=n)]
    return pd.Series(out, index=days)


def _first_step_and_params(fitted: Forecaster) -> tuple[float, pd.Series]:
    return _first_step(fitted), fitted.result.params  # type: ignore[attr-defined]


def test_the_skewed_fixture_has_the_skew_that_gives_the_kappa_term_its_power() -> None:
    """Measured, not assumed: ``lambda_hat = -0.536``, ``kappa = 0.637``, ``phi`` gap 0.033.

    The design's own toy gave a gap of 0.0214 (M18); this DGP's draws differ, and what matters is
    the gap is large next to the ``_returns`` fixture's 6e-4. If a library change shrinks it, this
    fails before W4 starts passing for the wrong reason.
    """
    fitted = GJR11_SKEWT.fit(_view(_skewed_returns()))
    _, p = _first_step_and_params(fitted)
    kappa = garch._kappa(p["eta"], p["lambda"])
    phi_true = garch._persistence(p, name="gjr11_skewt")
    phi_half = p["alpha[1]"] + p["gamma[1]"] / 2 + p["beta[1]"]
    assert p["lambda"] < -0.4 and kappa - 0.5 > 0.1
    assert phi_true - phi_half > 0.02


def test_w4_exact_agrees_with_simulation_where_simulation_is_trustworthy() -> None:
    """Exact, simulated at the full 100,000 paths, and the ``kappa = 1/2`` recursion, at h=5.

    Measured on this fixture: simulated/exact within 0.27% over six seeds; ``kappa = 1/2`` is 6.2%
    low. A 1% band separates the two by a factor of four in both directions. The tolerance comes
    from this fixture's spread and not SPY's 0.21-0.40% (R119's figures are a different process).
    The h=5 red case: if ``kappa`` were the constant 1/2 the first assertion would still hold
    (the simulation is exact) and the second would not.
    """
    view = _view(_skewed_returns(), horizon=5)
    fitted = GJR11_SKEWT.fit(view)
    exact = fitted.forecast(5).value
    simulated = dataclasses.replace(GJR11_SKEWT, method="simulation").fit(view).forecast(5).value
    first, p = _first_step_and_params(fitted)
    phi_half = p["alpha[1]"] + p["gamma[1]"] / 2 + p["beta[1]"]
    half = garch._path_sum(p["omega"], phi_half, first, 5) / garch.SCALE**2
    assert simulated == pytest.approx(exact, rel=0.01)
    assert half != pytest.approx(exact, rel=0.01)


def test_w4_the_three_recursions_are_distinguishable_at_h66_without_asserting_on_a_draw() -> None:
    """At h=66 the ``kappa = 1/2`` recursion is far from the exact one (0.60 of it here).

    Only the recursions are compared: that simulation *disagrees* with exact at h=66 is a property
    of SPY's ``psi/phi**2`` (R119's sweep), not of the code, and on a gentle fixture the two agree.
    A ``!=`` against a simulation here would pass for the wrong reason and break on an innocent
    change, so it is deliberately not written.
    """
    fitted = GJR11_SKEWT.fit(_view(_skewed_returns(), horizon=66))
    first, p = _first_step_and_params(fitted)
    phi_half = p["alpha[1]"] + p["gamma[1]"] / 2 + p["beta[1]"]
    half = garch._path_sum(p["omega"], phi_half, first, 66) / garch.SCALE**2
    assert half < 0.8 * fitted.forecast(66).value


@pytest.mark.parametrize("horizon", [1, 5, 22, 66])
def test_the_exact_forecast_is_the_path_sum_over_scale_squared(horizon: int) -> None:
    """The composition on a real fit: arch's step one, ``phi`` from ``kappa``, one ``/1e4``."""
    fitted = GJR11_SKEWT.fit(_view(_returns()))
    first, p = _first_step_and_params(fitted)
    phi = p["alpha[1]"] + p["gamma[1]"] * garch._kappa(p["eta"], p["lambda"]) + p["beta[1]"]
    expected = garch._path_sum(p["omega"], phi, first, horizon) / 1e4
    assert fitted.forecast(horizon).value == pytest.approx(expected, rel=1e-13)


def test_the_one_step_forecast_does_not_depend_on_the_method() -> None:
    """Step one takes no expectation: exact, simulated and analytic agree (1 ulp apart in M15)."""
    view = _view(_returns(), horizon=1)
    exact = GJR11_SKEWT.fit(view).forecast(1).value
    simulated = _few(dataclasses.replace(GJR11_SKEWT, method="simulation")).fit(view).forecast(1)
    assert simulated.value == pytest.approx(exact, rel=1e-12)
    first = _first_step(GJR11_SKEWT.fit(view))
    assert exact == first / 1e4


def test_an_explosive_exact_forecast_is_reported_not_refused() -> None:
    """``phi > 1`` is the result at 238/238 SPY origins (R118): a number, not a guard to trip."""
    params = pd.Series(
        [0.05, 0.30, 0.30, 0.90, 8.0, -0.3],
        index=["omega", "alpha[1]", "gamma[1]", "beta[1]", "eta", "lambda"],
    )
    result = _FakeResult([1.0] * 66)
    result.params = params  # type: ignore[misc]
    assert garch._persistence(params, name="x") > 1.0
    value = _forecaster(GJR11_SKEWT, result).forecast(66).value
    assert math.isfinite(value) and value > 66 / 1e4


def test_a_nan_step_one_is_a_recorded_skip_for_the_exact_branch_too() -> None:
    """The finiteness half of the guard still matters: it asserts that a NaN cannot get through."""
    with pytest.raises(ModelSkip, match="finite"):
        _forecaster(GJR11_SKEWT, _FakeResult([float("nan")] * 5)).forecast(5)


def test_an_unexpected_warning_from_the_exact_step_one_propagates() -> None:
    def odd() -> None:
        warnings.warn("odd", UserWarning, stacklevel=2)

    with pytest.raises(UserWarning, match="odd"):
        _forecaster(GJR11_SKEWT, _FakeResult([1.0] * 5, emit=odd)).forecast(5)


def test_an_exact_forecast_from_a_fit_without_the_leverage_label_is_a_value_error() -> None:
    """Wiring, not data: not a ``ModelSkip``, so ``evaluate`` aborts instead of filing an origin."""
    result = _FakeResult([1.0] * 5)
    result.params = _FakeResult.params.drop("gamma[1]")  # type: ignore[misc]
    with pytest.raises(ValueError, match=r"gamma\[1\]"):
        _forecaster(GJR11_SKEWT, result).forecast(5)


# ---------------------------------------------------------------------------
# The probe (R32, R79): the figures R90 and the exact-forecast design cite must stay regenerable.
# ---------------------------------------------------------------------------

PROBE = Path(__file__).resolve().parents[1] / "tools" / "measure_garch_forecast_error.py"


def _probe(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The probe, loaded from ``tools/`` (not a package), cheap enough to run in the suite."""
    monkeypatch.syspath_prepend(str(PROBE.parent))  # for ``_harness``
    spec = importlib.util.spec_from_file_location("measure_garch_forecast_error", PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REFERENCE_PATHS", 2_000)
    return module


def test_the_probe_prints_an_analytic_figure_for_every_specification_that_has_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """R90's headline figure must survive ``__post_init__`` refusing ``("GARCH", 1, "analytic")``.

    The probe used to build ``replace(spec, method="analytic")`` inside ``except ValueError``, so
    the refusal would have printed "analytic unavailable" and exited 0 with the figure gone.
    """
    probe = _probe(monkeypatch)
    returns = probe.simulate_returns(600, 20241012)
    probe.block_analytic_against_simulation(returns, (1, 5))
    out = capsys.readouterr().out
    assert "unavailable" not in out
    for name in ("garch11_skewt", "gjr11_skewt"):
        for horizon in (1, 5):
            (line,) = [ln for ln in out.splitlines() if re.match(rf"{name}\s+h={horizon}\s", ln)]
            assert re.search(r"analytic\s+\d+\.\d+\s+exact\s+\d+\.\d+\s+sim", line), line
    (egarch,) = [ln for ln in out.splitlines() if re.match(r"egarch11_skewt\s+h=1\s", ln)]
    assert "no analytic recursion" in egarch and "R114" in egarch


def test_a_refusal_inside_the_probe_escapes_it_rather_than_printing_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The red case for the above: a planted ``ValueError`` must propagate, not become a line."""
    probe = _probe(monkeypatch)
    returns = probe.simulate_returns(600, 20241012)

    def refuse(*_: object) -> float:
        raise ValueError("planted refusal")

    monkeypatch.setattr(probe, "_path_sum", refuse)
    with pytest.raises(ValueError, match="planted refusal"):
        probe.block_analytic_against_simulation(returns, (5,))
    assert "unavailable" not in capsys.readouterr().out


def test_the_probe_stops_when_the_recursion_no_longer_matches_archs_analytic_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W2 at probe level: a figure is never printed beside a recursion that disagrees with arch."""
    probe = _probe(monkeypatch)
    returns = probe.simulate_returns(600, 20241012)
    monkeypatch.setattr(probe, "_arch_analytic", lambda fitted, horizon: 1.0)
    with pytest.raises(RuntimeError, match="no longer matches arch"):
        probe.block_analytic_against_simulation(returns, (5,))


def test_the_probe_catches_no_value_error() -> None:
    """AST: no handler for ``ValueError`` anywhere in it, so nothing it calls can be swallowed."""
    handlers = [
        node
        for node in ast.walk(ast.parse(PROBE.read_text()))
        if isinstance(node, ast.ExceptHandler)
        and any(isinstance(n, ast.Name) and n.id == "ValueError" for n in ast.walk(node))
    ]
    assert handlers == []


def test_the_probe_shows_the_geometric_sum_failing_where_the_iteration_does_not(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """M11/M12 regenerable: a persistence one ulp below 1 breaks the geometric sum, not the loop."""
    probe = _probe(monkeypatch)
    returns = probe.simulate_returns(600, 20241012)
    probe.block_path_sum_numerics(returns, (66,))
    rows = [ln for ln in capsys.readouterr().out.splitlines() if "planted" in ln]
    assert len(rows) == 2
    for row in rows:
        iteration, geometric = (float(x) for x in re.findall(r"err (\S+)", row))
        assert iteration < 1e-12 and geometric > 1e-2, row
