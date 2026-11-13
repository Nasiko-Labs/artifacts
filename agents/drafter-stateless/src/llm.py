"""One JSON-mode LLM call, carrying the inbound traceparent, under a per-call timeout and the turn's
call budget (at most MAX_CALLS_PER_TURN calls and TURN_DEADLINE_SECS of wall clock). The call is a LangChain
LCEL chain (`ChatOpenAI.bind(...) | StrOutputParser()`); no LangChain memory: each call sees only the messages
built for it from this turn's input and the platform history slice."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import convert_to_messages
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from nasiko_trace import bound_traceparent

log = logging.getLogger("drafter-stateless.llm")

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

    llm: BaseChatModel
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


def build_model(model: str, *, base_url: str | None = None, api_key: str | None = None,
                http_async_client: Any = None, max_retries: int | None = None) -> ChatOpenAI:
    """The chat model. The LLM router injects OPENAI_BASE_URL / OPENAI_API_KEY, read here unless given;
    constructing it makes no call. `max_retries=None` keeps the OpenAI client's default, as the original."""
    return ChatOpenAI(model=model, api_key=api_key or os.environ.get("OPENAI_API_KEY") or "unset",
                      base_url=base_url or os.environ.get("OPENAI_BASE_URL"), temperature=TEMPERATURE,
                      max_retries=max_retries, http_async_client=http_async_client)


def json_chain(llm: BaseChatModel, traceparent: str, timeout: float) -> Runnable:
    """messages -> JSON-mode completion -> raw text. The traceparent rides on the request headers.

    `response_format` goes in `extra_body` on purpose: as a top-level kwarg ChatOpenAI would route JSON mode
    through `completions.parse`, which the OpenAI GenAI instrumentor does not wrap (no token spans). This way the
    wire body matches the plain-SDK original and the call goes through the instrumented `completions.create`."""
    bound = llm.bind(extra_body={"response_format": {"type": "json_object"}}, timeout=timeout,
                     extra_headers={"traceparent": traceparent})
    return bound | StrOutputParser()


async def ask_json(deps: TurnDeps, budget: Budget, messages: Sequence[Mapping[str, str]]) -> dict | None:
    """The model's JSON object, or None when the answer is not one. Raises MissingTraceparent before
    any network call, and BudgetExhausted when the turn is out of calls."""
    if not deps.traceparent:
        raise MissingTraceparent("the LLM router requires the inbound traceparent")
    timeout = budget.take()
    chain = json_chain(deps.llm, deps.traceparent, timeout)
    with bound_traceparent(deps.traceparent):
        content = await asyncio.wait_for(chain.ainvoke(convert_to_messages([dict(m) for m in messages])),
                                         timeout=timeout) or ""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        log.info("model answer is not JSON (%d chars)", len(content))
        return None
    return data if isinstance(data, dict) else None
