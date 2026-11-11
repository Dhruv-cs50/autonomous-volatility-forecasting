# The holdout reveal — decisions

Every ruling with the reasoning that produced it and what it costs if it is wrong. Numbering starts
at **R216**, established the way R156 requires: `git show <ref>:<decisions file>` over **every** ref,
local and remote, for every `docs/**/*decisions*.md` in each, highest number found **R215**.

The two rulings below are **the user's**, not a builder's defaults, and both were taken **before any
holdout number existed**. The evidence for that, read at the moment they were recorded
(2026-11-11T11:24:42Z, HEAD `eeab2f7`):

| | value |
|---|---|
| `docs/holdout-preregistration.md` content hash | `c0a095ab3a00711fd435eded709bde4dcdf9f1ab87ab016e92dc7c5cd7323779` — unchanged, matching R130 |
| `data/holdout_access.jsonl` | absent — no look has ever been logged |
| `data/trials.jsonl` | 414 lines, the registered figure |
| `data/comparisons.jsonl` | 52 lines |

---

## R216 — A contestant whose significance R115 withholds is still **eligible to be champion**

**The question.** The pre-registration's §3 fixes the champion as "the contestant in the 10% model
confidence set with the lowest QLIKE, at h=1 on SPY". Its §1 and §2 separately withhold **every**
significance claim from `egarch11_skewt` at h=1, because R115 requires Monte-Carlo error measured on
the evaluated series and that measurement does not exist. The document never says whether a
contestant whose *significance* is withheld remains eligible to be *champion*. Those are different
claims and both readings were consistent with the registered text.

**The ruling, the user's, 2026-11-11: eligible.** §3 is read literally — membership in the 10% set
and the lowest QLIKE decide it. The withheld significance restricts what may be **claimed about**
that contestant, not whether it can be **named**. The write-up therefore names the champion and
states plainly, in the same breath, that its lead carries no significance claim.

**Why this is an interpretation and not an amendment.** The registered document is unchanged and its
hash still matches R130; nothing was edited and no new registration is required. §3's sentence
already says what decides the champion, and this ruling picks which of two readings of that sentence
the machinery implements. No holdout number informed it: none exists.

**Why the ordering was the whole point.** `champion_from` returns `AMBIGUOUS` and `take` exits 13
when the rule does not settle the case — but **exit 13 still writes `reveal_complete`**, and once
that line is committed the seal is lifted and every contestant's holdout figures are on screen. A
ruling taken after that moment would be selection on the numbers by construction, however
principled its reasoning. So the question was put to the user while the machinery was still being
built, and this file is the record that it was answered first.

**What it costs if it is wrong.** The reported champion may be a contestant about which the
registration permits no significance claim, which is an unsatisfying result to present. That is a
cost in rhetoric, not in validity, and §3's companion bullet already admits an unsatisfying outcome
("success is not beating HAR"). The alternative reading would have cost more: silently skipping a
contestant that §1 itself describes as leading h=1 QLIKE on the dev window would be a
post-registration narrowing of the contestant set.

## R217 — The reveal may be taken **once the window has closed**, not after each horizon's tail

**The question.** The registered document contradicts itself. §1 and the ratified parent design make
the window closing sufficient; the registration's own closing paragraph **and R130** say
"`holdout_end` (2026-10-30) plus each horizon's tail", which for h=66 is about 2027-02-05.

**The ruling, the user's, 2026-11-11: the window closing is enough.** The implemented gate
`window_open` compares against `holdout_end`.

**Why this is safe, and it is the reason rather than a preference.** The choice is
**figure-neutral**. Every read is bounded at `holdout_end` by the same bound that bounds everyone
else's, and `load_panel` filters on `as_of`, so a target stamped 2027-02-05 is invisible to the
reveal whenever it is taken. Waiting three months changes **no number** in the report. The
registration's own §1 table already declares the consequence — 143 usable origins of 209 at h=66,
187 at h=22 — so the thin tails are a declared property of the window, not data still arriving.

**What this corrects, in passing.** The sprint board said "every registered origin's target
completed inside it, and no data is outstanding". The second clause is right and the first is wrong
as written: 187 of 209 origins at h=22 and 143 of 209 at h=66 have completed targets, which is what
§1's table says. The window is closed; the tails deliberately are not.

**What it costs if it is wrong.** Nothing numerically, by the argument above. If that argument is
itself wrong — if some read were *not* bounded at `holdout_end` — the cost would be real, so the
bound is witnessed in `store.py` rather than asserted here: `_read_limit` returns `holdout_end` for a
key-holding read, and the live region is unreachable without a committed reveal record.

---

## What is still **not** decided, and is not ours to decide

Four open questions stated in `docs/superpowers/specs/2026-11-10-reveal-design.md` remain open on
purpose: an unregistered `minimum_training` that provably cannot bind, whether a holdout origin may
train on earlier holdout sessions, the grouping of "the ranking on each loss", and a refusal
threshold that is arithmetically vacuous in a 209-session window. Each is reported as
under-specified rather than resolved, which is the registration's own standard: a stated "this
cannot be settled from what we wrote down" is a result.
