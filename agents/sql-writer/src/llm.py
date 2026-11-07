"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout under the turn deadline,
JSON mode, parsing, and a per-turn call budget (write and optimize turns make at most four calls)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from nasiko_trace import bound_traceparent

log = logging.getLogger("sql-writer.llm")

CALL_TIMEOUT_SECS = 60.0
TURN_DEADLINE_SECS = 240.0
MIN_CALL_SECS = 5.0
MAX_CALLS_PER_TURN = 4
MAX_TOKENS = 1_500


class LlmError(RuntimeError):
    """The model could not be reached, the budget or deadline is spent, or no traceparent is bound."""


@dataclass
class Budget:
    max_calls: int = MAX_CALLS_PER_TURN
    calls: int = 0

    def left(self) -> int:
        return self.max_calls - self.calls

    def take(self) -> None:
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

    def can_call(self) -> bool:
        return self.remaining() >= MIN_CALL_SECS and self.budget.left() > 0


async def call_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int = MAX_TOKENS) -> dict | None:
    """One call; the parsed JSON object, or None when the answer isn't a JSON object (the caller decides)."""
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if deps.remaining() < MIN_CALL_SECS:
        raise LlmError("turn deadline reached")
    deps.budget.take()
    timeout = max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining()))
    try:
        with bound_traceparent(deps.traceparent):
            response = await deps.llm.chat.completions.create(
                model=deps.model, messages=[dict(m) for m in messages], max_tokens=max_tokens,
                response_format={"type": "json_object"}, timeout=timeout,
                extra_headers={"traceparent": deps.traceparent},
            )
    except Exception as e:  # network, status, timeout: one class for the caller
        log.warning("model call failed: %s", type(e).__name__)
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    data = load(str(response.choices[0].message.content or ""))
    return data if isinstance(data, dict) else None


def load(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None
