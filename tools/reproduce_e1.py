"""Reproduce the headline: E1 planted in ``src/`` passes Phase 1's method and is caught by this one.

This is the driver behind "Proof that it bites" in ``docs/superpowers/phase2-decisions.md``. It
plants ``tools/e1_plant.patch``, runs **Phase 1's** point-in-time suite and the whole suite minus
Phase 2's own files against the plant, then runs **Phase 2's** truncation-equivalence sweep
against the same plant, and prints both so the pair can be diffed against the log.

**Why a patch and not a monkeypatch.** Phase 1's AST scan reads ``src/volab/features/*.py`` off
disk -- ``test_point_in_time.py`` globs ``Path(volab.__file__).parent / "features"`` and calls
``read_text()``. Against a monkeypatch it would scan clean source and pass vacuously, which would
report the escape as larger than it is while proving nothing. The plant has to be in the file.
``test_e1_is_detectable_at_exactly_the_cutoffs_where_its_band_is_reached`` says the same thing
from the other side: planting a peek into the shipped source is not something a standing test can
do to itself.

**Where it plants it, and why not here.** Never in your checkout. A run that dies between the
apply and the revert would leave a live forward peek in ``src/`` for the next commit to pick up,
which is the one failure mode this whole project is about. So the tree is built with ``git
archive HEAD`` into a temporary directory -- a read of the object store, no worktree
bookkeeping, no ``.git/index.lock`` to contend with another session over, and nothing to clean up
in the repository if this process is killed. The derived data tiers are cloned in, because
``data/**`` is gitignored and the real-data tiers would otherwise skip.

**Anti-vacuity, printed every run.** Two things an operator cannot check by looking: that
``volab`` resolved inside the temporary tree rather than through your editable install, and that
the source there really carries the plant. Both are printed before any measurement, and the
baseline pass runs *first* in the same tree, so the plant's evidence is a difference from that
baseline rather than an absolute number carrying whatever the environment contributes.

    uv run --locked python tools/reproduce_e1.py
    uv run --locked python tools/reproduce_e1.py --exhaustive   # adds the 2,245-cutoff tier
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from _harness import REPO, heading, note

# The suite Phase 1 relied on, by file. The claim is specifically about *this* file passing.
PHASE1_SUITE = "tests/test_point_in_time.py"

# Phase 2's own test files, excluded for the "whole suite" figure. Named as a constant and
# printed, because "the whole suite minus the new Phase 2 files" is not self-explaining and the
# count depends entirely on which files that means.
PHASE2_FILES: tuple[str, ...] = (
    "tests/test_truncation_equivalence.py",
    "tests/test_process_isolation.py",
    "tests/test_walkforward.py",
    "tests/test_walkforward_cli.py",
)

# Derived tiers to clone into the temporary tree. ``data/vendor_payloads`` is tracked, so
# ``git archive`` already carries it; these are gitignored because they are rebuildable from it.
# ``raw`` is in the list although no sweep reads it: without it one test skipped, and a whole-suite
# count carrying a silent skip is not comparable with one that does not.
DERIVED_TIERS: tuple[str, ...] = ("raw", "curated", "features")

# pytest's wall clock, stripped so two runs of the same tree diff cleanly. Timings from a machine
# running three of these probes at once are not a measurement anyone should quote -- R38.
_TIMING = re.compile(r" in \d+\.\d+s")


def _run(
    command: Sequence[str], *, cwd: Path, check: bool = True
) -> subprocess.CompletedProcess[str]:
    note(f"$ {' '.join(command)}")
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if check and completed.returncode != 0:
        note(completed.stdout[-4000:])
        note(completed.stderr[-4000:])
        raise SystemExit(f"{command[0]} failed with {completed.returncode}")
    return completed


def _pytest(tree: Path, *arguments: str) -> str:
    """Run pytest in ``tree`` and return its summary line with the timing stripped."""
    completed = subprocess.run(
        # ``-rfEs``, and the ``f`` is not redundant: ``-r`` *replaces* pytest's default summary
        # selection rather than adding to it, so a bare ``-rs`` printed the skip and silently
        # dropped the ``FAILED`` line -- which is the one line naming the single catcher, and the
        # whole point of the planted run. The ``s`` is there because one test skips here by
        # construction: a tree from ``git archive`` has no ``.git``, so ``test_store_guard``'s
        # git-work-tree guard declines to run, and a count carrying an unexplained skip is not a
        # count anyone can compare against a checkout's.
        ["uv", "run", "--locked", "python", "-m", "pytest", "-q", "-rfEs", *arguments],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        note(completed.stderr[-4000:])
        raise SystemExit("pytest produced no output")
    # The failure lines matter as much as the count: the log's claim is that the single catcher is
    # one incidental unit value test, and that is only checkable if the names are shown.
    for line in lines:
        if line.startswith(("FAILED", "ERROR", "SKIPPED")):
            print(line.split(" - ")[0] if line.startswith(("FAILED", "ERROR")) else line)
    return _TIMING.sub("", lines[-1])


def _sweep(tree: Path, *arguments: str) -> None:
    """One sweep tier in the planted tree. A tier that fails to run fails the whole driver.

    ``check=True`` deliberately. The first version let a tier raise ``DatasetNotFound`` and
    reported success with that row simply missing from the output -- a check that passes by not
    running, which is this project's named anti-goal.
    """
    completed = _run(
        ["uv", "run", "--locked", "python", "tools/sweep_truncation.py", *arguments],
        cwd=tree,
    )
    sys.stdout.write(completed.stdout)
    note(completed.stderr)


def _build_tree(destination: Path) -> None:
    """``git archive HEAD`` into ``destination``, plus the working copy of ``tools/``."""
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "archive", "HEAD"], cwd=REPO, capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(destination)], input=archive.stdout, check=True)
    # The working copy, not HEAD's: on the commit that introduces ``tools/`` these scripts are
    # not in HEAD yet, and after it the thing a reader is testing is what is in front of them.
    shutil.copytree(REPO / "tools", destination / "tools", dirs_exist_ok=True)


def _clone_data(destination: Path) -> bool:
    """Clone the derived tiers in, so the real-data tiers do not skip. Whether they will run.

    ``cp -c`` is an APFS clone: no bytes copied, no disk used until something writes. Falls back
    to an ordinary recursive copy where the filesystem has no clone, which is slower and correct.
    A symlink would be wrong in a way worth stating: anything in the planted run that wrote to
    the storage root would write leaky features back into the real store.

    **The destination is removed first, and that is load-bearing.** ``.gitignore`` keeps
    ``data/**/`` and ``data/**/.gitkeep`` tracked, so ``git archive`` has already created an empty
    ``data/curated``; ``cp -R src dst`` with an existing ``dst`` copies *into* it, giving
    ``data/curated/curated`` and a store the reads cannot find. That is exactly what the first
    run of this driver did, and it skipped the real tiers while exiting 0 -- which is why the
    caller now refuses to report rather than carrying on without them.
    """
    (destination / "data").mkdir(parents=True, exist_ok=True)
    cloned = True
    for tier in DERIVED_TIERS:
        source = REPO / "data" / tier
        target = destination / "data" / tier
        if not source.is_dir() or not any(source.iterdir()):
            note(f"data/{tier} is absent from this checkout; the real-data tiers cannot run")
            cloned = False
            continue
        if target.exists():
            shutil.rmtree(target)
        if subprocess.run(["cp", "-Rc", str(source), str(target)], check=False).returncode != 0:
            shutil.copytree(source, target)
    return cloned


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tree",
        type=Path,
        default=None,
        help="where to build the planted tree; defaults to a temporary directory",
    )
    parser.add_argument("--keep", action="store_true", help="leave the planted tree in place")
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="also sweep every real cutoff against the plant (adds minutes)",
    )
    parser.add_argument(
        "--skip-baseline",
        action="store_true",
        help="skip the clean run in the same tree; the plant's figures then stand alone",
    )
    arguments = parser.parse_args(argv)

    patch = REPO / "tools" / "e1_plant.patch"
    if not patch.is_file():
        raise SystemExit(f"{patch} is missing")
    sha = subprocess.run(
        ["git", "--no-optional-locks", "rev-parse", "--short", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )

    tree = arguments.tree or Path(tempfile.mkdtemp(prefix="volab-e1-"))
    started = time.perf_counter()
    try:
        heading("THE TREE THIS WAS MEASURED ON")
        # The archive is HEAD's content, so a dirty working tree is reported rather than silently
        # excluded: the figures below are HEAD's, whatever is uncommitted beside them.
        print(f"git {sha}{' (working tree dirty; the tree below is HEAD)' if dirty else ', clean'}")
        print(f"phase 1 suite: {PHASE1_SUITE}")
        print(f"phase 2 files excluded from the whole-suite figure: {len(PHASE2_FILES)}")
        for name in PHASE2_FILES:
            print(f"  {name}")
        note(f"planted tree at {tree}")
        _build_tree(tree)
        cloned = _clone_data(tree)
        print(f"real-data tiers available: {cloned}")
        if not cloned:
            raise SystemExit(
                "the derived data tiers are absent, so the real sweep tiers and the real-data "
                "tests would skip and the figures would be incomparable. Rebuild first: "
                "python -m volab.ingest build-raw && ... curate && python -m volab.features build"
            )
        ignores = [f"--ignore={name}" for name in PHASE2_FILES]

        if not arguments.skip_baseline:
            heading("BASELINE, SAME TREE, NO PLANT")
            print(f"phase 1 point-in-time suite: {_pytest(tree, PHASE1_SUITE)}")
            print(f"whole suite minus the phase 2 files: {_pytest(tree, *ignores)}")

        _run(["patch", "-p1", "--batch", "-i", str(patch)], cwd=tree)

        heading("THE PLANT IS LIVE IN THE SOURCE BEING TESTED")
        proof = _run(
            [
                "uv",
                "run",
                "--locked",
                "python",
                "-c",
                "import inspect;import volab;from volab.features import realized;"
                "print(f'volab from {volab.__file__}');"
                "print(f'E1 PLANT in realized.py: "
                '{"E1 PLANT" in inspect.getsource(realized)}\')',
            ],
            cwd=tree,
        )
        sys.stdout.write(proof.stdout)
        if "E1 PLANT in realized.py: True" not in proof.stdout:
            raise SystemExit("the plant is not live in the tree under test; refusing to report")
        if str(tree) not in proof.stdout:
            raise SystemExit(f"volab did not resolve inside {tree}; refusing to report")

        heading("PHASE 1'S METHOD, E1 PLANTED")
        print(f"phase 1 point-in-time suite: {_pytest(tree, PHASE1_SUITE)}")
        print(f"whole suite minus the phase 2 files: {_pytest(tree, *ignores)}")

        heading("PHASE 2'S METHOD, THE SAME PLANT")
        _sweep(tree, "--tier", "synthetic")
        _sweep(tree, "--tier", "real")
        if arguments.exhaustive:
            _sweep(tree, "--tier", "real", "--exhaustive")
        else:
            print("real exhaustive tier: not run; pass --exhaustive (about 6 minutes)")
    finally:
        note(f"ran in {time.perf_counter() - started:.1f}s")
        if arguments.keep:
            note(f"planted tree kept at {tree}")
        else:
            shutil.rmtree(tree, ignore_errors=True)
            note(f"planted tree removed from {tree}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    raise SystemExit(main())
