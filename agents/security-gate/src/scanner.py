"""The scanner wrapper: the PR's changed files are written into a fresh temp directory under sanitised
names, the static analyser installed in this agent's own image runs over them with ONLY the bundled rules
(`rules/`), offline flags, no shell, a time limit and resource limits, and the directory is removed after.
PR code is never executed: it is only read by the analyser.

Never `--config auto` and never the rule registry: both need the network and send data out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import findings_parse
import paths
from records import Finding

log = logging.getLogger("security_gate.scanner")

RULES_DIR = Path(__file__).resolve().parent / "rules"
SCAN_TIMEOUT_SECS = 90.0
PER_FILE_TIMEOUT_SECS = 30
# The analyser's own memory cap (MiB per rule and file). An address-space rlimit is opt-in only
# (SCANNER_ADDRESS_SPACE_MB): the analyser's runtime reserves far more virtual memory than it uses.
MAX_MEMORY_MB = 1024
ADDRESS_SPACE_MB = int(os.environ.get("SCANNER_ADDRESS_SPACE_MB") or 0)
CPU_LIMIT_SECS = 90
# Per-file problems that leave the rest of the scan valid (hunk-only files often parse partially).
_FILE_LEVEL_ERRORS = ("partialparsing", "syntax error", "lexical error", "timeout", "out of memory", "too many matches")
DEFAULT_BIN = "/opt/scanner/bin/semgrep"


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str = ""


Runner = Callable[[list[str], str, Mapping[str, str], float], Awaitable[Completed]]


@dataclass(frozen=True)
class ScanResult:
    status: str                                    # ok | timeout | failed | missing | empty
    findings: tuple[Finding, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()     # (path, reason)
    scanned: tuple[str, ...] = ()
    dropped: int = 0                               # results outside the scanned files or lines


def binary() -> str | None:
    configured = os.environ.get("SEMGREP_BIN") or ""
    for candidate in (configured, DEFAULT_BIN, shutil.which("semgrep") or ""):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def command(bin_path: str, rules_dir: Path = RULES_DIR) -> list[str]:
    return [bin_path, "scan", "--config", str(rules_dir), "--json", "--quiet", "--metrics=off",
            "--disable-version-check", "--no-git-ignore", "--timeout", str(PER_FILE_TIMEOUT_SECS), "-j", "1",
            "--max-memory", str(MAX_MEMORY_MB),
            "--max-target-bytes", "300000", "."]


def scan_env(home: str) -> dict[str, str]:
    """A minimal environment: no credentials, no telemetry, caches under the scan's own temp home."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "XDG_CACHE_HOME": home,
            "XDG_CONFIG_HOME": home, "SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0",
            "SEMGREP_USER_HOME": home, "SEMGREP_SETTINGS_FILE": os.path.join(home, "settings.yml"),
            "SEMGREP_VERSION_CACHE_PATH": os.path.join(home, "version"), "LANG": "C.UTF-8"}


async def scan(files: Mapping[str, str], runner: Runner | None = None, bin_path: str | None = None,
               timeout: float = SCAN_TIMEOUT_SECS) -> ScanResult:
    """Scan `files` (path → content). The temp directory is removed whatever happens."""
    if not files:
        return ScanResult("empty")
    exe = bin_path or binary() or ("semgrep" if runner is not None else None)  # an injected runner resolves it
    if exe is None:
        return ScanResult("missing")
    workdir = tempfile.mkdtemp(prefix="gate-")
    try:
        root, home = Path(workdir) / "src", Path(workdir) / "home"
        root.mkdir()
        home.mkdir()
        written, rejected = _write(root, files)
        if not written:
            return ScanResult("empty", rejected=rejected)
        try:
            done = await (runner or run_process)(command(exe), str(root), scan_env(str(home)), timeout)
        except TimeoutError:
            log.warning("scan timed out after %.0fs", timeout)
            return ScanResult("timeout", rejected=rejected, scanned=tuple(written))
        return _parse(done, written, rejected)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _write(root: Path, files: Mapping[str, str]) -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
    written: dict[str, str] = {}
    rejected: list[tuple[str, str]] = []
    for rel, content in files.items():
        target = paths.place(root, rel)
        if isinstance(target, str):
            rejected.append((rel, target))
            continue
        target.write_text(content, encoding="utf-8")
        written[rel] = content
    return written, tuple(rejected)


def _parse(done: Completed, written: dict[str, str], rejected: tuple[tuple[str, str], ...]) -> ScanResult:
    try:
        data = json.loads(done.stdout or "")
    except ValueError:
        log.warning("scanner exited %s without JSON: %s", done.returncode, (done.stderr or "")[-300:])
        return ScanResult("failed", rejected=rejected, scanned=tuple(written))
    found, dropped = findings_parse.from_scanner(data, written)
    fatal = fatal_errors(data)
    if fatal:
        log.warning("scanner failed (exit %s): %s | %s", done.returncode, "; ".join(fatal)[:600],
                    (done.stderr or "")[-300:])
        return ScanResult("failed", found, rejected, tuple(written), dropped)
    return ScanResult("ok", found, rejected, tuple(written), dropped)


def fatal_errors(data: object) -> list[str]:
    """Errors that mean the scan did not really run (a config, runtime or crash error), as short texts.
    Per-file parse problems are logged and tolerated."""
    errors = data.get("errors") if isinstance(data, dict) else None
    out = []
    for e in errors if isinstance(errors, list) else ():
        if not isinstance(e, dict):
            continue
        kind = json.dumps(e.get("type"))[:120]
        text = f"{e.get('level', '')} {kind} {str(e.get('message') or '')[:200]}"
        if e.get("level") == "error" and not any(k in kind.casefold() for k in _FILE_LEVEL_ERRORS):
            out.append(text)
        else:
            log.info("scanner note: %s", text[:300])
    return out


def _limits() -> None:  # runs in the child before exec
    import resource

    limits = [(resource.RLIMIT_CPU, CPU_LIMIT_SECS)]
    if ADDRESS_SPACE_MB:
        limits.append((resource.RLIMIT_AS, ADDRESS_SPACE_MB * 1024 * 1024))
    for limit, value in limits:
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass


async def run_process(argv: list[str], cwd: str, env: Mapping[str, str], timeout: float) -> Completed:
    """No shell; its own process group, killed whole on timeout."""
    proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=dict(env), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL,
                                                start_new_session=True, preexec_fn=_limits)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        _kill(proc)
        await proc.wait()
        raise
    return Completed(proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
