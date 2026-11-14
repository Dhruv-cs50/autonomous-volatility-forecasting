"""The six roles of the research agent, each a function from state to a partial update.

**Hub and spoke (framework lines 74-86): no role calls another.** Each function here takes the
sprint's state, plus exactly the tools the framework's table lists for that role, and returns the
piece of state it produced. It never reads another role's output from anywhere but the ``state``
it is handed, and it calls no other role. ``volab.agent.run_sprint`` is the hub: it routes every
handoff, merges every update and checkpoints between them. ``tests/test_agent_sprint.py`` derives
the "no role calls another" claim from this file's AST rather than asserting it here.

**A role gets the tools it is listed with and nothing more.** The Supervisor receives
:func:`list_models`' answer; the Researcher receives memory and a trial count; the Configurator
receives the universe and returns raw payloads, because the typed ``propose_config`` of the
framework's table is, in this design, the gate that the hub applies to what it returns; the Runner
receives ``submit``; the Critic receives dev metrics; the Reporter receives harness-rendered
figures. None receives a storage root, a panel, a shell or a date-typed value.

**What each prompt carries, which is the inventory R208 rests on (R214).** The Researcher sees the
sprint id, the kept-line count of the agent ledger, the vocabulary, the offered horizons
(``AGENT_HORIZONS``, those whose 10% MCS the development window can hold) and, for earlier
sprints, their ids, hypotheses and gate verdicts. The Configurator sees the hypothesis, its slot
number, the vocabulary, the universe, the offered horizons, the reference names and the parsed
payloads the earlier slots of this sprint emitted (R227). The Critic sees the
hypothesis, the ledger count and, per proposal, its id, the child's verdict name and the child's
``reason``. The Reporter sees the hypothesis and the harness-rendered figures. Date-shaped text
therefore reaches a prompt by three routes: the run date inside a sprint or proposal id, which
selects no window; origin dates inside the Critic's ``reason``, which is coverage or audit text
from the child (``the first origin that qualifies is <date>``, ``max as_of=``) and is bounded by
``dev_end`` because the child reads with ``end = dev_end``; and whatever the Reporter's figures
render, which is dev-only. ``exit_code``, ``pid``, ``trials_written``, ``comparisons``,
``output_tail`` and the child's stamps are in the Runner's output and in no prompt. That last
sentence is not left to this prose: ``tests/test_agent_sprint.py`` runs a sprint with sentinel
values in each such field and asserts none reaches a recorded prompt.
This module imports nothing that reaches ``volab.store`` (R192).

**The Critic gates nothing, and that is a ruling and not an omission.** The framework asks it for
"alignment between hypothesis and config", which is a language model judging text against a
configuration -- *precisely this project's founding negative result*, where an LLM reviewer
approved a feature built from future intraday bars. So :func:`critic` returns a verdict that the
hub writes to the sprint log and that **permits or blocks no run**. Only
``volab.agent.vocabulary.gate`` gates, with the hub's sprint-level repeat rule after it (R227).
Nothing in this module returns something the hub consults to decide whether to run.

**The Supervisor is not an LLM and does not learn.** The framework allows "cheap, or no LLM". It is
handed the model families, of which there is one, so "change model family" is not an available
action; ``get_mcs_result`` is not wired because the agent reads no comparison file at all (M7:
those records carry no origin dates, so no filter can make them safe). What it does is choose
between continuing and stopping. A bandit could replace it; this is not one.

**The Reporter's output is checked for digits, and for nothing else.**
:func:`invented_numbers` finds every numeric literal in the report that does not appear among the
harness-rendered figures the Reporter was given; digits inside a name (``rv_proxy_d66``, a sprint
id) are not literals (R229). That is the one validation of a report that is possible. It does
**not** validate tone or emphasis, spelled-out numbers ("forty trials"), the *position* of a literal
(a ``5`` anywhere in the figures excuses a ``5`` anywhere in the report), or whether a true number
is attached to the right claim. A report with every number right and the emphasis wrong passes
every check in this package.

What this module does not do: it makes no LLM call except through the ``Completer`` it is handed,
reads no environment and no file, and decides nothing about promotion.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from typing import Any

from volab.agent._llm import Completer, Prompt
from volab.agent._runner import ChildRun
from volab.agent.vocabulary import (
    _IDENTITY_GROUPS,
    AGENT_HORIZONS,
    MAX_EXTRA_REGRESSORS,
    REFERENCE_CONTESTANTS,
    VOCABULARY,
    Proposal,
)

ROLES: tuple[str, ...] = (
    "supervisor",
    "researcher",
    "configurator",
    "runner",
    "critic",
    "reporter",
)

# The one family the vocabulary can express. ``test_the_model_families_are_what_validate_accepts``
# ties it to ``validate`` so it cannot drift.
MODEL_FAMILIES: tuple[str, ...] = ("harx",)

# A numeric literal: digits, optionally joined to more digits by "." or ",". Applied identically
# to the report and to the figures, so ``1,000`` is one token on both sides.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

# A sprint id as ``python -m volab.agent`` writes it (``%Y-%m-%dT%H%M%SZ``), with an optional
# proposal suffix ``-NN``, or its short ``HHMMSSZ`` form. Its digits are a name, not a figure
# (R229). Matched in the report only.
_SPRINT_ID = re.compile(r"(?<!\w)(?:\d{4}-\d{2}-\d{2}T)?\d{6}Z(?:-\d{2})?(?!\w)")


def list_models() -> tuple[str, ...]:
    """The model families a proposal may name. A module constant; no I/O."""
    return MODEL_FAMILIES


def supervisor(state: Mapping[str, Any], *, models: Sequence[str]) -> dict[str, Any]:
    """Choose ``add_features`` or ``stop``. No LLM, no learning (see the module docstring).

    ``stop`` exactly when ``models`` is empty, since there is then nothing to propose against;
    otherwise ``add_features``. ``state`` is accepted so the signature is the one every role
    shares and is not read.
    """
    del state
    if not models:
        return {"supervisor": {"action": "stop", "note": "no model family is available"}}
    return {
        "supervisor": {
            "action": "add_features",
            "note": f"{len(models)} model family available: {', '.join(models)}",
        }
    }


def researcher(
    state: Mapping[str, Any],
    *,
    completer: Completer,
    memory: Sequence[Mapping[str, Any]],
    agent_trials: int,
) -> dict[str, Any]:
    """Write one hypothesis, informed by memory of earlier sprints and the agent's trial count.

    ``memory`` is what the hub read from the checkpointer: for each earlier sprint, its hypothesis
    and what the gate said of its proposals. ``agent_trials`` is the number of lines the agent
    ledger holds inside the development window. The prompt names the vocabulary and the limits,
    and passes no date-typed value; the only date-shaped text it carries is the run date inside
    the sprint ids (this sprint's and earlier ones'), which selects no window. The other roles'
    inventories are in the module docstring.
    """
    lines = [
        f"Sprint {state['sprint_id']}.",
        f"The agent ledger already holds {agent_trials} evaluated trial lines.",
        f"Features you may build a hypothesis on: {', '.join(VOCABULARY)}.",
        f"Horizons in sessions: {', '.join(str(h) for h in AGENT_HORIZONS)}.",
        "Earlier sprints:",
    ]
    lines.extend(
        f"- {entry.get('sprint_id')}: {entry.get('hypothesis')} (gate: {entry.get('gate')})"
        for entry in memory
    )
    if not memory:
        lines.append("- none")
    completion = completer(
        Prompt(
            system=(
                "You are the research agent of a volatility-forecasting lab. Write one short "
                "hypothesis: which extra regressors should improve a HAR model of realized "
                "variance, for which symbol and horizon, and why. Do not repeat a rejected idea."
            ),
            user="\n".join(lines),
        )
    )
    return {"hypothesis": completion.text.strip()}


def parse_payload(text: str) -> object:
    """The Configurator's reply as a JSON value, or the reply itself when it is not JSON.

    This is the parse boundary (R189). A reply that is not JSON is returned as the string it was,
    which ``validate`` refuses as ``NOT_A_MAPPING``; so garbage is refused by the gate, not by a
    parser with a second opinion. A reply wrapped in a Markdown code fence has the fence removed
    first, which is the one leniency: models fence JSON habitually.
    """
    body = text.strip()
    if body.startswith("```"):
        inner = body.split("\n", 1)[1] if "\n" in body else ""
        body = inner.rsplit("```", 1)[0].strip()
    try:
        return json.loads(body)
    except ValueError:
        return text


def configurator(
    state: Mapping[str, Any],
    *,
    completer: Completer,
    slots: int,
    universe: Sequence[str],
) -> dict[str, Any]:
    """One raw payload per slot, parsed but not validated. Validation is the gate's, not ours.

    Every slot gets a prompt of the same shape. It differs by the slot's number and by the list
    of what earlier slots emitted, so the model can see what this sprint already proposed (R227):
    with identical prompts the second real-store sprint emitted one configuration in every slot.
    Neither depends on where the canary goes, which is chosen after this returns, so the hub's
    canary substitution still cannot be distinguished from a real emission by anything upstream
    of the gate. The canary slot's own emission is listed to later slots and then discarded; not
    listing it would need to know the canary's slot. ``emitted`` is returned in slot order.
    """
    emitted: list[object] = []
    for slot in range(slots):
        earlier = [json.dumps(e, sort_keys=True) for e in emitted if isinstance(e, dict)]
        completion = completer(
            Prompt(
                system=(
                    "You turn a hypothesis into one JSON object and nothing else. Keys: "
                    '"family", "extra_regressors", "symbol", "horizon", "reference".'
                ),
                user=(
                    f"Hypothesis: {state['hypothesis']}\n"
                    f"Proposal {slot} of {slots}.\n"
                    "Already proposed in this sprint, so propose something different: "
                    f"{'; '.join(earlier) if earlier else 'none'}\n"
                    f"family: one of {', '.join(MODEL_FAMILIES)}.\n"
                    f"extra_regressors: a JSON array of 1 to {MAX_EXTRA_REGRESSORS} distinct "
                    f"names from {', '.join(VOCABULARY)}.\n"
                    # The first real-store sprint named three of one group in every slot (R225).
                    "Some of those are linear combinations of each other and of the HAR base, so "
                    "naming more is a singular regression; name "
                    + "; ".join(
                        f"at most {allowed} of {', '.join(members)}"
                        for members, allowed in _IDENTITY_GROUPS
                    )
                    + ".\n"
                    f"symbol: one of {', '.join(universe)}.\n"
                    f"horizon: an integer, one of {', '.join(str(h) for h in AGENT_HORIZONS)}.\n"
                    # A list even for one name: the first live sprint sent a string (R224).
                    f"reference: a JSON array of names from {', '.join(REFERENCE_CONTESTANTS)}, "
                    f"{REFERENCE_CONTESTANTS[0]} first, e.g. "
                    f"{json.dumps([REFERENCE_CONTESTANTS[0]])}"
                ),
            )
        )
        emitted.append(parse_payload(completion.text))
    return {"emitted": emitted}


def runner(proposal: Proposal, *, submit: Callable[[Proposal], ChildRun]) -> dict[str, Any]:
    """Submit one validated proposal and wait. Contains no LLM.

    Returns the child's outcome reduced to JSON the checkpointer can hold: the verdict name (R203),
    the raw exit code, the cohort and the stamps the child recorded on its own ledger lines, and,
    per loss, the MCS membership it computed. The full comparison record stays in the child's own
    ledger; the reduction exists so the Critic and the hub have dev metrics without a file read.
    A ``submit`` that raises is not caught here: a crash of the seam is the hub's to surface.
    """
    child = submit(proposal)
    result = child.result or {}
    comparisons: list[dict[str, Any]] = []
    for record in result.get("comparisons", []):
        mcs = record.get("mcs") or {}
        comparisons.append(
            {
                "loss": record.get("loss"),
                "contestants": record.get("contestants"),
                "refusal": record.get("refusal"),
                "mcs_sizes": mcs.get("sizes"),
            }
        )
    verdict = child.verdict
    return {
        "proposal_id": proposal.proposal_id,
        "verdict": verdict,
        "exit_code": child.exit_code,
        "pid": child.pid,
        "status": result.get("status"),
        "reason": result.get("reason"),
        "cohort": result.get("cohort"),
        "at": result.get("at"),
        "git_sha": result.get("git_sha"),
        "trials_written": result.get("trials_written"),
        "comparisons": comparisons,
        "output_tail": "" if verdict == "evaluated" else child.output_tail[-1000:],
    }


_MARKDOWN = "*_`#>"


def critic(
    state: Mapping[str, Any], *, completer: Completer, dev_metrics: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Comment on hypothesis, configuration, trial count and dev metrics. Gates nothing.

    The reply is stored as text with a ``verdict`` read from its first word (``approve`` or
    ``reject``, else ``unparsed``). The hub writes it to the sprint log and **never consults it**;
    see the module docstring for why. ``dev_metrics`` is the Runner's reduced output.
    """
    metrics = [
        f"- {m['proposal_id']}: {m['verdict']}" + (f" ({m['reason']})" if m.get("reason") else "")
        for m in dev_metrics
    ]
    completion = completer(
        Prompt(
            system=(
                "You are the critic. Begin your reply with APPROVE or REJECT, then say whether the "
                "configurations follow from the hypothesis and whether the evidence is thin."
            ),
            user=(
                f"Hypothesis: {state['hypothesis']}\n"
                f"Agent ledger lines before this sprint: {state['ledger']['agent_trials']}\n"
                "Dev-window outcomes:\n" + ("\n".join(metrics) if metrics else "- none")
            ),
        )
    )
    text = completion.text.strip()
    # Markdown around the first word (``**REJECT**``, ``## APPROVE``) is not part of it (R224).
    bare = text.lstrip(_MARKDOWN + " \t\r\n")
    first = bare.split(maxsplit=1)[0].strip(":.,;-" + _MARKDOWN).lower() if bare else ""
    return {
        "critique": {
            "text": text,
            "verdict": first if first in ("approve", "reject") else "unparsed",
        }
    }


