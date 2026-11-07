"""Per-request dependencies, shared by the read steps, the proposal and the create walk."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from config import CONFIG, Config
from nasiko_mcp import NasikoMcpClient


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one; `now` is
    the turn's fixed clock (UTC)."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    now: datetime
    config: Config = field(default=CONFIG)

    @property
    def today(self) -> date:
        return self.now.date()
