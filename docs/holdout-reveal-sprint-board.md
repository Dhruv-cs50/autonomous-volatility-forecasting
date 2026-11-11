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
| HR-3 | The seal lifecycle, implemented, **marker-free** | `src/volab/store.py`, `tests/` | three regions enforced **by consuming the `Region` classifier P4-05 already shipped, not a second one**; the record written **and flushed before** the read; a second append-only line marks completion; no other route lifts the seal | **done** `b82f138`, with `5322ba0`, `1072968` and `344feb5` on top. Built by one writer while a second was dispatched for it; the second verified rather than rebuilt and found W10 red at `b82f138`. 65 tests in `tests/test_store_seal.py`, W1-W21 plus W22 (a mid-process commit is invisible) and W23 (the cache reset has no production call site) |
| HR-4 | The reveal itself: fingerprint, the multi-read span, scoring | new module + CLI, `tests/` | refuses on a dirty tree or a missing pre-registration; records the pre-registration's content hash, the champion's config, git sha, dirty flag and a content hash; spans several reads because derived tiers stop at `dev_end`; retries are **counted, not forbidden** | **designed** `866cb14`, reviewed `eeab2f7`: `docs/superpowers/specs/2026-11-10-reveal-design.md`. 7 pre-read gates, 3 that cost an attempt, a 38-look span in four stages, 26 witnesses. Implementation **in progress**: the seal side landed at `b5f0730` -- the key threaded through seven readers, `audit_tally` added, and the stub that hid the whole defect fixed. Verified here: 222 tests green across the seal and ratchet files under the stricter stub. `src/volab/reveal/` is being built |
| HR-5 | The dress rehearsal | `tools/` and/or `tests/` | the **whole** procedure runs against a pseudo-holdout carved inside dev, on a **separate storage root**, leaving no mark on the real audit log | todo, after HR-4 |
| HR-1b | `VOLAB_SPLITS_HOLDOUT_END` refused in **both** directions | `src/volab/config.py`, `tests/` | P4-05 permitted a *later* override because it "only withholds data". The seal design makes `holdout_end` the read bound and the two keys reach that region, so a later override turns live dates into holdout dates a key-holder may read pre-reveal. Refused either way; `dev_end` unchanged | **done** `4275e9e`. Found mid-flight with one witness still red (`test_env_overrides_apply_to_every_field` set a later `holdout_end`) and finished rather than reverted: that override is now the file's own literal. 1876 tests green |
| HR-7 | The daily's **own** seal | `src/volab/daily/`, `tests/` | `daily/__init__.py:200` (`:117` before #18 moved it) holds its own `if as_of > dev_end: refused`, so **after the reveal `daily plan` still refuses every real date** and Phase 4 never gets a live morning. `store.py` lifting its seal does not lift that one. `plan_for` stays pure: a `revealed: bool = False` keyword defaulting to the sealed answer, `__main__` supplying `store.reveal_is_committed()` at the seam `_today()` already enters, and the refusal written `if not revealed and as_of > dev_end` — **no upper bound once revealed**. Also triages `ingest.curate`'s and the features CLI's `end=dev_end` pins into seal pins and research pins; `walkforward.load_for` is a research pin and stays at `dev_end` forever | todo, after HR-3. File set disjoint from HR-4 and HR-5, so it may run beside them | **done** `7b0aa79`. `plan_for` gains a keyword-only `revealed: bool = False` and stays pure; the refusal is `if not revealed and as_of > dev_end`, with no upper bound once revealed; `daily/__main__` is the only caller and passes `reveal_is_committed()`. 18 tests in `tests/test_daily_reveal.py`, five mutants killed by hand, and shape witnesses that no argparse flag, env var or `Settings` field can feed `revealed=` |
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


## What HR-3 found, and one lesson this board has now learned twice

**HR-3 was already built when its implementer arrived**, at `b82f138`, by a writer working in this
shared checkout while a second was dispatched for the same story. The second verified it against
HR-2's design instead of rebuilding it — the behaviour HR-1's implementer established — and that
is what caught the two defects below. The cost was one duplicated dispatch and no lost work, but
the board's "one implementer at a time in `store.py`, never two" was held by luck rather than by
bookkeeping: `git log` is the only thing that said so, and it was checked after the fact.

