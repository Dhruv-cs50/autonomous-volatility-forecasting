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
* ``test_the_preregistration_lists_exactly_the_set_these_tests_pin`` parses the bullets under
  "Contestants" in section 1 and asserts **set equality** with the literals, on both sides of the
  horizon split: the names in the ``h=1`` bullet equal :data:`REGISTERED_H1`, and the set the
  ``h>=2`` bullet registers equals :data:`REGISTERED_ABOVE`. A name in the document but not the
  literals fails, and so does a name in the literals but not the document, so neither side can be
  edited alone. It does *not* compare the document's order.

  The ``h>=2`` set is **parsed rather than inferred from the removal clause**. The document words
  it as "the same **minus ...**", so the set is the ``h=1`` set less what that clause removes --
  but only because the bullet names nothing else, which is now asserted rather than assumed, and
  the bullets are *enumerated* rather than looked up by name, so a third one cannot be added below
  them unread. Two witnesses plant those amendments in a copy of the text and watch the parse
  refuse them; both were seen passing before the parse changed. Reading the removal clause alone,
  as this module first did, could see neither.

These tests read a markdown file and two module constants. They read no data, so they carry no
holdout marker.
"""

import re
from pathlib import Path

import pytest

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


def _contestant_bullets(text: str) -> dict[str, str]:
    """The bullets under "**Contestants.**", by lead (``h=1``, ``h≥2``).

    A bullet runs from its ``- `` to the next bullet or the next unindented paragraph, so the
    h≥2 bullet's continuation lines are part of it. Every bullet of the block is returned, not
    the two this module expects: an amendment adding a third is then visible to the caller rather
    than being read by nothing.
    """
    start = text.index("**Contestants.**")
    bullets: dict[str, str] = {}
    lead: str | None = None
    for line in text[start:].splitlines()[1:]:
        if line.startswith("- "):
            lead, _, rest = line[2:].partition(":")
            assert lead not in bullets, f"two {lead!r} bullets under Contestants"
            bullets[lead] = rest
        elif lead is not None and line.startswith("  ") and line.strip():
            bullets[lead] += "\n" + line
        elif bullets and line.strip():
            break
    assert bullets, "the pre-registration has no bullets under Contestants; its shape changed"
    return bullets


def _registered_contestants(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """The contestant sets ``text`` registers: at ``h=1``, and above it.

    The h≥2 set is **parsed**, not inferred from the removal clause alone. The document words
    it as "the same minus ...", so the set above h=1 is the h=1 set less what that clause removes
    -- but only because the bullet says nothing else, and that is asserted here rather than
    assumed. A bullet that named a contestant outside its minus clause would be registering a
    different set, and inferring from the clause alone cannot see it.
    """
    bullets = _contestant_bullets(text)
    assert set(bullets) == {"h=1", "h≥2"}, (
        f"the contestant bullets are {sorted(bullets)}: the registered read has exactly two "
        "groups, h=1 and h≥2, so another bullet registers a set nothing here pins"
    )

    h1 = re.findall(r"`(\w+)`", bullets["h=1"])
    assert len(h1) == len(set(h1)), "a contestant is listed twice in the h=1 bullet"

    above = bullets["h≥2"]
    removed = re.search(r"minus ((?:`\w+`[, ]*)+)\*\*", above)
    assert removed, "the h≥2 bullet no longer says 'the same **minus ...**'"
    dropped = set(re.findall(r"`(\w+)`", removed.group(1)))
    named = set(re.findall(r"`(\w+)`", above))
    assert dropped <= set(h1), (
        f"the h≥2 bullet removes {sorted(dropped - set(h1))}, which the h=1 bullet does not list"
    )
    assert named == dropped, (
        f"the h≥2 bullet names {sorted(named - dropped)} outside its minus clause: it registers "
        "'the same minus ...' and nothing more, so a name added to it adds a contestant to the "
        "registered read above h=1"
    )
    return frozenset(h1), frozenset(h1) - dropped


def test_the_preregistration_lists_exactly_the_set_these_tests_pin() -> None:
    """Set equality in both directions, so neither the document nor the literals can be edited
    alone: a name in one and not the other fails, at h=1 and above it."""
    h1, above = _registered_contestants(PREREGISTRATION.read_text(encoding="utf-8"))
    assert h1 == frozenset(REGISTERED_H1)
    assert above == frozenset(REGISTERED_ABOVE)


# -- the amendments this fence exists to catch, planted in memory ---------------------------------
#
# The document is REGISTERED and its content hash is recorded in R130, so no witness here may edit
# it on disk. Each plants its amendment in a copy of the text and asserts the parse refuses it.


def _amended(find: str, replace: str) -> str:
    text = PREREGISTRATION.read_text(encoding="utf-8")
    assert find in text, f"the text to amend is no longer in the document: {find!r}"
    return text.replace(find, replace, 1)


def test_a_contestant_added_above_h1_only_is_caught() -> None:
    """The gap this closes. The h≥2 set used to be inferred from the minus clause alone, so an
    amendment that added a contestant to that bullet -- and to the cohorts beside it -- changed
    the registered read above h=1 while every test here still passed."""
    amended = _amended(
        "the same **minus `egarch11_skewt`** (R114)",
        "the same **minus `egarch11_skewt`**, plus `har_rv_iv` (R114)",
    )
    with pytest.raises(AssertionError, match="outside its minus clause"):
        _registered_contestants(amended)


def test_a_third_contestant_bullet_is_caught() -> None:
    """The same gap by another route: a third group. ``_contestant_bullets`` returns every bullet
    of the block rather than the two looked up by name, so a horizon-specific amendment cannot be
    added below the two this module reads and go unseen."""
    amended = _amended(
        "- h≥2: the same",
        "- h=66: the same **minus `har_levels_mz`** (invented).\n- h≥2: the same",
    )
    with pytest.raises(AssertionError, match="the registered read has exactly two groups"):
        _registered_contestants(amended)
