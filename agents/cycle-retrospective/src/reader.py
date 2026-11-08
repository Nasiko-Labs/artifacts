"""One turn's read side, shared by the cycle, issues and evidence steps: the fence adapter that records tool
output (`seen_text`, for the checks), the failure tracker in front of it, and a bounded JSON step with
one tool-less repair call. The repair also salvages a step that hit its step limit."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

import prompts
import replies
from budget import ToolCallCap, TurnLimitReached
from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("retro.reader")

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
    notes: tuple[str, ...] = ()


class TurnReader:
    """Per turn, never persisted. All read steps share one record of tool output and one failure tracker."""

    def __init__(self, deps: TurnDeps):
        self.fenced = FencedGateway(deps.gateway)
        self.health = ToolHealth(self.fenced)
        self.capped = ToolCallCap(self.health, deps.config.turn_tool_calls)

    @property
    def seen_text(self) -> str:
        return self.fenced.seen_text

    async def run_json(self, deps: TurnDeps, tools: Sequence[McpTool], system: str, user: str, loop_calls: int,
                       parse: Callable[[str], T | None], tool_calls: int = 20) -> StepRun:
        messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
        self.capped.start_step(tool_calls)
        try:
            result = await run_tool_loop(deps.llm, deps.model, messages, tools, self.capped, deps.traceparent,
                                         max_iterations=loop_calls, is_offered=self.health.is_offered)
        except TurnLimitReached:
            log.info("turn limit reached during a read step")
            return StepRun(None, "limit", (replies.STEP_LIMIT_NOTE,))
        if result.status in _PAUSED:
            return StepRun(None, "paused")
        hit_limit = result.status == "max_iterations"
        parsed = None if hit_limit else parse(result.text)
        if parsed is None and (self.seen_text or not hit_limit):
            parsed = await self._repair(deps, messages, result.text, parse)
        return StepRun(parsed, result.status, (replies.STEP_LIMIT_NOTE,) if hit_limit else ())

    async def _repair(self, deps: TurnDeps, messages: tuple, previous: str, parse: Callable[[str], T | None]) -> T | None:
        """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
        log.info("step answer was not the agreed JSON (or hit its limit); asking once for a rewrite")
        evidence = prompts.fence(self.seen_text) if self.seen_text else "(no tool results this turn)"
        repair = {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                             f"{prompts.fence(previous)}\n\n{prompts.REPAIR_INSTRUCTION}"}
        try:
            return parse(await ask_once(deps, (*messages, repair)))
        except TurnLimitReached:
            return None
