"""Per-request dependencies, built by the executor for one turn."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient

DEADLINE_SECS = 180.0  # the platform accepts a flow for 600 s; nothing new starts after this
MAX_LLM_CALLS = 4
MAX_TOOL_CALLS = 16  # chat reads; the gateway's tool-search lookups are not counted


@dataclass
class _Counter:
    llm: int = 0


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
    _counter: _Counter = field(default_factory=_Counter)

    def elapsed(self) -> float:
        return self.clock() - self.started

    def past_deadline(self) -> bool:
        return self.elapsed() >= DEADLINE_SECS

    def count_llm_call(self) -> None:
        self._counter.llm += 1

    @property
    def llm_calls(self) -> int:
        return self._counter.llm

    @property
    def llm_budget_left(self) -> int:
        return MAX_LLM_CALLS - self._counter.llm
