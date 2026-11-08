"""Per-request dependencies, shared by the graph, the read steps and the post walk."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one. `now` is the
    turn's clock (UTC); `started` / `clock` time the turn."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    now: datetime
    started: float | None = None
    clock: Callable[[], float] = time.monotonic

    def elapsed(self) -> float:
        return 0.0 if self.started is None else self.clock() - self.started
