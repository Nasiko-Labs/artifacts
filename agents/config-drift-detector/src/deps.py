"""Per-request dependencies, built by the executor for one turn."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient

DEADLINE_SECS = 330.0  # hard turn deadline; the platform accepts a flow for 600 s
FETCH_SECS = 150.0     # the code-host fetch step's own budget


@dataclass(frozen=True)
class TurnDeps:
    """`traceparent` is this request's inbound header, never a cached one. `now` is the turn clock (UTC);
    `clock` is monotonic seconds and `started` is when the turn began on it."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    now: datetime
    clock: Callable[[], float] = time.monotonic
    started: float = field(default_factory=time.monotonic)

    def remaining(self) -> float:
        return DEADLINE_SECS - (self.clock() - self.started)
