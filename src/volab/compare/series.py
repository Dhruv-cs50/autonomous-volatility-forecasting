"""``LossSeries``: the one way losses enter the statistics.

``Evaluation`` holds forecasts, per origin and per contestant, with refusals written as NaN and
a QLIKE-undefined origin still present for the loss that is defined there. The statistics need
the opposite: one rectangular matrix of losses, on origins where **every** contestant has one.
``loss_series`` is the only place that conversion happens, and it takes the origin set from
``Evaluation.comparable`` -- the definition the ledger's own means are computed over -- so a
comparison can never rest on a different set of origins from the trial it sits beside.

Witness for the shape: ``test_every_loss_series_is_rectangular_and_on_comparable_origins``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from volab.walkforward import LOSSES, Evaluation


@dataclass(frozen=True)
class LossSeries:
    """Per-origin losses for one ``(symbol, horizon, loss, cohort)``, aligned and rectangular."""

    symbol: str
    horizon: int
    loss: str
    cohort: str
    # The origin stride the run used, which is what the overlap order is derived from.
    stride: int
    # Sorted, and exactly ``evaluation.comparable(loss)``.
    origins: tuple[dt.date, ...]
    # Contestant -> loss at each origin, in the order of ``origins``.
    values: dict[str, tuple[float, ...]]

    @property
    def n(self) -> int:
        return len(self.origins)


def loss_series(evaluation: Evaluation, loss: str, *, cohort: str) -> LossSeries:
    """The loss of every contestant on every comparable origin, as one rectangular series.

    ``cohort`` must be the cohort the evaluation was run over: it reaches the sink as the join
    key, and a label the evaluation does not carry would file a comparison under the wrong name.
    """
    if loss not in LOSSES:
        raise ValueError(f"unknown loss {loss!r}; the losses are {sorted(LOSSES)}")
    if cohort != evaluation.cohort:
        raise ValueError(
            f"cohort {cohort!r} was asked for but the evaluation ran cohort {evaluation.cohort!r}"
        )
    function = LOSSES[loss]
    usable = evaluation.comparable(loss)
    origins = tuple(sorted(usable))
    values: dict[str, tuple[float, ...]] = {}
    for model in evaluation.contestants:
        by_origin = {
            origin: function(realized, forecast)
            for origin, realized, forecast in evaluation.scored(model)
            # Only where the loss is defined for everyone: QLIKE raises on a non-positive forecast,
            # which is how ``comparable`` came to exist.
            if origin in usable
        }
        values[model] = tuple(by_origin[origin] for origin in origins)
    return LossSeries(
        symbol=evaluation.symbol,
        horizon=evaluation.horizon,
        loss=loss,
        cohort=cohort,
        stride=evaluation.every,
        origins=origins,
        values=values,
    )
