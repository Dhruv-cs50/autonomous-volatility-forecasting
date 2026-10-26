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
