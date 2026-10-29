# Phase 2 completion sprint board — the baseline shootout the framework's row asks for

One board per phase; Phase 0/1's is `sprint-board.md` and Phase 2's harness is
`phase2-sprint-board.md`. Tracked in git deliberately: a plan `git clean` erases is not a plan.

**Why this board is not called Phase 3.** The framework's Phase 2 row is *"GARCH(1,1),
GJR/EGARCH with skew-t, HAR, recalibrated HAR; walk-forward; QLIKE/MSE; DM plus MCS"*, exiting on
*"a reproducible leaderboard per horizon, logged in MLflow"*. The repository has the harness and
three contestants; `grep` finds no GARCH, no DM, no MCS and no MLflow anywhere in `src/`. Naming
this Phase 3 would have skipped a row and made the numbering lie.

Sizing is **scope, not wall-clock**: a story is one dispatch of new work, and a sprint closes
before the next opens. Nothing is done on assertion — a story closes when a commit exists, its
tests pass and the gates are green.

## Constraints every story inherits

Each has already cost this project time, so each is stated rather than assumed:

- `uv run --locked …` always. A bare `uv run` re-locks silently; `--frozen` skips comparing the
  lock against `pyproject.toml`.
- **numpy and scipy are allowed only in the model zoo and the comparison layer** (R82). Nothing
  in `features/`, `panel.py`, `store.py` or the walk-forward solver imports them: that path is
  the point-in-time guarantee and stays pure pandas.
- `filterwarnings = ["error"]`, so **a warning is a failing test**. GARCH must fit on returns
  times 100 with the variance divided back by 1e4 (R83, measured). A convergence failure is a
  recorded skip, never a new `filterwarnings` entry.
- `tests/test_import_discipline.py` keeps `ALLOWED` at `{"store.py"}`, and now also scans
  `tools/`. Do not weaken either.
- **Never** set `allow_holdout`, read `VOLAB_HOLDOUT_UNLOCK`, or add `@pytest.mark.holdout`. The
  autouse fixture that fails an unmarked test reading past `dev_end` is a feature.
- Every new model meets the harness's existing standard: per-origin truncation equivalence,
  R78's target-side equivalence, a `RunAudit`-clean fit at every origin, and **every
  configuration counted on the ledger** whether or not it produced a forecast.
- Every leaderboard figure names the committed command that prints it (R32, as `tools/` applies
  it to measurements).
- If git reports `.git/index.lock`, wait and retry rather than deleting it.
- Concurrent implementers commit with an explicit pathspec, and `git add -N` first for new files.

## Sprint 1 — the model zoo and the comparison layer

