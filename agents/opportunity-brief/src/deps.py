"""Per-request dependencies and the turn's hard budgets (LLM calls, Hacker News requests, gateway calls,
wall-clock deadline). Built per request: `traceparent` is this request's inbound header, never a cached one."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient

TURN_DEADLINE_SECS = 360.0
MAX_LLM_CALLS = 4        # intake scope gate, cluster, write, one repair
MAX_HN_REQUESTS = 2
MAX_GATEWAY_CALLS = 6    # tool calls, discovery not counted


class BudgetSpent(RuntimeError):
    """A per-turn cap was reached; the caller degrades instead of calling again."""


@dataclass
class Budget:
    """Mutable per turn, never persisted."""

    llm: int = 0
    hn: int = 0
    gateway: int = 0
    repairs_left: int = 1

    def take(self, kind: str) -> None:
        limits = {"llm": MAX_LLM_CALLS, "hn": MAX_HN_REQUESTS, "gateway": MAX_GATEWAY_CALLS}
        used = getattr(self, kind)
        if used >= limits[kind]:
            raise BudgetSpent(f"{kind} budget for this turn is spent ({used})")
        setattr(self, kind, used + 1)

    def left(self, kind: str) -> int:
        limits = {"llm": MAX_LLM_CALLS, "hn": MAX_HN_REQUESTS, "gateway": MAX_GATEWAY_CALLS}
        return limits[kind] - getattr(self, kind)


@dataclass(frozen=True)
class TurnDeps:
    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    hn_http: Any                     # an httpx.AsyncClient with redirects off
    traceparent: str | None
    now: datetime
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: Budget = field(default_factory=Budget)

    def remaining(self) -> float:
        return self.deadline - self.clock()
