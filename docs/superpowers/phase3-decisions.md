# Phase 3 decisions

Continues the numbering from R133 in `docs/superpowers/phase2-completion-decisions.md`.

---

## R134 — Phase 3's requirement is a reconstruction, and it is labelled as one

The framework document that defines each phase's exit criterion **is not in this repository**.
The user confirmed on 2026-10-28 that it is not to hand and said to proceed.

Phase 3's scope is therefore assembled from `docs/phase2-completion-sprint-board.md` lines
226-228, the board's own "Not this phase's to fix" section: a HAR-RV-IV contestant using the
already-ingested VIX family, plus VaR/ES and vol-target evaluators; the options chain and the
options pricing library are absent and put everything needing them out of scope.

**Ruling: Phase 3 may be declared *complete as specified*, never *exited*.** Those are different
claims and any write-up must say which it is making. Phase 2 was reopened after its row text was
checked against `grep` and GARCH, DM and MCS were found missing from `src/` — guessing an exit
criterion is how a phase gets declared done when it is not.

**Standing ask:** commit the framework into `docs/` before the Phase 4 boundary. Two consecutive
boundaries resting on memory is the condition R129's audit found six errors under.

**User decision, 2026-10-29:** scope is the reconstruction **plus** both of Phase 2's live
carried items.

---

## R135 — `Domain.requires` is horizon-dependent, and R131's figures were measured on a premise a
real IV contestant does not share

R131 measured what a VIX1D-requiring contestant costs the whole cohort: 119 of 251 origins at
h=1, 120 of 250 at h=5, 123 of 247 at h=22, 132 of 238 at h=66. R133 pinned those figures in
`tests/test_coverage_real.py::test_the_designs_measured_figures_still_hold`.

**Those figures required `iv_h1` at every horizon.** `tests/test_coverage_real.py:41` sets
`REQUIRED = "iv_h1"` and uses it unchanged for all four. A HAR-RV-IV needs the horizon-**matched**
column. Re-measured 2026-10-28 against the committed store, same window (`2021-01-04`), stride 5,
`minimum_training=250`, through `wf.coverage_refusal`:

| h | origins | `iv_h1` everywhere | matched column only |
|---|---|---|---|
| 1 | 251 | refused, 119 lost | refused, **119 lost** (`iv_var_h1`) |
| 5 | 250 | refused, 120 lost | **no refusal, 0 lost** (`iv_var_h5`) |
| 22 | 247 | refused, 123 lost | **no refusal, 0 lost** (`iv_var_h22`) |
| 66 | 238 | refused, 132 lost | n/a — `IV_SERIES` has no h=66 entry |

**The VIX1D trade-off exists at h=1 and nowhere else.** The h=5 and h=22 losses are an artifact of
one tuple being read at every horizon, and they buy nothing. R131's figures are not wrong — they
measure what they say they measure — but the premise they were briefed under, that a flat tuple
is what an IV contestant would declare, is not a premise an IV contestant has to accept. R133's
pinned test is unaffected and stays as it is: it pins `iv_h1` at every horizon, which remains a
true statement about the store.

This is not merely tidiness. `Evaluation.comparable()` intersects defined origins across **all**
contestants (R56, R121), so a flat declaration strikes those 120 and 123 origins from every other
contestant's loss at horizons where the IV contestant does not need the column at all. The flat
design degrades the field it is being compared against, for nothing.

**Ruling (user decision, 2026-10-29):** `Domain` gains `per_horizon: tuple[tuple[int, str], ...]`
and `requires_at(horizon)`. A tuple of pairs, not a mapping, so the frozen dataclass stays
hashable and `eq` keeps value semantics. `requires` keeps its meaning — the columns needed at
every horizon — and `requires_at` is `requires` plus this horizon's pairs.

`__post_init__` **refuses a pair whose horizon the contestant does not answer.** A column declared
for a refused horizon is a dead declaration, and a dead declaration is how a later reader
concludes the contestant supports a horizon it does not.

**`Cohort.requires` is deleted, not kept beside `requires_at`.** All seven call sites already hold
a horizon. Keeping the horizon-blind name leaves the trap for whoever reaches for the shorter one
— the argument R131 made for refusing a bad cohort/horizon pairing rather than narrowing it.

**What the witnesses cannot do**, stated now rather than found in review: a contestant's
declaration cannot be shown *complete*. A contestant silently reading an undeclared column is
today's behaviour unchanged; R131 records this about `requires` and `per_horizon` inherits it. No
Phase 3 witness claims more.

---

## R136 — the evaluators are a separate package whose loss feeds the existing machinery

