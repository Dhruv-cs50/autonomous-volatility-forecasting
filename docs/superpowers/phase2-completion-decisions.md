# Phase 2 completion — decisions taken during execution

The framework's Phase 2 row asks for **GARCH(1,1), GJR/EGARCH with skew-t, HAR, recalibrated HAR;
walk-forward; QLIKE/MSE; DM plus MCS**, exiting on *"a reproducible leaderboard per horizon,
logged in MLflow"*. What the repository has is the **harness** — the walk-forward, the two losses,
the trial ledger, truncation equivalence on both the panel and the targets — plus HAR in levels,
HAR semi-log and a random walk. The Phase 2 board said so in its own Risks section rather than
claiming the row was done:

> **The model set is deliberately three.** The GARCH family and the recalibrated HAR of the
> framework's Phase 2 row are **not** in this branch; what is here is the harness they will be
> run through, plus the baseline.

So this is **Phase 2 completion**, not Phase 3, and the board is named that way. Calling it Phase 3
would have quietly skipped a row.

Numbering continues Phase 2's, which ended at **R81**. The standard every ruling here is written
against is unchanged, and it is Phase 1's closing one:

> **R32: a stated "cannot" is a test, not a sentence.** Every mechanical guarantee in a README,
> docstring or spec must name an executable witness, and one without a test id is an unverified
> claim.

---

## The dependency decision, which gated everything else

- **R82 — numpy, scipy and `arch` are admitted, and R4's ban is narrowed rather than dropped.**
  Phase 1's **R4** said the realized-measure functions import no numpy, and Phase 2's **R57**
  extended that to the linear algebra, which is a pure-Python 4×4 Gaussian elimination. Both
  are quoted in full because the reason matters:

  > **R4** … numpy is only a transitive dependency of pandas and absent from `pyproject.toml`;
  > declaring it would touch `uv.lock` against the **no-new-dependencies constraint**, and pandas
  > does everything needed.

  **That reason was a project constraint, not a principle**, and it has now been lifted by an
  explicit decision rather than by drift. `arch` is what the framework names for both halves of
  this phase — the GARCH family, and `MCS`/`SPA`/`StepM` for the comparison — and it brings numpy,
  scipy and statsmodels whatever we do. Hand-rolling a stationary-bootstrap model confidence set
  was considered and rejected: it is the single easiest place in this project to be subtly wrong,
  and an MCS we wrote ourselves would be evidence about our bootstrap rather than about the
  models.

  **What stays banned.** R4 and R57 continue to hold **where they were aimed**: nothing in
  `src/volab/features/`, `panel.py`, `store.py` or the walk-forward's solver imports numpy. The
  feature engine stays a pure-pandas, pure-Python path whose byte-exact comparisons and
  truncation equivalence are the project's actual claim, and `volab.identity` keeps comparing
  bytes rather than floats. numpy and scipy are confined to the **model zoo** and the
  **statistical comparison** layer, which are new packages and own no part of the point-in-time
  guarantee.

  **The witness, which this ruling claimed before it had one.** The sentence below used to end
  "which is why the ban keeps a witness rather than becoming a convention", and there was no
  such test: `grep -rn "numpy\|scipy" tests/` found three comments. An architect review caught
  it — **R32 turned on the ruling that cites R32, for the second time in this phase** after R79
  did the same thing. The ban is now scanned:
  `test_the_point_in_time_path_imports_no_numpy_or_scipy` walks the AST of every module in
  `features/`, plus `panel.py`, `store.py`, `walkforward/__init__.py` and `models/base.py`, with
  `test_the_numeric_scan_detects_each_spelling` as its red case and
  `test_the_sealed_set_is_not_empty_and_holds_what_it_claims` guarding against a rename quietly
  emptying the set. Imports only, not `sys.modules`: pandas imports numpy itself, so a
  module-object check would fire everywhere, and `Series.to_numpy()` is pandas' own API reaching
  no numpy name.

  *Cost if wrong:* a reader of R4 who does not reach this ruling believes a ban that has since
  been scoped. Mitigated by amending R4 in place with a pointer here. The sharper risk was scope
  creep — numpy quietly appearing in the feature path because it is now installed — and that is
  now a failing test rather than a convention.

  **scipy is declared too**, which the first version of this ruling admitted in prose and left
  out of `pyproject.toml`: it was present only as `arch`'s transitive dependency, and
  `mypy --strict` failed on it (`Library stubs not installed for "scipy"`). Declared, with
  `scipy-stubs` in the dev group rather than an `ignore_missing_imports` override — the override
  was the precedent set for `exchange_calendars` and `pyarrow`, but it makes every scipy call
  `Any`, and a statistical comparison layer is the last place to give up type checking.

- **R83 — GARCH is fitted on returns rescaled by 100, and the variance divided back by 1e4.**
  Measured, not assumed, because `pyproject.toml` sets `filterwarnings = ["error"]` and a warning
  is therefore a failing test. On a realistic daily log-return series, order 1e-2:

  ```
  RAW SCALE RAISES: DataScaleWarning y is poorly scaled, which may affect convergence of the
  optimizer when estimating the model parameters. The scale of y is 6.31e-05. Parameter
  estimation work better when this value is between 1 and 10
  x100 SCALE: no warning, converged
  ```

  The raw path also drew a `ConvergenceWarning` — *"the optimizer returned code 4, inequality
  constraints incompatible"* — so under this suite's settings the unscaled fit does not merely
  warn, it fails twice. Percent returns put the scale in `arch`'s own recommended 1-to-10 band.
  The rescaling is exact and reversible: variance scales by the square, so a forecast taken on
  `100 r` is divided by `1e4` to return to the variance scale the targets and both losses live on.

  **A convergence failure is a recorded skip, never a suppressed warning.** The harness already
  has the shape for this — `Evaluation.comparable` drops an origin for every model on a loss and
  reports how many — and R56's rule applies unchanged: an origin is scored or recorded as
  skipped, never silently dropped. Adding a `filterwarnings` entry to make a fit quiet would be
  the opposite of this project's posture.

  **Amended by R89 after measurement: `rescale` is left at its default and both `True` and
  `False` are forbidden.** This is the trap a reader of this ruling would otherwise walk into.
  `arch_model(..., rescale=False)` **emits nothing at all** on a badly scaled series, so it does
  not fix the scale — it silences the warning that tells you the scale is wrong, and with it this
  ruling's own red case. `rescale=True` silently sets `scale=100.0`, doing the rescaling behind
  your back so the ÷1e4 below would then be applied twice. Also measured: the warning comes from
  `fit()`, not the constructor, and `show_warning=False` suppresses the convergence branch
  entirely — which is why R91 detects non-convergence by `convergence_flag != 0` rather than by
  catching a warning. See R89 and R91 in the design doc.

  *Cost if wrong:* a factor of 1e4 in the wrong direction is a forecast four orders of magnitude
  out, which QLIKE and MSE would both show immediately rather than subtly.

