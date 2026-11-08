"""The hard-limited place where pasted code runs: a private run directory under /workspace/runs, a
fixed pytest command, an explicit environment allow-list (no OPENAI_*, MCP_*, NASIKO_* or any inherited
variable), resource limits set by sandbox_launcher.py, a wall-clock timeout that kills the whole process
group, and output capped. One run at a time per container."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("fix-this-test.sandbox")

# The persistent /workspace may hold a runs/ directory made by an earlier deploy running under another uid
# (unreadable to this one), so a per-uid directory and then the container's /tmp are tried before static mode.
DEFAULT_BASES = (Path("/workspace/runs"), Path(f"/workspace/runs-{os.getuid()}"),
                 Path(tempfile.gettempdir()) / "fix-this-test-runs")
LAUNCHER = Path(__file__).resolve().with_name("sandbox_launcher.py")
WALL_SECS = 30
OUTPUT_BYTES = 200_000
DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"


@dataclass(frozen=True)
class RunOutput:
    output: str
    exit_code: int | None
    timed_out: bool
    seconds: float


class Sandbox:
    def __init__(self, base: Path | tuple[Path, ...] = DEFAULT_BASES, wall_secs: float = WALL_SECS):
        self.candidates = tuple(Path(b) for b in base) if isinstance(base, tuple) else (Path(base),)
        self.base = self.candidates[0]
        self.wall_secs = wall_secs
        self.lock = asyncio.Lock()

    def available(self) -> bool:
        """True when a run directory can be created and written in one of the candidate bases (the first
        that works becomes `base`)."""
        for candidate in self.candidates:
            if _writable(candidate):
                self.base = candidate
                return True
        return False

    def sweep(self) -> int:
        """Remove run directories left by a crash; called once at startup. Never raises."""
        removed = 0
        for candidate in self.candidates:
            try:
                stale = [p for p in candidate.iterdir() if p.is_dir()] if candidate.is_dir() else []
            except OSError as e:
                log.warning("cannot sweep %s: %s", candidate, e)
                continue
            for path in stale:
                shutil.rmtree(path, ignore_errors=True)
            removed += len(stale)
        return removed

    def new_root(self) -> Path:
        root = self.base / uuid.uuid4().hex
        root.mkdir(mode=0o700, parents=True)
        (root / "tmp").mkdir(mode=0o700)
        return root

    @staticmethod
    def materialize(root: Path, name: str, files: dict[str, str]) -> Path:
        work = root / name
        work.mkdir(mode=0o700)
        for rel, text in files.items():
            target = (work / rel).resolve()
            if work.resolve() not in target.parents:
                raise ValueError(f"path escapes the run directory: {rel}")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_text(text)
            target.chmod(0o600)
        return work

    @staticmethod
    def remove(root: Path | None) -> None:
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)

    def run(self, root: Path, work: Path, args: list[str]) -> RunOutput:
        """pytest with `args` in `work`. Blocking; call through asyncio.to_thread."""
        env = {"PATH": os.environ.get("PATH") or DEFAULT_PATH, "HOME": str(root / "tmp"),
               "TMPDIR": str(root / "tmp"), "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}
        out_path = root / f"out-{uuid.uuid4().hex}.txt"
        started = time.monotonic()
        with out_path.open("wb") as out:
            proc = subprocess.Popen([sys.executable, "-I", str(LAUNCHER), *args], cwd=work, env=env,
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            timed_out = False
            try:
                proc.wait(timeout=self.wall_secs)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_group(proc)
        output = _read_capped(out_path)
        out_path.unlink(missing_ok=True)
        return RunOutput(output, None if timed_out else proc.returncode, timed_out, time.monotonic() - started)


def _writable(base: Path) -> bool:
    try:
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        probe = base / f".probe-{uuid.uuid4().hex}"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.wait()


def _read_capped(path: Path) -> str:
    """At most OUTPUT_BYTES: the head and the tail (pytest's summary is at the end)."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size <= OUTPUT_BYTES:
            return handle.read().decode("utf-8", errors="replace")
        head = handle.read(OUTPUT_BYTES // 4)
        handle.seek(size - (OUTPUT_BYTES - len(head)))
        tail = handle.read()
    return (head.decode("utf-8", errors="replace") + "\n[... output truncated ...]\n"
            + tail.decode("utf-8", errors="replace"))
