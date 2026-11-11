"""A throwaway git repository around a carved store, so ``take`` can be run end to end.

The repository root is ``tmp_path``; the store is ``tmp_path/data`` and the ledgers land beside it
exactly where the real ones do. Nothing here touches the real ``data/``: the constants the gates
compare against are replaced for the duration so that the throwaway's own preregistration is "the
registered one", and the key is simulated by a stub that **forwards its argument**.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import carved
import pytest

import volab.reveal as reveal
from volab import store
from volab.config import Settings
from volab.models.base import Cohort
from volab.walkforward import BASELINE_COHORT

TODAY = dt.date(2024, 4, 15)
CLEAN_UNLOCK_ENV = "VOLAB_HOLDOUT_UNLOCK"


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        [
            "git",
            "-c",
            "user.email=reveal@example.invalid",
            "-c",
            "user.name=reveal",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


@dataclass
class World:
    root: Path
    settings: Settings

    def commit(self, message: str = "commit") -> None:
        git(self.root, "add", "-f", ".")
        git(self.root, "commit", "-q", "-m", message)

    def audit(self) -> Path:
        return self.settings.storage_root / store.AUDIT_FILENAME


def build_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    key: bool = True,
    light: bool = True,
    with_store: bool = True,
) -> World:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    # A pre-read refusal never touches the store, so those rehearsals skip building one.
    settings = carved.carved_store(tmp_path) if with_store else carved.settings_at(tmp_path)
    document = tmp_path.joinpath(reveal.PREREGISTRATION)
    document.parent.mkdir(parents=True)
    document.write_text("the registered words\n", encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", reveal.PREREGISTRATION.as_posix())
    git(tmp_path, "commit", "-q", "-m", "register")
    monkeypatch.setattr(
        reveal, "REGISTERED_SHA256", hashlib.sha256(document.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(
        reveal,
        "REGISTERED_BLOB",
        git(tmp_path, "rev-parse", "HEAD:docs/holdout-preregistration.md"),
    )
    if light:
        # The real cohorts are seven GARCH-bearing contestants at stride 1; a rehearsal on ten
        # sessions needs the machinery, not the cost. The registered runs are asserted separately.
        monkeypatch.setattr(reveal, "RUNS", ((BASELINE_COHORT, (1,)),))
        monkeypatch.setattr(reveal, "MINIMUM_TRAINING", 50)
    if key:
        monkeypatch.setattr(store, "_unlock_granted", lambda allow_holdout: allow_holdout)
    assert os.environ.get(CLEAN_UNLOCK_ENV) is None
    return World(tmp_path, settings)


__all__ = ["TODAY", "World", "build_world", "git", "Cohort"]
