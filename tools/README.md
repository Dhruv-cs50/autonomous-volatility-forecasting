# `tools/` — the measurements the decisions log quotes, as artefacts you can run

Phase 2's headline evidence was quoted output with nothing committed behind it. The board carried
that as a named risk: *"the E1 plant and the three pre-measurement probes were run and their
output quoted, but neither the plant nor the probe scripts are committed, so US-2.1 and US-2.6
rest on quoted output rather than on something a reader can re-run."* This directory closes it.

It is ruling **R32** applied to measurements rather than to guarantees. R32 says a stated
"cannot" must name an executable witness; a stated *number* must name the thing that prints it,
or it is a figure on trust.

## What is not here

**No production code, and this directory cannot become importable as one.** It sits outside
`src/`, carries no `__init__.py`, and is not part of the `volab` distribution.
`tests/test_import_discipline.py` globs `Path(volab.__file__).parent` — `src/volab` and nothing
else — so nothing here widens the import ratchet, and `ALLOWED` stays `{"store.py"}`. Every read
below goes through `volab.store.load_panel` with an explicit `end`, like any other caller: no
script here names Parquet, and none of them sets `allow_holdout` or reads
`VOLAB_HOLDOUT_UNLOCK`.

**No new dependencies.** `pyproject.toml` and `uv.lock` are untouched. `ruff check .` and
`ruff format --check .` cover this directory as they cover everything else; `mypy`'s `files` stays
`["src"]`, because these are probes and not shipped code.

**Nothing is appended to the trial ledger.** `data/trials.jsonl` is written only by
`python -m volab.walkforward run`. `tools/measure_calibration.py` calls `evaluate` directly and
writes nothing — see R79 for why its *uncorrected* figures must not be reported as a result.

## The artefacts

| Artefact | Reproduces | Cost |
|---|---|---|
| `e1_plant.patch` | E1 in `src/volab/features/realized.py`, spelled so the AST scan cannot see it | — |
| `reproduce_e1.py` | "passes Phase 1's method, caught by Phase 2's" — both suites and all three sweep tiers against the plant | minutes; more with `--exhaustive` |
| `sweep_truncation.py` | the tier table: 0 of 79, 0 of 63, 0 of 2,245 clean; the planted counts | seconds; minutes with `--exhaustive` |
| `measure_vintages.py` | **M1** — no curated key carries a second vintage, and the slice is what the store would hand over | seconds |
| `measure_e1_power.py` | **M2** — E1's real detection power, 52 of 2,312, and the exact cutoffs it predicts | seconds |
| `measure_calendar_memo.py` | **M3** — cross-call state survives on the `lru_cache`d calendar, so a package-scoped AST ban cannot close E2's class | instant |
| `measure_calibration.py` | **R73** — mean forecast over mean realized per model, and the smearing correction's isolated effect | a minute or so |

```bash
uv run --locked python tools/measure_vintages.py
uv run --locked python tools/measure_e1_power.py
uv run --locked python tools/measure_calendar_memo.py
uv run --locked python tools/measure_calibration.py --symbol SPY --horizon 1 --horizon 22 --every 5
uv run --locked python tools/sweep_truncation.py --tier synthetic
uv run --locked python tools/sweep_truncation.py --tier real                # sampled, per-commit tier
uv run --locked python tools/sweep_truncation.py --tier real --exhaustive   # the dev-window proof
uv run --locked python tools/reproduce_e1.py --exhaustive
```

`--locked` matters, as everywhere in this project: `--frozen` installs from the lock without
comparing it against `pyproject.toml`, and a bare `uv run` re-locks silently.

## Reading the output

**stdout is the diffable part. stderr is not.** Storage roots, resolved paths, progress and
**every wall-clock figure** go to stderr. That split is deliberate: R38 records a timing that
went stale three times, and a duration measured while three of these probes share a machine is
not a number anyone should quote. Redirect stdout alone and what remains lines up with the
decisions log.

The sweep prints in `tests/test_truncation_equivalence.py`'s own wording — `"{k} of {N} cutoffs
disagree: …"` — because that is the string the log quotes, and because the sweep itself is
imported from that test module rather than reimplemented here. A figure in the log should come
from the code the log cites, not from a second implementation that happens to agree today.

## The real-data tiers need the store

`data/**` is gitignored because it is derivable from the committed vendor payloads, so on a fresh
clone these probes have nothing to read. Rebuild first, with no network — `build-raw` replays the
committed archive:

```bash
uv run --locked python -m volab.ingest build-raw
uv run --locked python -m volab.ingest curate
uv run --locked python -m volab.features build
```

## Why the E1 plant is a patch and not a monkeypatch

Phase 1's AST scan reads `src/volab/features/*.py` **off disk**: `test_point_in_time.py` globs
`Path(volab.__file__).parent / "features"` and calls `read_text()`. Against a monkeypatch it
would scan clean source and pass vacuously — reporting the escape as larger than it is while
proving nothing. The plant has to be in the file.

`sweep_truncation.py --plant e1` does offer the monkeypatch route, because for the *sweep's*
counts it is equivalent and far cheaper. It is the Phase-1-suite half of the claim that needs the
patch.

**The patch is never applied to your checkout.** `reproduce_e1.py` builds a throwaway tree with
`git archive HEAD` — a read of the object store, no worktree bookkeeping, no `.git/index.lock` to
contend with another session over — clones the derived data tiers in, and plants it there. A run
killed between the apply and the revert therefore cannot leave a live forward peek in `src/` for
the next commit to pick up, which is the one failure mode this project is about.

Two anti-vacuity lines print before any measurement: that `volab` resolved **inside** that tree
rather than through an editable install pointing at your real `src/`, and that the source there
really carries the plant. The driver exits non-zero rather than reporting a number if either is
false. The clean baseline runs first in the same tree, so the plant's evidence is a *difference*
from that baseline rather than an absolute count carrying whatever the environment contributes.

## One figure to read carefully

`measure_e1_power.py` prints both rates, because the log's single `hit rate: 0.0082` line was the
**positive band alone** while the sentence beside it described the union of both bands — 52 of
2,312, 0.0225. Both are now printed and labelled. The miss probability for a 40-cutoff sample is
`(1 − 0.0225)^40 ≈ 0.40`, which the log had rounded to "about a third".
