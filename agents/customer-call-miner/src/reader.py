"""One step's read side: the tools offered to the model, the fence adapter that records tool output, and
the failure tracker in front of it. Also the tool-less single LLM call every step uses."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import coverage
from deps import TurnDeps
from discovery import RECOVER_META, Catalog
from fence import FencedGateway, Recorded
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("call_miner.reader")


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
    source: str
    tools: tuple[McpTool, ...]
    fenced: FencedGateway
    health: ToolHealth

    @staticmethod
    def for_catalog(deps: TurnDeps, catalog: Catalog) -> Reader:
        tools = catalog.read_tools()
        log.info("%s read tools offered: %s", catalog.source, [t.name for t in tools])
        fenced = FencedGateway(deps.gateway)
        return Reader(catalog.source, tools, fenced, ToolHealth(fenced))

    async def loop(self, deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_iterations: int,
                   tools: Sequence[McpTool] | None = None) -> LoopResult:
        return await run_tool_loop(deps.llm, deps.model, messages, tuple(self.tools if tools is None else tools),
                                   self.health, deps.traceparent, max_iterations=max_iterations,
                                   is_offered=self.health.is_offered)

    async def call(self, deps: TurnDeps, name: str, arguments: Mapping[str, Any]) -> None:
        """A code-made call (replay) through the same health and fence layers; failures are recorded there."""
        try:
            await self.health.call_tool(name, arguments, deps.traceparent)
        except Exception as e:  # noqa: BLE001 - one failed replay must not end the step
            log.warning("replayed call to %s failed: %s", name, type(e).__name__)

    @property
    def records(self) -> tuple[Recorded, ...]:
        return self.fenced.records

    @property
    def seen_text(self) -> str:
        return self.fenced.seen_text

    def status(self) -> coverage.SourceStatus:
        return coverage.assess(self.source, self.tools, self.health.dropped, frozenset({RECOVER_META}))
