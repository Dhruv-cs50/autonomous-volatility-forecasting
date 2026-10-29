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

## R122, R123 — the recorded run, and a false reason the leaderboard prints

### R122 — the run is recorded, and a third of it carries a dirty-tree flag I caused

150 lines appended, ledger 114 -> 264, composition exactly as pre-registered: 42 at h=1 (seven
contestants) and 36 at each of h=5, 22 and 66 (six). `egarch11_skewt` appears at h=1 and nowhere
above it, so **R114 held in practice and not merely on paper**. `comparisons.jsonl` 4 -> 28.

**The blemish is mine.** Part 2's 108 lines carry `git_dirty: true` because I committed the board
while the shootout was running, and the leaderboard marks every one of those rows
`[DIRTY TREE: the sha does not identify the code that produced this]`. Only documentation changed,
but the flag cannot know that and **should not be taught to** — a flag that trusts the committer is
not a flag. So a third of the headline is from a tree nobody can reconstruct.

**Consequence, decided here rather than carried:** do not re-run part 2 to clean the flag. The
closed form (R120) changes GJR's forecast and forces a re-run anyway. The recorded run that Phase 2
exits on will be **one** clean-tree run after the closed form lands, with nothing else committing
while it goes. Two expensive runs to fix a flag that one run fixes incidentally would be waste.

**The dev window's `n_eff` is not the holdout's, and a write-up must not borrow across.** At h=22
the dev run has stride 5, so `q = ⌊21/5⌋ = 4` and `n_eff = 49.2` — DM significance **is** claimed
here. The pre-registered holdout read is stride 1 over a shorter window, giving `n_eff = 8.5` and
refusing it. Both are right for their own window. h=66 refuses in both (dev `n_eff = 16.8` against
the 20 that a 10% MCS needs), and the leaderboard prints the refusal with its arithmetic.

**What the run confirms about GJR, against the live ledger rather than a probe.** On SPY at h=66
it is last on MSE at 6.19e-4 against `har_levels_mz`'s 1.178e-4 — a factor of **5.26**, so the rank
is far outside the estimator's 8.8% deficit. On QLIKE it is 4th at **-3.52307** against
`har_levels_mz`'s **-3.50829**: a margin of **0.01478** against a 0.0073 systematic deficit, so
R120's refusal of that one ordering is confirmed at the digit rather than asserted.

**The truncation-equivalence report for this run is lost, and that is my driver's fault.** It
redirected the shootout's stdout and printed only a per-part summary line, so the report
`run_configuration` emits by delegation went nowhere. The exhaustive sweep was re-run standalone
afterwards instead, which is the stronger witness. **Third time in this session a driver of mine
discarded evidence** — `; echo "exit=$?"` masked an exit code and `-r s` dropped pytest's FAILED
line. The pattern is always the same shape: a wrapper that summarises rather than passes through.

### R123 — the withheld reason is false in every instance this run prints

`leaderboard/_withheld_reasons` prints one wording for every unclaimed contestant:

```python
reasons.append(f"{name}'s Monte Carlo error is above the floor")
```

Three distinct causes reach `unclaimed`, and `compare/__init__.py` distinguishes all three — a
figure above `MC_ERROR_CEILING_PERCENT`, a figure measured on another series **whatever its
value**, and **no figure at all**. Only the first matches the printed sentence. Checked against
this run:

| what prints | GJR's figure | why it is actually unclaimed | is the printed reason true? |
|---|---|---|---|
| h=1, h=5 | `None` | **unmeasured** — and at h=1 there is nothing to measure, since the forecast takes no expectation | **no** |
| h=22 | 0.207% | **source** — it is *under* the 1.5% ceiling | **no** |
| h=66 | 0.514% | **source** — also under the ceiling | **no** |

**So the reason is false in every instance the current run produces.** The only case it would
describe correctly is EGARCH's 3.273% at h=66, which R114 excludes, so it never prints. No claim is
wrongly *made* — the guard withholds correctly throughout, which is why this survived review — but
the stated reason misinforms a reader about which rule fired, and "above the floor" invites exactly
the wrong fix: re-measuring a figure that was never too large.

**This is US-3.5's own escalation biting on a different field.** US-3.8 already carries "import the
DM verdict wording rather than duplicate it"; `compare/__init__.py` line 278 holds the correct
three-way sentence and the leaderboard wrote a one-way paraphrase instead. **A second duplication
of the same kind, found by rendering the output rather than by reading the code** — which is also
how the printed figure's missing source was found at US-3.4. Fix belongs in US-3.8, and the witness
must assert the *reason text per cause*, not merely that some reason is printed.

## R124, R125 — the estimator decisions, and four things measurement overturned

Design: `docs/superpowers/specs/2026-10-28-gjr-exact-forecast-design.md` (`b2f6b16`, `f9b39a8`),
ratified by reference. The user chose to build the closed form before registering, so R120's open
item is closed.

### R124 — the three decisions the design left to me

**Ledger provenance: boundary sha and a required clean tree. No schema change.** Not a `method`
field on `Trial` and not a rename. The design priced all three and called only the third
self-describing; the reason it is still the wrong trade is one the design did not state. **The
leaderboard already partitions by `(at, git_sha)`** and renders each run as its own labelled
section, so a reader never compares two runs' GJR rows without seeing which run each came from —
per-trial self-description is redundant at the level anyone actually reads. And `method` is
GARCH-specific: adding it to `Trial` puts a field on 264 lines that is meaningless for four of the
seven contestants. A rename conflicts with the pre-registered contestant name, which is worse than
either.

**EGARCH keeps simulating at h=1, and the reason is post-hoc selection.** The design flagged this
rather than deciding it, correctly. Making it exact would cost nothing technically and would hand
its h=1 result a significance claim it currently cannot carry. **It leads h=1 QLIKE on the recorded
run at -7.38769.** Its h=1 simulation is already exactly correct — every one of the 100,000 paths
is identical, because `sigma2_{t+1}` is deterministic — so **there is no defect to fix, only a
claim to upgrade, for a contestant we have already watched win.** That is the thing
pre-registration exists to prevent, and it does not stop being that because the mechanism is sound.

**The contrast with GJR is the whole justification for treating them differently**, and it must be
stated or the GJR change looks like the same move. GJR's estimator is *unusable*: 238 of 238 SPY
origins over the 1.5% ceiling at h=66, a seed spread that does not fall with S, and R115
unsatisfiable at the reveal because measuring on-series needs an exact value to compare against.
The change is forced by the method's own failure, and the h=5 benefit is incidental to it. EGARCH
at h=1 has no failure. Revisit only after a contestant can declare its horizons (US-3.9) — an exact
EGARCH must *refuse* h>=2, and nothing enforces that today.

