"""One turn's read side: the discovered catalog, the fence adapter that records tool output, and the
failure tracker in front of it (shared by every read step, so a tool dropped in one step stays dropped).
Also the tool-less single call (`ask_once`) every other LLM call goes through."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import coverage
from deps import TurnDeps
from discovery import RECOVER_META, Catalog
from fence import FencedGateway
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

HELPERS = frozenset({RECOVER_META})


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


@dataclass(frozen=True)
class Reader:
    catalog: Catalog
    fenced: FencedGateway
    health: ToolHealth

    @staticmethod
    def over(deps: TurnDeps, catalog: Catalog) -> Reader:
        fenced = FencedGateway(deps.gateway)
        return Reader(catalog, fenced, ToolHealth(fenced))

    def tools(self, source: str) -> tuple[McpTool, ...]:
        return self.catalog.read_tools(source)

    def status(self, source: str) -> coverage.SourceStatus:
        statuses = coverage.assess(self.catalog.read_tools(), self.catalog.placed, self.health.dropped, HELPERS)
        return statuses[source]

    async def loop(self, deps: TurnDeps, messages: Sequence[Mapping[str, Any]], tools: Sequence[McpTool],
                   max_iterations: int) -> LoopResult:
        return await run_tool_loop(deps.llm, deps.model, messages, tools, self.health, deps.traceparent,
                                   max_iterations=max_iterations, is_offered=self.health.is_offered)
