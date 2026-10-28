"""The GARCH family as contestants: three specifications, one aggregation, one refusal (US-3.2).

**What needs the full simulation count: nothing in this file.** The shipped default is
``simulations=100_000`` (R90) and ``test_the_shipped_simulation_count_is_one_hundred_thousand``
pins it as a number, but every test that actually fits and forecasts builds its model with a few
hundred paths, because none of the properties under test depend on the count: they are about
what is passed to ``arch`` (a fake result records it), about determinism (any count is
deterministic under a fixed seed), and about the aggregation (linear whatever the paths). The
expensive claim -- how far the analytic and simulated forecasts sit apart at S=100,000 -- is a
measurement and not a test: ``tools/measure_garch_forecast_error.py`` (R32 applied to numbers).

The shared conformance suite is run once per specification. The two simulated specifications are
run at horizon 22 and not 1: at h=1 the step-one variance is deterministic, so a simulation that
ignored its seed would still pass clauses 5 and 6 vacuously.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import math
import warnings
import zlib
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
from arch.univariate.distribution import SkewStudent
from arch.utility.exceptions import ConvergenceWarning, DataScaleWarning, StartingValueWarning
from model_conformance import alone, conformance_suite, make_toy, run_cohort

import volab.models.garch as garch
from volab.errors import ModelSkip
from volab.models.base import Forecaster, Model, TrainingView
from volab.models.garch import EGARCH11_SKEWT, GARCH11_SKEWT, GARCH_MODELS, GJR11_SKEWT, GarchModel

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
    return dataclasses.replace(spec, simulations=FEW)


class _FakeResult:
    """Stands in for ``ARCHModelResult``: records how it was asked, answers a given variance."""

    # The last two entries are the skew-t shape ``(eta, lambda)``, as in a real result.
    params = pd.Series([0.05, 0.05, 0.90, 8.0, -0.3])

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
    """Witness for R91's table: names, volatility process, forecast method, and the count."""
    assert [spec.name for spec in GARCH_MODELS] == [
        "garch11_skewt",
        "gjr11_skewt",
        "egarch11_skewt",
    ]
    assert GARCH_MODELS == (GARCH11_SKEWT, GJR11_SKEWT, EGARCH11_SKEWT)
    assert [(s.volatility, s.o, s.method) for s in GARCH_MODELS] == [
        ("GARCH", 0, "analytic"),
        ("GARCH", 1, "simulation"),
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
    """R90: ``S=10,000`` was measured at up to 1.4% uncertain, worse than the leak R72 caught."""
    assert GJR11_SKEWT.simulations == EGARCH11_SKEWT.simulations == 100_000


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


def test_the_analytic_method_is_used_only_for_the_symmetric_specification() -> None:
    """R90: ``arch``'s analytic GJR is 17.7% low at h=66 under skew-t. This would have caught it.

    The string must occur exactly once in the module's code, and inside the call that builds
    ``garch11_skewt``. A docstring is a different ``Constant`` -- its value is a sentence.
    """
    tree = _module()
    occurrences = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "analytic"
    ]
    assert len(occurrences) == 1, f"'analytic' appears {len(occurrences)} times in the code"
    owners = [
        call
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and any(node is occurrences[0] for node in ast.walk(call))
        and any(isinstance(n, ast.Constant) and n.value == "garch11_skewt" for n in ast.walk(call))
    ]
    assert owners, "the one 'analytic' is not inside the garch11_skewt specification"


def test_the_simulated_specs_ask_for_simulation_with_the_stated_count() -> None:
    """What actually reaches ``arch`` -- recorded by a fake, so no path is simulated here."""
    for spec, method in (
        (GARCH11_SKEWT, "analytic"),
        (GJR11_SKEWT, "simulation"),
        (EGARCH11_SKEWT, "simulation"),
    ):
        result = _FakeResult([1.0] * 22)
        _forecaster(spec, result).forecast(22)
        (call,) = result.calls
        assert call["method"] == method, spec.name
        assert call["reindex"] is False
        if method == "simulation":
            assert call["simulations"] == 100_000
            assert callable(call["rng"])
        else:
            assert "simulations" not in call and "rng" not in call
        assert "random_state" not in call, "arch reads random_state only for the bootstrap"


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
    _forecaster(GJR11_SKEWT, result).forecast(5)
    seed = garch._seed("gjr11_skewt", "SPY", dt.date(2024, 4, 12), 5)
    shape = SkewStudent(seed=seed)
    expected = shape.simulate(_FakeResult.params.to_numpy()[-2:])(7)
    assert result.calls[0]["rng"](7).tolist() == expected.tolist()


def test_the_same_view_forecasts_the_same_number_twice() -> None:
    """Determinism of a real simulation (any count: the seed fixes the paths, not their number)."""
    view = _view(_returns())
    for spec in (_few(GJR11_SKEWT), _few(EGARCH11_SKEWT)):
        first = spec.fit(view).forecast(22).value
        second = spec.fit(view).forecast(22).value
        assert first == second, spec.name
        fitted = spec.fit(view)
        assert fitted.forecast(22).value == fitted.forecast(22).value


def test_a_different_origin_draws_different_paths() -> None:
    """The seed reaches the draws: same fit window, other origin, other key, other forecast.

    Without this the test above would pass for a model that ignored its seed *and* its
    randomness, and the seed is exactly what the design's ``random_state`` failed to deliver.
    """
    view = _view(_returns())
    later = dataclasses.replace(view, origin=view.origin + dt.timedelta(days=1))
    spec = _few(GJR11_SKEWT)
    assert spec.fit(view).forecast(22).value != spec.fit(later).forecast(22).value


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


def test_gjr11_conforms_at_a_horizon_where_the_seed_matters() -> None:
    conformance_suite(_few(GJR11_SKEWT), toy=make_toy(22), rebuild=lambda _: _few(GJR11_SKEWT))


def test_egarch11_conforms_at_a_horizon_where_the_seed_matters() -> None:
    conformance_suite(
        _few(EGARCH11_SKEWT), toy=make_toy(22), rebuild=lambda _: _few(EGARCH11_SKEWT)
    )
