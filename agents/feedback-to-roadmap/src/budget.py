"""The per-turn budgets, enforced in code: a wrapper around the LLM client that refuses the next call once the
turn has passed its deadline or spent its call cap (steps catch `TurnLimitReached` and render what they have
with the step-limit note), and a tool-call counter that answers over-budget calls with a note instead of
reaching the gateway."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from nasiko_mcp import ToolOk, ToolResult


class TurnLimitReached(Exception):
    """The turn's deadline passed or its LLM-call cap is spent; no further LLM call is made."""


class _Completions:
    def __init__(self, owner: BudgetedLLM):
        self._owner = owner

    async def create(self, **request: Any) -> Any:
        self._owner.spend()
        return await self._owner.inner.chat.completions.create(**request)


class _Chat:
    def __init__(self, owner: BudgetedLLM):
        self.completions = _Completions(owner)


class BudgetedLLM:
    """Looks like an OpenAI async client to the tool loop (`llm.chat.completions.create`)."""

    def __init__(self, inner: Any, *, max_calls: int, deadline_secs: float,
                 clock: Callable[[], float] = time.monotonic):
        self.inner = inner
        self._max_calls = max_calls
        self._clock = clock
        self._deadline = clock() + deadline_secs
        self._calls = 0
        self.chat = _Chat(self)

    @property
    def calls(self) -> int:
        return self._calls

    def spend(self) -> None:
        if self._clock() >= self._deadline:
            raise TurnLimitReached("turn deadline passed")
        if self._calls >= self._max_calls:
            raise TurnLimitReached("turn LLM-call cap spent")
        self._calls += 1


class ToolBudget:
    """A ToolCaller that forwards at most `limit` calls; the rest get an error note the model can read."""

    def __init__(self, inner: Any, limit: int):
        self._inner = inner
        self._limit = limit
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def exhausted(self) -> bool:
        return self._calls >= self._limit

    def is_offered(self, name: str) -> bool:
        checker = getattr(self._inner, "is_offered", None)
        return checker(name) if checker else True

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if self.exhausted:
            note = json.dumps({"error": "Tool-call budget for this step is spent. Answer now with what you have."})
            return ToolOk(content=({"type": "text", "text": note},), is_error=True, raw={})
        self._calls += 1
        return await self._inner.call_tool(name, arguments, traceparent)
