# Pre-registration for the one-time holdout read

**Status: REGISTERED 2026-10-28, on the user's instruction.** Committed on a clean tree, and
binding from that commit: nothing below may change, and a change means a **new** registration and
a new look, both counted.

**This document does not name its own commit, deliberately.** A file cannot carry the hash of
itself and a commit cannot name its own sha — a lesson this project learned the hard way when an
amend invalidated a sha that had just been written into the board. So the registering commit's sha
and this file's content hash are recorded in `docs/superpowers/phase2-completion-decisions.md`
(R130) in the *following* commit, and the reveal compares the file it reads against that hash.

**Registered while the window is still open, which is the point.** `holdout_end` is **2026-10-30**,
two sessions after this registration. So the commitments below were fixed before the window they
describe had even finished, let alone been read — the timestamp demonstrably precedes the data.
Nothing in this repository has read past `dev_end` (2025-12-31): `load_panel` requires an explicit
`end`, `allow_holdout` has never been set, `VOLAB_HOLDOUT_UNLOCK` has never been read, and an
autouse fixture fails any unmarked test that reaches past it.

**Why it exists.** The trial ledger counts how many things were tried. This closes the remaining
loose end — how the final number gets *read*. Everything below is fixed before the answer is
known, so the result cannot be reinterpreted after it is seen. See R105's companion design,
`docs/superpowers/specs/2026-10-27-holdout-reveal-design.md`, protection 1.

---

## 1. What will be scored

**Window.** `holdout_start` 2026-01-02 to `holdout_end` 2026-10-30, 209 XNYS sessions. Origins at
**stride 1**, to use every session available.

**Symbols.** SPY, QQQ, IWM — the configured universe, all three, no selection.

**Horizons and the origins each can actually use.** A target dated `h` sessions after its origin is
stamped at that later session, so the tail of the window has no completed answer. Declared now
rather than explained afterwards:

| Horizon | Origins usable | `n_eff` = n/h |
|---|---|---|
| 1 | 208 | 208.0 |
| 5 | 204 | 40.8 |
| 22 | 187 | 8.5 |
| 66 | 143 | 2.2 |

**Contestants.** Seven at h=1; **six at h=5, 22 and 66**, and the exclusion is on the record with
its reason rather than being a quiet drop:

- h=1: `har_levels`, `har_semilog`, `random_walk`, `garch11_skewt`, `gjr11_skewt`,
  `egarch11_skewt`, `har_levels_mz`.
- h≥2: the same **minus `egarch11_skewt`** (R114). EGARCH(1,1) with skew-t errors has **no finite
  multi-step conditional variance** — the recursion gives `σ² ∝ exp(α|z| + γz)` and Hansen's
  skew-t has polynomial tails, so the expectation diverges for any `α > 0`. Every h≥2 forecast it
  produced was a Monte-Carlo estimate of infinity; the dev run measured 1.70e+12 at h=22, with one
  path of 100,000 carrying 99.99999997% of one origin's forecast. h=1 is exact because it takes no
  expectation. This is a property of the specification, established a priori, **not** a result of
  looking at the holdout.

**No significance will be claimed for any simulated contestant** (`gjr11_skewt`,
`egarch11_skewt`) unless its Monte-Carlo error has been measured **on the series being
evaluated** (R115). A six-seed spread is not such a measurement: on SPY at h=66 the sample-implied
spread understates the true Monte-Carlo sd by a median factor of **58.8**, because the paths
carrying the variance are never drawn. Absence of measured spread is not evidence of precision.

**What is measured, and why the clause now binds only EGARCH (R127).** `gjr11_skewt` **no longer
simulates.** It forecasts from an exact closed form — `m_{k+1} = omega + phi_true * m_k` with
`kappa = E[z^2 1{z<0}]` from the fitted skew-t — so it has no Monte-Carlo error to measure and
leaves R115 entirely. The recorded run prints no Monte-Carlo text at any horizon above 1, because
nothing in that cohort simulates.

So the clause above now reaches exactly one contestant: **`egarch11_skewt` at h=1**, the only
horizon R114 admits it. Its h=1 forecast takes no expectation, so every simulated path is
identical and its Monte-Carlo error is identically zero — but it is **not measured on the evaluated
series**, so R115 withholds its significance, and that withholding stands. Making it exact would
cost nothing technically and would upgrade the claim of the contestant currently leading h=1 QLIKE,
after seeing it lead; R124 refused that as post-hoc selection and the refusal is part of this
registration.

## 2. What will be claimed, and what will not

This is the half that matters, because it is the half that could otherwise be chosen after seeing
the numbers.

**Claimed:**

- the loss value per contestant, per symbol, per horizon;
- the ranking on each loss;
- pairwise Diebold-Mariano, with the Harvey correction, **only where `n_eff ≥ 20`**;
- model confidence set membership at 10% and 25%, **only where `n_eff ≥ 2/α`**.

**Refused in advance, by the rule and not by choice after the fact.** From the table above:

| | MCS 10% | MCS 25% | DM significance |
|---|---|---|---|
| h=1 | claimed | claimed | claimed |
| h=5 | claimed | claimed | claimed |
| h=22 | **refused** (8.5 < 20) | claimed | **refused** |
| h=66 | **refused** (2.2 < 8) | **refused** | **refused** |

