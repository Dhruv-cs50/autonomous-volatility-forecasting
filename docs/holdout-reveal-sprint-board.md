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
| HR-1 | `holdout_end` in `Splits`, with the override direction **reversed** | `src/volab/config.py`, its YAML, `tests/` | `VOLAB_SPLITS_HOLDOUT_END` may move the boundary **later only**; an earlier value is refused. The mirror of `dev_end`, which may move **earlier only** | in progress |
| HR-2 | Architect: the seal's lifecycle in `store.py` | `docs/superpowers/specs/` | a design for: live unreachable before the reveal, the seal lifting on a **completed reveal record asked of git** rather than a flag, and log-before-read ordering | in progress |
| HR-3 | The seal lifecycle, implemented | `src/volab/store.py`, `tests/` | three regions enforced; the record written **and flushed before** the read; a second append-only line marks completion; no other route lifts the seal | todo, after HR-1 and HR-2 |
| HR-4 | The reveal itself: fingerprint, the multi-read span, scoring | new module + CLI, `tests/` | refuses on a dirty tree or a missing pre-registration; records the pre-registration's content hash, the champion's config, git sha, dirty flag and a content hash; spans several reads because derived tiers stop at `dev_end`; retries are **counted, not forbidden** | todo, after HR-3 |
| HR-5 | The dress rehearsal | `tools/` and/or `tests/` | the **whole** procedure runs against a pseudo-holdout carved inside dev, on a **separate storage root**, leaving no mark on the real audit log | todo, after HR-4 |
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
