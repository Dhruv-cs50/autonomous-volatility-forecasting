# Phase 2 — decisions taken during execution

`phase0-decisions.md` and `phase1-decisions.md` are the model: every decision the brief did not
settle, recorded with its reasoning and **what it costs if wrong**, because a decision made on
someone's behalf should be reviewable rather than invisible.

Numbering continues Phase 1's, which ended at **R32**. Phase 1's closing ruling is the one this
phase is written against:

> **R32: a stated "cannot" is a test, not a sentence.** Every mechanical guarantee in a README,
> docstring or spec must name an executable witness, and one without a test id is an unverified
> claim.

Phase 1's record was that six separate times a badly chosen leakage test let a real forward peek
through, and **every one was found by running something, never by reading it**. That record now
stands at seven, and the seventh is in this document.

---

## Every figure in this document names what prints it

R32 is about guarantees: a stated "cannot" must name an executable witness. Applied to
*measurements* it says the same thing one step over — **a quoted number must name the thing that
prints it, or it is a figure on trust.** For most of Phase 2 it was. The board carried that as a
named risk: the plant and the probes were run and their output quoted, but nothing was committed,
so the *properties* had standing tests while the *numbers* had none.

`tools/` closes it. Every figure below is now in one of three classes, and says which:

* **a `tools/` command** — the number is reproducible from a checkout. Run it and diff.
* **an existing test id** — the property is pinned by the suite, which asserts rather than prints.
* **historical** — taken on a named tree, usually against code that has since been fixed, and
  **not reproducible on HEAD**. Said so explicitly rather than left to look re-runnable. The
  seventh escape's first run, R72's 13 origins and R78's planted pass are all of this kind: they
  are records of what happened, not claims about the current tree.

**Every wall-clock figure anywhere in this document is historical, without exception**, and that
is a rule rather than a list: R38 says why, and the probes send timings to stderr so one cannot
leak into a diffable figure. That covers the tier table's duration column, the 5m44s beside it,
R38's own table, R47's "4s of the suite's 69s" — stale against R38's 89.2s for the final tree,
and left as the record of when it was taken — R57's per-origin cost, and the rebuild figures in
R79. Each is what some machine did once. A delivery audit found these uncovered by an earlier
version of this section, which claimed "every figure" while classing only the fenced blocks.

Two conventions those scripts follow, both of which exist because of something this phase got
wrong. **Wall clock goes to stderr**, never into the diffable output — R38's timing went stale
three times, and a duration measured while three probes share a machine is not a figure anyone
should quote. And **the sweep is imported from the test module that carries it**, so a figure
here comes from the code cited here rather than from a second implementation that agrees today.
`tools/README.md` has the inventory.

---

## The three measurements taken before any code was written

Each changed the design, and all three were run against `d18f004` (main, with Phase 1 merged)
before a line was written. The pattern is deliberate: Phase 1's expensive lesson was that prose
about a leakage property is worth less than one run.

### M1 — a post-load slice on `as_of` *is* what the store would hand over

> Prints both blocks below: `uv run --locked python tools/measure_vintages.py`

Truncation equivalence needs the truncated side to be the panel the store would have returned at
`t`, not merely a filtered copy of a later panel. Those differ the moment a key carries several
vintages: the store keeps the latest vintage at or before `end`, where a post-load filter drops a
revised row instead of falling back to its earlier one.

```
bars_daily: rows=6936 dup(symbol,ts)=0 dup(symbol,as_of)=0
vix_family: rows=8079 dup(symbol,ts)=0 dup(symbol,as_of)=0
fred_daily: rows=7118 dup(symbol,ts)=0 dup(symbol,as_of)=14
```

The 14 are the 7 known FRED publication-calendar collisions, two rows each, differing in `ts`.
The counts are over the whole universe, since the question is whether a vintage exists anywhere.
Then checked directly rather than inferred — slice against `load_panel(end=t)`, three cutoffs in
each of three datasets, **9 of 9 byte-identical**. The bars comparison is on one symbol, because
`volab.panel.load_bars` reads one symbol and comparing a frame no caller asks for would prove
less:

```
bars_daily 2019-04-18: slice=(627, 5) store=(627, 5) identical=True
bars_daily 2023-06-15: slice=(1674, 5) store=(1674, 5) identical=True
bars_daily 2025-01-09: slice=(2067, 5) store=(2067, 5) identical=True
vix_family 2019-04-18: slice=(2112, 5) store=(2112, 5) identical=True
vix_family 2023-06-15: slice=(5527, 5) store=(5527, 5) identical=True
vix_family 2025-01-09: slice=(7099, 5) store=(7099, 5) identical=True
fred_daily 2019-04-18: slice=(2096, 5) store=(2096, 5) identical=True
fred_daily 2023-06-15: slice=(5216, 5) store=(5216, 5) identical=True
fred_daily 2025-01-09: slice=(6386, 5) store=(6386, 5) identical=True
```

So one load plus a slicer is sound, and 2,300 store reads per sweep are not needed.

- **R33** `volab.panel.truncate` is a post-load slice, not a re-read, on the strength of M1 — and
  it carries its own witness rather than this measurement:
  `test_the_truncated_panel_is_what_the_store_would_have_handed_over` compares the slice against
  `load_panel(end=cutoff)` and first asserts no `(symbol, ts)` duplicate exists. *Cost if wrong:*
  the day curated starts carrying revisions, that test fails and names the remedy in its own
  message — re-read per cutoff — rather than the claim quietly becoming false.
- **R34** A second finding from the same probe, and a trap: a real loaded panel's `as_of` is
  `datetime64[us]`, not the `date` objects synthetic test panels carry, and
  `panel["as_of"] <= some_date` raises `TypeError: Invalid comparison between dtype=datetime64[us]
  and date`. Every `as_of` comparison in this phase goes through `volab.panel.as_of_dates`.
  *Cost if wrong:* a crash, loudly, which is the harmless direction.

### M2 — E1's detection power on real data is 2.2%, not the synthetic 8%

E1 ORs a band on the *next* session's return into the semivariance sign mask. Truncation at `t`
removes exactly one row's forward view — `t`'s own — so the two tables disagree at cutoff `t`
**iff** `t`'s next return lies in the band. Over SPY's 2,312 dev sessions:

> Prints this block: `uv run --locked python tools/measure_e1_power.py`

```
sessions: 2312
sessions whose NEXT return is in (0.03, 0.10): 19
  ... in (-0.10, -0.03): 33
hit rate: 0.0082  (the positive band alone)
union: 52 of 2312, rate 0.0225
a 40-cutoff sample expects 0.9 hit(s)
P(a 40-cutoff sample sees none): 0.403
informative cutoffs the sweep would visit: 2245
of those, E1 is detectable at: 52
first three: [datetime.date(2018, 2, 2), datetime.date(2018, 2, 7), datetime.date(2018, 10, 9)]
```

**Two corrections the script forced, and they are the argument for writing it.** The earlier
block printed one `hit rate: 0.0082` line, which is 19/2,312 — the *positive band alone* — while
the sentence beside it described the union. Both are now printed and labelled. And "about a third
of the time" was arithmetic nobody had run: `(1 − 0.0225)^40 = 0.403`, so a 40-cutoff sample
misses a live forward peek **about two times in five**, which is worse than the figure that was
there and makes R35's case more strongly rather than less.

52 of 2,312 as the union, 2.2%. **A 40-cutoff sample expects 0.9 hits**, so a sampled sweep would
have reported "no disagreement" against a live forward peek about 40% of the time. The
prototype's 3-of-38, and the 8% in this heading, are **historical**: they came from a prototype's
synthetic panel carrying a deliberate +5% move every 17 sessions, which is not a fixture in this
repository and is not reproducible here. They are in the record because the gap between 8% and
2.2% is what made this measurement worth taking before any code was written.

