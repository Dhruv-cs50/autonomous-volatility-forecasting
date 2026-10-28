# Pre-registration for the one-time holdout read

**Status: DRAFT. Not registered.** This document is only binding once it is committed on a clean
tree and the reveal records its content hash. Until then it is a proposal to react to.

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
evaluated** (R115). The existing figures come from a synthetic probe whose process is gentler
than the fitted one, and a six-seed spread is not a measurement of an estimator with no finite
mean. Absence of measured spread is not evidence of precision.

**Losses.** QLIKE and MSE, both, on the variance scale.

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

**Also refused:** EGARCH at h=66, on any loss. Its own Monte-Carlo forecast error is about 3.3%,
above the 1.5% ceiling R72 set as this project's floor for a believable difference (R102), so the
contestant's noise is wider than the differences being compared.

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

`data/trials.jsonl` line count at registration: **TO BE FILLED AT REGISTRATION.**

Deliberately left blank in this draft, and the expected figure has already moved twice, which is
why. The first draft expected 240 (72 + 168). The first shootout **crashed** after three of
twelve configurations and appended 42, leaving the ledger at **114** — those trials are counted,
not discarded (R112). The re-run appends **150** rather than 168, because EGARCH is excluded at
three of four horizons (R114): 7 contestants x 3 symbols x 1 horizon x 2 losses = 42, plus
6 x 3 x 3 x 2 = 108. So the expected figure at registration is **264**, and it will be read from
the ledger at that moment rather than predicted here. The reveal takes a **second** count and records both plus the delta
(R-design answer 2), since further work may legitimately add trials between registration and the
read.

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

**To register:** fill section 4, commit on a clean tree, and the reveal will record this file's
content hash. Nothing here may change afterwards; a change means a new registration and a new
look, both counted.
