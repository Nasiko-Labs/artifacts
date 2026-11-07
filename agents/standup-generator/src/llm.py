"""One tool-less LLM call, sent through the same loop as the read steps so it carries the inbound
traceparent the same way (header + bound span context)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from deps import TurnDeps
from tool_loop import run_tool_loop


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()
