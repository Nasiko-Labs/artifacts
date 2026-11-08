"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout under the turn
deadline, JSON mode, the per-turn call budget, and one repair call when the answer fails its contract."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import prompts
from deps import TurnDeps
from nasiko_trace import bound_traceparent

log = logging.getLogger("review_miner.llm")

CALL_TIMEOUT_SECS = 90.0
MIN_CALL_SECS = 5.0
MAX_TOKENS = 3_000


class LlmError(RuntimeError):
    """The model could not be reached, the budget is spent, or the answer never matched the contract."""


async def complete_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]],
                        validate: Callable[[Any], Any | None]) -> Any:
    raw = await call(deps, messages, json_mode=True)
    parsed = validate(load(raw))
    if parsed is not None:
        return parsed
    log.info("model answer failed its contract; one repair call")
    repaired = validate(load(await call(deps, prompts.repair_messages(messages, raw), json_mode=True)))
    if repaired is None:
        raise LlmError("the model's answer did not match the contract after one repair")
    return repaired


async def call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], json_mode: bool = False) -> str:
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if deps.remaining() < MIN_CALL_SECS:
        deps.budget.exhausted = True
        raise LlmError("no time left this turn")
    if not deps.budget.take():
        raise LlmError("no model calls left this turn")
    extra: dict[str, Any] = {"response_format": {"type": "json_object"}} if json_mode else {}
    try:
        with bound_traceparent(deps.traceparent):
            response = await deps.llm.chat.completions.create(
                model=deps.model, messages=[dict(m) for m in messages], max_tokens=MAX_TOKENS,
                timeout=max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining())),
                extra_headers={"traceparent": deps.traceparent}, **extra)
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
