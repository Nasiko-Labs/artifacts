"""The one LLM call wrapper: inbound traceparent on every request, JSON mode, a per-call timeout under the
turn deadline, and the per-turn call cap from deps.Budget."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from deps import BudgetSpent, TurnDeps
from nasiko_trace import bound_traceparent

log = logging.getLogger("opportunity_brief.llm")

CALL_TIMEOUT_SECS = 60.0
MIN_CALL_SECS = 5.0
MAX_TOKENS = 2_000


class LlmError(RuntimeError):
    """The model could not be reached, the budget is spent, or the answer was not JSON."""


async def call_json(deps: TurnDeps, system: str, user: str, max_tokens: int = MAX_TOKENS) -> dict:
    """One model call returning a JSON object, or LlmError."""
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if deps.remaining() < MIN_CALL_SECS:
        raise LlmError("turn deadline reached")
    try:
        deps.budget.take("llm")
    except BudgetSpent as e:
        raise LlmError(str(e)) from e
    timeout = max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining()))
    messages: Sequence[Mapping[str, Any]] = ({"role": "system", "content": system},
                                             {"role": "user", "content": user})
    try:
        with bound_traceparent(deps.traceparent):
            response = await deps.llm.chat.completions.create(
                model=deps.model, messages=[dict(m) for m in messages], max_tokens=max_tokens,
                response_format={"type": "json_object"}, timeout=timeout,
                extra_headers={"traceparent": deps.traceparent},
            )
    except Exception as e:  # network, status, timeout: one class for the caller
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    parsed = load(str(response.choices[0].message.content or ""))
    if not isinstance(parsed, dict):
        raise LlmError("the model did not return a JSON object")
    return parsed


def load(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None
