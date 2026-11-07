"""Per-request dependencies, shared by the graph and the approval walk."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient

TURN_DEADLINE_SECS = 360.0


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one. `now` is the
    turn's clock (UTC); `started` / `clock` time the turn against TURN_DEADLINE_SECS."""

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

    def out_of_time(self, reserve: float = 0.0) -> bool:
        return self.elapsed() > TURN_DEADLINE_SECS - reserve
