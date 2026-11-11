# The reveal itself — fingerprint, the multi-read span, scoring (HR-4) — design

**Status:** design only. No source file is edited by this story, and **no holdout datum was read to
write it.**
**Date:** 2026-11-10
**Story:** HR-4 on `docs/holdout-reveal-sprint-board.md`. Implemented by a later story.
**Branch:** `holdout-reveal`, at `5af8552` (HR-3 landed `b82f138`…`344feb5`, HR-7 landed `7b0aa79`).
**Registered:** `docs/holdout-preregistration.md`, REGISTERED 2026-10-28, `sha256`
`c0a095ab3a00711fd435eded709bde4dcdf9f1ab87ab016e92dc7c5cd7323779` — **verified with `shasum -a
256` in this checkout while writing this.** Its §1, §2 and §3 are the complete list of what the
reveal computes and claims. Nothing here adds to that list.
**Parents:** `docs/superpowers/specs/2026-10-27-holdout-reveal-design.md` (ratified; protections
1–5 are requirements) and `docs/superpowers/specs/2026-11-10-seal-lifecycle-design.md` (HR-2,
**now implemented** — where its design and the landed code disagree, §11 names the disagreement
and the code wins).

---

## 0. What this document did not do, said in my own words

This design builds the trigger. It does not pull it, and neither does the implementer.

Nothing described below was executed against `data/`. While writing it I ran `shasum -a 256` on the
pre-registration, `wc -l` on the two committed ledgers, `git log`, `git status`, and `ls` over the
store's **partition directory names** — that last one to establish how far each tier is ingested,
because which tiers the reveal has to derive is a load-bearing fact and the parent design's answer
to it was nine days old (HR-2's lesson: board against the repository, not against a design). A
directory name is not a datum: it says that a file for `date=2026-10-20` exists, not what is in it.
No `load_panel` call was made at any `end` past `dev_end`, `allow_holdout` was never passed,
`VOLAB_HOLDOUT_UNLOCK` was never set or read, no `@pytest.mark.holdout` was added,
`data/holdout_access.jsonl` **does not exist**, `data/trials.jsonl` is **414 lines** and
`data/comparisons.jsonl` is **52 lines** — the registered figures, unchanged.

The implementer inherits the same constraint, in §10. Every witness in §9 runs on a pseudo-holdout
carved inside dev, so the whole of HR-4 can be proven green without a single sealed read of the
real store.

---

## 1. Decision 1 — where it lives, what it is called, and its verb

**Decision: a package, `src/volab/reveal/`, with `__init__.py` as the library and `__main__.py` as
the CLI; the verbs are `python -m volab.reveal check` and `python -m volab.reveal take`.**

### Why under `src/volab/` and why not `tools/`

`tests/test_import_discipline.py` AST-scans `tools/*.py` for `allow_holdout` (as a keyword, a bare
name or an attribute) and for the string `VOLAB_HOLDOUT_UNLOCK` outside a docstring, and fails on
any of them (`_unlocks`, `test_probe_does_not_unlock_the_holdout`). It **does not** scan
`src/volab/`. So:

- **The reveal must not go in `tools/`.** It is the one caller in this repository that legitimately
  passes `allow_holdout=True`, and in `tools/` that is a failing test. The only ways to put it
  there are to exempt the file or to weaken `_unlocks` — both of which trade a permanent, checked
  ban for one story's convenience, and the ban is the thing that lets R130 say "`allow_holdout` has
  never been set in this repository". `tools/` stays what it is: probes that produce figures and
  hold no key. HR-5's dress rehearsal may live there **because it holds no key either** — it runs
  on a carved dev window and a separate storage root.
- **Under `src/volab/` it needs no ratchet exemption, and none may be added.** The scans that do
  reach `src/volab/` are the Parquet ban (`ALLOWED == {"store.py"}`), the numeric ban over the
  `SEALED` set (`panel.py`, `store.py`, `walkforward/__init__.py`, `models/base.py`, `features/**`)
  and the audit-log ban (`test_only_one_function_opens_the_audit_log`). `volab.reveal` reads no
  Parquet directly, is not in `SEALED` and nothing sealed imports it, and it never names the audit
  log. So it satisfies all three as written. `ALLOWED` stays `{"store.py"}`; `SEALED` and
  `SEALED_PACKAGES` are untouched; `volab.reveal` imports `volab.compare` (and therefore numpy,
  scipy and arch) exactly as `volab.evaluators` does, which is permitted because the direction of
  the ban is "nothing sealed may reach numpy", not "nothing may".

### Why a package and not a module

Four CLIs in this repository are `package/__main__.py` over a library (`compare`, `evaluators`,
`leaderboard`, `daily`), and the split earns its keep here for the same reason it does in `daily`:
**the gates and the fingerprint must be pure and table-testable, and the clock, `argparse` and
`sys.exit` must enter at one seam.** `reveal/__init__.py` contains no `print`, no `argparse` and no
`sys.exit`; `reveal/__main__.py` supplies `today`, maps a `Gate` to an exit code and prints.

### Why `take` and not `run`

Three CLIs already answer to `run` (`compare`, `evaluators`, `walkforward`). A verb a hand types by
muscle memory is the wrong verb for an act that happens once and cannot be undone. `take` is the
project's own word for it ("the reveal is taken"), it appears in no other CLI, and `check` is the
one that is safe to type repeatedly.

- **`check`** runs every gate that needs no key, prints each with its verdict, **writes nothing at
  all** — no audit line, no ledger line, no store partition — and exits 0 if every gate passes. It
  is the command the operator runs first, and it is the only one in this package that is safe to
  put in a Makefile.
- **`take`** is the one confirmatory read. It runs the same gates, then performs the span in §5.

### The human gate is the environment, and the module never touches it

`take` needs both keys: it passes `allow_holdout=True` to `load_panel`, and
`VOLAB_HOLDOUT_UNLOCK=1` must already be in the environment **because a human exported it**. The
module must never set it. A CLI that set the variable in its own process would make the reveal one
typed command with no key held, which is anti-goal #1 of the parent design wearing a uniform. This
is §10 item 2 and it has a shape witness (W17); the rule is checkable, not remembered.

---

## 2. Decision 2 — the refusals, before any read

**Decision: every gate that can be answered without a key runs before the `reveal_open` line is
written, in the fixed order below; each has its own exit code and its own sentence. Three further
refusals can only be answered after a read has happened, and those cost an attempt.**

`gates()` is pure: it takes the facts and returns the refusals it found, in order, and the CLI exits
on the first. Purity is what makes W1 a table rather than a shape a reader must trust. **Every
gate is referred to by its `name`, never by its row number**: the numbers below are a reading aid
in this document and the implementer must not encode them.

```python
# src/volab/reveal/__init__.py
@dataclass(frozen=True)
class Gate:
    code: int           # the process exit code, 3..12; never 0, 1 or 2
    name: str           # a stable identifier, e.g. "dirty_tree"
    message: str        # the whole sentence the operator reads; no further formatting

def gates(
    settings: Settings,
    *,
    today: dt.date,
    repo_root: Path,
    git_sha: str,
    git_dirty: bool | None,
    preregistration_sha256: str | None,   # None when the file is absent or unreadable
    preregistration_blob: str | None,     # None when git cannot name the blob at HEAD
    already_revealed: bool,
) -> tuple[Gate, ...]:
    """Every pre-read refusal that applies, in the order they are checked. Empty means proceed.

    Pure and total: it reads no file, runs no subprocess and asks no clock. The caller measures
    the world; this decides. ``today`` is the caller's, on ``daily._today``'s precedent.
    """
```

| # | gate | exit | the refusal, in words |
|---|---|---|---|
| 0 | usage | **2** | `argparse`'s own. Reserved: nothing in this design may use 2. |
| 1 | `dirty_tree` | **3** | `the tree is dirty (git <sha12>, repository scope): the reveal records a fingerprint that must identify the code that produced the numbers, and a dirty tree cannot. Commit or stash, then run again.` |
| 2 | `git_unknown` | **4** | `git cannot name this checkout's HEAD, so no fingerprint can be taken. The reveal refuses rather than recording "unknown" as a provenance claim.` |
| 3 | `preregistration_absent` | **5** | `docs/holdout-preregistration.md cannot be read: <reason>. The reveal is defined by that file and will not run without it.` |
| 4 | `preregistration_content` | **6** | `docs/holdout-preregistration.md hashes <actual> and R130 registered <expected>: the registered document has changed. A change means a NEW registration and a new look, both counted; it does not mean this reveal proceeds.` |
| 5 | `preregistration_blob` | **7** | `git rev-parse HEAD:docs/holdout-preregistration.md gives <actual> and R130 registered 4b5c04e…: the file on disk may be right but this checkout's history is not the one it was registered in.` |
| 6 | `window_open` | **8** | `holdout_end 2026-10-30 is not in the past (today <today>): the registered window has not finished, so there is nothing complete to read.` |
| 7 | `already_revealed` | **9** | `a completed reveal record is already committed at HEAD: the seal is lifted, the holdout is ordinary history and this read is not the one-time reveal. Re-scoring it is an ordinary run, not a reveal.` |

Gate 4 and gate 5 are **both** checked because R130 records both hashes and says why: *"the blob
sha … ties the content to this repository's history, while the sha256 survives being copied out of
git entirely"*, and *"the reveal compares the file it reads against these"* — plural. Two gates, two
exit codes, because they fail differently and an operator needs to know which.

Gate 1 asks `walkforward.git_state(repo_root)`, **repository scope**, for R70's reason: the package
scope reads an edit to `config/base.yaml` — which carries `dev_end`, `holdout_end`, the universe and
the storage root — as clean, and a provenance line blind to the configuration is not provenance.
This matters more here than anywhere else in the project, and it is also why the fingerprint carries
its own repo-scoped flag rather than trusting the audit line's (§11, finding 4).

### The three refusals that cost an attempt, and why they cannot be hoisted

| # | gate | exit | when it is known |
|---|---|---|---|
| 8 | `keys_not_held` | **10** | the first sealed read raises `HoldoutViolation`: the operator did not export the unlock variable, so `_unlock_granted` is `False` and the bound is `dev_end`. **No look line is written** — a step-3 refusal in `load_panel` touches no filesystem — but the `reveal_open` line already exists, so this attempt is counted and the next one is a new `reveal_id`. |
| 9 | `window_not_ingested` | **11** | after stage B's **three bars reads, deliberately the first reads of stage B** (look 16 of 38): a symbol's curated bars do not reach the last XNYS session at or before `holdout_end`. Today the raw bars stop at **2026-10-20**, six sessions short, so **this gate fires on the repository as it stands** and `python -m volab.ingest` must run first. The registered window's *end* is a commitment (§1, "Window"), unlike its origin counts (§5, predictions), so a short window is not the registered read. |
| 10 | `canary_disagrees` | **12** | after stage A: a curated frame read at `end=dev_end` is not identical to the one read at `end=dev_end` before stage A. See §5. |

**Why gate 8 cannot be hoisted.** Asking "do I hold the key" before writing anything would need
either a public key predicate in `store.py` — a new public name in the module whose public surface
is a frozen, checked set, added purely for operator comfort — or a probe read, and a probe read by a
key-holder **is** a counted look, deliberately
(`test_refused_unlocked_read_still_writes_one_audit_line` exists to make exactly that visible). So
the cost of a forgotten `export` is one counted attempt with zero looks, which the write-up reports
as such. That is the honest price and it is cheap; the alternatives either widen `store`'s public
surface or make a key-holder's probe free, and both are worse than `k = 2`.

---

## 3. Decision 3 — the fingerprint in `details`

**Decision: one object, built before any sealed read, written verbatim into the `reveal_open`
line's `details` and again into `reveal_complete`'s, with the result block added to the second.**

HR-2 §3 made `details` free-form and opaque to the guard precisely so this can be added without
touching the predicate's contract. The guard validates `schema`, `kind`, `reveal_id` and the three
dates and reads nothing else — so nothing here can affect whether the seal lifts, which is what
lets the fingerprint be as detailed as the evidence needs.

```python
@dataclass(frozen=True)
class Fingerprint:
    """What makes the result attributable. Taken before the first sealed read, never after."""
    # protection 1: the registered document, by both hashes R130 records
    preregistration_path: str              # "docs/holdout-preregistration.md"
    preregistration_sha256: str            # must equal REGISTERED_SHA256 or gate 4 fired
    preregistration_blob: str              # must equal REGISTERED_BLOB or gate 5 fired
    registering_commit: str                # R130's, a literal: 65617eea…
    # protection 4: the code that ran, asked at the repository scope (R70)
    git_sha: str
    git_dirty: bool                        # False, or gate 1 fired
    # the window and the knobs, so the run is reproducible from the record alone
    dev_end: str; holdout_start: str; holdout_end: str
    stride: int                            # 1, registered in §1
    minimum_training: int                  # 250, NOT registered -- see OQ3
    cohorts: tuple[str, ...]               # ("phase2-shootout", "phase2-shootout-multistep")
    losses: tuple[str, ...]                # ("qlike", "mse"), from walkforward.LOSSES
    benchmarks: tuple[str, ...]            # (), because SPA is not registered -- see §7
    # §4's two ledger snapshots, both of them, plus the deltas
    trials_registered: int                 # 414, a literal pinned by W4
    trials_at_read: int                    # count_trials() before this attempt appends anything
    trials_delta: int
    comparisons_registered: int            # 52
    comparisons_at_read: int
    comparisons_delta: int
    # the span, declared before it is taken (§5)
    planned_looks: int
    # the champion's configuration: None on the open line, filled on the complete line
    champion: dict[str, object] | None
    def record(self) -> dict[str, object]: ...
```

Four things about it are decisions, not fields:

- **The two ledger counts are taken before this attempt writes a single trial line.** Otherwise the
  reveal counts itself and the delta stops meaning "work that happened between registration and the
  read". §4 of the registration asks for the second count because *"further work may legitimately
  add trials between registration and the read"* — that is what the delta measures. The cumulative
  count including the reveal's own 150 lines is a different number and it is printed in its own
  right beside every ranking, which is §3's *"the write-up states the trial count from the ledger
  beside every ranking"* and is already what `compare.describe(comparison, trials=…)` does.
- **Both ledgers, not just trials.** The brief asks for the trial counts; §4 registers a **pair**
  (414 trials, 52 comparisons) and the reveal writes to both files. Recording one and not the other
  would leave half the registered snapshot unmeasured.
- **`git_dirty` is here even though the audit line already has a `git_dirty` key.** The line's own
  flag comes from `store._git_state()` at the **package** scope and is written by `store`, not by
  the reveal; this one is repo-scoped and is the reveal's own measurement, taken before anything was
  written. They answer different questions and will disagree — see §11, finding 4.
- **The champion is a content hash's worth of configuration, not a hash.** Protection 4 asks for
  "the champion's configuration … and a content hash of the champion's definition". The champion's
  definition in this repository is a `Model` instance assembled in
  `volab.compare.cohorts`; its identity is `(cohort name, contestant name, the cohort's full
  contestant tuple in order, the `Domain`/`Sampling` it declares, the git sha of the tree that
  built it)`. The git sha **is** the content hash of its definition, because gate 1 guarantees the
  tree was clean — which is the dirty-tree refusal buying something concrete rather than being
  hygiene. A separate hash of the model's source would be a second identity to keep in sync; the
  clean-tree sha is one that cannot drift. The champion block therefore records the fields above
  verbatim, and §12 leaves its exact JSON key order to the builder.

---

## 4. Decision 4 — the two keys bound the reveal, and that is now mechanical

Worth stating as a claim because HR-3 made it enforceable rather than asserted.

Post-HR-3, `_read_limit` gives the two keys `splits.holdout_end` and not `dt.date.max`. So **the
reveal's own reads cannot see past `holdout_end`**, by the same single comparison that bounds
everyone else's — not by the reveal's own discipline in always passing
`end=settings.splits.holdout_end`. If a future edit passed a later `end`, `load_panel` would refuse
it, name the live region, and say that no argument and no environment variable opens it.

That is what protection 3's credibility rests on: *the champion was fixed before the live data
existed*. Before HR-3 that sentence rested on the reveal reading carefully. It now rests on the
guard, and W5 pins it from inside the reveal's own call path.

The reveal reads at exactly one `end`, `settings.splits.holdout_end`, on **every** sealed read in
§5. There is no second cutoff anywhere in the package.

---

## 5. Decision 5 — the multi-read span

**Decision: four stages, 38 sealed looks under one `reveal_id`, every one at
`end = holdout_end`, every one past `dev_end` and therefore logged before it is taken; plus 20 free
dev reads that are not looks and are the canary for the one thing stage A could silently break.**

Derived tiers stop at `dev_end` — measured in this checkout from partition names: `data/raw` runs to
2026-10-20…2026-10-23 by source, while **every** curated and features partition stops at
2025-12-31. So the reveal cannot get its inputs from one read: it has to derive the holdout rows of
curated, then of features and targets, then read them back the way the dev path reads them.

### The stages, in order

**Stage A — raw → curated.** `volab.ingest.curate.curate_bars / curate_vix / curate_fred`, each at
`end=holdout_end`, called and not reimplemented. Looks, in call order:

| read | tier | count |
|---|---|---|
| `nasdaq_bars`, per symbol | RAW | 3 |
| `nasdaq_dividends`, per symbol | RAW | 3 |
| `cboe_vix`, per series | RAW | 4 |
| `fred`, per series | RAW | 3 |
| | | **13** |

Two of the three dividend reads raise `DatasetNotFound` — Nasdaq serves dividends for QQQ only —
and each still leaves an `look_open`/`look_close` pair with `outcome="DatasetNotFound"`. That is the
documented behaviour and it is correct: a key-holder's refused read is still a look.

**Stage B — curated → features and targets, and the panel the scoring uses.** The reveal reads each
symbol's bars **first**, then each exogenous series **once, shared across symbols** (the shape
`features/__main__._build` already uses), then does three things with the frames in hand:

