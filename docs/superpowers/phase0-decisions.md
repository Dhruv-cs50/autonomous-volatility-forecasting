# Phase 0 — decisions taken during execution

Phase 0 was executed by subagents against the spec and plan, with a fresh reviewer gating
each of the six tasks and a whole-branch review at the end. Along the way 34 decisions came
up that the spec and plan did not settle. They were made without consulting Dhruv, so each is
recorded here with its reasoning and what it costs if it turns out wrong — the point being
that a decision made on someone's behalf should be reviewable, not invisible.

Several are the controller catching defects in its own brief. Those are marked as such.

The numbering is the order they were made.


## R1
 T4's `pytester.makeconftest((pytester.path.parent / "conftest.py").read_text())` reads the pytester tmpdir's parent, not the real suite. Use `Path(__file__).parent / "conftest.py"`. Why: `pytester.path` is a fresh tmpdir, so the plan's expression reads the wrong file or none. Cost if wrong: the policing test passes vacuously and spec rule 7f is unproven.

## R2
 T4 ships two of its four tests red, because they import `volab.store`, which T5 creates. Accept T4 on the two `synthetic_store` tests only; the other two are verified at T5 Step 5. Why: the policing fixture must exist before `store.py` so no later test can unlock unnoticed — the plan ordered it deliberately. Cost if wrong: T4's review sees a partly red file and may fail the task on it, costing one fix round.

## R3
 `DEFAULT_CONFIG_PATH` resolves by walking up from `volab.__file__` to the directory holding `config/base.yaml`, not from the process cwd. Same mechanism resolves `storage.root`. Why: the plan's `test_repo_default_config_loads` calls `load_settings()` with no argument, and a cwd-relative default makes it pass or fail on where pytest was invoked. Cost if wrong: config loads only from the repo root, breaking any later scheduled job that runs elsewhere.

## R4
 delete the `uv init --package` boilerplate (`main()` in `__init__.py`, the `[project.scripts]` entry) in T1. Why: the plan specifies `errors.py` as the only content of the package after T1, and dead scaffolding would reach the reviewer as unexplained code. Cost if wrong: trivial, a stray function.

## R5
 drop `@pytest.mark.holdout` from `test_dev_window_read_writes_no_audit_line`; it performs a locked in-window read. Why: the marker disables the policing fixture, so marking a test that must NOT unlock weakens the very check it sits beside. Cost if wrong: none to behaviour; a marked test simply proves less than it appears to.

## R6
 accept ruff `extend-exclude = ["reference", "docs"]`. Why: verified that this ruff version format-checks Python code blocks inside markdown, so `docs/**/*.md` fails without the exclusion — including the plan's own deliberately stub-bodied test snippets — and `reference/vol_agent.py` is pre-existing material, not project code. Cost if wrong: a stray `.py` placed under `docs/` later would escape lint; no `.py` exists there now.

## R7
 accept the extra `.gitignore` line `!data/**/`. Why: `data/**` ignores the subdirectories themselves, so the `.gitkeep` exception never fires without first re-including the directories; the plan's two-line version does not work. Verified the three `.gitkeep` files are tracked. Cost if wrong: none; the narrower pattern would leave `data/` untracked and the layout undocumented in git.

## R8
 CI must use `uv sync --locked` and `uv run --locked` on every step, not `uv sync --frozen` plus bare `uv run`. Why: the reviewer is right that `--frozen` installs from the lock without comparing it to pyproject.toml, and bare `uv run` re-locks by default, so the plan's own text defeated the constraint it stated. Spec section 9 named the wrong flag while stating the correct intent ("fails if uv.lock is stale"); intent is the binding authority. Spec and plan amended in 445042b. Cost if wrong: none to behaviour; `--locked` is strictly stricter, and a genuinely stale lock now fails CI instead of being silently repaired in the runner.

