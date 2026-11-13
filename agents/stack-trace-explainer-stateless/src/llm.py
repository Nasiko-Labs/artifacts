"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout under the turn deadline,
JSON mode, and a per-turn call budget (4 calls per turn, whatever mix of explain / repair / follow-up)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import convert_to_messages
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable

from nasiko_trace import bound_traceparent

log = logging.getLogger("stack-trace-explainer-stateless.llm")

CALL_TIMEOUT_SECS = 60.0
TURN_DEADLINE_SECS = 240.0
MIN_CALL_SECS = 5.0
MAX_CALLS_PER_TURN = 4
MAX_TOKENS = 1_400
JSON_MODE = {"type": "json_object"}
TEMPERATURE = 0.1


class LlmError(RuntimeError):
    """The model could not be reached, or the turn has no call or time left."""


@dataclass
class Budget:
    """Mutable per turn: how many calls were made out of the turn's allowance."""

    max_calls: int = MAX_CALLS_PER_TURN
    calls: int = 0

    @property
    def left(self) -> int:
        return self.max_calls - self.calls

    def take_call(self) -> None:
        if self.calls >= self.max_calls:
            raise LlmError("call budget for this turn is spent")
        self.calls += 1


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: Any
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: Budget = field(default_factory=Budget)

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
            raw = await json_chain(deps, max_tokens, timeout).ainvoke(convert_to_messages([dict(m) for m in messages]))
    except Exception as e:  # network, status, timeout: one class for the caller
        log.warning("model call failed: %s", type(e).__name__)
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    return str(raw or "")


async def call_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> dict | None:
    """One call, parsed as a JSON object; None when the answer is not one."""
    loaded = load(await call(deps, messages))
    return loaded if isinstance(loaded, dict) else None


def json_chain(deps: TurnDeps, max_tokens: int, timeout: float) -> Runnable:
    """The LCEL runnable for one call: the chat model bound to this call's options | a string parser.
    JSON mode goes in `extra_body`, not `response_format`: ChatOpenAI routes a `response_format` kwarg through
    the SDK's `parse()`, which bypasses the instrumented `create()` and so drops the GenAI token span. On the
    wire the request is the same."""
    model = deps.llm.bind(model=deps.model, max_tokens=max_tokens, timeout=timeout, temperature=TEMPERATURE,
                          extra_headers={"traceparent": deps.traceparent}, extra_body={"response_format": JSON_MODE})
    return model | StrOutputParser()


def load(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None
