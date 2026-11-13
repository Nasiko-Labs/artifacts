"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout under the turn
deadline, JSON mode, parsing, and a per-turn call budget with at most one repair call per turn."""

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

import prompts
from nasiko_trace import bound_traceparent

log = logging.getLogger("pr-description-writer-stateless.llm")

CALL_TIMEOUT_SECS = 60.0
TURN_DEADLINE_SECS = 240.0
MIN_CALL_SECS = 5.0
MAX_CALLS_PER_TURN = 5
MAX_REPAIRS_PER_TURN = 1
MAX_TOKENS = 2_000
JSON_MODE = {"type": "json_object"}


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

    llm: Any
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: Budget = field(default_factory=Budget)

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def has_time(self, reserve: float = 0.0) -> bool:
        return self.remaining() >= MIN_CALL_SECS + reserve


async def complete_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], contract: str,
                        validate: Callable[[Any], dict | None], max_tokens: int = MAX_TOKENS) -> dict:
    """The request, then one repair call if the answer fails `validate` and the turn's repair is unused."""
    raw = await call(deps, messages, max_tokens)
    parsed = validate(load(raw))
    if parsed is not None:
        return parsed
    if not deps.has_time() or not deps.budget.take_repair():
        raise LlmError("the model's answer did not match the contract and no repair is left")
    log.info("model answer failed its contract; one repair call")
    repaired = validate(load(await call(deps, prompts.repair_json_messages(contract, raw), max_tokens)))
    if repaired is None:
        raise LlmError("the model's answer did not match the contract after one repair")
    return repaired


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
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    return str(raw or "")


def json_chain(deps: TurnDeps, max_tokens: int, timeout: float) -> Runnable:
    """The LCEL runnable for one call: the chat model bound to this call's options | a string parser.
    JSON mode goes in `extra_body`, not `response_format`: ChatOpenAI routes a `response_format` kwarg through
    the SDK's `parse()`, which bypasses the instrumented `create()` and so drops the GenAI token span. On the
    wire the request is the same."""
    model = deps.llm.bind(model=deps.model, max_tokens=max_tokens, timeout=timeout, 
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
