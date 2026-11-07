"""One turn's read side, shared by the doc, team and duplicate steps: the fence adapter that records tool
output (`seen_text`, for the checks), the failure tracker in front of it, and a bounded JSON step with one
tool-less repair call. The repair also salvages a step that hit its step limit."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

import prompts
import replies
from budget import TurnLimitReached
from deps import TurnDeps
from fence import FencedGateway, SeenCall
from llmcall import ask_once
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("spec_breaker.reader")

_PAUSED = ("approval_pending", "authorization_required")
T = TypeVar("T")


@dataclass(frozen=True)
class StepRun:
    """`status`: completed | max_iterations | paused | limit (the turn's deadline or call cap)."""

    parsed: Any
    status: str
    notes: tuple[str, ...] = ()
    salvaged: bool = False


class TurnReader:
    """Per turn, never persisted. All read steps share one record of tool output and one failure tracker."""

    def __init__(self, deps: TurnDeps):
        self.fenced = FencedGateway(deps.gateway)
        self.health = ToolHealth(self.fenced)

    @property
    def seen_text(self) -> str:
        return self.fenced.seen_text

    @property
    def calls(self) -> tuple[SeenCall, ...]:
        return self.fenced.calls

    async def run_json(self, deps: TurnDeps, tools: Sequence[McpTool], system: str, user: str, loop_calls: int,
                       parse: Callable[[str], T | None]) -> StepRun:
        messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
        try:
            result = await run_tool_loop(deps.llm, deps.model, messages, tools, self.health, deps.traceparent,
                                         max_iterations=loop_calls, is_offered=self.health.is_offered)
        except TurnLimitReached:
            log.info("turn limit reached during a read step")
            return StepRun(None, "limit", (replies.STEP_LIMIT_NOTE,))
        if result.status in _PAUSED:
            return StepRun(None, "paused")
        hit_limit = result.status == "max_iterations"
        parsed = None if hit_limit else parse(result.text)
        salvaged = False
        if parsed is None and (self.seen_text or not hit_limit):
            parsed = await self._repair(deps, messages, result.text, parse)
            salvaged = parsed is not None
        return StepRun(parsed, result.status, (replies.STEP_LIMIT_NOTE,) if hit_limit else (), salvaged)

    async def _repair(self, deps: TurnDeps, messages: tuple, previous: str, parse: Callable[[str], T | None]) -> T | None:
        """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
        log.info("step answer was not the agreed JSON (or hit its limit); asking once for a rewrite")
        evidence = prompts.fence(self.seen_text[-12_000:]) if self.seen_text else "(no tool results this turn)"
        repair = {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                             f"{prompts.fence(previous)}\n\n{prompts.REPAIR_INSTRUCTION}"}
        try:
            return parse(await ask_once(deps, (*messages, repair)))
        except TurnLimitReached:
            return None
