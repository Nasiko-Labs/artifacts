"""Per-request dependencies, shared by the graph and the write walk. Tool discovery is in discovery.py."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one; `now` is
    the turn clock (UTC)."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    now: datetime
