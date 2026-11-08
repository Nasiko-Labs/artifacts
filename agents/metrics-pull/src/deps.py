"""Per-request dependencies, shared by the graph and the tool steps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one. `now` is the
    turn's clock (UTC): it stamps `as_of` and anchors every date phrase."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    now: datetime

    @property
    def today(self) -> date:
        return self.now.date()