- **R35** Sampling is kept as the cheap per-commit tier **with its power stated**. Two further
  tiers carry the coverage, and the distinction between them matters because an earlier version
  of this ruling blurred it and a delivery audit called that out:

  * **The dev-window proof is the recorded exhaustive sweep**, `VOLAB_PIT_EXHAUSTIVE=1`: 2,245
    cutoffs, 0 disagreements clean and 52 with E1 planted, 6m00s. That is the only evidence
    covering the whole dev window, and it is not per-commit.
  * **The walk-forward is exhaustive over its own origins, which are not the dev window.** It
    rebuilds the table at every origin it evaluates from a panel truncated there, so comparing
    its rows against the persisted table checks every origin *it visited* — 251 of 1,255
    sessions in the recorded run, which used a stride of 5, and 251 of the 2,312 dev sessions.
    Saying it "sweeps every session" was the adjacent-truth shape of C1: true of the code at
    `--every 1`, false of any run anyone had made.

  Hand-feeding the sampler the 52 band dates was
  considered and **rejected**: that is tuning the test to the plant it is meant to catch blind,
  and a test tuned to one plant is evidence about that plant and nothing else. *Cost if wrong:* a
  per-commit run can miss a narrow peek that the walk-forward then catches, one commit later
  rather than at the commit that introduced it.
- **R36** The sweep's cutoff set is a **stride plus derived awkward positions**, never a seeded
  random sample. A stride is a function of the calendar alone, so a failure reproduces exactly
  and nobody has to reason about a seed. The awkward positions — a half-day, a holiday eve, the
  session before a long gap, the last session before `dev_end`, the first with a full history —
  are read from the exchange calendar and each asserted to be what its key claims, so a
  hand-listed date that stopped being a half-day fails rather than silently becoming ordinary.
  Witnesses: `test_the_awkward_cutoffs_are_what_they_claim_to_be`,
  `test_every_real_cutoff_removes_at_least_one_row`.
  *Cost if wrong:* less coverage variety than a random sample would give across runs.

### M3 — E2 can hide outside the feature package, so the AST ban cannot close its class

> Prints this block: `uv run --locked python tools/measure_calendar_memo.py`

The cheap candidate for E2 was an AST ban on module-level mutable state in
`src/volab/features/`. It is blind to state parked on an object handed *in*:

```
attribute set on calendar: OK -> {'scale': 1.234}
same object from a second get_calendar: True
memo survives the second call: {'scale': 1.234}
__slots__? ()
```

`get_calendar` is `lru_cache(maxsize=8)`d and returns the same calendar to every caller, and the
class accepts arbitrary attributes although `__slots__` is `()`. A memo written by
`build_features` therefore survives into the next call with **no module-level name in the package
at all**.

- **R37** E2's class is closed by **subprocess isolation**, not by the AST ban. The ban is sound
  about the construct it names; it is the *scope* that fails, and widening the scope to "every
  object any feature function can reach" is no longer an AST question. Subprocess isolation is
  behavioural and does not care where the state was parked. The rejected option's cost is also on
  the record: `IV_SERIES`, `FRED_SERIES` and `HAR_WINDOWS` are module-level dicts used as
  constants and a package-scoped ban would have had to outlaw all three.
  Witness: `test_a_memo_on_the_calendar_defeats_a_package_scoped_ast_ban`. *Cost if wrong:* two
  interpreter starts per cutoff, about a second each, so the isolated sweep is a handful of
  cutoffs rather than hundreds.
- **R38** The costs that decided the sweep's shape, measured rather than inferred, which closes
  the "cost at realistic scale" unknown the pivot note left open: the real panel loads in
  **6.6s**; one `build_features` over all 2,312 sessions is **0.07s**; a full-vs-truncated pair is
  **~0.07s**; a 37-cutoff sweep is **2.70s**; the exhaustive 2,245-cutoff sweep is **6m00s**.
  Compute is not the constraint for the sweep itself.

  **Every figure in this ruling is historical**, and it is the one ruling `tools/` deliberately
  does not make reproducible. A timing is a property of a machine and a moment, not of the tree,
  so a script that printed one would invite exactly the staleness this ruling is a correction
  for. That is why the probes send wall clock to stderr. Read these as a record of what the
  decision was taken on, not as numbers to diff.

  **What it did cost is the suite**, and this number has been wrong three times, so here is every
  measurement rather than one more confident summary:

  | Tree | Tests | Wall clock | Machine |
  |---|---|---|---|
  | before Phase 2 (`d18f004`) | 644 | 36.8s | idle |
  | Phase 2, mid-build | 706 | 69.0s | idle |
  | Phase 2, mid-build | 706 | 163.9s | two subagents running |
  | Phase 2, final | 707 | 89.2s | idle |

  So **roughly 37s to roughly 90s**, about 2.4x, and a contended run can be twice that again. The
  real-data tiers are nearly all of the increase — the persisted-table byte comparison, the
  walk-forward's per-origin comparison and the sampled real sweep, at roughly 13-20s each. The
  first version of this ruling quoted the pre-Phase-2 figures and a delivery audit caught them as
  stale; the second said "about 120s" from the contended run; the third said 69.0s, which was
  real but was superseded by the tests added after it. The table is the fix for a number that
  keeps going stale. A per-commit ratchet that reads real data is not cheap, and the alternative —
  not running it per commit — is how a regression reaches a later phase.

---

## The seventh escape: extent dependence at a cutoff with no bar

Found by the new sweep on its **first run**, before any plant.

> **Historical, and deliberately not reproducible.** The defect is fixed (R39), so a sweep on
> HEAD prints `0 of 79` -- which is the point. What `tools/` makes re-runnable is the *method*
> that found it: `tools/sweep_truncation.py --tier synthetic`, which would print the line below
> again on any tree where the span fix were reverted.

```
1 of 79 cutoffs disagree: [(datetime.date(2024, 4, 17), 'row counts differ: 74 vs 73')]
```

Probed rather than guessed:

```
is the cutoff a session?          True
does the subject have a bar on it? False
last bar at or before the cutoff:  2024-04-16
full rows 74 last as_of 2024-04-17
trunc rows 73 last as_of 2024-04-16
the extra row's features all null? False
```

`build_features` derived its session index from `min(days)` to `max(days)` over **every** bar the
panel held, and applied the cutoff only to the finished frame. So cutting at a session the
subject did not trade gave two different tables: a panel reaching past that session put the span
beyond the cutoff and emitted a row for it — carrying the exogenous observations that were
genuinely knowable that day — while a panel physically stopping at the same cutoff ended one
session earlier and emitted no such row.

That is extent dependence, which is **the one defect `through` exists to prevent**. It survived
Phase 1 because the perturbation fixture could not express "the cutoff session has no bar".

- **R39** The span now runs from the subject's first bar to the **later** of its last bar and
  `through`: `span_end = max(max(days), through)`. Both halves are load-bearing, for opposite
  reasons, and the first attempt at this fix got it exactly wrong.

  **The wrong fix, and what caught it.** The first repair cut the span *back* to the last bar at
  or before `through`. That restored the invariant and **silenced all three planted forward
  peeks at once**, because the engine no longer held a future to read — which is precisely the
  trap spec section 9 names: an engine handed only the rows it is allowed to use produces
  identical output whether or not the future exists, and every leakage test passes by testing
  nothing. It traded a narrow real bug for a vacuous suite, the worst trade available. The red
  cases caught it in one run; reading the diff did not, and I had already written the comment
  explaining why the wrong version was right. *That is the phase's lesson repeating itself on
  me, not on an agent.*

  Reaching the span *forward* keeps the knowable exogenous row instead of discarding it, keeps
  the future in front of the engine, and makes the emitted row set a function of the cutoff and
  the calendar alone. *Cost if wrong:* a symbol whose bars stop before `dev_end` would gain
  null-feature rows out to `dev_end`. Measured not to apply — all three symbols' bars reach
  `dev_end` — and pinned by
  `test_the_span_fix_did_not_move_the_persisted_feature_table`, which asserts that premise for
  the whole universe and byte-compares a fresh build against the 2,312 persisted partitions.
- **R40** The engine still **refuses** a malformed row dated after the cutoff, where a truncated
  panel simply would not have it. Validation runs over every bar handed over; only the span is
  bounded. Refusal is fail-closed — it declines to build rather than carrying a value across —
  but the two panels do differ in that one behaviour, so it is stated in the README's limits
  rather than papered over. *Cost if wrong:* a caller with a corrupt future row gets a refusal
  instead of a table.

---

## Truncation equivalence: the headline numbers

`tests/test_truncation_equivalence.py`. Built at cutoff `t` from the full panel, built again from
a panel physically stopping at `t`, compared byte-exactly with `volab.identity`.

### The clean row, which matters as much as the red ones

A check that fired on honest code would be useless.

