"""The contestant's declared domain (US-3.9a): what it can answer, and what the cohort derives.

The declaration cannot be checked against behaviour. EGARCH at h=22 returns a finite, ordinary-
looking ``1.70e+12`` -- which is why R114 demanded an a-priori refusal -- so nothing here tests
that a bound is *right*. What these witnesses hold is that a declaration cannot be omitted, cannot
be expressed wrongly (``GarchModel`` derives it), and is acted on before any fit.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

import pytest
from model_conformance import SYMBOL, Spy, make_toy, run_cohort

from volab.features.targets import HORIZONS
from volab.models import base
from volab.models.base import UNRESTRICTED, Cohort, Domain, Measurement, Model, Sampling


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
