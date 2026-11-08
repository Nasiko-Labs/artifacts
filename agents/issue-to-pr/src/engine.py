"""The coding engine: OpenCode's local HTTP server (`opencode serve` on 127.0.0.1:4096), driven from Python.

Copied approach from the opencode-agent (custom chat-completions provider, warmed cache, plugins off,
HOME=/workspace, an in-container LLM proxy that re-attaches the flow's traceparent), with two changes:
the processes are started on first use (so an idle agent holds no Node memory, and a dead server can be
restarted once), and the traceparent is set per run on the proxy instead of "last request wins".

One run at a time per container (the lock); a second turn gets the busy reply. OpenCode gets no
connector and no write tool: it only edits files in the run directory.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("issue_to_pr.engine")

OPENCODE_URL = "http://127.0.0.1:4096"
PROXY_URL = "http://127.0.0.1:8001"
PROVIDER_ID = "openai-chat"
RUN_SECS = 240.0
START_SECS = 120.0
HEALTH_PATH = "/global/health"
LOG_TAIL_CHARS = 2_000
_DONE = re.compile(r"^\s*DONE:\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class EngineRun:
    session_id: str | None
    done_line: str = ""
    completed: bool = False
    timed_out: bool = False
    error: str | None = None       # unavailable | out_of_memory | failed


class Processes:
    """Starts and restarts the proxy and `opencode serve`. Replaced by a fake in tests."""

    def __init__(self, app_dir: Path = Path("/app"), env: Mapping[str, str] | None = None):
        self._app_dir = app_dir
        self._env = dict(env if env is not None else os.environ)
        self._procs: list[subprocess.Popen] = []

    def _child_env(self) -> dict[str, str]:
        env = {k: v for k, v in self._env.items() if not k.startswith(("MCP_", "NASIKO_")) and k not in ("PORT",)}
        router = self._env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        env.update({"LLM_ROUTER_BASE_URL": router, "OPENAI_BASE_URL": f"{PROXY_URL}/llm", "HOSTNAME": "127.0.0.1",
                    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "NODE_OPTIONS": "--max-old-space-size=256",
                    "PROXY_PORT": "8001"})
        return env

    def start(self) -> None:
        self.stop()
        env = self._child_env()
        self._procs = [
            subprocess.Popen(["node", str(self._app_dir / "opencode_proxy.js")], env=env),
            subprocess.Popen(["opencode", "serve", "--hostname", "127.0.0.1", "--port", "4096"], env=env),
        ]

    def stop(self) -> None:
        for proc in self._procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self._procs = []

    @property
    def started(self) -> bool:
        return bool(self._procs) and all(p.poll() is None for p in self._procs)

    def log_tail(self) -> str:
        path = Path(self._env.get("HOME", "/workspace")) / ".local/share/opencode/log/opencode.log"
        try:
            return path.read_text(errors="replace")[-LOG_TAIL_CHARS:]
        except OSError:
            return ""


class OpenCodeEngine:
    def __init__(self, processes: Any = None, http: httpx.AsyncClient | None = None, model: str = "gpt-4o-mini",
                 run_secs: float = RUN_SECS, start_secs: float = START_SECS,
                 sleep: Callable[[float], Any] = asyncio.sleep):
        self._procs = processes if processes is not None else Processes()
        self._http = http or httpx.AsyncClient()
        self._model = model
        self.run_secs = run_secs
        self._start_secs = start_secs
        self._sleep = sleep
        self.lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self.lock.locked()

    async def healthy(self) -> bool:
        try:
            resp = await self._http.get(OPENCODE_URL + HEALTH_PATH, timeout=5.0)
            return resp.status_code < 500
        except httpx.HTTPError:
            return False

    async def _ensure(self, budget: float) -> bool:
        if self._procs.started and await self.healthy():
            return True
        await asyncio.to_thread(self._procs.start)
        waited = 0.0
        while waited < min(budget, self._start_secs):
            if await self.healthy():
                return True
            await self._sleep(1.0)
            waited += 1.0
        return False

    async def run(self, *, session_id: str | None, directory: Path, prompt: str, traceparent: str,
                  budget: float) -> EngineRun:
        """One engine run, under the caller's lock. `budget` = seconds left in the turn."""
        if not await self._ensure(budget):
            return EngineRun(session_id, error="unavailable")
        params = {"directory": str(directory)}
        headers = {"x-opencode-directory": str(directory)}
        try:
            await self._http.post(f"{PROXY_URL}/_traceparent", json={"traceparent": traceparent}, timeout=5.0)
            if session_id is None:
                created = await self._http.post(f"{OPENCODE_URL}/session", params=params, headers=headers,
                                                json={}, timeout=30.0)
                created.raise_for_status()
                session_id = str(created.json()["id"])
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("engine session setup failed: %s", type(e).__name__)
            return await self._failed(session_id)
        timeout = max(5.0, min(self.run_secs, budget))
        body = {"parts": [{"type": "text", "text": prompt}],
                "model": {"providerID": PROVIDER_ID, "modelID": self._model}}
        try:
            resp = await self._http.post(f"{OPENCODE_URL}/session/{session_id}/message", params=params,
                                         headers=headers, json=body, timeout=timeout)
            resp.raise_for_status()
        except httpx.TimeoutException:
            await self._abort(session_id, params)
            return EngineRun(session_id, timed_out=True)
        except httpx.HTTPError as e:
            log.warning("engine run failed: %s", type(e).__name__)
            return await self._failed(session_id)
        return EngineRun(session_id, done_line=done_line(resp), completed=True)

    async def _abort(self, session_id: str, params: dict) -> None:
        try:
            await self._http.post(f"{OPENCODE_URL}/session/{session_id}/abort", params=params, timeout=10.0)
        except httpx.HTTPError:
            log.warning("engine abort failed")

    async def _failed(self, session_id: str | None) -> EngineRun:
        if await self.healthy():
            return EngineRun(session_id, error="failed")
        log.warning("opencode serve is not answering; one restart. log tail: %s", self._procs.log_tail()[-500:])
        await asyncio.to_thread(self._procs.start)
        return EngineRun(None, error="out_of_memory")


def done_line(resp: httpx.Response) -> str:
    """The engine's own `DONE: …` line (text only; shown as the engine's words, never trusted)."""
    try:
        data = resp.json()
    except ValueError:
        return ""
    parts = data.get("parts") if isinstance(data, dict) else None
    texts = [str(p.get("text") or "") for p in parts or [] if isinstance(p, dict) and p.get("type") == "text"]
    found = _DONE.findall("\n".join(texts))
    return found[-1].strip()[:300] if found else ""
