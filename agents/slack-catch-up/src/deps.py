"""Per-request dependencies, built by the executor for one turn."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient

DEADLINE_SECS = 360.0  # the platform accepts a flow for 600 s; nothing new starts after this
EXTRACT_DEADLINE_SECS = 300.0  # a channel whose extraction has not started by now is "not summarized (time)"


@dataclass(frozen=True)
class TurnDeps:
    """`traceparent` is this request's inbound header, never a cached one. `now` is the wall clock (UTC,
    aware) when the turn began; `clock` is monotonic seconds and `started` the turn start on it."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    now: datetime
    clock: Callable[[], float] = time.monotonic
    started: float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return self.clock() - self.started

    def past_deadline(self) -> bool:
        return self.elapsed() >= DEADLINE_SECS

    def past_extract_deadline(self) -> bool:
        return self.elapsed() >= EXTRACT_DEADLINE_SECS
