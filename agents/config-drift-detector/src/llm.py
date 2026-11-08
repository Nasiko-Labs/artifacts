"""Tool-less LLM calls through the kit loop (so each carries the inbound traceparent the same way), with
JSON parsing and one repair call."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import prompts
from deps import TurnDeps
from tool_loop import run_tool_loop

log = logging.getLogger("drift.llm")


class NoTools:
    """The gateway for tool-less calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


def load_object(text: str) -> dict | None:
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    for candidate in (body, body[body.find("{"):body.rfind("}") + 1] if "{" in body else ""):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


async def ask_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> dict | None:
    """One call, plus one repair call when the reply is not a JSON object. None when both fail."""
    first = await ask_once(deps, messages)
    data = load_object(first)
    if data is not None:
        return data
    log.info("reply was not a JSON object; asking once for a repair")
    repair = (*messages, {"role": "assistant", "content": first}, {"role": "user", "content": prompts.REPAIR})
    return load_object(await ask_once(deps, repair))
