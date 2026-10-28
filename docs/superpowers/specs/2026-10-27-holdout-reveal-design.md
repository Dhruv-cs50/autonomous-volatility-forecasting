# The holdout's end, the one-time reveal, and the live record — design

**Status:** awaiting approval. Nothing here is built.
**Date:** 2026-10-27
**Phase:** gates Phase 4, delivers Phase 6's reveal. Not Phase 2's, and Phase 2 does not wait on it.

---

## Intent, and why this exists now

This project's claim is not that it beats HAR. It is that it is an honest harness: it counts its
trials, it can show its leakage tests failing on demand, and it keeps one window of data sealed so
that a single final score means something. The sealed window — the holdout — is the last
load-bearing piece, and until now nobody had looked closely at how it ends.

Two facts, measured rather than assumed:

```
dev_end        2025-12-31
holdout_start  2026-01-02
today          2026-10-27
XNYS sessions from holdout_start to today: 206
```

The README defines the holdout as *"a 200-session holdout that accrues as the project runs"*. So
`Splits` having no `holdout_end` was **deliberate, not an oversight**: the window was never fixed,
it grew. And it has now **filled** — 206 sessions against a planned 200 — quietly, while the
harness was being built.

That produces the problem this design solves. The framework's **Phase 4** wants unattended daily
runs. A daily run asks for data "up to today", and today lies inside the sealed window. Worse, the
window as specified never closes, so it swallows every future day: a daily pipeline could never
legally reach the present. **The plan contradicted itself**, and nothing had noticed because
nothing had yet tried to run daily.

## What the user decided

Recorded as theirs, in their words where possible.

1. **The holdout closes at 2026-10-30.** A fixed date, not a rolling count.
2. **The seal lifts once, at the reveal, and the data then becomes ordinary history.** Chosen over
   a second narrow "forecast-only" read path, on the grounds that it needs no new hole in the one
   piece of code whose whole job is refusing holdout reads, and costs only ordering.
3. **All five protections are in**, because they make the project "more robust and decision
   oriented in a way that knows where the model can go wrong, removes a lot of the sandbox area,
   and can help us in the future too."

## Goals

- A third region of time exists and is named: **dev**, **holdout**, **live**.
- The reveal is a single, irreversible, pre-registered, audited event that can happen **once**.
- After the reveal, the project keeps earning evidence forever, from a frozen model, without
  anyone having to trust it.
- Every claim here has an executable witness (R32).

## Non-goals

- Moving `dev_end`. It is a pinned literal and the integrity of every recorded figure rests on it
  never moving. Retraining on more data is a different project.
- Building Phase 4's scheduler. This design only removes the contradiction blocking it.
- Reading the holdout. **This design does not take the reveal.** It builds the machinery and
  leaves the trigger to the user, which is the framework's own human gate.

## Anti-goals — what would make this a failure even if it worked

- **A seal that can be lifted by a flag or an environment variable.** The existing
  `allow_holdout` + `VOLAB_HOLDOUT_UNLOCK` pair is a developer key for tests, not a reveal. If the
  reveal becomes "set two things and read", the sealed window was never sealed.
- **An unlogged look.** If someone can read the holdout and leave no trace, the trial ledger's
  honesty is cosmetic — the one number nobody counted would be the number that mattered.
- **A reveal that can be retried until it reads well.** This is the forking-paths hole the trial
  ledger exists to close, applied to the headline. A crashed or abandoned attempt must still be a
  visible look.
- **A live record nobody can attribute.** If the champion can change after the reveal without that
  being provable, the ongoing record is worthless and worse than none, because it looks like
  evidence.

## The three regions, and the seal's lifecycle

| Region | Dates | Before the reveal | After the reveal |
|---|---|---|---|
| dev | ≤ `dev_end` (2025-12-31) | free | free |
| holdout | `holdout_start` … `holdout_end` (2026-01-02 … 2026-10-30) | sealed | free |
| live | > `holdout_end` | **unreachable** | free |

**Live is unreachable before the reveal, and that is not an accident.** A read "to today" passes
through the holdout on its way, so there is no way to hand out live data without handing out the
sealed window with it. This is precisely why option two — a narrow forecast-only path — was
rejected: it would have required the guard to serve a read that skips a hole in the middle, which
is a second code path through the exact function that must never have one.

The consequence, stated plainly because it reorders the framework: **Phase 4's unattended daily
runs can only run on live data after Phase 6's reveal.** Phase 4 can be built before, and
exercised on dev, but its first genuinely live morning comes after the drawer is opened.