| Story | Goal | Files | Acceptance | State |
|---|---|---|---|---|
| US-3.1 | Architect the model-zoo interface and the comparison design before any model is written | `docs/superpowers/specs/2026-10-25-phase2-completion-design.md` | one `fit(train) -> forecast(h)` interface the existing three models can be expressed in without changing their numbers; the h-step aggregation defined against the `targets_rv_h*` definition rather than invented; DM's HAC lag and the MCS bootstrap's block length derived from the horizon and the stride-5 origins, not defaulted | done `ce9f781`, `d05f491` — R85-R97, ratified by reference in the decisions log |
| US-3.1b | The shared seam: the `Model`/`Forecaster` interface, the cohort, and the one edit that can move a recorded number | `src/volab/models/{__init__,base}.py`, `src/volab/walkforward/__init__.py`, `src/volab/walkforward/__main__.py`, `src/volab/errors.py`, `tests/model_conformance.py`, `tests/test_model_interface.py`, `tests/goldens/baseline_toy_forecasts.json` | the three baseline models are expressed through the interface with **byte-identical per-origin forecasts**, against goldens captured *before* `evaluate` is touched; `MODELS` frozen and `Evaluation.contestants` added so appending a contestant cannot shrink the baseline's comparable set; `"phase2-baseline"` reserved so a shootout cannot be labelled as the recorded run | done `2dbd454` + coordinator follow-up — all six recorded figures unmoved, 787 tests; the two defects it raised fixed under R98 and R99 |
| US-3.2 | GARCH(1,1) and GJR/EGARCH with skew-t errors, behind that interface | `src/volab/models/garch.py`, `tests/test_garch.py` | fits warning-free under `filterwarnings=error` via R83's rescaling; a non-converging origin is a recorded skip; h-step forecasts on the variance scale the losses use | done `4618ba8`, `a2179dd` — 45 tests, six planted defects each caught; found R90's seeding inert and R91's filter overridden by arch (R100, R101, R102) |
| US-3.3 | Recalibrated HAR — Mincer-Zarnowitz, as the framework defines it at line 27 | `src/volab/models/recalibrated.py`, `tests/test_recalibrated.py` | the MZ regression is fitted on **training-window** forecasts only, so recalibration cannot see the origin it is scoring; a red case shows an in-sample fit scoring better and being refused | done `0ee890a`, `c1c46a8` — 28 tests, six mutations each caught, the in-sample red case refused; found R96's lattice reading fatal at h>=22 (R103) |
| US-3.4 | Diebold-Mariano, MCS and SPA over every contestant, and the leaderboard per horizon | `src/volab/compare/`, `tests/test_compare.py` | DM carries the Harvey correction and an HAC lag justified by the horizon; MCS membership reported at 10% and 25%; the leaderboard states **how many trials were evaluated** beside every ranking | done `d077416`, `fa78cd8` — 152 tests, 24 planted mutations, DM anchored on `ttest_1samp` at q=0; R107-R110, with two follow-ups in flight |
| US-3.5 | The leaderboard as a committed command, reading what the sink wrote | `src/volab/leaderboard/{__init__,__main__}.py`, `tests/test_leaderboard.py` | `python -m volab.leaderboard` prints, per symbol and horizon, every contestant ranked on each loss with **the trial count beside it**, plus MCS membership and `n_eff`; it **computes nothing of its own**, reading `data/trials.jsonl` for the losses and `data/comparisons.jsonl` for membership, so the leaderboard cannot disagree with what it reports; the headline trial count is every ledger line with the per-cohort breakdown beside it, so a new cohort cannot reset the number (R59); a trial whose tree was dirty is shown as such rather than silently ranked | **done** `f1df79f` — marked late; R129's audit found it closed in git while this board still said todo |
| US-3.3b | Strengthen conformance clause 2 so it catches a model that captured its own target frame | `tests/model_conformance.py`, `tests/test_model_interface.py` | a plant that keeps its own reference to the target frame — the R78 shape — is caught by the **shared** suite rather than by one contestant's own test; the existing contestants still pass unchanged | done `d36d689` — clause 7 wired in with the signature unchanged, all seven contestants pass, plant caught by it and passed by clause 2 (R105) |
| US-3.3c | Make the rebuild factory mandatory, so the captured-copy case is closed for every contestant | `tests/model_conformance.py`, `tests/test_model_interface.py`, `tests/test_garch.py`, `tests/test_recalibrated.py` | `conformance_suite` requires a `rebuild` factory, so no contestant can decline it; a plant that `.copy()`s the target frame at construction is caught; the seven existing contestants pass (trivially, since none takes a frame) | **done** `d0f1902` — marked late (R129). `conformance_suite(model, *, toy, rebuild)` has `rebuild` required and `test_the_conformance_suite_requires_a_rebuild_factory` is its witness |
| US-3.7 | **The recorded shootout run**: every contestant, every symbol, every horizon, against the real store, appended to the ledger | `data/trials.jsonl`, `data/comparisons.jsonl`, `docs/superpowers/phase2-completion-decisions.md` | one run on a clean tree whose sha resolves to the code that produced it; **7 contestants x 3 symbols x 4 horizons x 2 losses = 168 trials, 168 ledger lines**, each carrying its cohort; the per-origin truncation-equivalence by-product reported; the measured Monte-Carlo error beside every simulated figure and `n_eff` beside every MCS one; the run's console output quoted in the decisions log the way Phase 2's own recorded run was | **superseded by US-3.12**, not delivered as written (R129). Its acceptance check — 168 trials — became unreachable the moment R114 cut EGARCH to h=1, making a run 150 lines rather than 168; and its 2.5-hour estimate no longer holds now that GJR is exact and does not simulate. US-3.12 delivered the thing itself: one clean-tree run, `4865619` and `6e0da08`, with the console block quoted in R129 |
| US-3.8 | Leaderboard polish, from US-3.5's own escalations | `src/volab/compare/sink.py`, `src/volab/leaderboard/`, `tests/` | SPA's 5% verdict is **stored** rather than re-derived at the point of display, so the leaderboard keeps computing nothing of its own; the DM verdict wording is imported rather than duplicated; a malformed ledger line prints and exits 1 as `compare`'s CLI does instead of raising a traceback; and `--symbol`/`--horizon` filters exist, because the re-run produced roughly 500 pairwise lines and nobody will read them. **R123 added a third duplication to fix:** `_withheld_reasons` prints "Monte Carlo error is above the floor" for all three causes that withhold a claim, and that sentence is false in *every* instance the recorded run prints — two are unmeasured figures and two are figures *under* the ceiling withheld on source. The correct three-way wording already exists in `compare/__init__.py`; the witness must assert the reason text per cause | **done** `0d01a15`, `6b93422` — "above the floor" now appears zero times in the real output. Importing the wording proved impossible (`test_the_leaderboard_imports_no_statistics` forbids the leaderboard importing `volab.compare`), so the sink stores the sentence and the leaderboard prints it verbatim — stricter than the import I asked for (R126). `--symbol`/`--horizon` filters deliberately not done |
| US-3.9 | **Architect:** a contestant declares its domain | `docs/superpowers/specs/2026-10-28-contestant-capability-design.md` | a design that closes the cohort/horizon trap *and* the origin-set trap Phase 3's first contestant triggers | **done** `78b2d61`, `af8d223` (R131) |
| US-3.9a | A contestant declares its domain, and the harness **refuses** out of it | `src/volab/models/{base,garch}.py`, `src/volab/walkforward/__init__.py`, `src/volab/compare/{cohorts,__main__}.py`, `tests/` | a wrong cohort/horizon pairing is refused with its reason rather than producing an ordinary-looking number, through `evaluate` and through the CLI's exit code; `Cohort.horizons` and `MULTISTEP`'s membership are derived rather than listed | **done** `4fcb307`, `18f05f2`, `d51dfb7`, `04479bb`, `f15da4e`, `a2eb1cb` — 1424 tests, 12 planted defects all caught, five departures accepted (R132). Follow-ups done `8c8b2a8`, `0cfe2e4` — 1442 tests. The delegation inherits the whole domain including `sampling`, and the real-store witness uses an oracle independent of `coverage_refusal` (R133). **Acceptance amended:** this row said the harness *drops* the contestant. It refuses, because `data/trials.jsonl` already holds 14 `phase2-shootout` lines at h=5 and 14 at h=22 with **all seven** contestants, so a drop would write six-contestant lines under the same name at the same horizon (R131) |
| US-3.9b | Retire the Monte-Carlo table | `src/volab/compare/cohorts.py`, `src/volab/models/base.py`, `tests/` | a contestant's measured error travels with the contestant; EGARCH's two entries are **deleted** rather than migrated, since the horizon guard makes them unreachable | **done** `761cf71` — 1448 tests. `monte_carlo_error` reads `domain.sampling` and refuses two members measured on different series. The witness that holds is the one refusing the *shape*: no module-level dict in `cohorts.py` may be keyed `(str, int)`, because a deleted table can be reinstated (R134) |
| US-3.10 | **Architect:** GJR's exact closed-form multistep forecast | `docs/superpowers/specs/2026-10-28-gjr-exact-forecast-design.md` | a design for `m_{k+1} = ω + φ_true·m_k` with `κ` from the fitted skew-t, the seam that selects it (arch knows no `"exact"` method, so it cannot be a third string handed to arch), where the quadrature lives given `models/base.py` is sealed, and the validation strategy — including the symmetric cross-check, where `o=0` has no `κ` and arch's analytic recursion is correct, so the closed form must reproduce it from an independent path | **done** `b2f6b16`, `f9b39a8` — closed form beats quadrature (exact up to the Student-t CDF, 15us against 11-24ms), and it found that the quadrature I supplied as "verified" is wrong below `eta = 4` (R125) |
| US-3.11 | Implement the closed form, retire GJR's simulation | `src/volab/models/garch.py`, `src/volab/compare/cohorts.py`, `tests/` | GJR forecasts exactly at every horizon, leaves R115 entirely, and `MONTE_CARLO_ERROR_PERCENT` loses its GJR rows because `monte_carlo_error()` then keyed off `method == "simulation"` — historical as of US-3.9b, which made it read `domain.sampling` (R134) | **done** `2e8b023`, `39e644c`, `75ae610`, `fb58468`, `a21bb8c` — **`75ae610` is the boundary sha** R124's provenance decision rests on. All five witnesses built, including W2 (arch's analytic GJR *is* the `kappa = 1/2` recursion), which turns R118's root cause into a test. R91's table amended to `("GARCH", 1, "exact")` (R127) |
| US-3.12 | The one clean recorded run | — | **one** shootout on a clean tree with nothing else committing, replacing both the dirty-tree part 2 and GJR's simulated rows in a single pass (R122) | **done** `4865619`, `6e0da08` — zero dirty flags, ledger 264 -> 414, truncation equivalence `N of N` at all twelve configurations, and 132 of 150 overlapping values bit-identical with all 18 changes being GJR at h>=2 (R128) |
| US-3.13 | The transitive numeric ban the GJR design specified and nobody built | `tests/test_import_discipline.py` | the sealed path reaches no numeric library *indirectly* either, with two guards against the walk passing emptily | **done** `234b85d`, corrected in R129 — the first version's walk missed parent-package `__init__` edges *and said in its docstring that it did not*, and skipped relative imports; both closed, `arch` added to `NUMERIC_ROOTS`, and verified by planting numpy in `models/__init__.py` on the real tree |
| US-3.14 | A real-panel witness for `har_levels_mz` at h=22 and h=66 | `tests/test_recalibrated.py` | the shipped 60-point lookback and 40-pair floor are exercised on real data at the horizons the toy cannot reach, with the floor tested at its shipped value on a real origin either side of it | **done** `b04f079` — 8 cases, 11 planted defects run, one honestly reported inert. 1368 tests |
| US-3.6 | `delivery-auditor` over this board against git at the sprint close | — | the board and the repository agree, and the board is corrected where they do not | **done** (R129) — it confirmed five of six claims with measured numbers and found six things wrong, including a witness of mine whose docstring described a stronger test than the code performed. Every finding acted on in `b81c455` |

**US-3.1b reported DONE_WITH_CONCERNS, and the concerns were right.** It kept the design as
written rather than departing from it silently, and raised two things it could not decide alone:
a Protocol defect that would have forbidden frozen dataclasses (now R98), and a pre-existing
ledger over-count it deliberately preserved rather than changing R74's behaviour unasked (now
R99). Both are fixed, each with a red case. Its own refactor held: all six figures R73 records
are unchanged to the digit, `data/trials.jsonl` is still 72 lines, and reverting `comparable` to
read `MODELS` fails nine tests.

