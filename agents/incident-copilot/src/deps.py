"""Per-request dependencies and the time/step limits every step reads. Built once per request."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient


@dataclass(frozen=True)
class Limits:
    """Bounds for one turn (seconds and LLM calls). Tests pass smaller ones."""

    incident_secs: float = 60.0
    loop_secs: float = 120.0
    summarize_secs: float = 20.0
    turn_secs: float = 330.0
    parallel: int = 3


@dataclass(frozen=True)
class TurnDeps:
    """`traceparent` is this request's inbound header, never a cached one. `now` is the turn's clock."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    now: datetime
    limits: Limits = field(default_factory=Limits)
    started: float = field(default_factory=time.monotonic)

    def seconds_left(self) -> float:
        return self.limits.turn_secs - (time.monotonic() - self.started)