| read | tier | count |
|---|---|---|
| `volab.panel.load_bars`, per symbol | CURATED | 3 |
| `volab.panel.load_exogenous(vix_family)`, per series | CURATED | 4 |
| `volab.panel.load_exogenous(fred_daily)`, per series | CURATED | 3 |
| | | **10** |

**The bars come first so the `window_not_ingested` gate fires at look 16 of 38** rather than at 35.
That gate reads the bars' maximum `as_of`, and it fires on the repository as it stands, so paying
19 further looks before finding out would be a self-inflicted cost. Hoisting it before stage A
would mean asking the filesystem about partition names, which is a read path this project does not
have outside `store.py`; the first curated bars frame is the earliest honest place.

1. `volab.panel.assemble(bars, exogenous)` per symbol → the curated panel. This is exactly what
   `load_curated_panel` returns; the only difference is that the exogenous frames are read once
   instead of once per symbol, which saves 14 looks and changes no value.
2. `features.build.build_features(panel, through=holdout_end, calendar)` → `write_features` for
   **only the rows whose `as_of > dev_end`**.
3. `features.targets.build_targets(target_panel, horizon, calendar)` for each horizon →
   `write_targets` for **only the rows whose `as_of > dev_end`**.

**The write filter is the invariant, not a tidiness.** Filtering before writing means no dev-dated
features or targets partition is ever opened, so every recorded dev figure stays byte-reproducible
by construction rather than by a check. Note which rows this *does* admit: a target whose origin is
inside dev but whose horizon completed after `dev_end` has `as_of > dev_end`, has never existed in
the store, and is a legitimate knowable training target for a holdout origin. It is written, its
partition is holdout-dated, and it is not a scored origin because the origin window starts at
`holdout_start`.

**Stage A cannot be filtered the same way**, because `curate_*` reads *and* writes, and
`write_curated` overwrites. So it rewrites all ~14,600 curated partitions, dev-dated ones included.
That rewrite is a content no-op **by construction**: raw is immutable (`write_raw` refuses to
replace a partition), curation is a pure function of raw at or before `end`, no curated key carries
a second vintage (measured, `tools/measure_vintages.py`), and the one `end`-dependent input —
`_distribution_dates` — can only gain dates after `dev_end`, which match no dev-dated bar row. A
construction argument is not a check, so:

**The canary.** Before stage A the reveal reads each `(dataset, symbol)` pair at `end=dev_end` —
3 bars + 4 vix + 3 fred = **10 reads** — and after stage A it reads the same ten again, and compares
with `pandas.testing.assert_frame_equal`. **20 reads, none of them looks**: `end == dev_end` is not
past `dev_end`, so the predicate is asked zero times, nothing is audited and nothing is logged. Any
difference exits 12 before a single number is produced. This is the cheapest available form of "the
dev figures still reproduce", it uses the only sanctioned read path, and it costs about 12 seconds
once.

**Stage C — the targets, read back from the store.**

| read | tier | count |
|---|---|---|
| `walkforward.load_targets`, per (symbol, horizon) | FEATURES | 12 |

Read back rather than handed over in memory, because `load_panel`'s `as_of` filtering and
latest-vintage-per-key semantics are what `knowable_targets` and `scoreable_origins` are defined
against, and the dev path reads them this way. The same code, the same semantics, no second path.

**Stage D — the persisted feature table, for the truncation-equivalence report.**

| read | tier | count |
|---|---|---|
| `load_panel(FEATURE_SET, tier=FEATURES)`, per symbol, **memoised** | FEATURES | 3 |

`run_configuration` calls its `persisted` callable once per configuration, so an unmemoised version
would cost 12 looks for 3 distinct frames. The reveal memoises per symbol. This stage exists because
`compare_against_persisted` is the truncation-equivalence check, and §3 of the registration makes *"a
leakage test that could not be shown failing"* one of the three definitions of a failed run — so the
guard stays armed on the holdout window, printing `N of N origins identical` exactly as the recorded
dev run's §4 evidence does. It is a leakage guard, not a claim, so it adds nothing to §2's list.

