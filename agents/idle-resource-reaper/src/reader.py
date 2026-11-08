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

    def owner_tools(self) -> tuple[McpTool, ...]:
        """Read tools whose own name or description places them on the code host, chat or email (search
        provenance is not evidence: a semantic search returns its nearest tools even when none fits)."""
        placed = tuple(t for t in self.catalog.read_tools() if t.name not in HELPERS and self.catalog.placed(t))
        return (*placed, self.catalog.recover) if placed and self.catalog.recover else placed

    def statuses(self) -> dict[str, coverage.SourceStatus]:
        return coverage.assess(self.owner_tools(), self.catalog.placed, self.health.dropped, HELPERS)

    async def loop(self, deps: TurnDeps, messages: Sequence[Mapping[str, Any]], tools: Sequence[McpTool],
                   max_iterations: int) -> LoopResult:
        return await run_tool_loop(deps.llm, deps.model, messages, tools, self.health, deps.traceparent,
                                   max_iterations=max_iterations, is_offered=self.health.is_offered)
