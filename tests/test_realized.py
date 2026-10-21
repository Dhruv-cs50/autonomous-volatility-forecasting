"""Trailing realized-measure proxies and HAR components, against hand-computed values.

Every function here is a pure map from a return series to a same-length series, so the tests
pin it the same way: a short series whose every output can be written out by hand, asserted at
*every* index. Asserting only the last element of a window would let a window that is one
session too long or too short slip through, because the sums of adjacent windows differ in
exactly one term -- so each test below checks the full output, nulls included.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from volab.features.har import har_components
from volab.features.realized import (
    bipower_proxy,
    rv_proxy,
    semivariance_proxy,
    signed_jump_proxy,
)

NAN = float("nan")

# Signs alternate and magnitudes are all distinct, so no two windows share a sum and a window
# that is one session off cannot land on the right answer by accident.
R = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02])


def _expect(expected: list[float]) -> object:
    """``pytest.approx`` that treats a NaN as equal to a NaN, so nulls are asserted too."""
    return pytest.approx(expected, nan_ok=True, abs=1e-15)


def _brute_rv(values: list[float], window: int) -> list[float]:
    """The reference: a plain-Python trailing sum, sharing no code path with pandas rolling."""
    return [
        NAN if i + 1 < window else sum(v * v for v in values[i + 1 - window : i + 1])
        for i in range(len(values))
    ]


# --- rv_proxy -------------------------------------------------------------------------------


def test_rv_proxy_window_1_is_the_squared_return() -> None:
    out = rv_proxy(R, window=1)
    assert list(out) == _expect([0.0001, 0.0004, 0.0009, 0.0001, 0.0004])


def test_rv_proxy_window_3_sums_the_trailing_three() -> None:
    out = rv_proxy(R, window=3)
    assert list(out) == _expect(
        [
            NAN,
            NAN,
            0.01**2 + 0.02**2 + 0.03**2,
            0.02**2 + 0.03**2 + 0.01**2,
            0.03**2 + 0.01**2 + 0.02**2,
        ]
    )


def test_rv_proxy_is_nan_before_the_window_fills() -> None:
    out = rv_proxy(R, window=3)
    assert out.iloc[:2].isna().all()
    assert out.iloc[2:].notna().all()


def test_rv_proxy_preserves_length_and_index() -> None:
    indexed = pd.Series(R.to_numpy(), index=pd.RangeIndex(10, 15), name="r")
    out = rv_proxy(indexed, window=3)
    assert out.index.equals(indexed.index)
    assert len(out) == len(indexed)


def test_rv_proxy_matches_a_brute_force_sum_at_every_index_and_window() -> None:
    values = [0.011, -0.023, 0.031, -0.007, 0.019, -0.041, 0.005, 0.013, -0.029, 0.017]
    for window in (1, 2, 3, 5, 10):
        assert list(rv_proxy(pd.Series(values), window=window)) == _expect(
            _brute_rv(values, window)
        )


def test_rv_proxy_at_a_row_does_not_depend_on_later_rows() -> None:
    # The trailing-only property, tested behaviourally: truncating the series after row k must
    # leave rows 0..k exactly as they were, and perturbing a later row must not move them.
    full = rv_proxy(R, window=3)
    assert list(rv_proxy(R.iloc[:4], window=3)) == _expect(list(full.iloc[:4]))
    bumped = R.copy()
    bumped.iloc[4] = 9.0
    assert list(rv_proxy(bumped, window=3).iloc[:4]) == _expect(list(full.iloc[:4]))
    # ...and the perturbed row itself does move, so the check above could have failed.
    assert rv_proxy(bumped, window=3).iloc[4] != pytest.approx(full.iloc[4])


def test_a_nan_return_propagates_to_its_windows() -> None:
    # A null return makes every window containing it null -- never a sum over the rest.
    r = pd.Series([0.01, NAN, 0.03, -0.01, 0.02, 0.04])
    out = rv_proxy(r, window=2)
    assert list(out) == _expect(
        [NAN, NAN, NAN, 0.03**2 + 0.01**2, 0.01**2 + 0.02**2, 0.02**2 + 0.04**2]
    )
    # The null clears exactly `window` rows after it, no earlier and no later.
    assert out.iloc[3:].notna().all()


def test_window_longer_than_the_series_is_all_nan() -> None:
    assert rv_proxy(R, window=6).isna().all()
    assert rv_proxy(R, window=100).isna().all()
    assert semivariance_proxy(R, window=6, sign="neg").isna().all()
    assert signed_jump_proxy(R, window=6).isna().all()
    assert bipower_proxy(R, window=6).isna().all()


def test_an_empty_series_gives_an_empty_series() -> None:
    assert len(rv_proxy(pd.Series([], dtype="float64"), window=3)) == 0


@pytest.mark.parametrize("window", [0, -1, -22])
def test_a_window_below_1_is_rejected(window: int) -> None:
    with pytest.raises(ValueError, match="window"):
        rv_proxy(R, window=window)
    with pytest.raises(ValueError, match="window"):
        semivariance_proxy(R, window=window, sign="neg")
    with pytest.raises(ValueError, match="window"):
        signed_jump_proxy(R, window=window)


# --- semivariance_proxy ---------------------------------------------------------------------


def test_semivariance_neg_keeps_only_negative_returns() -> None:
    # Window 3 at i=2 holds (0.01, -0.02, 0.03): only the -0.02 survives.
    out = semivariance_proxy(R, window=3, sign="neg")
    assert list(out) == _expect([NAN, NAN, 0.02**2, 0.02**2 + 0.01**2, 0.01**2])


def test_semivariance_pos_keeps_only_positive_returns() -> None:
    out = semivariance_proxy(R, window=3, sign="pos")
    assert list(out) == _expect([NAN, NAN, 0.01**2 + 0.03**2, 0.03**2, 0.03**2 + 0.02**2])


def test_semivariance_pos_and_neg_sum_to_rv() -> None:
    for window in (1, 2, 3, 5):
        pos = semivariance_proxy(R, window=window, sign="pos")
        neg = semivariance_proxy(R, window=window, sign="neg")
        total = rv_proxy(R, window=window)
        assert list(pos + neg) == _expect(list(total))


def test_a_zero_return_belongs_to_neither_semivariance_and_costs_nothing() -> None:
    r = pd.Series([0.0, -0.02, 0.0, 0.03])
    assert list(semivariance_proxy(r, window=2, sign="neg")) == _expect(
        [NAN, 0.02**2, 0.02**2, 0.0]
    )
    assert list(semivariance_proxy(r, window=2, sign="pos")) == _expect([NAN, 0.0, 0.0, 0.03**2])


def test_semivariance_nan_return_propagates_and_is_not_read_as_a_non_negative() -> None:
    # The easy bug: `r < 0` is False for NaN, so a naive mask turns a null into a zero and the
    # window sums over the remaining rows instead of going null.
    r = pd.Series([-0.01, NAN, 0.03, -0.02])
    assert list(semivariance_proxy(r, window=2, sign="neg")) == _expect([NAN, NAN, NAN, 0.02**2])
    assert list(semivariance_proxy(r, window=2, sign="pos")) == _expect([NAN, NAN, NAN, 0.03**2])


def test_semivariance_rejects_an_unknown_sign() -> None:
    with pytest.raises(ValueError, match="sign"):
        semivariance_proxy(R, window=3, sign="up")  # type: ignore[arg-type]


# --- signed_jump_proxy ----------------------------------------------------------------------


def test_signed_jump_is_pos_minus_neg() -> None:
    out = signed_jump_proxy(R, window=3)
    assert list(out) == _expect(
        [
            NAN,
            NAN,
            (0.01**2 + 0.03**2) - 0.02**2,
            0.03**2 - (0.02**2 + 0.01**2),
            (0.03**2 + 0.02**2) - 0.01**2,
        ]
    )


def test_signed_jump_is_negative_when_downside_dominates() -> None:
    r = pd.Series([-0.05, 0.01, -0.04])
    assert signed_jump_proxy(r, window=3).iloc[2] == pytest.approx(0.01**2 - 0.05**2 - 0.04**2)
    assert signed_jump_proxy(r, window=3).iloc[2] < 0


# --- bipower_proxy --------------------------------------------------------------------------


def test_bipower_proxy_matches_the_hand_computation() -> None:
    # Window 3 at i=2 spans two adjacent pairs: (0.01, -0.02) and (-0.02, 0.03).
    out = bipower_proxy(R, window=3)
    assert list(out) == _expect(
        [
            NAN,
            NAN,
            (math.pi / 2) * (abs(0.01 * -0.02) + abs(-0.02 * 0.03)),
            (math.pi / 2) * (abs(-0.02 * 0.03) + abs(0.03 * -0.01)),
            (math.pi / 2) * (abs(0.03 * -0.01) + abs(-0.01 * 0.02)),
        ]
    )


def test_bipower_proxy_window_2_is_one_adjacent_pair() -> None:
    out = bipower_proxy(R, window=2)
    assert list(out) == _expect(
        [
            NAN,
            (math.pi / 2) * abs(0.01 * -0.02),
            (math.pi / 2) * abs(-0.02 * 0.03),
            (math.pi / 2) * abs(0.03 * -0.01),
            (math.pi / 2) * abs(-0.01 * 0.02),
        ]
    )


def test_bipower_proxy_never_pairs_a_return_with_a_later_one() -> None:
    # Pairs are (t-1, t), so row t cannot see row t+1: bumping the last return leaves the rest.
    bumped = R.copy()
    bumped.iloc[4] = 9.0
    assert list(bipower_proxy(bumped, window=3).iloc[:4]) == _expect(
        list(bipower_proxy(R, window=3).iloc[:4])
    )


def test_bipower_proxy_nan_return_propagates_to_the_pairs_it_touches() -> None:
    r = pd.Series([0.01, -0.02, NAN, -0.01, 0.02, 0.03])
    out = bipower_proxy(r, window=2)
    # NaN at row 2 poisons the pairs (1,2) and (2,3), i.e. rows 2 and 3, and nothing else.
    assert list(out) == _expect(
        [
            NAN,
            (math.pi / 2) * abs(0.01 * -0.02),
            NAN,
            NAN,
            (math.pi / 2) * abs(-0.01 * 0.02),
            (math.pi / 2) * abs(0.02 * 0.03),
        ]
    )


@pytest.mark.parametrize("window", [1, 0, -3])
def test_bipower_proxy_rejects_window_below_2(window: int) -> None:
    with pytest.raises(ValueError, match="window"):
        bipower_proxy(R, window=window)


# --- har_components -------------------------------------------------------------------------


def test_har_components_have_the_three_named_keys() -> None:
    assert list(har_components(R)) == ["har_d", "har_w", "har_m"]


def test_har_d_is_the_squared_return_itself() -> None:
    assert list(har_components(R)["har_d"]) == _expect([0.0001, 0.0004, 0.0009, 0.0001, 0.0004])


def test_har_components_are_means_not_sums() -> None:
    # Six returns, so har_w is defined at rows 4 and 5 and the two means differ in one term.
    r = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02, 0.04])
    har_w = har_components(r)["har_w"]
    assert list(har_w) == _expect(
        [
            NAN,
            NAN,
            NAN,
            NAN,
            (0.01**2 + 0.02**2 + 0.03**2 + 0.01**2 + 0.02**2) / 5,
            (0.02**2 + 0.03**2 + 0.01**2 + 0.02**2 + 0.04**2) / 5,
        ]
    )
    # The mean is the rv_proxy sum over the same window, divided by its length.
    assert har_w.iloc[5] == pytest.approx(rv_proxy(r, window=5).iloc[5] / 5)


def test_har_m_is_the_mean_over_22_sessions_and_nan_before_that() -> None:
    values = [0.001 * (i + 1) * (-1) ** i for i in range(25)]
    har_m = har_components(pd.Series(values))["har_m"]
    assert har_m.iloc[:21].isna().all()
    # Rows 21, 22, 23 and 24 each differ from their neighbour by one dropped and one added term.
    for i in range(21, 25):
        assert har_m.iloc[i] == pytest.approx(sum(v * v for v in values[i - 21 : i + 1]) / 22)
    assert har_m.iloc[21] != pytest.approx(har_m.iloc[22])


def test_har_w_first_value_lands_on_row_4_not_5_or_3() -> None:
    har_w = har_components(R)["har_w"]
    assert list(har_w.isna()) == [True, True, True, True, False]


def test_har_components_nan_return_propagates_and_is_not_averaged_over_the_rest() -> None:
    r = pd.Series([0.01, -0.02, NAN, -0.01, 0.02, 0.04, 0.01, -0.03, 0.02])
    har_w = har_components(r)["har_w"]
    # Windows containing row 2 are rows 2..6; row 7 is the first clean one.
    assert har_w.iloc[:7].isna().all()
    assert har_w.iloc[7] == pytest.approx((0.01**2 + 0.02**2 + 0.04**2 + 0.01**2 + 0.03**2) / 5)


def test_har_components_all_nan_when_the_series_is_shorter_than_every_window() -> None:
    out = har_components(pd.Series([0.01, -0.02, 0.03]))
    assert out["har_d"].notna().all()
    assert out["har_w"].isna().all()
    assert out["har_m"].isna().all()


def test_har_components_preserve_index_and_length() -> None:
    indexed = pd.Series(R.to_numpy(), index=pd.RangeIndex(7, 12))
    for component in har_components(indexed).values():
        assert component.index.equals(indexed.index)
