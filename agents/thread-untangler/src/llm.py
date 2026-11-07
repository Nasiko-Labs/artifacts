"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout, JSON mode,
parsing, and at most one repair call when the answer is not valid JSON for its contract. The repair is
drawn from a per-turn `RepairBudget`, so a chunked turn spends one repair in total, not one per chunk."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import prompts
from nasiko_trace import bound_traceparent

log = logging.getLogger("thread_untangler.llm")

CALL_TIMEOUT_SECS = 90.0
TURN_DEADLINE_SECS = 420.0
MAX_TOKENS = 2_000


class LlmError(RuntimeError):
    """The model could not be reached, or never returned JSON matching the contract."""


class RepairBudget:
    """How many repair calls this turn may still make (shared by every call that is given it)."""

    def __init__(self, repairs: int = 1):
        self._left = repairs

    def take(self) -> bool:
        if self._left <= 0:
            return False
        self._left -= 1
        return True



@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: Any
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)

    def remaining(self) -> float:
        return self.deadline - self.clock()


async def complete_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], contract: str,
                        validate: Callable[[Any], dict | None], budget: RepairBudget | None = None,
                        max_tokens: int = MAX_TOKENS) -> dict:
    """The request, then one repair only if `budget` still allows it and the answer fails `validate`."""
    raw = await _call(deps, messages, max_tokens)
    parsed = validate(_load(raw))
    if parsed is not None:
        return parsed
    if budget is None or not budget.take():
        raise LlmError("the model's answer did not match the contract and no repair is left")
    log.info("model answer failed its contract; one repair call")
    repaired = validate(_load(await _call(deps, prompts.repair_messages(contract, raw), max_tokens)))
    if repaired is None:
        raise LlmError("the model's answer did not match the contract after one repair")
    return repaired


async def _call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int) -> str:
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    timeout = max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining()))
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