### The validation direction is opposite to `dev_end`'s, and this matters

`volab.config` already refuses an environment override that moves `dev_end` **later**, because
later exposes sealed data. For `holdout_end` the dangerous direction is **earlier**: moving it
earlier shrinks the sealed window, so days that were sealed become "live" and freely readable
once the reveal happens. So:

- `VOLAB_SPLITS_DEV_END` may only move **earlier** (existing behaviour, unchanged).
- `VOLAB_SPLITS_HOLDOUT_END` may only move **later**. An earlier value is refused with the same
  kind of message.

A builder who copies the `dev_end` rule without thinking will get this backwards, and the test
suite is where that must be caught.

## The five protections

Each is one of the user's five, with the mechanism I propose. Where I chose the mechanism rather
than the user, it is marked **(my call)**.

### 1. Pre-registration — write down what you will report, before you look

A committed file (`docs/holdout-preregistration.md` **(my call)** on the path) naming, before any
holdout read:

- the exact contestants, symbols, horizons and losses that will be reported;
- the decision rule — what counts as the champion, and what would count as a failure;
- the trial-ledger line count at the moment of registration, so the "how many did you try" number
  is fixed before the answer is known.

**The reveal refuses unless this file exists and the working tree is clean**, and it records the
file's content hash in the access log. Then the result cannot be reinterpreted after it is seen,
because what "the result" means was fixed in a commit that predates it.

This is the natural completion of the project's own argument. The ledger already counts how many
things were tried; pre-registration closes the remaining loose end, which is how the final number
is *read*.

### 2. A dress rehearsal on practice data

The reveal happens once, so the procedure must be proven before it matters.

Carve a pseudo-holdout entirely inside dev — a `dev_end` earlier than the real one and the 200
sessions after it — and run the **whole** reveal procedure against it: pre-registration check,
log-before-read, fingerprint, read, score, write-up.

**It must leave no mark on the real audit log.** The rehearsal runs against a separate storage root
**(my call)**, which is the mechanism the existing tests already use (`tmp_path`), rather than a
"rehearsal mode" flag inside the real path — a flag that disables auditing is the anti-goal above
wearing a different hat.

### 3. The live record becomes the real evidence

After the reveal the champion is frozen and published, so every subsequent day is out-of-sample
by construction and cannot be gamed: the model was fixed before the data existed.

The sealed window gives 200 days, once. The live record gives unlimited days, forever, and is
*more* credible, because the model is public and frozen. So it is a headline output, not a
by-product: an append-only forecast log, one line per day per configuration, each carrying the
champion fingerprint from protection 4.

### 4. The champion's fingerprint, taken at reveal time

What makes 3 trustworthy. At the moment of the reveal, record: the champion's configuration, the
git sha, the dirty flag, and a content hash of the champion's definition **(my call** on using a
content hash as well as the sha, so a dirty tree is still identifiable**)**.

Every later forecast line carries that fingerprint. "We quietly improved it since" then becomes
impossible to say without it being visible.

### 5. Log the look before you take it

`store.py` already has the machinery: `AUDIT_FILENAME = "holdout_access.jsonl"`, a read past
`dev_end` needs **both** `allow_holdout=True` and `VOLAB_HOLDOUT_UNLOCK=1`, and the file is
git-tracked from the moment a read happens.

The change is ordering: **the record is written and flushed before the read is attempted**, not
after. An attempt that crashes, is interrupted, or is abandoned must still leave its line. "Count
your trials", applied to the final exam itself — exactly once, and provable.

**And the seal lifts on that record, not on a flag.** After a completed reveal record exists, the
guard stops treating `dev_end` as the limit. Because `holdout_access.jsonl` is committed, lifting
the seal requires a commit — visible, reviewable, and attributable — rather than an environment
variable someone can set in a shell. A builder must not add any other route.

## Constraints inherited

Non-negotiable, and each has already cost this project time:

- `uv run --locked …` always; never bare `uv run`, never `--frozen`.
- `filterwarnings = ["error"]`: a warning is a failing test.
- `tests/test_import_discipline.py` keeps `ALLOWED == {"store.py"}`; only `store.py` touches
  Parquet, and the sealed point-in-time path imports no numpy or scipy.
- The autouse `_forbid_holdout_unlock` fixture fails any unmarked test that reads past `dev_end`.
  **It is a feature.** New tests that legitimately exercise the reveal use `@pytest.mark.holdout`
  and a `tmp_path` root — never the real store.
