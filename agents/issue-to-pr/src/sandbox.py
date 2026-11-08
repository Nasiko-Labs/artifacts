"""The hard-limited place where fetched repository tests run (copied from the fix-this-test approach, owned
here): a fixed pytest command, an explicit environment allow-list (no OPENAI_*, MCP_*, NASIKO_* or other
inherited variable), resource limits set by sandbox_launcher.py, a wall-clock limit that kills the whole
process group, and capped output."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

LAUNCHER = Path(__file__).resolve().with_name("sandbox_launcher.py")
WALL_SECS = 60
OUTPUT_BYTES = 200_000
DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"


@dataclass(frozen=True)
class RunOutput:
    output: str
    exit_code: int | None
    timed_out: bool
    seconds: float


def run_pytest(root: Path, work: Path, args: list[str], wall_secs: float = WALL_SECS) -> RunOutput:
    """pytest with `args` in `work`. Blocking; call through asyncio.to_thread."""
    tmp = root / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = {"PATH": os.environ.get("PATH") or DEFAULT_PATH, "HOME": str(tmp), "TMPDIR": str(tmp),
           "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}
    out_path = root / f"out-{uuid.uuid4().hex}.txt"
    started = time.monotonic()
    with out_path.open("wb") as out:
        proc = subprocess.Popen([sys.executable, "-I", str(LAUNCHER), *args], cwd=work, env=env,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
        timed_out = False
        try:
            proc.wait(timeout=wall_secs)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
    output = _read_capped(out_path)
    out_path.unlink(missing_ok=True)
    return RunOutput(output, None if timed_out else proc.returncode, timed_out, time.monotonic() - started)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.wait()


def _read_capped(path: Path) -> str:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size <= OUTPUT_BYTES:
            return handle.read().decode("utf-8", errors="replace")
        head = handle.read(OUTPUT_BYTES // 4)
        handle.seek(size - (OUTPUT_BYTES - len(head)))
        tail = handle.read()
    return head.decode("utf-8", errors="replace") + "\n[... output truncated ...]\n" + \
        tail.decode("utf-8", errors="replace")
