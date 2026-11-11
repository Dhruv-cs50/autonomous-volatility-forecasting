"""HAR with a declared subset of the feature vocabulary (P5-B1).

The contestant is a HAR plus the extra columns it was constructed with, so the research agent has
something to propose. These tests guard what can go wrong quietly:

* the arithmetic: a noiseless design with known coefficients is recovered, so a wrong regression
  fails and does not merely crash;
* the error class. A null extra at the origin, too few training rows carrying every extra, and a
  singular design caused by an extra are this contestant's own refusal, a ``ModelSkip``. A
  ``SchemaError`` would delete the origin for every contestant in the cohort (R87). The singular
  case is a real one, not a hypothetical: ``har_d_proxy`` *is* ``rv_proxy_d1`` exactly, so
  ``rv_proxy_d1`` beside the HAR base is an exactly singular design;
* the declaration (``requires`` is the HAR columns then the extras) and the name, which must be
  usable as a path segment (R196);
* the import discipline, and the shared conformance suite at three horizons.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import random
from pathlib import Path

import pandas as pd
import pytest
from model_conformance import conformance_suite, make_toy, views_of

import volab.walkforward as wf
from volab.errors import ModelSkip, SchemaError
from volab.features.build import HAR_COLUMNS
from volab.models.base import Model, TrainingView
from volab.models.harx import NAME, HarX, harx
from volab.paths import _GLOB_METACHARACTERS, validate_segment

SOURCE = Path(__file__).resolve().parents[1] / "src" / "volab" / "models" / "harx.py"

# Known truth for the synthetic design: y = 0.5 + 2 d + 1 w + 0.5 m + 3 x - 4 z, no noise.
INTERCEPT, COEFFICIENTS = 0.5, {"har_d_proxy": 2.0, "har_w_proxy": 1.0, "har_m_proxy": 0.5}
EXTRA_COEFFICIENTS = {"x": 3.0, "z": -4.0}


def _synthetic(rows: int = 60, minimum_training: int = 20) -> tuple[TrainingView, float]:
    """A view whose response is an exact linear function of independent random regressors, and the
    response the origin's row would have."""
    generator = random.Random(7)
    truth = {**COEFFICIENTS, **EXTRA_COEFFICIENTS}
    columns = {name: [generator.uniform(0.5, 2.0) for _ in range(rows + 1)] for name in truth}
    frame = pd.DataFrame(columns)
    frame["y"] = INTERCEPT + sum(frame[name] * weight for name, weight in truth.items())
    row = frame.iloc[-1].drop("y")
    view = TrainingView(
        symbol="SPY",
        origin=dt.date(2024, 1, 2),
        horizon=1,
        response="y",
        training=frame.iloc[:rows].copy(),
        row=row,
        returns=pd.Series(dtype="float64"),
        minimum_training=minimum_training,
        stride=1,
    )
    return view, float(frame["y"].iloc[-1])


def _toy_view(horizon: int = 1) -> TrainingView:
    return views_of(make_toy(horizon, minimum_training=20))[-1]


def test_it_recovers_known_coefficients_from_a_noiseless_design() -> None:
    view, truth = _synthetic()
    model = harx(("x", "z"))
    fitted = model.fit(view)
    assert fitted.forecast(1).value == pytest.approx(truth, abs=1e-8)
    assert fitted.observations == 60
    # The prediction moves by exactly the extra's coefficient when the extra moves by one.
    bumped = view.row.copy()
    bumped["z"] += 1.0
    assert model.fit(dataclasses.replace(view, row=bumped)).forecast(1).value == pytest.approx(
        truth - 4.0, abs=1e-8
    )


def test_the_regression_is_the_har_regressors_then_the_extras_through_the_module_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, ...]] = []
    real = wf.fit_ols

    def spy(frame: pd.DataFrame, regressors: tuple[str, ...], response: str) -> wf.Fit:
        seen.append(tuple(regressors))
        return real(frame, regressors, response)

    monkeypatch.setattr(wf, "fit_ols", spy)
    view, _ = _synthetic()
    harx(("z", "x")).fit(view)
    assert seen == [(*wf.HAR_REGRESSORS, "z", "x")]


def test_it_declares_the_har_columns_then_the_extras_at_every_horizon() -> None:
    model = harx(("iv_var_h1", "vvix"))
    for horizon in (1, 5, 22, 66):
        assert model.domain.answers(horizon)
        assert model.domain.requires_at(horizon) == (*HAR_COLUMNS, "iv_var_h1", "vvix")


def test_it_is_a_model_that_fits_the_target_side() -> None:
    model = harx(("vvix",))
    assert isinstance(model, Model)
    assert isinstance(model, HarX)
    assert model.fit_depends_on_horizon is True


def test_the_name_is_harx_plus_the_extras_with_no_glob_metacharacter() -> None:
    """R196: a bracketed name would be rejected by ``validate_segment`` the moment it reached a
    path, because ``[`` and ``]`` are glob metacharacters."""
    name = harx(("a", "b")).name
    assert name == "harx+a+b"
    assert NAME == "harx"
    assert not _GLOB_METACHARACTERS & set(name)
    assert validate_segment("model", name) == name
    assert harx(("iv_h5", "vvix")).name == "harx+iv_h5+vvix"