**Order: the re-run happens before registration.** That was the point of building the closed form.

**Two test changes authorised, both recorded because they touch earlier rulings.**
`test_the_three_specs_are_exactly_what_r91_names` is amended to `("GARCH", 1, "exact")`, which
**amends R91's recorded table** — the implementer was right to refuse to do that unasked. And the
AST literal-count test is **deleted, not weakened**: it checked syntax, it would have passed a
method string assembled at runtime, and it never covered EGARCH at all. Three stronger witnesses
replace it, one of them behavioural.

### R125 — four of my own claims that did not survive being measured

**The quadrature I called "verified" is wrong across arch's actual bounds.** I handed the design a
Simpson scheme as verified to 1e-10, on the strength of the earlier probe's identity checks. Across
`eta`'s full range it is off by **0.228** at `eta = 2.05, lambda = -0.9`, and returns
`kappa = 0.4755` at `eta = 2.10, lambda = 0` where symmetry forces exactly 1/2 — the `z^2` tail is
too heavy to truncate at `sinh(30)`. It is sound for `eta >= 4`, and SPY fits 5.01-7.03, so nothing
already recorded is wrong. **But I passed on another agent's verification as my own without
checking its range**, and "verified" meant "verified where it was tried". Production now uses a
closed form instead (exact up to the Student-t CDF, `scipy.special.stdtr`), which agrees with the
oracle to 5.6e-12 where the oracle is trustworthy and costs 15us against 11-24ms.

**R119's instruction to re-measure the Monte-Carlo table on the real panel is superseded, not
done.** GJR leaves the table entirely once it is exact, so there is no reader for an SPY figure;
and EGARCH's entries *cannot* be re-measured on-series, because R114 established it has no finite
multi-step mean for an error to be measured against. So the table loses GJR's two rows as
unreachable and keeps EGARCH's two behind an invariant test that no figure outlives the contestant
that needed it. My ruling asked for a measurement that the right fix makes unnecessary.

**R90's "17.7% low at h=66" understates arch's analytic GJR by half.** Against the exact value it
is **38-45% low** on SPY. Not a contradiction — 17.7% was the probe series measured against
simulation, and this is the real panel measured against a closed form — but the honest number is
now available and is much worse, which strengthens rather than weakens R90's original call.

**The geometric sum would have been a silent 51% error, and its guard would never have fired.** I
offered the closed-form geometric series and iteration as alternatives and asked for a ruling.
Iteration wins, and not on elegance: on SPY's GARCH(1,1) fits the geometric form is wrong by
6.0%/25.2%/51.0% at h=5/22/66, because `alpha + beta` arrives as `1 - O(eps)` and
`1 - phi` cancels catastrophically — **and the `phi == 1.0` branch that was supposed to protect it
never fires at `1 - 1e-16`.** R118 records persistence pinned to 1 within 1e-9 at 192 of 250
origins, so the unprotected case is the *modal* one.

**Struck, and the reason is worth more than the claim was.** This paragraph first ended "iteration
matches an 80-bit evaluation to exactly 0.0", repeating the design's own wording. **On this machine
`numpy.longdouble` *is* `float64`** — itemsize 8, eps 2.22e-16, measured — because arm64 macOS has
no 80-bit type, so that comparison was float64 against float64 and established nothing. The
implementer caught it and redid the check with `fractions.Fraction`, i.e. exact rationals, which is
the right instrument and needs no hardware to cooperate. **The conclusion is unchanged and now
actually supported**; only my evidence for it was void. Third entry in this ruling where I restated
a number without checking the instrument that produced it.

**The pattern across all four.** Every one is an assumption of mine that a measurement reversed,
and three of the four were about numerical behaviour I had reasoned about rather than run. R119's
is different and worse: it prescribed work before the fix was designed, so it asked for a
measurement the real fix retires.

## R126 — R123 is fixed, my instruction for fixing it was impossible, and what the old records can say

US-3.8, `0d01a15` plus `6b93422`. **"above the floor" now appears zero times in the real output**,
verified by rendering the committed ledgers rather than by reading the diff.

### The instruction I gave was impossible, and the answer is better than it

I told the implementer to **import** the correct three-way wording from `compare/__init__.py`
rather than paraphrase it. It cannot: `test_the_leaderboard_imports_no_statistics` asserts
`not name.startswith("volab.compare")` for every leaderboard module, which is the structural half
of "the leaderboard computes nothing of its own" — and importing `compare.sink` also costs ~1.5s,
because that package's `__init__` loads `arch`. The line-278 text is also an inline disjunction
inside `describe()`, not three separable sentences.

