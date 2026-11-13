# The holdout reveal — decisions

Every ruling with the reasoning that produced it and what it costs if it is wrong. Numbering began
at **R216**, established the way R156 requires: `git show <ref>:<decisions file>` over **every** ref,
local and remote, for every `docs/**/*decisions*.md` in each, highest number found **R215**.

**It collided anyway, and that is worth recording as a limit of R156's procedure.** Phase 5 took
R216 for its own follow-up bundle (`84bf0c3`, merged in #20) on the same day, 2026-11-11. Both
sessions ran the scan honestly, at different moments, and both read R215 as the maximum: the
procedure checks what is written down, and nothing it can read tells it what another session is
about to write. HR-6's audit found the duplicate.

**The resolution, on the user's instruction, 2026-11-12: this sprint's first ruling is renumbered
R216 → R223.** Phase 5's R216 keeps the number because it reached `main` first while this branch is
still unmerged, so moving it would invalidate citations already merged. R217 through R222 are
untouched and unique, so this log now runs **R223, then R217 … R222** — out of sequence on purpose.
Renumbering the user's other rulings to restore the order would churn every citation of each of
them to buy nothing but tidiness, and the ordering claim that matters here is chronological, not
numerical: R223 and R217 were taken first, before any holdout number existed, and the table below
is the evidence of that.

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

## R223 — A contestant whose significance R115 withholds is still **eligible to be champion**

*Recorded as R216 when it was taken on 2026-11-11, and renumbered on 2026-11-12 after HR-6 found
Phase 5 holding the same number. The ruling itself is unchanged; only its label moved.*

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

---

## R218 — A `take` that read the holdout and then failed **still lifts the seal**, and that is the honest state

**The question, raised in review.** `take` writes `record_reveal_completed` at step 16 and only then
exits 13 (ambiguous champion) or 14 (truncation disagreed). So the command can exit non-zero having
already done the irreversible half of its job — the shape that burns an operator, who reads a
failure code, concludes "it did not work", and walks away from a lifted seal.

**The ruling: keep the write.** By step 16 the holdout **has been read** — 38 logged looks, the
figures in `details`. The seal's claim is that nobody has seen the data, and that claim is already
false. Withholding the completion line would leave the *machinery* sealed while the data was
actually seen, so the next run would present itself as the clean first read **with a log saying
so**. That converts an awkward truth into a false provenance claim, which is strictly worse than
lifting. The parent design's vocabulary for an unpaired `reveal_open` is "a crashed attempt,
counted, not a lift", and a run that read all 38 looks and could not name a champion did not crash.

**What it costs if it is wrong.** An operator who misreads a non-zero exit as "nothing happened"
burns the one-time read without realising. R219 is the mitigation and it is not optional: this
ruling is only defensible alongside it.

## R219 — The seal-state sentence is **derived from the record**, never from the exit code, and there are **four** states

**The error this corrects was mine.** Having decided R218, I told the implementer to make exits 13
and 14 announce the lifted seal — and to extend the same treatment to exit 1. That is wrong, and the
review seat that caught it was right: **exit 1 is the generic code for any unexpected exception,
including one raised before the first read.** A message keyed on the code would have told an
operator "the holdout is no longer sealed" when nothing was read and the reveal was still available.
That is a false claim in the *more expensive* direction — it says a one-time read is spent when it is
not, so the operator does not re-run. R218's hazard is a reader who thinks the seal is intact when
it is lifted; this one is a reader who thinks it is lifted when it is intact.

**The ruling: at exit, read the state and report what is there.** The sentence is a pure function of
`store.audit_tally()` (the on-disk half) and `store.reveal_is_committed()` (the committed half), and
of nothing else — not the exit code, not which gate fired. Four states:

| on disk | at HEAD | what the operator is told |
|---|---|---|
| neither line | — | nothing was written; the reveal is untouched and still available |
| `reveal_open` alone | — | the attempt is counted and **the seal is intact**; the next `take` is a new `reveal_id` |
| **both lines** | **not committed** | **the holdout has been read and this run was the reveal**; the figures are in `details`, and **the seal lifts when `data/holdout_access.jsonl` is committed** |
| both lines | committed | the seal is lifted; the holdout is ordinary history |

**The third state is not an edge case — it is where every successful `take` ends.**
`_reveal_committed` asks HEAD, so immediately after a clean run the record is on disk and uncommitted.
"Lifted" is wrong there and "intact" is wrong too, and it is exactly the moment the operator most
needs the precise sentence: the read is spent, and one commit stands between that fact and the
machinery agreeing with it. It is also why the design's commit-then-retry procedure works — the
first audit line dirties the tree at repository scope, so the next attempt refuses until the previous
one's evidence is committed.

**And it changes what the witness asserts, which is the better half of the ruling.** Not "code 13
produces this wording" but **"the wording matches the on-disk and committed record"**, across every
outcome the CLI can produce. A table keyed on exit codes is an enumeration, and this project has
been bitten by stale enumerations before; a check derived from the record cannot go stale when
someone renumbers a code or adds a failure path next year.

## R220 — A counted attempt that **took looks** is not the same state as one that took none, and R219's table grows a fifth row

**How this was found.** The exit-15 story (a configuration refused during scoring) returns *before*
`record_reveal_completed`, so `seal_state` derives `COUNTED` and the operator's last line reads
"seal: INTACT … it is counted and lifts nothing". By then `read_bars` and `derive_and_read` have
already taken their sealed looks. The implementer reported this rather than smoothing it over, and
noted the sharper half: **its new exit-table row asserted the false sentence**, so the witness
pinned the defect as though it were the contract. That is the same failure as the ledger over-count
test this project shipped once already — a test that asserts a bug is worse than no test.

**The hazard is R218's, in the direction R219 was not looking.** R219 removed "says lifted when
intact". This is "says intact when the data has been seen", which is the original hazard: a reader
concludes the holdout is untouched when a key-holder has read part of it.

**It is not confined to exit 15.** `window_not_ingested` (11) fires at look **16 of 38** and
`canary_disagrees` (12) after stage A's **13** looks, and an unhandled exception mid-span exits 1 the
same way. Every one of those leaves sealed looks taken and no completion line. Only
`keys_not_held` (10) genuinely takes none, because a step-3 refusal in `load_panel` touches no
filesystem before raising.

**The ruling: `seal_state` reads `tally.looks` as well as `tally.attempts` and `tally.completed`,
and `COUNTED` splits in two.**

| on disk | at HEAD | what the operator is told |
|---|---|---|
| neither line | — | nothing written; the reveal is untouched and still available |
| `reveal_open`, **no look lines** | — | the attempt is counted and **nothing was read**; the seal is intact |
| `reveal_open` **and look lines**, no completion | — | **sealed looks were taken, so holdout data has been seen.** The seal is intact at HEAD and this attempt is counted, but **a later run is not a first look** — the log says how many were taken and the write-up must say so |
| both lines | not committed | the holdout has been read and this run **was** the reveal; the seal lifts when `data/holdout_access.jsonl` is committed |
| both lines | committed | lifted; the holdout is ordinary history |

**Why not simply write `reveal_complete` on exit 15 instead.** That was the other candidate and it
is wrong: "lifted" means the holdout is ordinary history to be read freely, which is far too strong
for a run that took 16 of 38 looks and computed no registered figure. The honest statement is
narrower than either existing sentence, so it needs its own row rather than being rounded to the
nearest one. `record_reveal_completed` stays exactly where R218 put it.

**It stays derived, per R219.** The new row is a function of the tally, not of the exit code, so
exits 1, 11, 12 and 15 all acquire the correct sentence without any of them naming it — and so does
a failure path nobody has written yet.

**What it costs if it is wrong.** Nothing operationally: the sentence is strictly more informative
than the one it replaces. The cost of *not* making it is an operator who reads "INTACT" after a
key-holder has read raw holdout bars, and a next run that believes it is the first look.

## R221 — The looks count is the **log's**, not the attempt's, and that is the correct reading

**The residual R220 left, reported by its implementer rather than hidden.** `tally.looks` counts
every `look_open` in the whole audit log, not the ones this attempt took. So an attempt that was
refused for want of a key (exit 10, which touches no filesystem) would still print the
looks-were-taken sentence if an *earlier* attempt had left looks in the log.

**The ruling: leave it, and keep the wording that makes it honest.** The sentence says "the log
holds N sealed looks", which is a true statement about the log, and the question an operator needs
answered is not "did this process read something" but **"has the holdout been seen at all"** — for
which the log is the right scope. If an earlier attempt took looks then holdout data *has* been
seen, a later run *is* not a first look, and both halves of the sentence hold regardless of which
attempt did it.

**The direction of the failure is what settles it.** The dangerous sentence is "nothing was read",
and it prints only when the log holds **zero** looks — in which case nothing was read, by anyone,
ever. So the imprecision can only ever over-warn, never under-warn. Attributing looks to a
`reveal_id` would be more precise and is deliberately not done: it would add a second reading of the
log to the one `audit_tally` already gives, for a gain of precision in the safe direction only.

**One footgun from the same change, and it gets a guard rather than a comment.**
`SEAL_SENTENCES[COUNTED_WITH_LOOKS]` is now a template holding `{looks}`, so anything printing the
dict directly emits a literal `{looks}`. Production goes through `seal_sentence(tally, committed=…)`
and must keep doing so. That is a witness, not a convention: nothing under `src/volab/` may
reference `SEAL_SENTENCES` outside `seal_sentence` itself.

**Also recorded, because the implementer reported it against its own work.** Its mutant run showed
rows 13, 14, 0 and the printing-crash row failing under M1 and M3 — which was a bug in its own
table guard, fixed afterwards, so those four are **not** real catches and are not counted as such.
And M3 (the looks count hard-coded to 16) slips past exit rows 11 and 10 by luck, because that
carved store happens to take exactly 16 looks; the unit test with a tally of 7 is what actually
kills it. Both are the kind of thing a report usually rounds up, and neither was.

## R222 — HR-5's dress rehearsal is **delivered as witnesses, not as a `tools/` script**, and that is stronger

**HR-5's acceptance, from the board:** "the **whole** procedure runs against a pseudo-holdout carved
inside dev, on a **separate storage root**, leaving no mark on the real audit log".

**It is met, by `tests/carved.py` and `tests/test_reveal_cli.py`.** The carved world sets `dev_end`
2024-03-28 with a ten-session "holdout" of 2024-04-01..2024-04-12, mirroring the real store's shape
— raw runs through `holdout_end`, curated, features and targets stop at `dev_end`, which is exactly
the state the reveal exists to move on from. Eighteen CLI witnesses run real `take`s over it on a
`tmp_path` root, including the full span with its planned looks logged before they are taken, the
ledger footprint the shape predicts, a missing key, a crash during scoring, a second attempt
needing a commit first, every exit's seal sentence, and a refused configuration. The real audit log
stays absent throughout, which the tripwire readings in this sprint's commits record either side of
every run.

**Why a `tools/` script was never the right shape, and the ratchet says so.**
`tests/test_import_discipline.py` AST-scans `tools/*.py` for `allow_holdout` and for the unlock
variable, and fails on either. The rehearsal must hold a key — it is a rehearsal *of a keyed read*.
So a `tools/` rehearsal is implementable only by widening a ratchet that exists to keep probes out
of the sealed window, which trades a real guarantee for a convenience. The witnesses hold the key
through `monkeypatch.setattr(store, "_unlock_granted", lambda allow_holdout: allow_holdout)` — the
identity form, so it proves the key was actually passed — while the suite's own police stay armed
and no marker is added anywhere.

**What this does not claim.** The rehearsal exercises the procedure, not the real window: the carved
store has ten holdout sessions against the real 209, three symbols against three, and one cohort
pair. It cannot predict the real run's figures and does not try to. What it establishes is that the
procedure runs end to end, that the span is the declared one, and that every refusal path leaves the
record and the sentence the design says it should.

**The remaining gap is honest and stays on the board.** `tests/carved.py`'s docstring is the place a
reader meets the construction, and `HR-5` is marked done against these witnesses rather than
against a script that does not exist.
