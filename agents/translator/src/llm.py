"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout bounded by the
turn deadline, JSON mode, and parsing. Repairs are decided by the callers, which know the contract."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from nasiko_trace import bound_traceparent

log = logging.getLogger("translator.llm")

CALL_TIMEOUT_SECS = 90.0
TURN_DEADLINE_SECS = 300.0
MIN_CALL_SECS = 5.0
MAX_TOKENS = 4_000
SCOPE_MAX_TOKENS = 20


class LlmError(RuntimeError):
    """The model could not be reached."""


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

    def has_time(self) -> bool:
        return self.remaining() > MIN_CALL_SECS


async def call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int = MAX_TOKENS) -> str:
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
        log.warning("model call failed: %s", type(e).__name__)
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


async def scope(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """"text" or "off_topic"; anything unreadable is treated as off_topic (fail closed)."""
    parsed = load(await call(deps, messages, SCOPE_MAX_TOKENS))
    status = parsed.get("status") if isinstance(parsed, dict) else None
    return "text" if status == "text" else "off_topic"
