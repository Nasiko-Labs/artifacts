"""The read side of one turn: the bounded tool loop over read-only tools, and the tool-less single
call. Every call goes through the kit loop, so every LLM request carries the inbound traceparent."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

PAUSED = ("approval_pending", "authorization_required")


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


async def read_loop(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], tools: Sequence[McpTool],
                    health: ToolHealth, max_iterations: int) -> tuple[LoopResult, FencedGateway]:
    """A bounded loop over `tools` only. Output is fenced and recorded per call site (so one meeting's
    evidence is checked against that meeting's tool output); `health` is shared by the whole turn, so a
    tool found dead for one meeting is not offered for the next."""
    fenced = FencedGateway(health)
    result = await run_tool_loop(deps.llm, deps.model, messages, tools, fenced, deps.traceparent,
                                 max_iterations=max_iterations, is_offered=health.is_offered)
    return result, fenced