**W10 was red at `b82f138`.** `Splits.holdout_end` is a **required** field — `567616c` ("P4-05b")
removed its `dt.date.max` default on 2026-11-05 — so the SIGKILL child's hand-built `Splits`
raised `TypeError` before it reached the kill. Fixed at `5322ba0`. A story was committed with a
witness failing, which is the thing the delivery model says closes a story and does not.

**HR-2's design was written against a five-day-stale tree, and this is the lesson repeated.** The
board already records, about HR-1, "board against the repository, not against a design". HR-2's
design then reasoned about `Splits.holdout_end` defaulting to `dt.date.max` — §4's whole rationale
for clamping rather than refusing, and §8 item 17 — because this branch was cut from `main` at
`c207c59`, *before* #18 merged `P4-05b`. The design was not wrong when its parent was written; it
was wrong about the tree it would be implemented in. **The clamp is still correct and W19 still
tests it**, but it now guards only the later-override case, not the omitted-field case it was
argued from. §4 and §8 item 17 are stale as written and are superseded by this note.

**`load_panel` is no longer a pure function of its arguments and the store**, and the purity is
removed only past `dev_end`. `revealed = past_dev_end and _reveal_committed(effective)`
short-circuits, so every read at or below `dev_end` asks the predicate zero times and is
bit-for-bit what it was: truncation equivalence, the per-origin fresh interpreters, the extent test
and the byte-identity leakage checks are untouched, which HR-2 measured rather than argued (120
reads in a shootout, every one at `end == dev_end`, zero predicate calls). Past `dev_end` the
refusal depends on this checkout's HEAD, resolved once per process per splits key and memoised, so
a commit landing mid-run is invisible rather than quietly both — W22. The honest limit: two
*different* splits keys in one process resolve at two different moments, and only a test asks
about more than one today.

**Unexplained, and recorded rather than dismissed.**
`tests/test_daily_runner.py::test_a_run_abandoned_mid_stage_leaves_a_start_with_no_completion_and_a_retryable_date`
failed once in a loaded full-suite run and passed alone. Not confirmed flaky — unexplained.


## What HR-7 found, one correction to its own report, and a new story

**Its triage of the `end=dev_end` pins, comments only and no behaviour changed.** Seal pins, to be
lifted by a later story: `ingest/__main__._curate` (it decides which rows enter the curated tier),
and `features/__main__`'s `_exogenous`, `_bars` and `_build`'s `through=` (features are derived
from curated rows, so a live-region feature exists only if these lift). Research pins, staying at
`dev_end` forever: `walkforward.load_for`, untouched, and — found outside the brief —
`evaluators/__main__`, `compare/__main__` and `walkforward/__main__`, whose scoring and
walk-forward reads are research boundaries. `ingest/curate.py` pins nothing itself.

**One claim in HR-7's report is wrong, and checking it rather than relaying it is the point.** It
reported that because `main()` now calls `reveal_is_committed()` on every invocation, existing
`daily_cli.main` tests shell out to git, and that on reveal day any of them expecting a live date
to be refused would flip to `work`. Neither holds. `reveal_is_committed()` calls
`_reveal_committed` **by name inside `store`**, which is exactly what HR-3's conftest stub
replaces for an unmarked test. Verified directly rather than argued: inside an unmarked test
`store._reveal_committed.__qualname__` is `_forbid_holdout_unlock.<locals>.<lambda>`, so the
public accessor returns `False` from the stub and spawns no subprocess. The suite's meaning does
not change on reveal day and no git runs — which is the property HR-3 added that stub for, now
confirmed to reach one module further than the story that added it tested.

