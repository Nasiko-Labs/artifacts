"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout under the turn
deadline, JSON mode, and a per-turn budget of two calls (a repair only while the budget allows)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import prompts
from nasiko_trace import bound_traceparent

log = logging.getLogger("token-spend-analyzer.llm")

CALL_TIMEOUT_SECS = 60.0
TURN_DEADLINE_SECS = 240.0
MIN_CALL_SECS = 3.0
MAX_CALLS_PER_TURN = 2
MAX_TOKENS = 900


class LlmError(RuntimeError):
    """The model could not be reached, or never returned JSON matching the contract."""


@dataclass
class Budget:
    max_calls: int = MAX_CALLS_PER_TURN
    calls: int = 0

    def take(self) -> bool:
        if self.calls >= self.max_calls:
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

    def remaining(self) -> float:
        return self.deadline - self.clock()


async def complete_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], contract: str,
                        validate: Callable[[Any], dict | None]) -> dict:
    """The request, then one repair call if the answer fails `validate` and the budget allows."""
    raw = await _call(deps, messages)
    parsed = validate(load(raw))
    if parsed is not None:
        return parsed
    log.info("model answer failed its contract; one repair call if budget allows")
    repaired = validate(load(await _call(deps, prompts.repair_messages(contract, raw))))
    if repaired is None:
        raise LlmError("the model's answer did not match the contract after one repair")
    return repaired


async def _call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if deps.remaining() < MIN_CALL_SECS or not deps.budget.take():
        raise LlmError("no time or call budget left this turn")
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
