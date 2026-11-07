"""One JSON-mode LLM call, carrying the inbound traceparent, under a per-call timeout and the turn's
call budget (at most MAX_CALLS_PER_TURN calls and TURN_DEADLINE_SECS of wall clock)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from nasiko_trace import bound_traceparent

log = logging.getLogger("drafter.llm")

CALL_TIMEOUT_SECS = 60
TURN_DEADLINE_SECS = 240
MAX_CALLS_PER_TURN = 4
TEMPERATURE = 0.3


class BudgetExhausted(Exception):
    """The turn has used its calls or its time."""


class MissingTraceparent(Exception):
    """The request carried no traceparent, so the LLM router would refuse the call."""


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: Any
    model: str
    traceparent: str | None
    today: date


class Budget:
    """This turn's remaining LLM calls and time. One instance per turn."""

    def __init__(self, max_calls: int = MAX_CALLS_PER_TURN, deadline_secs: float = TURN_DEADLINE_SECS):
        self._max_calls = max_calls
        self._deadline = time.monotonic() + deadline_secs
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    def can_call(self) -> bool:
        return self._used < self._max_calls and self.remaining() > 1

    def remaining(self) -> float:
        return self._deadline - time.monotonic()

    def take(self) -> float:
        """Spend one call; returns the timeout it may use."""
        if not self.can_call():
            raise BudgetExhausted(f"used {self._used} of {self._max_calls} calls")
        self._used += 1
        return min(CALL_TIMEOUT_SECS, self.remaining())


async def ask_json(deps: TurnDeps, budget: Budget, messages: Sequence[Mapping[str, str]]) -> dict | None:
    """The model's JSON object, or None when the answer is not one. Raises MissingTraceparent before
    any network call, and BudgetExhausted when the turn is out of calls."""
    if not deps.traceparent:
        raise MissingTraceparent("the LLM router requires the inbound traceparent")
    timeout = budget.take()
    request = {"model": deps.model, "messages": [dict(m) for m in messages], "temperature": TEMPERATURE,
               "response_format": {"type": "json_object"}, "timeout": timeout,
               "extra_headers": {"traceparent": deps.traceparent}}
    with bound_traceparent(deps.traceparent):
        response = await asyncio.wait_for(deps.llm.chat.completions.create(**request), timeout=timeout)
    content = response.choices[0].message.content or ""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        log.info("model answer is not JSON (%d chars)", len(content))
        return None
    return data if isinstance(data, dict) else None
