"""The contestant's declared domain (US-3.9a): what it can answer, and what the cohort derives.

The declaration cannot be checked against behaviour. EGARCH at h=22 returns a finite, ordinary-
looking ``1.70e+12`` -- which is why R114 demanded an a-priori refusal -- so nothing here tests
that a bound is *right*. What these witnesses hold is that a declaration cannot be omitted, cannot
be expressed wrongly (``GarchModel`` derives it), and is acted on before any fit.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest
from model_conformance import SYMBOL, Spy, Toy, make_toy, run_cohort

from volab.features.build import build_features
from volab.features.targets import HORIZONS
from volab.models import base
from volab.models.base import UNRESTRICTED, Cohort, Domain, Measurement, Model, Sampling
from volab.sessions import get_calendar
from volab.walkforward import (
    ConfigurationOutcome,
    Trial,
    coverage_refusal,
    evaluate,
    run_configuration,
    scoreable_origins,
)


class _Declares:
    """A contestant that declares nothing but a name and a domain."""

    fit_depends_on_horizon = False

    def __init__(self, name: str, domain: Domain) -> None:
        self.name = name
        self.domain = domain

    def fit(self, view: object) -> object:
        raise AssertionError("a declaration is read, never fitted")


def _cohort(*domains: Domain, name: str = "c") -> Cohort:
    members = tuple(_Declares(f"m{i}", domain) for i, domain in enumerate(domains))
    return Cohort(name, members)  # type: ignore[arg-type]


def test_the_default_domain_answers_every_horizon_and_requires_nothing() -> None:
    assert Domain() == UNRESTRICTED
    assert UNRESTRICTED.max_horizon is None and UNRESTRICTED.requires == ()
    assert UNRESTRICTED.sampling is None
    assert all(UNRESTRICTED.answers(horizon) for horizon in (1, 5, 22, 66, 10_000))


def test_a_bounded_domain_answers_a_prefix_of_the_horizons() -> None:
    domain = Domain(max_horizon=5)
    assert [domain.answers(horizon) for horizon in HORIZONS] == [True, True, False, False]


def test_a_domain_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        UNRESTRICTED.max_horizon = 1  # type: ignore[misc]


def test_a_model_without_a_domain_is_not_a_model() -> None:
    """The declaration is a required protocol member, so it cannot be omitted."""

    class NoDomain:
        name = "no_domain"
        fit_depends_on_horizon = False

        def fit(self, view: object) -> object:
            raise AssertionError

    assert not isinstance(NoDomain(), Model)
    assert isinstance(_Declares("declares", UNRESTRICTED), Model)


def test_a_cohort_is_refused_at_a_horizon_some_member_cannot_answer_naming_the_member() -> None:
    cohort = _cohort(UNRESTRICTED, Domain(max_horizon=1), name="the-cohort")
    assert cohort.refusal(1) is None
    reason = cohort.refusal(22)
    assert reason is not None
    for fact in ("the-cohort", "'m1'", "22", "h<=1"):
        assert fact in reason, fact
    assert "'m0'" not in reason


def test_the_refusal_names_every_member_that_cannot_answer() -> None:
    cohort = _cohort(Domain(max_horizon=1), Domain(max_horizon=5), UNRESTRICTED)
    reason = cohort.refusal(22)
    assert reason is not None and "'m0'" in reason and "'m1'" in reason
    only_first = cohort.refusal(5)
    assert only_first is not None and "'m0'" in only_first and "'m1'" not in only_first


def test_the_cohort_horizons_are_those_every_member_answers_in_the_configured_order() -> None:
    assert _cohort(UNRESTRICTED).horizons(HORIZONS) == HORIZONS
    assert _cohort(UNRESTRICTED, Domain(max_horizon=1)).horizons(HORIZONS) == (1,)
    assert _cohort(Domain(max_horizon=5), Domain(max_horizon=22)).horizons(HORIZONS) == (1, 5)
    assert _cohort(Domain(max_horizon=0)).horizons(HORIZONS) == ()
    assert _cohort().horizons(HORIZONS) == HORIZONS, "an empty cohort restricts nothing"
    assert _cohort(UNRESTRICTED).horizons([66, 1]) == (66, 1), "the caller's order is kept"


def test_the_refusal_and_the_horizons_agree() -> None:
    cohort = _cohort(Domain(max_horizon=5), UNRESTRICTED)
    answered = set(cohort.horizons(HORIZONS))
    assert {h for h in HORIZONS if cohort.refusal(h) is None} == answered


def test_the_required_columns_are_de_duplicated_in_first_seen_order() -> None:
    cohort = _cohort(
        Domain(requires=("iv_h1", "vvix")), UNRESTRICTED, Domain(requires=("vvix", "baa10y"))
    )
    assert cohort.requires == ("iv_h1", "vvix", "baa10y")
    assert _cohort(UNRESTRICTED).requires == ()


# -- Sampling and Measurement: shipped with the declaration, read by US-3.9b ---------------------


def test_a_sampling_reads_a_measurement_only_at_its_horizon_and_path_count() -> None:
    measured = Measurement(percent=0.5, source="SPY h22", horizon=22, paths=1000)
    sampling = Sampling(paths=1000, measured=(measured,))
    assert sampling.error(22) == 0.5
    assert sampling.error(5) is None
    assert Sampling(paths=2000, measured=(measured,)).error(22) is None
    assert Sampling(paths=1000).error(22) is None
    assert sampling.source == "SPY h22"
    assert Sampling(paths=1000).source is None


def test_a_sampling_whose_measurements_disagree_on_the_series_is_refused() -> None:
    one = Measurement(percent=0.5, source="SPY h22", horizon=22, paths=1000)
    other = Measurement(percent=0.7, source="QQQ h66", horizon=66, paths=1000)
    with pytest.raises(ValueError, match="SPY h22"):
        Sampling(paths=1000, measured=(one, other))


def test_the_declaration_module_names_no_numeric_library() -> None:
    """The cheap local form of ``test_import_discipline``'s walk, which is the real witness."""
    source = Path(base.__file__).read_text()
    for forbidden in ("numpy", "scipy", "arch"):
        assert f"import {forbidden}" not in source and f"from {forbidden}" not in source