### The key has to be threaded, and it changes HR-4's file set

**Found by reading the signatures rather than the designs, and it is the one thing here that no
witness as first drafted would have caught.** `load_panel`'s `allow_holdout` defaults to `False`,
and `_unlock_granted(allow_holdout)` needs the argument **and** the environment variable. Of the 38
reads above, only stage D's is a direct `load_panel` call. The other 35 go through functions that
take no such parameter and therefore pass the default:

| function | file | reads |
|---|---|---|
| `curate_bars`, `curate_vix`, `curate_fred`, `_distribution_dates` | `src/volab/ingest/curate.py` | 13 |
| `load_exogenous`, `load_bars` | `src/volab/panel.py` | 10 |
| `load_targets` | `src/volab/walkforward/__init__.py` | 12 |

So **a `take` written as the span above describes would exit 10 on its first raw read, every time,
however the operator's shell is set up.** Checked by grep rather than inferred: no module under
`src/volab/` other than `store.py` names `allow_holdout` at all today.

**Decision: a keyword-only `allow_holdout: bool = False` is threaded through those seven functions
and forwarded to `load_panel`; nothing else in them changes.** The price, stated:

- **The file set grows.** HR-4 now touches `src/volab/ingest/curate.py`, `src/volab/panel.py` and
  `src/volab/walkforward/__init__.py`, two of which (`panel.py`, `walkforward/__init__.py`) are in
  the `SEALED` set. A keyword-only `bool` with a `False` default imports nothing, adds no numeric
  edge and changes no existing call site, so the numeric ban and the Parquet ban are untouched — but
  these are the project's most load-bearing modules and the board's one-implementer rule has to
  cover them. **The coordinator needs this before dispatching HR-4 beside HR-5 or HR-8.**
- **Seven more places where the key could be passed.** That is what W25 is for: under `src/volab/`,
  `allow_holdout=True` may be passed **only** from `volab/reveal/`, and `walkforward.load_for` and
  the three research `__main__`s must never pass it even though `load_targets` now accepts it.

The two alternatives, and what rules each out:

- **Reimplement curation so stage A needs no key-bearing call.** `curate_*` reads and writes in one
  function, so "just the transform" does not exist as a public shape; building one duplicates the
  typing, the non-session rejection, the OHLC padding and the distribution flag. R97's rule — call,
  do not reimplement — exists because two copies drift, and "which rows enter the curated tier" is
  the worst place in this repository to have a second copy.
- **A `store` contextvar that grants the key inside a scope, the way `reveal_scope` grants a
  `reveal_id`.** **Refused, and it must stay refused:** `reveal_scope` labels lines and changes no
  bound, whereas a key scope *is* a second route to the unlock — the parent design's anti-goal #1
  verbatim. An argument passed at every call site is visible at every call site; an ambient one is
  not. §10 item 19.

### The total, as a formula rather than a magic number

With `S = len(universe) = 3`, `V = len(panel.VIX_SERIES) = 4`, `F = len(panel.FRED_SERIES_NAMES) =
3`, `H = len(HORIZONS) = 4`:

```
looks = (2S + V + F)      # stage A, raw
      + (V + F + S)       # stage B, curated
      + (S * H)           # stage C, targets
      + S                 # stage D, persisted features
      = 4S + 2(V + F) + S*H
      = 12 + 14 + 12 = 38
```

`reveal.expected_looks(settings)` computes it, the CLI prints it before stage A as *"this attempt
plans 38 sealed looks"*, the fingerprint records it as `planned_looks`, and W7 asserts that the
pseudo-holdout rehearsal's `count(kind == "look_open")` equals the formula evaluated on its own
settings. A span that drifts from its declaration then fails a test instead of being noticed by
nobody. Every one of the 38 is taken inside `with store.reveal_scope(reveal_id):`, so every line
carries the attempt it belongs to.

### Where the look count and the attempt count come from, and why they are two numbers

`store.AUDIT_FILENAME` may be named in exactly one function body in `store.py` and **nowhere else
under `src/volab`** (`test_only_one_function_opens_the_audit_log` also catches the bare literal
`"holdout_access.jsonl"`). So `volab.reveal` cannot open the audit log, and the tally must come from
`store`. HR-4 therefore makes **one** addition to `store.py`:

```python
# src/volab/store.py -- the only change HR-4 makes to this module
@dataclass(frozen=True)
class AuditTally:
    """What the audit log holds. Read-only; nothing here lifts anything."""
    looks: int                      # count(kind == "look_open")
    looks_closed: int               # count(kind == "look_close")
    attempts: tuple[str, ...]       # every reveal_open's id, in file order
    completed: frozenset[str]       # the ids with a matching, strictly later reveal_complete

def audit_tally(settings: Settings) -> AuditTally:
    """Count the sealed looks and the reveal attempts in ``settings``' audit log.

    Reads the log under ``storage_root``, which is where the writer appends -- not the committed
    blob the predicate asks git about. It therefore sees the attempt in progress, whose lines are
    not yet committed, which is the whole reason the write-up can report its own figures. Skips
    anything it cannot parse, exactly as ``_completed_reveal_in`` does, and never raises.
    """
```

Price, stated: it adds a public name to the module whose public surface is a frozen checked set, so
`_PUBLIC` in `tests/test_store_seal.py` grows by two entries and `test_only_one_function_opens_the_
audit_log`'s `owners == {"_append_audit"}` becomes `{"_append_audit", "audit_tally"}`. That is an
edit to a seal witness, which deserves to be uncomfortable. The alternatives are worse: naming the
log in `volab.reveal` breaks the one-writer invariant outright, and putting the write-up in `tools/`
to escape the scan moves the reveal out of `src/volab/`, which §1 rules out. One read-only accessor
beside `reveal_is_committed`, on the same reasoning (it opens nothing and runs no Parquet), is the
smallest hole that does the job.

**Then the two figures, and they are not the same number.** The coordinator's instruction and HR-2's
Q4 both say the reported figure is `count(kind == "look_open")`, replacing the audit log's raw line
count. That is right about what replaces the line count, and the write-up reports it. But it is
**not** the parent design's *k*: with one attempt spanning 38 reads, `count(look_open)` is 38 and
*k* is 1, and "the k-th completed" cannot be said of a `look_open`, which pairs with a `look_close`
and has no notion of completion. So the sentence splits in two, and the write-up says both:

- **38 sealed reads logged** — `tally.looks`, the honest replacement for the line count that
  `.gitignore`, `docs/superpowers/plans/2026-10-19-phase0-skeleton.md:98` and
  `docs/superpowers/phase0-decisions.md:69` all call for.
- **k reveal attempts, the k-th completed** — `k = len(tally.attempts)`, with
  `tally.attempts[-1] in tally.completed` after this attempt finishes. This is the parent design's
  figure, and it is the one that answers the anti-goal "a reveal that can be retried until it reads
  well".

Both are printed by `take` and both go into `reveal_complete`'s `details`. §11, finding 6 records
the collapse as a defect in HR-2's Q4 rather than silently papering over it.

---

## 6. Decision 6 — retries are counted, not forbidden

**Decision: a crashed attempt is a `reveal_open` with no `reveal_complete`; the next attempt is a
new `reveal_id` and nothing is reset, repaired or deleted.**

How each failure looks in the record:

| failure | what the log holds | what the ledger holds |
|---|---|---|
| gates 1–7 refuse | **nothing**: `take` writes no line until every pre-read gate has passed | nothing |
| the keys were not exported (gate 8) | one `reveal_open`, no look lines, no `reveal_complete` | nothing |
| the window is short, or the canary disagrees (gates 9, 10) | one `reveal_open`, the looks taken so far with their closes, no `reveal_complete` | nothing |
| a crash during scoring | one `reveal_open`, 38 complete look pairs, no `reveal_complete` | the trial lines appended so far, under this attempt's own `at` |
| `SIGKILL` mid-read | one `reveal_open`, one `look_open` with no `look_close` | whatever had been appended |
| it completed | `reveal_open`, 38 pairs, `reveal_complete` with the same id | 150 trials and 24 comparisons under one `at` |

Three consequences worth writing down rather than discovering:

- **A crashed attempt leaves its partial trials on the ledger, and they stay.** That is R112's
  precedent applied to the reveal — *"R112's crashed run counted rather than discarded"* is one of
  the three reasons §4's figure moved to 414. The next attempt's lines carry a new `at`, so the
  leaderboard partitions them apart by `(at, git_sha)` and a reader can see both.
- **The next attempt therefore reports a larger `trials_at_read` and a larger delta**, which is
  honest and is precisely why §4 asks for two counts and the delta rather than an equality.
- **The seal does not lift on a crashed attempt**, because `_completed_reveal_in` needs the ordered
  pair. A half-finished reveal leaves everything sealed, which is the fail-closed direction.

### A retry needs a commit first, and that is a strengthening

**Consequence of §11 finding 4, and it has to be written down or an implementer will "fix" it by
narrowing the gate.** The first audit line the reveal writes makes
`data/holdout_access.jsonl` appear, and `_git_state`'s repository-scoped pathspec excludes
`data/raw`, `data/curated` and `data/features` but **not** the three tracked `data/*.jsonl` files.
So after any attempt that wrote a line, the tree is dirty and **the next `take` exits 3 until a
human commits what the previous attempt left behind** — its `reveal_open`, its look lines, and any
partial trial lines.

That is the right behaviour and it is not friction to be removed:

- every attempt, including every crashed one, becomes a commit — visible, reviewable, attributable,
  which is the basis the whole seal rests on;
- committing an unpaired `reveal_open` **lifts nothing**, because `_completed_reveal_in` needs the
  ordered pair, so the commit is safe;
- and the k-th attempt's own count is then auditable from git history rather than only from a file.

The procedure is therefore: `take` → if it did not complete, read the log, commit it with the
partial ledger lines and a message saying what failed → `take` again. §10 item 20 forbids the
shortcut of excluding those three files from the dirty check, and W11 models the whole sequence.

**Retrying is not resuming.** Stage A and stage B are idempotent (`write_curated` and
`write_features` overwrite; the write filter keeps them off dev partitions), so a second attempt
re-derives the same rows and re-reads them: 38 more looks. The reveal has no resume flag and must
not grow one — a flag that skipped the looks would make the second attempt's looks invisible, which
is the anti-goal "an unlogged look" by another route.

---

## 7. Decision 7 — what is computed, and the champion

**Decision: §1, §2 and §3 of the registration, computed by the code that already computes them, and
nothing else. Two cohort runs; stride 1; `benchmarks=()`; the decision evaluators are not called.**

### The shape of the run

Twelve configurations, in two cohort runs under one `reveal_id` and one `at`:

| cohort | contestants | horizons | symbols | configurations |
|---|---|---|---|---|
| `phase2-shootout` | 7 | 1 | SPY, QQQ, IWM | 3 |
| `phase2-shootout-multistep` | 6 | 5, 22, 66 | SPY, QQQ, IWM | 9 |