**One hand-off for US-3.4.** R97's extraction is partial: `run_configuration` owns the audit
refusal, the per-configuration append, the truncation report and the console lines, but R62
(`--to` past `dev_end`) and R70 (the sha and dirty flag at run start) still live in
`__main__.py`. US-3.4's entry point must **call `run_configuration` rather than reimplement it**,
and keep R62 and R70 where they are. The flush that was the third item on that list is gone (R99).

**US-3.7 was missing from the first version of this board, and the omission is worth recording.**
Stories existed to *build* the contestants, the comparison and the leaderboard command, and none
to **run** the experiment. The framework's exit criterion is a *reproducible* leaderboard per
horizon, and Phase 2's own precedent is its "recorded walk-forward run" section: a named commit,
a clean tree and 72 ledger lines. A leaderboard command with no recorded run behind it reports
nothing, and the phase cannot exit on it. Found by being asked what remained, which is the same
way a delivery audit finds things — by checking the board against the claim rather than against
itself.

**It roughly triples the trial count** — written when a single 168-line run was expected off a
72-line baseline. The ledger is now **414**: 72 baseline, 42 from the crashed run (R112), 150 from
the superseded re-run and 150 from the clean recorded run. That is the number any write-up has to
report. More contestants is more trials; the ledger exists so that is stated rather than hidden.

