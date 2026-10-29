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
from volab.errors import ModelSkip, SchemaError
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


def _usable_only(view: TrainingView, rows: int) -> TrainingView:
    """``view`` with ``iv_var_h1`` carried on **every** training row but ``har_d_proxy`` null on all
    but the latest ``rows``, so the IV column is present far more often than a row is usable."""
    training = view.training.copy()
    kept = training["har_d_proxy"].where(training.index.isin(training.index[-rows:]))
    return dataclasses.replace(view, training=training.assign(har_d_proxy=kept))


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
    """Covers a case conformance cannot: clause 10 nulls the column in the row and the training
    frame at once, so it cannot tell this check from the thin-training one below."""
    view = _view(1)
    row = view.row.copy()
    row["iv_var_h1"] = float("nan")
    with pytest.raises(ModelSkip, match="iv_var_h1 is null at the origin"):
        har_rv_iv().fit(dataclasses.replace(view, row=row))


def test_it_skips_when_too_few_training_rows_carry_the_iv_column() -> None:
    view = _view(1)
    thin = _carrying_only(view, view.minimum_training - 1)
    with pytest.raises(ModelSkip, match=r"19 training row\(s\) .* carry iv_var_h1 together"):
        har_rv_iv().fit(thin)


def test_the_floor_counts_rows_fit_ols_can_use_and_not_rows_that_merely_carry_the_iv_column() -> (
    None
):
    """``iv_var_h1`` is present on every training row here, so a count of those alone is far above
    the floor; but ``har_d_proxy`` is null on all except ``minimum_training - 1`` of them, so that
    many are usable. The skip is the usable count's. One more usable row fits."""
    view = _view(1)
    assert view.training["iv_var_h1"].notna().all()
    with pytest.raises(ModelSkip, match=r"19 training row\(s\) .* carry iv_var_h1 together"):
        har_rv_iv().fit(_usable_only(view, view.minimum_training - 1))
    fitted = har_rv_iv().fit(_usable_only(view, view.minimum_training))
    assert fitted.observations == view.minimum_training


def test_a_constant_iv_column_is_this_contestants_skip_and_not_the_cohorts_schema_error() -> None:
    """A constant column is collinear with the intercept, so the system is singular, but the HAR
    regressors alone fit the same window. ``SchemaError`` would skip the origin for every
    contestant (R87); the cause is a column only this contestant reads, so it is a ``ModelSkip``."""
    view = _view(1)
    constant = dataclasses.replace(view, training=view.training.assign(iv_var_h1=1e-4))
    wf.fit_ols(constant.training, wf.HAR_REGRESSORS, constant.response)  # the HAR alone fits
    with pytest.raises(ModelSkip, match="iv_var_h1 at .* unfittable although the HAR"):
        har_rv_iv().fit(constant)


def test_a_column_collinear_with_a_har_regressor_is_also_this_contestants_skip() -> None:
    """The same conversion for collinearity rather than constancy: ``iv_var_h1`` planted as exactly
    twice ``har_w_proxy``, on a window the HAR regressors alone still fit."""
    view = _view(1)
    training = view.training.assign(iv_var_h1=2 * view.training["har_w_proxy"])
    collinear = dataclasses.replace(view, training=training)
    wf.fit_ols(training, wf.HAR_REGRESSORS, view.response)  # the HAR alone fits
    with pytest.raises(ModelSkip, match="iv_var_h1 at .* unfittable although the HAR"):
        har_rv_iv().fit(collinear)


def test_a_window_the_har_regressors_cannot_fit_stays_a_schema_error() -> None:
    """The conversion is not an over-catch: when the HAR alone is singular the window is
    unfittable for everyone, and that is the window's ``SchemaError`` to raise."""
    view = _view(1)
    constant = dataclasses.replace(view, training=view.training.assign(har_d_proxy=1e-4))
    with pytest.raises(SchemaError, match="singular"):
        har_rv_iv().fit(constant)


def test_it_fits_at_exactly_the_minimum_number_of_carrying_rows() -> None:
    """The floor is ``minimum_training``, inclusive: one row fewer skips, this many fits. Here the
    rows carrying the IV column are also the rows usable by ``fit_ols``; the two counts are not
    the same thing (on the toy's last view 140 rows carry it and 96 are usable), and the one that
    is counted is pinned by the test above."""
    view = _view(1)
    carried = view.minimum_training
    forecast = har_rv_iv().fit(_carrying_only(view, carried)).forecast(1)
    assert forecast.observations == carried


def test_the_forecast_is_the_ols_on_the_har_regressors_plus_the_matched_column() -> None:
    """Planted structure: the response is exactly ``3 * iv + 1e-6``, so OLS must recover it from
    the column matched to the view's horizon. At h=5, so that reading ``iv_var_h1`` instead -- left
    as it was -- is caught. This does **not** witness the absence of a rescale: a consistent one
    leaves the forecast unchanged (see the test below)."""
    view = _view(5)
    training = view.training.assign(**{view.response: 3 * view.training["iv_var_h5"] + 1e-6})
    forecast = har_rv_iv().fit(dataclasses.replace(view, training=training)).forecast(5)
    assert forecast.value == pytest.approx(3 * view.row["iv_var_h5"] + 1e-6, rel=1e-6)


def test_the_matched_column_reaches_the_regression_untransformed_on_both_sides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OLS with an intercept is scale-equivariant, so a rescale applied to the training frame
    **and** the row alike changes no forecast and cannot be witnessed by one. What can be pinned
    is identity: the frame handed to ``fit_ols`` carries the column exactly as the view does, and
    the forecast is that fit evaluated at the view's own row, equal to an independent fit on the
    untouched view. A one-sided or non-linear transform fails the second; any transform of the
    column fails the first."""
    view = _view(5)
    handed: list[pd.DataFrame] = []
    real = wf.fit_ols

    def spy(frame: pd.DataFrame, regressors: Sequence[str], response: str) -> wf.Fit:
        handed.append(frame)
        return real(frame, regressors, response)

    monkeypatch.setattr(wf, "fit_ols", spy)
    forecast = har_rv_iv().fit(view).forecast(5)
    monkeypatch.undo()

    assert len(handed) == 1
    assert handed[0]["iv_var_h5"].equals(view.training["iv_var_h5"])
    independent = real(view.training, (*wf.HAR_REGRESSORS, "iv_var_h5"), view.response)
    assert forecast.value == independent.predict(view.row)


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
    with pytest.raises(ValueError, match="asked to forecast horizon 5"):
        fitted.forecast(5)


def test_a_view_without_the_matched_column_is_a_value_error_not_a_data_problem() -> None:
    """h=66 has no implied-variance column. ``evaluate`` never asks (the domain refuses), so a
    fit reaching here is wiring, and ``SchemaError`` would file it as an unfittable window."""
    view = dataclasses.replace(_view(1), horizon=66)
    with pytest.raises(ValueError, match="has no 'iv_var_h66' in the view"):
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
