# The acceptance kit: a merge gate that checks the numbers, not only the tests

**Status: design, approved in conversation 2026-10-28.** Approval here permits writing the
implementation plan, not implementing.

## Intent, and the evidence it rests on

Every gate this repository has checks **behaviour**. None checks **claims**. The difference is not
theoretical: on 2026-10-28 a delivery audit found three figures in live source that no committed
probe prints, and the suite had been green across all of them for two sprints.

| where | said | the probe prints |
|---|---|---|
| `models/garch.py` | analytic GJR "17.7% low at h=66 ... on the probe series" | **20.1%** |
| `models/garch.py` | geometric form "wrong by up to 51%", "reproduces an 80-bit evaluation exactly" | **44%** at h=22, **71%** at h=66, against exact rationals |
| `compare/cohorts.py`, `compare/__init__.py`, `tests/test_compare.py` | EGARCH h=22 "230.79% on SPY" | nothing; no such figure exists |

The 17.7% was worse than stale: **the decisions log already recorded 20.1% against it**, so live
source contradicted the project's own record while every test passed. The "80-bit evaluation" claim
had been **explicitly struck by R125** and survived verbatim in the module. And the 230.79% figure
sat in **three** places, of which the audit found one.

R32 already requires that a cited figure have a re-runnable probe. It is a convention, and this is
the third time in this project that a convention without a witness has failed — R79 and R82 both
claimed witnesses that did not exist, and R129's parent-package gap was a witness whose docstring
described a stronger check than the code performed. **The pattern is narrower than "not enough
tests": in each case the test existed and passed, and the prose around it was wrong.**

So: a kit that re-derives the repository's live numeric claims, refuses a claim that has no probe,
and blocks a merge when either fails.

## Goals

1. Every numeric claim in `src/` is either **pinned to a probe** that reproduces it, or **exempt
   with a stated reason**. A third state does not exist.
2. A claim whose probe disagrees with it **fails the build**, naming the file, the claim and both
   values.
3. A **new** unregistered claim fails the build, so the kit cannot be outgrown by accretion.
4. One named entry point, **identical locally and in CI**, so a developer reproduces a red build
   rather than guessing at it.
5. `main` refuses a merge until it is green.

## Non-goals

- **Pinning the `docs/` figures.** 214 numeric claims live there, and the decisions logs are
  *dated records*. R90's "17.7% on the design's own series" and R129's "20.1% on the committed
  probe's" are both true, of different series, on different dates. A gate that forced them to agree
  would overwrite the record rather than protect it. Freezing history is not checking a claim.
- **Tolerance bands and flake budgets.** Unnecessary: every probe is deterministically seeded
  (`zlib.crc32` of `name|symbol|origin|horizon`, R90) and the six-seed spreads use fixed strings, so
  even the Monte-Carlo figures reproduce bit-exactly. If a probe is ever made non-deterministic,
  that is a change to reject, not to accommodate.
- **Rewriting the probes.** They gain a machine-readable mode; their prose output stays, because a
  human reading a probe run is the reason they exist.

## Anti-goals — what would make this a failure even if it shipped

- A gate that is **green by editing the number**. That is exactly how `17.7%` survived, and
  category 3 below is where it would happen.
- A gate whose expensive checks run **somewhere other than the merge path**. The exhaustive
  truncation sweep — 2,245 cutoffs, the strongest leakage witness here — was kept out of
  per-commit runs for cost and has therefore **never run automatically**. Nobody reads a green
  nightly, and a failure found after merge is a failure found on `main`.
- A gate so noisy that the habit becomes adding exemptions.

## The three categories of figure

The distinction is the design. It emerged in part from R133, where an implementer had to pin real
data in a test and flagged it rather than hiding it.

**1. A figure in prose, under `src/`.** A claim about the code *as it is now*. Must be
re-derivable, and if it drifts it lies. **28 distinct claims today.** All three audit findings were
here. These are pinned.

**2. A figure in a dated record, under `docs/`.** A measurement taken on a date, often on a series
or a tree that no longer exists. **Must not be pinned.** The weaker rule, a convention rather than
a gate at first: a figure introduced into `docs/` as *current* names the probe that prints it.