What it built instead: **the sink stores the sentence, and the leaderboard prints it verbatim.**
That is strictly better than what I asked for. Importing would have left the leaderboard choosing
*which* sentence to print, which is a derivation; storing leaves it choosing nothing. The same
mechanism now carries the DM and SPA verdicts, so the leaderboard holds no verdict wording at all.
**I prescribed a mechanism that the module's own contract forbade** — the second time in two
rulings that I specified a fix before checking it was reachable (R125 records R119's).

The three sentences, each stored, each asserted per cause by
`test_each_withheld_reason_states_its_own_cause_and_only_that_one`:

* above the ceiling — `<name>'s Monte Carlo error is above the 1.5% ceiling`
* measured elsewhere — `... was measured on another series (<source>), not on <symbol> h<horizon>,
  so it cannot authorise a claim whatever its value`
* no figure — `... was not measured, so no precision is established`

The ceiling sentence deliberately **omits the figure**: the implementer's first version printed
`3.273%` inside the reason, and the existing `test_a_stored_error_with_no_source_is_not_printed_bare`
caught it as an R102 violation — a bare figure with no source beside it. An old witness catching a
new line is the ratchet working.

### The decision it escalated: the 28 old records keep a fallback, and that is right

The committed comparison records predate the stored fields, so they print
`<name> has no claim, and this record stores no reason` — **204 output lines** (222 occurrences),
78 on `37c74e7` and 126 on `0dd0e60`. *(This read "162 lines" until R129's audit counted it; the
figure was wrong when written, not drift since. All 204 are on superseded runs and the clean runs
show none, so the acceptance below is unaffected and only the number was wrong.)* The
alternative was to let the leaderboard classify from the stored source and value, which is exactly
the derivation its contract forbids and `test_the_leaderboard_imports_no_statistics` structurally
prevents. Backfilling an append-only ledger is worse than either.

**Accepted.** The sentence is *true* of those records: they really do not store a reason. So the
output went from a false statement to an honest absence, which is the whole of R123's complaint —
but it is worth being exact about what is not yet fixed: **the recorded run now prints no true
cause either.** The true causes arrive when US-3.12 re-records against the exact GJR, which R122
already committed to for an unrelated reason. Nothing further is owed here; the fallback expires
on its own.

### Item 4, and why the implementer was right to stop

The CLI now reports an unreadable ledger and returns 1 instead of letting a traceback out, with
`render` still **raising** rather than skipping — refuse-never-skip is unchanged, only the
presentation. It sits in `leaderboard/__main__.py`, outside that implementer's file set, so it
stopped and handed over the patch. It also **refused the shortcut** of folding the error into
`(lines, False)`, which would have overloaded what "consistent" means and falsified the module's
own docstring. Correct on both counts.

### Carried, and not this story's

`compare/__init__.py`'s `describe()` should take its sentences from the sink rather than keep the
inline disjunction, and `analyse()` should emit the *cause* rather than leave the sink to restate
its three-way rule — `test_the_stored_reasons_are_exactly_the_unclaimed_contestants` exists
precisely because the rule now lives in two places, and a drift witness is a guard over a
duplication, not a cure for one. Deferred rather than done because an implementer is in flight in
the same package.

## R127 — GJR forecasts exactly, R91's table is amended, and the ban finally got its transitive witness

US-3.11: `2e8b023`, `39e644c`, `75ae610`, `fb58468`, `a21bb8c`. Verified here rather than relayed —
`gjr11_skewt` reports `method='exact'`, `monte_carlo_error(MULTISTEP, h)` is `{}` at h=5, 22 and 66,
and `monte_carlo_error(SHOOTOUT, 1)` carries EGARCH alone. **GJR is out of the Monte-Carlo
apparatus entirely**, which is what took it out of R115.

**R91's recorded table is amended**, as R124 authorised: `("GARCH", 1, "simulation")` becomes
`("GARCH", 1, "exact")`. R91 named three specifications and their methods; the specifications are
unchanged and one method is not. **GJR is exact, GARCH(1,1) stays analytic, EGARCH stays
simulated.** The AST literal-count test is gone, replaced by the full
`{GARCH, EGARCH} x o x get_args(Method)` product asserting that **exactly four pairings
construct** — stronger than the design's three examples, and it covers EGARCH, which the literal
count never did. Exhaustiveness was verified by planting a fourth `Method` member and confirming
`mypy` rejects it at `assert_never`.

**`75ae610` is the boundary sha** — the commit at which GJR becomes exact. R124's provenance
decision rests on exactly that, so it is recorded here rather than left to be rediscovered from the
log.

### Three things I said that the code corrected

**The probe would have crashed, not silently lied.** I told the implementer — and the user — that
the old probe would swallow a construction refusal into `except ValueError` and print "analytic
unavailable" while exiting 0. Read against the real source at `cd95c6d`: the loop runs over all
three specs and calls `_forecast(..., method="simulation")` **outside** the try, so the illegal
`("GARCH", 0, "simulation")` raises on the first spec and the probe dies before reaching the
`except` at all. The silent-deletion mechanism was real for the analytic call in isolation; the
crash precedes it. **And the crash exists only because the design made `o=0` simulation illegal**,
which the old probe relied on — so a looser legal set would have produced exactly the silent
failure I described. I was right about the mechanism and wrong about which failure fires first, and
the implementer's new tests are red against the old probe 4 ways out of 5.

**M18's fixture did not reproduce**, and the replacement is stronger: `lambda_hat` -0.536 against
the design's -0.532, `kappa` 0.637 against 0.629, and a `phi` gap of 0.033 against 0.0214 — so the
`kappa = 1/2` forecast is 6.2% low at h=5 where the design measured 3.9%. The design omitted its
seed and draw details. The tolerance rests on the *measured* six-seed spread (0.27%), not on a
theoretical sd, and a test asserts the fixture's own skew and persistence gap so a library change
that weakens it fails by name rather than by passing more easily.

**`lambda = +-1` has no degenerate value.** The design implied `kappa` collapses to 0 or 1 there.
It does not: the shift `a` leaves mass on both sides of `z = 0`, and `kappa` is about 0.36 at the
corners. The test asserts finiteness, the whole-line identities and the mirror identity instead of
a number.

### R82's convention finally has the witness it claimed twice

The per-file numeric scan reads one module's AST, so it catches a sealed module importing numpy and
**misses a sealed module importing a first-party module that imports numpy**. The 2026-10-28 design
leaned on that gap knowingly — it put the scipy code in `models/garch.py` rather than a new
`models/skewt.py` partly *because* a new scipy-importing module would open a surface the scan could
not see — and specified the hardening in the same breath. **The design shipped and the hardening did
not**, which is R82's own failure repeating: a ruling that keeps a convention rather than a witness,
for the third time in this project.

Built here, since `tests/test_import_discipline.py` was in no implementer's file set:
`test_the_point_in_time_path_reaches_no_numpy_or_scipy_even_indirectly`, a breadth-first walk of
the first-party import graph from every sealed module, reporting the shortest chain. **It passes
today for a real reason**: only `models/garch.py`, `compare/dm.py` and `compare/multiple.py` import
numeric libraries anywhere in `src`, and the sealed path imports `models.base`, never
`models.garch`. So it is a ratchet over a property the tree already has — the only kind worth
adding before someone breaks it.

Two guards against it passing emptily, because a transitive scan that follows no edge *is* the
per-file scan: `test_the_transitive_scan_walks_more_than_one_hop` asserts the walk leaves the
sealed set (it reaches `config`, `errors`, `paths`, `sessions`, `identity` and `ingest.curate`,
none of them sealed), and `test_a_planted_first_party_edge_to_numpy_is_caught` plants a **two-hop**
chain — `models.base` -> `models.skewt` -> `models.quadrature` — which is the exact shape the design
feared, and checks that the same graph with `import math` in place of numpy is *not* reported.
`ALLOWED` is untouched and still `{"store.py"}`; it is the Parquet exemption and was never the
numeric one, so `store.py` is in this graph like everything else.

### Carried

`tests/model_conformance.py` and the sealed-set hardening aside, the probe's figures are
**discharged for the mechanism, not the numbers**: every block regenerates on the synthetic probe
series, and no committed probe reads market data, so the design's SPY figures (exact/analytic
1.61-1.81 at h=66, sim/exact 0.996-1.041) remain unregenerable. `garch.py` therefore still says
"17.7% low at h=66" and **not** the 38-45% SPY figure from R125, deliberately: R32 wants a cited
figure to have a probe that prints it, and that one has none. Until a market-data probe exists the
SPY numbers live in the decisions log as measurements, not as quotable project figures.

