"""Per-request dependencies, shared by the graph steps and the write walk."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from config import Config
from nasiko_mcp import NasikoMcpClient


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one.
    `clock` is monotonic seconds and `started` its value when the turn began (the deadline's origin)."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    today: date
    config: Config = field(default_factory=Config)
    clock: Callable[[], float] = time.monotonic
    started: float = 0.0

    def elapsed(self) -> float:
        return self.clock() - self.started
