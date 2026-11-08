"""Per-request dependencies, shared by the graph, the channel pick and the post walk."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient

TURN_DEADLINE_SECS = 360
EVENTS_CUTOFF_SECS = 270      # the events step is skipped after this
LLM_CALL_TIMEOUT_SECS = 45.0
TOOL_CALL_TIMEOUT_SECS = 30


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one. `now` is the turn's
    clock (UTC); `started` / `clock` time the turn for the events-step time guard."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    now: datetime
    started: float | None = None
    clock: Callable[[], float] = time.monotonic

    @property
    def today(self) -> date:
        return self.now.date()

    def elapsed(self) -> float:
        return 0.0 if self.started is None else self.clock() - self.started