def test_it_skips_an_origin_where_an_extra_is_null_naming_the_column() -> None:
    view, _ = _synthetic()
    row = view.row.copy()
    row["z"] = float("nan")
    with pytest.raises(ModelSkip, match="'z'") as raised:
        harx(("x", "z")).fit(dataclasses.replace(view, row=row))
    assert not isinstance(raised.value, SchemaError)


def test_it_skips_when_too_few_training_rows_carry_every_extra() -> None:
    view, _ = _synthetic()
    training = view.training.copy()
    # ``x`` carried on exactly one row fewer than the minimum, ``z`` carried everywhere.
    training["x"] = training["x"].where(training.index >= len(training) - (20 - 1))
    thin = dataclasses.replace(view, training=training)
    with pytest.raises(ModelSkip, match="'x'") as raised:
        harx(("x", "z")).fit(thin)
    assert not isinstance(raised.value, SchemaError)
    # The reason names the thin column and not the dense one. The model's own name lists every
    # extra, so the check is on what follows it.
    reason = str(raised.value).split(": ", 1)[1]
    assert "'x' is thin on its own" in reason
    assert "'z'" not in reason


def test_two_extras_thin_only_jointly_are_reported_as_such() -> None:
    """Each extra is carried on 30 rows, enough alone, but on disjoint ones: no row carries both."""
    view, _ = _synthetic()
    training = view.training.copy()
    first = training.index < 30
    training["x"] = training["x"].where(first)
    training["z"] = training["z"].where(~first)
    with pytest.raises(ModelSkip) as raised:
        harx(("x", "z")).fit(dataclasses.replace(view, training=training))
    reason = str(raised.value).split(": ", 1)[1]
    assert "0 training row(s)" in reason
    assert "none is thin on its own" in reason
    assert "thin on its own" not in reason.replace("none is thin on its own", "")


def test_two_individually_thin_extras_are_named_in_the_plural() -> None:
    view, _ = _synthetic()
    training = view.training.copy()
    keep = training.index >= len(training) - 5
    training["x"] = training["x"].where(keep)
    training["z"] = training["z"].where(keep)
    with pytest.raises(ModelSkip) as raised:
        harx(("x", "z")).fit(dataclasses.replace(view, training=training))
    assert "'x', 'z' are each thin on their own" in str(raised.value)


def test_the_har_base_is_the_rv_proxy_d1_column_exactly_on_the_toy() -> None:
    view = _toy_view()
    pair = view.training[["har_d_proxy", "rv_proxy_d1"]].dropna()
    assert len(pair) >= view.minimum_training
    assert (pair["har_d_proxy"] == pair["rv_proxy_d1"]).all()


@pytest.mark.parametrize("column", ["rv_proxy_d1", "rv_proxy_d5", "rv_proxy_d22"])
def test_an_extra_that_is_a_har_regressor_in_disguise_is_a_skip_not_a_schema_error(
    column: str,
) -> None:
    """On the toy each of these is collinear with the HAR base, so the design is singular. It is
    the proposal's fault and not the window's: the HAR alone fits the same window."""
    view = _toy_view()
    wf.fit_ols(view.training, wf.HAR_REGRESSORS, view.response)  # the window is fittable
    with pytest.raises(ModelSkip) as raised:
        harx((column,)).fit(view)
    assert not isinstance(raised.value, SchemaError)
    # After the name prefix: the model's own name contains the column and would match anyway.
    assert column in str(raised.value).split(": ", 1)[1]
    assert isinstance(raised.value.__cause__, SchemaError)


def test_a_window_the_har_regressors_cannot_fit_stays_a_schema_error() -> None:
    view, _ = _synthetic()
    training = view.training.assign(har_d_proxy=1.0)
    with pytest.raises(SchemaError, match="singular"):
        harx(("x",)).fit(dataclasses.replace(view, training=training))


def test_forecasting_a_horizon_the_fit_was_not_made_for_is_a_value_error() -> None:
    view, _ = _synthetic()
    with pytest.raises(ValueError, match="asked to forecast horizon 5"):
        harx(("x",)).fit(view).forecast(5)


def test_a_view_without_a_declared_extra_is_a_value_error_not_a_data_problem() -> None:
    view, _ = _synthetic()
    with pytest.raises(ValueError, match="no 'missing' in the view"):
        harx(("missing",)).fit(view)


def test_an_empty_extra_is_refused_at_construction_of_the_dataclass_and_the_factory() -> None:
    with pytest.raises(ValueError, match="at least one"):
        HarX(extra=())
    with pytest.raises(ValueError, match="at least one"):
        harx(())


def test_a_repeated_extra_is_refused_at_construction_of_the_dataclass_and_the_factory() -> None:
    with pytest.raises(ValueError, match=r"repeats \['x'\]"):
        HarX(extra=("x", "z", "x"))
    with pytest.raises(ValueError, match=r"repeats \['x'\]"):
        harx(("x", "x"))


def test_the_module_imports_no_numpy_scipy_or_arch() -> None:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(SOURCE.read_text())):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots, "no imports were read, so nothing was checked"
    assert not {"numpy", "scipy", "arch"} & roots


def test_the_full_conformance_suite_passes() -> None:
    def build() -> HarX:
        return harx(("iv_var_h1", "vvix"))

    conformance_suite(
        build(),
        toy=make_toy(1, minimum_training=20),
        rebuild=lambda _: build(),
        also_at=(make_toy(5, minimum_training=20), make_toy(22, minimum_training=20)),
    )