> Prints each row: `uv run --locked python tools/sweep_truncation.py --tier synthetic`, then
> `--tier real`, then `--tier real --exhaustive`. A passing pytest run prints "1 passed", never
> "0 of 2245 cutoffs disagree", which is why the clean row needed a script of its own.

| Tier | Cutoffs | Disagreements | Wall clock |
|---|---|---|---|
| synthetic, exhaustive | 79 | **0** | ~2s |
| real curated, sampled (stride + awkward) | 63 | **0** | ~4s |
| real curated, **exhaustive** (`VOLAB_PIT_EXHAUSTIVE=1`) | 2,245 | **0** | 6m00s, re-confirmed on the final tree at 5m44s |

All three re-confirmed from `tools/` on the current tree: `0 of 79`, `0 of 63`, **`0 of 2245`**.
The durations in the column above, **5m44s included**, are historical by the rule stated at the
top of this document: the probes print no timing to stdout, and the re-confirming runs shared a
machine with two others, so no new duration is recorded here to replace them.

### Proof that it bites: E1 planted in `src/`, not monkeypatched

> Prints everything in this section: `uv run --locked python tools/reproduce_e1.py --exhaustive`.
> The plant is `tools/e1_plant.patch`, applied to a throwaway `git archive HEAD` tree and **never
> to your checkout** — see R80 for why each half of that matters, and for the defect the driver's
> own first run produced.

The scan-silent spelling — `drop` / `set_axis` / `reindex` with `Index.delete`, no `shift`, no
slice, no `center` — written into `semivariance_proxy` in `src/volab/features/realized.py`.
That spelling is the committed patch's, verbatim, and the reason it is not a negative slice is on
Phase 1's record: the first E1 variant used `index[:-1]`, the AST scan caught it, and the escape
would have been understated.

**Phase 1's method, against that plant.** As originally recorded — **historical, and the tree it
was taken on is `21dc15b`**, sprint 1's close, which held 663 tests and only two Phase 2 test
files because the walk-forward's two did not exist yet:

```
=== PHASE 1 POINT-IN-TIME SUITE, E1 PLANTED ===
100 passed in 4.51s

=== WHOLE SUITE MINUS THE NEW PHASE 2 FILES, E1 PLANTED ===
FAILED tests/test_realized.py::test_signed_jump_is_negative_when_downside_dominates
1 failed, 644 passed in 25.70s
```

**That tree is named here because the figure was wrong rather than stale, and the difference
matters.** The section around it describes the final tree, where the same exclusion set — the
four files listed by `tools/reproduce_e1.py` — leaves **648**, not 645. The obvious explanation,
that the suite had grown, is false: `git diff 3c7e087 HEAD -- tests/` shows no test file outside
the exclusion set changed at all. Collecting on the archived trees settles it: 645 is `21dc15b`
with two Phase 2 files, 648 is both `3c7e087` and HEAD with four. So the count was right about a
tree this section did not name, which is the failure mode R32 exists for, one level up from a
missing test id.

Re-recorded on HEAD. This is the driver's own output, as an excerpt: the two anti-vacuity
lines it prints between these sections are shown further down, under R80.

```
=== BASELINE, SAME TREE, NO PLANT ===
phase 1 point-in-time suite: 100 passed
SKIPPED [1] tests/test_store_guard.py:342: not inside a git work tree
whole suite minus the phase 2 files: 647 passed, 1 skipped
=== PHASE 1'S METHOD, E1 PLANTED ===
phase 1 point-in-time suite: 100 passed
FAILED tests/test_realized.py::test_signed_jump_is_negative_when_downside_dominates
SKIPPED [1] tests/test_store_guard.py:342: not inside a git work tree
whole suite minus the phase 2 files: 1 failed, 646 passed, 1 skipped
```

Three things that block carries deliberately. The **baseline runs first in the same tree**, so
the plant's evidence is a difference — 647 passed to 646 passed and one failure — rather than an
absolute count carrying whatever the environment contributes. The **skip names itself**: a tree
built by `git archive` has no `.git`, so `test_store_guard`'s git-work-tree guard declines to
run, and in an ordinary checkout that test passes and the figure is 647 of 648. And the **failure
is named**, because the claim is that the single catcher is one incidental unit value test; a
bare count could not show that it is still the same one.

**100 of 100 point-in-time tests pass with a live forward peek in the source.** The single
catcher is a hand-written unit value test whose returns happen to land in the band — incidental,
not a defence. Phase 1's own **607-of-608** figure is historical — a different tree with a
different suite — and this is the same measurement taken again rather than that one cited.

**Phase 2's method, against the same plant.** The block below is this document's abridgement:
the `...` are elisions made here, the driver prints each disagreement on one unwrapped line, and
it also prints the two `predicted by M2` / `caught set == predicted set` lines quoted further
down. Only the lines shown are claimed to be verbatim.

```
=== SYNTHETIC, EXHAUSTIVE ===
1 of 79 cutoffs disagree: [(datetime.date(2024, 5, 1),
  'semivar_pos_proxy_d1 row 83: np.float64(0.0) vs np.float64(8.105577997667874e-07)')]

=== REAL, SAMPLED ===
1 of 63 cutoffs disagree: [(datetime.date(2025, 4, 2),
  'semivar_pos_proxy_d1 row 2123: np.float64(0.0) vs np.float64(3.979571359720352e-05)')]

=== REAL, EXHAUSTIVE ===
52 of 2245 cutoffs disagree: [(datetime.date(2018, 2, 2), ...),
  (datetime.date(2018, 2, 7), ...), (datetime.date(2018, 10, 9), ...)]
```

**52, which is the number M2 predicted before the sweep was run** — 19 positive-band plus 33
negative-band sessions. A prediction made from the return distribution and then confirmed to the
unit is stronger evidence than the detection alone, because it means the mechanism is understood
and not merely observed.

**And the check is now on the set, not the count.** `tools/sweep_truncation.py` derives
`{t : r[t+1] in the band}` from the returns and compares it against the caught set by equality,
so a figure that matched by coincidence would still fail. On the planted tree it prints
`caught set == predicted set: True` at all three tiers — 1 of 1, 1 of 1 and **52 of 52**. That
is R41's standard, which the synthetic red case already held, applied to the real-data tiers the
decisions log quotes. M2's own first three predicted dates are 2018-02-02, 2018-02-07 and
2018-10-09, which are the first three the exhaustive sweep reports. The real sampled tier caught it at 2025-04-02, the eve of the April 2025
selloff, which is the sort of session the band was chosen to look credible on.

- **R41** The red cases assert the sweep's **exact detectable surface**, not a count.
  `test_e1_is_detectable_at_exactly_the_cutoffs_where_its_band_is_reached` derives
  `{t : r[t+1] in the band}` from the fixture's own returns and asserts the sweep finds precisely
  that set — neither fewer, which would mean the sweep is weaker than the leak, nor more, which
  would mean it fires for a reason nobody has accounted for. *Cost if wrong:* a stricter test to
  maintain. The alternative is the loose assertion that has hidden an escape six times here.
- **R42** The sweep is **blind where the leak's own input is already null**, and that is derived
  rather than tolerated. If `r[t+1]` is null — the next session is a gap — the peek reads null
  from the full panel too and agrees with the truncated one, so there is no difference to find:
  a blind spot of the leak, not of the method. Measured at 2 of 79 on the synthetic fixture,
  either side of its deliberate gap. Witness: `blind_cutoffs` and
  `test_the_sweep_catches_a_scan_silent_forward_peek`, which asserts the caught set is
  exactly the informative cutoffs minus the blind ones.

  **One wrong rule along the way, worth recording because it is a pandas trap.** The first
  version of `blind_cutoffs` also treated `r[t]` being null as blind. It is not:
  `Series.mask(condition, 0.0)` replaces wherever the condition holds, **nulls included**, so a
  forward-dependent mask turns an unknown return into a confident zero. At cutoff 2024-04-18 the
  full panel produced `0.0` where the truncated one produced `NaN`, and the peek was caught at
  the very session whose own return is missing. The shipped `semivariance_proxy` is safe from
  that only because its condition is a comparison on `returns` itself and a comparison against
  NaN is False — which its docstring says, and which is load-bearing rather than incidental.

---

## E2's class, closed

`tests/test_process_isolation.py`. Each side of the comparison in a fresh interpreter, so there
is no first build in the process for state to survive from.

