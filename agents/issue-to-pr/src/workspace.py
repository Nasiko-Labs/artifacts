"""The private run directory under /workspace/runs: originals written in (paths checked so nothing escapes),
the engine's edits read back (symlinks, binaries and oversize files reported, never followed), and the
directory removed at the end of every turn. One directory per conversation so a revision reuses the same
engine session on the same path; stale directories are swept at startup."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import paths as p

DEFAULT_BASE = Path("/workspace/runs")
SKIP_NAMES = frozenset({"AGENTS.md"})
SKIP_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", "node_modules", ".opencode"})


@dataclass(frozen=True)
class Collected:
    files: dict[str, str]
    symlinks: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


class Workspace:
    def __init__(self, base: Path = DEFAULT_BASE):
        self.base = Path(base)

    def available(self) -> bool:
        try:
            self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
            probe = self.base / f".probe-{uuid.uuid4().hex}"
            probe.write_text("ok")
            probe.unlink()
            return True
        except OSError:
            return False

    def sweep(self) -> int:
        if not self.base.is_dir():
            return 0
        stale = [d for d in self.base.iterdir() if d.is_dir()]
        for d in stale:
            shutil.rmtree(d, ignore_errors=True)
        return len(stale)

    def root_for(self, context_id: str) -> Path:
        return self.base / hashlib.sha256(context_id.encode()).hexdigest()[:24]

    def prepare(self, context_id: str) -> Path:
        root = self.root_for(context_id)
        shutil.rmtree(root, ignore_errors=True)
        (root / "tmp").mkdir(parents=True, mode=0o700)
        return root

    @staticmethod
    def materialize(root: Path, name: str, files: Mapping[str, str], extra: Mapping[str, str] | None = None) -> Path:
        work = root / name
        work.mkdir(mode=0o700, parents=True, exist_ok=True)
        for rel, text in {**files, **(extra or {})}.items():
            clean = p.normalise(rel)
            target = (work / (clean or "")).resolve()
            if clean is None or work.resolve() not in target.parents:
                raise ValueError(f"path escapes the run directory: {rel}")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))
        return work

    @staticmethod
    def collect(work: Path) -> Collected:
        files: dict[str, str] = {}
        links, skipped = [], []
        for dirpath, dirnames, filenames in os.walk(work, followlinks=False):
            here = Path(dirpath)
            for d in list(dirnames):
                if d in SKIP_DIRS or (here / d).is_symlink():
                    if (here / d).is_symlink():
                        links.append(str((here / d).relative_to(work)))
                    dirnames.remove(d)
            for name in filenames:
                path = here / name
                rel = str(path.relative_to(work)).replace(os.sep, "/")
                if rel in SKIP_NAMES or name.endswith(".pyc"):
                    continue
                if path.is_symlink():
                    links.append(rel)
                    continue
                data = path.read_bytes()
                if len(data) > p.MAX_FILE_BYTES or b"\x00" in data:
                    skipped.append(rel)
                    continue
                files[rel] = data.decode("utf-8", errors="replace")
        return Collected(files, tuple(sorted(links)), tuple(sorted(skipped)))

    @staticmethod
    def remove(root: Path | None) -> None:
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)