**3. A figure pinned inside a test.** Already executable and self-proving — but with semantics
unlike any ordinary test: it is **expected to fail** when the data legitimately changes, and the
correct response is **re-measurement, not editing the literal**. `test_the_designs_measured_figures_still_hold`
is the live example, pinning `(251, 119) … (238, 132)`.

Category 3 is the soft spot. A test that pins a measurement therefore takes its expected values
**from the inventory** rather than from inline literals, so changing one is a reviewed diff in one
file with a stated reason. The gate cannot stop a determined edit; it can refuse to let it be quiet.

## Design

### Section 1 — The scanner and the completeness ratchet

A scanner walks docstrings and comments under `src/` and collects **candidate** claims by shape:
a percentage, scientific notation, an `N of M`, a thousands-separated integer. It deliberately
over-collects, because a missed claim is a silent hole and a false positive is one reviewable line.

Every candidate must appear in the inventory as either a `[[claim]]` or an `[[exempt]]` **with a
`reason`**. A candidate in neither fails the build. This is the `ALLOWED = {"store.py"}` pattern the
import ratchet already uses, and it is what makes the kit hold over time rather than at one commit.

Known exemption classes, each needing its reason recorded:

- **Parameter choices.** `CALIBRATION_POINTS = 60`, `MINIMUM_PAIRS = 40`, `SIMULATIONS = 100_000`
  are decisions, not measurements. Nothing reproduces a choice.
- **Labels.** Ruling numbers, section references, dates.
- **Platform-dependent figures.** R125 records that `numpy.longdouble` *is* `float64` on this arm64
  Mac. CI runs `ubuntu-latest`, where it is genuinely 80-bit. A claim resting on local hardware
  would pass here and fail there, so it must be exempt with that stated. (The geometric-sum probe
  is safe: it compares against `fractions.Fraction`, chosen precisely *because* longdouble was
  vacuous.)
- **Structural counts** that a test already asserts directly, e.g. "seven contestants at h=1",
  where the assertion is the witness and a probe would be a second copy.

**Expect this list to grow during implementation.** It was assembled from one session's memory, and
the first full scan over all 28 claims will reject things that are not measurements in ways nobody
predicted. Record each new class with its reason; do not widen a pattern to make a scan quiet.

### Section 2 — The inventory and the anchor

One committed file, `tools/figures.toml`. Each pinned claim carries:

- `file` — the source file holding the claim.
- `anchor` — a substring that must occur **exactly once** in that file and that contains the
  figure. Occurring zero or twice is itself a failure, naming the claim.
- `probe` — the command that prints it.
- `field` — a dotted path into that probe's machine-readable output.
- `transform` — optional, for prose that rounds, e.g. one decimal place.

```toml
[[claim]]
file   = "src/volab/models/garch.py"
anchor = "20.1% low at h=66"
probe  = "tools/measure_garch_forecast_error.py"
field  = "analytic_vs_exact.gjr11_skewt.h66.analytic_percent_low"
round  = 1
```

The gate, per claim: assert the anchor is unique, extract the number from it, run the probe, read
`field`, compare after `transform`, and on failure print the file, the anchor, the claim's value
and the probe's.

**The anchoring cost, stated plainly because it is the main argument against this approach.**
Rewording a sentence containing a pinned figure breaks the build. That is the design working — it
forces you to look at the claim when you edit the prose around it — but it will be irritating, and
if it proves irritating enough the honest response is to revisit the alternative (numbers out of
prose entirely, cited by name) rather than to loosen the anchors.

**One copy of every number** is the property being bought. A registry that also held the value
would recreate the duplication class that made `MONTE_CARLO_ERROR_PERCENT` go stale the moment a
real measurement existed (R119).

### Section 3 — Probes emit addressable values

This is the bulk of the work. The probes compute their inputs in clean helpers — `_arch_analytic`,
`_simulated`, `_recursions`, `_geometric`, `_rational` — and then compute the **derived** quantities
**inside the f-strings**: `100 * (1 - analytic / simulated)` *is* the 20.1%, and it exists nowhere
but at the print site.

