"""One turn's read side, shared by the tickets and bugs steps: the fence adapter that records tool output
(`seen_text`, for the checks), the failure tracker in front of it, a per-step tool-call cap, and a bounded
JSON step with one tool-less repair call. The repair also salvages a step that hit its step limit."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

import prompts
import replies
from budget import TurnLimitReached
from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpTool, ToolOk, ToolResult
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("support_digest.reader")

_PAUSED = ("approval_pending", "authorization_required")
T = TypeVar("T")
_MAX_REPAIR_EVIDENCE = 200_000  # chars of the newest tool output the repair call sees


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


class CappedCaller:
    """Answers with a note instead of forwarding once a step has spent its tool calls."""

    def __init__(self, inner: Any, most: int):
        self._inner = inner
        self._left = most
        self.capped = False

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if self._left <= 0:
            self.capped = True
            note = "The tool-call budget for this step is spent. Answer now with what you have."
            return ToolOk(content=({"type": "text", "text": json.dumps({"error": note})},), is_error=True, raw={})
        self._left -= 1
        return await self._inner.call_tool(name, arguments, traceparent)


@dataclass(frozen=True)
class StepRun:
    """`status`: completed | max_iterations | paused | limit (the turn's deadline or call cap)."""

    parsed: Any
    status: str
    notes: tuple[str, ...] = ()
    salvaged: bool = False
    capped: bool = False


class TurnReader:
    """Per turn, never persisted. All read steps share one record of tool output and one failure tracker."""

    def __init__(self, deps: TurnDeps):
        self.fenced = FencedGateway(deps.gateway)
        self.health = ToolHealth(self.fenced)

    @property
    def seen_text(self) -> str:
        return self.fenced.seen_text

    async def run_json(self, deps: TurnDeps, tools: Sequence[McpTool], system: str, user: str, loop_calls: int,
                       tool_calls: int, parse: Callable[[str], T | None]) -> StepRun:
        messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
        caller = CappedCaller(self.health, tool_calls)
        try:
            result = await run_tool_loop(deps.llm, deps.model, messages, tools, caller, deps.traceparent,
                                         max_iterations=loop_calls, is_offered=self.health.is_offered)
        except TurnLimitReached:
            log.info("turn limit reached during a read step")
            return StepRun(None, "limit", (replies.STEP_LIMIT_NOTE,), capped=caller.capped)
        if result.status in _PAUSED:
            return StepRun(None, "paused")
        hit_limit = result.status == "max_iterations"
        parsed = None if hit_limit else parse(result.text)
        salvaged = False
        if parsed is None and (self.seen_text or not hit_limit):
            parsed = await self._repair(deps, messages, result.text, parse)
            salvaged = hit_limit and parsed is not None
        notes = (replies.STEP_LIMIT_NOTE,) if hit_limit else ()
        return StepRun(parsed, result.status, notes, salvaged, caller.capped)

    async def _repair(self, deps: TurnDeps, messages: Sequence[dict], previous: str,
                      parse: Callable[[str], T | None]) -> T | None:
        """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
        log.info("step answer was not the agreed JSON (or hit its limit); asking once for a rewrite")
        seen = self.seen_text[-_MAX_REPAIR_EVIDENCE:]
        evidence = prompts.fence(seen) if seen else "(no tool results this turn)"
        repair = {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                             f"{prompts.fence(previous)}\n\n{prompts.REPAIR_INSTRUCTION}"}
        try:
            return parse(await ask_once(deps, (*messages, repair)))
        except TurnLimitReached:
            return None
