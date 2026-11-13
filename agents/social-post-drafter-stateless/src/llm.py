"""One JSON-mode LLM call through a LangChain `ChatOpenAI` runnable, carrying the inbound traceparent, under a per-call timeout and the turn's
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

from langchain_core.messages import convert_to_messages
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable

from nasiko_trace import bound_traceparent

log = logging.getLogger("social_post_drafter_stateless.llm")

CALL_TIMEOUT_SECS = 60
TURN_DEADLINE_SECS = 180
MAX_CALLS_PER_TURN = 5
TEMPERATURE = 0.6
JSON_MODE = {"type": "json_object"}


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
    with bound_traceparent(deps.traceparent):
        chain = json_chain(deps, timeout)
        content = await asyncio.wait_for(chain.ainvoke(convert_to_messages([dict(m) for m in messages])), timeout=timeout)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        log.info("model answer is not JSON (%d chars)", len(content))
        return None
    return data if isinstance(data, dict) else None


def json_chain(deps: TurnDeps, timeout: float) -> Runnable:
    """The LCEL runnable for one call: the chat model bound to this call's options | a string parser.
    JSON mode goes in `extra_body`, not `response_format`: ChatOpenAI routes a `response_format` kwarg through
    the SDK's `parse()`, which bypasses the instrumented `create()` and so drops the GenAI token span. On the
    wire the request is the same."""
    model = deps.llm.bind(model=deps.model, temperature=TEMPERATURE, timeout=timeout,
                          extra_headers={"traceparent": deps.traceparent}, extra_body={"response_format": JSON_MODE})
    return model | StrOutputParser()
