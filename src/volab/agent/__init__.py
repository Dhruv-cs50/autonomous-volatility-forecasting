"""The research agent: it proposes experiments and never touches the evaluation path.

**This package imports nothing that reaches ``volab.store``, and that is the whole design.** Not a
convention and not a prompt instruction -- an import-graph fact, asserted transitively by
``tests/test_agent_discipline.py``. The project exists because an LLM reviewer in a published
framework approved a feature built from future intraday bars, so a refusal that lives in a sentence
of instruction is the one defence this repository will not accept (R192).

The consequences are visible in what is *absent* here. There is no ``end`` parameter, no ``start``,
no stride, and no date-typed field of any kind on any tool: the harness fixes the window, so a peek
is unrepresentable rather than forbidden (R184). The agent authors no code -- it selects from a
closed vocabulary derived from the feature engine -- so "a feature built from future bars" is not
something it can express (R185). Its evaluations run in a fresh interpreter each
(``volab.candidate``) and append to their own ledgers, never to ``data/trials.jsonl``, because R139
is a user decision (R183). Promotion lives in ``volab.promote``, a separate package reached by a
separate process, so a sprint cannot promote what it just proposed (R188).

What could *not* be made structural is listed in the design under "What is not structural" --
overfitting through many trials is counted rather than refused, report tone cannot be validated, and
"a human approved" is really "a different process ran". Said there rather than papered over here.

Filled in by P5-D4; this stub exists so the fences of sprint A have a package to scan.
"""