## R128 — the clean recorded run, and what the exact GJR actually changed

`4865619` (part 1, h=1) and `6e0da08` (part 2, h=5/22/66). Gates green at 1357 tests before either
part started. **Both parts ran on a clean tree** — part 1 at `234b85d`, part 2 at `4865619` — so
the run carries **zero dirty flags**, and the driver committed part 1's ledger before part 2 began
precisely so that part 1's own appends could not dirty part 2's R70 state. That was the mechanism
behind R122's blemish and it is now closed. Ledger 264 -> 306 -> **414**; comparisons 28 -> 52.

**The truncation-equivalence report was captured this time**, which R122 recorded as lost: twelve
reports, `251 of 251`, `250 of 250`, `247 of 247` and `238 of 238` origins identical, three symbols
by four horizons, zero disagreements. The driver tees every stage with `pipefail` and `|| exit <n>`
per stage rather than summarising, because four separate wrappers of mine in this session threw
evidence away.

### The acceptance check, which is the real result

The design asked that **every non-GJR value come out bit-identical**. Of 150 overlapping
configurations, **132 are bit-identical and 18 changed**, and the decomposition is exact: 126
non-GJR configurations, plus GJR's 6 at h=1, leaving all 18 of GJR's h>=2 configurations. Nothing
else moved. **Origin counts are unchanged at every configuration**, so the exact method refuses no
origin the simulation answered — the `Evaluation.comparable()` reasoning that R121 supported
empirically now has a second witness.

**GJR at h=1 came out bit-identical**, although the implementer measured a 1-ulp difference at one
of two probed origins. A 1-ulp change in one of 251 forecasts moves the mean by ~1e-16 relative,
which lands on the same double. Worth stating because it looks like the h=1 path did not change
when in fact it did and the aggregate could not tell.

| symbol | h=5 MSE | h=22 MSE | **h=66 MSE** |
|---|---|---|---|
| SPY | +0.011% | -0.413% | **+8.84%** |
| QQQ | +0.172% | +0.315% | **+4.40%** |
| IWM | +0.019% | +0.178% | -0.693% |

**SPY's +8.84% is the design's predicted 8.8% deficit, confirmed on the real panel**, and it is a
*worse* loss — R120 said correcting the estimator would hurt GJR rather than help it, and it did.
IWM moves least and in the other direction, consistent with R118: it is the mild symbol, explosive
at only 109 of 238 origins with `psi <= 1.135`.

### The payoff, and a sign error of mine

**GJR's h=5 QLIKE lead now carries a claim.** It leads SPY at -6.6611 and sits in the **10% model
confidence set with no withholding and no Monte-Carlo text at all**. That was impossible while it
simulated: R115 withheld it at every horizon, h=5 included, which is what R120 identified as the
registration blocker. The blocker is closed.

**R120's refusal of the h=66 QLIKE ordering is superseded, and I got the direction wrong when I
first read the result.** I said the exact value moved GJR *away* from `har_levels_mz`. It moved
**closer**: -3.52307 -> -3.51604, less negative and so worse, narrowing the margin from 0.01478 to
**0.00775**. The rank holds — GJR is still 4th — but the gap halved rather than widened.

What retires the refusal is not the gap's size but its premise. R120 refused the ordering because a
0.0148 margin sat only twice an estimator's *systematic deficit*, which is a median shift and does
not average away. **With GJR exact there is no deficit**, so 0.00775 is a genuine difference between
two exactly computed numbers — narrower than the gap that was refused, and reportable where that
one was not. No significance attaches either way: h=66 refuses all of it on `n_eff = 16.8`.

### What the output no longer says

At every horizon above 1 the leaderboard now prints **no Monte-Carlo line at all**, because nothing
in `phase2-shootout-multistep` simulates — `monte_carlo_error(MULTISTEP, h)` is `{}`. The withheld
reasons on those records are only `n_eff` thinness and columns disagreeing at 5%.

**That was overstated when first written, and R129's audit caught it.** It said the new records'
reasons are "only ever" those two. True of the h>=2 half; **false of the h=1 half of the same
recorded run**, where `234b85d` carries 48 reasons reading `egarch11_skewt's Monte Carlo error was
not measured, so no precision is established`. That sentence is literally true of the record
(`monte_carlo_error: {"egarch11_skewt": None}`) and is one of R126's three real causes, so nothing
false is printed — but "only ever" was wrong, and the honest statement is that **three** causes
appear across the clean run, not two. A nuance worth keeping beside it: EGARCH's h=1 Monte-Carlo
error is in fact identically zero, since the forecast takes no expectation, so "not measured" is
accurate about the record while the *operative* reason is R124's refusal to make it exact. R126's fallback text (`this record stores no reason`) now appears **only on the
superseded records**, which is exactly the expiry R126 predicted rather than a debt anyone has to
pay.

Pre-registration sections 1, 2 and 4 are updated: R115's clause now binds `egarch11_skewt` at h=1
alone, section 4 carries the ledger's history to 414 with the dirty-flag blemish disclosed, and the
unordered-pair rule is kept while binding nothing at h>=2.

## R129 — the delivery audit, and six things it found that I had wrong

US-3.6. The auditor confirmed five of the six claims I put to it with measured numbers, changed
nothing in the tree, and found more than I expected. **Every finding below was verified here before
being acted on.** What it confirmed, for the record: the acceptance check (132 bit-identical, 18
changed, all GJR at h>=2, every other field identical on all 150), the headline (414 lines, 108
dirty and all of them `0dd0e60`, zero on the clean run), R114 in practice, R121's 38-of-38, the
gates (1357 passed), and every R128 figure including the 0.014779 -> 0.007751 margin.

### The transitive ban had two gaps, and its docstring claimed one of them did not exist

The worst finding, because it is a false claim inside a witness I wrote three rulings ago.

