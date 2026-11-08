"""One tool-less LLM call through the kit loop, so it carries the inbound traceparent the same way the
tool loops do."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from deps import TurnDeps
from tool_loop import run_tool_loop

LLM_TIMEOUT_SECS = 45.0


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1,
                                 timeout=LLM_TIMEOUT_SECS)
    return result.text.strip()