# -- evaluate and the driver refuse a cohort at a horizon a member cannot answer -------------------


def test_evaluate_refuses_a_cohort_at_a_horizon_a_member_cannot_answer() -> None:
    """``ValueError`` from ``evaluate`` itself, so the guard does not live only in the CLI, and
    raised before any fit: no contestant is handed a view."""
    toy = make_toy(5)
    narrow = Spy("narrow", domain=Domain(max_horizon=1))
    wide = Spy("wide")
    cohort = Cohort("limited", (wide, narrow))
    with pytest.raises(ValueError, match=r"'narrow'.*h<=1"):
        run_cohort(toy, cohort)
    assert wide.views == [] and narrow.views == [], "the refusal came after a fit"


def test_evaluate_accepts_the_same_cohort_at_the_horizon_it_can_answer() -> None:
    toy = make_toy(1)
    narrow = Spy("narrow", domain=Domain(max_horizon=1))
    evaluation = run_cohort(toy, Cohort("limited", (Spy("wide"), narrow)))
    assert evaluation.results and narrow.views


def test_the_driver_refuses_before_counting_an_empty_window() -> None:
    """``run_configuration`` returns early for a window with no scoreable origin and appends
    counted NaN lines labelled with the cohort. Without its own guard that path would write
    ``limited`` lines at an illegal horizon, and ``evaluate``'s guard would never be reached."""
    from model_conformance import make_toy as toy_at

    from volab.sessions import get_calendar
    from volab.walkforward import run_configuration

    toy = toy_at(5)
    written: list[object] = []
    with pytest.raises(ValueError, match="limited"):
        run_configuration(
            toy.settings,
            SYMBOL,
            toy.horizon,
            toy.panel,
            toy.targets,
            calendar=get_calendar(toy.settings),
            first=dt.date(2020, 1, 2),
            last=dt.date(2020, 1, 3),
            every=toy.stride,
            minimum_training=toy.minimum_training,
            cohort=Cohort("limited", (Spy("narrow", domain=Domain(max_horizon=1)),)),
            persisted=lambda: None,
            append=lambda trials: written.extend(trials) or len(trials),
            report=lambda _: None,
        )
    assert written == []