**Ruling (user decision, 2026-10-29):** `src/volab/evaluators/` consumes an in-memory
`Evaluation`, computes the Fissler-Ziegel joint (VaR, ES) loss, assembles a `LossSeries` by hand
and hands it to `volab.compare.analyse`. DM, MCS and SPA then apply unchanged. Coverage tests and
vol-target P&L sit beside it as diagnostics. Results go to `data/decisions.jsonl` through a sink
modelled on `compare/sink.py`.

**`walkforward.LOSSES` does not gain an entry, and that is the whole point.** `LOSSES` is read by
the driver loop, so an entry there makes every future run of every cohort write a third trial line
per contestant per configuration. `count_trials` counts `data/trials.jsonl`, the pre-registration
pins an expected count, and the reveal reports a second count plus the delta. FZ0 is strictly
consistent for the (VaR, ES) pair, which is what licenses `analyse` accepting it; `analyse` takes
a `LossSeries` and knows nothing about where one came from.

**A separate ledger, not a second record shape in `trials.jsonl`** — the argument `.gitignore`
already makes for `comparisons.jsonl`: a second kind of record there corrupts the one number the
write-up has to report.

**Measured, not assumed:** per-origin forecasts are persisted **nowhere**. `trials.jsonl` holds
means, `comparisons.jsonl` holds statistics, and `LossSeries` is built from an in-memory
`Evaluation` and discarded. Adding a persisted per-origin artifact was considered and rejected as
a second source of truth for numbers the driver already holds.

---

## R137 — the quantile map is Gaussian, and the understatement is the finding

**Ruling (user decision, 2026-10-29):** `VaR = z_alpha * sqrt(sigma2_h)`, ES from the Gaussian
formula. One map, no estimated parameter, one trial per contestant.

A Gaussian map understates tails. That is not a defect of the design; it is what Kupiec and
Christoffersen are there to measure, so the understatement arrives as a number rather than as an
assumption nobody tested. Student-t with `df=5` was rejected because 5 has no evidence behind it
in this repository and tuning it until coverage passed would be fitting the map to its own test.
Filtered historical simulation is the literature's answer and is **deferred, not rejected**: it
needs a one-step forecast at every session rather than every fifth, a stride-1 run this phase does
not otherwise need.

**Overlap.** Kupiec and Christoffersen assume independent hits. At stride 5 that holds at h=5 and
fails at h=22. The coverage tests **refuse** where `stride < horizon` and name the condition. A
HAC correction was considered and rejected: its own validity at these sample sizes would then need
evidence of its own, and a refusal that says why is honest at no cost. FZ0 is unaffected —
`analyse` already handles overlap through `h_eff` and the Bartlett column (R94).

**The dividend bias is printed, not fixed.** Curated bars carry raw OHLCV and `has_distribution`,
no adjusted close, so a close-to-close vol-target P&L is wrong on ex-dates. Building a
total-return series is out of scope, so the report carries the `has_distribution` count inside the
window — the move `MONTE_CARLO_ERROR_SOURCE` makes: a figure that cannot be fixed here is printed
with what it is wrong about.

---

## R138 — no Phase 3 run appends to `trials.jsonl` until the reveal has been triggered

**Ruling (user decision, 2026-10-29):** Phase 3 builds and tests to completion; no Phase 3
walk-forward writes a ledger line until the user has triggered the holdout reveal.

`count_trials` counts `data/trials.jsonl` and the reveal records a second count plus the delta.
`holdout_end` is 2026-10-30. A Phase 3 run before the reveal would put Phase 3's trials inside a
delta a reader will attribute to Phase 2 re-runs. The alternative — run freely and say in the
write-up which part of the delta is Phase 3 — was offered and declined.

Phase 3's recorded run is therefore **built, tested and held**, and the board carries it as
blocked on an event only the user can cause. Unit and integration tests do not touch the real
ledger and are unaffected.

---

## R139 — the registered read has no mechanical fence, and Phase 3 builds one before it builds
anything else

`docs/holdout-preregistration.md` section 5 says Phase 3's contestants "must not be added to this
window after the fact". **No test enforces it.** Verified: `grep -rn 'preregistration\|prereg'`
over `tests/` and `src/` returns nothing. `SHOOTOUT` and `MULTISTEP` are ordinary module-level
constants, and a future agent adding `har_rv_iv` to either would break a registration recorded as
a one-way door, with a one-line diff and a green suite.

**Ruling: P3-01 is story one**, before any Phase 3 code exists — a witness pinning
`SHOOTOUT.contestants` and `MULTISTEP.contestants` to the pre-registration's own list, with the
document's section 2 quoted in the docstring and R130 and the registering sha `65617ee` named. It
is the cheapest story in the phase and the only one protecting something that cannot be undone.