`7 × 3 × 1 × 2 = 42` trials plus `6 × 3 × 3 × 2 = 108` trials = **150 trial lines** and
`12 × 2 = 24` comparison records — §4's own arithmetic for the registered run's shape, which is
what makes the reveal's own ledger footprint predictable and therefore checkable (W9).

Two cohorts is why the reveal is a module and not a flag on `compare`: `compare/__main__` takes one
`--cohort`, refuses `--to` past `dev_end` (R62, correctly — it is a research entry point), and gets
its frames from `walkforward.load_for`, which is pinned at `dev_end` forever and must stay pinned
(HR-2 §8 item 9). So the reveal restates `compare/__main__._run`'s **loop** — about forty lines,
already duplicated once between `compare` and `evaluators`, with the same reason `_persisted` is
restated there — and calls, rather than reimplements, every piece that computes anything:
`walkforward.run_configuration`, `compare.cohorts.monte_carlo_error`, `compare.series.loss_series`,
`compare.analyse`, `compare.describe`, `compare.sink.JsonlSink`, `walkforward.count_trials`,
`leaderboard.ranked_trials`.

**Everything registered as "claimed" then falls out of code that already prints it:** the loss per
contestant per symbol per horizon (`Trial.value`, via `run_configuration`'s own appends), the
ranking (`leaderboard.ranked_trials`, the project's only ranking), pairwise DM with the Harvey
correction (`compare.dm.pairwise`), and MCS at 10% and 25% (`compare.multiple.model_confidence_set`).
And so do the refusals: `claims_withheld` is `n_eff < 2 / min(SIZES)` — literally `n_eff < 20` —
and `model_confidence_set` refuses size α where `n_eff < 2/α`. The registered table is not
re-implemented anywhere in `volab.reveal`; it is *predicted* by the registration and *produced* by
the existing rules. W2 asserts the two agree.

### Stride 1, and what it does to the table

§1 registers *"Origins at stride 1, to use every session available"*, and the registered `n_eff`
column is `n/h` — which is `n/(q+1)` with `q = overlap_lag(h, 1) = h - 1`. So stride 1 is already
baked into the registered arithmetic and is not a knob. `first = holdout_start`,
`last = holdout_end`, `every = 1`. Consequences, each checked rather than assumed:

| h | origins (registered prediction) | q | n_eff | MCS 10% | MCS 25% | DM |
|---|---|---|---|---|---|---|
| 1 | 208 | 0 | 208.0 | claimed | claimed | claimed |
| 5 | 204 | 4 | 40.8 | claimed | claimed | claimed |
| 22 | 187 | 21 | 8.5 | refused | claimed | withheld |
| 66 | 143 | 65 | 2.2 | refused | refused | withheld |

The Harvey correction needs `1 ≤ h_eff < n`; at h=66 that is `66 < 143`, and at h=22, `22 < 187`, so
it is defined at every cell. Politis-White needs ≥ 11 origins and gets 143 at worst. The dev run's
figures were taken at stride 5 and are **not** column-comparable with these; the registered table is
the holdout's own and the write-up must not put the two side by side as if they were.

### SPA is not computed

`compare.analyse` runs SPA against `BENCHMARKS = ("har_levels", "random_walk")` by default. §2's
"Claimed" list has four items and SPA is not one of them. **Decision: the reveal passes
`benchmarks=()`.** Reasons, in order of weight: a 10,000-replication bootstrap p-value about beating
HAR, stored in `comparisons.jsonl` and claimed by nobody, is the forking path this entire apparatus
exists to close — and §3 specifically refuses to define success as beating HAR, which is the exact
hypothesis SPA tests. The price: the reveal's 24 comparison records carry `"spa": []` and are not
column-comparable with the 52 dev records on those fields, and `Comparison.block` comes from the MCS
alone (so it is `None` at h=66, where the MCS is refused — which is already true of the dev run's
h=66 records). The price is small because it is reversible in the only direction that matters:
after the reveal the holdout is ordinary history, so anyone may compute SPA on it later, as a new
and separately registered claim. Computing it now and not claiming it is the one option that cannot
be undone.

### The decision evaluators are not called

`volab.evaluators` scores the Fissler–Ziegel loss of each contestant's Gaussian (VaR, ES), Kupiec
and Christoffersen coverage, and a volatility-targeted P&L. None of it is in §1, §2 or §3. R139
already holds Phase 3's recorded run until after the reveal, and §5 of the registration says a later
contestant — or a later instrument — *"gets a later, separately registered read, or the live
record"*. So the reveal does not import `volab.evaluators`, and W18 asserts that as a shape.

### The champion rule, exactly as §3 fixes it

> The champion is the contestant in the 10% model confidence set with the lowest QLIKE, at **h=1 on
> SPY**, chosen in advance as the primary configuration. If the 10% set has one member, that is the
> champion; if several, the lowest QLIKE among them; if the set is refused for thinness, **there is
> no champion** and that is the result.

```python
class ChampionStatus(StrEnum):
    NAMED = "named"            # one contestant, and both readings of the rule agree on it
    NONE = "none"              # the 10% set was refused: §3's permitted outcome, and a result
    AMBIGUOUS = "ambiguous"    # the two readings of §3 disagree -- see OQ1; nothing is named

@dataclass(frozen=True)
class Champion:
    status: ChampionStatus
    name: str | None                          # None unless NAMED
    qlike: float | None
    members: tuple[str, ...]                  # the 10% set in cohort order, or ()
    qlike_by_member: dict[str, float]         # every member's mean QLIKE, so the order is checkable
    unclaimed: tuple[str, ...]                # members whose significance R115 withholds
    candidates: tuple[str, ...]               # the two readings' answers when AMBIGUOUS
    refusal: str | None                       # the MCS's own sentence when NONE
    rule: str                                 # the verbatim sentence from §3 that was applied

def champion_from(comparison: Comparison, trials: Sequence[Trial]) -> Champion:
    """§3's rule, applied to the (SPY, h=1, qlike, phase2-shootout) record and its trials.

    Pure, and it is handed the two objects rather than reading a ledger, so W3's table needs
    no store, no git and no key. ``trials`` are the in-memory ``Trial`` objects this run produced,
    never ledger lines: the champion must come from the same objects the comparison was built from.
    The *printed ranking* is a different job and uses ``leaderboard.ranked_trials`` (see below).
    """
```

- `members` is `comparison.mcs.members[0.10]`; `refusal` is `comparison.mcs.refusals[0.10]`.
- **The printed ranking and the champion come from two places on purpose, and a witness ties
  them.** The ranking §2 claims is `leaderboard.ranked_trials`, which takes `TrialLine` ledger
  lines — so the reveal reads its own lines back with `leaderboard.read_trials(root)`, filters to
  this run's `at`, and ranks those: the printed ranking is then provably the ledger's, byte for
  byte, which is the leaderboard's whole reason for computing nothing. `champion_from` works on the
  in-memory `Trial`s instead, because the champion must come from the same objects the confidence
  set was built from. W26 asserts the two agree: the champion is rank 1 among the 10% members in
  `ranked_trials`' order for `(SPY, h=1, qlike)`.
- "lowest QLIKE" is the lowest `Trial.value` for `loss == "qlike"` among the members. QLIKE here is
  `log(f) + r/f` and the leaderboard's own words are "lower is better", so lowest is best and the
  rule reads the way it is written. No direction has to be inferred.
- The mean is taken over `Evaluation.comparable("qlike")`, the same origin set the MCS is computed
  on, because `loss_series` takes its origins from `comparable` — so the ranking and the confidence
  set cannot rest on different windows.
- **`AMBIGUOUS` is a real outcome and the machine must not break the tie.** See OQ1: §3 names
  membership and QLIKE and says nothing about a member whose significance R115 withholds, and §1
  all but predicts that this is `egarch11_skewt` at h=1. `take` prints both candidates and the
  sentence that does not distinguish them, writes `reveal_complete` (the read happened; the numbers
  are claimed), and exits **13**. Exit 13 means "completed, champion not determined": the user
  settles the reading, and protection 3's live record — which is a later story — does not start
  until they have.

### The refusals that print rather than being omitted

Each is produced by code that already prints it, which is why none of them can be quietly dropped:

| registered refusal | what prints it |
|---|---|
| no significance of any kind at h=22 | `Comparison.claims_withheld` → `[n_eff=8.5: no significance claimed]` on every DM pair, no verdict; `mcs.refusals[0.10]` prints `MCS 10%: REFUSED [n_eff=8.5] -- n_eff = 8.5 is below 2/alpha = 20 …` |
| no significance of any kind at h=66 | the same, plus **both** MCS sizes refused |
| no confidence set at all at h=66 | `mcs.refusals` holds 0.10 and 0.25; `describe` iterates `{*members, *refusals}`, so the refused sizes print |
| R94 firing on the holdout's own h=66 figure | the h=66 row above. The board says to expect it; `n_eff = 2.2` against the dev window's larger figure. The write-up reports the refusal; it does not omit the row |
| `egarch11_skewt`'s claim withheld at h=1 (R115) | `monte_carlo_error(SHOOTOUT, 1)` gives `{"egarch11_skewt": None}` with source `NO_MEASUREMENT`, so it lands in `unclaimed` and `unclaimed_reasons` words it. The reveal passes the same map the shootout does, unmodified |
| an unordered pair under the 1.5% ceiling (R102/R120) | `Comparison.pair_claimed` → `pair_verdict` is `None`, and the pair prints with its figures and no ordering |

**The reveal words none of these.** Every sentence is `compare`'s or `compare.sink`'s, stored in the
record and printed verbatim — US-3.8's rule, and the reason the leaderboard cannot paraphrase. A
second wording in `volab.reveal` would be a second rule that could drift from the first.

---

## 8. The exact interfaces

All in `src/volab/reveal/__init__.py` unless stated. No numpy import of its own; `pandas` and the
`volab` packages only. `mypy --strict` clean: every signature annotated, no `Any` outside the JSON
boundary.

```python
# ---- the seven signatures HR-4 changes outside its own package, and nothing else in them ------
#   src/volab/ingest/curate.py
#     curate_bars(symbol, *, end, settings=None, allow_holdout: bool = False) -> int
#     curate_vix(series,  *, end, settings=None, allow_holdout: bool = False) -> int
#     curate_fred(series, *, end, settings=None, allow_holdout: bool = False) -> int
#     _distribution_dates(symbol, *, end, settings, allow_holdout: bool = False) -> set[dt.date]
#   src/volab/panel.py
#     load_bars(settings, symbol, *, end, allow_holdout: bool = False) -> pd.DataFrame
#     load_exogenous(settings, dataset, series, column, *, end, report=None,
#                    allow_holdout: bool = False) -> pd.DataFrame | None
#   src/volab/walkforward/__init__.py
#     load_targets(settings, symbol, horizon, *, end, allow_holdout: bool = False) -> pd.DataFrame
# Each forwards the flag unchanged to its one ``load_panel`` call. ``load_for`` is NOT changed and
# never passes it (W25). ``load_curated_panel`` is NOT changed: the reveal assembles its own panel.

# ---- the registered literals, each pinned to its source by a witness --------------------------
PREREGISTRATION: Path = Path("docs") / "holdout-preregistration.md"
REGISTERED_SHA256: str = "c0a095ab3a00711fd435eded709bde4dcdf9f1ab87ab016e92dc7c5cd7323779"
REGISTERED_BLOB: str = "4b5c04e38280cbff591d353b1ccf8a91cb3d517b"
REGISTERING_COMMIT: str = "65617eea2e1c95b8cf0b90555810bf64944c9153"
REGISTERED_TRIALS: int = 414
REGISTERED_COMPARISONS: int = 52
REGISTERED_ORIGINS: dict[int, int] = {1: 208, 5: 204, 22: 187, 66: 143}   # §1's predictions
STRIDE: int = 1                        # §1, "Origins at stride 1"
MINIMUM_TRAINING: int = 250            # NOT registered; the CLI default. See OQ3
BENCHMARKS: tuple[str, ...] = ()       # SPA is not in §2's claimed list
CHAMPION = ("SPY", 1, "qlike", 0.10)   # §3's primary configuration and the set it reads

# ---- gates, pure ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Gate: code: int; name: str; message: str

def gates(settings, *, today, repo_root, git_sha, git_dirty,
          preregistration_sha256, preregistration_blob, already_revealed) -> tuple[Gate, ...]
def content_sha256(path: Path) -> str | None            # None on OSError/UnicodeDecodeError
def committed_blob(repo_root: Path, relative: str) -> str | None   # git rev-parse HEAD:<path>
def tail_completion(settings: Settings, horizon: int) -> dt.date  # the session h after holdout_end

# ---- the span, declared before it is taken ----------------------------------------------------
@dataclass(frozen=True)
class Read:
    stage: str          # "A" | "B" | "C" | "D"
    dataset: str
    tier: Tier
    symbols: tuple[str, ...]
    end: dt.date        # always settings.splits.holdout_end; W5 asserts it
    sealed: bool        # True for all of them; False only for the canary's dev reads

def plan(settings: Settings) -> tuple[Read, ...]        # 38 sealed Reads, in execution order
def expected_looks(settings: Settings) -> int           # len([r for r in plan(...) if r.sealed])
def canary_keys(settings: Settings) -> tuple[tuple[str, str], ...]   # the 10 (dataset, symbol)

# ---- the fingerprint --------------------------------------------------------------------------
@dataclass(frozen=True)
class Fingerprint: ...                                  # §3; .record() -> dict[str, object]

# ---- the champion -----------------------------------------------------------------------------
class ChampionStatus(StrEnum): NAMED = ...; NONE = ...; AMBIGUOUS = ...
@dataclass(frozen=True)
class Champion: ...                                     # §7
def champion_from(comparison: Comparison, trials: Sequence[Trial]) -> Champion

# ---- the stages: each takes its frames or its settings, and reports through a callable --------
@dataclass(frozen=True)
class Snapshot: frames: dict[tuple[str, str], pd.DataFrame]
def snapshot(settings: Settings) -> Snapshot                        # the canary, at dev_end
def compare_snapshots(before: Snapshot, after: Snapshot) -> tuple[str, ...]   # the differences

def derive_curated(settings, *, end, report) -> dict[str, int]       # stage A, partitions written
@dataclass(frozen=True)
class Inputs:
    panels: dict[str, pd.DataFrame]                     # symbol -> the assembled curated panel
    targets: dict[tuple[str, int], pd.DataFrame]        # (symbol, horizon) -> targets
    persisted: dict[str, pd.DataFrame | None]           # symbol -> the stored feature table
    last_session: dict[str, dt.date]                    # symbol -> max as_of in its curated bars
def derive_and_read(settings, *, end, calendar, report) -> Inputs    # stages B, C, D

@dataclass(frozen=True)
class Scored:
    comparisons: tuple[Comparison, ...]
    trials: tuple[Trial, ...]
    refused: tuple[str, ...]                            # configurations run_configuration refused
    disagreed: tuple[str, ...]                          # configurations whose truncation check failed
    origins: dict[tuple[str, int], int]                 # the actual origin counts, vs REGISTERED_ORIGINS
def score(settings, inputs, *, sink, at, sha, dirty, calendar, report) -> Scored
```

```python
# src/volab/store.py -- HR-4's one addition, §5
@dataclass(frozen=True)
class AuditTally: looks: int; looks_closed: int; attempts: tuple[str, ...]; completed: frozenset[str]
def audit_tally(settings: Settings) -> AuditTally
```

### `take`'s order, which is the whole protocol

```
1  load_settings(); today = _today(); sha, dirty = git_state(_REPO_ROOT)
2  gates(...)                     -> exit 3..9 on the first; writes NOTHING
3  before = snapshot(settings)    -> 10 free dev reads
4  fingerprint = Fingerprint(... trials_at_read=count_trials(root) ...)   # before any append
5  reveal_id = store.record_reveal_opened(settings, details=fingerprint.record())
6  with store.reveal_scope(reveal_id):
7      derive_curated(...)                       # stage A: 13 looks
8      after = snapshot(settings); compare_snapshots(before, after) -> exit 12
9      bars = read_bars(...)                     # stage B's first 3 looks
10     gate `window_not_ingested`                -> exit 11, at look 16 of 38
11     inputs = derive_and_read(..., bars)       # the rest of B, then C and D: 7 + 12 + 3
12 scored = score(...)                           # no reads; appends per configuration (R74)
13 champion = champion_from(the SPY h1 qlike record, scored.trials)
14 ranking = ranked_trials over read_trials(root) filtered to this run's `at`
15 tally = store.audit_tally(settings)
16 store.record_reveal_completed(settings, reveal_id, details={**fingerprint.record(),
                                                              "champion": champion, "tally": ...})
17 print the write-up block; exit 0, or 13 (ambiguous champion), or 14 (truncation disagreed)

An unhandled exception anywhere in 5-17 exits **1** (Python's own), and the attempt is counted: it
is a `reveal_open` with no `reveal_complete`, exactly as §6's table says.
```

Four orderings in there are decisions:

- **The fingerprint is built at step 4, before step 5**, so every number in it predates the first
  sealed read. A fingerprint taken after the reads would be a description of the result.
- **Scoring (step 12) does no reads.** The parent design's call 2 — *"everything fallible happens
  outside the logged read, so that k is almost always 1"* — is implementable only if the inputs are
  all in hand first, which is why stages B, C and D finish before `score` starts.
- **`record_reveal_completed` is step 16, after the write-up's figures are computed and before they
  are printed.** If printing fails, the record is already complete and the figures are in
  `details`; if `details` could not be built, nothing claims the reveal completed.
- **Writing it before exits 13 and 14 is the honest state, not an oversight.** By step 16 the
  holdout *has been read*: the sealed looks are logged and the figures are in hand. The seal's
  claim is that nobody has seen the data, and that claim is false once the reads happened,
  whatever is written afterwards. Leaving `reveal_open` unpaired would keep the machinery sealed
  while the data was seen, and let a later run present itself as the clean first read. So the
  pair is written, and the burden moves to telling the operator exactly what state they are in.
- **Every exit prints what the record says about the seal, derived from the record and from
  nothing else.** The last line of output is `SEAL_SENTENCES[seal_state(audit_tally, committed)]`
  -- never a function of the exit code, because exit 1 is any unexpected exception and can mean
  "nothing happened" or "the holdout was read and only the printing failed". There are four
  states: *untouched* (neither line exists), *counted* (a `reveal_open` without its completion;
  seal intact), *read, uncommitted* (both lines on disk: the holdout has been read, this run was
  the reveal, and the seal lifts when `data/holdout_access.jsonl` is committed -- the state every
  successful `take` ends in) and *lifted* (both lines committed at HEAD). No gate's own message
  speaks about the seal (witness: `test_the_gate_codes_are_distinct_and_never_reserved`).
- **Exit 0 means completed and determined.** 13 and 14 both mean "completed, with something the
  reader must be told"; 13 wins if both apply, because an undetermined champion is the larger fact.

---

## 9. Decision 8 — the witnesses

Instance witnesses first, then the shape witnesses, which are the ones that answer "there is no
second route" (R134, and HR-2 §7's precedent). Twenty-six witnesses, nine of them shape witnesses.

**Every one runs without reading the real holdout, and the mechanism is stated for each.** Three
techniques do all the work, none of which holds a key against `data/`:

- **(P) pure** — the function is handed its inputs and touches no store, no git and no clock.
- **(C) the carved pseudo-holdout** — `conftest.synthetic_store` on `tmp_path`, whose splits are
  `dev_end 2023-12-31 / holdout_start 2024-01-02 / holdout_end 2024-12-31`, **all three passed
  explicitly** because `Splits.holdout_end` has been a required field since `567616c` and nothing
  may lean on a default that no longer exists. Every `end` used is at or before 2024-12-31, which is
  **below the repository's own `dev_end`**, so `_police_holdout_read` has nothing to object to and
  the test needs no marker. `monkeypatch.setattr(store, "_unlock_granted", lambda _: True)` opens
  the carved window.

  **The stub is `lambda allow_holdout: allow_holdout`, not `lambda _: True`.** HR-3's own witnesses
  use the discarding form, which is right for them — they test the guard, not a caller. Here it
  would be a hole wide enough to hide this design's blocker in: a discarding stub grants the key
  whatever the caller passed, so a stage function that forgot to forward `allow_holdout` would read
  happily in every witness and refuse in production. The identity stub simulates an exported
  variable while keeping the argument real, so **an unthreaded read fails the test.** W7, W9, W10,
  W11 and W24 all depend on this and none of them is meaningful without it.

  **No `allow_holdout` is passed to a read of `data/`, no environment variable is set, and the test
  can assert `os.environ.get("VOLAB_HOLDOUT_UNLOCK") is None` as part of its own contract.** This is
  HR-3's marker-free mechanism, and it is stronger than a marked test: it proves the property
  against a caller who holds both keys while the test holds neither.
- **(S) a scan** — AST or source, over `src/volab/reveal/` or over the whole package.

New file: `tests/test_reveal.py` for everything in `volab.reveal`; `tests/test_reveal_cli.py` for
the exit codes; `tests/test_import_discipline.py` for W17's unlock scan if it is more at home beside
`_unlocks`, whose shape it reuses.

### The gates refuse before anything is written

- **W1 `test_every_gate_is_a_table_and_the_order_is_fixed`** (P) — `gates()` parametrised over the
  seven pre-read gates plus the all-clear, each with one fact perturbed, asserting the exact
  `(code, name)` returned and that a dirty tree is reported *before* a hash mismatch (an operator
  with both should fix the tree first, because the hash of a dirty file is not the hash of a commit).
  Pure: no store, no git, no clock.
- **W2 `test_the_registered_refusal_table_is_what_the_code_produces`** (P) — for each
  `(h, n)` in `REGISTERED_ORIGINS`, `overlap_lag(h, 1)`, `effective_observations(n, q)` and the two
  thresholds reproduce §2's table cell for cell: MCS 10% refused exactly at h ∈ {22, 66}, MCS 25%
  refused exactly at h=66, `claims_withheld` true exactly at h ∈ {22, 66}. **This is the witness
  that ties the registered document to the code's own rules**, and it imports nothing from
  `volab.reveal` except the literals, so it fails if either side moves.
- **W3 `test_the_champion_rule_is_the_sentence_in_section_3`** (P) — `champion_from` over a table:
  one member → NAMED; several → the lowest QLIKE; the set refused → NONE with the MCS's own
  sentence; the lowest-QLIKE member in `unclaimed` while another member is not → **AMBIGUOUS with
  both candidates**, asserted explicitly so the machine's refusal to break the tie is a test and not
  a comment; a tie in QLIKE between two members → AMBIGUOUS (an exact tie is a tie, and §3 gives no
  tiebreak). Hand-built `Comparison` and `Trial` objects; no store.
- **W4 `test_the_registered_literals_are_what_the_document_and_r130_say`** (P) — `REGISTERED_SHA256`
  equals `hashlib.sha256(PREREGISTRATION.read_bytes()).hexdigest()` **and** appears verbatim in
  R130's table in `docs/superpowers/phase2-completion-decisions.md`; `REGISTERED_BLOB`,
  `REGISTERING_COMMIT`, `REGISTERED_TRIALS` and `REGISTERED_COMPARISONS` likewise appear in R130;
  and `REGISTERED_TRIALS` matches §4's own sentence, parsed with the same shape
  `tests/test_preregistration_fence.py::_registered_figures` uses. Reads a markdown file and hashes
  a markdown file. No panel data, exactly as the fence test's docstring says of itself.

### The span is what it says it is

- **W5 `test_every_sealed_read_in_the_plan_is_bounded_at_holdout_end`** (P) — every `Read` in
  `plan(settings)` has `end == settings.splits.holdout_end` and `sealed is True`, and the canary's
  reads have `end == dev_end` and `sealed is False`. Parametrised over the repository's settings
  **and** a carved `Splits`, so a hard-coded date fails it.
- **W6 `test_the_plan_is_the_formula`** (P) — `expected_looks(settings) == 4*S + 2*(V+F) + S*H`
  computed from `len(settings.universe)`, `len(panel.VIX_SERIES)`, `len(panel.FRED_SERIES_NAMES)`
  and `len(HORIZONS)`, for the repository's settings (38) and for a two-symbol carved one. A read
  added to the span without the formula moving fails.
- **W7 `test_the_carved_reveal_logs_exactly_the_planned_looks_before_it_takes_them`** (C) — the
  whole of `take`'s steps 3–10 against a carved pseudo-holdout with a handful of synthetic sessions
  in every raw dataset: assert `store.audit_tally(settings).looks == expected_looks(settings)`, that
  every look line's `reveal_id` is the attempt's, that every look's `end` is the carved
  `holdout_end`, and that **each `look_open` precedes its own `look_close`** in file order. This is
  the span's instance witness and it is also HR-5's rehearsal in miniature; HR-5 widens it to the
  whole procedure on a separate storage root.
- **W8 `test_the_canary_catches_a_changed_dev_frame`** (C) — `compare_snapshots` over two snapshots
  that differ in one cell returns that `(dataset, symbol)`; and a `take` whose stage A is
  monkeypatched to also rewrite a dev-dated partition exits **12** and writes no `reveal_complete`.
  The red arm is the point: the canary has to be seen failing.
- **W9 `test_a_carved_reveal_writes_the_ledger_footprint_its_shape_predicts`** (C) — on the carved
  window, the trial lines written equal `len(h1_cohort) * S * 1 * len(LOSSES) +
  len(above_cohort) * S * H_above * len(LOSSES)` and the comparison records equal
  `configurations * len(LOSSES)`, both derived from the cohorts rather than typed. On the real
  window those are 150 and 24.

### A crashed attempt is visible and lifts nothing

- **W10 `test_a_crash_during_scoring_leaves_an_open_with_no_complete`** (C) — `score` patched to
  raise; assert exactly one `reveal_open`, 38 (carved: the formula's) complete look pairs, no
  `reveal_complete`, and `store._completed_reveal_in(log_text, carved_splits) is False`. The last
  assertion is the one that matters: a crashed attempt lifts nothing.
- **W11 `test_a_second_attempt_needs_a_commit_first_and_is_a_new_id`** (C) — the whole retry
  sequence of §6, in a throwaway git repository on `tmp_path`: attempt 1 crashes; a second `take`
  exits **3** because the log it wrote dirtied the tree; the log and the partial ledger lines are
  committed; attempt 2 runs and completes. Then `audit_tally().attempts` holds two distinct ids,
  `completed` holds only the second, `len(attempts) == 2` is the *k* the write-up reports, and
  `store._completed_reveal_in(log, carved_splits)` was `False` after the committed crash and `True`
  only after attempt 2. The middle step is the one that keeps an implementer from "fixing" retries
  by narrowing gate `dirty_tree`.
- **W12 `test_a_missing_key_costs_an_attempt_and_no_look`** (C) — with `_unlock_granted` left alone
  (so the carved window is sealed to the test), `take` exits **10**, the log holds one
  `reveal_open`, `tally.looks == 0`, and no `look_open` exists. The witness for §2's stated price.
- **W13 `test_the_tally_counts_looks_and_attempts_separately`** (P) — `audit_tally` over a
  hand-written log holding junk lines, an unmatched `look_open`, a complete pair, a `reveal_open`
  with no complete and a complete pair of reveal lines: `looks`, `looks_closed`, `attempts` and
  `completed` are each exactly right, and the function does not raise on any of the malformed lines.

### Nothing is computed that was not registered — shape witnesses

- **W14 `test_the_reveal_claims_only_what_section_2_registers`** (S) — AST over
  `src/volab/reveal/`: the call to `compare.analyse` passes `benchmarks=` with an empty tuple
  literal (or the module constant that is one), and no name `superior_predictive_ability`, `SpaResult`
  or `BENCHMARKS` imported from `volab.compare` appears anywhere in the package.
- **W15 `test_the_reveal_computes_no_statistic_of_its_own`** (S) — the package imports no numpy,
  scipy or arch name directly; **no function in it contains `sum`, `/`, `**`, `mean`, `std` or any
  numeric-library attribute applied to a loss value**; and the only names it imports from
  `volab.compare`, `volab.walkforward` and `volab.leaderboard` are on an explicit allow-list
  asserted in the test. **Ordering stored values is not computing a statistic and is permitted:**
  `champion_from` must take a minimum over QLIKEs that `run_configuration` already computed, so the
  scan allows `min`, `max` and `sorted` and bans arithmetic. The distinction is the one that
  matters — a minimum of stored numbers cannot disagree with the ledger, and a re-derived mean can.
  Patterned on `test_the_leaderboard_imports_no_statistics`, which exists for the same reason:
  a module that recomputes a number can disagree with the thing it reports.
- **W16 `test_no_refusal_sentence_is_worded_twice`** (S) — no string constant in
  `src/volab/reveal/` contains `"REFUSED"`, `"no significance"`, `"n_eff"` or `"MCS"`; every such
  sentence comes from `compare` or `compare.sink`. A paraphrase here is a second rule.
- **W17 `test_the_reveal_never_puts_the_key_in_the_environment`** (S) — the coordinator's refusal,
  made checkable. Over every file in `src/volab/reveal/`, reusing `_unlocks`' shape from
  `tests/test_import_discipline.py` (AST, with docstring constants exempt so the prose that
  promises the ban does not trip it): **no assignment whose target is a subscript of `os.environ`
  or `environ`; no call to `os.putenv`, `putenv`, `setenv` or `os.environ.setdefault`/`update`; and
  no string constant containing `VOLAB_HOLDOUT_UNLOCK` outside a docstring.** Passing
  `allow_holdout=` is permitted here and only here, so the test asserts that too — but by **call
  site set, not by count**: every `allow_holdout` keyword in the package has the constant `True`,
  and the set of functions containing one is exactly the stage functions, named explicitly in the
  test. (An earlier draft said "exactly once", which was wrong by seven: the key is threaded through
  seven call sites, see §5.) Red arms, each
  planted in a source string and watched to fail: `os.environ["VOLAB_HOLDOUT_UNLOCK"] = "1"`,
  `os.environ.setdefault(KEY, "1")`, `os.putenv("VOLAB_HOLDOUT_UNLOCK", "1")`,
  `KEY = "VOLAB_HOLDOUT_UNLOCK"`. Green arms: a docstring and a comment naming the variable.
- **W18 `test_the_reveal_does_not_reach_the_decision_evaluators`** (S) — nothing under
  `src/volab/reveal/` imports `volab.evaluators`, directly or through a parent `__init__`, using the
  transitive graph `test_the_point_in_time_path_reaches_no_numpy_or_scipy_even_indirectly` already
  builds. §5 of the registration is the reason.
- **W19 `test_the_reveal_names_no_second_cutoff`** (S) — in `src/volab/reveal/`, every `end=`
  keyword in a call is either `settings.splits.holdout_end`, `settings.splits.dev_end` (the canary
  and only the canary), or a parameter named `end`; and no date literal appears anywhere in the
  package except in `REGISTERED_*` and in a docstring. One cutoff, one place.
- **W20 `test_the_reveal_opens_no_path_and_names_no_log`** (S) — `volab.reveal` contains no
  `open(`, no `Path.write_*`, no `AUDIT_FILENAME` and no `"holdout_access.jsonl"`; every write goes
  through `compare.sink.JsonlSink`, `store.write_curated`, `store.write_features`,
  `features.targets.write_targets` or the three `store.record_*` functions. The existing
  `test_only_one_function_opens_the_audit_log` and `test_only_the_sink_opens_a_path` already assert
  half of this; this one closes it for the new package.
- **W21 `test_the_import_ratchet_is_untouched`** (S) — `ALLOWED == {"store.py"}`, `SEALED` and
  `SEALED_PACKAGES` unchanged, and `volab.reveal` is in neither. So a reader can see that the new
  package bought no exemption.
- **W25 `test_only_the_reveal_ever_passes_the_key`** (S) — the witness the threading in §5 cannot go
  without. Over every module under `src/volab/`: an `allow_holdout=True` keyword appears **only** in
  files under `src/volab/reveal/`; the seven threaded functions each declare it keyword-only with a
  `False` default and forward it unchanged to exactly one `load_panel` call; and
  `walkforward.load_for`, `compare/__main__`, `evaluators/__main__` and `walkforward/__main__`
  contain no `allow_holdout` at all, which is what keeps the three research entry points research
  entry points now that `load_targets` accepts a key. Red arm: a planted
  `load_targets(..., allow_holdout=True)` inside `load_for`'s body fails it.
- **W26 `test_the_champion_is_rank_one_among_the_members`** (P) — `champion_from`'s answer and
  `leaderboard.ranked_trials`' order agree: for a hand-built `(SPY, h=1, qlike)` record and its
  trials, the NAMED champion is the highest-ranked 10% member in `ranked_trials`' order, and in the
  AMBIGUOUS case both candidates are members and neither is named. Two orderings of one set of
  numbers cannot be left to agree by coincidence.

### The CLI

- **W22 `test_check_writes_nothing_at_all`** (C) — `check` against a carved store with every gate
  passing and with each gate failing: no audit file is created, no partition is written, no ledger
  line is appended, and the bytes of both ledgers are identical either side. The command that is
  safe to type repeatedly is asserted to be safe.
- **W23 `test_every_exit_code_is_reachable_and_distinct`** (C) — a parametrised table over
  3…14 and 0, each reached by one fixture state, asserting the code and that the first line of
  output is that gate's message. 2 is asserted to be `argparse`'s and nothing else's.
- **W24 `test_the_write_up_reports_both_counts_and_names_its_command`** (C) — the carved attempt's
  final block contains the look count, `k` with "the k-th completed", the two trial counts and their
  delta, the two comparison counts and their delta, the champion block, and **the exact command line
  that produced it** — R32, and §3's third definition of failure is "a figure with no command that
  reproduces it".

---

## 10. Decision 9 — what the implementer must not do

A refusal list of twenty-two items, in HR-2 §8's style. Each is something a reasonable builder
might do, and several of them are things an earlier draft of this design did.

1. **Must not read the real holdout.** No `allow_holdout=True` against `data/`, no setting or
   reading of `VOLAB_HOLDOUT_UNLOCK`, no `@pytest.mark.holdout`, no `load_panel` at an `end` past
   2025-12-31 against the repository's store. Every witness runs on the carved window. If the
   implementer finds itself about to, it stops and reports, as the board says.
2. **Must not put the unlock key in the environment.** The module may pass `allow_holdout=True`; it
   may **not** assign to `os.environ`, call `os.putenv`/`setenv`, or name `VOLAB_HOLDOUT_UNLOCK`
   outside a docstring. The variable is exported by a human in a shell and read by `store` alone. A
   reveal that set it would be one typed command with no key held — anti-goal #1 by another route.
   W17 is the witness and it must be written before the stage functions, not after.
3. **Must not leave `data/` changed.** `data/holdout_access.jsonl` stays **absent**,
   `data/trials.jsonl` stays at **414** lines and `data/comparisons.jsonl` at **52** when the story
   closes. Every record any witness writes goes under `tmp_path`. Creating the real audit file would
   lift nothing — it would be uncommitted — but it would be the first line of a log whose count is a
   reported number.
4. **Must not edit `docs/holdout-preregistration.md`.** It is registered at `65617ee` with both
   hashes in R130. If something in it genuinely requires a change, that is a design defect: stop and
   report it rather than editing. A change means a new registration and a new look, both counted.
5. **Must not resolve OQ1 in code.** If the two readings of §3 disagree, `champion_from` returns
   `AMBIGUOUS` and `take` exits 13. Picking the member the numbers favour is the exact failure this
   apparatus exists to prevent, and it would be invisible in review because the code would look
   decisive.
6. **Must not add a statistic.** No SPA, no decision evaluators, no third loss, no extra MCS size,
   no pooled-across-symbols ranking, no bootstrap the registration does not name. §1–§3 is the list.
7. **Must not reimplement anything that computes a number.** `run_configuration`, `analyse`,
   `describe`, `loss_series`, `monte_carlo_error`, `ranked_trials`, `count_trials`, `append_trials`
   and `JsonlSink` are called. The duplicated forty-line driver loop is the only copy, and it copies
   structure, not statistics.
8. **Must not give `walkforward.load_for` an `end` parameter**, or change it at all. Its pin at
   `dev_end` is a research boundary and stays forever; its docstring says so and it is right.
   Likewise `compare/__main__`, `evaluators/__main__` and `walkforward/__main__` keep their R62
   refusal of `--to` past `dev_end` — HR-7 triaged all three as research pins.
9. **Must not write a dev-dated partition.** Stage B filters to `as_of > dev_end` before writing.
   Stage A cannot, so the canary is mandatory: it is not an optional belt beside stage A's braces,
   it is the only check that the rewrite was a no-op.
10. **Must not skip the canary when retrying**, and must not cache its "before" snapshot across
    attempts. A second attempt takes its own before-and-after.
11. **Must not take the ledger counts after appending.** `trials_at_read` is read at step 4, before
    `record_reveal_opened`. A count taken later includes the reveal's own lines and the delta stops
    meaning what §4 asks it to mean.
12. **Must not add a resume, skip-derive, offline or rehearsal flag to this package.** HR-5's
    rehearsal works by a separate storage root, as the parent design decided. A flag that skipped
    the looks would make them invisible.
13. **Must not add a second route to the lift.** `volab.reveal` writes the record through
    `store.record_reveal_opened` / `record_reveal_completed` and nothing else. It does not write the
    log, does not touch `_REVEAL_CACHE`, never calls `_reset_reveal_cache` (W23 in
    `test_store_seal.py` fails if any call site appears under `src/volab`), and does not commit the
    record — **committing is a human act**, which is the whole basis of "lifting the seal requires a
    commit, visible, reviewable and attributable".
14. **Must not import a private name from `store.py`.** The ratchet forbids it and caught
    `walkforward` reaching for `_git_state`. Use `store.git_state`, `store.reveal_is_committed`,
    `store.reveal_scope`, `store.record_reveal_opened`, `store.record_reveal_completed` and the new
    `store.audit_tally`, and nothing else.
15. **Must not name the audit log.** Not `AUDIT_FILENAME`, not the string. The tally comes from
    `store.audit_tally`. Adding it is the one edit to `store.py` this story makes, and it must come
    with its `_PUBLIC` and `owners` updates in `tests/test_store_seal.py` in the same commit — a
    witness left red is what closed HR-3 wrongly and the board says so.
16. **Must not word a refusal twice.** Every "REFUSED", every `n_eff` sentence and every withheld
    reason comes from `compare`. W16.
17. **Must not use `uv run` bare or `--frozen`.** `uv run --locked …` only. No new dependency;
    `pyproject.toml` and `uv.lock` are not touched.
18. **Must not weaken `tests/conftest.py`.** `_forbid_holdout_unlock`, `_police_holdout_read`,
    `_clean_volab_environment` and the `_reveal_committed` stub all stay. This story adds nothing to
    them: its witnesses are marker-free by construction.
19. **Must not make the key ambient.** No contextvar, no module global, no `Settings` field, no
    environment read inside `volab.reveal`, no default of `True` anywhere. `allow_holdout` is an
    argument passed explicitly at each of the seven threaded call sites, and `reveal_scope` — or
    any scope — must never imply it. W25 and W17 are the witnesses; §5 is the reasoning.
20. **Must not narrow gate `dirty_tree` to make retries easier.** `_git_state`'s
    repository-scoped pathspec excludes the three derivable tiers and **must keep including**
    `data/holdout_access.jsonl`, `data/trials.jsonl` and `data/comparisons.jsonl`. A retry commits
    the previous attempt first (§6); that is the mechanism, not an obstacle to it.
21. **Must not forward `allow_holdout` anywhere it is not needed.** The seven threaded functions
    and stage D's direct `load_panel` call, and nothing else. In particular not `load_for`, not
    `load_curated_panel` (the reveal assembles its own panel and does not call it), and not into
    `build_features`, `build_targets` or any writer, none of which reads.
22. **Must not mark the story done on assertion.** A commit, green tests and green gates, with
    the three tripwires in item 3 verified by command, or it is not done.

---

## 11. What does not survive contact with the code

### Findings against the registered document

1. **§1's usable-origin table is a prediction and the code produces it, but the two can only be
   compared after the read.** §5 says so explicitly — *"the holdout's own `n_eff` refusals are
   derived from the window, not chosen, so they are predictions here rather than commitments"* — so
   a mismatch between 208/204/187/143 and the actual counts is **reported, not refused**
   (`Scored.origins` beside `REGISTERED_ORIGINS`). The window's *end* is a different matter and is a
   commitment, which is why gate 9 exists. Today the raw tier stops at **2026-10-20**, six sessions
   short of `holdout_end`, so **gate 9 fires on the repository as it stands** and ingestion must run
   before the reveal can be taken. That is a fact about the repository, not a defect.
2. **The registered document contains two incompatible statements about when the reveal may run, and
   R130 repeats one of them.** §1 and the parent design's ratified decision are *read only to
   `holdout_end` and drop the unfinished tail* — the whole point of the 208/204/187/143 table. The
   closing paragraph says the reveal *"cannot run before `holdout_end` (2026-10-30) plus each
   horizon's tail"*, and R130 says h=66 *"needs about three further months"*. Taken literally the
   reveal cannot be taken before roughly 2027-02-05. **It is left unresolved (OQ2)**, and the one
   thing that makes it safe to leave unresolved is that it is figure-neutral: the read is bounded at
   `holdout_end`, `load_panel` filters on `as_of`, and a target stamped 2027-02-05 is invisible to a
   read bounded at 2026-10-30 **whenever that read happens**. Waiting three months changes no
   number. So gate 6 is `today > holdout_end`, `tail_completion()` is computed and printed beside
   it, and the stricter reading is one line in a pure function if the user says it binds.
3. **§4 registers two figures and the reveal's own sentence mentions one.** *"The reveal takes a
   second count and records both plus the delta"* appears under a section that pins both
   `trials.jsonl` (414) and `comparisons.jsonl` (52). The reveal records both pairs; recording one
   would leave half the registered snapshot unmeasured.

### Findings against the parent design and HR-2

4. **`git_dirty` on the audit lines is not the flag the fingerprint needs, and it will read `true`
   for reasons that are not the code's.** `_open_look`, `_close_look` and `record_reveal_completed`
   call `store._git_state()` with the **package** scope (`cwd = src/volab`, pathspec `.`), so their
   flag answers "was the read path modified" and cannot see `data/` or `config/` at all. The
   reveal's own flag is `git_state(_REPO_ROOT)`, repository-scoped, taken once before anything is
   written (R70). And note the asymmetry the pathspec creates: `_git_state`'s repo-scoped status
   excludes `data/raw`, `data/curated` and `data/features` but **not** `data/trials.jsonl`,
   `data/comparisons.jsonl` or `data/holdout_access.jsonl`, all three of which are tracked by
   `!`-exceptions. So the reveal's first audit line makes the repository dirty — **by writing its own
   evidence**, not by changing code. Consequences: gate 1 must run before anything is written (it
   does), the fingerprint's flag is the authoritative one, and a reader who sees `git_dirty: true` on
   a `reveal_complete` line must not read it as "this run is not reproducible". The stage A and
   stage B partition writes are invisible to the flag, which is exactly why that pathspec exists.
5. **HR-2 §4's rationale for the `holdout_end` clamp, and §8 item 17, argue from a default that no
   longer exists.** `Splits.holdout_end` has been required since `567616c` ("P4-05b", 2026-11-05).
   The board already records this; it is restated here because the landed `load_panel` **step 0b
   comment still says** *"`Splits.holdout_end` defaults to `dt.date.max` so every hand-built Splits
   would otherwise be refused"*, which is now false. The clamp is still correct and W19 in
   `test_store_seal.py` still tests it; only the reason is stale. Every `Splits(...)` in HR-4's
   witnesses passes all three dates. Fixing that comment is a one-line docs change in `store.py` and
   is **not** HR-4's: it belongs to whoever next edits that file, and it is recorded here so it is
   not lost.
6. **HR-2's Q4 collapses two different numbers into one sentence, and the multi-read span pulls them
   apart.** `count(kind == "look_open")` is the number of sealed *reads* and is the right replacement
   for the audit log's raw line count. The parent design's *k* in "k logged looks, the k-th
   completed" is the number of *attempts*, which is `count(kind == "reveal_open")`. With one attempt
   spanning 38 reads these are 38 and 1, and "the 38th completed" is not a sentence anyone can mean:
   a `look_open` pairs with a `look_close` and has no completion. The write-up reports both, named
   differently (§5). Three documents — `.gitignore`,
   `docs/superpowers/plans/2026-10-19-phase0-skeleton.md:98` and
   `docs/superpowers/phase0-decisions.md:69` — want the "line count" sentence adjusted when the
   reveal is taken, and now want it adjusted to the *look* count specifically.
7. **The parent design's "the reveal spans several reads" is right and its "one attempt id, every
   read under it attributed to that id" is implemented by `reveal_scope`** — but the number is 38,
   not "several", and 24 of those 38 are reads the parent design did not anticipate: it names
   deriving curated and feature rows from raw and does not mention that the *targets* tier also
   stops at `dev_end` (it does: every `targets_rv_h*` partition stops at 2025-12-31), nor that the
   persisted feature table must be read back for the truncation-equivalence check to stay armed.
8. **The parent design's protection 4 asks for "a content hash of the champion's definition" and
   there is nothing to hash that is not already the git sha.** The champion's definition is a
   `Model` assembled in `volab.compare.cohorts` from `volab.models.*`; its content is the tree, and
   gate 1 guarantees the tree is clean. §3 records the configuration verbatim and the clean-tree sha
   as its identity, and declines to invent a second hash that would have to be kept in sync. The
   protection's intent — "we quietly improved it since" must be impossible to say — is met.
9. **The board's "every registered origin's target completed inside it, and no data is outstanding"
   is wrong as written.** 187 of 209 origins have a completed h=22 target inside the window and 143
   of 209 have an h=66 one; the registration's own table says so. The window is closed and the
   *window* is complete; the *tails* are not, deliberately. The board's sentence should say that.

11. **HR-2 §6's interface list is complete for `store.py` and silent about the one thing the
    reveal's callers need: the key.** It threaded `reveal_id` through a contextvar and said why a
    `load_panel` parameter would have to go through "four more signatures" — which is true, and the
    *key* has to go through seven. Nothing in either design noticed that
    `curate_*`, `load_bars`, `load_exogenous` and `load_targets` pass `allow_holdout=False` by
    omission, so a reveal built to either design as written would refuse its own first read. §5
    settles it by threading and W25 pins it. This is also the clearest case in this sprint of a
    witness technique hiding a defect: HR-3's `lambda _: True` stub, copied into HR-4 unchanged,
    would have kept every span witness green.
12. **Look lines from the reveal will not say `volab.reveal`.** `store._caller_frame` returns the
    first frame **outside** `src/volab`, and under `python -m volab.reveal` every reveal frame is
    inside the package, so `caller_module` will be `runpy` (or `unknown`) — not the
    `"caller_module":"volab.reveal"` HR-2 §3's record sketch shows. Derived from the code, not
    measured. It is a cosmetic loss of attribution and the `reveal_id` on every line carries the
    real attribution, so this design does not change `_caller_frame`; it records the discrepancy so
    nobody reads an audit line as evidence that the reveal was *not* the caller.
13. **`committed_blob` is a second git call site and must carry the same two fixes.**
    `--no-optional-locks` and the 5-second timeout, copied from `_git_state`'s comment rather than
    re-derived, failing closed to `None` — which trips gate `preregistration_blob` rather than
    raising. The one hour this project lost to a missing lock flag is reason enough not to have a
    third call site without it.

### One finding with no owner yet

10. **The reveal at stride 1 is roughly five times the dev run's fitting cost per configuration**
    (208 origins at h=1 against 42 at stride 5, and 143 against 29 at h=66), across twelve
    configurations including three GARCH specifications. Nothing in the design depends on the
    runtime, but the operator should expect one long process, and §8's ordering is what keeps a
    crash in hour three from costing more than one counted attempt. If the measured runtime turns out
    to be prohibitive, that is a fact to report, **not** a reason to change the stride: §1 registers
    stride 1 and a change means a new registration.

---

> **Status after the rulings (2026-11-11).** OQ1 was settled by the user as R216 and OQ2 as R217
> (`docs/superpowers/holdout-reveal-decisions.md`): a contestant whose significance R115 withholds
> *is* eligible to be champion (`champion_from` returns `NAMED` with the withholding stated in the
> same breath; `AMBIGUOUS` and exit 13 remain for an exact QLIKE tie), and the window closing is
> enough to take the reveal (gate `window_open`). The entries below are kept as written; OQ3-OQ6
> remain open.

## 12. Open questions, left unresolved on purpose

These are places where the registered document does not decide. **Each is left open deliberately.**
A stated "this cannot be settled from what we wrote down" is a result; settling it in whichever
direction the numbers favour is the failure this whole apparatus exists to prevent. None of them is
a reason to edit the registration — that would mean a new registration and a second counted look.

- **OQ1 — may a contestant whose significance is withheld be the champion?** §3 names exactly two
  criteria: membership in the 10% set, and the lowest QLIKE. §1 and §2 withhold every significance
  claim for `egarch11_skewt` at h=1 under R115, and §1 notes it is *"the contestant currently
  leading h=1 QLIKE"* on the dev run — so the case is likely, not hypothetical. Reading (a), the
  literal text: membership plus lowest QLIKE, so a withheld-significance member can be champion,
  carrying its withholding as a disclosure. Reading (b): "champion" is itself a claim, so a
  contestant R115 withholds cannot carry it, and the champion is the lowest-QLIKE member whose
  significance is not withheld. **The document distinguishes neither.** `champion_from` returns
  `AMBIGUOUS` with both candidates, `take` exits 13, and the user states the reading in the
  write-up. *Who can settle it:* the user, and it must be settled **before** the read — not
  merely "cleanest". Exit 13 still writes `reveal_complete`, and once that is committed the seal is
  lifted and both candidates' QLIKEs are on screen, so a ruling made afterwards is selection on the
  numbers by construction, which is the one thing this apparatus exists to prevent. Before the read
  the decision has no numerical content and costs nothing. **This is the design's one request of
  the user, and it is a blocker on taking the reveal rather than on building it.** Whether a
  clarifying ruling counts as a new registration is also the user's call; this design does not
  propose editing the registered document and must not be read as proposing it.
- **OQ2 — when may the reveal run?** §1 plus the ratified parent decision say "once the window has
  closed"; the closing paragraph and R130 say "`holdout_end` plus each horizon's tail", which is
  about 2027-02-05. Gate 6 implements the former. The choice is **figure-neutral** (finding 2), which
  is why defaulting to the earlier gate cannot favour any result, and why the one-line alternative
  is named rather than argued. *Who can settle it:* the user.
- **OQ3 — `minimum_training` is not registered.** The dev run used the CLI default, 250, and the
  reveal uses 250. It provably cannot bind in this window — every holdout origin has 2,300+ training
  rows — so the under-specification has no numerical consequence here, and it is recorded in the
  fingerprint so that it is a stated parameter rather than an invisible default. *Not settled:*
  whether the registration intended to fix it. Nothing turns on it.
- **OQ4 — does a holdout origin's training set include earlier holdout sessions?** The registration
  names the origin set, the contestants, the losses and the window, and says nothing about the
  training window. `evaluate` truncates the panel at each origin, so training is the expanding
  window through that origin — including earlier holdout sessions. That is the only behaviour the
  harness has, so the **implementer has no decision to make**; what is open is whether the
  registration meant it, and it should be stated in the write-up rather than assumed. Choosing the
  other reading would need new code, and therefore a new registration.
- **OQ5 — "the ranking on each loss" over what grouping?** The code ranks within one run, one loss
  and one `(symbol, horizon)` (`leaderboard.ranked_trials` refuses a mixed group), so that is what
  the reveal reports. Whether §2 also intended a ranking pooled across symbols is not stated, and no
  such ranking exists in this project. *Not settled;* nothing is computed for it.
- **OQ6 — if the actual origin counts differ from §1's table, what then?** §5 makes them
  predictions, so the design reports both (`Scored.origins` beside `REGISTERED_ORIGINS`) and claims
  nothing about the difference. **And the question that looked dangerous is arithmetically vacuous,
  which is better than a resolution:** a registered refusal could only be overturned by an origin
  count larger than the window can hold. DM significance at h=22 needs `n_eff = n/22 ≥ 20`, i.e.
  `n ≥ 440`; the MCS 25% at h=66 needs `n ≥ 528`; and the window is 209 sessions, so `n ≤ 209` at
  every horizon once gate `window_not_ingested` has passed. No achievable count can unrefuse a cell
  §2 refuses. A count *lower* than predicted only refuses more, and is reported. So nothing has to
  be chosen here, and nothing is.

---

## 13. What I deliberately left to the builder

- Every exact refusal sentence beyond the fields each must name (§2's table gives the content, not
  the punctuation), and the layout of the write-up block beyond the items W24 requires.
- Whether `plan()` returns `Read` objects that the stage functions then *execute*, or whether it is
  a declaration the stage functions are asserted against. The second is simpler and the first is
  harder to let drift; the contract (`plan`, `expected_looks`, W6, W7) is the same either way.
- The mechanics of the carved pseudo-holdout fixture: how many synthetic sessions, which exogenous
  series, and whether it lives in `tests/test_reveal.py` or in a shared helper HR-5 can reuse.
  HR-5 will want it, so a helper is likely, but the shape is the builder's.
- The JSON key order and nesting inside `details`, beyond the field list in §3.
- Whether `AuditTally` also exposes the look lines themselves (for a `--verbose` write-up) or only
  the counts. Counts are what the write-up needs.
- Whether `check` prints the plan and the predicted look count, or only the gates. Printing it is
  free and probably right.
- How `score` reports progress — twelve configurations over a long run want some output; the
  content is `run_configuration`'s own `report` callable and the formatting is the builder's.