**Parent-package `__init__` edges were never added.** `_first_party_imports` added the parent only
for `from volab.models import base`. Python runs `volab/__init__.py` and `volab/models/__init__.py`
whenever anything imports `volab.models.base`, whatever the spelling — and the scan was reading the
names written down rather than the packages they imply. Measured here before fixing:
`_first_party_imports("from volab.models.base import X")` returned
`['volab.models.base', 'volab.models.base.X']` with **no `volab.models`**, and `volab.models` and
`volab.ingest` were **absent from the reachable set entirely**. So numpy placed in either
package's `__init__` was invisible. **The function's own docstring asserted the edge was there.**

**Relative imports were skipped outright** (`not node.level`), excused by a comment that grep finds
no relative import under `src`. True when written, enforced by nothing: `ruff` selects neither
`TID252` nor `ban-relative-imports`, so one appearing would have passed every gate while silently
emptying the scan.

Both fixed, with `_ancestors` and a resolved `package` per module, plus
`test_a_numeric_import_in_a_parent_package_init_is_caught` and
`test_a_relative_import_reaching_numpy_is_caught` (which covers `from . import x`, `from .x import
f` and a level-2 climb). The one-hop guard now also asserts `volab`, `volab.models`, `volab.ingest`
and `volab.features` are all reached. **Verified on the real tree**: `import numpy` planted in
`src/volab/models/__init__.py` now fails with
`walkforward/__init__.py reaches a numeric library through volab.walkforward -> volab.models`,
which the old scan could not see at all.

**`arch` is now a numeric root.** A sealed module doing `import arch` passed the AST scan, because
`NUMERIC_ROOTS` held only numpy and scipy — yet R82 admitted arch on exactly the same terms and it
pulls scipy in behind it. The test names still say "numpy or scipy"; left alone deliberately,
because a renamed test is a citation this project then has to chase, which is finding 6 below.

**Not fixed, and rated correctly as low:** `importlib.import_module("numpy")` and `__import__` are
invisible to any AST scan. That is the technique's limit, not this witness's.

### Three figures in live source that no committed probe prints

R32 requires a cited figure to have something that reproduces it. I had told the auditor the one
figure I knew about was accepted *because* a probe printed it. **It does not**, and I had not run it.

| where | said | the committed probe prints |
|---|---|---|
| `garch.py` method docstring | analytic GJR "17.7% low at h=66 ... on the probe series" | **20.1%** at h=66, 9.4% at h=22 |
| `garch.py` `_path_sum` | geometric form "wrong by up to 51%", "reproduces an 80-bit evaluation exactly" | **44%** at h=22 and **71%** at h=66 at `1 - 2**-52`; iteration within ~1e-15 of **exact rationals** |
| `cohorts.py` docstring | EGARCH h=22 "230.79% on SPY" | nothing; no SPY figure exists |

The 17.7% was the design's own earlier series, and **the decisions log already recorded 20.1%
against it** — so live source contradicted this file. The 51% was R125's SPY figure, which that
same ruling says must stay out of `garch.py` for want of a probe. And "80-bit ... exactly" is the
claim **R125 explicitly struck**, surviving verbatim in the module while the ruling that struck it
sat in the log. All three corrected to what the probe prints, each naming the probe.

The EGARCH SPY figure is deleted rather than replaced: a percentage error against a quantity R114
established **does not exist** reads as an error bar for something that has none, so the argument
it was supporting is now made without it.

### The truncation result had no artifact, so here it is

R128 said twelve reports were captured and gave only the four `N of N` pairs. The log was in the
scratchpad, which is ephemeral, and US-3.7's acceptance wants the console output *in this file* the
way Phase 2's own recorded run quoted it. Quoted now, from the clean run:

```
SPY h1:   truncation equivalence: 251 of 251 origins identical
QQQ h1:   truncation equivalence: 251 of 251 origins identical
IWM h1:   truncation equivalence: 251 of 251 origins identical
SPY h5:   truncation equivalence: 250 of 250 origins identical
SPY h22:  truncation equivalence: 247 of 247 origins identical
SPY h66:  truncation equivalence: 238 of 238 origins identical
QQQ h5:   truncation equivalence: 250 of 250 origins identical
QQQ h22:  truncation equivalence: 247 of 247 origins identical
QQQ h66:  truncation equivalence: 238 of 238 origins identical
IWM h5:   truncation equivalence: 250 of 250 origins identical
IWM h22:  truncation equivalence: 247 of 247 origins identical
IWM h66:  truncation equivalence: 238 of 238 origins identical
```

Each sits beside its companion line — for SPY h1,
`251 build(s) and 251 training set(s), every one stopping at its own origin` — which is what makes
the count mean something rather than being a tally of nothing. Twelve configurations, zero
disagreements. The standalone exhaustive sweep over the dev window is the stronger witness and
reads `0 of 2245 cutoffs disagree`.

### My two wrong numbers, corrected in place

R126 said the fallback reason prints on **162** lines. It is **204** output lines, 78 on `37c74e7`
and 126 on `0dd0e60`. Wrong when written, not drift: rendering R126's own commit gives 204 too.

R128 said the clean records' withheld reasons are "only ever" `n_eff` thinness and column
disagreement. True at h>=2, **false at h=1**, where 48 reasons read "Monte Carlo error was not
measured". Three causes appear across the run, not two. Nothing false is printed — the sentence is
accurate about the record — but my summary of it was.

### The board was stale on four stories

US-3.5 and US-3.3c were **done and unmarked** (`f1df79f`, `d0f1902`), with their witnesses present
and passing. US-3.13 was marked done with no sha, against this board's own rule that a story closes
when a commit exists; it is `234b85d`. And **US-3.7 was never closed or superseded**, still naming
an acceptance check — 168 trials — that R114 made unreachable, and carrying "from 72 to 240" and
"doubles" where the ledger is 414. Five Risks entries that later rulings overturned are also still
written as live. All corrected.

**This is the third time a review seat has caught a witness of mine claiming something it did not
do** (R79, R82, and now the parent-package edge), and the first time it caught live source
contradicting this very file. The pattern is narrower than "I don't test enough": in all three the
test existed and passed, and the *docstring* described a stronger test than the code performed.

## R130 — the holdout pre-registration is REGISTERED

On the user's instruction, 2026-10-28. This ruling exists because the registered document **cannot
record its own identity**: a file cannot carry the hash of itself and a commit cannot name its own
sha. Earlier in this project an amend invalidated a sha that had just been written into the board,
which is the same mistake one layer down. So the identity lives here, in the commit *after* the one
it describes.

