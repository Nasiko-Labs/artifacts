"""Per-request dependencies for the meeting-card mode."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nasiko_mcp import NasikoMcpClient


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one; `now` is
    this turn's clock (fixed in tests); `monotonic` drives the time guard."""

    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    now: datetime
    default_tz: str | None = None
    monotonic: Callable[[], float] = field(default=time.monotonic)
