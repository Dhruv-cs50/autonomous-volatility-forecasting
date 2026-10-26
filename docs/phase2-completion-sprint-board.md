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
| US-3.1 | Architect the model-zoo interface and the comparison design before any model is written | `docs/superpowers/phase2-completion-decisions.md` (design section only, no code) | one `fit(train) -> forecast(h)` interface the existing three models can be expressed in without changing their numbers; the h-step aggregation defined against the `targets_rv_h*` definition rather than invented; DM's HAC lag and the MCS bootstrap's block length derived from the horizon and the stride-5 origins, not defaulted | todo |
| US-3.2 | GARCH(1,1) and GJR/EGARCH with skew-t errors, behind that interface | `src/volab/models/garch.py`, `tests/test_garch.py` | fits warning-free under `filterwarnings=error` via R83's rescaling; a non-converging origin is a recorded skip; h-step forecasts on the variance scale the losses use | blocked on US-3.1 |
| US-3.3 | Recalibrated HAR — Mincer-Zarnowitz, as the framework defines it at line 27 | `src/volab/models/recalibrated.py`, `tests/test_recalibrated.py` | the MZ regression is fitted on **training-window** forecasts only, so recalibration cannot see the origin it is scoring; a red case shows an in-sample fit scoring better and being refused | blocked on US-3.1 |
| US-3.4 | Diebold-Mariano, MCS and SPA over every contestant, and the leaderboard per horizon | `src/volab/compare/`, `tests/test_compare.py` | DM carries the Harvey correction and an HAC lag justified by the horizon; MCS membership reported at 10% and 25%; the leaderboard states **how many trials were evaluated** beside every ranking | blocked on US-3.2, US-3.3 |
| US-3.5 | The leaderboard as a committed command, reading the ledger | `src/volab/leaderboard/`, `tests/test_leaderboard.py` | `python -m volab.leaderboard` prints, per symbol and horizon, every contestant ranked on each loss with **the trial count beside it** and MCS membership at 10% and 25%; it reads `data/trials.jsonl` and computes nothing of its own, so the leaderboard cannot disagree with the ledger; a trial whose tree was dirty is shown as such rather than silently ranked | todo, depends on US-3.4 |
| US-3.6 | `delivery-auditor` over this board against git at the sprint close | — | the board and the repository agree, and the board is corrected where they do not | todo |

## Parallelism

US-3.2 and US-3.3 touch disjoint files and may run together **after** US-3.1 lands, because both
need the interface it defines. US-3.4 needs contestants to compare, so it serialises after both.
Never two implementers in one file, and the git index is shared state too: pathspec commits only.

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