## Parallelism

US-3.2 and US-3.3 touch disjoint files and may run together **after US-3.1b**, not merely after
the design. The seam is a story rather than a set of stubs the coordinator hand-creates, and the
reason is specific: `walkforward/__init__.py` is the one edit in this phase that can move
`har_semilog`'s recorded `4.295799e-07`, and it needs its goldens captured *before* `evaluate` is
touched. Creating it by hand would do that work with no tests and no golden. US-3.4 needs
contestants to compare, so it serialises after both; `compare/cohorts.py` is the only module
importing both model modules and belongs to US-3.4, so nobody imports a module that does not
exist yet. Never two implementers in one file, and the git index is shared state too: pathspec
commits only, `git add -N` first for new files.

**Two follow-ups sent back to US-3.4 rather than accepted.** The printed `[MC error 3.273%]`
carried no indication that it was measured on the probe's synthetic series rather than on the
symbol being evaluated — beside an SPY result it reads as SPY's figure, which is C1's shape again.
And SPA ran unflagged where DM withholds significance and the MCS refuses at 10% on the same
`n_eff`; SPA bootstraps the same overlapping losses, so the thinness does not disappear because
its null changed.

## Risks and what is carried

- **The phase exits against a modified criterion, deliberately** (R84, resolved). The framework
  says *"logged in MLflow"*; this phase logs to the append-only git-tracked ledger and **refuses
  MLflow for a stated reason** — 3.x needs `pyarrow<26` and relaxing that pin would change the
  engine under `store.py`, on which the byte-exact comparator and therefore the whole
  point-in-time claim sits. The row is **not** recorded as satisfied. Any write-up citing Phase 2
  must say which criterion it met.
