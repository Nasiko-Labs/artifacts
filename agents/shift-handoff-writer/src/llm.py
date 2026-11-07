"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout, the turn's call
budget, JSON mode, and one repair call when the answer is not valid JSON for its contract."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import openai

import prompts
from nasiko_trace import bound_traceparent

log = logging.getLogger("shift_handoff.llm")

CALL_TIMEOUT_SECS = 90.0
TURN_DEADLINE_SECS = 240.0
MIN_CALL_SECS = 5.0
MAX_TOKENS = 2_500
MAX_CALLS_PER_TURN = 12
TRANSIENT_RETRIES = 1


class LlmError(RuntimeError):
    """The model could not be reached, the budget is spent, or the answer never matched the contract."""


@dataclass
class Budget:
    limit: int = MAX_CALLS_PER_TURN
    calls: int = 0

    def left(self) -> int:
        return self.limit - self.calls


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: Any
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    now: Callable[[], datetime] = datetime.now
    budget: Budget = field(default_factory=Budget)

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def can_call(self) -> bool:
        return self.budget.left() > 0 and self.remaining() >= MIN_CALL_SECS


async def complete_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], contract: str,
                        validate: Callable[[Any], dict | None], max_tokens: int = MAX_TOKENS) -> dict:
    """At most two calls: the request, then one repair (budget and time allowing) if `validate` fails."""
    raw = await _call(deps, messages, max_tokens)
    parsed = validate(_load(raw))
    if parsed is not None:
        return parsed
    if not deps.can_call():
        raise LlmError("the model's answer did not match the contract and no budget is left to repair it")
    log.info("model answer failed its contract; one repair call")
    repaired = validate(_load(await _call(deps, prompts.repair_messages(contract, raw), max_tokens)))
    if repaired is None:
        raise LlmError("the model's answer did not match the contract after one repair")
    return repaired


async def _call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int) -> str:
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if not deps.can_call():
        raise LlmError("the turn's call budget or deadline is spent")
    deps.budget.calls += 1
    for attempt in range(1 + TRANSIENT_RETRIES):
        timeout = max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining()))
        try:
            with bound_traceparent(deps.traceparent):
                response = await deps.llm.chat.completions.create(
                    model=deps.model, messages=[dict(m) for m in messages], max_tokens=max_tokens,
                    response_format={"type": "json_object"}, timeout=timeout,
                    extra_headers={"traceparent": deps.traceparent},
                )
            return str(response.choices[0].message.content or "")
        except Exception as e:  # network, status, timeout: one class for the caller
            if attempt < TRANSIENT_RETRIES and _transient(e) and deps.remaining() >= MIN_CALL_SECS:
                log.info("model call failed transiently (%s); one retry", type(e).__name__)
                continue
            raise LlmError(f"model call failed: {type(e).__name__}") from e
    raise LlmError("model call failed")  # pragma: no cover - the loop always returns or raises


def _transient(error: Exception) -> bool:
    """A router 5xx or a dropped connection is worth one retry; a 4xx (e.g. a 403 on the traceparent) is not."""
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status >= 500
    return isinstance(error, (openai.APIConnectionError, openai.APITimeoutError))


def _load(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None
