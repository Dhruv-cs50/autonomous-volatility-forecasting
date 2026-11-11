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

These tests read a markdown file, two module constants, and -- for the registered trial count in
section 4 -- the two committed ledgers under ``data/``. They read no **panel** data and nothing
past ``dev_end``, so they carry no holdout marker, and the ledger read is byte-compared either
side to prove it changed nothing.
"""

import itertools
import json
import re
from pathlib import Path

import pytest

from volab.compare.cohorts import MULTISTEP, SHOOTOUT
from volab.compare.sink import COMPARISON_LEDGER
from volab.config import load_settings
from volab.features.targets import HORIZONS
from volab.walkforward import LOSSES, TRIAL_LEDGER

REPO = Path(__file__).resolve().parents[1]
PREREGISTRATION = REPO / "docs" / "holdout-preregistration.md"

# Written as words in the document's prose, so they are read as words here rather than by a digit
# search that would also match the horizons in the same sentence. A word absent from this map
# fails; it is not skipped, because skipping is how a count stops being checked without saying so.
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _word(number: str) -> int:
    assert number.lower() in _NUMBER_WORDS, f"{number!r} is not a number word this test knows"
    return _NUMBER_WORDS[number.lower()]


def _horizons_above_h1() -> tuple[int, ...]:
    """The horizons the registered read scores above h=1, from the code's own horizon set."""
    return tuple(horizon for horizon in HORIZONS if horizon >= 2)


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


# -- section 1's prose counts, and section 4's trial-count table ---------------------------------
#
# Row 2 of the Phase 3 deferred findings: the document's own counts and its registered trial table
# were tied to the code by nothing. Section 4 is the figure the holdout reveal reports a delta
# against, so an untested tie there is the number the whole read is measured by.


def _prose_counts(text: str) -> tuple[int, int, tuple[int, ...]]:
    """``(at h=1, above h=1, the horizons named above h=1)`` from the Contestants sentence."""
    match = re.search(
        r"\*\*Contestants\.\*\* (\w+) at h=1; \*\*(\w+) at h=([^*]+)\*\*",
        " ".join(text.split()),
    )
    assert match, "the Contestants sentence no longer states its two counts"
    horizons = tuple(int(number) for number in re.findall(r"\d+", match.group(3)))
    return _word(match.group(1)), _word(match.group(2)), horizons


def _registered_figures(text: str) -> tuple[int, int]:
    """The two ledger line counts section 4 records as read at registration."""
    match = re.search(
        r"`data/trials\.jsonl` line count at registration: \*\*(\d+)\.\*\* "
        r"`data/comparisons\.jsonl`: \*\*(\d+)\.\*\*",
        " ".join(text.split()),
    )
    assert match, "section 4 no longer records the two line counts read at registration"
    return int(match.group(1)), int(match.group(2))


def _table_milestones(text: str) -> list[tuple[int, int]]:
    """``(added, running total)`` per row of section 4's table, in document order.

    The first row states a total with no increment, so its ``added`` is the total itself. The
    arithmetic between the rows is checked by the caller rather than trusted.
    """
    section = text[text.index("## 4. Trials already counted") : text.index("## 5.")]
    rows: list[tuple[int, int]] = []
    for line in section.splitlines():
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cell = line.split("|")[2].replace("*", "").strip()
        if match := re.fullmatch(r"\+(\d+) = (\d+)", cell):
            rows.append((int(match.group(1)), int(match.group(2))))
        elif cell.isdigit():
            rows.append((int(cell), int(cell)))
    assert len(rows) > 1, "section 4's trial table no longer has rows this test can read"
    return rows


def _last_row_arithmetic(text: str) -> tuple[list[int], int, list[int], int]:
    """The factors and products section 4 states for its last row."""
    match = re.search(
        r"The arithmetic of the last row: (\d+) contestants x (\d+) symbols x (\d+) "
        r"horizon x (\d+) losses = (\d+), plus (\d+) x (\d+) x (\d+) x (\d+) = (\d+)",
        " ".join(text.split()),
    )
    assert match, "section 4 no longer spells out the arithmetic of its last row"
    numbers = [int(group) for group in match.groups()]
    return numbers[:4], numbers[4], numbers[5:9], numbers[9]


def test_the_prose_counts_are_the_sizes_of_the_registered_sets() -> None:
    """ "Seven at h=1; six at h=5, 22 and 66" is a claim about the cohorts, and it is checked here
    against them rather than being prose nothing reads. The horizons come from ``HORIZONS``, so
    adding a horizon to the project makes this sentence wrong and says so."""
    at_h1, above_h1, horizons = _prose_counts(PREREGISTRATION.read_text(encoding="utf-8"))
    assert at_h1 == len(REGISTERED_H1) == len(SHOOTOUT.contestants)
    assert above_h1 == len(REGISTERED_ABOVE) == len(MULTISTEP.contestants)
    assert horizons == _horizons_above_h1()


def _check_table_adds_up(text: str) -> int:
    """Re-add section 4's table and check it reaches the registered count. Returns that count.

    One function so the witness below exercises the assertions the green test does, rather than a
    copy of them that could drift from it.
    """
    milestones = _table_milestones(text)
    running = milestones[0][1]
    assert milestones[0][0] == running, "the first row states a total, not an increment"
    for added, total in milestones[1:]:
        running += added
        assert total == running, f"the row adding {added} does not reach {total}"
    registered_trials, _ = _registered_figures(text)
    assert running == registered_trials, (
        f"section 4's table reaches {running} and it registers {registered_trials}"
    )
    return running