| | |
|---|---|
| registering commit | **`65617eea2e1c95b8cf0b90555810bf64944c9153`** |
| file | `docs/holdout-preregistration.md` |
| git blob | `4b5c04e38280cbff591d353b1ccf8a91cb3d517b` |
| `sha256` of content | **`c0a095ab3a00711fd435eded709bde4dcdf9f1ab87ab016e92dc7c5cd7323779`** |
| size | 11,380 bytes, 193 lines |
| tree at registration | **clean** — `git status --short` empty before and after |
| `data/trials.jsonl` | **414 lines** |
| `data/comparisons.jsonl` | **52 records** |

Both hashes are recorded because either can verify the document and they fail differently: the
blob sha is what `git rev-parse HEAD:docs/holdout-preregistration.md` returns and ties the content
to *this repository's* history, while the `sha256` survives being copied out of git entirely. The
reveal compares the file it reads against these and refuses on a mismatch.

**Registered while the window was still open, and that is the whole of its force.** `holdout_end`
is **2026-10-30**, two sessions after this commit. So every commitment in the document — the
contestant sets, the losses, the `n_eff` refusals, the champion rule, the definition of failure —
was fixed before the window it describes had finished, let alone been read. A pre-registration
filed after the fact binds only on the author's word that nobody looked; this one binds on a
timestamp that precedes the data.

**What backs the claim that nothing has been read.** `load_panel` is the only read path and
requires an explicit `end`; `allow_holdout` has never been set in this repository and
`VOLAB_HOLDOUT_UNLOCK` has never been read; an autouse fixture fails any unmarked test that
reaches past `dev_end`; and every recorded run's last origin is on or before 2025-12-24, visible in
the ledger's own `last_origin` field. The seal is a mechanism, not an assurance.

**What is now fixed and cannot be revisited without a new registration:** seven contestants at
h=1 and six above it (R114), QLIKE and MSE on the variance scale, DM with the Harvey correction
only where `n_eff >= 20`, MCS only where `n_eff >= 2/alpha`, **no significance of any kind at h=22
or h=66**, no confidence set at all at h=66, and the champion defined as the lowest-QLIKE member
of the 10% set at h=1 on SPY — with **no champion** a permitted outcome if that set is refused for
thinness. Success explicitly does not require beating HAR; what would make the run a failure is a
trial that reached no ledger line, a leakage test that could not be shown failing, or a figure with
no command that reproduces it.

**What remains, and it is the user's to trigger:** the reveal. It cannot run before `holdout_end`
plus each horizon's tail — h=66 needs about three further months of sessions before its last
origin completes — it takes a second trial count and records both plus the delta against the 414
above, and it happens deliberately and once. Changing anything in the registered document means a
new registration and a new look, **both counted**.

## R131 — US-3.9's design, and the collision already sitting in the ledger

Design: `docs/superpowers/specs/2026-10-28-contestant-capability-design.md`, `78b2d61` corrected by
`af8d223`, ratified by reference. 808 lines. Every load-bearing number below was re-measured here
before being accepted.

### The decision: refuse the configuration, do not drop the contestant

The board's acceptance check said the harness "drops" a contestant it cannot answer for.
**Amended to refuse**, and the deciding reason is not taste — it is already in the committed data:

```
phase2-shootout  h=5   14 lines  SPY  all seven contestants incl. egarch11_skewt
phase2-shootout  h=22  14 lines  SPY  all seven contestants incl. egarch11_skewt
```

Those are the crashed run's (R112). A drop design would write **six**-contestant lines under that
*same cohort name* at that *same horizon*, so the ledger would hold two different contestant sets
under one name at one horizon with nothing to distinguish them, and a reader comparing the two
would be comparing different things. **The contradiction is latent in the file today**; a drop
design would realise it on its first run. Refusal cannot.

`evaluate` raises `ValueError`, joining its existing reserved-name guard; the CLI pre-checks and
exits 1 printing the same refusal string. The inflexibility is paid down by *deriving* the cohort's
horizons, deriving `MULTISTEP`'s membership, and naming the cohort to run instead.

### Form 2 measured: what a VIX1D contestant would cost everyone else

Verified independently here, straight from the curated panel:

| series | rows | first session |
|---|---|---|
| VIX, VIX9D, VVIX | 2,389 each | 2016-07-01 |
| **VIX1D** | **912** | **2022-05-13** |

`Evaluation.comparable()` intersects *defined* origins across all contestants, so a VIX1D
contestant removes origins from **every** contestant's loss. Measured over the shootout window at
stride 5, **with the target lag**: **119 of 251 origins at h=1 (47.4%)**, 120 of 250 at h=5, 123 of
247 at h=22, and **132 of 238 (55.5%) at h=66**. Against R121's measured **zero** for EGARCH's
removal. So this is not the same scale of effect at all, and the output would look entirely
ordinary.

The chosen answer declares the required feature **columns** and lets the harness measure coverage
per run — no declared start date, because that is a second copy of a measurable fact and R119
already recorded what happens to those. A **leading** boundary refuses; an **interior** gap stays a
recorded per-origin skip. That split is empirical: `iv_h1`/`iv_var_h1` have 912 non-null values and
**zero** interior nulls, while FRED's `dgs10`/`t10y2y` have 17 interior nulls of which **3 of 247
land on shootout origins** — bond holidays with XNYS open. Refusing on those would refuse the run
for three missing days.

### Two premises I gave the architect that were wrong

**"It touches `Evaluation.contestants`, `trials()` and the comparison record's shape."** Under
refusal it touches **none of them**, and no ledger schema change is proposed. That is the main
thing refusal buys and I had it backwards — I briefed the blast radius as larger than the design
needed, which would have made the heavier option look cheaper than it was.

**`comparisons.jsonl` already carries `contestants`.** Verified: the first record lists all seven.
So the comparison record already self-describes its contestant set, and part of what I thought
needed building exists.

### The CLI default changes, deliberately

`python -m volab.compare run` goes from "seven contestants at all four horizons" to the cohort's
own horizons with the reason printed. **Accepted**, because the present default *is* the trap this
story closes — an operator should not have to pass a flag to get a meaningful run, and the old
behaviour's output looked ordinary while being meaningless. Recorded as a deliberate change to what
a documented command does.

### Sequencing