- **R84 — MLflow 3.x is refused by the lock, and the exit criterion is therefore open.**
  The framework's Phase 2 row ends *"logged in MLflow"*, and MLflow 3.x with a local file store
  was the chosen option. It does not resolve. The conflict is **pyarrow, not pandas**:

  ```
  mlflow>=3.0.0 depends on pyarrow>=4.0.0,<26
  … your project depends on mlflow>=3 and pyarrow>=26.0.0,
  we can conclude that your project's requirements are unsatisfiable.
  ```

  Unpinned, the resolver backsolves to **`mlflow==1.27.0`**, a 2022 release. That was installed,
  seen for what it was, and removed rather than left in the lock: a four-year-old tracker reached
  by accident is not a decision anyone made.

  The live options were to relax `pyarrow>=26.0.0` so MLflow 3.x fits, or to grow the existing
  `data/trials.jsonl` into the leaderboard.

  **Resolved: the ledger, and `pyarrow` is not touched.** Relaxing the pin would change the
  engine under `store.py` — the one module allowed to read Parquet, whose two documented
  round-trip asymmetries (R69) were *measured on the current version* — and the byte-exact
  comparator that the whole point-in-time claim rests on sits directly on top of those. Trading
  that for a reporting convenience is the wrong way round. The ledger also already carries more
  provenance than a default MLflow run does: the git sha, a dirty flag, the origin stride,
  `requested`, `skipped` and `dropped` counts, and one line per evaluated configuration whether
  or not it forecast anything (R74, R75).

  **This is a deliberate deviation from the framework's exit criterion, and the row is not
  rounded up to meet it.** The framework's Phase 2 line ends *"logged in MLflow"*; this phase
  will exit with *"logged in an append-only git-tracked ledger, with MLflow refused for a stated
  reason"*. Said plainly because the alternative — recording the row as satisfied — is the kind
  of adjacent truth this project has caught itself in twice (C1, and R35's "sweeps every
  session"). MLflow remains available later behind a `pyarrow` bump, and nothing in the
  leaderboard's design should assume the ledger is the only possible sink.

  *Cost if wrong:* no MLflow UI, and a tracker the project maintains itself. The ledger is
  ~100 lines of append-only JSON with its own tests, so the maintenance is small; the thing
  genuinely given up is MLflow's run comparison view.

---

## R85-R97 — ratified, and recorded where they were written

US-3.1's design is `docs/superpowers/specs/2026-10-25-phase2-completion-design.md`, and it
carries thirteen rulings with their derivations, their probes and their costs. **They are
ratified as written and are not copied here.** Duplicating them would create two texts that can
disagree, which is the defect this project has already hit twice — R49's section-number
correction, and the three places R38's timing went stale. One home, indexed here:

| Ruling | What it settles | Section |
|---|---|---|
| **R85** | one interface, `fit(view) -> forecast(h)`, over a view of everything knowable at the origin | 3.2 |
| **R86** | `MODELS` frozen at the three baseline names; the contestant set is a named cohort | 3.3 |
| **R87** | a structural refusal skips the origin, a model's own refusal is recorded against it | 3.4 |
| **R88** | the h-step forecast is the **sum** of per-step conditional variances, `mean="Zero"`, ÷1e4 once | 4.2 |
| **R89** | `rescale` stays at its default; `True` and `False` are both forbidden | 4.3 |
| **R90** | GARCH(1,1) analytic; **GJR and EGARCH simulate**, because the analytic path is 17.7% low | 4.4 |
| **R91** | non-convergence is `convergence_flag != 0`, under a scoped warning filter | 4.5 |
| **R92** | rectangular HAC truncated at `q = ⌊(h−1)/s⌋`, paired with Harvey at `h_eff = q+1` | 5.2 |
| **R93** | block length `max(q+1, ⌈Politis–White on loss **levels**⌉)` — levels, not differentials | 5.3 |
| **R94** | `n_eff = n/(q+1)` printed beside every figure; **h=66 reports MCS at 25% only** | 5.4 |
| **R95** | the comparison layer hands results to a `Sink` and never opens a path itself | 6 |
| **R96** | the MZ sample is an inner walk-forward at the outer stride, from knowable rows only | 7 |
| **R97** | the per-configuration driver is extracted, so one loop serves the CLI and the shootout | 8 |

**Three of them overturned the architect's own first draft**, which is recorded because a design
that was revised under measurement is worth more on the record than one that was not. The first
pass judged `arch`'s analytic GJR multistep forecast accurate to 0.0147% — measured on **iid
Gaussian** returns, the single case where a symmetric leverage recursion cannot fail. On a
persistent leveraged skew-t series it is **17.7% low** at h=66, 39.737 against 48.288 from
200,000 paths. The same pass put the bootstrap block length on the loss *differentials*, where
the shared overlap cancels: Politis–White returns 14.21 on the loss levels and **0.20** on the
differential of the same pair, so estimating from differentials would have resampled heavily
overlapping losses as if they were independent.

**What that costs, stated because it is not small.** Simulation at S=100,000 — chosen because the
Monte-Carlo spread at S=10,000 was 0.27-1.40%, inside the 0.2-1.5% band R72 set as this
project's floor for a difference worth believing — makes the full shootout roughly **2.5 hours
instead of about 15 minutes**. That is the price of not shipping a forecast that is biased by
more than the leak this project was built to catch.

---

## R98, R99 — two defects US-3.1b surfaced, and only one of them was its own

- **R98 — the `Model` and `Forecaster` Protocol members are read-only properties, not settable
  attributes.** R85 specified `name: str`, `fit_depends_on_horizon: bool` and `observations: int`
  as plain annotations. A settable Protocol member is satisfied only by a *mutable* attribute, so
  `mypy --strict` rejects a frozen dataclass:

  ```
  error: Incompatible return value type (got "FrozenForecaster", expected "Forecaster")
  note: Protocol member Forecaster.observations expected settable variable,
        got read-only attribute
  ```

  **Frozen dataclasses are this project's dominant idiom** — `Fit`, `Trial`, `OriginResult`,
  `Evaluation`, `Cohort` and `Forecast` are all frozen — so a seam that forbids them would have
  made every contestant mutable to satisfy its own interface. That is backwards, and the
  implementer was right to flag it rather than quietly dropping `frozen=True`. A read-only member
  is strictly more permissive: it accepts a frozen dataclass, a plain class with an annotated
  class attribute, and a property alike.

  Verified **in both directions** rather than once, by type-checking a frozen-dataclass
  contestant inside `src` where `mypy src` sees it: it passes with the properties and fails with
  the exact error above without them. (`volab` ships no `py.typed`, so the probe had to live
  inside the package — an incidental finding, noted for whenever this is consumed as a library.)
  *Cost if wrong:* contestants are written mutable, and the seam's own idiom disagrees with the
  rest of the codebase.

- **R99 — the refusal path no longer re-appends trials R74 has already written, and this was a
  live over-count in the one number the project exists to report.** `_flush` was the pre-R74
  behaviour: append everything once, at the end, so a refusal could not erase printed numbers.
  R74 then made each configuration append as it finished — and the flush stayed. The two
  overlapped, so on a run where SPY succeeded and QQQ refused, **SPY's six trials were written
  twice**, the second time under `datetime.now(dt.UTC)` rather than the run's single `started_at`,
  so the duplicates were not even grouped with their own run and R70's one-timestamp rule broke
  with them.

  The ledger's entire purpose is the sentence in `walkforward`'s own docstring — *"a harness that
  reports a winner without saying how many candidates it considered is the thing this project
  refuses"* — and it was inflating that count on exactly the path R74 was written to protect.

  **Why no test saw it, which is the part worth keeping.** The witness was
  `test_the_refusal_path_still_flushes_the_trials_already_counted`, and it exercised `_flush` in
  isolation: hand it trials, assert they are appended. They were. Nothing drove **two**
  configurations, so nothing observed the interaction, and the test's name described a property
  its body never checked. That is the same shape as R72 and R78 — a test that checks a helper
  rather than the behaviour the helper exists to produce — and it is now the third instance in
  this project, which is why it is a ruling rather than a commit note.

  Replaced by `test_a_later_refusal_leaves_the_earlier_trials_counted_exactly_once`, which drives
  the CLI over two configurations at two horizons, asserts the ledger holds the first
  configuration's lines **exactly once**, and asserts all lines share one `at`. Confirmed to fail
  on the pre-fix code at precisely that assertion before being relied on. *Cost if wrong:* the
  trial count is the headline number, and a second kind of duplicate would corrupt it the same
  way; the test is over the behaviour now rather than the helper, so a future refactor that
  reintroduces a second writer fails here.

---

## R100-R104 — what the two contestants found when they were built

US-3.2 and US-3.3 both reported `DONE_WITH_CONCERNS`, and between them they overturned three of
the architect's rulings. All of the figures below were **re-measured by the coordinator** from the
committed probe rather than taken from the reports.

- **R100 — R90's seeding keyword was wrong, and its Monte-Carlo figures were therefore unseeded
  draws.** `arch` reads `random_state` only for the **bootstrap**; a simulation *forecast* takes
  `rng`. The design's `random_state=RandomState(seed)` compiled, warned of nothing, and seeded
  nothing. Confirmed directly: two identical `forecast(..., method="simulation",
  random_state=RandomState(7))` calls return **different** numbers.

  That is not cosmetic. The same view forecasting a different number on each call breaks the
  byte-exact reproducibility this whole project runs on, and it was caught by the conformance
  suite's determinism clause rather than by review — which is the suite earning its cost. Now
  `rng=SkewStudent(seed=crc32_seed).simulate(...)`, rebuilt per `forecast` call, with `zlib.crc32`
  rather than the builtin `hash`, which is salted per process. *Cost if wrong:* unreproducible
  forecasts that still look fine, and a leaderboard nobody can re-derive.

- **R101 — R91's warning mechanism does not work, because `arch` overrides the caller's filter.**
  `fit` runs `warnings.filterwarnings("always", "", ConvergenceWarning)` internally, which beats
  an enclosing `simplefilter("error")`. Measured: inside `catch_warnings()` with `"error"`,
  `fit(options={"maxiter": 1})` returned `convergence_flag == 9` and **printed instead of
  raising**. So the scoped-filter design would have silently stopped detecting non-convergence.
  Replaced by `catch_warnings(record=True)` with `"always"`, classifying afterwards —
  `ConvergenceWarning` and `StartingValueWarning` become `ModelSkip`, `DataScaleWarning` and
  anything else are re-raised — with the `convergence_flag != 0` check independent of warnings
  entirely. A parametrised test pins identical behaviour under caller filters of `error`,
  `ignore`, `always` and `default`. *Cost if wrong:* a non-converged fit scored as a forecast.

- **R102 — the Monte-Carlo error does not fall with `S` for EGARCH at h=66, and that is reported
  rather than tuned away.** The probe, re-run by the coordinator:

  | spec | h | S=10,000 | S=100,000 |
  |---|---|---|---|
  | `gjr11_skewt` | 22 | 0.664 % | 0.207 % |
  | `gjr11_skewt` | 66 | 0.882 % | 0.514 % |
  | `egarch11_skewt` | 22 | 1.226 % | 0.163 % |
  | `egarch11_skewt` | 66 | 0.951 % | **3.273 %** |

  Ten times the paths made the last row **worse**. The mechanism is not a bug: the fitted β is
  about 0.98 with skew-t `η = 8`, and compounding that through EGARCH's exponential over 66 steps
  gives an h-step sum whose mean is dominated by rare explosive paths. For a sufficiently
  heavy-tailed quantity the sample mean does not converge at `1/√S`, so buying paths buys
  nothing there.

  **This undercuts R90's stated justification for S=100,000**, which was that it brings the
  residual error to 0.19-0.29% — inside the 0.2-1.5% band R72 set as this project's floor for a
  believable difference. That holds for three of the four rows and fails for the fourth.

  **The ruling, and what is deliberately *not* done.** `S` stays at 100,000: it demonstrably
  helps everywhere else, and the measured error is printed beside every simulated figure, as R94
  already does with `n_eff`. **No significance is claimed for EGARCH at h=66.** Lowering `S`
  there was considered and refused — with six seeds against a heavy tail the spread estimate is
  itself thin, so the honest claim is that the error is *insensitive to S in the measured range*,
  not that 10,000 beats 100,000. A median or trimmed estimator was also refused: the target is a
  mean, and changing the estimator to stabilise it would be answering a different question.
  *Cost if wrong:* the longest horizon's EGARCH figure carries an error bar wide enough to be
  uninformative, which is said out loud instead of being presented as a result.

  Also re-measured, and **worse than the design thought**: the analytic GJR multistep is **9.4%
  low at h=22 and 20.1% low at h=66** on the probe's series, against the design's 17.7% on its
  own. EGARCH has no analytic multistep at all (`ValueError`). R90's conclusion stands and is
  strengthened; only its figures were series-specific, which the probe's docstring now says.

- **R103 — R96's lattice is "the trailing 60 lattice points **that have a completed target**",
  not the trailing 60 lattice points.** Under the literal reading a lattice point only has a row
  in `training` when `k·stride ≥ h`, leaving `61 − ⌈h/stride⌉` pairs: **39 at h=22**, under the 40
  the ruling itself requires, so every origin would be refused — and **none at all at h=66**. The
  literal reading would have silently deleted the contestant at exactly the horizons the
  framework says recalibration matters most. Witness:
  `test_a_horizon_longer_than_the_stride_does_not_eat_the_sample`, on a hand-built 600-session
  view at h=1, 5, 22 and 66, confirmed to fail under the literal reading. *Cost if wrong:* a
  contestant that reports "refused" everywhere and looks like a data problem.

- **R104 — conformance clause 2 cannot see a leak sourced from a target frame the model captured
  itself, and that is now a story rather than a comment.** The clause truncates the targets
  handed to `evaluate`, so it catches a model that reads the frame it is given. It is blind to
  one that kept its own reference — which is precisely the shape of R78, where the audit checked
  the slice rather than what the fit consumed. Both implementers hit this independently; US-3.3's
  own guard scales the unknowable targets rather than removing them, which does catch it, but
  that guard lives in `test_recalibrated.py` and protects one contestant. **Every future
  contestant inherits the weak clause**, so the strengthening is US-3.3b, below. Recorded because
  the recalibrated implementer deleted a test that pinned the blind spot and reported it instead,
  which was the right call and would otherwise have left no trace.

---

## R105, R106 — how far the conformance clause reaches, and the hole it made me look for

- **R105 — clause 7 closes the same-object case of R104, and the rest is named rather than
  implied.** `clause_7_unknowable_targets_are_out_of_reach` scales every target dated after the
  origin — the origin's own included — **in place, in `toy.targets` itself**, and requires the
  contestant's forecast to be unchanged, restoring the cells in a `finally` and asserting the
  frame came back bit-equal. Scaling rather than removing is what makes it work: a model holding
  the frame sees changed values, where removal can be invisible to it. It is wired into
  `conformance_suite` with the signature unchanged, so all seven existing contestants and every
  future one inherit it with no edits — and all seven pass. Cost is about clause 2's, measured per
  contestant.

  **What it does not reach**, stated because an unstated limit is how R104 happened in the first
  place: a contestant that `.copy()`d the frame at construction, derived a Series from it (pandas
  3.0.6 copy-on-write separates that from later writes to the parent), or loaded its own frame.
  Those need the model **rebuilt** from a perturbed frame, which is what
  `test_recalibrated.py`'s `forecast_moves_with_the_unknowable` guard did for one contestant.
  **That guard is now gone**, subsumed by clause 8 below, which uses the same perturbation and the
  same rebuild for every contestant — so the mechanism survived and stopped being one file's.

  **Why that is not simply added as an optional argument**, which is the implementer's argument
  and it is correct: an opt-in `rebuild=` would be skipped by exactly the contestant that needed
  it, which is R104 over again. Closing the class needs it **mandatory**, so no contestant can
  decline, and that is a signature change touching three caller files — **US-3.3c**, below. Note
  what it would and would not buy: none of the seven current contestants takes a frame at
  construction, so for all of them a mandatory factory passes trivially. Its value is entirely
  prospective, against the contestant someone writes next.
  *Cost if wrong:* a leak that enters through a captured copy rather than a captured reference
  survives the shared suite, as it does today, with one contestant's own test as the only cover.

- **R106 — a contestant may not reach the store, and nothing enforced that until now.** Found by
  asking what else the clause-7 boundary implied rather than by anything failing. A model receives
  everything it may see in its `TrainingView`; `src/volab/models/` was nevertheless free to write
  `from volab.store import load_panel`. `load_panel` requires an explicit `end` and refuses to
  cross `dev_end`, so the holdout was never reachable — but every session **up to** `dev_end` was,
  which at a 2021 origin is four years of future.

  **Truncation equivalence could not have caught it**, which is why this is a ruling and not a
  tidy-up. The comparison perturbs the panel handed to the engine; a contestant bypassing that
  panel returns the same answer on both sides and the test passes. That is R78's shape one level
  over — the check watches the input, and the leak does not arrive through the input.

  Now scanned: `test_a_contestant_cannot_reach_the_store` over every module in
  `src/volab/models/`, with a red case per spelling (module import, `from`-import, attribute call,
  bare name) and a vacuity guard.

  **The first version of the ban was too broad, and the suite caught it immediately.** It banned
  `volab.panel` by module, and `models/recalibrated.py` legitimately imports `as_of_dates` from
  it. `volab.panel` is a *mixed* module: `load_curated_panel`, `load_bars` and `load_exogenous`
  read, while `as_of_dates`, `truncate` and `collapse_superseded_observations` are pure frame
  helpers that open nothing. So `volab.store` is banned by module — it has no other purpose — and
  the read *functions* are banned by name wherever they are reached from. The rejected version is
  kept as a passing red case (`volab.panel's pure frame helpers`), so the ban cannot quietly
  broaden again. *Cost if wrong:* a contestant with its own read path, invisible to the primary
  point-in-time test.

---

## R107-R110 — the comparison layer's rulings, and three more places the design was wrong

US-3.4 planted **24 defects** and reported that two survived until it closed them, which is the
kind of number that makes a mutation claim worth believing. It anchored the hard part on closed
forms rather than on plausibility: at `q = 0` the headline DM equals `scipy.stats.ttest_1samp` to
1e-12, and on the alternating series `d = c + (−1)^i` the rectangular `V`, the Bartlett `V` and
the non-PSD case are all exact arithmetic. Gates verified by the coordinator: **1040 tests**,
`mypy` clean over 35 modules, `data/trials.jsonl` still 72 lines.

- **R107 — a model is in the confidence set when `p > α`, which is `arch`'s rule and not
  Hansen-Lunde-Nason's `≥`.** They differ only at an exact tie, roughly one draw in ten thousand
  with 10,000 replications, and the ruling is to follow the implementation that computes the
  p-value rather than to post-process its output into a different convention. Both a tie test and
  a surviving mutation pin it — the mutation is how the difference was found at all. *Cost if
  wrong:* a boundary case excluded where the paper would include it, in a set already reported at
  two levels.

- **R108 — five refusals the design did not name, each detected rather than warned about.**
  Politis-White divides by zero at `n ≤ 10` (measured), so the bootstrap refuses below 11
  origins; DM refuses at `n ≤ q+1`; identical or constant loss series refuse for MCS and SPA,
  because `arch` otherwise emits a `RuntimeWarning` and R101 is the precedent for detecting the
  condition instead of catching the warning; and `loss_series` refuses a cohort label that does
  not match its evaluation.

  The fifth is a **generalisation beyond the design, and the right direction**: DM significance is
  withheld wherever `n_eff < 20`, not only at h=66, with `dm_significance_claimed` and a per-pair
  `claimed` flag on the record. R94 derived the threshold for the MCS and named h=66; the same
  thinness applies to DM at any `(h, stride)` reaching it. *Cost if wrong:* significance withheld
  on a window someone later judges adequate — recoverable, and the opposite error is not.

- **R109 — only the sink opens a path, and the scan that proves it cannot go blind.**
  `test_only_the_sink_opens_a_path` is an AST scan over every `compare/*.py` banning `open`,
  `write_*`, `read_*`, `to_json`, `to_csv`, `to_parquet`, `mkdir` and `dump`, with a red case per
  spelling — **and it requires `sink.py` itself to still show `.open` and `.mkdir`**, so the day
  the writer moves, the scan fails instead of passing over an empty set. That second half is the
  lesson from `test_store_is_actually_exempt` and from
  `test_the_sealed_set_is_not_empty_and_holds_what_it_claims`, applied a third time.

  Confirmed behaviourally as well as structurally: a run through an in-memory `Sink` creates
  nothing under the storage root, which is what keeps `MlflowSink` reachable after R84 refused
  MLflow; and after `append_comparisons`, `count_trials` is **0**, so a comparison can never
  reach the trial ledger and corrupt the one number the write-up reports.

- **R110 — three more corrections to the design, found by building it.** The pattern from R100-R103
  continues, and the count now stands at six of the architect's rulings corrected by the
  implementers who used them.

  1. **§5.3 is wrong about where the `q+1` floor bites.** It says the floor matters at h=1 and
     h=5; at stride 5, `q+1` is 1 at both, so it does nothing there. It binds at **h=22 and
     h=66**, where `q+1` is 5 and 14.
  2. **§7 names `test_the_refusal_path_still_flushes_the_trials_already_counted`**, which **R99
     deleted** — it was the test whose isolation hid the ledger over-count. The parametrised
     `test_a_later_refusal_leaves_the_earlier_trials_counted_exactly_once` stands in its place,
     now running against both entry points.
  3. **R93's 14.21-against-0.20 is series-specific**, as R102 already said of R90's figures. On
     the implementer's own MA(13) pair at n=238 Politis-White gives **15.51 and 15.72 on the
     levels against 0.34 on the differential**, and three seeds put the differential at 0.34,
     0.89 and 1.04. The *conclusion* — estimate the block length from levels, never differentials,
     because the shared overlap cancels — is unchanged and is what the test asserts (levels > 13,
     differential < 2). Only the two decimals were ever specific to one series.

  *Cost if wrong:* a reader takes a figure for a property. Which is the whole reason this document
  separates "a `tools/` command", "an existing test id" and "historical" at the top.

---

## R111 — the rebuild factory is mandatory, and it passes vacuously today on purpose

US-3.3c closed R105's remaining case. `conformance_suite(model, *, toy, rebuild)` now takes a
keyword-only factory **with no default**, so a caller cannot decline it: omitting it is a
`TypeError`, which is the one form of "mandatory" that cannot be forgotten.
`clause_8_a_rebuilt_contestant_cannot_see_unknowable_targets` builds the contestant twice — once
from the honest target frame, once from a copy whose post-origin targets are scaled — and requires
the two forecasts to agree.

**The before/after that shows it does something**, which is the part a count of passing tests
cannot tell you: a plant whose constructor does `self.frame = targets.copy()` **passes clause 2
and passes clause 7**, and fails clause 8. The same holds for a plant that stores a derived
`Series`, which R105 predicted and the implementer confirmed. So the hole R105 named is shut.

**It passes vacuously for all seven current contestants, and the docstring says so.** None of them
takes a target frame at construction, so every `rebuild` is a closure ignoring its argument. The
clause tests nothing today; its value is entirely prospective, against the contestant someone
writes next. Recorded in this tone deliberately — a clause described as though it were checking
something now is how a suite comes to be trusted for work it is not doing.

Three limits, stated rather than left to be discovered:

1. It cannot detect a factory that ignores its argument when the contestant genuinely needs the
   frame. That is the vacuity above, and no test can close it — only review of the call site can.
2. It does not catch a contestant that loads its own frame from a store, because a rebuild hands
   it no new data. Truncation equivalence misses that too; **R106's store scan is the cover**, and
   the two were written a day apart without either knowing it was completing the other.
3. The factory's model must share the name of the model under test, which the clause enforces, or
   a factory could quietly substitute a different contestant.

*Cost if wrong:* a clause that runs for every contestant and costs about clause 7 — roughly 1.5s
across all seven — in exchange for closing a leak class that has already escaped this project
twice on the target side (R72, R78).

**Two process notes, recorded because the honest report is worth more than the tidy one.** The
implementer committed twice with the lint check red and fixed it in two follow-up commits rather
than quietly amending, so `d0f1902..18762b1` is four commits for one story; the history is left as
it happened. And it implemented clause 8 before writing the plant test, then demonstrated the test
could fail by neutering the clause — which is not test-first, is reported as not test-first, and is
weaker evidence than a genuine red run. The same departure as US-3.2 and US-3.3, and the third
time it is worth noting that the method keeps losing to the convenience of writing the code first.

---

## The partial shootout run, recorded as partial

`python -m volab.compare run`, all defaults, on `2e9e31b` with a clean tree. **It did not
finish.** It appended **42 of an intended 168 trials** and exited 1 after SPY h=22, on a crash
inside `arch`'s model confidence set. Recorded here in full rather than re-run and quietly
replaced, because this document's own standard is that a run which happened is part of the
record.

```
storage root …/vol-lab/data
reads stop at dev_end 2025-12-31
git 2e9e31b61ce3, clean
  251 origin(s) from 2021-01-04 to 2025-12-24
  truncation equivalence: 251 of 251 origins identical
  250 origin(s) from 2021-01-04 to 2025-12-17
  truncation equivalence: 250 of 250 origins identical
  247 origin(s) from 2021-01-04 to 2025-11-25
  truncation equivalence: 247 of 247 origins identical
```

- **R112 — the 42 trials are committed and counted.** R74 exists for this case: a number that was
  computed must not vanish because the run that produced it later failed, and a ledger that drops
  a crashed run's trials is the forking-paths hole it was built to close. Verified rather than
  assumed: every appended line carries sha `2e9e31b`, `git_dirty: false`, one shared `at` and
  cohort `phase2-shootout`; the 72 pre-existing baseline lines are untouched.

  **So the ledger reads 114 lines, of which 42 come from a partial run, and no write-up may
  describe it as 240.** The intended figure is on the board; the actual figure is on the ledger;
  they differ and the difference is the point. *Cost if wrong:* a reader takes 114 for a complete
  shootout.

### What the three completed configurations say, with every caveat attached

**Truncation equivalence held at every origin of every configuration that ran** — 251 of 251, 250
of 250, 247 of 247, no disagreement. The leakage check rode along for free, as designed, and the
seven-contestant cohort did not move it.

On **QLIKE**, and this was not expected: the GARCH family leads `har_levels` at all three
horizons that completed.

| | best | second | third |
|---|---|---|---|
| h=1 | `egarch11_skewt` −8.25987 | `gjr11_skewt` −8.24700 | `har_levels` −8.23195 |
| h=5 | `gjr11_skewt` −6.66089 | `har_levels` −6.63694 | `garch11_skewt` −6.63434 |
| h=22 | `garch11_skewt` −5.07616 | `gjr11_skewt` −5.03890 | `har_levels` −5.00938 |

**Nothing may be concluded from that table yet, and the reasons are not formalities.** It is
**SPY only** — QQQ and IWM never ran. The margins are in the third decimal of a loss whose level
is meaningless (R73's caution: QLIKE is reported without its additive constant, so only
differences within a configuration mean anything). The model confidence sets at h=1 and h=5
contain **six or seven of the seven contestants**, so the sets separate almost nothing. And the
same EGARCH that leads h=1 on QLIKE has an MSE of **1.70e+12** at h=22 — so at minimum one
contestant is producing a number on one loss while being catastrophically wrong on the other,
which is itself a reason to distrust its QLIKE ranking rather than to report it.

Said plainly because a partial run showing the new contestants ahead is exactly the result it
would be most tempting to quote early.

### The first real disagreement the two-column DM design had to refuse

R92's second column existed on the argument that at short horizons the headline estimator reduces
to the sample variance while volatility clustering leaves the losses autocorrelated anyway. On its
first outing against real data it earned that:

```
random_walk vs garch11_skewt: d_bar=+4.75e-07
  | headline t=+1.853 p=0.0650 | bartlett z=+2.3 p=0.0214
  [columns disagree at 5%: both shown, neither claimed]
```

One column says not significant, the other says significant, and the harness **printed both and
claimed neither** rather than taking the convenient one. That is the first time the comparison
layer had to decline something real.

Also confirmed on real data: **`q = 0` at h=5 with stride 5**, the counter-intuitive value R92
derived and flagged as the case where intuition says otherwise.

### Two defects, under root-cause diagnosis before any fix

Recorded now; the rulings follow the diagnosis, because patching a statistical layer on a
hypothesis is how a number nobody can trust gets produced.

1. **`arch` 8.0.0's model confidence set crashes against `pandas` 3.0.6**, in the elimination path
   of `_format_pvalues`, where an index array reaches an Arrow-backed `Index` that rejects it. It
   did **not** fire at h=1 or h=5 although eliminations demonstrably happened there, so "any
   elimination" is not the trigger. **The more serious fact is that all 152 tests in
   `tests/test_compare.py` pass**: they run on synthetic loss series, and the headline function
   dies on real data. A suite that is green while the thing it covers fails in production is this
   project's founding complaint, now landed on its newest code.
2. **EGARCH diverges progressively on real data**: MSE `3.76e-07` at h=1 (second best),
   `8.38e-05` at h=5 (a hundredfold worse than every rival), `1.70e+12` at h=22. A variance
   forecast of order 1e6 against a target of order 1e-5 is not a forecast. R102 predicted trouble
   at h=66 from Monte-Carlo spread on a synthetic probe series; the real failure is far larger and
   arrives at h=22.

   **This is also what the Monte-Carlo provenance label was for.** The h=22 line printed
   `MC error 0.163% (synthetic probe series, not SPY)` beside a contestant that was exploding — a
   calm figure, measured on other data, useless here. The label stopped it being read as SPY's
   own; a bare `0.163%` would have made the output look settled.

---

## R113-R116 — what the diagnosis found, including two things I had said wrongly

- **R113 — the MCS crash is `arch`'s, not pandas', and the crash is the only reason it was
  found.** `MCS._compute_r` does `i = loc.squeeze()[0]`, a scalar **only when the argmax of the
  standardized loss differential is unique**. With several rows, `squeeze()` leaves shape `(m, 2)`
  and `[0]` returns the `(i, j)` **pair**, which (a) puts an `ndarray` into `eliminated`, making
  the index column object dtype that `Index.__getitem__` rejects, and (b) **eliminates two models
  in one step**. `_compute_max` handles the same case correctly; `METHOD = "R"` does not.

  The tie comes from float64 annihilation: with one mean loss at 1.7e12 and the rest near 1e-5,
  `L̄_i − L̄_j` falls below half an ulp of the large value, so the entire standardized row is one
  repeated float. That is why h=1 and h=5 eliminated cleanly — at h=5 EGARCH's ratio is ~1e2,
  nowhere near 1/eps.

  **I told the user this was a pandas 3 / Arrow incompatibility. It is not, and the diagnosis
  refuted it:** an object-dtype indexer raises on a pandas-2-shaped `Index` too, and on raw numpy;
  only the message differs. A pandas pin would have bought nothing.

  **And the fix I floated was the worst option available.** Pre-formatting the losses so arch
  never indexes an Arrow `Index` converts the crash into a **silently wrong** MCS that eliminates
  `har_levels` — the SPA benchmark — alongside the exploding contestant and reports k−1 rows. The
  guard instead **refuses**, on the mean losses, where `|L̄_j − L̄_k| ≤ 4·eps·max|L̄_m|` for some
  pair against a common contestant: the exact analogue of `same_losses`'s scaled tolerance one
  level up. Deliberately a conservative superset of arch's tie, because a refusal with a reason
  beats an elimination order decided by rounding. There is **no clean ratio threshold** — the
  sweep showed 1e14 crashing and 1e15 surviving, since the bootstrap variances must collide too.
  Worth filing upstream: the fix there is `i = int(loc[0, 0])`.

- **R114 — EGARCH(1,1) with skew-t errors has no finite multi-step conditional variance, so every
  h ≥ 2 forecast it produced is an estimate of infinity.** Not an implementation defect. From the
  recursion, `σ²_{t+1} = C·(σ²_t)^β·exp(α|z_t| + γz_t)`, and Hansen's standardized skew-t has
  **polynomial** tails, so `E[exp(α|z| + γz)] = +∞` for any `α > 0`. The integral over `|z| < 800`
  measures 1.91e+128 and grows without bound. `h = 1` is exact because `σ²_{t+1}` is known at `t`
  and needs no expectation. This is Nelson's own reason for using GED rather than a t.

  The sample mean over S paths is finite for any S but has no limit, so it **diverges in S**:
  0.00252 at S=1e4, 0.0630 at 1e5, 0.159 at 1e6, with relative spread going 4.5% → 231% → 239%,
  while the **median** path stays at 0.0025-0.0033. The ledger's 1.70e+12 was reproduced
  bit-identically, and **one origin carries 99.99999999999999% of it**, within which **one path
  of 100,000 carries 99.99999997%**. All 247 fits converged, β ∈ [0.957, 0.968]; there was nothing
  for `convergence_flag` to catch.

  **So it is unusable at every h ≥ 2 — including h=5**, whose 8.38e-05 is the same non-existent
  expectation that merely drew a less absurd number. Only h=1 is credible (3.76e-07, second best
  on MSE).

  **The refusal the user approved, and that I proposed, is wrong, and this is the correction.** A
  threshold on "a forecast many orders above the realised scale" (a) compares against the
  **target**, which is look-ahead; (b) selects on the random draw, biasing the scored mean
  downward; and (c) could not work mechanically, because `Evaluation.comparable` **intersects the
  defined-origin set across all contestants**, so a per-origin skip would empty every one of the
  seven contestants' sample at that horizon. The refusal must be **contestant-level and a
  priori** — available from the specification alone, using no draw and no target: EGARCH + an
  error law with polynomial tails + α > 0 ⇒ no finite h-step variance for h ≥ 2.

  *Cost if wrong:* the shootout loses one contestant at three of four horizons. Against that,
  "EGARCH(1,1) with skew-t has no finite multi-step variance forecast" is a **genuine, citable
  result this project produced by measuring** — R102 saw its shadow at h=66 on a synthetic probe;
  this run proved it on real data.

- **R115 — a Monte-Carlo error measured on another series may withhold a claim but never
  authorise one.** The present rule did the opposite. `("egarch11_skewt", 22)` is 0.163%, under
  the 1.5% ceiling, so EGARCH at h=22 was **not** in `unclaimed` and **significance was claimed
  for it** — while its measured spread on SPY at the same path count is **230.79%**. Verified
  directly.

  Two causes, the second deeper: the probe's series is generated at α=0.05, β=0.87 against SPY's
  fitted α≈0.25, β≈0.96, so it is not merely another series but a **gentler process**; and **a
  six-seed standard deviation is not a measurement of an estimator with no finite mean**, so a
  small figure there is no evidence of precision at all. The rule treated absence of measured
  spread as presence of precision. *Cost if wrong:* the guard built to withhold claims was
  granting them.

- **R116 — the h=5 comparison records are not interpretable for any contestant, not just for
  EGARCH.** `arch`'s MCS p-values are a cumulative maximum along the elimination sequence, and
  EGARCH's loss column enters every step's test statistic. At h=5 MSE it was eliminated second at
  0.571, which **floors every later p-value**: `har_semilog` and `har_levels_mz` read 0.625,
  `har_levels` and `garch11_skewt` 0.704. Both SPA runs at h=5 also carry EGARCH among the
  alternatives. So those records are marked uninterpretable rather than footnoted.

  **What is clean:** every h=1 record; every pairwise DM not involving EGARCH at any horizon,
  since `pairwise()` runs before `model_confidence_set()`; and the other six contestants' 36
  trials. Also recorded: `comparisons.jsonl` has no h=22 record not because it failed but because
  both losses' comparisons are built in one comprehension, so the successful **qlike** comparison
  was lost when **mse** raised beside it.

### Still unchecked, and flagged rather than assumed clean

GJR's fitted persistence on SPY is `α + γ/2 + β ≈ 1.000`, right at the fourth-moment boundary.
Its η ∈ [5.31, 6.39] does give the innovation a fourth moment, unlike EGARCH's, so its simulated
mean has finite variance — but the process-level condition was not verified and its path
concentration was not measured. The same `top1_share` diagnostic on GJR at h=22 is the cheap
follow-up. **Not claimed clean.**

## R117-R121 — GJR is not EGARCH, and the reason it looks wrong is a library bug

Dispatched because R116 left GJR "not claimed clean" while it sits in both cohorts at every
horizon and leads QLIKE at h=5. The check was independent of the diagnosis that produced R113-R116.
Verified here before recording: arch's constraint source, the recursion algebra, and every figure
against the live ledger.

### R117 — the multi-step mean exists, and R116 named the wrong boundary

`σ²_{t+k+1} = ω + σ²_{t+k}·A_{t+k}` with `A = β + (α + γ·1{z<0})z²` and `A` independent of
`σ²_{t+k}`, so the conditional mean obeys a **linear** recursion:

```
m_{k+1} = ω + φ·m_k ,   φ = α·E[z²] + γ·κ + β ,   κ = E[z² 1{z<0}]
```

**The exact condition for `E_t[σ²_{t+k}]` to exist is `E[z²] < ∞`, i.e. `η > 2`.** Nothing else.
arch bounds `η ∈ [2.05, 300]`, so the h-step mean exists at every origin at every finite h,
**whatever the persistence**. `φ < 1` governs boundedness as `h → ∞`, not existence at finite h.

This is not EGARCH by analogy and the distinction is the whole point: there is no exponential of a
fat-tailed variable anywhere in GJR's recursion. EGARCH had no finite value to converge to; GJR
does, and `η ∈ [5.01, 7.03]` over 250 SPY origins makes `ψ = E[A²]` finite, so the simulated mean's
variance is finite too.

**Correcting R116, which is mine.** It called `α + γ/2 + β ≈ 1.000` "the fourth-moment boundary".
That is the **second**-moment (covariance-stationarity) boundary under `κ = ½`; the fourth-moment
condition is `ψ < 1`. The error mattered: it is why R116 reasoned from `η` giving the *innovation* a
fourth moment and concluded the estimator was probably fine, when the quantity that governs the
estimator is `ψ`, which is 1.29 to 1.89 on SPY — above 1 at 250 of 250 origins.

### R118 — root cause: arch's stationarity constraint assumes a symmetric error law

`arch/univariate/volatility.py`, `GARCH.constraints`, read in this session:

```python
# sum(alpha) + 0.5 sum(gamma) + sum(beta) < 1
a[k_arch + 1, p + 1 : p + o + 1] = -0.5
```

That `0.5` **is** `E[z² 1{z<0}]` for a symmetric law. Under Hansen's skew-t with `λ < 0`, `κ > ½`:

```
φ_true = (α + γ/2 + β)  +  γ·(κ − ½)
```

so **arch's feasible region contains explosive processes**, and the likelihood pushes persistence to
the edge of the region it is given. The root is the feasible region, not the constraint binding:
the smallest `γ(κ − ½)` across SPY's 250 origins is 0.01526 and the largest available slack
`1 − φ_sym` is 0.00657, so the origins where the constraint did *not* bind are explosive too.

| symbol | `φ_sym` pinned at 1 | **`φ_true ≥ 1`** | median `φ_true` | max `φ_true` | max `ψ` |
|---|---|---|---|---|---|
| SPY | 150 / 250 | **238 / 238** | 1.0165 | 1.0267 | 1.885 |
| QQQ | 71 | **238 / 238** | 1.0170 | 1.0320 | 2.238 |
| IWM | 0 | **109 / 238** | 0.9995 | 1.0061 | 1.135 |

`λ < 0` at every origin of every symbol. **So the fitted SPY and QQQ processes are explosive at
every origin**, and GJR's exact h=66 mean is 3.70x the realized target at the median SPY origin.
This is a property of the contestant *as arch constrains it*, which is what the leaderboard
evaluates and must therefore say.

`garch11_skewt` is immune **by construction**: `φ = α·E[z²] + β = α + β` has no `κ` in it, so
arch's constraint is the true condition and its analytic recursion is the true mean under any
unit-variance law. On SPY `α + β` is pinned to 1 within 1e-9 at 192/250 origins — effectively an
IGARCH fit, exact and finite, growing about linearly in h. R90's choice of `"analytic"` for the
symmetric specification is the entire reason it is trustworthy at h=66.

### R119 — the probe's Monte-Carlo figures are not a safe proxy, and the table must be re-measured

`compare/cohorts.py` carries `("gjr11_skewt", 22): 0.207` and `("gjr11_skewt", 66): 0.514` from
R102's synthetic probe. Measured on SPY, per-origin, as the distribution of `f_sim / f_exact`:

| horizon | min | median | max | theory rel sd at S=1e5 (median / max) | origins over the 1.5% ceiling |
|---|---|---|---|---|---|
| h=5 | 0.9938 | 0.9998 | 1.0084 | 0.21% / 0.40% | **0 / 250** |
| h=22 | 0.9638 | 0.9978 | 1.3258 | 1.32% / 20.5% | **106 / 247** |
| h=66 | 0.8178 | 0.9783 | 1.5434 | 193% / 3,409,228% | **238 / 238** |

Relative MC error grows like `(ψ/φ²)^h`; SPY's 1.26-1.79 gives `5e6` to `5e16` at h=66 against the
probe's `1.088 → 3e2`. **That gap is why the probe's 0.514% never transferred.**

**No claim was ever made on these figures** — `MONTE_CARLO_ERROR_SOURCE` is
`"synthetic probe series, not SPY"`, never equal to `series_source(series)`, so the guard prints
`0.207%, no claim` and withholds. The defect is display truth, not a false claim: a reader sees a
figure labelled "probe, not SPY" and reasonably infers SPY is similar, and it is not — the probe is
optimistic by one to six orders of magnitude. `cohorts.py` already anticipated this: *"The day the
figures are re-measured on the real panel, this string and the table change together."* That day is
now, and the table must carry the SPY figures, "not measured" for QQQ and IWM, and a marker at h=66
saying no feasible path count bounds the error.

**`top1_share` would have been the wrong follow-up**, which R116 proposed as the cheap deciding
test. GJR has **no** SPY origin with a path over 50% at h=22 or h=66, so by that metric it reads
clean. The ratio of theoretical to sample-implied MC sd has median 58.8 and max 5.2e5 at h=66: the
paths carrying the variance are never drawn, so concentration among the drawn paths looks benign by
construction. This is the same trap that made R102's six-seed spread read as precision for EGARCH,
and it is the third time in this project a diagnostic has been proposed that measures the sample
rather than the estimator.

### R120 — GJR at h=66 is **not** excluded, and the open question is the estimator, not the contestant

R114 excluded EGARCH at h≥2 because the quantity **does not exist**. GJR's does (R117), so there is
nothing non-existent being estimated and exclusion would delete the most informative result in the
shootout — that the fitted process is explosive. Pre-registration §2 already refuses *all*
significance at h=22 and h=66 on `n_eff` grounds, so what h=66 can carry is a loss value and a
ranking, both of which survive: GJR is last on MSE by a factor of 5.3, and correcting the estimator
makes its loss **worse** (exact 6.74e-4 against the simulated 6.19e-4), not better.

**One ordering is refused:** GJR against `har_levels_mz` for 4th/5th on h=66 QLIKE, a 0.0148 margin
against a 0.0073 systematic deficit. That is a median shift, not independent noise, so it does not
average away across origins. Reported as unordered.

**No rank in the in-flight run flips** at any horizon on either loss — checked against the live
ledger, SPY only. The run was not stopped. The h=5 QLIKE lead is real: a 0.0002 sim-vs-exact
difference against a 0.0240 margin, 118x.

**What is open, and it blocks registration rather than Phase 2's close.** R115 requires a simulated
contestant's MC error to be measured on the series being evaluated. At the reveal those are holdout
fits, and the measurement needs `f_exact`, which only a closed form supplies — seed spread
understates the true sd by a median 58.8x. So under the simulated estimator R115 would refuse GJR's
significance at h≥22 *and* at h=5, where it currently leads. The two honest routes are the exact
closed form `m_{k+1} = ω + φ_true·m_k` with `κ` by quadrature of the fitted skew-t — which also
retires R90's reason to simulate — or registering with the simulated estimator and declaring its
refusals in advance. **The user's decision, not a builder's default.**

The deeper fix — a `κ(η, λ)`-aware stationarity constraint replacing arch's `0.5` — changes the fit
itself and its effect on the losses is unknown. **Refused before registration** for exactly that
reason, and recorded as a Phase 3 candidate.

### R121 — the re-run's ledger provenance, and what the overlap proves

The re-run appended under sha `0dd0e60` with `git_dirty: true`, not the `37c74e7` its own gate
pushed. Neither is a code change and a ledger reader cannot know that unless it is written down:
part 1's 42 appends dirtied the tracked `trials.jsonl` before part 2 captured its R70 state, and
the board commit `0dd0e60` landed between the two parts. **A ledger line's sha is the tree when
that configuration started, not the tree the run's gate passed on.**

| run | sha | dirty | lines |
|---|---|---|---|
| dev shootout | `02eb0e5` | clean | 72 |
| crashed run (R112) | `2e9e31b` | clean | 42 |
| re-run part 1, h=1 | `37c74e7` | clean | 42 |
| re-run part 2, h=5/22/66 | `0dd0e60` | **dirty** | 108 |

**38 of 38 overlapping configurations are bit-identical in value *and* origin count** across those
different shas. Two things follow, neither of which needed a new test:

1. A determinism witness across processes and trees, not merely across two calls in one process.
2. **R114's exclusion cost zero origins.** The crashed run computed h=5 and h=22 with seven
   contestants, the re-run with six, and every surviving contestant's origin count is unchanged —
   so EGARCH was defined wherever the other six were, and dropping it did not widen the
   intersection. `Evaluation.comparable()`'s intersection was not silently paying for R114.