def invented_numbers(report: str, figures: str) -> tuple[str, ...]:
    """Numeric literals in ``report`` that appear nowhere in ``figures``, in order, unrepeated.

    Digits only; see the module docstring for what that leaves unchecked. An empty tuple means no
    literal was invented, which is **not** the same as the report being accurate.

    Digits that are part of a name are not literals (R229). In the report, a digit run is skipped
    when the character before it is a letter or an underscore (``semivar_neg_proxy_d1``, ``d22``,
    ``iv_h22``, ``baa10y``), or when it lies inside a sprint id (``2026-11-13T054207Z``, with or
    without a ``-02`` proposal suffix, or the short ``054207Z``). Everything else is still a
    literal: a number followed by a unit (``22%``, ``5bp``), a horizon in prose (``22-session``,
    ``h=66``), a list marker (``2.``, ``(2)``), a bare suffix (``run -02``), a sign or a line start
    in front of it. The figures side is not filtered: every digit run there may be quoted, so a
    report's ``run -01`` still matches the figures' ``...Z-01``.
    """
    allowed = set(_NUMBER.findall(figures))
    ids = [m.span() for m in _SPRINT_ID.finditer(report)]
    seen: list[str] = []
    for match in _NUMBER.finditer(report):
        start, literal = match.start(), match.group()
        if start and (report[start - 1].isalpha() or report[start - 1] == "_"):
            continue
        if any(lo <= start < hi for lo, hi in ids):
            continue
        if literal not in allowed and literal not in seen:
            seen.append(literal)
    return tuple(seen)