- **The semi-log specification still fits a log scale while the losses are on the variance
  scale** (R73), and `har_levels` can forecast a non-positive variance, which drops those origins
  from QLIKE for every model. Adding GARCH does not change either; it adds contestants that have
  the same property.
- **The 200-session holdout is thin at the 22- and 66-session horizons**, unchanged by anything
  here. Phase 6 remains the only confirmatory read.
- **A cross-sectional peek stays unreachable rather than tested**, inherited from Phase 1.
- **More contestants is more trials.** The ledger exists so the count is reported rather than
  hidden, and this phase roughly doubles it. That is the honest cost of a shootout and belongs in
  the headline, not a footnote.
- ~~**The shootout costs about 2.5 hours, not about 15 minutes**~~ **(R90, R93; overtaken by R127).** GJR no longer simulates, so the h=66 cell that dominated the cost is gone and the clean recorded run's two parts took minutes. The reasoning below is kept because it is why GJR was *not* left on arch's analytic recursion, which is still the right call: GJR and EGARCH have no
  usable analytic multistep forecast — `arch`'s is **17.7% low** at h=66 under skew-t, measured
  against 200,000 paths — so they simulate, and S=10,000 carries a Monte-Carlo spread of
  0.27-1.40%, which is inside the very band R72 set as this project's floor for a believable
  difference. S=100,000 buys 0.19-0.29% at 0.76-2.91s per call. The alternative was a contestant
  biased by more than the leak this project exists to catch.
- **h=66 cannot support an MCS at 10% on this window** (R94). With `q = ⌊(h−1)/s⌋ = 13`,
  `n_eff = n/(q+1) ≈ 17`, under the 20 that 10% needs. It reports at 25% only, with `n_eff`
  printed beside it and the refusal stated. **No stride rescues this** — `n_eff ≈ W/h` is
  stride-invariant for `h ≥ s`, so stride 1 gives 19 against stride 5's 17; only a longer window
  would, and the full dev window reaches about 30.
- **Adding contestants will move the baseline models' losses**, and that is `comparable` working
  rather than a regression (R56). Wherever a new contestant refuses an origin, the intersected
  set shrinks for every model on that loss. This is why `Trial.cohort` is required, why absence
  of the key on the existing 72 lines means `phase2-baseline`, and why that name is reserved so
  the shootout's lines cannot be mistaken for the recorded run.

- **EGARCH at h=66 gets no benefit from more simulation paths** (R102): the Monte-Carlo error is
  0.951% at S=10,000 and **3.273%** at S=100,000, because an EGARCH with β≈0.98 and skew-t η=8
  compounded over 66 steps has a mean dominated by rare explosive paths, so the sample mean does
  not converge at 1/√S. `S` stays at 100,000 because it helps the other three rows, the measured
  error is printed beside every simulated figure, and **no significance is claimed there**. The
  2.5-hour shootout cost is therefore buying accuracy at three of four simulated rows, not four.
- **`compare`'s CLI defaults `--every` to 5 while `walkforward`'s defaults to 1.** Deliberate —
  R92's and R94's `q` and `n_eff` were derived at stride 5 and the recorded run used it — but the
  two entry points now disagree on a default, which is worth knowing before someone compares runs.
- **`_persisted` is duplicated** in `compare/__main__.py` because `walkforward`'s copy is private
  to its own `__main__`. Coordinator's to resolve, not an implementer's.
