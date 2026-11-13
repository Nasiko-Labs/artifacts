"""The one LLM call wrapper, a LangChain LCEL chain (`ChatOpenAI.bind(...) | StrOutputParser()`): the inbound
traceparent on every request, a per-call timeout under the turn deadline, JSON mode, parsing, and a per-turn call
budget (hard cap 6) with at most one repair call. No LangChain memory: each call sees only the messages built
for it from this turn's input (and the platform history slice)."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import convert_to_messages
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from nasiko_trace import bound_traceparent

log = logging.getLogger("task-breakdown-stateless.llm")

CALL_TIMEOUT_SECS = 60.0
TURN_DEADLINE_SECS = 240.0
MIN_CALL_SECS = 5.0
MAX_CALLS_PER_TURN = 6
MAX_REPAIRS_PER_TURN = 1
MAX_TOKENS = 6_000
TEMPERATURE = 0.2


class LlmError(RuntimeError):
    """The model could not be reached, or never returned JSON matching the contract."""


@dataclass
class Budget:
    """Mutable per turn: how many calls were made and whether the one repair is still unused."""

    max_calls: int = MAX_CALLS_PER_TURN
    calls: int = 0
    repairs_left: int = MAX_REPAIRS_PER_TURN

    def take_call(self) -> None:
        if self.calls >= self.max_calls:
            raise LlmError("call budget for this turn is spent")
        self.calls += 1

    def take_repair(self) -> bool:
        if self.repairs_left <= 0 or self.calls >= self.max_calls:
            return False
        self.repairs_left -= 1
        return True


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: BaseChatModel
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: Budget = field(default_factory=Budget)

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def has_time(self) -> bool:
        return self.remaining() >= MIN_CALL_SECS


def build_model(model: str, *, base_url: str | None = None, api_key: str | None = None,
                http_async_client: Any = None) -> ChatOpenAI:
    """The chat model. The LLM router injects OPENAI_BASE_URL / OPENAI_API_KEY, read here unless given;
    constructing it makes no call. No client retries: one call is one budgeted attempt."""
    return ChatOpenAI(model=model, api_key=api_key or os.environ.get("OPENAI_API_KEY") or "unset",
                      base_url=base_url or os.environ.get("OPENAI_BASE_URL"), temperature=TEMPERATURE,
                      max_retries=0, http_async_client=http_async_client)


def json_chain(llm: BaseChatModel, traceparent: str, max_tokens: int, timeout: float) -> Runnable:
    """messages -> JSON-mode completion -> raw text. The traceparent rides on the request headers.

    `response_format` and `max_tokens` go in `extra_body` on purpose: as top-level kwargs ChatOpenAI would send
    `max_completion_tokens` instead and route JSON mode through `completions.parse`, which the OpenAI GenAI
    instrumentor does not wrap (no token spans). This way the wire body matches the plain-SDK original and the
    call goes through the instrumented `completions.create`."""
    bound = llm.bind(extra_body={"response_format": {"type": "json_object"}, "max_tokens": max_tokens},
                     timeout=timeout, extra_headers={"traceparent": traceparent})
    return bound | StrOutputParser()


async def ask_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]],
                   validate: Callable[[Any], dict | None], max_tokens: int = MAX_TOKENS) -> dict | None:
    """One call; the validated JSON object, or None when the answer does not match the contract."""
    return validate(load(await call(deps, messages, max_tokens)))


async def call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int = MAX_TOKENS) -> str:
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if not deps.has_time():
        raise LlmError("turn deadline reached")
    deps.budget.take_call()
    timeout = max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining()))
    try:
        with bound_traceparent(deps.traceparent):
            chain = json_chain(deps.llm, deps.traceparent, max_tokens, timeout)
            text = await chain.ainvoke(convert_to_messages([dict(m) for m in messages]))
    except Exception as e:  # network, status, timeout: one class for the caller
        log.warning("model call failed: %s", type(e).__name__)
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    return str(text or "")


def load(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None
