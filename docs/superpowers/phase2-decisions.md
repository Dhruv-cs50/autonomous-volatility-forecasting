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

## The three measurements taken before any code was written

Each changed the design, and all three were run against `d18f004` (main, with Phase 1 merged)
before a line was written. The pattern is deliberate: Phase 1's expensive lesson was that prose
about a leakage property is worth less than one run.

### M1 — a post-load slice on `as_of` *is* what the store would hand over

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
Then checked directly rather than inferred — slice against `load_panel(end=t)`, three cutoffs in
each of three datasets, **9 of 9 byte-identical**:

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

```
sessions: 2312
sessions whose NEXT return is in (0.03, 0.10): 19
  ... in (-0.10, -0.03): 33
hit rate: 0.0082
```

52 of 2,312 as the union, 2.2%. **A 40-cutoff sample expects 0.9 hits**, so a sampled sweep would
have reported "no disagreement" against a live forward peek about a third of the time. The
prototype's 3-of-38 came from a synthetic panel carrying a deliberate +5% move every 17 sessions.

- **R35** Sampling is kept as the cheap per-commit tier **with its power stated**, and exhaustive
  coverage comes from the walk-forward, which rebuilds the table at every forecast origin from a
  panel truncated at that origin — so comparing its rows against the persisted full-panel table
  sweeps every session at no extra cost. Hand-feeding the sampler the 52 band dates was
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
  *Cost if wrong:* less coverage variety than a random sample would give across runs.

### M3 — E2 can hide outside the feature package, so the AST ban cannot close its class

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
  Compute is not the constraint.

---

## The seventh escape: extent dependence at a cutoff with no bar

Found by the new sweep on its **first run**, before any plant.

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

| Tier | Cutoffs | Disagreements | Wall clock |
|---|---|---|---|
| synthetic, exhaustive | 79 | **0** | ~2s |
| real curated, sampled (stride + awkward) | 63 | **0** | ~4s |
| real curated, **exhaustive** (`VOLAB_PIT_EXHAUSTIVE=1`) | 2,245 | **0** | 6m00s |

### Proof that it bites: E1 planted in `src/`, not monkeypatched

The scan-silent spelling — `drop` / `set_axis` / `reindex` with `Index.delete`, no `shift`, no
slice, no `center` — written into `semivariance_proxy` in `src/volab/features/realized.py`.

**Phase 1's method, against that plant:**

```
=== PHASE 1 POINT-IN-TIME SUITE, E1 PLANTED ===
100 passed in 4.51s

=== WHOLE SUITE MINUS THE NEW PHASE 2 FILES, E1 PLANTED ===
FAILED tests/test_realized.py::test_signed_jump_is_negative_when_downside_dominates
1 failed, 644 passed in 25.70s
```

**100 of 100 point-in-time tests pass with a live forward peek in the source.** The single
catcher is a hand-written unit value test whose returns happen to land in the band — incidental,
not a defence. This reproduces Phase 1's own 607-of-608 measurement on the current tree rather
than citing it.

**Phase 2's method, against the same plant:**

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
and not merely observed. The real sampled tier caught it at 2025-04-02, the eve of the April 2025
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
  either side of its deliberate gap.

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
- **R45** The child **never calls `load_panel`**. `conftest`'s autouse `_forbid_holdout_unlock`
  does not exist in that process, so a read there would be unpoliced. The parent reads under the
  policing and hands the panel over as a pickle in `tmp_path`. Pickle rather than Parquet so
  dtypes cross unchanged — the comparison downstream is byte-exact and a Parquet round trip has
  its own documented asymmetries, two of which this phase ran into.
- **R46** The child hashes its columns with **the comparator's own canonicaliser**
  (`volab.identity.canonical_bytes`), not a reduction written for the pipe. A digest that could
  disagree with `assert_byte_identical` about one pair of tables would be a near-miss of exactly
  the kind this phase keeps finding.

---

## The demotion, and the retraction

