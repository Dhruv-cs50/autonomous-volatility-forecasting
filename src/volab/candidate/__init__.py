"""The evaluation child: one agent proposal, one fresh interpreter, its own two ledgers.

Separate from ``volab.agent`` because the agent must not reach a read path and this module must
(R192). It is the only writer of ``data/agent_trials.jsonl`` and ``data/agent_comparisons.jsonl``,
and it reaches them through the ``append`` callable ``run_configuration`` already takes (R97) --
so **the agent's runs cannot append to the reported trial ledger, because they are handed a
different ``append``**, which is a fact about the wiring rather than a rule anyone follows.

A fresh interpreter per proposal is not a preference: ``volab.daily``'s docstring records that a
memoised full-sample statistic filled by one build and reused by the next is invisible to
truncation equivalence *within* a process, and a sprint evaluating several proposals in sequence
would reintroduce that across proposals, in a loop.

It re-validates the proposal file it is handed, because the gate's verdict does not travel with it.

Filled in by P5-C1; this stub exists so the fences of sprint A have a package to scan.
"""
