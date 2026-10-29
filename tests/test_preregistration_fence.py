"""The holdout read's contestant set is tied to the pre-registration that froze it.

``docs/holdout-preregistration.md`` was REGISTERED at commit ``65617ee`` (ruling R130). Its
section 5 says Phase 3's contestants "do not exist yet and must not be added to this window after
the fact -- a later contestant gets a later, separately registered read, or the live record".

Other pins already exist: ``tests/test_compare.py`` asserts the seven names in
``test_the_shootout_cohort_is_the_seven_contestants_and_begins_with_har_levels`` and pins
``MULTISTEP`` as the shootout without ``egarch11_skewt`` beside it. They carry no link to the
document, so a later contestant could be added to the cohorts and to those literals together, and
nothing would say the registered read had changed. This module's distinct contribution is that
link:

* ``test_the_shootout_is_exactly_the_preregistered_contestant_set`` and
  ``test_the_multistep_cohort_is_exactly_the_preregistered_set_above_h1`` compare each cohort's
  contestant names, in order, to the literals below. Adding, removing or reordering a contestant
  fails them.
* ``test_the_preregistration_lists_exactly_the_set_these_tests_pin`` parses the two bullets under
  "Contestants" in section 1 of the document and asserts **set equality** with the literals: the
  names in the ``h=1`` bullet equal :data:`REGISTERED_H1`, and the names the ``h>=2`` bullet
  removes ("the same **minus ...**") equal the one name :data:`REGISTERED_H1` has and
  :data:`REGISTERED_ABOVE` lacks. A name in the document but not the literals fails, and so does a
  name in the literals but not the document, so neither side can be edited alone. It reads only
  those two bullets, so a later paragraph mentioning a name in backticks changes nothing. It does
  *not* compare the document's order, nor read any other part of it.

These tests read a markdown file and two module constants. They read no data, so they carry no
holdout marker.
"""

import re
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


def _bullet(text: str, lead: str) -> str:
    """The bullet that opens with ``lead``: from there to the next blank line or bullet."""
    match = re.search(rf"^- {re.escape(lead)}(.*?)(?=^\s*$|^- )", text, re.MULTILINE | re.DOTALL)
    assert match, f"the pre-registration has no {lead!r} bullet; its shape changed"
    return match.group(1)


def test_the_preregistration_lists_exactly_the_set_these_tests_pin() -> None:
    text = PREREGISTRATION.read_text(encoding="utf-8")

    h1 = re.findall(r"`(\w+)`", _bullet(text, "h=1:"))
    assert len(h1) == len(set(h1)), "a contestant is listed twice in the h=1 bullet"
    assert set(h1) == set(REGISTERED_H1)

    above = _bullet(text, "h\u22652:")
    removed = re.search(r"minus ((?:`\w+`[, ]*)+)\*\*", above)
    assert removed, "the h>=2 bullet no longer says 'the same **minus ...**'"
    assert set(re.findall(r"`(\w+)`", removed.group(1))) == set(REGISTERED_H1) - set(
        REGISTERED_ABOVE
    )