# -- Form 2: a contestant that can answer, but only over a shorter history ------------------------
#
# The toy carries SPY's bars beside every VIX-family and FRED series, so a late-starting series is
# a row filter on the panel and needs no fixture surgery. VIX1D feeds ``iv_h1``.

REQUIRED = "iv_h1"
MINIMUM = 10


def _sessions(toy: Toy) -> list[dt.date]:
    # VIX, which every test leaves whole: VIX1D is the series they cut.
    return sorted(set(toy.panel[toy.panel["symbol"] == "VIX"]["as_of"]))


def _without_vix1d_before(toy: Toy, day: dt.date) -> Toy:
    """``toy`` with VIX1D beginning at ``day``, as the real series begins on 2022-05-13."""
    gone = (toy.panel["symbol"] == "VIX1D") & (toy.panel["as_of"] < day)
    return dataclasses.replace(toy, panel=toy.panel[~gone].reset_index(drop=True))


def _without_vix1d_on(toy: Toy, day: dt.date) -> Toy:
    """``toy`` with one interior VIX1D session missing: a data accident, not a window error."""
    gone = (toy.panel["symbol"] == "VIX1D") & (toy.panel["as_of"] == day)
    return dataclasses.replace(toy, panel=toy.panel[~gone].reset_index(drop=True))


def _stored(toy: Toy) -> pd.DataFrame:
    """The feature table the store would hold for ``toy``: built once, from the whole panel."""
    return build_features(
        toy.panel, through=_sessions(toy)[-1], calendar=get_calendar(toy.settings)
    )


@dataclass
class _Driven:
    outcome: ConfigurationOutcome
    lines: list[Trial]
    reports: list[str]


def _drive(
    toy: Toy,
    cohort: Cohort,
    *,
    first: dt.date,
    last: dt.date,
    every: int = 1,
    minimum: int = MINIMUM,
    persisted: Callable[[], pd.DataFrame | None] | None = None,
) -> _Driven:
    lines: list[Trial] = []
    reports: list[str] = []

    def append(trials: Sequence[Trial]) -> int:
        lines.extend(trials)
        return len(trials)

    outcome = run_configuration(
        toy.settings,
        SYMBOL,
        toy.horizon,
        toy.panel,
        toy.targets,
        calendar=get_calendar(toy.settings),
        first=first,
        last=last,
        every=every,
        minimum_training=minimum,
        cohort=cohort,
        persisted=(lambda: _stored(toy)) if persisted is None else persisted,
        append=append,
        report=reports.append,
    )
    return _Driven(outcome, lines, reports)


def _named_origin(text: str) -> dt.date:
    """The origin the refusal says qualifies, read from its own sentence and not searched for."""
    found = re.search(r"first that qualifies is (\d{4}-\d{2}-\d{2})", text)
    assert found is not None, text
    return dt.date.fromisoformat(found.group(1))


def _needs() -> Spy:
    return Spy("needs_iv", domain=Domain(requires=(REQUIRED,)))


def _first_origin_evaluate_stops_skipping(
    toy: Toy, first: dt.date, last: dt.date, minimum: int = MINIMUM
) -> dt.date:
    """The oracle: run ``evaluate`` itself, over the origins the driver would use, and read the
    first one not skipped for want of training rows. Nothing here shares code with
    ``coverage_refusal``."""
    origins = scoreable_origins(toy.settings, toy.targets, first=first, last=last, every=1)
    evaluation = evaluate(
        toy.panel,
        toy.targets,
        origins,
        symbol=SYMBOL,
        horizon=toy.horizon,
        calendar=get_calendar(toy.settings),
        minimum_training=minimum,
        every=1,
        cohort=Cohort("oracle", (_needs(),)),
    )
    thin = {day for day, why in evaluation.skipped if "knowable training row" in why}
    return next(day for day in origins if day not in thin)


