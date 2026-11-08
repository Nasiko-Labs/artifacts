"""One source's read side for a turn: the tools offered, the fence adapter that records their output
(`seen_text`, for the checks), the failure tracker in front of it, and a bounded JSON step with one tool-less
repair call (which also salvages a step that hit its limit). Plus the tool-less single call."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

import coverage
import prompts
import replies
from budget import TurnLimitReached
from deps import TurnDeps
from discovery import Catalog, Source
from fence import FencedGateway
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("issue_hygiene.reader")

_PAUSED = ("approval_pending", "authorization_required")
T = TypeVar("T")


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


@dataclass(frozen=True)
class StepRun:
    """`status`: completed | max_iterations | paused | limit (the turn's deadline or call cap)."""

    parsed: Any
    status: str
    salvaged: bool = False


class Reader:
    def __init__(self, deps: TurnDeps, source: Source, tools: Sequence[McpTool]):
        self.source = source
        self.tools = tuple(tools)
        self.fenced = FencedGateway(deps.gateway)
        self.health = ToolHealth(self.fenced)

    @property
    def seen_text(self) -> str:
        return self.fenced.seen_text

    @property
    def status(self) -> coverage.SourceStatus:
        return coverage.assess(self.source, self.tools, self.health.dropped)

    async def run_json(self, deps: TurnDeps, system: str, user: str, loop_calls: int,
                       parse: Callable[[str], T | None]) -> StepRun:
        messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
        try:
            result = await run_tool_loop(deps.llm, deps.model, messages, self.tools, self.health, deps.traceparent,
                                         max_iterations=loop_calls, is_offered=self.health.is_offered)
        except TurnLimitReached:
            log.info("turn limit reached during the %s step", self.source)
            return StepRun(None, "limit")
        if result.status in _PAUSED:
            return StepRun(None, "paused")
        hit_limit = result.status == "max_iterations"
        parsed = None if hit_limit else parse(result.text)
        if parsed is not None:
            return StepRun(parsed, result.status)
        if not (self.seen_text or not hit_limit):
            return StepRun(None, result.status)
        repaired = await self._repair(deps, messages, result.text, parse)
        return StepRun(repaired, result.status, salvaged=repaired is not None)

    async def _repair(self, deps: TurnDeps, messages: tuple, previous: str, parse: Callable[[str], T | None]) -> T | None:
        log.info("%s step answer was not the agreed JSON (or hit its limit); asking once for a rewrite", self.source)
        evidence = prompts.fence(self.seen_text) if self.seen_text else "(no tool results this turn)"
        repair = {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                             f"{prompts.fence(previous)}\n\n{prompts.REPAIR_INSTRUCTION}"}
        try:
            return parse(await ask_once(deps, (*messages, repair)))
        except TurnLimitReached:
            return None


def reader_for(deps: TurnDeps, catalog: Catalog, source: Source) -> Reader:
    return Reader(deps, source, catalog.read_tools(source))


def limit_note(run: StepRun) -> tuple[str, ...]:
    return (replies.STEP_LIMIT_NOTE,) if run.status in ("max_iterations", "limit") else ()
