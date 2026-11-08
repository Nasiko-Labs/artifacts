"""Run the converter worker in a resource-limited subprocess, one file at a time per slot.

A crash, a timeout or garbage output fails that file only; the agent keeps serving. Plain text and
Markdown are returned as they are (normalised) without a subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("doc_converter.convert")

WORKER = Path(__file__).resolve().parent / "converter_worker.py"
WALL_TIMEOUT_SECS = 90.0
MAX_CONCURRENT = 3
MAX_WORKER_OUTPUT_BYTES = 64 * 1024 * 1024
_WORKER_ENV = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "2"}
_PASSTHROUGH_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "DOC_CONVERTER_WORKER_MEMORY_MB")


@dataclass(frozen=True)
class Converted:
    ok: bool
    markdown: str = ""
    units: dict = field(default_factory=dict)
    sheets: tuple[str, ...] = ()
    reason: str = ""


class Converter:
    def __init__(self, argv: Sequence[str] | None = None, wall_timeout: float = WALL_TIMEOUT_SECS,
                 concurrency: int = MAX_CONCURRENT):
        self._argv = tuple(argv) if argv is not None else (sys.executable, "-I", str(WORKER))
        self._timeout = wall_timeout
        self._slots = asyncio.Semaphore(concurrency)

    async def convert(self, data: bytes, fmt: str, options: dict | None = None) -> Converted:
        if fmt in ("txt", "md"):
            return Converted(True, data.decode("utf-8", errors="replace"))
        async with self._slots:
            with tempfile.TemporaryDirectory(prefix="docconv-") as tmp:
                path = Path(tmp) / f"input.{fmt}"
                path.write_bytes(data)
                return await self._run(str(path), fmt, options or {})

    async def _run(self, path: str, fmt: str, options: dict) -> Converted:
        env = {k: os.environ[k] for k in _PASSTHROUGH_ENV if k in os.environ} | _WORKER_ENV
        proc = await asyncio.create_subprocess_exec(
            *self._argv, path, fmt, json.dumps(options), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            limit=MAX_WORKER_OUTPUT_BYTES)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            log.warning("converter worker killed after %.0fs (%s)", self._timeout, fmt)
            return Converted(False, reason=f"conversion took longer than {self._timeout:.0f} seconds and was stopped")
        return _result(proc.returncode, out, err, fmt)


def _result(code: int | None, out: bytes, err: bytes, fmt: str) -> Converted:
    try:
        body = json.loads(out.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        body = None
    if code != 0 or not isinstance(body, dict):
        log.warning("converter worker failed for %s (exit %s): %s", fmt, code, err[-500:].decode("utf-8", "replace"))
        return Converted(False, reason="the converter stopped on this file (it may be damaged or too large)")
    if not body.get("ok"):
        if body.get("error"):
            log.info("converter refused %s: %s", fmt, body.get("error"))
        return Converted(False, reason=str(body.get("reason") or "the converter could not read this file"))
    units = {k: int(v) for k, v in (body.get("units") or {}).items() if isinstance(v, int)}
    return Converted(True, str(body.get("markdown") or ""), units, tuple(str(s) for s in body.get("sheets") or ()))
