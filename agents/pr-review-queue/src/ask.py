"""One tool-less model call through the kit loop, so it carries the traceparent like every other call."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from deps import TurnDeps
from tool_loop import run_tool_loop


class NoTools:
    """The gateway for tool-less calls; the loop never forwards when no tool is offered."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, system: str, user: str) -> str:
    messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()