| id | story | files | acceptance | state |
|---|---|---|---|---|
| HR-8 | A revealed date past the calendar's last session | `src/volab/daily/`, `tests/` | HR-7 removed the upper bound, correctly. A consequence: with `revealed=True`, an `--as-of` past the XNYS calendar's last session (2027-11-10) now reaches `is_session` and raises `DateOutOfBounds` instead of returning a plan. Before HR-7 it was refused before the calendar was asked. Not a seal hole and not reachable by a daily run on today's date, but a traceback where an outcome belongs. Turn it into a `no_session` or `refused` outcome with the calendar's bound named | todo, found by HR-7 and deliberately left outside it |


## What HR-4's design found, three of them verified here before being written down

**The blocker, and it would have shipped green.** Of the reveal's 38 sealed reads, exactly one is a
direct `load_panel` call; the other 37 go through `panel.load_bars`, `panel.load_exogenous`,
`walkforward.load_targets` and `ingest.curate_*`, **none of which takes an `allow_holdout`
parameter** — verified: their signatures are `(settings, symbol, *, end)` and the only mentions of
the keyword under `src/volab/` outside `store.py` are prose in `config.py` and `daily/__init__.py`.
So a `take` built to either design as written would be refused on its first raw read, every time.
HR-4 therefore threads a keyword-only `allow_holdout: bool = False` through those signatures.

**And HR-3's own witness stub would have hidden it.** `tests/test_store_seal.py:88` stubs
`monkeypatch.setattr(store, "_unlock_granted", lambda _: True)` — verified by reading the line. The
underscore **discards the argument**, so a witness using it goes green whether the caller passed the
key or not: every span witness would have passed against production code that never threaded it.
The stub becomes `lambda allow_holdout: allow_holdout`, and this is the lesson worth keeping from
the whole sprint: **a permissive stub copied from the story below can hide the defect the story
above introduces.** Nothing was wrong with HR-3; the stub was adequate for what HR-3 tested and
inadequate for what HR-4 needs, and only HR-4 could find that out.

**The window is not ingested, so the reveal cannot be taken yet.** Read from partition
**directory names only**, no value read: `data/raw/nasdaq_bars/SPY` ends at `date=2026-10-20`,
`cboe_vix/VIX` at `2026-10-20`, `fred/DGS10` at `2026-10-22`. `holdout_end` is **2026-10-30**, so
roughly seven sessions are missing and `python -m volab.ingest` must run first. The design makes
this gate `window_not_ingested`, which costs an attempt rather than being free, and that is correct:
finding out requires a read.

### Two questions for the user, and the first one is ordered

**OQ1 — may a contestant whose significance R115 withholds be the champion?** The registration's §3
names only "the member of the 10% model confidence set with the lowest QLIKE at h=1 on SPY". Its §1
and §2 withhold every claim for `egarch11_skewt` at h=1 *and* call it "the contestant currently
leading h=1 QLIKE". The document does not distinguish the two readings, so `champion_from` returns
`AMBIGUOUS` and `take` exits 13 rather than choosing. **This must be settled before the reveal is
taken, not after:** exit 13 still writes `reveal_complete`, and once that is committed the seal is
lifted and both candidates' numbers are on screen, so any ruling made afterwards is selection on
the numbers by construction.

**OQ2 — when may it run?** The registration's §1 and the ratified parent design say "once the
window has closed"; the registration's own closing paragraph and R130 say "`holdout_end` plus each
horizon's tail", about 2027-02-05. The implemented gate takes the former, and that is safe only
because the choice is **figure-neutral**: the read is bounded at `holdout_end` and `load_panel`
filters on `as_of`, so a target stamped in 2027 is invisible whenever the read happens. Waiting
three months changes no number.

Four further open questions (an unregistered `minimum_training` that provably cannot bind, whether a
holdout origin may train on earlier holdout sessions, the grouping of "the ranking on each loss",
and an arithmetically vacuous refusal threshold) are stated in the design and left unresolved there,
which is the correct outcome rather than a gap.


## The two rulings the user took, and two the review round forced

`docs/superpowers/holdout-reveal-decisions.md` holds all four with their reasoning. In brief:

