"""The horizon-matched implied-variance HAR (P3-03).

The contestant is a HAR with one extra regressor, the implied variance whose maturity matches the
horizon. What these tests are about is the three ways that can go wrong quietly: the wrong
horizon's column being read, the column being rescaled when it is already on the target's scale,
and a null column being fitted around instead of refused.
"""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pytest
from model_conformance import conformance_suite, make_toy, views_of

import volab.walkforward as wf
from volab.errors import ModelSkip
from volab.models.base import Model, TrainingView
from volab.models.iv import har_rv_iv

SOURCE = Path(__file__).resolve().parents[1] / "src" / "volab" / "models" / "iv.py"


def _view(horizon: int) -> TrainingView:
    """A real view from the toy at ``horizon``: the last origin, which has the most history."""
    return views_of(make_toy(horizon, minimum_training=20))[-1]


def _carrying_only(view: TrainingView, rows: int) -> TrainingView:
    """``view`` with ``iv_var_h1`` null on every training row but the latest ``rows``."""
    training = view.training.copy()
    kept = training["iv_var_h1"].where(training.index.isin(training.index[-rows:]))
    return dataclasses.replace(view, training=training.assign(iv_var_h1=kept))


def test_it_declares_the_matched_column_at_each_horizon_and_refuses_h66() -> None:
    domain = har_rv_iv().domain
    assert domain.requires_at(1) == ("iv_var_h1",)
    assert domain.requires_at(5) == ("iv_var_h5",)
    assert domain.requires_at(22) == ("iv_var_h22",)
    assert domain.answers(22)
    assert not domain.answers(66)


def test_it_is_named_and_fits_the_target_side() -> None:
    model = har_rv_iv()
    assert isinstance(model, Model)
    assert model.name == "har_rv_iv"
    assert model.fit_depends_on_horizon is True


def test_it_skips_an_origin_whose_iv_column_is_null() -> None:
    view = _view(1)
    row = view.row.copy()
    row["iv_var_h1"] = float("nan")
    with pytest.raises(ModelSkip, match="iv_var_h1"):
        har_rv_iv().fit(dataclasses.replace(view, row=row))


def test_it_skips_when_too_few_training_rows_carry_the_iv_column() -> None:
    view = _view(1)
    thin = _carrying_only(view, view.minimum_training - 1)
    with pytest.raises(ModelSkip, match="iv_var_h1"):
        har_rv_iv().fit(thin)


def test_it_fits_at_exactly_the_minimum_number_of_carrying_rows() -> None:
    """The floor is ``minimum_training``, inclusive: one row fewer skips, this many fits."""
    view = _view(1)
    carried = view.minimum_training
    forecast = har_rv_iv().fit(_carrying_only(view, carried)).forecast(1)
    assert forecast.observations == carried


def test_the_forecast_is_the_ols_on_the_har_regressors_plus_the_matched_column() -> None:
    """Planted structure: the response is exactly ``3 * iv + 1e-6``, so OLS must recover it.

    The coefficient 3 and the intercept are applied to the column **unscaled**: a contestant that
    rescaled ``iv_var_h5`` (already ``(iv/100)**2 * 5/252``) would not return ``3 * iv + 1e-6``.
    At h=5 so that reading ``iv_var_h1`` instead -- which this test leaves as it was -- is also
    caught.
    """
    view = _view(5)
    training = view.training.assign(**{view.response: 3 * view.training["iv_var_h5"] + 1e-6})
    forecast = har_rv_iv().fit(dataclasses.replace(view, training=training)).forecast(5)
    assert forecast.value == pytest.approx(3 * view.row["iv_var_h5"] + 1e-6, rel=1e-6)


def test_it_reads_only_the_column_matched_to_the_views_horizon() -> None:
    """With the other horizons' columns nulled out entirely, the fit is unchanged."""
    view = _view(5)
    others = [name for name in ("iv_var_h1", "iv_var_h22") if name in view.training.columns]
    assert others
    bare_row = view.row.copy()
    for name in others:
        bare_row[name] = float("nan")
    bare = dataclasses.replace(
        view,
        training=view.training.assign(**{name: float("nan") for name in others}),
        row=bare_row,
    )
    assert har_rv_iv().fit(bare).forecast(5) == har_rv_iv().fit(view).forecast(5)


def test_it_regresses_on_the_har_regressors_and_the_matched_column_through_fit_ols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, ...]] = []
    real = wf.fit_ols

    def spy(frame: pd.DataFrame, regressors: Sequence[str], response: str) -> wf.Fit:
        seen.append(tuple(regressors))
        return real(frame, regressors, response)

    monkeypatch.setattr(wf, "fit_ols", spy)
    har_rv_iv().fit(_view(22))
    assert seen == [(*wf.HAR_REGRESSORS, "iv_var_h22")]


def test_forecasting_a_horizon_the_fit_was_not_made_for_is_a_value_error() -> None:
    fitted = har_rv_iv().fit(_view(1))
    with pytest.raises(ValueError, match="horizon"):
        fitted.forecast(5)


def test_a_view_without_the_matched_column_is_a_value_error_not_a_data_problem() -> None:
    """h=66 has no implied-variance column. ``evaluate`` never asks (the domain refuses), so a
    fit reaching here is wiring, and ``SchemaError`` would file it as an unfittable window."""
    view = dataclasses.replace(_view(1), horizon=66)
    with pytest.raises(ValueError, match="iv_var_h66"):
        har_rv_iv().fit(view)


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
    """All three probes, because ``conformance_suite`` fails a declaration at a horizon no probe
    was built at (clause 10), and h=22 is the one whose toy is thinnest."""
    conformance_suite(
        har_rv_iv(),
        toy=make_toy(1, minimum_training=20),
        rebuild=lambda _: har_rv_iv(),
        also_at=(make_toy(5, minimum_training=20), make_toy(22, minimum_training=20)),
    )
