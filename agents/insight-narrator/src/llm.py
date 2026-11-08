"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout bounded by the
turn deadline, JSON mode, and a call counter for the per-turn budget (at most two calls)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from nasiko_trace import bound_traceparent

log = logging.getLogger("insight_narrator.llm")

CALL_TIMEOUT_SECS = 60.0
TURN_DEADLINE_SECS = 120.0
MAX_TOKENS = 600
MAX_CALLS = 2


class LlmError(RuntimeError):
    """The model could not be reached, or the turn's time or call budget is spent."""


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: Any
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    calls: list[int] = field(default_factory=list)   # one entry per model request, for the call budget

    def remaining(self) -> float:
        return self.deadline - self.clock()


async def complete(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int = MAX_TOKENS) -> str:
    """One model request; the raw text of its answer."""
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if deps.remaining() <= 0:
        raise LlmError("turn deadline reached")
    if len(deps.calls) >= MAX_CALLS:
        raise LlmError("call budget spent")
    deps.calls.append(1)
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
    return str(response.choices[0].message.content or "")


def load_json(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None