def test_a_contestant_whose_required_column_starts_mid_window_refuses_the_run() -> None:
    """The red case. VIX1D begins at session 100 and the window begins at 60, so for 40 sessions
    the contestant has no ``iv_h1`` and every other contestant's origin would be intersected away.
    Refused, with the column, the origin that does qualify and how many precede it."""
    toy = _without_vix1d_before(make_toy(5), _sessions(make_toy(5))[100])
    contestant = _needs()
    driven = _drive(
        toy,
        Cohort("iv", (Spy("plain"), contestant)),
        first=_sessions(toy)[60],
        last=_sessions(toy)[-1],
    )
    assert driven.outcome.refused and driven.outcome.written == 0 and driven.lines == []
    assert contestant.views == [], "the refusal came after a fit"
    text = "\n".join(driven.reports)
    assert "REFUSED" in text and REQUIRED in text
    qualifying = _first_origin_evaluate_stops_skipping(toy, _sessions(toy)[60], _sessions(toy)[-1])
    assert _named_origin(text) == qualifying
    assert re.search(r"\b\d+ of the \d+ requested origins precede it", text), text
    assert "--from" in text and "cohort of its own" in text, "both remedies are named"


def test_the_coverage_refusal_agrees_with_the_origin_evaluate_stops_skipping() -> None:
    """At a long horizon, where the target lag bites hardest.

    First: a window beginning exactly at the column's first non-null session is **still**
    refused. A rule keyed to the first non-null session passes it, and the contestant then
    declines on thin training until it has ``minimum_training`` rows with completed targets while
    the intersection quietly removes those origins from everyone's loss. Second: the origin the
    refusal names is the origin ``evaluate`` itself stops skipping, so the two cannot disagree by
    ``h`` sessions -- the error the design's first draft made.

    The toy's SPY has a missing bar, which leaves the 22-session HAR component null for 22
    sessions after it; VIX1D therefore begins after that, at session 96, with a minimum of 3."""
    minimum = 3
    base = make_toy(22)
    sessions = _sessions(base)
    start = sessions[96]
    toy = _without_vix1d_before(base, start)
    last = sessions[-1]

    driven = _drive(toy, Cohort("iv", (_needs(),)), first=start, last=last, minimum=minimum)
    assert driven.outcome.refused, "the window begins at the first non-null session and is refused"

    expected = _first_origin_evaluate_stops_skipping(toy, start, last, minimum)
    assert expected >= sessions[96 + 22], "the lag moved the boundary 22 sessions past the start"
    assert _named_origin("\n".join(driven.reports)) == expected

    # Moving --from to the named origin is accepted, so the remedy the message gives works.
    accepted = _drive(toy, Cohort("iv", (_needs(),)), first=expected, last=last, minimum=minimum)
    assert not accepted.outcome.refused and accepted.outcome.written > 0


def test_an_interior_gap_in_a_required_column_skips_the_origin_for_everyone_with_a_reason() -> None:
    """The FRED case, measured at 3 of 247 origins: a data accident. Not refused; the origin is
    skipped for every contestant, recorded, and every trial's origin count drops by exactly one."""
    base = make_toy(5)
    sessions = _sessions(base)
    gap = sessions[100]
    window = {"first": sessions[98], "last": sessions[110]}
    cohort = Cohort("iv", (Spy("plain"), _needs()))

    clean = _drive(base, cohort, **window)
    gappy_toy = _without_vix1d_on(base, gap)
    gappy = _drive(gappy_toy, cohort, **window)

    assert not gappy.outcome.refused
    assert gappy.outcome.evaluation is not None
    skipped = dict(gappy.outcome.evaluation.skipped)
    assert gap in skipped and REQUIRED in skipped[gap]
    assert {t.model: t.origins for t in clean.lines} == {"plain": 13, "needs_iv": 13}
    assert {t.model: t.origins for t in gappy.lines} == {"plain": 12, "needs_iv": 12}