So each probe gains a machine-readable mode (`--json`, to stdout), which requires naming those
derived quantities first. **That naming has value independent of this kit**: a figure worth citing
in a docstring is worth a name in code.

Seven probes: `measure_garch_forecast_error.py`, `measure_calibration.py`, `measure_e1_power.py`,
`measure_vintages.py`, `measure_calendar_memo.py`, `sweep_truncation.py`, `reproduce_e1.py`. The
refactor is mechanical per block, because each `block_*` function already owns its numbers.

Constraints: the prose output must not change (it is quoted in decisions logs); a probe must emit
**either** prose **or** JSON, never interleaved; and a probe that cannot produce a value must say so
in the payload rather than omit the key, so a missing figure is distinguishable from a zero.

### Section 4 — The runner and the gate

One entry point, run identically by a developer and by CI. Ordered **cheapest first**, so a typo
fails in ten seconds rather than ten minutes:

1. `ruff check .`
2. `ruff format --check .`
3. `mypy src` (strict)
4. the store rebuild — `ingest build-raw`, `ingest curate`, `features build` (~70s, offline)
5. `pytest -q -rfEs` (~1442 tests, ~3min)
6. the figure checks — the scanner, the inventory, every probe (~5min)
7. the exhaustive truncation sweep — 2,245 cutoffs (~3min)

It must **pass through** every stage's output rather than summarising it. Four separate times in
one session a wrapper of this author's discarded evidence: a redirect lost a truncation report,
`; echo "exit=$?"` masked an exit code, `-r s` dropped pytest's FAILED line, and `${PIPESTATUS[0]}`
silently printed empty under zsh. Use `pipefail`, `tee`, and an explicit exit code per stage.

**Branch protection on `main`** requires this check, plus a pull request. It does not exist today —
`main` currently returns `Branch not protected`, so a red PR can be merged and the whole kit would
be advisory. This is a repository settings change and belongs to the user.

### Section 5 — What the gate must refuse to do

- It must not **rewrite** a claim to match a probe. A disagreement is a question for a human: either
  the code changed and the prose should follow, or the prose was always wrong. Only a person knows
  which, and the second case is a finding worth recording as a ruling.
- It must not **skip** a probe that fails to run. A probe that errors is a red build, not an absent
  check — a silent skip is how the "data arrived, no witness arrived" gap (R129) was able to persist.
- It must not read past `dev_end`. Probes run under the same autouse holdout fixture as the suite;
  `allow_holdout` is never set and `VOLAB_HOLDOUT_UNLOCK` is never read.

## Left to the builder

- The scanner's exact candidate patterns, and whether it reads the AST or the file text. The AST
  gives docstrings precisely but misses `#` comments, which hold several of the 28.
- Whether the inventory is TOML, or a Python module with typed entries that `mypy` can check. TOML
  is more readable in review; a module gets type checking for free.
- Where the runner lives — a `tools/` script, or `python -m volab.gate`. It must not become
  importable production code, and it must not widen the import ratchet.
- How a category-3 test reads its values from the inventory without the test package importing the
  gate's machinery.
- Whether the CI workflow calls the one runner or keeps its current step list and adds to it. The
  runner is the point; CI having its own copy of the order would be a second source of truth.

## Open questions for the user

1. **Branch protection** is a repository settings change and has not been made. The kit is
   advisory without it.
2. The `docs/` convention in category 2 is **not mechanically enforced** in this design. It could
   later be ratcheted — a figure introduced into `docs/` naming no probe fails — but that needs the
   214 existing claims triaged first, which is its own piece of work and is not proposed here.

## Calls made by the author, so they can be checked rather than assumed

- **Anchored-in-prose over a registry**, because this repository's docstrings *are* its record and
  moving the numbers out would cost more than the fragility saves. The user chose this from three
  options with the trade-offs stated.
- **Everything on every PR**, chosen by the user, on the argument that a split gate is how the
  exhaustive sweep came never to run.
- **`docs/` left unpinned**, author's call, on the grounds that pinning a dated record destroys it.
- **No tolerances**, author's call, because the probes are deterministically seeded.
- **Over-collect in the scanner and exempt with reasons**, author's call, because a missed claim is
  silent and a false positive is a reviewable line.