def reporter(state: Mapping[str, Any], *, completer: Completer, figures: str) -> dict[str, Any]:
    """Draft the sprint's narrative from the harness-rendered ``figures`` and validate its digits.

    Returns the draft, whether :func:`invented_numbers` found nothing, and what it found. A draft
    that fails is still returned, not discarded; the hub decides what stands as the report. The
    validation covers digits only.
    """
    completion = completer(
        Prompt(
            system=(
                "You write the sprint's research-log entry in plain prose. State only figures "
                "that appear in the list you are given; do not compute or estimate any other."
            ),
            user=f"Hypothesis: {state['hypothesis']}\nFigures:\n{figures}",
        )
    )
    draft = completion.text.strip()
    invented = invented_numbers(draft, figures)
    return {"report": {"draft": draft, "validated": not invented, "invented": list(invented)}}


def proposal_from_state(entry: Mapping[str, Any]) -> Proposal:
    """Rebuild a :class:`Proposal` from its checkpointed JSON form (lists become tuples)."""
    return Proposal(
        proposal_id=entry["proposal_id"],
        family=entry["family"],
        extra_regressors=tuple(entry["extra_regressors"]),
        symbol=entry["symbol"],
        horizon=entry["horizon"],
        reference=tuple(entry["reference"]),
    )


def proposal_to_state(proposal: Proposal) -> dict[str, Any]:
    """A :class:`Proposal` as plain JSON (tuples become lists), for checkpointing and logging."""
    plain: dict[str, Any] = json.loads(json.dumps(asdict(proposal)))
    return plain