**US-3.9a** (declaration, both refusals, conformance clauses 9 and 10) then **US-3.9b** (retire the
Monte-Carlo table). They **serialise**: both touch `models/base.py` and `compare/cohorts.py`, so
they cannot be parallel implementers. 3.9a is in flight.

EGARCH's two remaining table entries are to be **deleted rather than migrated**, because the
horizon guard makes them unreachable by construction — the same ground R125 removed GJR's rows on.

### What the witnesses can and cannot do, stated rather than discovered later

`max_horizon` **cannot be checked behaviourally.** EGARCH at h=22 returns a finite,
ordinary-looking `1.70e+12`; that is exactly why R114 demanded an a-priori refusal. So the
witnesses enforce "the declaration cannot be omitted and cannot be expressed wrongly", not "the
harness notices a bad forecast". `requires` is checkable in one direction only, and the
undeclared-column residual is **today's behaviour unchanged**. A design that claimed more here
would be claiming a witness it does not have, which this project has now done three times (R79,
R82, R129).

### A hazard of two agents in one checkout, which our own rules do not cover

The architect observed `src/volab/models/recalibrated.py` **with the Mincer-Zarnowitz recalibration
stripped** (`value=outer.value`) partway through its session, ran `tests/test_recalibrated.py`, and
saw **3 failures including the in-sample red case**. Nothing was wrong: that was US-3.14's
implementer running a planted mutation, and it reverted. The tree is clean and the suite is at
1368.

But the rule we work to is *"never two implementers writing the same files"*, and that rule was
**satisfied** — one owned `tests/test_recalibrated.py`, the other owned only `docs/`. It does not
cover **mutation testing**, which temporarily breaks a file in `src/` that the planter does not own
and every concurrent agent can see. An agent that happened to run the gates in that window would
report a red tree and could reasonably start debugging someone else's plant.

**The rule needs a clause: a plant-and-revert needs exclusive `src/`, or it belongs in a scratch
copy.** The architect handled it correctly — it did not revert what it had not written, and it
re-checked `git log -1` before each amend so it never amended across another agent's commit.

## R132 — US-3.9a: the trap is closed, and five departures worth keeping

`4fcb307`, `18f05f2`, `d51dfb7`, `04479bb`, `f15da4e`, `a2eb1cb`. **1424 tests**, from 1368. Gates
green. Verified here rather than relayed: ledgers unchanged at **414 / 52**, `ALLOWED` still
`{"store.py"}`, `tests/test_import_discipline.py` untouched, and **the registered read still
compares seven contestants at h=1 and six above** — which was the one thing that could not move.

**The trap, closed.** `SHOOTOUT.horizons((1, 5, 22, 66))` **derives** to `(1,)` and `MULTISTEP`'s
to all four; `MULTISTEP`'s membership is derived from the declarations rather than filtered by
name. The refusal reads:

```
cohort 'phase2-shootout' cannot be evaluated at h=22: 'egarch11_skewt' answers h<=1.
Its contestant set is part of the number, so a member is never dropped silently
```

That second sentence is the ruling in the error message, which is where it is most use. R114's
exclusion is now enforced by the harness rather than remembered by the operator.

### Departures accepted, each because the code knew something the design did not

**Conformance clause 9 is opt-in across horizons** (`clause_9(model, toy, also=())`,
`conformance_suite(..., also_at=())`). The design had it rebuild the toy at every configured
horizon, which cannot be done generically: the toy has 146 sessions against a 66-session target
lag, so **no** contestant answers an origin at h=66 there, and `har_levels_mz` answers nothing on a
fresh stride-6 toy at any horizon because it needs 40 calibration pairs. Both are
**indistinguishable from "declares a horizon it cannot answer"** — the first implementation failed
three recalibrated tests for exactly that reason. So the toy's own horizon is always checked and
`also_at` supplies toys the caller vouches for. Left uncovered and stated: `har_levels_mz`'s
unrestricted claim above its toy horizon, and h=66 for everyone.

**The driver had an empty-window path the design missed.** `run_configuration` returns *before*
`evaluate` when no origin is scoreable, appending counted NaN lines under the cohort's name — so
the `evaluate` guard alone would never have fired there. The same refusal now sits at the top of
the driver, with its own witness.

**`Measurement` and `Sampling` shipped in 3.9a** rather than 3.9b, because `GarchModel.domain`
builds a `Sampling` and the h=1 EGARCH witness reads `domain.sampling`. They have unit tests of
their own, so **3.9b must not re-add that core** — only W13-W15 remain its.

**The two refusals keep different exit codes, deliberately.** The horizon refusal exits 1 (a CLI
guard before any work); the coverage refusal reuses `ConfigurationOutcome.refused`, which the CLI
already turns into exit 2. Two codes, two meanings, and the second was reused rather than invented.
Unifying them would erase a distinction the CLI already made.

**The thin-skip reason now names the count that fell short**, but only when `cohort.requires` is
non-empty: "only 1500 rows" against a minimum of 250 contradicts itself when the shortfall is in a
column. The old wording is asserted unchanged for cohorts requiring nothing.

### Two gaps sent back, both the implementer's own findings

**`RecalibratedHar.domain` returns `UNRESTRICTED` and can launder a base's bound.** It flagged this,
left it per the design, and named delegation as the safe form. **I am overruling the design**:
delegation to `self.base.domain` is not merely safer but the only correct answer, because the
recalibration consumes the base's forecast and therefore cannot answer a horizon the base cannot.
No base declares a bound today, which is exactly when it is cheap.

**`coverage_refusal` has no real-store witness** — the design's VIX1D figures are exercised only
against the toy. Sent back with the measured numbers to assert against the mechanism rather than
pin a count, since pinning today's 119 would pin the data rather than the behaviour (R72, R78, R99).

### Two smaller notes

**The attribution line differs on these six commits** (`Claude Sonnet 5.5`, not the `Claude Opus 5`
I specified) because that is the model that actually ran. **Left alone**: it is more accurate than
the line I gave, and rewriting six commits to make it less true is the wrong trade. Mixed lines in
the log are the honest record of who wrote what.

**The compare CLI's bare-command output changed** and the design's "docs unchanged" note did not
cover it: it now prints `--horizon not given: running h=1 ... left out: <refusal>` when the default
narrows a run. That is the point — an operator should see why they got one horizon instead of four
— but it is a change to what a documented command prints, recorded rather than discovered.

## R133 — US-3.9a's two gaps closed, and a third category of figure the test kit will need

