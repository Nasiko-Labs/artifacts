"""The turn's call budgets, enforced in code: one LLM meter shared by every step (the steps run concurrently,
so the cap holds however their calls interleave), and a per-step cap on tool calls."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from nasiko_mcp import ToolOk, ToolResult

log = logging.getLogger("launch_readiness.budget")


class BudgetExhausted(Exception):
    """The turn's LLM call budget is spent; the step salvages what it has."""


class _Completions:
    def __init__(self, meter: MeteredLLM):
        self._meter = meter

    async def create(self, **request: Any) -> Any:
        self._meter.take()
        return await self._meter.inner.chat.completions.create(**request)


class _Chat:
    def __init__(self, meter: MeteredLLM):
        self.completions = _Completions(meter)


class MeteredLLM:
    """Looks like the OpenAI client to the tool loop (`llm.chat.completions.create`), and counts."""

    def __init__(self, inner: Any, limit: int):
        self.inner = inner
        self.limit = limit
        self.used = 0
        self.chat = _Chat(self)

    def take(self) -> None:
        if self.used >= self.limit:
            log.warning("LLM call budget (%d) spent", self.limit)
            raise BudgetExhausted(f"LLM call budget of {self.limit} spent")
        self.used += 1

    @property
    def left(self) -> int:
        return max(self.limit - self.used, 0)


class CappedCaller:
    """A ToolCaller that answers with a note after `limit` calls, so a step never exceeds its share."""

    def __init__(self, inner: Any, limit: int):
        self._inner = inner
        self._limit = limit
        self.calls = 0
        self.hit = False

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if self.calls >= self._limit:
            self.hit = True
            note = {"error": f"tool call limit ({self._limit}) for this step reached; answer now with what you have"}
            return ToolOk(content=({"type": "text", "text": json.dumps(note)},), is_error=True, raw={})
        self.calls += 1
        return await self._inner.call_tool(name, arguments, traceparent)
