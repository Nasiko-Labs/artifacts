"""The per-turn LLM budget, enforced in code: a wrapper around the LLM client that refuses the next
call once the turn has passed its deadline or spent its call cap. Steps catch `TurnLimitReached` and
render what they have with the step-limit note."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


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