The file carries the **discriminating pair**, which is what makes the mechanism justified rather
than merely plausible. Nothing about the plant changes between the two tests; only the number of
interpreters does.

> Both rows are standing tests rather than a recorded run, which is why they have no `tools/`
> command: `test_a_memoised_full_sample_statistic_escapes_an_in_process_comparison` is the first
> row and `test_process_isolation_catches_the_memoised_statistic_that_escapes_in_process` the
> second. Run `uv run --locked pytest tests/test_process_isolation.py -v` to see the pair.

| Comparison | Memoised full-sample statistic |
|---|---|
| two builds in one process | **agree** — the escape passes for honest |
| one build per process, 3 cutoffs | **disagree at 3 of 3** — caught |

- **R43** `test_a_memoised_full_sample_statistic_escapes_an_in_process_comparison` **asserts the
  escape**, so it fails the day the escape stops working. Deliberate: subprocess isolation is
  only worth its cost while that holds, and a guarantee whose justification has quietly
  evaporated is exactly what this project refuses. The same shape applies to
  `test_a_memo_on_the_calendar_defeats_a_package_scoped_ast_ban`, whose failure would mean the
  cheaper mechanism had become sufficient.
- **R44** The child process is passed `-W error` explicitly. `pyproject.toml`'s
  `filterwarnings = ["error"]` is pytest's, and pytest does not run in the child, so without the
  flag a warning there would pass silently where the same warning in the suite is a failure.
  Witness: `_build_in_a_child` passes it, and
  `test_the_isolated_build_agrees_with_an_in_process_one_on_clean_code` fails if the child
  computes anything other than the suite's own table.
- **R45** The child **never calls `load_panel`**. `conftest`'s autouse `_forbid_holdout_unlock`
  does not exist in that process, so a read there would be unpoliced. The parent reads under the
  policing and hands the panel over as a pickle in `tmp_path`. Pickle rather than Parquet so
  dtypes cross unchanged — the comparison downstream is byte-exact and a Parquet round trip has
  its own documented asymmetries, two of which this phase ran into.
- **R46** The child hashes its columns with **the comparator's own canonicaliser**
  (`volab.identity.canonical_bytes`), not a reduction written for the pipe. A digest that could
  disagree with `assert_byte_identical` about one pair of tables would be a near-miss of exactly
  the kind this phase keeps finding. Witness:
  `test_the_isolated_build_agrees_with_an_in_process_one_on_clean_code`, which compares the
  child's digest against one taken in-process with the same canonicaliser.

---

## The demotion, and the retraction

- **R47** The perturbation suite and the AST scan are **kept in full and demoted in the
  documentation**, never deleted. They are cheap per-commit ratchets against a careless spelling
  or an obvious regression. `tests/test_point_in_time.py`'s docstring now opens by saying so, the
  README's evidence section leads with truncation equivalence, and **the scan is never to be
  cited as a defence**: four evasions are measured — an order-only peek, a volume peek, a
  back-fill spelled invisibly, and E1 — and none needs a `shift`, a slice or a `center`.
  *Cost if wrong:* two layers to maintain for a modest marginal catch. Deleting them would lose
  the cheap early signal, and `tests/test_point_in_time.py` costs about 4s of the suite's 69s —
  re-measured, because the earlier figure here was written before Phase 2's own tests landed and
  was stale the moment they did.
- **R48** Spec section 9's *"the store cannot help"* is **retracted**, in place, with the
  reasoning and with what the surrounding paragraph still gets right — the same retraction is in
  `volab/features/build.py`'s module docstring, which carried the sentence too. The store can
  help, by being asked again: re-slice per cutoff. The premise was true and the conclusion was
  false, and the two are not in tension once the comparison is between two builds rather than
  inside one: the engine still takes a panel that may extend past `through`, and the *test*
  supplies the truncated panel as the second term.
- **R49** A correction to the retraction itself: Phase 1's decisions log and the Phase 2 brief
  both cite that sentence as **section 11's**. It is **section 9's**, in the feature-engine
  section, 76 lines above where section 11 begins. Recorded because a wrong pointer sends the
  next reader to the wrong place, which is the failure mode R32 names.

---

## The walk-forward

`src/volab/walkforward/`, with `python -m volab.walkforward run`.

- **R50** **One build per origin, from a panel truncated at that origin, serving two jobs.** The
  table built from the panel stopping at `t` holds a row for every session up to `t`, so it
  supplies both the training rows and the origin's own row — and neither was computed with
  `t + 1` in the frame. The obvious alternative, training from the persisted table restricted to
  `t' <= t`, would be correct *only if* truncation equivalence holds, which is the very thing
  under test. This way the walk-forward does not lean on its own test result. *Cost if wrong:*
  about 0.06s per origin, which is the dominant cost of a run.
- **R51** **Refit at every origin, not every k-th.** Refitting is four parameters from a
  normal-equation solve and costs nothing beside the rebuild. Refitting less often would mean
  forecasting the origins between refits from a model fitted on data those origins had not yet
  seen — reintroducing exactly the surface this module exists to close.
- **R52** The claim "no row is ever read from a panel extending past its origin" is checked as a
  property of **the data each call is handed**, not of the code it runs: `PanelAudit` records
  `(through, max(as_of))` for every `build_features` call and
  `test_no_build_is_ever_handed_a_row_past_its_own_origin` asserts it call by call — and then
  feeds the audit a violation to prove the audit itself can fail.
- **R53** **Training targets are sliced on `as_of`, never on `ts`.** A target's `ts` is the base
  session it is *for*; its `as_of` is the session its horizon completes, which is when it becomes
  knowable. Slicing on `ts` would hand the fit up to `horizon - 1` sessions of realized future —
  21 sessions at h=22 and 65 at h=66, which is not a subtlety but the whole forecast.
  `test_training_targets_are_knowable_at_the_origin` asserts the `as_of` slice is a strict subset
  of the `ts` slice at some origin, which is exactly the set a `ts` slice would have leaked, and
  fails if the fixture cannot show the difference at all.
- **R54 (amended in place by R73 — the original text is kept below it, because a decision that
  turned out wrong is worth more on the record than removed from it).**

  *Original:* **Log-HAR, not HAR in levels.** HAR in levels can forecast a non-positive variance
  and QLIKE is undefined there; a floor would be a free parameter chosen to make the loss
  computable. Fitting `log` variance and exponentiating is strictly positive by construction. The
  cost is that the fit targets the log scale while the loss is on the variance scale, recorded
  rather than hidden. The levels variant is **not run at all** rather than run with a floor.

  *Amended:* the second sentence still holds — a floor would be a free parameter — but the
  conclusion does not. **Not running the standard baseline is the worse sin**, and the machinery
  to handle a non-positive forecast already existed: `Evaluation.comparable` drops such an origin
  for every model on that loss and reports how many. So `har_levels` is run, with no floor. The
  log-response variant was also misnamed and miscalibrated; see R73.
- **R55** **QLIKE raises on a non-positive forecast** rather than returning NaN. A loss that
  silently goes missing is how a model with an undefined forecast comes to look like a model with
  no observations.
- **R56** **An origin that cannot be forecast is skipped and recorded, never scored as NaN.** Two
  causes, both real: too few knowable training rows, and a null regressor at the origin itself —
  a session within 22 of a missing bar has a null monthly component, because an incomplete window
  is null here and never partially filled. Found by
  `test_the_log_har_forecast_is_always_positive_so_qlike_is_always_defined`, which failed on a
  NaN at 2024-04-22 before the skip existed. Scoring it would have quietly shrunk every loss's
  denominator while the origin still appeared evaluated.
  `test_every_origin_is_scored_or_recorded_as_skipped_never_silently_dropped` pins the accounting.
