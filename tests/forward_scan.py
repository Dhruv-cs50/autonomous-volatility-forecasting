"""The structural forward-look scan, and nothing else.

Not a test module, and **not a defence**. It is a cheap per-commit ratchet against a careless
spelling of a forward peek, and its measured evasions now number four: escape B, the volume
peek, E1's band predicate, and a back-fill spelled invisibly. Phase 2's standing judgement is
that it must never be cited as evidence that a table is point-in-time correct -- see
``tests/test_truncation_equivalence.py``, which is the test that carries that claim.

It lives here rather than inside ``test_point_in_time.py`` so that a test wanting to show a
planted peek is *invisible* to the scan can run the real scanner over its own plant instead of
reasoning about it. A claim of scan-silence checked by hand is exactly the kind of unverified
"cannot" ruling R32 forbids. The scanner's own red cases -- the parametrised list proving each
spelling it does catch -- stay in ``test_point_in_time.py``.
"""

from __future__ import annotations

import ast

# The two calls whose argument is an offset in rows.
OFFSET_CALLS = {"shift", "diff"}
# Calls that take a value from a *later* row. ``ffill`` and ``pad`` take one from an earlier row,
# which is all a trailing feature may do, so they are deliberately absent.
BACKWARD_FILL_CALLS = {"bfill", "backfill", "interpolate"}
# Calls whose ``method=`` chooses which direction a fill runs in.
FILL_METHOD_CALLS = {"fillna", "reindex", "asfreq", "align", "resample"}
# The only two ``method=`` values that fill from the past. ``build_features`` passes no method at
# all -- its ``reindex`` leaves a gap session null and its ``fillna`` takes an explicit series.
TRAILING_METHODS = {"ffill", "pad"}
# ``rolling(window, min_periods, center, ...)``: ``center`` is reachable positionally, so a
# keyword-only rule waves ``rolling(5, 5, True)`` straight through. Measured on pandas 3.0.6.
_ROLLING_CENTER_POSITION = 2


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_trailing_offset(offset: ast.expr) -> bool:
    """Whether ``offset`` is provably a non-negative integer literal.

    An allow-list, not a list of forbidden spellings, because the forbidden ones cannot be
    enumerated: ``shift(-1)`` is only the obvious one, and ``shift(-n)``, ``shift(0 - 1)``,
    ``shift(~0)`` and ``shift(lag)`` are the same leak written differently. A scan that flagged a
    unary minus would wave every other one through, so anything that is not a literal ``0``,
    ``1``, ``2``, ... is flagged and the author has to come and say why.
    """
    return (
        isinstance(offset, ast.Constant)
        and isinstance(offset.value, int)
        and not isinstance(offset.value, bool)
        and offset.value >= 0
    )


def _is_false(value: ast.expr) -> bool:
    return isinstance(value, ast.Constant) and not value.value


def _is_trailing_method(value: ast.expr) -> bool:
    """Whether a ``method=`` provably fills from earlier rows, or does not fill at all."""
    return isinstance(value, ast.Constant) and (
        value.value is None or value.value in TRAILING_METHODS
    )


def _forward_looks(source: str) -> list[str]:
    """Every syntactic way ``source`` could read a value from after the row being computed."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Slice):
            # No module in the strict half of this package slices anything, so the rule is "no
            # slicing" rather than "no reversing": a forward window is spelled ``values[i + 1:]``
            # and a reversal ``values[::-1]``, and a step-only rule would miss the first.
            found.append(f"slices a series with [{ast.unparse(node)}]")
            continue
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node)
        if name in BACKWARD_FILL_CALLS:
            # A back-fill is the subtlest forward peek of all: it reads a *later* row's value and
            # presents it as this row's own, so a gap session's close would be bridged from the
            # session after it and a future price would reach log_returns directly.
            found.append(f"fills from later rows with {name}()")
        if name == "rolling" and len(node.args) > _ROLLING_CENTER_POSITION:
            positional = node.args[_ROLLING_CENTER_POSITION]
            if not _is_false(positional):
                found.append(
                    f"centres a window with rolling's third argument {ast.unparse(positional)}"
                )
        for keyword in node.keywords:
            if keyword.arg == "center" and not _is_false(keyword.value):
                found.append(f"centres a window with center={ast.unparse(keyword.value)}")
            if (
                keyword.arg == "method"
                and name in FILL_METHOD_CALLS
                and not _is_trailing_method(keyword.value)
            ):
                found.append(
                    f"fills from later rows with {name}(method={ast.unparse(keyword.value)})"
                )
            if (
                keyword.arg == "periods"
                and name in OFFSET_CALLS
                and not _is_trailing_offset(keyword.value)
            ):
                found.append(f"offsets with {name}(periods={ast.unparse(keyword.value)})")
        if name in OFFSET_CALLS:
            found.extend(
                f"offsets with {name}({ast.unparse(argument)})"
                for argument in node.args
                if not _is_trailing_offset(argument)
            )
    return found