- **R47** The perturbation suite and the AST scan are **kept in full and demoted in the
  documentation**, never deleted. They are cheap per-commit ratchets against a careless spelling
  or an obvious regression. `tests/test_point_in_time.py`'s docstring now opens by saying so, the
  README's evidence section leads with truncation equivalence, and **the scan is never to be
  cited as a defence**: four evasions are measured — an order-only peek, a volume peek, a
  back-fill spelled invisibly, and E1 — and none needs a `shift`, a slice or a `center`.
  *Cost if wrong:* two layers to maintain for a modest marginal catch. Deleting them would lose
  the cheap early signal, and they cost about 17s of a 2-minute suite.
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
- **R54** **Log-HAR, not HAR in levels.** HAR in levels can forecast a non-positive variance and
  QLIKE is undefined there; a floor would be a free parameter chosen to make the loss computable.
  Fitting `log` variance and exponentiating is strictly positive by construction. *Cost if
  wrong:* the fit targets the log scale while the loss is on the variance scale, which is a real
  mismatch, recorded rather than hidden. The levels variant is **not run at all** rather than run
  with a floor, so it is not a hidden trial.
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
- **R58** The singularity threshold is **relative**, scaled by the largest magnitude in the
  system, not absolute. An absolute `1e-14` was a latent refusal of perfectly good windows: HAR
  cross-products are order 1e-16, so a Gram entry can sit below any fixed epsilon while the
  system is well conditioned. Found by a test before it could reject a real fit.
- **R59** **Every trial is counted**, in `data/trials.jsonl`: append-only, one line per evaluated
  configuration — model × symbol × horizon × loss — each keyed by the git sha of the tree that
  produced it. Tracked in git by an explicit `.gitignore` exception, as `data/holdout_access.jsonl`
  is, because a count that `git clean -xdf` erases cannot be the number a write-up reports.
  `test_a_run_of_n_configurations_appends_exactly_n_ledger_lines` and
  `test_the_ledger_appends_and_never_rewrites` make "every trial is counted" a fact about the code.
- **R60** **The ledger's first four lines are a smoke test, and they stay.** A 13-origin toy
  window over 2024 Q1, written while the CLI was being shaken out. Deleting them was considered
  and rejected: the ledger exists to count everything, each line is self-describing through its
  `first_origin`/`last_origin`, and pruning the exploratory runs is precisely the selective
  reporting the project refuses. *Cost if wrong:* a reader must notice the origin range before
  comparing two lines, which the fields make possible.
- **R61** `git_sha` passes `--no-optional-locks`, always. Phase 1 lost about an hour to
  `index.lock` files its own audit stamp was creating by omitting it on a 24,218-partition tree.
  Asserted on the argv, as Phase 1's own fix is, because reproducing the contention needs a
  concurrent committer.
- **R62** The CLI has **no `--end` and no unlock flag**, and `--to` past `dev_end` is refused
  loudly rather than clamped silently — a silent clamp would let a caller believe they had
  evaluated a window they had not. Phase 6 remains the only confirmatory read.
- **R63** A `PanelAudit` violation makes the run **exit non-zero**, not warn. A panel reaching
  past its own cutoff is the one defect the module exists to prevent, and reporting it as a
  warning would be the posture this project refuses.

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
  the comment at its definition says it is not.

---

## Two store round-trip asymmetries, both normalised explicitly

Neither is Phase 2's doing, and both were met while byte-comparing a fresh build against disk.

1. `as_of` is written as `datetime.date` and read back as `datetime64[us]`.
2. `has_distribution` is written as pandas' nullable `boolean` and read back as numpy `bool`,
   because no null ever reached disk for it.

- **R69** Both are normalised at the two comparison sites rather than by loosening the
  comparator, and the boolean cast runs the **narrow** way — `bool` up to `boolean` — so the null
  mask is still compared and a null appearing in a fresh build is still a difference rather than
  being normalised away. *Cost if wrong:* two casts to keep in step with the store's write path.

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
- **Log-HAR's scale mismatch** (R54).