## R9
 add `pythonpath = ["src"]` to `[tool.pytest.ini_options]` rather than chasing the flag or pinning a non-editable install. Why: it makes the suite independent of a fragile macOS file flag, and it is functionally identical to what the editable install's `.pth` does — both put `src` on `sys.path`, nothing else. Spec section 4 chose the src layout so tests "cannot accidentally import a half-configured working copy"; with a src layout that concern is about the repo root being importable, which `pythonpath = ["src"]` does not do, so the spec's intent is preserved. The editable install stays in place. Cost if wrong: tests would no longer fail if the package became uninstallable, a failure mode CI's own `uv sync --locked` still catches on Linux.

## R10
 fold the reviewer's Minor "keep a CI step that imports volab outside pytest" into this fix round. Why: R9's `pythonpath = ["src"]` is what made tests pass without a working editable install, so it weakened exactly the property spec section 4 wanted from the src layout ("tests exercise the installed package"). A one-line `uv run --locked python -c "import volab"` CI step restores it. I created the gap with R9, so closing it belongs in this round rather than the deferred pile. Cost if wrong: one extra CI step, a few seconds per run.

## R11
 fold the reviewer's Minor about `dataset_glob` accepting a bare `str` (and returning `[]` for an empty list) into Important finding 3's segment-validation fix. Why: same function, same class of defect — an unclosed algebra — and `dataset_glob(root, tier, dataset, "SPY")` silently yielding one pattern per letter is the same silent-widening failure as a glob character. Keeping them apart would mean two passes over five lines. Cost if wrong: a caller that relied on passing a bare string now gets an error, which is the point.

## R12
 "resolves" in spec rule 5 means LEXICALLY; the symlink gap the reviewer flagged is intended, not a defect. Why: the containment rule exists to catch a config typo aiming the store at /etc or a sibling project, not a hostile symlink, and `Path.resolve()` would make config validation depend on filesystem state and fail before `data/` exists. Spec rule 5 amended to say so. Cost if wrong: a symlinked `data` dir escapes containment — but anyone able to create it already has write access to the repo and could point the config there directly.

## R13
 promote Minor 3 (dotted top-level keys bypass the duplicate-key guard) into the fix round. Why: it is the exact silent-last-wins defect the `_UniqueKeyLoader` was added to prevent, and the value it can silently override is `dev_end` — the boundary this whole phase exists to defend. Not polish. Cost if wrong: a few lines of key validation nobody needed.

## R14
 promote Minor 4 (no boundary-exact tests for rule 4) into the fix round. Why: rule 4 is the rule this task exists to protect, and the reviewer showed a one-day mutation (`gap_start = dev_end + 2d`) survives the committed suite, with the only incidental catch coming from a test about env overrides. A rule guarded by accident is not guarded. Cost if wrong: two extra test cases.

## R15
 promote Minor 5 (`entry.strip()` silently repairing symbols) into the fix round, scoped — strip only the comma-separated env form, reject surrounding whitespace in YAML list entries. Why: it directly contradicts the binding constraint "never silently repair or clip a bad configuration", and `" SPY"` in YAML is a typo worth surfacing while `"SPY, QQQ"` in an env var is normal syntax. Cost if wrong: a config with padded symbols now errors instead of working.

## R16
 promote Minor 7 into the fix round, but ONLY the three `universe` cases that share a single `match="universe"` across empty / missing / non-list failures. Why: an over-broad match lets a test pass for the wrong reason, which is the specific failure mode I asked this task to rule out. The `extra`, `typo` and `calendar` patterns are distinctive enough in context. Cost if wrong: trivial.

## R18
 fold Minors 2 and 4 into Task 5 rather than opening a fix round on an approved task. Why: Task 5 must already edit the policing fixture (R17) and is the task that makes those two red tests go live, so the edits land in the one dispatch that owns that code. Minor 2 — the pytester test runs in-process, so the outer autouse wrapper masks a defect confined to the copied conftest; `runpytest_subprocess()` plus a stricter `fnmatch_lines` anchor fixes it. Minor 4 — a naive `ingested_at` passed to `write()` surfaces as an unclear schema or path error. Cost if wrong: Task 5's dispatch carries three extra small edits.

