# The holdout reveal — sprint board

Design: `docs/superpowers/specs/2026-10-27-holdout-reveal-design.md`, ratified. Pre-registration:
`docs/holdout-preregistration.md`, **REGISTERED** 2026-10-28 at commit
`65617eea2e1c95b8cf0b90555810bf64944c9153`, content hash
`c0a095ab3a00711fd435eded709bde4dcdf9f1ab87ab016e92dc7c5cd7323779`, recorded in R130.

**Why now.** `holdout_end` was **2026-10-30**. The window is closed, every registered origin's
target completed inside it, and no data is outstanding. The machinery is the only thing missing.

**Scope: build and rehearse. Not perform.** The reveal is a one-time, irreversible, human-gated
act and the trigger is the user's. A story that reads the real holdout is out of scope, and an
implementer that finds itself about to is to stop and report.

**The consequence that makes this urgent rather than tidy.** Live data (`> holdout_end`) is
**unreachable** before the reveal, by design: a read "to today" passes through the sealed window on
its way, and the design rejected a narrow forecast-only path precisely because it would put a
second code path through the one function that must never have one. So **Phase 4's unattended daily
runs cannot touch live data until this lands.** Phase 4 is already built and merged (#17, #18).

## Stories

| id | story | files | acceptance | state |
|---|---|---|---|---|
| HR-1 | `holdout_end` in `Splits`, with the override direction **reversed** | `src/volab/config.py`, its YAML, `tests/` | `VOLAB_SPLITS_HOLDOUT_END` may move the boundary **later only**; an earlier value is refused. The mirror of `dev_end`, which may move **earlier only** | **already done** `85de6a5` ("P4-05: the live region"), nine days before this board existed. Verified directly: `holdout_end` is 2026-10-30, `Region` is DEV/HOLDOUT/LIVE, `region_of` classifies 2026-11-10 as LIVE, and six witnesses cover both refusal directions and the equal case. The implementer dispatched for it **stopped and reported** rather than rewriting working code |
| HR-2 | Architect: the seal's lifecycle in `store.py` | `docs/superpowers/specs/` | a design for: live unreachable before the reveal, the seal lifting on a **completed reveal record asked of git** rather than a flag, and log-before-read ordering | **done** `ca027b8`, corrected at `c03e7d7` after its own review round. 21 witnesses, 7 of them shape witnesses, and a 19-item refusal list in §8 |
| HR-3 | The seal lifecycle, implemented, **marker-free** | `src/volab/store.py`, `tests/` | three regions enforced **by consuming the `Region` classifier P4-05 already shipped, not a second one**; the record written **and flushed before** the read; a second append-only line marks completion; no other route lifts the seal | todo, after HR-1 and HR-2 |
| HR-4 | The reveal itself: fingerprint, the multi-read span, scoring | new module + CLI, `tests/` | refuses on a dirty tree or a missing pre-registration; records the pre-registration's content hash, the champion's config, git sha, dirty flag and a content hash; spans several reads because derived tiers stop at `dev_end`; retries are **counted, not forbidden** | todo, after HR-3 |
| HR-5 | The dress rehearsal | `tools/` and/or `tests/` | the **whole** procedure runs against a pseudo-holdout carved inside dev, on a **separate storage root**, leaving no mark on the real audit log | todo, after HR-4 |
| HR-1b | `VOLAB_SPLITS_HOLDOUT_END` refused in **both** directions | `src/volab/config.py`, `tests/` | P4-05 permitted a *later* override because it "only withholds data". The seal design makes `holdout_end` the read bound and the two keys reach that region, so a later override turns live dates into holdout dates a key-holder may read pre-reveal. Refused either way; `dev_end` unchanged | in progress (disjoint from HR-3) |
| HR-7 | The daily's **own** seal | `src/volab/daily/`, `tests/` | `daily/__init__.py:117` holds its own `if as_of > dev_end: refused`, so **after the reveal `daily plan` still refuses every real date** and Phase 4 never gets a live morning. `store.py` lifting its seal does not lift that one. `plan_for` stays pure: a `revealed: bool = False` keyword defaulting to the sealed answer, `__main__` supplying `store.reveal_is_committed()` at the seam `_today()` already enters, and the refusal written `if not revealed and as_of > dev_end` — **no upper bound once revealed**. Also triages `ingest.curate`'s and the features CLI's `end=dev_end` pins into seal pins and research pins; `walkforward.load_for` is a research pin and stays at `dev_end` forever | todo, after HR-3. File set disjoint from HR-4 and HR-5, so it may run beside them |
| HR-6 | `delivery-auditor` at sprint close | — | the board and the repository agree | todo, last |

## Serialisation, stated rather than wished away

This is **largely a serial chain**, and pretending otherwise would be the parallelism theatre the
delivery model warns about. HR-3, HR-4 and HR-5 are one security mechanism built in layers: each
needs the one below to exist before its witnesses mean anything. Only HR-1 and HR-2 are genuinely
concurrent, and only because HR-2 writes a document while HR-1 writes `config.py`.

`src/volab/store.py` is the single most dangerous file in the repository — the only module allowed
to touch Parquet, and where the seal lives. **One implementer at a time in it, never two.**

## Constraints every story inherits

- `uv run --locked ...` only. Never bare `uv run` (it re-locks), never `--frozen`.
- `load_panel()` is the only read path and `end` is required on every call.
- **No story may read the real holdout.** Do not set `allow_holdout`, do not set
  `VOLAB_HOLDOUT_UNLOCK`, do not add `@pytest.mark.holdout`. The autouse fixture that fails an
  unmarked test reading past `dev_end` stays, and the rehearsal works by using a **separate storage
  root**, never by disabling a guard.
- `ALLOWED` in `tests/test_import_discipline.py` stays `{"store.py"}`. The numeric ban is
  transitive and walks parent-package `__init__` edges (R127, R129).
- `pytest` runs `filterwarnings = ["error"]`; `mypy` runs `strict = true`.
- Pathspec commits only; `git add -N` for new files. Mutation testing needs exclusive `src/` or a
  scratch copy (R131).
- **The registered document must not change.** Its hash is recorded. A change means a new
  registration and a new look, both counted.

## Expected, and to be reported rather than hidden

R94's refusal rule **should fire on the holdout's own h=66 figure**. The window yields 143 usable
origins there against 208 at h=1, so `n_eff` is thinner than the dev window's. The write-up reports
that refusal; it does not quietly omit the row.


## What HR-1's surprise changes about this board

HR-1 was **already built** before this sprint was boarded, by Phase 4's own P4-05 story. I wrote
the board from the parent design's description of work still to do, and nine days of other
sessions' merges had moved past it. Two lessons, both cheap here and expensive later:

- **Board against the repository, not against a design.** The design is a record of what was
  intended on 2026-10-27; `main` is what is true on 2026-11-10. I had checked that no *reveal*
  machinery existed and inferred that none of its groundwork did either.
- **The parent design's "validation direction" section now describes landed work**, so a reader
  meeting it cold would think it outstanding. HR-2's design should say so rather than leave the
  two documents disagreeing.

The implementer's behaviour is the part worth keeping: told to check first and stop if the work
existed, it checked, stopped, and brought evidence — the commit, an empty diff, the file contents
and the test names. The cost of the wrong board line was one dispatch and no damage.


## Four answers the HR-2 design forced, and one premise of mine it killed

**Q1, the marker ban: resolved marker-free, and the parent design is amended.** My brief and this
board forbade `@pytest.mark.holdout`; the parent design said new reveal tests use exactly it. The
architect found the contradiction and defaulted to marker-free, which is **stronger rather than
merely compliant**: `monkeypatch.setattr(store, "_unlock_granted", lambda _: True)` proves live is
unreachable *to a caller holding both keys* while the test holds none, and
`_police_holdout_read` stays armed throughout. Every marker is a test permanently licensed to read
past `dev_end` — a hole that outlives the story that opened it. The parent's instruction is wrong
and this supersedes it.

**Q2: the later-only override is refused in both directions** (HR-1b). Not a change of taste — a
change of fact. P4-05's reasoning was that a later `holdout_end` "only withholds data", true while
nothing consumed it. The bound consumes it and the keys reach that region, so the environment could
widen what a key-holder sees before any reveal.

**Q3: HR-7 and HR-1b are boarded** above.

**Q4: the reported figure becomes `count(kind == "look_open")`**, "k logged looks, the k-th
completed", not the raw line count — because a look is now two lines. Three documents want the
sentence adjusted when the reveal is taken; nothing needs changing yet, since no line exists.

### The premise of mine a measurement killed

My brief asserted the predicate was on a hot path — "`load_panel` is called once per origin per
contestant, thousands of times in a shootout" — and warned that a git subprocess per call was not
viable. **Measured: false by kind, not degree.** `walkforward.load_for` reads at
`end = settings.splits.dev_end` and nothing else, truncating per origin in memory. A whole shootout
is about **120 reads, every one in DEV, with zero predicate calls**. I had invented pressure that
would have pushed the predicate toward something cheap and forgeable; caching is a footnote. The
measured cost is **0 ms on the shootout path** and 35.6 ms once per process for the reveal,
rehearsal or daily.
