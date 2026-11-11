"""The human gate: approving an agent candidate, in a process of its own.

A separate package rather than a verb on ``volab.agent`` so that the separation is an import-graph
fact instead of a convention -- a verb in the same module means a sprint *could* promote in-process
and only discipline would stop it (R188). The edge runs one way: this package imports
``volab.agent.sprintlog`` and never the reverse, and both directions are asserted.

**Promotion here writes a ``promoted`` event and nothing else.** No champion pointer moves and
nothing a daily stage consumes is written, because the framework's own ordering puts a single logged
holdout check *before* promotion and that check is Phase 6's. So the framework's "``interrupt``
before promotion" is met by pause-and-persist, approval in a separate process, and a recorded
decision -- not by a champion changing hands.

Filled in by P5-E1; this stub exists so the fences of sprint A have a package to scan.
"""