## R19
 do NOT add in-tree tests for the `write()` knobs (reviewer's Minor 1). Why: Task 5's own 21 tests exercise every knob hard — `as_of=None`, `drop_columns`, `ts_naive`, `tier=RAW` with differing `ingested_at` — so dedicated fixture tests would duplicate that coverage one task early. Cost if wrong: a fixture regression shows up as a Task 5 test failure instead of a fixture test failure, which is still a failure.

## R20
 step 13 correctly fires on `past_dev_end`, not on holding both keys (reviewer's Minor 8, which asked for intent rather than a change). Why: the file is `holdout_access.jsonl` and a both-keys read inside the dev window touches no sealed row; padding the log with reads that saw nothing would dilute its line count, which is the number the eventual write-up must report. Spec rule (d) amended to say so explicitly. Cost if wrong: an unlocked in-window read goes unrecorded, which is the intended reading.

## R21
 fold reviewer Minors 1, 2, 3, 4, 5 and 6 into Task 6's dispatch rather than opening a fix round on an approved task. Why: minors do not enter the loop, but the final review allows only ONE fix wave, and these six are each one or two lines on code Task 6's reviewer will see anyway. Minor 1 (`columns=[]` returns an empty frame, violating the stated never-return-empty invariant) is the only one touching a spec invariant and is the reason the batch is worth carrying rather than deferring. Cost if wrong: Task 6's review surface is slightly wider than its own ratchet.

## R22
 on Minor 4, DROP the Hive `date` column before returning. Why: it is a partition artefact that duplicates `as_of`, nothing in the contract promises it, and no test pins the column set — so leaving it invites downstream code to depend on an accident. Step 9 guarantees the two agree, which is exactly why `date` carries no information a caller needs. Cost if wrong: a caller wanting the partition value reads `as_of` instead.

## R23
 close the two bypasses the implementer honestly flagged in the ratchet itself, before review. Why: the scan's whole purpose is to fail on a FUTURE change, and `from pandas import read_parquet` followed by a bare `read_parquet(...)` is an `ast.Name` rather than an `ast.Attribute`, while `import pyarrow.dataset` and `from pyarrow import dataset` are not banned at all. A ratchet with a documented bypass invites the exact edit it exists to stop, and this one is what later makes a research agent's missing `read_parquet` tool enforceable rather than decorative. My brief specified the narrow scan, so this is my defect. Cost if wrong: a slightly broader ban that could flag a legitimate future pyarrow use in a non-store module — which is precisely the case that should come to a human.

## R24
 fix Important 1 — `SRC.glob("*.py")` is non-recursive, so any future subpackage escapes the ratchet. Why: `src/volab/agent/tools.py` is precisely where Phase 5's research-agent tooling lands, and a `pd.read_parquet` there would pass silently — defeating the one restriction that makes the agent's missing read_parquet tool enforceable. Latent today (no subpackage exists), load-bearing the moment the package grows. My brief specified the narrow glob. Cost if wrong: none; rglob with a relative-path ALLOWED comparison is strictly broader and the test tree stays out of scope.

## R25
 fold Minors 3, 4, 5, 8 and the `volab.store` private-import half of Minor 2 into the same round. Why: each is one or two lines on code this round already touches, and Minor 5 is an inconsistency in the very guard I asked for last round (`symbols` rejects a bare string, `columns` does not, and `not columns` raises on a numpy array or Index). The private-import ban closes `from volab.store import _scan`, which would read Parquet with no guard at all. Cost if wrong: trivial.

## R26
 do NOT add `polars`/`fastparquet` to BANNED_ROOTS (reviewer's Minor 2, first half). Why: neither is a dependency, so banning them is speculative scope the ratchet does not need; `scan_parquet` as a banned NAME is free insurance and is included. Cost if wrong: if polars is ever added, the ratchet needs one more root — a one-line change a reviewer would catch.

## R27
 accept the implementer's reported order change — the empty/bare-string `columns` guard now runs after the `UnknownSymbol` check rather than before it. Why: the columns guard is not one of the 13 documented steps, so it violates no part of the fixed order, and reporting an unknown symbol first is the better diagnosis — universe membership is the more fundamental mistake. No test depended on the old order. Cost if wrong: a caller making both mistakes at once hears about the symbol first.

## R28
 close both spellings by adding store's private read helpers `_scan` and `_matching_files` to BANNED_CALLS, rather than writing a new rule. Why: `_scan` as a banned NAME is caught by the existing ImportFrom-alias branch (closing `from ..store import _scan` at any relative level) and by the existing Attribute branch (closing `store._scan(...)` and `volab.store._scan(...)`), so one list change closes two spellings through machinery already tested — and store.py, where they are defined, is exempt so its own definitions are not flagged. Keep the module-level private rule too, extended for `level > 0`. Cost if wrong: a non-store module naming a local helper `_scan` would be flagged, which is a name worth avoiding there anyway.

## R29
 I-4 gets BOTH fixes the reviewer recommended, implemented as a dev_end comparison rather than a provenance marker. Why: the hole is not an invalid `Settings` (dev_end 2099 with holdout_start 2100 passes every rule) but one whose boundary differs from the REPOSITORY's. So `load_panel` compares an explicitly-passed `settings.splits.dev_end` against the repo's configured value and refuses a later one, which costs no churn because `synthetic_store` already uses the repo's own 2023-12-31. A provenance field would have forced an escape hatch through every test. Plus the suite-level wrap, plus the threat model written into spec section 7. Cost if wrong: a legitimate future caller wanting a later dev_end must change config, which is the point.

## R30
 on the spec/plan tension the reviewer surfaced (the audit log is "a number the final write-up must report" while the plan's exit criterion wants it gitignored), UNTRACK IT NO LONGER — remove `data/holdout_access.jsonl` from .gitignore and make it a tracked research artefact. Why: for a project whose thesis is an honest harness, the audit log IS the evidence, and an artefact that `git clean -xdf` erases cannot be the number anything reports. Cost if wrong: an unlocked read dirties the tree, which is a feature here.

## R31
 adopt the reviewer's R20 completion — record a line on a REFUSED unlocked read too. Why: as built, a key-holder whose holdout read fails leaves no trace, so existence, emptiness and schema of post-dev_end data can be probed without limit while the log's line count stays clean. Unrecorded probing of the sealed window defeats the purpose of counting accesses. Cost if wrong: the log gains lines for attempts, which must be distinguishable from successes — so the record carries an outcome field.

## R32
 R19's premise was partly false, as the reviewer found — `tier=FEATURES` was the one carry-forward that fell through and has no read test. Fixing in this wave.

## R33
 close the new Important defect now rather than surfacing it as a residual. Why: `symbols=["SPY","SPY"]` now raises `SchemaError` claiming the STORE is corrupt, where before this wave it returned the correct rows — a regression, and one that misattributes a caller mistake to the data, which this project treats as part of its contract. The reviewer verified it by probe and said it would merge once the line lands. A one-line de-duplication is smaller than the cost of shipping a known regression into Phase 1, so the no-second-wave rule yields to it. Cost if wrong: one extra round on a one-line change.

## R34
 also fix new-breakage Minors 2 and 4, both one-liners that prevent an error from masking another. #2 — the audit write on the refusal path is unprotected, so a failing `_append_audit` replaces the refusal the caller should have seen, contradicting the module's own "re-raised untouched" promise. #4 — the conftest `sys.modules` sweep calls `getattr` on every module, so a PEP-562 `__getattr__` raising anything but AttributeError would break every test at fixture setup. Cost if wrong: trivial.

## R35
 record new-breakage Minor 3 in the spec rather than changing it — every explicit-`settings` call now hard-depends on the repo's `config/base.yaml`, so using the library against a store outside the checkout raises `ConfigError` where it previously worked. That is what R29 asked for and it fails loudly; it belongs documented in section 7g, not reverted. Cost if wrong: a future out-of-checkout caller needs a config file, which is the enforcement working.

## Deferred findings, carried into Phase 1

Reviews raised these and they were judged not worth fixing in Phase 0. The whole-branch
review triaged them: four were promoted and fixed, and these are the remainder. Each line is
the finding as the reviewer wrote it.

- Task 1: minor (deferred): no `permissions: contents: read` block in ci.yml — cheap hardening, no current need since the workflow does not use the token.
- Task 1: minor (deferred): `requires = ["uv_build>=0.13.1,<0.14.0"]` carries an upper bound against the lower-bounds-only rule; uv-generated build backend pin, different category from runtime deps.
- Task 1: minor (deferred): HoldoutViolation docstring sentence "This includes the post-read assertion." is terse without the spec at hand.
- Task 2: minor (deferred): stamp drops sub-second precision, so two raw ingests inside one second for the same source/symbol/date collide on filename. Matters only if raw writes are not append-only; revisit in Phase 1 when real ingestion exists.
- Task 2: minor (deferred): `tzinfo is None` check misses a tzinfo whose `utcoffset()` returns None.
- Task 2: minor (deferred): `_partition` accepts a `datetime` (a `date` subclass) and emits `date=2023-06-15T00:00:00`; `parse_path` rejects it, but the builder should fail first.
- Task 2: minor (deferred): `parse_path` labels the raw source segment as "dataset" in its error message, where the builder says "source" — cosmetic.
- Task 2: minor (deferred): a non-`str` element in `symbols` raises `TypeError` not `ValueError`; mypy rules it out.
- Task 2: minor (deferred): the new CI `import volab` step fails locally whenever the macOS hidden flag is set on volab.pth. Accepted and in fact useful — it is now the only check that surfaces the flag, since pytest no longer depends on the .pth.
- Task 3: minor (deferred): `validate_segment` allows inner whitespace and treats `spy` and `SPY` as distinct symbols. Case sensitivity is defensible for tickers; whitespace is not obviously so. Revisit if real ingestion produces odd symbols.
- Task 3: minor (deferred): schema and env map are conflated — accepted config keys are `_ENV_OVERRIDES.keys()`, so adding a non-overridable setting later means editing the env map. A separate `_KNOWN_KEYS` would be cleaner. Genuine polish; no current wrong behaviour.
- Task 4: minor (deferred): the report's out-of-tree verification injected a fake `volab.store` via sys.modules, so it proves the fixture against the implementer's reading of the contract, not the real Task 5 behaviour. The report says so itself; not counted as policing verification.
- Task 4: minor (deferred): `synthetic_store` parameters are untyped in the test file; annotating would require importing from conftest. Cosmetic.
- Task 5: minor (deferred): the bare-string `symbols` guard is untested (test list fixed at 21); it only hoists `dataset_glob`'s own error from step 4 to step 1.
- Task 5: minor (deferred): `_git_state`'s fallback path is untested; the audit test asserts key presence only.
- Task 6: minor (deferred): `columns=["date"]` still returns the partition column, since the drop applies only to the `columns is None` path. Correct as ruled — an explicit request for a column is honoured.
- Task 6: minor (deferred): item 3's `kind="stable"` dedup tie-break has no direct test; building a tie needs two raw parts sharing an `ingested_at`, which the path layout makes awkward.
- Task 6: minor (deferred): the scan is static and name-based, so dynamic access (`getattr(pd, "read_"+"parquet")`, `importlib.import_module("duckdb")`) remains undetectable. Inherent to an AST check; a determined bypass is a human-review matter, not a test one.
- Task 6: minor (deferred): the `ingested_at` tie-break survivor is the last row in DuckDB scan order, deterministic in practice but not contractual.
- Task 6: minor (deferred): the `date`-drop test's synthetic store carries only mandatory columns, so it cannot catch a regression that drops payload columns alongside `date`.
- Task 6: minor (deferred): the ban is name-based, so a future non-store module defining or calling an identifier named `_scan` or `_matching_files` would be flagged. None exists today; latent only, and that name is worth avoiding outside store.py anyway.
- Task 6: minor (deferred): the relative private-import rule flags `from .store import _helper` inside a subpackage that has its own unrelated `store.py`. Resolving the target path would remove it.
- Task 6: minor (deferred): attribute access to a private store helper NOT named in BANNED_CALLS (a future `store._read_raw(...)`) is still uncaught in the attribute form. Pre-existing, not introduced by the fix.
- Task 6: minor (deferred): the comment at test_import_discipline.py:11-12 says naming the helpers "catches every spelling", which holds for those two names only — slight overstatement.
- Task 6: minor (deferred): the six new bypass cases have no committed red-before-fix evidence; they are parametrised against the shared `_violations` helper, which is itself independently exercised.