`8c8b2a8` and `0cfe2e4`. **1442 tests**, from 1424. Ledgers unchanged at 414 / 52, tree clean.
Verified here: `har_levels_mz().domain` **is** its base's domain and is `UNRESTRICTED` today, so
nothing in the registered read moves; the `UNRESTRICTED` import is gone from `recalibrated.py`.

**The delegation went further than I asked, correctly.** I said to inherit the bound; it inherits
the whole domain — `max_horizon`, `requires` **and `sampling`** — on the argument that a
recalibration of a *sampled* forecast is still a Monte-Carlo estimate, so `sampling` travels by the
same reasoning as the bound. That is right and I had not said it. Witnessed with a deliberately
declaring base: a base bounded at h=5 gives a wrapper whose derived horizons are `(1, 5)` and whose
refusal at h=22 names `declaring_base_mz`; a base requiring `iv_h1` gives a wrapper that requires
it. Both ran red against the old `UNRESTRICTED` return before the fix.

**The real-store witness uses an independent oracle**, which is what makes it worth having: a
cumulative scan from the *definition* — the 250th row non-null in both the HAR floor and `iv_h1`,
plus the session its target completes on — against `coverage_refusal`, which rebuilds a training
frame per origin. Two paths, one answer. It also caught the planted target-lag defect at h=5, where
h=1's single-session lag is too small to move the named origin.

### The finding that changes the test kit's design

One of its tests, `test_the_designs_measured_figures_still_hold`, **pins the data**: (251, 119),
(250, 120), (247, 123), (238, 132) — the VIX1D coverage figures R131 recorded. It could not avoid
that honestly, flagged it, kept it separate from the five mechanism tests that will not move with
the data, and documented in its docstring that **a store rebuild should make it fail and be
re-measured, not edited to pass.**

That is a **third category of figure** the planned merge-gate kit did not have:

1. **A figure in prose** — needs an anchor and a probe. The 28 in `src/`.
2. **A figure in a dated record** — `docs/` decisions logs. Must *not* be pinned; re-deriving it
   would overwrite history.
3. **A figure pinned inside a test** — already executable and self-proving, but with semantics
   unlike any ordinary test: it is *expected* to fail when the data legitimately changes, and the
   correct response is re-measurement, not editing the literal.

Category 3 is where the kit could be defeated most easily, and by exactly the move that let `17.7%`
survive two sprints: **making the build green by editing the number.** A test that pins a
measurement should therefore take its expected values *from the inventory* rather than from inline
literals, so changing one is a diff in a reviewed file with a stated reason rather than a quiet edit
in a test body. The gate cannot stop a determined edit; it can make the edit visible and require it
to say why.

## R134 — US-3.9b: the Monte-Carlo table is retired, and a latent flaw in R132's own contract

`761cf71`. **1448 tests**, from 1442. Verified here: `MONTE_CARLO_ERROR_PERCENT` and
`MONTE_CARLO_ERROR_SOURCE` are **gone** (zero references), the ledgers are byte-identical at
414 / 52 with md5 `090e37b0…` / `b9f41c26…`, and the registered read is unchanged — the shipped
shootout at h=1 reports `{'egarch11_skewt': None}` against source `'no measurement exists'`, so
EGARCH is withheld on **both** grounds (no figure, and no figure measured on the evaluated series)
with the same stored reason sentence as before.

A contestant's measured error now travels with the contestant: `monte_carlo_error` reads each
member's `domain.sampling` and **refuses** when two members were measured on different series,
naming both. EGARCH's h=22 and h=66 rows are **deleted rather than migrated**, because the horizon
guard makes them unreachable by construction — the same ground R125 removed GJR's rows on.

**The witness that matters is the one that kills the class rather than the instance:**
`test_no_module_level_table_in_cohorts_is_keyed_by_a_contestant_and_a_horizon` asserts no
module-level dict in `cohorts.py` has `(str, int)` keys. The old table's original sin was
**surviving its reader** (R119), and a deleted table can be reinstated by the next person who wants
somewhere convenient to put a number. A test that refuses the *shape* is the only kind that holds.
Its companion, W14's rename case, is what a resurrected name-keyed table would fail: a renamed
contestant carries its figure under the new name.

### The latent flaw, which is R132's and not this story's

`Sampling.__post_init__` requires **one source per contestant**, but `series_source` includes the
horizon — `"SPY h22"`. So **a single contestant can never hold measurements at both h=22 and h=66
on its own series without being refused.** It is latent today only because **zero measurements
ship**: nothing in the repository declares a `Measurement`, so the constraint has never been
exercised.

It is a defect in the contract US-3.9a introduced, not in US-3.9b's use of it, and the implementer
correctly left a `base.py`-level change alone rather than widening its own story. **It will bite
the first time anyone measures one contestant at two horizons**, which is precisely what a Phase 3
IV contestant measured at h=1, h=5 and h=22 would need. Carried, and the Phase 3 session has been
told.

### Two things the design undercounted

**Two display strings change, not one.** With the sentinel in place, `describe` would have printed
"measured on the **no measurement exists**" — a sentence that reads as a series name. Fixed with a
branch on the sentinel. The design's "one display string changes" was wrong, and the CLI's trial
line now reads `[MC error not measured (no measurement exists)]`.

**`None` could not be the empty source.** `analyse` raises on it, so the absence of a measurement
needed a *name* — `NO_MEASUREMENT = "no measurement exists"`, living in `volab.compare` rather than
`cohorts` to avoid an import cycle. That is R119's rule holding in a new place: **no figure at all
must read as "not measured", never as zero and never as precision.**

### Deleted tests, and why deleting them was right

`test_every_monte_carlo_figure_is_reachable` is **gone**, because the staleness class it guarded is
eliminated rather than managed — there is no table for a figure to outlive its reader in. Likewise
`test_the_shipped_source_says_it_is_not_the_run_s_series`, which asserted properties of a constant
that no longer exists; its property is restated against the shipped cohort instead. Deleting a test
whose subject is gone is correct; deleting one whose subject merely moved would not be, and the
shape test above is what guards the difference.

### One honest gap in the planted-defect sweep

The R124 door — EGARCH declared with `Measurement(0.0, "SPY h1", …)`, which would hand the h=1
leader a significance claim — **is** caught, by the source-comparison test and the shipped-cohort
test. But the implementer did not confirm that W16 and the registered-read test fail on it
independently, and said so rather than implying full coverage. Recorded because the first attempt at
that plant failed at import and therefore proved nothing, which is the kind of thing a sweep can
silently miscount.
