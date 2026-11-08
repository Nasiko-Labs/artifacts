"""Per-request dependencies and the turn's limits (deadline, LLM-call budget)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

TURN_DEADLINE_SECS = 330.0   # hard deadline; the platform's flow window is 600 s
SEARCH_DEADLINE_SECS = 180.0
MAX_LLM_CALLS = 10           # the tool-loop steps and the narrate call share it


@dataclass
class Budget:
    """Mutable per turn: LLM calls made so far."""

    max_calls: int = MAX_LLM_CALLS
    calls: int = 0

    def left(self) -> int:
        return max(0, self.max_calls - self.calls)

    def take(self, n: int = 1) -> None:
        self.calls += n


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one; `now` is the turn
    clock (UTC)."""

    llm: Any
    model: str
    gateway: Any
    traceparent: str | None
    now: datetime
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: Budget = field(default_factory=Budget)

    def remaining(self) -> float:
        return self.deadline - self.clock()
