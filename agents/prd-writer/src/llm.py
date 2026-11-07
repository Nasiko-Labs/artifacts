"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout, the turn
deadline, a per-turn call budget, JSON mode, and one repair call when the answer is not valid JSON."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import prompts
from nasiko_trace import bound_traceparent

log = logging.getLogger("prd-writer.llm")

CALL_TIMEOUT_SECS = 90.0
TURN_DEADLINE_SECS = 240.0
MAX_TOKENS = 2_000
MAX_CALLS_PER_TURN = 5
MIN_CALL_SECS = 1.0


class LlmError(RuntimeError):
    """The model could not be reached, or never returned JSON matching the contract."""


class DeadlineError(LlmError):
    """The turn deadline passed, or the turn's call budget is spent."""


class CallBudget:
    """Counts this turn's model calls (the one mutable piece of a turn, owned by its TurnDeps)."""

    def __init__(self, limit: int = MAX_CALLS_PER_TURN):
        self.limit, self.used = limit, 0

    def left(self) -> int:
        return self.limit - self.used

    def take(self) -> None:
        if self.used >= self.limit:
            raise DeadlineError("call budget spent")
        self.used += 1


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: Any
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: CallBudget = field(default_factory=CallBudget)

    def remaining(self) -> float:
        return self.deadline - self.clock()


async def complete_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], contract: str,
                        validate: Callable[[Any], dict | None], max_tokens: int = MAX_TOKENS) -> dict:
    """The request, then one repair if the answer fails `validate` and the budget allows it."""
    raw = await _call(deps, messages, max_tokens)
    parsed = validate(_load(raw))
    if parsed is not None:
        return parsed
    if deps.budget.left() <= 0:
        raise LlmError("the model's answer did not match the contract and no call is left to repair it")
    log.info("model answer failed its contract; one repair call")
    repaired = validate(_load(await _call(deps, prompts.repair_messages(contract, raw), max_tokens)))
    if repaired is None:
        raise LlmError("the model's answer did not match the contract after one repair")
    return repaired


async def _call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int) -> str:
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if deps.remaining() < MIN_CALL_SECS:
        raise DeadlineError("turn deadline passed")
    deps.budget.take()
    timeout = max(MIN_CALL_SECS, min(CALL_TIMEOUT_SECS, deps.remaining()))
    try:
        with bound_traceparent(deps.traceparent):
            response = await deps.llm.chat.completions.create(
                model=deps.model, messages=[dict(m) for m in messages], max_tokens=max_tokens,
                response_format={"type": "json_object"}, timeout=timeout,
                extra_headers={"traceparent": deps.traceparent},
            )
    except Exception as e:  # network, status, timeout: one class for the caller
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    return str(response.choices[0].message.content or "")


def _load(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None
