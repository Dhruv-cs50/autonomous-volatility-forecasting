"""M3 -- can cross-call state hide outside the feature package? Yes: on the calendar object.

Reproduces the measurement recorded as **M3** in ``docs/superpowers/phase2-decisions.md``, which
is what ruling **R37** rests on: **E2's class is closed by subprocess isolation, not by an AST
ban on module-level mutable state in ``src/volab/features/``.**

The ban is sound about the construct it names. It is the *scope* that fails. ``get_calendar`` is
``lru_cache``d, so every caller is handed the **same** calendar object, and that object accepts
arbitrary attributes although its class declares ``__slots__ = ()`` -- the slots are empty and
the class still carries a ``__dict__``. A memo written by ``build_features`` onto the calendar it
was handed therefore survives into the next call with **no module-level name in the package at
all**, and widening the ban to "every object any feature function can reach" is no longer an AST
question. Subprocess isolation is behavioural and does not care where the state was parked.

The standing witness for the same property is
``test_a_memo_on_the_calendar_defeats_a_package_scoped_ast_ban``, whose failure would mean the
cheaper mechanism had become sufficient (R43). This script is the log's quoted output.

    uv run --locked python tools/measure_calendar_memo.py
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from _harness import heading, note

from volab.config import load_settings
from volab.sessions import get_calendar

# An attribute name no calendar class defines, so a success here is the memo and not a property.
MEMO = "scale"
VALUE = 1.234


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    settings = load_settings()
    note(f"calendar {settings.calendar}")
    first = get_calendar(settings)
    assert not hasattr(first, MEMO), f"{type(first).__name__} already defines {MEMO!r}"

    heading("M3: WHERE E2 CAN HIDE")
    setattr(first, MEMO, VALUE)
    print(f"attribute set on calendar: OK -> {{{MEMO!r}: {getattr(first, MEMO)}}}")
    second = get_calendar(settings)
    print(f"same object from a second get_calendar: {first is second}")
    print(f"memo survives the second call: {{{MEMO!r}: {getattr(second, MEMO)}}}")
    print(f"__slots__? {getattr(type(first), '__slots__', None)!r}")

    # Left as it was found. ``get_calendar`` is cached process-wide and a probe that walked away
    # leaving a memo on the shared calendar would be doing E2 for real.
    delattr(first, MEMO)
    note(f"memo removed; hasattr now {hasattr(get_calendar(settings), MEMO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
