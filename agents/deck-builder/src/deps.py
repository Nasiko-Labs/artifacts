"""Per-request dependencies. Built per request: `traceparent` is this request's inbound header, never a
cached one (a resumed task re-mints it from the resume request)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nasiko_mcp import NasikoMcpClient


@dataclass(frozen=True)
class TurnDeps:
    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
