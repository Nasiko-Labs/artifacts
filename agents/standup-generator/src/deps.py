"""Per-request dependencies, built by the executor for one turn."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient

FETCH_DEADLINE_SECS = 180.0  # a read step starting after this is skipped and reported as coverage
HARD_DEADLINE_SECS = 330.0  # after this no further LLM call starts (the gist is left out)


@dataclass(frozen=True)
class TurnDeps:
    """`traceparent` is this request's inbound header, never a cached one. `now` is the turn clock
    (aware UTC). `clock` is monotonic seconds; `started` is when the turn began on that clock."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    now: datetime
    clock: Callable[[], float] = time.monotonic
    started: float = field(default_factory=time.monotonic)

    def past(self, limit_secs: float) -> bool:
        return self.clock() - self.started >= limit_secs