- ~~**The recalibrated contestant has no real-data witness at h ≥ 22**~~ — **discharged**
  `b04f079` (US-3.14). Carried for two sprints, and R129's audit is what finally named it: data
  arriving is not a witness arriving. Three real-panel tests at h=22 and h=66 now cover the fit,
  the 60-point lookback and the 40-pair floor, the last against a **real** origin with 39 pairs
  and the next session with 40, found by bisection rather than a hardcoded date. **Not redundant
  with the toy:** an unscaled intercept in `mincer_zarnowitz` is caught *only* by these, because
  the toy's a=0, b=1 case has no intercept to mis-scale. SPY only; QQQ and IWM have ledger lines
  at those horizons and no witness, accepted because the contestant is symbol-agnostic and the
  logic is what is under test. +18s suite runtime.
- **Coverage gaps in the recalibrated contestant's tests**, stated rather than discovered later:
  the shipped 60-point lookback and 40-pair floor are exercised only at h=1, stride 1, on the last
  four toy origins; the toy has no usable pairs at h≥22, so those horizons are covered only by the
  hand-built lattice test. A real-data witness belongs with US-3.4, which will have the cohort.
- **Both contestants were built before their tests in part**, and both compensated with mutation
  checks rather than claiming otherwise — six planted defects each, every one caught by a named
  test. Recorded because the method says test-first and the honest report of a departure is worth
  more than a silent one.

- **Nothing enforces the cohort/horizon pairing** that R114 requires. `phase2-shootout` (seven
  contestants) is correct only at h=1 and `phase2-shootout-multistep` (six) only at h≥2, and
  `--cohort` will accept either at either. Run the wrong one and the output is meaningless but
  looks ordinary. US-3.9 is the durable fix; until it lands this is an operator trap, recorded
  rather than relied upon to be remembered.
- **`gjr11_skewt` is settled, and the answer was a library bug** (R117-R120). It does **not**
  share EGARCH's pathology: its multi-step mean always exists, so it is not excluded anywhere. But
  `arch`'s stationarity constraint hard-codes `κ = ½`, which is `E[z²1{z<0}]` only for a
  *symmetric* law, so under skew-t with `λ < 0` the feasible region contains explosive processes —
  and the fit is explosive at **238/238 SPY and 238/238 QQQ origins** (109/238 on IWM). The h=5
  QLIKE lead is real (0.0002 estimator error against a 0.0240 margin). No rank flips; the run was
  not stopped. **What is open is which estimator GJR takes into the holdout, and it blocks
  registration** — see the first risk below.
- **`garch11_skewt` is clean, and immune by construction** (R118). `φ = α + β` has no `κ` in it, so
  arch's constraint is the true condition and its analytic recursion is the true mean under any
  unit-variance law. Worth one line on the record: `α + β` is pinned to 1 within 1e-9 at 192/250
  SPY origins, so it is effectively an IGARCH fit — exact and finite, growing about linearly in h.
  **R90's choice of `"analytic"` for the symmetric specification is the entire reason it is
  trustworthy at h=66.**

- ~~**Which estimator `gjr11_skewt` takes into the holdout is undecided**~~ **— decided (R124) and built (R127).** It takes the exact closed form, so it leaves R115 entirely and the registration blocker is closed. Kept for the reasoning: R115 requires a simulated contestant's Monte-Carlo error to be measured on the series
  being evaluated; at the reveal those are holdout fits, and the measurement needs an exact value
  to compare against. A seed spread is not one — on SPY at h=66 it understates the true sd by a
  median **58.8x**. So under the simulated estimator R115 refuses GJR's significance at **every**
  horizon, h=5 included, where it leads. The exact closed form `m_{k+1} = ω + φ_true·m_k` with `κ`
  by quadrature would remove the estimator from the question entirely and retire R90's reason to
  simulate; registering as-is means declaring those refusals in advance. **The user's decision.**
- ~~**The Monte-Carlo error table must be replaced before registration**~~ **(R119; superseded by R125).** GJR left the table altogether, and EGARCH's entries cannot be re-measured on-series because R114 established it has no finite mean to measure against. The table keeps EGARCH's two entries behind an invariant test. Original reasoning: It authorises nothing — its source is never the evaluated
  series, so the guard withholds and prints `no claim` — but `0.207%` beside a GJR row reads as
  though SPY were similar, and SPY is 1.32% median at h=22 and unbounded at h=66. The module's own
  comment anticipated this: *"The day the figures are re-measured on the real panel, this string
  and the table change together."* **What the replacement contains depends on the decision above**,
  so it is not started. EGARCH's h=22/h=66 entries are dead either way — R114 excludes it at h≥2.
