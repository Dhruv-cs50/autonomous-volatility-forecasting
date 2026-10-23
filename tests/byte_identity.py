"""Byte-exact table comparison, shared by every point-in-time test in this suite.

Not a test module. It lives here rather than in ``test_point_in_time.py`` because Phase 2's
truncation-equivalence tests compare tables too, and a second comparator written beside them
would be free to be weaker -- which is the whole failure mode this project exists to refuse. One
comparator, one set of red cases proving it can fail, in ``test_point_in_time.py``.

Approximate comparison is what lets a leak the size of a rounding error through, so nothing here
has a tolerance.
"""

from __future__ import annotations

import pandas as pd


def canonical_bytes(series: pd.Series) -> bytes:
    """``series``' values as bytes, canonically, so two independently built frames can compare.

    Raw buffer bytes wherever there is one: a NaN payload, a ``-0.0`` and a value differing in
    the last place all read as different, which is the point.

    Object arrays need the exception, and the probe that established it is worth recording.
    ``to_numpy().tobytes()`` on an object array is a list of **pointers** -- equal only when both
    frames hold the very same Python objects, which two independent builds never do. Under pandas
    3 a string column materialises a fresh ``str`` per call, a tz-aware column fresh
    ``Timestamp``s, and ``as_of`` holds ``datetime.date`` objects rebuilt by every
    ``sessions_between``; all four compared unequal between two builds of one identical panel. So
    timestamps go through their integer buffer (the dtype check below covers the timezone), a
    nullable boolean through its values buffer *and* its null mask, and the remaining object
    columns -- ``symbol`` and ``as_of`` -- through ``repr``, which for strings and dates is exact
    and also catches a date silently becoming a ``Timestamp``.
    """
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        # ``tz_convert``, never ``tz_localize``: the latter yields *wall-clock* time, so two
        # instants an hour apart inside a DST fall-back hour would compare byte-equal under an
        # identical dtype. ``build_features`` only requires ``ts`` to be timezone-aware, not UTC.
        return series.dt.tz_convert(None).to_numpy().tobytes()
    if series.dtype == "boolean":
        values = series.to_numpy(dtype=bool, na_value=False)
        return values.tobytes() + series.isna().to_numpy().tobytes()
    if series.dtype == "float64":
        return series.to_numpy().tobytes()
    return repr(list(series)).encode()


def assert_byte_identical(left: pd.DataFrame, right: pd.DataFrame) -> None:
    """The two tables are the same table: same columns, same dtypes, same bytes, no tolerance."""
    assert list(left.columns) == list(right.columns), "the columns themselves moved"
    assert list(left.index) == list(right.index), "the row index moved"
    for column in left.columns:
        assert left[column].dtype == right[column].dtype, f"{column}: dtype moved"
        assert canonical_bytes(left[column]) == canonical_bytes(right[column]), (
            f"{column}: values moved"
        )


def first_difference(left: pd.DataFrame, right: pd.DataFrame) -> str | None:
    """Where ``left`` and ``right`` first disagree, described, or ``None`` if they do not.

    The reporting counterpart to ``assert_byte_identical``, for a sweep that must survey many
    cutoffs and then say how many disagreed rather than stopping at the first one. It uses the
    same canonicaliser, so the two can never answer differently about one pair of tables -- a
    sweep that reported a disagreement the assertion would not see, or missed one it would,
    would be the sort of near-miss this phase keeps finding.

    The row it names is located by value comparison rather than by bytes, because bytes are a
    whole-column digest and "which row" is the first thing anyone reading a failure wants.
    """
    if list(left.columns) != list(right.columns):
        return f"columns differ: {list(left.columns)} vs {list(right.columns)}"
    if len(left) != len(right):
        return f"row counts differ: {len(left)} vs {len(right)}"
    for column in left.columns:
        if left[column].dtype != right[column].dtype:
            return f"{column}: dtype {left[column].dtype} vs {right[column].dtype}"
        if canonical_bytes(left[column]) == canonical_bytes(right[column]):
            continue
        a = left[column].reset_index(drop=True)
        b = right[column].reset_index(drop=True)
        for position in range(len(a)):
            one, two = a.iloc[position], b.iloc[position]
            if one == two or (pd.isna(one) and pd.isna(two)):
                continue
            return f"{column} row {position}: {one!r} vs {two!r}"
        # Byte-unequal with no value-unequal row: a real difference the eye cannot see, such as
        # a NaN payload or a -0.0. Reported as such rather than swallowed.
        return f"{column}: byte-unequal although every value compares equal"
    return None
