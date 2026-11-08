"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout under the turn
deadline, JSON mode, and a per-turn call budget (attribute <= 16 + write <= 10 + repair <= 2 = 28)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from nasiko_trace import bound_traceparent

log = logging.getLogger("kudos-collector-offline.llm")

CALL_TIMEOUT_SECS = 90.0
TURN_DEADLINE_SECS = 300.0
MIN_CALL_SECS = 3.0
MAX_CALLS_PER_TURN = 28
MAX_TOKENS = 3000
CONCURRENCY = 4


class LlmError(RuntimeError):
    """The model could not be reached, or returned nothing usable."""


class DeadlineError(LlmError):
    """No time or call budget left this turn: callers fall back to code-only output."""


@dataclass
class Budget:
    max_calls: int = MAX_CALLS_PER_TURN
    calls: int = 0
    exhausted: bool = False

    def take(self) -> bool:
        if self.calls >= self.max_calls:
            self.exhausted = True
            return False
        self.calls += 1
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
    today: date = field(default_factory=date.today)

    def remaining(self) -> float:
        return self.deadline - self.clock()


async def complete_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]],
                        validate: Callable[[Any], Any | None]) -> Any:
    """One call; the validated answer, or LlmError. Callers decide their own fallback (no hidden retries)."""
    raw = await _call(deps, messages)
    parsed = validate(load(raw))
    if parsed is None:
        log.info("model answer failed its contract")
        raise LlmError("the model's answer did not match the contract")
    return parsed


async def _call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if deps.remaining() < MIN_CALL_SECS:
        deps.budget.exhausted = True
        raise DeadlineError("no time left this turn")
    if not deps.budget.take():
        raise DeadlineError("no call budget left this turn")
    timeout = max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining()))
    try:
        with bound_traceparent(deps.traceparent):
            response = await deps.llm.chat.completions.create(
                model=deps.model, messages=[dict(m) for m in messages], max_tokens=MAX_TOKENS,
                response_format={"type": "json_object"}, timeout=timeout,
                extra_headers={"traceparent": deps.traceparent},
            )
    except Exception as e:  # network, status, timeout: one class for the caller
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    return str(response.choices[0].message.content or "")


def load(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None