So **no significance of any kind will be claimed at the 1-month or 3-month horizons**, and no
confidence set at all at 3 months. Those rows will print their refusal and its reason. This is
declared here so that a disappointing long-horizon result cannot later be presented as
significant, and a flattering one cannot either.

**The rule stands and its one live instance is gone.** Where two contestants' losses sit closer
together than a *simulated* contestant's measured Monte-Carlo error, the pair is reported as
**unordered** rather than ranked; the 1.5% ceiling R72 set as this project's floor for a believable
difference (R102) decides it. The instance R120 named — `gjr11_skewt` against `har_levels_mz` for
4th/5th on h=66 QLIKE — **no longer qualifies**, because GJR has no Monte-Carlo error any more. On
the recorded run that gap is 0.00775 between two exactly computed numbers, which is a *narrower*
margin than the 0.0148 R120 refused, and reportable where that one was not: the refusal was never
about the size of the gap but about comparing a gap to an estimator's own systematic deficit. No
significance attaches to it in any case, because h=66 refuses all significance on `n_eff`.

The rule is kept rather than deleted because `egarch11_skewt` still simulates at h=1 and a future
contestant may too. It currently binds nothing at h>=2, since no contestant there simulates.

`egarch11_skewt` needs no separate refusal here — R114 excludes it at every horizon above 1.

## 3. The decision rule

Fixed now. **Both bullets below were put to the user as my proposals and confirmed by them**, so
they are recorded as the user's decisions and not as a builder's defaults — which matters, because
the first one permits an unsatisfying outcome and the second defines failure in a way that does
not include a bad score:

- **The champion** is the contestant in the 10% model confidence set with the lowest QLIKE, at
  **h=1 on SPY**, chosen in advance as the primary configuration. If the 10% set has one member,
  that is the champion; if several, the lowest QLIKE among them; if the set is refused for
  thinness, **there is no champion** and that is the result.
- **Success is not beating HAR.** This project's stated contribution is the harness. A result in
  which `har_levels` or `har_semilog` wins is a successful run and will be reported as plainly as
  any other. What would make it a *failed* run: a trial that reached no ledger line, a leakage
  test that could not be shown failing, or a figure with no command that reproduces it.
- **Every reported number names what produced it** (R32), and the write-up states the trial count
  from the ledger beside every ranking.

## 4. Trials already counted

`data/trials.jsonl` line count at registration: **414.** `data/comparisons.jsonl`: **52.**

Read from the ledger at this moment rather than predicted, which is why the field was left blank
until now: **the expected figure moved three times** while this document was a draft (240, then
264, then 414). Each move had a reason recorded — R112's crashed run counted rather than
discarded, R114's exclusion cutting a run from 168 lines to 150, and R127's exact GJR forcing the
clean re-run — and any of the three predictions would have been wrong by the time it mattered.

| ledger state | lines | why |
|---|---|---|
| after the dev walk-forward | 72 | the recorded Phase 2 baseline (R86) |
| first shootout, **crashed** after 3 of 12 configurations | +42 = 114 | counted, not discarded (R112) |
| re-run, EGARCH excluded at h>=2 | +150 = 264 | R114 |
| **the clean recorded run**, GJR now exact | **+150 = 414** | R122, R127 |

So the figure **as of this draft is 414**, and it will be read from the ledger at registration
rather than taken from this table. The arithmetic of the last row: 7 contestants x 3 symbols x 1
horizon x 2 losses = 42, plus 6 x 3 x 3 x 2 = 108.

**Nothing is superseded in the file, and that is the point.** All three shootouts remain, so the
ledger says 414 things were tried and not 150. The leaderboard partitions by `(at, git_sha)` and
renders each run separately, which is how a reader tells the exact GJR from the simulated one
(R124); **`75ae610` is the boundary sha** at which GJR became exact.

**One blemish is permanent and disclosed:** 108 of the 414 lines carry `git_dirty: true`, because a
documentation commit of mine landed between the two halves of the *superseded* re-run (R122). The
leaderboard marks every one of those rows. The clean recorded run that this registration rests on
carries **zero** dirty flags, and its own truncation-equivalence report is `N of N origins
identical` at all twelve configurations.

The reveal takes a **second** count and records both plus the delta (R-design answer 2), since
further work may legitimately add trials between registration and the read.

## 5. What this does not pre-register

Said plainly, so the boundary is not mistaken for a claim:

- the holdout's own `n_eff` refusals are derived from the window, not chosen, so they are
  predictions here rather than commitments;
- nothing about Phase 3's contestants, which do not exist yet and must not be added to this
  window after the fact — a later contestant gets a later, separately registered read, or the
  live record;
- the live forward record that begins after the reveal, which is a different instrument with its
  own continuing log.

---

**Registered.** Section 4 is filled from the ledger, the tree was clean, and the registering
commit's sha and this file's content hash are recorded in R130 in the commit that follows this
one. Nothing here may change afterwards; a change means a new registration and a new look, both
counted.

**What remains, and belongs to the user:** the reveal itself. It cannot run before `holdout_end`
(2026-10-30) plus each horizon's tail, it takes a second trial count and records both plus the
delta, and it is triggered deliberately and once.
