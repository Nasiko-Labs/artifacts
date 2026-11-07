"""Per-request dependencies, shared by the graph and the write walk. Tool discovery is in discovery.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from nasiko_mcp import NasikoMcpClient


@dataclass(frozen=True)
class Limits:
    """LLM calls per step (the last one is kept for a JSON repair) and wall-clock caps in seconds.
    Turn 1: intake 1 + find 4 + docs 3 + extract 2 + status 4 + repeats 3 = 17 calls at most."""

    find_calls: int = 4
    docs_calls: int = 3
    extract_calls: int = 2
    status_calls: int = 4
    repeats_calls: int = 3
    gather_secs: float = 120.0
    extract_secs: float = 40.0
    status_secs: float = 90.0
    repeats_secs: float = 60.0
    turn_secs: float = 330.0
    repair_secs: float = 20.0


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    today: date
    limits: Limits = field(default_factory=Limits)
