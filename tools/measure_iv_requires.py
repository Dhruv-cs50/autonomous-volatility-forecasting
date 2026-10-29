"""What a VIX1D-requiring contestant costs every other contestant, per horizon (P3-10).

Reproduces the table in section 4 of the Phase 3 design
(``docs/superpowers/specs/2026-10-29-phase3-iv-and-decision-evaluators-design.md``) that the
horizon-dependent ``Domain.per_horizon`` rests on: of the origins a run requests, how many
``wf.coverage_refusal`` says precede the first one a declaration can be fitted at, under

* ``requires=("iv_h1",)`` -- VIX1D, read at every horizon, and
* the horizon-matched column ``har_rv_iv`` actually declares (``iv_var_h1``, ``iv_var_h5``,
  ``iv_var_h22``), taken from ``volab.models.iv.DOMAIN`` and not respelled here.

Each origin lost is lost to **every** contestant in the cohort (``Evaluation.comparable``
intersects them out), which is why the count matters. The answer the design reasons from is that
the trade-off exists at h=1 and nowhere else: VIX1D begins 2022-05-13, VIX9D and VIX in 2016.

It reads the committed store through ``wf.load_for`` and the CLI's own persisted-table read, both
of which stop at ``dev_end``, and calls no ``evaluate``, so it **appends nothing to the trial
ledger**. **It reads the count out of ``coverage_refusal``'s message text** (``"... and N of the M
requested origins precede it"``), the only place the function reports it, so rewording that message
breaks this probe: it raises rather than printing a wrong figure. Reword the two together.
A horizon with no matched column (h=66, which ``har_rv_iv`` does not answer)
prints ``n/a``. When a figure here disagrees with the spec, that is a finding about the store or
the code, to be re-measured and recorded, not a reason to adjust this script until it matches.

    uv run --locked python tools/measure_iv_requires.py
"""

from __future__ import annotations

import datetime as dt
import re

from _harness import heading, note

import volab.compare.__main__ as compare_cli
import volab.walkforward as wf
from volab.config import load_settings
from volab.features.targets import HORIZONS
from volab.models.iv import DOMAIN
from volab.panel import as_of_dates

SYMBOL = "SPY"
WINDOW_FIRST = dt.date(2021, 1, 4)  # the shootout's own default window
EVERY = 5
MINIMUM = 250
HORIZON_BLIND = "iv_h1"

_LOST = re.compile(r"first that qualifies is (\d{4}-\d{2}-\d{2}), and (\d+) of the (\d+) requested")


def _lost(reason: str | None, requested: int) -> str:
    """Origins lost: ``"119 of 251"``, ``"none"`` if not refused, ``"all 251"`` if none qualify."""
    if reason is None:
        return "none"
    found = _LOST.search(reason)
    if found is None:
        if f"none of the {requested} requested origins qualifies" in reason:
            return f"all {requested}"
        raise RuntimeError(f"a refusal this probe cannot read: {reason}")
    return f"{found.group(2)} of {found.group(3)} (first runnable {found.group(1)})"


def main() -> int:
    settings = load_settings()
    note(f"storage root {settings.storage_root}")
    note(f"reads stop at dev_end {settings.splits.dev_end}")
    stored = compare_cli._persisted(settings, SYMBOL)
    if stored is None:
        print("the store holds no built feature table; run `python -m volab.features build`")
        return 1
    note(f"{SYMBOL} stored table {len(list(as_of_dates(stored)))} sessions")

    heading(
        f"origins lost: {SYMBOL} from {WINDOW_FIRST}, every {EVERY}, minimum_training {MINIMUM}"
    )
    for horizon in HORIZONS:
        _, targets = wf.load_for(settings, SYMBOL, horizon)
        origins = wf.scoreable_origins(
            settings, targets, first=WINDOW_FIRST, last=settings.splits.dev_end, every=EVERY
        )
        blind = wf.coverage_refusal(
            stored, targets, (HORIZON_BLIND,), origins, horizon=horizon, minimum_training=MINIMUM
        )
        matched = DOMAIN.requires_at(horizon) if DOMAIN.answers(horizon) else ()
        print(f"h={horizon:<2} requested {len(origins)}")
        print(f"      requires=({HORIZON_BLIND!r},): {_lost(blind, len(origins))}")
        if matched:
            reason = wf.coverage_refusal(
                stored, targets, matched, origins, horizon=horizon, minimum_training=MINIMUM
            )
            print(f"      matched {matched[0]}: {_lost(reason, len(origins))}")
        else:
            print("      matched: n/a (har_rv_iv does not answer this horizon)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
