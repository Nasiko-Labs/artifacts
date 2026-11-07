"""Per-request dependencies, shared by the graph and the write walk. Tool discovery is in discovery.py."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from nasiko_mcp import NasikoMcpClient
from quarter import DEFAULT_FISCAL_START_MONTH


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one. `clock` and
    `started` time the turn against the platform's flow window."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    today: date
    fiscal_start_month: int = DEFAULT_FISCAL_START_MONTH
    clock: Callable[[], float] = time.monotonic
    started: float | None = None

    def elapsed(self) -> float:
        return 0.0 if self.started is None else self.clock() - self.started