def test_coverage_is_reported_as_not_checked_when_the_store_holds_no_built_table() -> None:
    """Nothing to check is not a pass: said in the voice of the truncation line, and the run
    falls through to the per-origin skips instead of being refused on no evidence."""
    toy = make_toy(5)
    sessions = _sessions(toy)
    driven = _drive(
        toy,
        Cohort("iv", (_needs(),)),
        first=sessions[98],
        last=sessions[104],
        persisted=lambda: None,
    )
    text = "\n".join(driven.reports)
    assert "coverage: NOT CHECKED, the store holds no built table" in text
    assert not driven.outcome.refused and driven.outcome.written > 0


def test_a_cohort_that_requires_nothing_prints_no_coverage_line_and_reads_the_store_once() -> None:
    """Existing output must not change, and ``persisted`` is a store read in the CLI."""
    toy = make_toy(5)
    sessions = _sessions(toy)
    reads: list[int] = []

    def persisted() -> pd.DataFrame | None:
        reads.append(1)
        return _stored(toy)

    plain = _drive(
        toy,
        Cohort("plain", (Spy("plain"),)),
        first=sessions[98],
        last=sessions[104],
        persisted=persisted,
    )
    assert "coverage" not in "\n".join(plain.reports) and len(reads) == 1
    reads.clear()
    needing = _drive(
        toy, Cohort("iv", (_needs(),)), first=sessions[98], last=sessions[104], persisted=persisted
    )
    assert len(reads) == 1, "one read serves the coverage check and the truncation comparison"
    assert not needing.outcome.refused and needing.outcome.written > 0


def test_a_declared_column_the_feature_table_does_not_have_is_a_value_error() -> None:
    """Wiring, not data: every origin would be equally broken, so ``evaluate`` aborts the run
    rather than skipping each origin with a reason."""
    toy = make_toy(5)
    typo = Spy("typo", domain=Domain(requires=("iv_h2",)))
    with pytest.raises(ValueError, match="iv_h2"):
        run_cohort(toy, Cohort("c", (typo,)))


def test_the_coverage_check_is_silent_about_a_window_too_early_for_everyone() -> None:
    """A window where the HAR floor alone is thin is today's behaviour -- per-origin skips for
    every contestant -- and the column is not to blame, so it is not this refusal's to make."""
    toy = make_toy(5)
    sessions = _sessions(toy)
    reason = coverage_refusal(
        _stored(toy),
        toy.targets,
        (REQUIRED,),
        [sessions[24]],
        horizon=5,
        minimum_training=MINIMUM,
    )
    assert reason is None


def test_coverage_refusal_of_nothing_required_is_none() -> None:
    toy = make_toy(5)
    assert (
        coverage_refusal(
            _stored(toy), toy.targets, (), [_sessions(toy)[60]], horizon=5, minimum_training=MINIMUM
        )
        is None
    )


def test_a_thin_skip_with_a_required_column_says_which_count_fell_short() -> None:
    """The per-origin fallback the NOT CHECKED path relies on must not contradict itself: "only
    1500 rows" against a minimum of 250 reads as a lie when the shortfall is in a column."""
    base = make_toy(5)
    sessions = _sessions(base)
    toy = _without_vix1d_before(base, sessions[100])
    origins = [sessions[60], sessions[61]]
    evaluation = evaluate(
        toy.panel,
        toy.targets,
        origins,
        symbol=SYMBOL,
        horizon=5,
        calendar=get_calendar(toy.settings),
        minimum_training=MINIMUM,
        every=1,
        cohort=Cohort("iv", (_needs(),)),
    )
    reasons = [why for _, why in evaluation.skipped]
    assert len(reasons) == 2
    assert all(
        re.search(r"only \d+ knowable training row\(s\), 0 of them non-null in \['iv_h1'\]", r)
        for r in reasons
    ), reasons
    plain = evaluate(
        toy.panel,
        toy.targets,
        [sessions[24]],
        symbol=SYMBOL,
        horizon=5,
        calendar=get_calendar(toy.settings),
        minimum_training=MINIMUM,
        every=1,
        cohort=Cohort("plain", (Spy("plain"),)),
    )
    assert all("non-null in" not in why for _, why in plain.skipped), (
        "existing wording is unchanged"
    )
