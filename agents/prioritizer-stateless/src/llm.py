"""The one LLM call, as a LangChain LCEL runnable (messages | ChatOpenAI | StrOutputParser): the inbound
traceparent on every request, a per-call timeout under the turn deadline, JSON mode, parsing, and a per-turn
call budget (two rating batches + one repair)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable, RunnableLambda

from nasiko_trace import bound_traceparent

log = logging.getLogger("prioritizer.llm")

CALL_TIMEOUT_SECS = 60.0
TURN_DEADLINE_SECS = 240.0
MIN_CALL_SECS = 5.0
MAX_CALLS_PER_TURN = 3
MAX_REPAIRS_PER_TURN = 1
MAX_TOKENS = 2_000


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


def utc_today() -> date:
    return datetime.now(UTC).date()


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: BaseChatModel  # a ChatOpenAI on OPENAI_BASE_URL / OPENAI_API_KEY; `model` is bound per call
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: Budget = field(default_factory=Budget)
    today: Callable[[], date] = utc_today

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def has_time(self) -> bool:
        return self.remaining() >= MIN_CALL_SECS


async def call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int = MAX_TOKENS) -> str:
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if not deps.has_time():
        raise LlmError("turn deadline reached")
    deps.budget.take_call()
    timeout = max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining()))
    try:
        with bound_traceparent(deps.traceparent):
            text = await json_chain(deps, max_tokens, timeout).ainvoke(list(messages))
    except Exception as e:  # network, status, timeout: one class for the caller
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    return str(text or "")


def json_chain(deps: TurnDeps, max_tokens: int, timeout: float) -> Runnable:
    """messages -> chat model in JSON mode (this request's traceparent as a header) -> the reply text.

    `response_format` and `max_tokens` ride in `extra_body` on purpose: as top-level kwargs ChatOpenAI would
    send `max_completion_tokens` and route JSON mode through `completions.parse`, which the OpenAI GenAI
    instrumentor does not wrap (no token spans). This way the wire body matches the plain-SDK original and the
    call goes through the instrumented `completions.create`."""
    model = deps.llm.bind(model=deps.model, timeout=timeout, extra_headers={"traceparent": deps.traceparent},
                          extra_body={"response_format": {"type": "json_object"}, "max_tokens": max_tokens})
    return RunnableLambda(_as_messages) | model | StrOutputParser()


def _as_messages(messages: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """(role, content) pairs: LangChain builds the messages as-is, never templating the braces in prompts."""
    return [(str(m["role"]), str(m["content"])) for m in messages]


def load(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None
