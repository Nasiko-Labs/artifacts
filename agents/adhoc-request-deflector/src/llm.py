"""Tool-less model calls through the kit loop, so every call carries the inbound traceparent the same
way, and the JSON object in an answer read leniently (code fences and prose around it tolerated)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from deps import TurnDeps
from tool_loop import run_tool_loop

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], **kwargs: Any) -> str:
    deps.count_llm_call()
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1,
                                 **kwargs)
    return result.text.strip()


def parse_answer(text: str) -> dict | None:
    body = _FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
