# The seal's lifecycle in `store.py` — design (HR-2)

**Status:** design only. No source file is edited by this story.
**Date:** 2026-11-10
**Story:** HR-2 on `docs/holdout-reveal-sprint-board.md`. Implemented by HR-3.
**Parent:** `docs/superpowers/specs/2026-10-27-holdout-reveal-design.md`, ratified. Its three-regions
table, protection 5, and its builder-check answers are requirements here, not open questions.
**Branch:** `holdout-reveal`, cut from `main` at `c207c59`.

Every number below was measured in this checkout (`~/dev/Autonomous Volotility Forcasting/vol-lab`,
the non-iCloud clone — the iCloud clone is slower and the git figures there will be worse).

---

## 1. The actual starting line, measured rather than assumed

HR-1 is **already landed**, at `85de6a5` ("P4-05: the live region — holdout_end, the later-only
override, three named regions"), nine days before this design. `src/volab/config.py` already has:

- `splits.holdout_end`, `2026-10-30` in `config/base.yaml`, matching the registered value;
- `Region`, an enum of `DEV` / `HOLDOUT` / `LIVE`;
- `Splits.region_of(day) -> Region`, total over every date and pure, with `holdout_end` defaulting
  to `dt.date.max` so a hand-built `Splits` has no live region and fails closed;
- `_refuse_an_earlier_holdout_end`, the mirror of `_refuse_a_later_dev_end`, with its witnesses in
  `tests/test_config.py`.

So decision 3 is not "invent a classifier". It is "how does `store.py` consume the one that
exists". The board's HR-1 row is redundant and should be marked done at `85de6a5`.

**`store.py` enforces none of it.** `config.py`'s own docstring is explicit, and it is the honest
statement of where we start:

> The dates after `holdout_end` are `live` (see `Region`): unreachable until the holdout is
> revealed, and **no read path here or in `store` is any more open for them than for the
> holdout.** The guarded read path compares a caller's requested `end` against `dev_end`

and `region_of`'s own docstring: *"Naming `live` opens nothing: the guard asks only about
`dev_end`."* `store.load_panel` step 3 is one comparison, `end > resolved.splits.dev_end`, and the
two keys open everything beyond it. **Today both keys read live data.** That is the defect
requirement 1 names, and `Region` is descriptive only — nothing consumes it.

### The hot-path premise in the brief is false, and this is load-bearing

The brief says `load_panel` is "called once per origin per contestant, thousands of times in a
shootout". It is not. `volab.walkforward.load_for` reads the panel **once** per `(symbol, horizon)`
and the per-origin loop truncates it in memory. Measured, on the real store:

```
load_for(SPY, h=22)                 9 load_panel calls, 5.9 s   (median 617 ms/call, 406–857)
a real 13-origin walk-forward       1 further call (_persisted); the origin sweep makes ZERO
every call's end                    2025-12-31 == settings.splits.dev_end, exactly
any call with end > dev_end         False
whole shootout, derived             10 × 3 symbols × 4 horizons = 120 calls, all in DEV
```

`load_for`'s docstring already pins why: *"`dev_end` and nothing else. There is no parameter here
that could ask for a later `end`."* So **the shootout asks the reveal predicate zero times**, and
the caching question is a footnote rather than the design's pivot. That matters twice: it removes
the pressure that would otherwise have pushed the predicate toward something cheap-and-forgeable,
and it means the 35 ms git call below is paid by the reveal, the rehearsal and the daily — three
callers that make a handful of past-`dev_end` reads each — and by nothing else.

---

## 2. Decision 1 — how the guard asks "has the reveal happened"

**Decision: lazily, then memoised per process. The predicate is asked only when
`end > dev_end`, which is never on the shootout path; the first such call costs one
`git cat-file` and every later one costs a dict lookup. Any git failure means "not revealed".**

Measured costs, this checkout:

```
git cat-file blob HEAD:data/holdout_access.jsonl   (absent at HEAD)   median  35.6 ms (26–111)
git cat-file blob HEAD:data/trials.jsonl           (163 KB blob)      median  35.2 ms (30–77)
git rev-parse HEAD                      (subprocess+git floor)        median  14.5 ms
git status --porcelain, scoped  (what _git_state already runs)        median  18.9 ms
memoised predicate (dict lookup)                                              0.000 ms
Splits.region_of(date)                                                        0.000 ms
load_settings()                                                               0.63 ms
one load_panel read of the real store                                 median 617    ms
```

The laziness is the decision; the memo is insurance:

- **A dev read never asks.** `revealed = past_dev_end and _reveal_committed(...)` short-circuits on
  `and`. 120 shootout reads, 0 subprocesses, 0 ms.
- **First past-`dev_end` call in a process:** one subprocess, 35.6 ms, against a 617 ms read — 5.8%
  of one read, 0.05% of the 67 s a `(symbol, horizon)` configuration spends reading.
- **Later calls:** a dict lookup. Post-reveal Phase 4 runs one process per morning, so the whole
  lifetime cost of the predicate for a daily run is 35.6 ms.
- **Unmeasured but bounded:** the blob grows by two lines per sealed look. `cat-file` on a 163 KB
  blob measured the same 35.2 ms as on an absent path, so the cost is subprocess spawn, not blob
  size, for any size this log will reach.

**Cache key and invalidation.** `dict[tuple[dev_end, holdout_start, holdout_end], bool]`, keyed on
the repository's own splits, never invalidated within a process.

- Keyed on the splits because the lift is scoped to them (§4), and because a test that varies the
  configured splits must not inherit another test's answer.
- A cached **False** is fail-closed: a process that started before the reveal commit keeps refusing
  until it is restarted. That is the correct direction and it is cheap to pay — restart the process.
- A cached **True** matches the specified semantics rather than weakening them: the parent design
  makes the transition one-way and irreversible, so a monotone cache *is* the lifecycle. A `git
  reset` mid-process does not re-seal, and nothing in the design says it should.
- No invalidation hook is exported. `_reset_reveal_cache()` is private and can only force a
  re-derivation from git; there is no way, public or private, to set the cache to `True`. §7 W13
  makes the public surface a checked set so a new lever is a failing test.

**Fail-closed, explicitly.** `_git_state`'s `("unknown", None)` fallback is the wrong pattern to
copy here: an audit line that cannot name the commit still beats no line, but a *seal* that cannot
ask git must stay shut. Non-zero return, `OSError`, `SubprocessError`, the 5 s timeout, a detached
or empty HEAD, a directory that is not a repository, an unparseable blob — every one of them is
`False`. Consequence, stated: **an installed copy of `volab` with no git checkout can never read
past `dev_end`.** That is the price of requirement 3 and it is the right price.

**`--no-optional-locks` stays**, copied from `_git_state`'s comment rather than re-derived.
`cat-file` takes no index lock, but the flag costs nothing and the one hour that flag's absence
cost this project is reason enough not to have a second git call site without it.

**HEAD is per-worktree**, and this repository has seven worktrees. The predicate therefore answers
"is the reveal committed in *this* checkout's HEAD". That is the intended meaning: the seal is a
property of the tree the code is running from.

---

## 3. Decision 2 — what a completed reveal record is

**Decision: an ordered pair of append-only lines in `data/holdout_access.jsonl` as git has it at
HEAD — a `reveal_open` line pinning the splits it is a reveal of, and a later `reveal_complete`
line naming the same 32-hex `reveal_id`. The guard parses the committed blob with a pure function;
anything it cannot fully validate is skipped, and a blob with no valid pair means not revealed.**

### The file gains a `kind`, and four record kinds

`AUDIT_FILENAME = "holdout_access.jsonl"` keeps its name and its place
(`settings.storage_root / AUDIT_FILENAME`). Every line gains `schema: 1` and a `kind`.

```jsonc
// kind "look_open" — written and flushed BEFORE the read is attempted
{"schema":1,"kind":"look_open","look_id":"<32 lowercase hex>","reveal_id":null,
 "ts":"2026-11-10T19:04:11.482913+00:00","dataset":"bars_daily","tier":"curated",
 "end":"2026-10-30","start":null,"symbols":["SPY"],
 "caller_module":"volab.reveal","caller_lineno":88,
 "git_sha":"c207c59…","git_dirty":false}

// kind "look_close" — written after the read returns or refuses. No git call.
{"schema":1,"kind":"look_close","look_id":"<same 32 hex>","reveal_id":null,
 "ts":"2026-11-10T19:04:12.004401+00:00","outcome":"success"}

// kind "reveal_open" — the first line of the pair that lifts the seal
{"schema":1,"kind":"reveal_open","reveal_id":"<32 lowercase hex>",
 "ts":"…","dev_end":"2025-12-31","holdout_start":"2026-01-02","holdout_end":"2026-10-30",
 "git_sha":"…","git_dirty":false,"details":{ /* HR-4's evidence, opaque to the guard */ }}

// kind "reveal_complete" — the second line. Marks the record complete.
{"schema":1,"kind":"reveal_complete","reveal_id":"<same 32 hex>",
 "ts":"…","git_sha":"…","git_dirty":false,"details":{ /* HR-4's */ }}
```

`outcome` moves from the single line to `look_close`: a line written before the read cannot know
it. `details` is a free-form object for HR-4's pre-registration content hash, champion
fingerprint, the two trial counts and their delta — **the guard never reads it**, which is the seam
that lets HR-4 add evidence without touching the predicate's contract.

`reveal_id` and `look_id` are `secrets.token_hex(16)`.

### What the guard checks, exactly

`_completed_reveal_in(text, splits)` returns `True` iff there exists an id `X` such that:

1. some line is a JSON **object** with `schema` an `int` in `{1}`, `kind == "reveal_open"`,
   `reveal_id == X` matching `\A[0-9a-f]{32}\Z`, and `dev_end`, `holdout_start`, `holdout_end` all
   present as `YYYY-MM-DD` strings **equal to** `splits.dev_end`, `splits.holdout_start`,
   `splits.holdout_end`; and
2. a **strictly later** line in the same text is a JSON object with `schema` in `{1}`,
   `kind == "reveal_complete"` and `reveal_id == X`.

Everything else is skipped: a blank line, a line that is not JSON, a line that is JSON but not an
object, a missing or unknown `schema`, an unknown `kind`, a malformed id, a missing or
non-ISO date field, a `reveal_complete` whose id has no qualifying earlier `reveal_open`.
**The function never raises.** A corrupt log must not turn into an exception on a read path; it
must turn into "not revealed".

That rules out, by construction:

| attempted record | why it does not lift the seal |
|---|---|
| `reveal_open` only — the process crashed mid-reveal | no `reveal_complete`. This is the parent design's k-th-attempt case: counted, visible, not a lift |
| `reveal_complete` only | no qualifying `reveal_open` earlier in the file |
| complete before open | ordering is required, so the pair cannot be assembled backwards |
| two lines with different ids | the ids must match |
| id that is not 32 lowercase hex | refused; a hand-typed id is not a record |
| truncated or malformed JSON | skipped |
| no `schema`, or `schema: 2` | skipped — a future format re-seals rather than half-opens |
| a record pinning other splits (the rehearsal's carved `dev_end`) | the three dates must equal the repository's |
| a perfect pair in the working tree, uncommitted | the predicate asks `HEAD`, not the filesystem |
| a perfect pair under `VOLAB_STORAGE_ROOT` | same; the predicate never sees `storage_root` |

### What it deliberately does **not** check, and the honest limit

- **`git_dirty` is recorded, not checked.** HR-4 refuses on a dirty tree; duplicating that refusal
  inside the guard would put a second rule in the one function that must not grow them.
- **A hand-written, well-formed, committed pair lifts the seal.** That is not a hole, it is the
  mechanism: the parent design's whole claim is that lifting the seal *requires a commit* —
  visible, reviewable, attributable. The guard defends structurally against partial, crashed,
  mis-scoped and filesystem-only records. Against a deliberate forgery it defends only to the
  extent that the forgery must appear as an attributable commit to a tracked file. Claiming more
  would be false.
- **Reverting the reveal commit re-seals a fresh process.** Measured: after `git revert` of the
  commit that added the record, `cat-file` fails and the predicate returns `False`. "One-way" is
  enforced within a process by the memo and by policy outside it, not against `git revert`. It
  fails closed, so it is harmless — but the record must stay committed for the seal to stay
  lifted, which is itself a reason the evidence stays in the tree.

### Where the predicate looks, and why that path is a constant

```python
REVEAL_RECORD_GIT_PATH: str = "data/holdout_access.jsonl"
_REPO_ROOT: Path = DEFAULT_CONFIG_PATH.parent.parent
```

`git --no-optional-locks -C _REPO_ROOT cat-file blob HEAD:data/holdout_access.jsonl`. The
`HEAD:path` form is resolved from the top of the tree, so one subprocess is enough and `-C` depth
does not matter. `_REPO_ROOT` is derived from `config.DEFAULT_CONFIG_PATH`, which is public and is
found by walking up from the installed package — the same derivation `config._REPO_ROOT` uses, so
the two cannot disagree about which checkout is "the repository".

**The asymmetry between the writer and the predicate is the design, not an oversight.** The writer
appends to `settings.storage_root / AUDIT_FILENAME`; the predicate reads
`HEAD:data/holdout_access.jsonl`. Under the committed `storage.root: "data"` these are the same
file; under `VOLAB_STORAGE_ROOT` or a `tmp_path` root they are not, and that is exactly what closes
the forgery route the parent design found and what makes HR-5's rehearsal unable to lift the real
seal. W14 pins that they coincide under the committed config and only under it.

---

## 4. Decision 3 — three regions, one comparison

**Decision: one bound, computed once per call, from a pure three-line function. `region_of` is used
for the refusal's wording and for the witnesses' table, never for the decision — and a witness
asserts the two can never disagree.**

Step 3 of `load_panel` becomes, in full:

```python
    # 3. The seal, before any filesystem access whatsoever. One bound, one comparison: the three
    #    regions are the three values the bound can take, not three branches.
    past_dev_end = end > resolved.splits.dev_end
    revealed = past_dev_end and _reveal_committed(configured_splits)
    unlocked = not revealed and _unlock_granted(allow_holdout)
    limit = _read_limit(resolved.splits, revealed=revealed, unlocked=unlocked)
    if end > limit:
        region = resolved.splits.region_of(end)
        raise HoldoutViolation(
            f"end {end} is in the {region.value} region (dev_end {resolved.splits.dev_end}, "
            f"holdout_end {resolved.splits.holdout_end}) and this read may see no later than "
            f"{limit}: " + (
                f"reading the holdout window needs both allow_holdout=True and {_UNLOCK_ENV}=1"
                if region is Region.HOLDOUT
                else "the live region is unreachable until a completed reveal record is committed; "
                     "no argument and no environment variable opens it"
            )
        )
    audited = past_dev_end and not revealed
```

and the bound itself:

```python
def _read_limit(splits: Splits, *, revealed: bool, unlocked: bool) -> dt.date:
    """The latest ``as_of`` this call may see. The whole seal, as one date.

    Three regions, three values, and the order is the precedence: a committed reveal makes the
    holdout ordinary history and the live region readable; the two keys open the sealed window and
    **stop at its end**; neither leaves ``dev_end``. Pure and total, so the twelve cells of
    (region x state) are a table a test can assert rather than a shape a reader must trust.
    """
    if revealed:
        return dt.date.max
    if unlocked:
        return splits.holdout_end
    return splits.dev_end
```

Why this shape:

- **One comparison and one `raise`.** `end` is tested against exactly one name. The three-way
  answer lives in the *value* of `limit`, not in control flow, so there is no second code path
  through the function the parent design forbids one in. W12 asserts the comparison and the call
  sites are each exactly one.
- **`region_of` for the message only.** The refusal has to say different things for the holdout
  (the keys apply) and for live (nothing applies), and an operator who reads "needs both keys"
  about a date no key reaches has been told the wrong thing — the same argument
  `daily.plan_for` makes for ordering its refusals. A message is not a code path. W2 asserts the
  message's classifier and the bound never disagree about which dates are sealed, so they cannot
  drift.
- **Short-circuit order is load-bearing.** `past_dev_end and _reveal_committed(...)` keeps git off
  the dev path. `not revealed and _unlock_granted(...)` means `_unlock_granted` is not called at
  all once the reveal is committed, which also keeps the conftest police out of the post-reveal
  path. Neither changes today's behaviour for a dev read: `_unlock_granted` is already skipped
  there by the existing `past_dev_end and not _unlock_granted(...)`.
- **`dt.date.max` for revealed.** Not "today": `as_of` filtering already bounds the answer to data
  that exists, and a clock inside `store.py` would be a new dependency on the one thing
  `volab.daily` went to trouble to admit at a single seam.
- **The `Splits` default keeps hand-built values working.** `conftest.synthetic_store` builds
  `Splits(dev_end=2023-12-31, holdout_start=2024-01-02)`, so `holdout_end` is `dt.date.max` and
  `unlocked` yields `dt.date.max` — today's behaviour, unchanged, for every existing test. A
  witness for the live region must therefore pass an explicit `holdout_end`; there is no live
  region without one.

### `configured_splits`, and a new step 0b

`revealed` is asked about the **repository's** configuration, not the caller's: the reveal is a
property of the repository. `load_panel` already resolves `load_settings()` on both branches of
step 0, at 0.63 ms, so this is free:

```python
    if settings is None:
        resolved = configured = load_settings()
    else:
        resolved = settings
        configured = load_settings()
        if resolved.splits.dev_end > configured.splits.dev_end:
            raise HoldoutViolation(...)            # step 0, unchanged
        if resolved.splits.holdout_end > configured.splits.holdout_end:
            raise HoldoutViolation(...)            # step 0b, NEW
    configured_splits = configured.splits
```

**Step 0b is new and it is necessary.** `holdout_end` has just become load-bearing for read
permission, which it was not when P4-05 landed it. Under the bound above, a caller who hands over
a `Settings` with `holdout_end = dt.date.max` and holds the two keys reads live data before the
reveal — the exact failure requirement 1 forbids, reached without any key being added. Step 0
blocks the same trick on `dev_end`; step 0b blocks it on `holdout_end`.

Note the direction is **opposite** to `config._refuse_an_earlier_holdout_end`, and both are right
in their own place. config protects "a date the committed file seals must not silently become
live"; `store` protects "the window the keys open must not extend past the sealed window". A
builder who copies either rule into the other place will get it backwards, exactly as the parent
design warned for `dev_end`. The error message must say which direction it is refusing and why.

**The residual hole, which `store.py` cannot close — see §9, Q2.** `VOLAB_SPLITS_HOLDOUT_END` may
move `holdout_end` *later*, by P4-05's ratified rule, and config's refusal compares against the
file's literal. So the environment can still widen what the two keys reach, because `load_settings()`
itself reports the overridden value and step 0b compares against that. Closing it means changing
config to refuse the variable in **both** directions, which is a one-line change to a ratified rule
and belongs to its own story, not to HR-3.

---

## 5. Decision 4 — the two-key unlock: superseded, not replaced, and narrowed

**Decision: the two keys stay, narrowed from "anything past `dev_end`" to "the holdout region
only". They are the sole pre-reveal route into the sealed window and they never reach live. A
committed completed reveal record supersedes them: afterwards the bound is `dt.date.max` and
nothing needs a key. They are not an alternative to the reveal — they are the key the reveal
turns.**

| `end`'s region | before a committed reveal | after |
|---|---|---|
| dev | free, unaudited | free, unaudited |
| holdout | **two keys**, and every look is logged before it is taken | free, unaudited |
| live | **unreachable by any route** | free, unaudited |

### Why the keys cannot simply be retired, which the parent design's wording does not allow for

The parent design's anti-goal #1 says the key pair "is a developer key for tests, not a reveal".
Taken literally with "the seal lifts on a **completed** record", it is not implementable: **the
reveal must read the holdout before its record can be completed**, so a seal that lifts only on a
completed record cannot authorise the reads that complete it. Something has to open the window for
the reveal itself, and the parent design's own protection 5 names it — the keys, with the look
logged first.

So the correct statement, which I propose as the amendment to that anti-goal, is: *the keys open
the sealed window for one counted, logged look at a time; they never lift the seal for anyone else,
they never reach live, and what retires them is a committed record of a look that completed.* The
anti-goal survives intact in substance — no flag and no environment variable lifts the seal, because
nothing the keys do changes what the *next* process may read. Only the commit does that.

And narrowing the keys to the holdout buys something specific and valuable: **the reveal itself
cannot see live data.** Its reads are bounded at `holdout_end` by the same bound that bounds
everyone else's. That is precisely what protection 3's credibility rests on — the champion being
fixed before the live data existed — and today the keys would let the reveal read straight through
to today.

### Consequence for the autouse fixture, which is the question behind the question

`tests/conftest.py::_forbid_holdout_unlock` arms `_police_holdout_read` with
`load_settings().splits.dev_end` and fails any unmarked test whose read past that boundary
*returns*. Nothing about HR-3 changes that, because pre-reveal the predicate is `False` and the
boundary is still `dev_end`.

But on the day the real record is committed, every unmarked test in the suite would start reading
past `dev_end` freely and the fixture's guarantee would evaporate — and worse, with an unscoped
lift, `synthetic_store`'s `dev_end` of 2023-12-31 would unseal too. Two things prevent that, and
HR-3 owns both:

1. **The lift is scoped to the splits it reveals** (§3): a record pinning `dev_end 2025-12-31`
   says nothing about a `Splits` whose `dev_end` is 2023-12-31, so `synthetic_store` stays sealed
   forever regardless of what is committed. This is why `reveal_open` carries all three dates.
2. **The suite is made reveal-invariant**, following the `_unlock_granted` precedent exactly.
   `_reveal_committed` is a plain module-level function, called by name and never aliased or bound,
   for the same documented reason `_unlock_granted` is; and `_forbid_holdout_unlock` adds, for an
   unmarked test:

   ```python
   monkeypatch.setattr(store, "_reveal_committed", lambda splits: False)
   ```

   An unmarked test therefore sees the window sealed whatever HEAD says, so the suite's meaning does
   not change on the day the reveal is taken. This **strengthens** the fixture — it removes a
   dependency on git state that the fixture does not currently know it has. Tests of the predicate
   itself call `_reveal_committed_in` or `_completed_reveal_in` directly and are untouched by the
   police, which is why those two exist as separate functions.

### Two existing tests change shape, and here is exactly how

Both are in `tests/test_store_guard.py`, both `@pytest.mark.holdout`, both on a `tmp_path` root.
Neither is being weakened; both are being re-pinned to the new record contract.

- `test_both_keys_return_holdout_rows_and_write_one_audit_line` (line 257). `assert len(lines) == 1`
  becomes `== 2`; the exact key set becomes `look_open`'s; `record["outcome"] == "success"` moves to
  the second line. Rename to `…_and_write_an_open_and_a_close_line`. It must additionally assert the
  **order**: line 1 is `look_open`, line 2 is `look_close`, and the two share one `look_id`.
- `test_refused_unlocked_read_still_writes_one_audit_line` (line 448). Same: two lines, the close
  line carrying `outcome == "DatasetNotFound"`. Its docstring's reasoning — that a key-holder must
  not be able to probe the sealed window while the log's count stays clean — is unchanged and
  strengthened, because the open line is now written even if the close line's write fails.

### The line count the write-up reports changes meaning, and this is a real cost

`.gitignore`, `docs/superpowers/plans/2026-10-19-phase0-skeleton.md:98` and
`docs/superpowers/phase0-decisions.md:69` all say the audit log's **line count** is a number the
write-up must report. With two lines per look that count is no longer the number of looks. The
replacement is `count(kind == "look_open")`, which is why `kind` is on every line.

This is a trade, not a free change, and it is worth it: the old count was already wrong in the one
case that matters most. A read that crashes between step 4 and step 12 leaves a line today only if
the `contextlib.suppress`-wrapped append in the `except` block succeeds, and a hard kill leaves
nothing at all. `count(look_open)` is the true number of looks taken, including the abandoned ones —
which is what "k logged looks, the k-th completed" needs.

### Post-reveal reads are not audited, deliberately

`audited = past_dev_end and not revealed`, so after the reveal the holdout and live are ordinary
history and reads of them write no lines. That is the parent design's own words ("the data then
becomes ordinary history"), it keeps the log a record of *sealed* looks so its count keeps meaning
what it means, and it avoids 33 ms of git per read forever (two subprocesses in `_git_state`). The
ongoing record of live reads is protection 3's forecast log, HR-4's, not this one's.

---

## 6. The exact interfaces HR-3 implements

All in `src/volab/store.py` unless stated. New stdlib imports: `contextvars`, `re`, `secrets`, and
`Iterator`/`Mapping` from `collections.abc`. New imports from `volab.config`:
`DEFAULT_CONFIG_PATH`, `Region`, `Splits`. All numpy-free, scipy-free, arch-free; nothing here
touches Parquet.

```python
# ---- constants -------------------------------------------------------------------------------
AUDIT_FILENAME: str = "holdout_access.jsonl"          # unchanged
AUDIT_SCHEMA: int = 1                                  # written on every line
_KNOWN_AUDIT_SCHEMAS: frozenset[int] = frozenset({1})  # what the guard will parse
REVEAL_RECORD_GIT_PATH: str = "data/holdout_access.jsonl"   # root-relative, NOT storage_root
_REPO_ROOT: Path = DEFAULT_CONFIG_PATH.parent.parent
_ID_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")
_REVEAL_CACHE: dict[tuple[dt.date, dt.date, dt.date], bool] = {}
_REVEAL_SCOPE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "volab_store_reveal_scope", default=None
)

# ---- the bound -------------------------------------------------------------------------------
def _read_limit(splits: Splits, *, revealed: bool, unlocked: bool) -> dt.date: ...

# ---- the predicate, in three layers so each is testable at its own level ---------------------
def _completed_reveal_in(text: str, splits: Splits) -> bool:
    """Pure. Does ``text`` hold an ordered, well-formed reveal pair scoped to ``splits``?
    Never raises; anything unparseable is skipped."""

def _reveal_committed_in(repo_root: Path, splits: Splits) -> bool:
    """One ``git cat-file`` against ``repo_root``'s HEAD, then ``_completed_reveal_in``.
    Every failure mode is False. Takes a root for testability; ``load_panel`` never calls it."""

def _reveal_committed(splits: Splits) -> bool:
    """Memoised ``_reveal_committed_in(_REPO_ROOT, splits)``.

    A plain module-level function, called by name and never bound or aliased anywhere, for the
    reason ``_unlock_granted`` is: the suite's policing fixture replaces it and must be sure
    every question about the seal's state goes through its replacement."""

def _reset_reveal_cache() -> None:
    """Forget the memo, forcing the next question back to git. Private, and it can only cause a
    re-derivation: there is no way, public or private, to set the answer to True."""

def reveal_is_committed() -> bool:
    """Whether this checkout's HEAD holds a completed reveal record for the repository's own
    splits. Public on ``git_state``'s precedent: it runs ``git`` and reads no Parquet, so the
    import ratchet's reasoning does not reach it. Read-only; it lifts nothing."""
    return _reveal_committed(load_settings().splits)

# ---- the audit log: one writer, and nothing else may name the file ---------------------------
def _append_audit(settings: Settings, record: dict[str, Any]) -> None:
    """Append one JSON object and flush it to the kernel, then fsync. The ONLY function in this
    package that names ``AUDIT_FILENAME`` or opens the log."""

def _open_look(
    settings: Settings, *, dataset: str, tier: Tier, end: dt.date,
    start: dt.date | None, symbols: Sequence[str] | None,
) -> str:
    """Write and flush the ``look_open`` line and return its ``look_id``. Runs ``_git_state``."""

def _close_look(settings: Settings, *, look_id: str, outcome: str) -> None:
    """Write and flush the ``look_close`` line. Runs no git: the sha is on the open line."""

# ---- the reveal record, written by HR-4, shaped here -----------------------------------------
def record_reveal_opened(settings: Settings, *, details: Mapping[str, Any]) -> str:
    """Write and flush the ``reveal_open`` line, pinning ``settings.splits``, and return the
    ``reveal_id``. ``details`` is HR-4's evidence and is opaque to the guard."""

def record_reveal_completed(
    settings: Settings, reveal_id: str, *, details: Mapping[str, Any] = MappingProxyType({})
) -> None:
    """Write and flush the ``reveal_complete`` line for ``reveal_id``."""

@contextlib.contextmanager
def reveal_scope(reveal_id: str) -> Iterator[None]:
    """Attribute every look taken inside to ``reveal_id``.

    A context manager rather than a ``load_panel`` parameter, for two reasons. HR-4's reads go
    through ``volab.panel.load_curated_panel`` and ``volab.walkforward.load_targets``, so a
    parameter would have to be threaded through four more signatures; and the guarded function's
    signature stays exactly as it is. It labels lines and changes no bound. Re-entry with a
    different id raises ``RuntimeError``: one reveal event is one scope, and a nested second id
    would silently mislabel reads."""
```

### Where the lines go in `load_panel`

Only step 3 and the audit calls change. Steps 0–2 and 4–13 keep their order, their messages and
their reasoning; the module docstring's numbered list gains 0b and renames 13.

```python
    ...                                             # step 3 exactly as written in §4
    look_id = _open_look(resolved, dataset=dataset, tier=tier, end=end,
                         start=start, symbols=symbols) if audited else ""
    try:
        ...                                         # steps 4-12, untouched
    except Exception as exc:
        if audited:
            with contextlib.suppress(Exception):
                _close_look(resolved, look_id=look_id, outcome=type(exc).__name__)
        raise
    if audited:
        _close_look(resolved, look_id=look_id, outcome="success")
```

Three orderings in there are decisions, not style:

- **`_open_look` precedes the `try`,** so it precedes step 4 and therefore any filesystem access.
- **`_open_look` is NOT wrapped in `suppress`.** A look that cannot be logged **does not happen**:
  the exception propagates and the read never runs. This is a strengthening and it is the direction
  the anti-goal "an unlogged look" demands. The existing asymmetry is preserved exactly — the
  `except` path stays suppressed so an unwritable log cannot displace the refusal the caller needs
  to see, and the success path stays unsuppressed.
- **A step-3 refusal writes nothing,** as today: nothing was read, no filesystem was touched, and
  padding the log with reads that saw nothing would dilute the count the write-up reports — which is
  the ruling in `docs/superpowers/phase0-decisions.md:69`, preserved.

`fsync` costs 0.070 ms against 0.053 ms for `flush()` alone and 0.048 ms for neither, so it is in.
It is not what the crash witness proves (see W5) — it buys durability across power loss, where
`flush()` alone buys durability across process death.

---

## 7. Decision 5 — the witnesses

Instance witnesses first, then the shape witnesses, which are the ones that answer "there is no
second route". R134's distinction is the reason the second group exists: a deleted route can be
reinstated under another name, so the witness that holds is the one that refuses the *shape*. The
precedents are `tests/test_import_discipline.py` (AST over the package) and
`tests/test_compare.py::test_no_module_level_table_in_cohorts_is_keyed_by_a_contestant_and_a_horizon`
(runtime introspection over a module's globals). Both patterns are used below.

### Live is unreachable before the reveal

- **W1 `test_the_read_limit_is_the_whole_table`** — `_read_limit` parametrised over three regions ×
  four `(revealed, unlocked)` pairs = 12 cells against an explicit expected table, including
  `(True, True)` which `load_panel` never produces and which `_read_limit`, being total, must still
  answer (revealed wins). This is the primary witness for decision 3 and it needs no store, no git
  and no keys.
- **W2 `test_the_limit_and_the_region_never_disagree`** — for `dt.date.min`, `dt.date.max`, and
  each of `dev_end`, `holdout_start`, `holdout_end` ± 1 day, the set of dates `_read_limit` admits
  is exactly the union of the regions the state permits, as `Splits.region_of` classifies them.
  This is what stops the refusal's wording and the bound from drifting apart.
- **W3 `test_live_is_unreachable_before_the_reveal_even_to_a_caller_holding_both_keys`** —
  `synthetic_store`, `Splits(dev_end=2023-12-31, holdout_start=2024-01-02,
  holdout_end=2024-12-31)`, `monkeypatch.setattr(store, "_unlock_granted", lambda _: True)`,
  `end=2025-01-02` → `HoldoutViolation` whose message contains `"live"` and not
  `"allow_holdout"`. Asserts also that the audit file does not exist (a step-3 refusal is not a
  look) and that `os.environ.get(UNLOCK_ENV) is None` — the test proves the property without ever
  holding a key. See §9 Q1 on why it is written this way.
- **W4** — the reshaped `test_both_keys_return_holdout_rows_…`, which keeps the positive half: the
  holdout *is* reachable with the keys before the reveal, and leaves an ordered pair of lines.

### A forged, partial or mis-scoped record does not lift the seal

- **W5 `test_a_partial_or_forged_log_is_not_a_completed_reveal`** — a parametrised table over
  `_completed_reveal_in(text, splits)` with one row per line of §3's table, every one `False`, plus
  the one well-formed pair → `True`, plus a well-formed pair preceded by junk and look lines →
  `True`. Pure, instant, exhaustive over the shapes the guard names.
- **W6 `test_the_predicate_asks_git_and_not_the_filesystem`** — build a temp repo (measured: init,
  commit and three predicate calls total 182 ms), write a perfect pair, assert
  `_reveal_committed_in(root, splits) is False` while uncommitted; `git add -f` and commit with
  `-c user.email=… -c user.name=…`; assert `True`. Verified to behave exactly so.
- **W7 `test_a_record_under_another_storage_root_does_not_lift_the_seal`** — a perfect pair written
  into a `tmp_path` used as `settings.storage_root`; the seal holds. This is HR-5's rehearsal in
  miniature and it is the witness for the forgery route the parent design found.
- **W8 `test_a_record_scoped_to_other_splits_does_not_lift_the_seal`** — a committed, perfect pair
  pinning `dev_end 2025-12-31` against a `Splits` whose `dev_end` is 2023-12-31 → `False`. This is
  what keeps `synthetic_store` sealed after the real reveal.
- **W9 `test_every_git_failure_leaves_the_window_sealed`** — the subprocess runner patched to raise
  `TimeoutExpired`, `OSError`, `SubprocessError`, and to return a non-zero code; plus
  `_reveal_committed_in(tmp_path, splits)` on a directory that is not a repository. All `False`,
  none raising.

### The log line survives a crash mid-read

- **W10 `test_the_open_line_survives_a_sigkill_inside_the_read`** — a subprocess that builds a
  `tmp_path` store, replaces `store._scan` with a function that `os.kill(os.getpid(), SIGKILL)`s,
  and attempts a past-`dev_end` read. The parent asserts the log holds **exactly one** line, its
  `kind` is `look_open`, and no `look_close` exists.

  **The mutant this discriminates is the order, not the flush.** Measured both arms directly:

  ```
  log-before-read: rc=-9  lines=1  look_open present  look_close absent
  log-after -read: rc=-9  lines=0  look_open absent   look_close absent
  ```

  An implementer who moves the append after the read fails this test. (An implementer who drops
  `fsync` does not, and should not: with each append in its own `with` block the flush happens at
  close either way. `fsync` is for power loss and has no cheap witness, so it is pinned by W16's
  shape check instead.)
- **W11 `test_a_look_that_cannot_be_logged_does_not_happen`** — `_append_audit` patched to raise on
  the first call; assert the read raises and that `store._scan` was never called.

### There is no second route — shape witnesses

- **W12 `test_the_seal_is_one_comparison_against_one_bound`** — AST over `src/volab/store.py`:
  inside `load_panel`, exactly one `Compare` whose left is `Name("end")` with a `Gt` op against
  `Name("limit")`; and in the whole module, exactly one call site each of `_read_limit`,
  `_reveal_committed` and `_unlock_granted`.
- **W13 `test_nothing_but_git_and_the_splits_decides_the_seal`** — AST over the bodies of
  `_read_limit`, `_completed_reveal_in`, `_reveal_committed_in` and `_reveal_committed`: no
  reference to `os.environ`, `os.getenv`, `storage_root`, `AUDIT_FILENAME` or `Settings`, and no
  parameter annotated `Settings`. This is the checked form of requirement 3 and of anti-goal #1.
- **W14 `test_only_one_function_opens_the_audit_log`** — `AUDIT_FILENAME` is referenced in exactly
  one function body in `store.py`, and no module under `src/volab` other than `store.py` names it.
  So HR-4 cannot grow a second writer with a different ordering.
- **W15 `test_no_public_name_can_change_the_seals_state`** — `{n for n in vars(store) if not
  n.startswith("_")}` equals an explicit frozen set. A new public lever is then a failing test
  rather than a review that has to notice it. Pattern taken from the `(str, int)`-keyed-dict
  witness: the shape is refused, not one instance of it.
- **W16 `test_the_predicates_path_is_what_git_actually_tracks`** — `REVEAL_RECORD_GIT_PATH` appears
  as a `!`-negation in `.gitignore`; under the committed `config/base.yaml`,
  `load_settings().storage_root / AUDIT_FILENAME == _REPO_ROOT / REVEAL_RECORD_GIT_PATH`; and the
  git call's argv contains `--no-optional-locks`, `cat-file` and the `HEAD:` form, with no
  `rev-list`, `log` or `--all`. Writer and predicate coincide under the committed config and only
  under it, which is both the correctness property and the rehearsal's isolation.
- **W17 `test_the_suite_is_sealed_whatever_head_says`** — with the police installed, an unmarked
  test's past-`dev_end` read is refused even when `_reveal_committed_in` would return `True`;
  and `store._reveal_committed` is the police's replacement for the duration of an unmarked test.
- **W18 `test_a_reveal_scope_labels_the_looks_it_encloses`** — inside `reveal_scope(id)` the look
  lines carry `reveal_id == id`; outside, `null`; re-entry with a different id raises
  `RuntimeError`.

---

## 8. Decision 6 — what HR-3 must not do

A refusal list, so the implementer has one. Each item is something a reasonable builder might do.

1. **Must not add any second route to the lift.** No `VOLAB_*` variable, no keyword argument, no
   CLI flag, no module-level toggle, no file outside git, no "rehearsal mode". The predicate's only
   inputs are this checkout's HEAD and the repository's `Splits`.
2. **Must not read the real holdout.** No `allow_holdout=True` against `data/`, no setting or
   reading of `VOLAB_HOLDOUT_UNLOCK`, no new `@pytest.mark.holdout` (see §9 Q1), no read with
   `end > 2025-12-31` against the repository's store. If HR-3 finds itself about to, it stops and
   reports, as the board says.
3. **Must not create or commit `data/holdout_access.jsonl`.** Every record it writes goes under
   `tmp_path`. Creating the real file would lift nothing (it would be uncommitted) but would dirty
   the tree and would be the first line of a log whose count is a reported number.
4. **Must not give the predicate a `Settings`, a `storage_root`, or a caller-supplied root.**
   `_reveal_committed_in` takes a root for testability only; `load_panel` reaches git only through
   `_reveal_committed` and `_REPO_ROOT`. W13 and W12 check this.
5. **Must not add a forecast-only, hole-skipping or "live tail" read path.** One `end`, one window,
   one bound. The parent design rejected this explicitly and says why: it would put a second code
   path through the one function that must never have one.
6. **Must not weaken `tests/conftest.py`.** `_forbid_holdout_unlock`, `_police_holdout_read`,
   `pytest_fixture_setup` and `_clean_volab_environment` all stay. HR-3 *adds* the
   `_reveal_committed` replacement to the first of them; it removes nothing and it does not touch
   the `_unlock_granted` wrapper or the `_names_bound_to` sweep.
7. **Must not touch the import ratchet.** `ALLOWED` stays `{"store.py"}`. No numpy, scipy or arch,
   directly or transitively, and no module outside `store.py` may import any private name from it —
   including every new private helper here. `reveal_is_committed` and `reveal_scope` are the only
   new names other modules may import.
8. **Must not edit `docs/holdout-preregistration.md`.** It is registered at `65617ee` with its hash
   in R130. Nothing in this design requires a word of it to change; if HR-3 finds something that
   does, that is a design defect and HR-3 stops and reports it rather than editing.
9. **Must not change `walkforward.load_for`, or give it an `end` parameter.** Its pin at `dev_end`
   is a *research* boundary, not the seal, and it must stay pinned forever — its docstring says so
   and it is right. The reveal reads through HR-4's own code, not through the shootout's.
10. **Must not edit `src/volab/daily/` or `src/volab/config.py`.** The daily refusal and the
    `VOLAB_SPLITS_HOLDOUT_END` direction are separate stories (§9 Q2, Q3). Step 0b lives in
    `store.py`.
11. **Must not let the predicate raise.** A malformed, enormous, binary or absent log is "not
    revealed". A `HoldoutViolation` or a `JSONDecodeError` escaping the predicate would make a
    corrupt log a failure of the read path.
12. **Must not wrap `_open_look` in `contextlib.suppress`.** A look that cannot be logged does not
    happen. Equally, must not *unwrap* the `suppress` on the `except` path: an unwritable log must
    not displace the refusal the caller needs to see.
13. **Must not re-run `git` for the close line.** That would double today's 33 ms-per-audited-read
    (`rev-parse` 14.5 ms + scoped `status` 18.9 ms) for a sha the open line already carries.
14. **Must not cache the predicate across different splits,** and must not expose any way to set
    the cache to `True`.
15. **Must not use `git log -S`, `git rev-list --all`, `--follow`, or a second subprocess.** HEAD,
    one `cat-file`. Asking all of history would mean a record committed and then reverted — perhaps
    *because* it was wrong — kept the seal open forever.
16. **Must not shrink the audit record.** `ts`, `outcome`, `dataset`, `tier`, `end`, `start`,
    `symbols`, `caller_module`, `caller_lineno`, `git_sha`, `git_dirty` all survive; `schema`,
    `kind`, `look_id` and `reveal_id` are added, and `outcome` moves to the close line.
17. **Must not mark a story done on assertion.** The two reshaped tests in `test_store_guard.py`
    are part of the diff, not a follow-up.

---

## 9. What does not survive contact with the code, and what only the user can settle

### Findings that amend the parent design

- **HR-1 is already done** at `85de6a5`. The parent design's "what is left to the builder" item
  *"Where `holdout_end` lives in `Splits`, its YAML key, and its validation"* is landed, with
  `Region` and `region_of` as a bonus the parent design did not ask for and this design uses.
- **The brief's hot-path premise is wrong** (§1). 120 reads per shootout, all at `end == dev_end`,
  zero predicate calls. The per-origin rebuild truncates in memory.
- **Anti-goal #1's wording is not implementable as written** (§5). The keys are what the reveal
  turns; they cannot be "for tests only" and also be the thing that opens the window the completed
  record is a record of. The substance survives; the sentence needs the amendment in §5.
- **Protection 5's "the file is git-tracked from the moment a read happens" is true only under the
  committed `storage.root`.** Under a `tmp_path` or `VOLAB_STORAGE_ROOT` root the written log and
  the tracked blob are different files. That asymmetry is what makes the predicate forgery-proof, so
  it is a feature — but the sentence overstates and W16 is where the real property is pinned.
- **The audit log's line count stops being the number of looks** (§5). `count(kind == "look_open")`
  replaces it, and is strictly more honest.
- **The parent design's "Phase 4's first genuinely live morning comes after the drawer is opened"
  does not follow from opening it in `store.py`** — see the live bug below.

### The live bug the coordinator asked about: Phase 4 has its own copy of the seal

`src/volab/daily/__init__.py::plan_for` holds its own boundary comparison:

```python
    if as_of > settings.splits.dev_end:
        return Plan(as_of=as_of, outcome="refused", reason=..., stages=())
```

Its docstring already anticipates the shape of the problem — *"Until the holdout reveal has happened
and a `live` region exists (P4-05), **every real date is past `dev_end`**, so this refusal is the
normal outcome rather than an edge case"* — but the code compares against `dev_end` unconditionally.
**After the reveal, `daily plan` will still refuse every real date**, and Phase 4 (built and merged,
#17/#18) will still never have a live morning. `store.py` lifting its seal does not lift this one.

This is a second expression of the same rule, which is the thing to fix rather than to copy. It is
outside HR-3's files, so it needs its own story. Recommended shape, preserving `plan_for`'s purity —
which the module went to trouble for, entering the clock at exactly one seam:

```python
# src/volab/daily/__init__.py
def plan_for(
    settings: Settings, as_of: dt.date, *,
    completed: frozenset[dt.date] = frozenset(),
    revealed: bool = False,                       # NEW, defaulting to the sealed answer
) -> Plan: ...
    # the refusal becomes:  as_of > (settings.splits.holdout_end if revealed else dev_end)
    # and after the reveal there is no upper bound at all, matching store's dt.date.max

# src/volab/daily/__main__.py  -- the single seam, beside _today()
revealed = reveal_is_committed()
```

`plan_for` stays pure and table-testable; `__main__` supplies the answer, exactly as it supplies
`_today()`. `reveal_is_committed` is the public accessor §6 defines, on `git_state`'s precedent,
so the rule has one home. Note also that `volab.ingest.curate` and the features CLI pin
`end=settings.splits.dev_end`; those pins are the same class of thing and that story must decide
which of them are seal pins (to be lifted) and which are research pins (to stay). `walkforward
.load_for` is unambiguously the latter and stays at `dev_end` forever.

### Q1 — for the orchestrator, and it changes HR-3's witness plan

**The brief and the board forbid adding `@pytest.mark.holdout`; the parent design says new reveal
tests use exactly that marker with a `tmp_path` root.** These conflict, and W3, W10 and W11 all
need a past-`dev_end` read on a synthetic root.

**My default, which I have designed the witnesses around: marker-free.** The mechanism is
`monkeypatch.setattr(store, "_unlock_granted", lambda _: True)` with the synthetic store's
`dev_end` and an `end` that is **before the repository's** `dev_end`. That satisfies both rules at
once:

- No key is ever held: no `VOLAB_HOLDOUT_UNLOCK` is set, no `allow_holdout=` is passed, no marker.
  The test can assert `os.environ.get(UNLOCK_ENV) is None` as part of its own contract.
- It is strictly *stronger* than the marked form for W3, because it proves live is unreachable to a
  caller who **does** hold both keys without the test holding them.
- `_police_holdout_read` — the stronger of the two conftest guards, and the one that actually
  protects the real store — stays armed throughout, and it fails any unmarked test whose read past
  the repository's `dev_end` **returns**. W3's read raises; W10's process is killed; W11's read
  raises. None of them can accidentally read the real holdout, and if one ever did, the police
  catches it.

The existing two marked tests stay marked, because they genuinely hold the keys and must. If you
prefer the parent design's reading, the change is one line per witness and the two existing marked
tests are the precedent — but then say so before HR-3 starts, because it is three witnesses'
structure.

### Q2 — for the user: `VOLAB_SPLITS_HOLDOUT_END` is now load-bearing, and later-only is no longer safe

P4-05 ratified "later only", on the reasoning that a later `holdout_end` *"only withholds data"*.
That was true when nothing consumed `holdout_end`. Under §4's bound it is no longer: a later
`holdout_end` turns live dates into holdout dates, and the two keys reach the holdout. So the
environment can widen what the keys reach, pre-reveal, into data that the committed file calls live
— weakening protection 3 exactly where its credibility comes from.

The exposure is narrow: it needs a shell that also sets `VOLAB_HOLDOUT_UNLOCK` and a caller passing
`allow_holdout=True`; `tools/` is AST-scanned for both; the suite's autouse fixtures clear every
`VOLAB_*` override and fail any unmarked test that reaches for a key; and the same person could
edit `config/base.yaml` instead, which `_refuse_a_later_dev_end` already admits. But it is a real
widening and `store.py` cannot close it, because `load_settings()` reports the overridden value and
step 0b can only compare against that.

**Recommendation: a one-line story making `_refuse_an_earlier_holdout_end` refuse the variable in
*both* directions** — `holdout_end` becomes a committed literal, full stop. That is already what
config's own message says for the absent-literal case: *"the end of the sealed window is a committed
literal, not an environment variable."* Cost: a direction that is harmless today stops being
available, and a ratified rule changes. Benefit: the one date that now decides what a key-holder may
see cannot be moved from a shell. Only you or the user can authorise amending a ratified rule, so I
have not assumed it; §4 states the residual hole plainly either way.

### Q3 — for the orchestrator: two new stories

- **HR-7, the daily's own seal** (above). `src/volab/daily/`, `tests/`. After HR-3; independent of
  HR-4 and HR-5, so it can run in parallel with them — its file set is disjoint from both.
- **HR-1b, the `holdout_end` override direction** (Q2), `src/volab/config.py`, `tests/`. One line
  plus witnesses, and it must not run concurrently with anything else in `config.py`.

### Q4 — for the write-up

Confirm that the reported figure becomes "**k** logged looks (`count(kind == "look_open")`), the
k-th completed" rather than the log's raw line count. The gitignore comment,
`docs/superpowers/plans/2026-10-19-phase0-skeleton.md:98` and `docs/superpowers/phase0-decisions.md`
all say "line count", and three documents will want the sentence adjusted when the reveal is taken.
Nothing needs changing now — no line exists yet.

---

## 10. What I deliberately left to the builder

- The exact wording of the refusal messages, beyond the requirement that the live message must not
  mention the keys and the holdout message must, and that step 0b must name its direction.
- Whether `_completed_reveal_in` scans the text once collecting ids or twice; only its contract is
  fixed.
- The mechanics of W6's temp repository — a fixture or a helper in `tests/`.
- How W10's subprocess is spawned and what it asserts beyond "exactly one line, `look_open`, no
  `look_close`".
- `details`' contents, which are HR-4's entirely. The guard never reads them, which is the point.
- Whether `_reset_reveal_cache` is also wired into a conftest fixture for hygiene, or only called
  by the predicate's own tests.
