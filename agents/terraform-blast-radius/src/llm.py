"""The tool-less JSON LLM call: the inbound traceparent on every request, a per-call timeout under the turn
deadline, the turn's shared call budget, and at most one repair call."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import prompts
from deps import TurnDeps
from nasiko_trace import bound_traceparent

log = logging.getLogger("terraform_blast_radius.llm")

CALL_TIMEOUT_SECS = 90.0
MIN_CALL_SECS = 5.0
MAX_TOKENS = 1_200


class LlmError(RuntimeError):
    """The model could not be reached, or never returned JSON matching the contract."""


async def complete_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], contract: str,
                        validate: Callable[[Any], dict | None]) -> dict:
    """The request, then one repair call if the answer fails `validate`."""
    raw = await call(deps, messages)
    parsed = validate(load(raw))
    if parsed is not None:
        return parsed
    log.info("model answer failed its contract; one repair call")
    repaired = validate(load(await call(deps, prompts.repair_messages(contract, raw))))
    if repaired is None:
        raise LlmError("the model's answer did not match the contract after one repair")
    return repaired


async def call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if deps.remaining() < MIN_CALL_SECS:
        raise LlmError("turn deadline reached")
    if deps.budget.left() <= 0:
        raise LlmError("LLM call budget for this turn is spent")
    deps.budget.take()
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


async def is_off_topic(deps: TurnDeps, text: str, has_result: bool) -> bool:
    """One scope call. Fails closed: an unreadable or failed answer counts as off topic."""
    def valid(data: Any) -> dict | None:
        verdict = str(data.get("verdict") or "") if isinstance(data, dict) else ""
        return {"verdict": verdict} if verdict in (prompts.OFF_TOPIC, "IN_SCOPE") else None

    try:
        data = await complete_json(deps, prompts.scope_messages(text, has_result), prompts.SCOPE_CONTRACT, valid)
    except LlmError as e:
        log.warning("scope call failed (%s); declining", e)
        return True
    return data["verdict"] == prompts.OFF_TOPIC
