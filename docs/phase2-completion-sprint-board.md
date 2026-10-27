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
| US-3.4 | Diebold-Mariano, MCS and SPA over every contestant, and the leaderboard per horizon | `src/volab/compare/`, `tests/test_compare.py` | DM carries the Harvey correction and an HAC lag justified by the horizon; MCS membership reported at 10% and 25%; the leaderboard states **how many trials were evaluated** beside every ranking | todo, unblocked — must **call** `run_configuration`, not reimplement it, and print the measured Monte-Carlo error beside every simulated figure (R102) |
| US-3.5 | The leaderboard as a committed command, reading what the sink wrote | `src/volab/leaderboard/{__init__,__main__}.py`, `tests/test_leaderboard.py` | `python -m volab.leaderboard` prints, per symbol and horizon, every contestant ranked on each loss with **the trial count beside it**, plus MCS membership and `n_eff`; it **computes nothing of its own**, reading `data/trials.jsonl` for the losses and `data/comparisons.jsonl` for membership, so the leaderboard cannot disagree with what it reports; the headline trial count is every ledger line with the per-cohort breakdown beside it, so a new cohort cannot reset the number (R59); a trial whose tree was dirty is shown as such rather than silently ranked | todo, depends on US-3.4 |
| US-3.3b | Strengthen conformance clause 2 so it catches a model that captured its own target frame | `tests/model_conformance.py`, `tests/test_model_interface.py` | a plant that keeps its own reference to the target frame — the R78 shape — is caught by the **shared** suite rather than by one contestant's own test; the existing contestants still pass unchanged | done `d36d689` — clause 7 wired in with the signature unchanged, all seven contestants pass, plant caught by it and passed by clause 2 (R105) |
| US-3.3c | Make the rebuild factory mandatory, so the captured-copy case is closed for every contestant | `tests/model_conformance.py`, `tests/test_model_interface.py`, `tests/test_garch.py`, `tests/test_recalibrated.py` | `conformance_suite` requires a `rebuild` factory, so no contestant can decline it; a plant that `.copy()`s the target frame at construction is caught; the seven existing contestants pass (trivially, since none takes a frame) | todo (R105), lower priority — its value is prospective |
| US-3.7 | **The recorded shootout run**: every contestant, every symbol, every horizon, against the real store, appended to the ledger | `data/trials.jsonl`, `data/comparisons.jsonl`, `docs/superpowers/phase2-completion-decisions.md` | one run on a clean tree whose sha resolves to the code that produced it; **7 contestants x 3 symbols x 4 horizons x 2 losses = 168 trials, 168 ledger lines**, each carrying its cohort; the per-origin truncation-equivalence by-product reported; the measured Monte-Carlo error beside every simulated figure and `n_eff` beside every MCS one; the run's console output quoted in the decisions log the way Phase 2's own recorded run was | todo, depends on US-3.4 and US-3.5 — **about 2.5 hours of compute** (R90, R102) |
| US-3.6 | `delivery-auditor` over this board against git at the sprint close | — | the board and the repository agree, and the board is corrected where they do not | todo |

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

**It roughly triples the trial count**, from 72 to 240, and that is the number any write-up has to
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
- **The shootout costs about 2.5 hours, not about 15 minutes** (R90, R93). GJR and EGARCH have no
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
- **Coverage gaps in the recalibrated contestant's tests**, stated rather than discovered later:
  the shipped 60-point lookback and 40-pair floor are exercised only at h=1, stride 1, on the last
  four toy origins; the toy has no usable pairs at h≥22, so those horizons are covered only by the
  hand-built lattice test. A real-data witness belongs with US-3.4, which will have the cohort.
- **Both contestants were built before their tests in part**, and both compensated with mutation
  checks rather than claiming otherwise — six planted defects each, every one caught by a named
  test. Recorded because the method says test-first and the honest report of a departure is worth
  more than a silent one.

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
