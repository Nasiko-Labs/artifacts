"""Per-request dependencies, built by the executor for one turn."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient

DEADLINE_SECS = 300.0  # the platform accepts a flow for 600 s; a step starting after this is skipped


@dataclass(frozen=True)
class TurnDeps:
    """`traceparent` is this request's inbound header, never a cached one. `now` is the aware UTC
    instant the turn started (fixed in tests). `clock` is monotonic seconds; `started` is when the
    turn began on that clock."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    now: datetime
    clock: Callable[[], float] = time.monotonic
    started: float = field(default_factory=time.monotonic)

    def past_deadline(self) -> bool:
        return self.clock() - self.started >= DEADLINE_SECS
