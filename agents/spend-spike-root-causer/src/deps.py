"""Per-request dependencies, shared by the graph, the lookups and the post walk."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from nasiko_mcp import NasikoMcpClient

TURN_DEADLINE_SECS = 300.0
LOOKUP_CUTOFF_SECS = 240.0   # deploy and owner lookups are skipped after this
LLM_CALL_TIMEOUT_SECS = 45.0
TOOL_CALL_TIMEOUT_SECS = 30.0


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    today: date
    clock: Callable[[], float] = time.monotonic
    started: float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return self.clock() - self.started

    def out_of_time(self) -> bool:
        return self.elapsed() >= LOOKUP_CUTOFF_SECS