- **The deeper fix is refused before registration, deliberately.** A `κ(η, λ)`-aware stationarity
  constraint replacing arch's `0.5` would fix the *model* rather than the estimator, but it changes
  the fit itself and its effect on the losses is unknown. Recorded as a **Phase 3 candidate**, not
  a Phase 2 story.
- **`top1_share` was the wrong diagnostic, and it was mine.** R116 proposed it as the cheap
  deciding test for GJR. GJR has no SPY origin with a path over 50% at h=22 or h=66, so it reads
  clean — while the ratio of theoretical to sample-implied Monte-Carlo sd has median 58.8 and max
  5.2e5 at h=66. The paths carrying the variance are never drawn, so concentration among the drawn
  paths is benign by construction. **Third time in this project a proposed diagnostic measured the
  sample rather than the estimator** (R72, R78 were the others).

## Not this phase's to fix, and carried forward

- **`Splits` has `dev_end` and `holdout_start` but no holdout end.** Today is past
  `holdout_start`, so *any* read of live data is a holdout read. The framework's **Phase 4**
  wants "unattended daily runs", which collides with the seal head-on; it needs an architect
  ruling and a user decision before a single Phase 4 story is written. Recorded here because an
  agent asked to make dailies work is exactly who would reach for `allow_holdout`.
- **Phase 3 needs an options source and the pricing library**, neither of which is in this
  repository. Partial unblocks exist that do not: HAR-RV-IV can use the already-ingested VIX
  family, and the VaR/ES and vol-target evaluators need only forecasts and prices.
- **Phase 5 needs Postgres and LLM API keys**, which an agent cannot provision.


## Sprint close

**Phase 2 completion is done, with one story carried and one criterion deviated from.** Closed on
the strength of R129's audit rather than on assertion: every story the board calls done has a
commit, its witnesses exist by name and bite, and the gates are green — `ruff`, `ruff format`,
`mypy --strict` over 37 files, **1360 tests passing**.

**What the phase exits on.** `python -m volab.leaderboard` prints a reproducible leaderboard per
symbol and horizon from the two committed ledgers, computing nothing of its own: seven contestants
at h=1 and six above it, both losses, DM with the Harvey correction, MCS and SPA, `n_eff` beside
every multiple-comparison figure and a printed refusal wherever the window is too thin. The
recorded run behind it is `4865619` and `6e0da08`, on a clean tree, with
`N of N origins identical` at all twelve configurations.

**The deviation, which is not ticked off.** The framework says *"logged in MLflow"*. This phase
logs to the append-only git-tracked ledger and **refuses MLflow for a stated reason** (R84): 3.x
needs `pyarrow<26`, the project pins `>=26`, and relaxing it would change the engine under
`store.py` on which the byte-exact comparator — and therefore the whole point-in-time claim —
rests. Any write-up citing Phase 2 must say which criterion it met.

**Carried into Phase 3, deliberately and not quietly:**

1. **US-3.9 is not started.** Nothing enforces the cohort-to-horizon pairing, so `--cohort
   phase2-shootout --horizon 22` would include `egarch11_skewt` where R114 established it has no
   finite multi-step mean, and the output would look entirely ordinary while being meaningless.
   Narrower than it was — GJR is exact at every horizon now, so EGARCH is the only horizon-limited
   contestant — but still an operator trap, and the durable fix is a contestant declaring its own
   horizons.
2. **`har_levels_mz` has no witness at h >= 22.** It has ledger lines there now, which is not the
   same thing as a test. R129 flagged it and it is **not** discharged.
3. **`egarch11_skewt` still simulates at h=1** by decision (R124), so R115 withholds its
   significance although its h=1 Monte-Carlo error is identically zero. Revisit only after US-3.9,
   and only with the post-hoc-selection reasoning in R124 in view.
4. **`Splits` still has no holdout end**, which collides head-on with Phase 4's unattended daily
   runs and needs a user decision before a single Phase 4 story exists.

**What is not Phase 2's and never was:** the holdout read itself. The pre-registration is a DRAFT
with section 4 blank by design; filling it, committing on a clean tree and recording the content
hash is the registration, and it is a one-way door belonging to the user.