- **R216** (the user's, 2026-11-11, **before any holdout number existed**): a contestant whose
  significance R115 withholds **is** eligible to be champion. §3 is read literally, and the
  withheld significance restricts what may be *claimed about* a contestant rather than whether it
  can be *named*. The ordering is the substance, not the ceremony: `take` exits 13 on an ambiguous
  champion but still writes `reveal_complete`, so a ruling taken afterwards would be selection on
  the numbers by construction.
- **R217** (the user's): the reveal may be taken **once the window has closed** -- §1's reading,
  not the closing paragraph's "plus each horizon's tail". Safe because figure-neutral: the read is
  bounded at `holdout_end` and `load_panel` filters on `as_of`, so a target stamped in 2027 is
  invisible whenever the read happens.
- **R218**: a `take` that read the holdout and then failed **still lifts the seal**. Withholding the
  completion line would leave the machinery sealed while the data was seen, and the next run would
  present itself as the clean first read with a log saying so -- an awkward truth turned into a
  false provenance claim.
- **R219**, which corrects an instruction of mine: the sentence saying whether the seal is lifted is
  **derived** from `audit_tally()` and `reveal_is_committed()`, never keyed on the exit code -- exit
  1 covers an exception raised *before* the first read, and a code-keyed message would tell an
  operator their one-time read was spent when it was not. Four states, and the third (both lines on
  disk, not yet committed) is where **every** successful `take` ends.

## Phase 5's two requirements for the reveal, one of them corrected

Phase 5 (the agent layer, merged in #20) passed on two items it had held for Phase 6. Neither is in
this branch, and the reason is the lesson this board has now learned three times: **this branch was
cut from `main` at `c207c59`, before #20 merged**, so `src/volab/counts.py` does not exist here at
all (`git cat-file -e origin/main:src/volab/counts.py` succeeds; the local path does not). `main`
has since moved to `842ed35` with #23. `origin/main` must be merged in before either item is acted
on — after the implementer in `src/volab/reveal/` commits, never over its uncommitted tree.

**Item 1, accepted with a refinement.** Phase 5 writes agent trials to `data/agent_trials.jsonl`
and agent comparisons to `data/agent_comparisons.jsonl`, separately, to honour R139 — nothing
appends to `trials.jsonl` before the reveal. Both are empty today but a sprint could fill them. The
refinement: the reveal reports the **registered pair exactly as registered** (`trials.jsonl` 414,
`comparisons.jsonl` 52), because §4 names those two files and a second count that silently changed
its denominator would make the delta meaningless — **and alongside it** `counts.count_all(root)`'s
total, labelled as a disclosed superset with its reason. Both numbers, and no amendment to a
registered document.

**Item 2's premise is wrong, in Phase 5's own favour, and it was checked rather than relayed.** The
claim was that holdout comparison rows without a `last_origin` would make R208 stop holding "and no
test notices". `origin/main:src/volab/agent/ledger.py:68` **fails closed**: its docstring says *"A
line with no `last_origin` is excluded and counted"* and the code is `if "last_origin" not in row:`
→ excluded, exclusion counted. So such a row cannot reach an LLM prompt; it is dropped and the drop
is visible. The real consequence is a **completeness** gap, not a leak — and no agent reads
`comparisons.jsonl` at all, because R183 gave the agent its own. Phase 5 has since agreed. So
`last_origin` is worth adding, and it does not block the reveal.

**Item 3, already satisfied by construction and verified.** Holdout rows must land in
`trials.jsonl`, never `data/agent_trials.jsonl`, or every sprint would log them as integrity
anomalies. The reveal writes through `JsonlSink` (`compare/sink.py:40`, `data/comparisons.jsonl`)
and `walkforward`'s `TRIAL_LEDGER` (`data/trials.jsonl`), and **nothing in this tree names the agent
ledgers at all**. Nothing to do; recorded so a later change cannot quietly break it.

| id | story | files | acceptance | state |
|---|---|---|---|---|
| HR-9 | Phase 5's ledger items | `src/volab/reveal/`, `tests/` | after merging `origin/main`: the reveal's fingerprint reports the registered pair **and** `count_all`'s total as a labelled superset; the reveal's comparison rows carry a `last_origin` so the agent's R208 filter discriminates rather than blanket-excluding them | todo, blocked on the merge |
