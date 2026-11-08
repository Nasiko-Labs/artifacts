"""Per-request dependencies. `traceparent` is this request's inbound header, never a cached one."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nasiko_mcp import NasikoMcpClient

TURN_DEADLINE_SECS = 300.0


@dataclass(frozen=True)
class TurnDeps:
    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    converter: Any = None  # convert.Converter; None means the default one
    fetcher: Any = None    # netguard.SafeFetcher; None means the default one
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)

    def remaining(self) -> float:
        return self.deadline - self.clock()