def test_section_4s_table_adds_up_to_the_registered_trial_count() -> None:
    """The table is a running total, and the figure the reveal measures its delta against is its
    last row. Every step is re-added here, so a row edited alone fails."""
    assert _check_table_adds_up(PREREGISTRATION.read_text(encoding="utf-8")) > 0


def test_section_4s_last_row_is_the_shape_of_the_two_cohorts() -> None:
    """ "7 contestants x 3 symbols x 1 horizon x 2 losses = 42, plus 6 x 3 x 3 x 2 = 108" is the
    registered run's own shape, so every factor is a quantity the code holds: the two cohort
    sizes, the configured universe, the horizons above h=1 and the loss set. The two products must
    also be the last row's increment, which is what ties the arithmetic to the table above it."""
    text = PREREGISTRATION.read_text(encoding="utf-8")
    universe = len(load_settings().universe)
    h1_factors, h1_product, above_factors, above_product = _last_row_arithmetic(text)
    assert h1_factors == [len(SHOOTOUT.contestants), universe, 1, len(LOSSES)]
    assert above_factors == [
        len(MULTISTEP.contestants),
        universe,
        len(_horizons_above_h1()),
        len(LOSSES),
    ]
    assert h1_product == h1_factors[0] * h1_factors[1] * h1_factors[2] * h1_factors[3]
    assert above_product == (
        above_factors[0] * above_factors[1] * above_factors[2] * above_factors[3]
    )
    assert h1_product + above_product == _table_milestones(text)[-1][0]


def test_the_registered_figures_are_a_prefix_of_the_committed_ledgers() -> None:
    """The tie the reveal actually measures by, and it is a **floor**, not an equality.

    Both ledgers are append-only and section 4 itself says the reveal "takes a second count and
    records both plus the delta ... since further work may legitimately add trials". Pinning
    equality here would turn a legitimate run into a test failure, which is the mistake
    ``test_the_committed_ledger_reads_correctly_and_is_not_touched`` records in
    ``tests/test_leaderboard.py``. What is pinned instead:

    * neither ledger may be shorter than the figure registered against it -- shrinkage means a
      rewrite of an append-only file, and the delta the reveal reports would be meaningless;
    * one trial line is one ``(model, symbol, horizon, loss)`` **within each run**, which is what
      makes the table's arithmetic a count of configurations rather than of lines. Not across the
      file: all three shootouts are kept, so a configuration recurs between runs by design;
    * every milestone in section 4's table falls on a run boundary of the registered prefix, so
      the table describes the runs the ledger actually holds.

    Read-only: the bytes are compared either side.
    """
    text = PREREGISTRATION.read_text(encoding="utf-8")
    registered_trials, registered_comparisons = _registered_figures(text)
    trials_path = REPO / TRIAL_LEDGER
    before = trials_path.read_bytes()
    rows = [json.loads(line) for line in before.decode("utf-8").splitlines() if line.strip()]
    comparisons = [
        line
        for line in (REPO / COMPARISON_LEDGER).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(rows) >= registered_trials, "the trial ledger is shorter than the registered count"
    assert len(comparisons) >= registered_comparisons, "the comparison ledger has lost lines"

    prefix = rows[:registered_trials]
    boundaries, running = set(), 0
    for at, group in itertools.groupby(prefix, key=lambda row: row["at"]):
        lines = list(group)
        # Within one run, and deliberately not across the file: section 4 keeps all three
        # shootouts, so "the ledger says 414 things were tried and not 150" and the same
        # configuration recurs by design. It is one line per configuration *per run* that makes
        # each run's count the product of its factors.
        configurations = {(r["model"], r["symbol"], r["horizon"], r["loss"]) for r in lines}
        assert len(configurations) == len(lines), f"the run at {at} repeats a configuration"
        running += len(lines)
        boundaries.add(running)
    for added, total in _table_milestones(text):
        assert total in boundaries, f"the row reaching {total} (+{added}) is no run boundary"

    assert trials_path.read_bytes() == before, "this test must not touch the ledger"


def test_an_edited_trial_count_is_caught() -> None:
    """The witness for the table tests above. Section 4's figures were read by nothing before this
    commit, so an amendment to the registered count -- the number the reveal's delta is measured
    against -- passed every test in this module.

    Two amendments, because the table can be edited in two ways and one assertion catches each:
    a row whose own arithmetic stops working, and a row that still adds up but no longer reaches
    the registered figure. The second is the one a careful forger would write.
    """
    with pytest.raises(AssertionError, match="does not reach"):
        _check_table_adds_up(_amended("**+150 = 414**", "**+150 = 415**"))
    with pytest.raises(AssertionError, match="reaches 415 and it registers 414"):
        _check_table_adds_up(_amended("**+150 = 414**", "**+151 = 415**"))


def test_an_edited_prose_count_is_caught() -> None:
    """The same for section 1's prose: "Seven at h=1" was read by nothing."""
    at_h1, _, _ = _prose_counts(_amended("Seven at h=1", "Eight at h=1"))
    assert at_h1 == 8, "the amendment did not take, so this witnesses nothing"
    assert at_h1 != len(REGISTERED_H1)
