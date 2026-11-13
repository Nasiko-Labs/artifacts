"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout, JSON mode, and a
per-turn call budget (at most 4 calls: rewrite, repair, audit, repair of the audit)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from nasiko_trace import bound_traceparent

log = logging.getLogger("humanizer.llm")

CALL_TIMEOUT_SECS = 60.0
LONG_CALL_TIMEOUT_SECS = 120.0
TURN_DEADLINE_SECS = 240.0
MAX_CALLS_PER_TURN = 4
MAX_TOKENS = 4_000
MAX_OUTPUT_TOKENS = 16_000
CHARS_PER_TOKEN = 3
CHANGES_TOKENS = 1_500
MIN_CALL_SECS = 5.0


class LlmError(RuntimeError):
    """The model could not be reached in time."""


class CallBudget:
    """Counts this turn's model calls. The only mutable object in a turn, owned by that turn alone."""

    def __init__(self, limit: int = MAX_CALLS_PER_TURN):
        self.limit = limit
        self.used = 0

    def left(self) -> int:
        return self.limit - self.used

    def take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
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


@dataclass(frozen=True)
class Answer:
    raw: str
    data: Any  # parsed JSON, or None when the answer was not JSON


async def call_json(deps: TurnDeps, budget: CallBudget, messages: Sequence[Mapping[str, Any]],
                    max_tokens: int = MAX_TOKENS) -> Answer:
    """One call. Raises LlmError when the budget or the deadline is spent, or the call fails."""
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if deps.remaining() < MIN_CALL_SECS or not budget.take():
        raise LlmError("turn budget spent")
    per_call = CALL_TIMEOUT_SECS if max_tokens <= MAX_TOKENS else LONG_CALL_TIMEOUT_SECS
    timeout = max(1.0, min(per_call, deps.remaining()))
    try:
        with bound_traceparent(deps.traceparent):
            response = await deps.llm.chat.completions.create(
                model=deps.model, messages=[dict(m) for m in messages], max_tokens=max_tokens,
                response_format={"type": "json_object"}, timeout=timeout,
                extra_headers={"traceparent": deps.traceparent},
            )
    except Exception as e:  # network, status, timeout: one class for the caller
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    raw = str(response.choices[0].message.content or "")
    return Answer(raw, _load(raw))


def output_tokens(text: str) -> int:
    """Room for the whole edited text plus the change list: a long draft must not be cut mid-JSON."""
    return min(MAX_OUTPUT_TOKENS, max(MAX_TOKENS, len(text) // CHARS_PER_TOKEN + CHANGES_TOKENS))


def can_call(deps: TurnDeps, budget: CallBudget) -> bool:
    return budget.left() > 0 and deps.remaining() >= MIN_CALL_SECS


def _load(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        log.info("model answer was not JSON")
        return None
