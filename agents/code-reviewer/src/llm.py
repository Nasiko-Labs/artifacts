"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout under the turn
deadline, JSON mode, parsing, and a per-turn budget (at most MAX_CALLS_PER_TURN calls, of which at most
MAX_REPAIRS_PER_TURN are repairs)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from nasiko_trace import bound_traceparent

log = logging.getLogger("code-reviewer.llm")

CALL_TIMEOUT_SECS = 60.0
TURN_DEADLINE_SECS = 360.0
MIN_CALL_SECS = 5.0
MAX_CALLS_PER_TURN = 9
MAX_REPAIRS_PER_TURN = 2
MAX_TOKENS = 3_000
TEMPERATURE = 0.1


class LlmError(RuntimeError):
    """The model could not be reached, or the turn is out of calls or time."""


class MissingTraceparent(LlmError):
    """No inbound traceparent: the LLM router would refuse the call, so none is made."""


@dataclass
class Budget:
    """Mutable per turn: calls made and repairs left."""

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

    def has_time(self) -> bool:
        return self.remaining() >= MIN_CALL_SECS


async def call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int = MAX_TOKENS) -> str:
    if not deps.traceparent:
        raise MissingTraceparent("no inbound traceparent")
    if not deps.has_time():
        raise LlmError("turn deadline reached")
    deps.budget.take_call()
    timeout = max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining()))
    try:
        with bound_traceparent(deps.traceparent):
            response = await deps.llm.chat.completions.create(
                model=deps.model, messages=[dict(m) for m in messages], max_tokens=max_tokens,
                temperature=TEMPERATURE, response_format={"type": "json_object"}, timeout=timeout,
                extra_headers={"traceparent": deps.traceparent},
            )
    except Exception as e:  # network, status, timeout: one class for the caller
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    return str(response.choices[0].message.content or "")


def load(raw: str) -> dict | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
    except ValueError:
        log.info("model answer is not JSON (%d chars)", len(text))
        return None
    return data if isinstance(data, dict) else None


async def ask_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int = MAX_TOKENS) -> dict | None:
    """The model's JSON object, or None when the answer is not one."""
    return load(await call(deps, messages, max_tokens))
