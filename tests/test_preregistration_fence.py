"""The holdout read's contestant set is fenced against a later contestant.

``docs/holdout-preregistration.md`` was REGISTERED at commit ``65617ee`` (ruling R130). Its
section 5 says Phase 3's contestants "do not exist yet and must not be added to this window after
the fact -- a later contestant gets a later, separately registered read, or the live record".
Nothing but this module mechanically stops a contestant being appended to the cohorts the
pre-registration froze, so these tests pin both cohorts to the registered set:

* ``test_the_shootout_is_exactly_the_preregistered_contestant_set`` and
  ``test_the_multistep_cohort_is_exactly_the_preregistered_set_above_h1`` compare each cohort's
  contestant names, in order, to the literals below. Adding, removing or reordering a contestant
  fails them.
* ``test_the_preregistration_still_names_the_set_these_tests_pin`` checks only that every name in
  :data:`REGISTERED_H1` still appears, backticked, somewhere in the pre-registration. It is what
  stops the first two being "fixed" by editing the literals to match a changed cohort. It does
  *not* check that the document lists nothing else, nor which section a name sits in.

These tests read a markdown file and two module constants. They read no data, so they carry no
holdout marker.
"""

from pathlib import Path

from volab.compare.cohorts import MULTISTEP, SHOOTOUT

PREREGISTRATION = Path(__file__).resolve().parents[1] / "docs" / "holdout-preregistration.md"

REGISTERED_H1 = (
    "har_levels",
    "har_semilog",
    "random_walk",
    "garch11_skewt",
    "gjr11_skewt",
    "egarch11_skewt",
    "har_levels_mz",
)
REGISTERED_ABOVE = tuple(n for n in REGISTERED_H1 if n != "egarch11_skewt")


def test_the_shootout_is_exactly_the_preregistered_contestant_set() -> None:
    assert SHOOTOUT.contestants == REGISTERED_H1


def test_the_multistep_cohort_is_exactly_the_preregistered_set_above_h1() -> None:
    assert MULTISTEP.contestants == REGISTERED_ABOVE


def test_the_preregistration_still_names_the_set_these_tests_pin() -> None:
    text = PREREGISTRATION.read_text(encoding="utf-8")
    for name in REGISTERED_H1:
        assert f"`{name}`" in text