- `data/trials.jsonl` is append-only and its line count is a reported number.
- R32: every mechanical guarantee names an executable witness.

## Answers the builder check forced out, and they are improvements

A reviewer planned from this document and found three things it did not settle, plus two
constraints it had missed. Two of the three are settled here **as my call**, with the reasoning,
so they can be overruled; the third is the user's and is asked separately.

### A crashed reveal does not brick the project. Attempts are counted, not forbidden. (my call)

The document held three statements that could not all be true: the reveal happens **once**, a
crashed attempt is still a **visible look**, and the seal lifts on a **completed** record. A
process that logs its line and then dies is visible but not complete.

Read one way — a crash spends the only shot — one transient failure (an unwritable path, a
`filterwarnings = ["error"]` warning in scoring, a Ctrl-C) permanently bricks the project's
headline and Phase 4 never gets a live morning. Nobody would choose that.

**So retries are permitted, and every attempt is reported.** This follows the project's own
established principle rather than inventing one: the trial ledger does not forbid a second trial,
it *counts* it and makes the count part of the result (R74, R75). The write-up must therefore
report **"k logged looks, the k-th completed"**, and k is a figure like any other — if k is not 1,
that is a fact about the experiment the reader is entitled to.

**And the structure is arranged so k is almost always 1.** Everything fallible happens outside
the logged read: deriving the holdout's curated, feature and target rows, and all scoring and
write-up, which operate on data already in hand. The logged read itself does as little as
possible. A reveal that fails should fail before the look or after it, not during.

A record is marked complete by a **second line** referring to the first by its id — append-only,
consistent with every other ledger here, and it means neither line can be rewritten to hide the
other.

### The pre-registered trial count is a snapshot, and the reveal records a second one. (my call)

Asked: must the ledger line count at reveal still equal the count pre-registration pinned?

**No — refusing on a changed count would force all of Phase 3 to finish before registration**, a
severe scheduling constraint on two phases for no gain. The protection's purpose is that the
number is **known and reported**, not frozen. So the reveal takes a second count, and the record
and the write-up carry **both, plus the delta**. A pinned number that silently understated the
trials would do less than it claims; two numbers and their difference say exactly what happened.

### The reveal predicate must ask git, not the filesystem. (my call, and a real hole)

The reviewer found a forgery route straight through anti-goal #1. The audit log lives at
`settings.storage_root / holdout_access.jsonl`; `data/**` is gitignored and derivable; and
`storage.root` is an **environment override** (`VOLAB_STORAGE_ROOT`). So a predicate that asks
"does a reveal line exist under the storage root" lifts the seal from a shell — which is precisely
the anti-goal.

**So the predicate asks whether the reveal record is committed in git, at the repository root**,
not whether a file exists under the storage root. That is the basis this document already claimed
("lifting the seal requires a commit") and it is immune to `VOLAB_STORAGE_ROOT`.

This also resolves cleanly against protection 2: the dress rehearsal runs on a separate storage
root and its records are never committed, so it **cannot** lift the real seal. That is the correct
behaviour and not a limitation — a rehearsal that could unseal the real window would be worse than
no rehearsal.

### The reveal spans several reads, because the derived tiers stop at `dev_end`

Verified by the reviewer: `data/raw` runs to 2026-10-20, but curated, features and targets stop at
2025-12-31, because both CLIs pin `end=settings.splits.dev_end` and accept no later value. The
Phase 1 spec anticipates this — Phase 6 derives the holdout rows of the curated and feature tiers
from raw — which means **one reveal event is several audited reads**, not one. The log-before-read
rule applies to the event, not to each read: one attempt id, and every read under it attributed to
that id.

## What is left to the builder

- Where `holdout_end` lives in `Splits`, its YAML key, and its validation (including the reversed
  override direction above).
- The reveal's shape: one CLI verb, or a module with a CLI over it.
- The schema of the forecast log and of the reveal record, beyond the fields named here.
- How the guard asks "has the reveal happened", and how that read is itself kept cheap and
  side-effect-free.
- Whether the dress rehearsal is a test, a `tools/` probe, or both. It is evidence, so R32 and the
  `tools/` convention both apply.

## Open, and genuinely the user's

- **When to take the reveal.** The machinery can be complete and the trigger unpulled; the
  framework makes this a human gate and this design keeps it one.
- **What the pre-registration says.** Its content is a research decision, not an engineering one.