- **R57** The linear algebra is **pure Python**: a 4×4 normal-equation solve by Gaussian
  elimination with partial pivoting. numpy is only a transitive dependency of pandas and is absent
  from `pyproject.toml`, so importing it is forbidden here as everywhere else (Phase 1's R4).
  Partial pivoting is not decoration — the HAR regressors are squared daily returns of order
  1e-8 — and `test_solve_needs_partial_pivoting_to_be_accurate` carries the red case.
- **R58** The singularity threshold is relative **per column**, and it took two attempts.
  An absolute `1e-14` was a latent refusal of perfectly good windows: HAR cross-products are
  order 1e-16, so a Gram entry can sit below any fixed epsilon while the system is well
  conditioned. The second attempt scaled by the largest magnitude *anywhere in the system* —
  **and that was still effectively absolute**, because the largest entry is always the intercept
  cell, the observation count, while the pivots that matter are orders of magnitude smaller. A
  review measured the consequence: with HAR-like regressors the fit was refused as singular at
  every daily volatility at or below 0.2%, against roughly 0.5–0.6% on real calm equity data —
  a 10x margin in variance, which any low-vol series or change of units crosses. Scaling per
  column is the scale-free version. Witness:
  `test_solve_needs_partial_pivoting_to_be_accurate` and
  `test_solve_refuses_a_singular_system_rather_than_inventing_an_answer`.
  *Cost if wrong:* a collinear column whose scale is tiny could slip through as non-singular,
  where the answer is then dominated by rounding. The previous version's failure was the
  opposite and worse: refusing honest windows silently, as a `skipped` entry.
- **R59** **Every trial is counted**, in `data/trials.jsonl`: append-only, one line per
  evaluated configuration — model x symbol x horizon x loss — each carrying the git sha **and a
  dirty flag**, the origin stride, how many origins were requested, and how many were skipped.
  Tracked in git by an explicit `.gitignore` exception, as `data/holdout_access.jsonl` is,
  because a count that `git clean -xdf` erases cannot be the number a write-up reports.
  Witnesses: `test_a_run_of_n_configurations_appends_exactly_n_ledger_lines`,
  `test_the_ledger_appends_and_never_rewrites`,
  `test_every_ledger_line_names_the_tree_that_produced_it`,
  `test_a_trial_says_what_it_covers_not_only_what_it_managed`,
  `test_a_nan_value_is_written_as_null_not_as_bare_nan`, and end to end through the CLI,
  `test_a_run_writes_one_ledger_line_per_configuration_it_evaluated`.
- **R60 (rewritten; the first version was wrong).** The ledger's four smoke-test lines were
  originally **kept**, on the stated grounds that each line is self-describing through its
  `first_origin`/`last_origin`. A delivery audit checked that premise and it is **false**: those
  lines named sha `21dc15b`, and `git cat-file -e 21dc15b:src/volab/walkforward/__init__.py`
  fails — the tree they point at does not contain the code that produced them, because the
  walk-forward was still untracked when they were written. A line that points at the wrong tree
  is worse than no line: it invites a reader to reconstruct a run from a commit that cannot
  produce it.

  So the ledger was **emptied before its first commit** and refilled by one run on a committed
  tree. That is not pruning an inconvenient result — the file had never been committed, no
  append-only promise was broken, and the four lines were a 13-origin shake-out of the CLI
  rather than a measurement anyone would report. *Cost if wrong:* the exploratory run is no
  longer on the record. Recorded here instead, which is the honest form of it.
- **R70** **The sha and the dirty flag are captured at the start of a run, and the flag asks
  about the whole repository.** Two defects, both found by review rather than by a test:

  1. `git_sha()` read HEAD when the *ledger was written*, which is minutes after the numbers were
     produced. The first headline run proved it concretely rather than theoretically: it began at
     one commit, the working tree was committed while it ran, and its lines therefore named a
     tree it had never executed. Written 93 seconds after that commit's timestamp.
  2. There was no dirty flag at all, and the obvious one was blind. `store._git_state` runs git
     with `cwd` set to the package, so an edit to `config/base.yaml` — which carries
     `splits.dev_end`, the universe and the storage root — or to `tests/` read as **clean**.
     Measured from both directories. A provenance line that cannot see the configuration is not
     provenance.

  `store._git_state` now takes the scope to ask about, defaulting to the package for its own
  audit stamp, and the walk-forward passes the repository root. *Cost if wrong:* a dirty run is
  still not reproducible — the flag records that it is not, rather than making it so. A diff hash
  would close that and is not implemented.
- **R71** The walk-forward reaches `git_state` through a **public** accessor,
  `volab.store.git_state`. The import ratchet forbids any module outside `store.py` from
  importing a private name from it, and **it caught this exact line** and failed the suite when
  the delegation first reached for `_git_state`. That is the ratchet working, so the resolution is
  a sanctioned public accessor rather than an exemption: the helper runs `git` and touches no
  Parquet, so the reasoning behind the ban does not reach it. `ALLOWED` stays `{"store.py"}`.
- **R61** `git_sha` passes `--no-optional-locks`, always. Phase 1 lost about an hour to
  `index.lock` files its own audit stamp was creating by omitting it on a 24,218-partition tree.
  Asserted on the argv, as Phase 1's own fix is, because reproducing the contention needs a
  concurrent committer. Witness: `test_the_git_state_call_never_takes_an_optional_lock`,
  which asserts the argv of every `git` call the delegation makes.
- **R62** The CLI has **no `--end` and no unlock flag**, and `--to` past `dev_end` is refused
  loudly rather than clamped silently — a silent clamp would let a caller believe they had
  evaluated a window they had not. Phase 6 remains the only confirmatory read. Witnesses, which
  did not exist until a delivery audit found that **no test imported the CLI at all**:
  `test_there_is_no_end_argument_and_no_unlock_flag`,
  `test_a_to_date_past_dev_end_is_refused_rather_than_clamped`, and
  `test_holdout_start_is_past_dev_end_so_the_refusal_covers_the_real_boundary`.
- **R63** A `RunAudit` violation makes the run **exit non-zero**, not warn. A call reaching
  past its own cutoff is the one defect the module exists to prevent, and reporting it as a
  warning would be the posture this project refuses, and the trials already counted are
  flushed before it returns, so a refusal cannot erase numbers that were printed.
  Witnesses: `test_an_audit_violation_makes_the_run_exit_non_zero` and
  `test_the_refusal_path_still_flushes_the_trials_already_counted`.

---

## The eighth escape, and what an independent review found

Phase 1's record was that every finding came from running something. Phase 2's additions to that
record came from two things Phase 2 did not do for itself: an independent code review of the
walk-forward and a delivery audit of the board against git. **Both found real defects that this
phase's own tests passed.**

### R72 — a `ts`-based target slice passed all 25 walk-forward tests

> **Historical.** Both blocks below, **and the clean-against-planted loss table**, were taken
> against code that has since been fixed, so none of them is reproducible on HEAD. The standing
> witnesses named at the end of this ruling are what carry it now.

The eighth time in this project a leakage test let a real forward peek through.

`test_training_targets_are_knowable_at_the_origin` **re-implemented both slices inside the test
and compared them to each other**, instead of exercising `evaluate`. So it passed whatever
`evaluate` did. Verified by planting the wrong slice in source:

```
PLANTED in source: knowable_targets now slices on ts
.........................                        [100%]
25 passed in 17.50s
```

All 25, the three real-data tests included. At h=1 that slice admits the origin's own base
session, whose target completes one session *after* the origin — the whole quantity being
forecast.

**What the leak buys, which is the part worth keeping.** Measured on real SPY data over
identical windows, clean against planted:

| | clean | planted | change |
|---|---|---|---|
| h=1 QLIKE | -4.71022 | -4.90416 | 0.19, about 4% |
| h=66 QLIKE | -3.82735 | -3.84474 | 0.017, about 0.5% |
| h=66 MSE | 6.73004e-05 | 6.63023e-05 | 1.5% |

**A real forward peek that moves the reported loss by 0.2% to 1.5%.** That is far below the
noise any reviewer would question and far below what a statistical test would flag, and it is
the project's founding premise arriving as a measurement in its own harness: this is exactly why
refusal has to be structural. A leak worth 1% is invisible to anything that looks at the number.

**Closed by making the property observable rather than inferred.** `knowable_targets` is now a
named module-level function so a test can replace it, and `RunAudit` records the maximum
training-target `as_of` per origin. Planting the slice in source now fails:

```
AssertionError: 13 origin(s) trained on a target that had not completed yet,
first (datetime.date(2024, 4, 12), datetime.date(2024, 4, 15))
```

Witnesses: `test_training_targets_are_knowable_at_the_origin` (now through `evaluate`),
`test_the_audit_catches_a_target_slice_that_reads_the_wrong_column`, and
`test_the_panel_and_target_audits_are_independent` — which records *why* the existing audit could
never have caught it: the leak was in the targets, and the panel audit does not see them.

*Cost if wrong:* one more named function and a second audit list.

### R73 — the forecast was the geometric mean wearing the arithmetic mean's name

The model was called `har_log` and was a **semi-log** specification: a logged response on level
regressors, exponentiated. `exp(fitted log y)` estimates the geometric mean of `y`, and the
quantity being forecast is the arithmetic mean. Measured on the real SPY panel:

> **Historical in its format, reproducible in its figures.** `har_log` does not exist and
> nothing prints that name or that `mean realized … ratio` layout for a semi-log row, so this
> block is a record. The *numbers* come back from
> `uv run --locked python tools/measure_calibration.py --horizon 1 --horizon 22 --from 2024-01-02
> --every 5` — the random-walk rows verbatim, the semi-log ones as the uncorrected rows of its
> second section, in that section's own format, reprinted below. That the two agree to
> four figures — `0.2529` at h=1 and `0.6768` at h=22, over `n=101` and `n=96`, against the same
> `1.7903` and `1.3428` for the random walk — is itself a result: it establishes that `har_log`
> *was* exactly today's `har_semilog` with the smearing factor forced to 1, so the rename changed
> a name and the correction changed a number, and nothing else moved underneath them.

```
h1   har_log      n= 101 mean realized 7.5138e-05 mean forecast 1.9002e-05  ratio 0.2529
h1   random_walk  n= 101 mean realized 7.5138e-05 mean forecast 1.3452e-04  ratio 1.7903
h22  har_log      n=  96 mean realized 2.3063e-03 mean forecast 1.5610e-03  ratio 0.6768
h22  random_walk  n=  96 mean realized 2.3063e-03 mean forecast 3.0969e-03  ratio 1.3428
```

`har_log` is gone, so those two rows no longer have that name. Here is what the script prints for
the same window today, with the random-walk rows unchanged and the semi-log rows appearing as the
uncorrected counterfactual — the same four figures, from code that exists:

```
h1   random_walk  n= 101 mean realized 7.5138e-05 mean forecast 1.3452e-04  ratio 1.7903
h22  random_walk  n=  96 mean realized 2.3063e-03 mean forecast 3.0969e-03  ratio 1.3428
h1   har_semilog uncorrected calib +0.2529  qlike -6.64929  mse 1.907139e-08
h22  har_semilog uncorrected calib +0.6768  qlike -5.06696  mse 1.439528e-05
```

**A factor of four low at h=1.** Not a leak, but a validity problem for any ranking built on it,
and nothing in this phase's tests looked at calibration at all.

Three changes, and the third is the one that matters most:

1. **Duan's smearing estimator** — the mean of `exp(residual)` — corrects the retransformation.
   Nonparametric, so it assumes nothing about residuals that are not Gaussian in logs, and it is
   a named estimator rather than a factor chosen to make the numbers look calibrated. That
   distinction is the one R54 drew when it refused a forecast floor.
2. **Renamed `har_semilog`**, because that is what it is. Corsi's log-HAR logs the components
   too. Logging them here was considered and rejected: `har_d_proxy` is a squared daily return
   and is legitimately **zero** on a session whose close did not move, so `log` of it needs
   either a dropped row (selection on a regressor) or an added epsilon (a free parameter).
3. **`har_levels` is now run** — Corsi's HAR, the baseline the literature actually means.
   R54 originally declined to run it because it can forecast a non-positive variance where QLIKE
   is undefined, and dodged that by running only the log variant. **That was the wrong trade**:
   not running the standard baseline is a worse sin than reporting that it sometimes forecasts a
   non-positive number, and the machinery to handle it already existed —
   `Evaluation.comparable` drops such an origin for every model on that loss and reports how
   many. So R54 is amended: the levels variant is run, with no floor.

**Measured after the fix, because "the correction works" is itself a claim.** The same probe,
re-run on `02eb0e5` over SPY origins every 5 sessions from 2021-01-04, as mean forecast over
mean realized — 1.0 is calibrated:

> Prints every figure in this table, as one line per model rather than as a table, and the block
> below it verbatim:
> `uv run --locked python tools/measure_calibration.py --symbol SPY --horizon 1 --horizon 22
> --every 5`. Re-confirmed on the current tree, every figure to four decimals, and the losses
> also match the recorded run's own lines in `data/trials.jsonl` — two independent paths to the
> same numbers, since the ledger came from the CLI and these come from `evaluate` directly.

| | h=1 | h=22 |
|---|---|---|
| `har_levels` | 0.7745 | 1.1263 |
| `har_semilog` | 0.6230 | 1.0137 |
| `random_walk` | 0.5382 | 0.7010 |

And the correction's own effect, isolated by holding the model, the window and everything else
fixed and only forcing the smearing factor to 1:

```
h1   har_semilog corrected   calib +0.6230  qlike -8.03456  mse 4.295799e-07
h1   har_semilog uncorrected calib +0.1067  qlike -3.53163  mse 4.586929e-07
h1   random_walk benchmark                 qlike 514.08325  mse 4.453125e-07
h22  har_semilog corrected   calib +1.0137  qlike -4.99100  mse 9.271182e-06
h22  har_semilog uncorrected calib +0.5454  qlike -4.78135  mse 1.080418e-05
h22  random_walk benchmark                 qlike 1392.58273  mse 1.620724e-05
```

So the correction moves calibration from 0.107 to 0.623 on this window, and **the same model
goes from losing to the random walk on MSE (4.587e-07 against 4.453e-07) to beating it
(4.296e-07)**. Note the honest caveats: every model still *under*-forecasts at h=1, so the
correction reduced the bias rather than removing it; and the 0.107 here differs from the 0.2529
quoted above because that probe used a 2024-onward window, which is a different sample and not a
different result.

**That last sentence was an assertion when it was written, and is now a measurement.** One script
runs both windows, so the claim is checkable rather than plausible: `--from 2021-01-04` gives
0.1067 uncorrected and `--from 2024-01-02` gives 0.2529, on the same model, the same code and
the same stride. The window is the only thing that differs, which is what "a different sample
and not a different result" has to mean to be worth saying.

Witness: `test_the_smearing_factor_corrects_the_retransformation_bias`, which asserts only that
the factor is at least 1 — that is Jensen's inequality, not calibration, and the calibration
figures above are a recorded measurement rather than a standing test. Said plainly because an
earlier draft of this ruling implied the test checked the calibration.

*Cost if wrong:* three models and two losses is 6 trials per symbol-horizon, 72 for the full
run, and the semi-log specification still fits a log scale while the loss is on the variance
scale. Both are on the ledger rather than in a footnote.

### R74 — trials are appended per configuration, not once at the end

`append_trials` ran after the whole loop. A run that printed SPY's results and then tripped the
refusal branch on QQQ, raised, or was interrupted reached **no ledger at all** — a number seen
but not counted, which is precisely the forking-paths hole the ledger exists to close. Trials are
now appended as each configuration finishes, the refusal path flushes before returning, and a
configuration that produced no forecast still yields trials with `origins=0` rather than an empty
list. Witnesses: `test_the_refusal_path_still_flushes_the_trials_already_counted`,
`test_a_configuration_that_forecast_nothing_is_still_counted`.

### R75 — a trial now says what it covers, not only what it managed

`origins` alone cannot distinguish a trial covering 250 of 251 requested origins from one
covering 250 of 1,250: the number is identical. Every line now carries `requested`, `skipped` and
the origin **stride**, because a trial over every fifth session is not the same experiment as one
over every session and the ledger has to be able to say which it was. A NaN value is written as
`null`, since `json.dumps` emits a bare `NaN` that no strict parser reads back. Witnesses:
`test_a_trial_says_what_it_covers_not_only_what_it_managed`,
`test_a_nan_value_is_written_as_null_not_as_bare_nan`.

### R76 — the dead second loop is deleted, not kept

`features_at_origins` was a second per-origin loop that nothing called, and it **diverged** from
`evaluate`'s: it raised on an empty truncation where `evaluate` skips. Two loops with different
behaviour, one of them never exercised, is a place for the tested one and the believed one to
come apart. Deleted rather than wired up.

### R77 — training values come from the truncated frame, not from a dict over the whole one

`evaluate` built one dict of target values over the *entire* target frame and used it for both
training and scoring. Under the store's dedupe on `(symbol, ts)` that is equivalent to reading
from the truncated frame, so it was not a live defect — but it made correctness depend on the
dedupe rather than on the slice. Training values now come from `knowable_targets`' own output;
only the realized value used for *scoring* is read from the whole frame, which is future relative
to its origin by definition and is bounded by `dev_end` through `scoreable_origins`.

### R78 — the ninth escape: the target audit checked the slice, not what the fit consumed

> **Historical.** The `40 passed` and the `AssertionError` below both come from a tree carrying
> a plant that was never committed -- a training set built from the whole target frame. Unlike
> E1 it is not in `tools/`: E1's plant earns a committed patch because the claim about it is a
> claim about *Phase 1's whole suite*, which only a patched source can test. This one's claim is
> about a single audit, and `test_the_forecast_does_not_change_when_future_targets_are_removed`
> asserts it on every run.

Found by asking an advisor to attack the fix for R72, which is the right place to look: a fix
written in a hurry to close an escape is the most likely place for the next one.

`RunAudit.record_training` logs the maximum `as_of` of what `knowable_targets` **returned**. That
proves the slice is correct. It does not prove the fit *used* the slice's output — which is the
same tautology as R72, one level down. Verified by planting a training set built from the whole
target frame, bypassing `knowable_targets` entirely:

```
PLANTED: training set built from the whole target frame, bypassing knowable_targets
........................................                 [100%]
40 passed in 40.67s
```

All 40, the R72 audit test and its red case included.

**Closed by target-side truncation equivalence**, which is the same definition this whole phase
rests on, applied to the other input: build the forecast at `t` from the full target frame, build
it again from a frame that physically stops at `t`, and compare. It does not care *how* the
future was reached, only whether removing it changes the answer — so unlike an audit it cannot
be satisfied by something adjacent. On the plant:

```
AssertionError: 2024-04-12: the fit used 49 rows with the future present and 48 without it,
so it was reading future targets
```

The audit is **kept as well**, because it localises a failure to an origin and a date where the
equivalence test only says the answer moved. `realized` is excluded from the comparison: it is
legitimately null in the truncated run, because the value being forecast has not happened yet,
which is the point.

Witness: `test_the_forecast_does_not_change_when_future_targets_are_removed`.
*Cost if wrong:* one extra `evaluate` call per compared origin, and the comparison is over a
sample of the toy fixture's origins rather than all of them.

**The pattern, now nine for nine.** Every leakage property in this project that was checked by
reasoning about the code has eventually been found wrong, and every one was caught by running
something. Three of the nine were caught in Phase 2, and two of those three were in code written
*to close a previous one*. The honest conclusion is not that the harness is finished but that
this is the rate: a new mechanical claim here should be assumed to have a hole until a plant has
been run against it.

### What the review found that is recorded and not fixed

- **`QLIKE`'s overflow clamp.** `(realized - forecast) ** 2` can raise `OverflowError` for a
  forecast near the 1e304 clamp, which contradicts the comment claiming the loss "should see" a
  diverged fit as a large number. Left as a raise: an overflow on a diverged fit is loud, and the
  alternative is a cap that is another free parameter.
- **The skip message overstates usable rows.** It reports the count before `dropna`, so "only N
  knowable rows" is an upper bound. Cosmetic, in a message, and recorded rather than silently
  adjusted.
- **`evaluate` does not reject duplicate or unsorted origins.** Nothing reachable passes them —
  `origins_in` and `scoreable_origins` both produce sorted unique dates — but it is unguarded.
- **A dirty run records no diff hash**, so a dirty line says it is not reproducible rather than
  making it so.

---

## The recorded walk-forward run

`python -m volab.walkforward run --from 2021-01-04 --every 5`, on commit `02eb0e5` with a clean
tree, 400.4s. Three symbols x four horizons x three models x two losses = **72 trials, 72 ledger
lines**, every one carrying a sha that resolves to a tree containing the code, `git_dirty: false`,
and the stride it used.

> **This block names its own command, and re-running it is not free of consequence**: the ledger
> is append-only by design, so a second run adds 72 more lines under a different sha rather than
> reproducing these. The *numbers* in it are reproducible without touching the ledger —
> `uv run --locked python tools/measure_calibration.py` reaches `evaluate` directly and writes
> nothing (R81) — and `data/trials.jsonl` already holds these 72 lines, which is where a reader
> should check them.

```
git 02eb0e5bc419, clean
SPY h1:
  251 origin(s) from 2021-01-04 to 2025-12-24
  251 build(s) and 251 training set(s), every one stopping at its own origin
  truncation equivalence: 251 of 251 origins identical
  har_levels   qlike -8.19649 over 250 origin(s), 1 dropped
  har_semilog  qlike -8.03456 over 250 origin(s), 1 dropped
  random_walk  qlike +514.083 over 250 origin(s), 1 dropped
  har_levels   mse   +3.88884e-07 over 251 origin(s), 0 dropped
  har_semilog  mse   +4.2958e-07 over 251 origin(s), 0 dropped
  random_walk  mse   +4.45313e-07 over 251 origin(s), 0 dropped
...
trials: 72 appended, ledger now 72 line(s), was 0
models counted: 3, losses: ['mse', 'qlike']
ran in 400.4s
```

**Truncation equivalence held at every origin of every configuration** — 251 of 251, 250 of 250,
247 of 247, 238 of 238, for each of SPY, QQQ and IWM. Those four pairs are **historical console
output**: the ledger records losses and origin counts, not the equivalence result, so unlike the
qlike and mse lines above they cannot be checked against `data/trials.jsonl`. Re-running the CLI
would recompute them and append 72 more trials, which is why they are left as a record. That is exhaustive over the origins this
run visited, which is a stride of 5 across 2021-2025 and **not** the dev window; the dev-window
result is the separate exhaustive sweep.

**Fixing the calibration changed a ranking, measured on the same model and the same window.**
The first version of this paragraph compared the *pre-fix semi-log* model against the *post-fix
levels* model, which is two different models and proves nothing; it also quoted a figure read
from a ledger that had since been deleted. Re-measured properly, by holding everything fixed and
forcing the smearing factor to 1:

> Prints these three lines, among others: `uv run --locked python
> tools/measure_calibration.py --symbol SPY --horizon 1 --every 5`.

```
h1   har_semilog corrected   calib +0.6230  qlike -8.03456  mse 4.295799e-07
h1   har_semilog uncorrected calib +0.1067  qlike -3.53163  mse 4.586929e-07
h1   random_walk benchmark                 qlike 514.08325  mse 4.453125e-07
```

The same model goes from **losing** to a single squared return to **beating** it. The
uncorrected 4.587e-07 independently reproduces the 4.59e-07 the review reported, which is worth
noting: that figure came from a ledger this phase had already deleted, and it checked out.

`har_levels` is better still at 3.889e-07 on this configuration, but that is a different model
and is reported as such. **The lesson is that calibration belongs in the harness rather than in a
modelling footnote**: a model that was 4x low was reported as the QLIKE winner while losing to
the benchmark on MSE, and nothing in the suite looked at calibration at all.

One honest caution about QLIKE here: it is reported as `log(f) + r/f` with no additive constant,
so its *level* is meaningless — the daily variance proxies are order 1e-5 to 1e-8 and their log
dominates. Only differences between models on the same configuration mean anything, and the
random walk's large positive values come from origins where its forecast is far too small, which
QLIKE punishes hard. Nothing is inferred here from a single number's sign or size.

---

## The evidence, made reproducible

The board carried this as a named risk and it is now closed: the plant and the probes are
committed, so the figures above are re-runnable rather than quoted. Three rulings came out of
doing it, and one of them is a defect this directory's own first run produced.

- **R79** The measurements live in **`tools/`, outside `src/`, with no `__init__.py`**, and that
  placement is the ruling rather than a convenience. `tests/test_import_discipline.py` globs
  `Path(volab.__file__).parent` — `src/volab` and nothing else — so a probe directory at the
  repository root cannot widen the import ratchet, and `ALLOWED` stays `{"store.py"}`. The
  alternative, a `volab.probes` package, would have put measurement code inside the distribution
  and inside the ratchet's scope, where the only way to let a probe read the store would have
  been to loosen the thing the ratchet exists to hold. Every probe reads through
  `load_panel` with an explicit `end` — directly in `measure_vintages.py`, through
  `volab.panel.load_curated_panel` in the two sweep probes, and through `walkforward.evaluate`
  in the calibration probe, while `measure_calendar_memo.py` reads no data at all — and none
  names Parquet, sets `allow_holdout` or reads `VOLAB_HOLDOUT_UNLOCK`. `ruff` covers the
  directory, `mypy`'s `files` stays `["src"]`, and no dependency was added.

  **Those three sentences had no witness, and a delivery audit said so — R32 turned on the
  ruling that cites R32.** They have three now, over `tools/` as a separate root so the scan
  for `src/volab` still covers that package alone:
  `test_the_probe_directory_is_outside_the_scanned_package` (outside `SRC`, no `__init__.py`,
  `ALLOWED` still exactly `{"store.py"}`), `test_probe_does_not_read_parquet_directly` (the
  existing `_violations` scanner, per probe) and `test_probe_does_not_unlock_the_holdout`. The
  last reads the **AST** and exempts docstrings, because `tools/README.md` and
  `tools/_harness.py` both *promise* this ban in words and a text search would trip over the
  promise itself; `test_the_unlock_scan_detects_each_spelling` is its red case and
  `test_the_unlock_scan_ignores_prose_that_only_names_it` pins the exemption.

  **Verified the way the claim is phrased**, since "reproducible from a checkout" is itself a
  claim: in a tree built from `git archive HEAD` with **no derived data at all**, `build-raw`,
  `curate` and `features build` rebuilt the store from the committed payloads in 68.7s with no
  network, and M1, M2 and the sampled real sweep then printed the same figures as this
  repository's own store — `9 of 9`, `52 of 2,312`, `0 of 63`. *Cost if wrong:* a reader trusts
  a number this document cannot produce, which is the state Phase 2 shipped in.

- **R80** The E1 plant is **a patch, applied to a throwaway tree, never a monkeypatch and never
  this checkout.** Each half of that is load-bearing for a different reason.

  *Not a monkeypatch*, because Phase 1's AST scan reads `src/volab/features/*.py` **off disk** —
  `test_point_in_time.py` globs the package directory and calls `read_text()`. Against a
  monkeypatch it would scan clean source and pass vacuously, reporting the escape as larger than
  it is while proving nothing. `tools/sweep_truncation.py --plant e1` keeps the monkeypatch route
  anyway, because for the *sweep's* counts it is equivalent and far cheaper; it is the
  Phase-1-suite half of the claim that needs the file on disk changed.

  *Not this checkout*, because a run killed between the apply and the revert would leave a live
  forward peek in `src/` for the next commit to pick up. `git archive HEAD` into a temporary
  directory touches no worktree bookkeeping and contends for no `.git/index.lock`, and there is
  nothing to clean up in the repository if the process dies.

  **And the driver's own first run is why it now refuses rather than reports.** `.gitignore`
  keeps `data/**/` tracked, so `git archive` had already created an empty `data/curated`; `cp -R`
  into an existing destination nests, giving `data/curated/curated`. The real sweep tiers raised
  `DatasetNotFound`, the driver swallowed it, and the run **exited 0 with those rows simply
  missing from the output** — a check that passes by not running, which is this project's named
  anti-goal, produced by the script written to close a reproducibility gap. The destination is
  now removed before the clone, a tier that fails fails the driver, and the availability of the
  real-data tiers is printed before any measurement. *Cost if wrong:* an operator reads a
  planted figure that was never computed.

- **R81** `tools/measure_calibration.py` **appends nothing to the trial ledger**, and its
  *uncorrected* figures are not a result anyone may report. They are a counterfactual —
  `fit_ols` wrapped so the smearing factor is 1, with the model, the window, the training rows
  and the origin set untouched — and their only job is to isolate what the correction is worth.
  Putting them on the ledger would add six trials per configuration that no candidate model
  produced, which is the opposite of what the ledger counts. The **corrected** losses it prints
  are the same numbers the recorded run put on the ledger, and agreeing with it from a second
  path is the check: those came from the CLI, these come from `evaluate` directly. *Cost if
  wrong:* a reader could mistake a counterfactual for a seventh and eighth trial.

---

## Structural decisions

- **R64** `volab.identity` holds the **one** byte-exact comparator, in `src/` rather than in the
  suite. It began in `test_point_in_time.py`, moved to a shared test module when the truncation
  tests needed it, and moved into `src/` when the walk-forward began *reporting* an exhaustive
  truncation result — a production report reaching into `tests/` is both a mypy error and a
  design error. Its red cases stay in `tests/test_point_in_time.py`. *Cost if wrong:* a library
  module whose only production caller is a report.
- **R65** `volab.panel` holds the **one** panel-assembly path, shared by the features CLI, the
  tests and the walk-forward. The features CLI keeps its helper names as thin delegations so its
  existing tests still pin it. The second copy was not hypothetical: writing one for a scratch
  measurement cost two failed runs — a `DatasetNotFound` from omitting `symbols`, and a
  `SchemaError` from omitting the superseded-observation collapse.
- **R66** `tests/forward_scan.py` holds the scanner, so a test wanting to show a planted peek is
  *invisible* to the scan can run the real scanner over its own plant.
  `test_the_plants_are_invisible_to_the_forward_scan` does exactly that. A claim of scan-silence
  checked by hand is the kind of unverified "cannot" R32 forbids — and Phase 1's near-miss, where
  the first E1 variant was scan-visible through a negative slice and would have understated the
  escape, is the reason it matters.
- **R67** `test_features_cli.py::test_every_read_stops_at_dev_end` now patches
  `volab.panel.load_panel` rather than the CLI's own binding, because that is where the reads
  moved. The guarantee is unchanged and the witness follows the code; a patch left on the old
  binding would have passed while recording nothing.
- **R68** `VOLAB_PIT_EXHAUSTIVE` is `VOLAB_`-prefixed in the manner of `VOLAB_HOLDOUT_UNLOCK` — a
  key, not a setting — so `conftest`'s `_clean_volab_environment`, which clears only the names in
  `_ENV_OVERRIDES`, leaves it alone. *Cost if wrong:* a reader may mistake it for configuration;
  the comment at its definition says it is not. Witness: `conftest`'s
  `_clean_volab_environment` clears only `_ENV_OVERRIDES`, which
  `tests/test_conftest_policy.py` pins.

---

## Two store round-trip asymmetries, both normalised explicitly

Neither is Phase 2's doing, and both were met while byte-comparing a fresh build against disk.

1. `as_of` is written as `datetime.date` and read back as `datetime64[us]`.
2. `has_distribution` is written as pandas' nullable `boolean` and read back as numpy `bool`,
   because no null ever reached disk for it.

- **R69** Both are normalised at the two comparison sites rather than by loosening the
  comparator, and the boolean cast runs the **narrow** way — `bool` up to `boolean` — so the null
  mask is still compared and a null appearing in a fresh build is still a difference rather than
  being normalised away. Witnesses: `test_the_span_fix_did_not_move_the_persisted_feature_table`
  and `test_the_per_origin_rows_are_byte_identical_to_the_persisted_table`, the two sites that
  do the casting. *Cost if wrong:* two casts to keep in step with the store's write path.

---

## What is carried, not closed

Stated plainly, because a leakage suite whose limits are not stated invites exactly the trust
this project exists to withhold.

- **A sampled sweep is a ratchet with a stated power, not a proof.** 2.2% per cutoff against a
  band-threshold peek. The exhaustive run and the walk-forward are the proof, and they are not
  per-commit.
- **A cross-sectional peek is unreachable rather than tested.** `build_features` refuses a panel
  holding more than one non-exogenous symbol, so the shape cannot be expressed; if that guard
  were relaxed, nothing here would notice. Inherited unchanged from Phase 1.
- **Refusal is not equivalence** (R40).
- **The exogenous row at a gap session.** R39 keeps it, which is the better of the two
  behaviours, but it means the table can carry a row whose features are all null and whose
  exogenous columns are not. That is correct and it is worth knowing.
- **The 200-session holdout is thin at the 22- and 66-session horizons.** Inherited from Phase 1
  and unchanged by anything here.
- **The semi-log specification's scale mismatch** (R54, R73): it fits a log scale while the
  loss is on the variance scale, and it still under-forecasts at h=1 (0.623 of realized) after
  the smearing correction. `har_levels` is closer at 0.775 and is not positive by construction.
