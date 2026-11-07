"""One turn's read side, shared by the commitments, tracker and chat steps: the fence adapter that
records tool output, the failure tracker in front of it, and a bounded tool loop whose JSON answer is
repaired once (from this turn's fenced tool output) when it is unreadable or the loop hit its limit."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import prompts
from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("commitment_audit.reader")

T = TypeVar("T")
_PAUSED = ("approval_pending", "authorization_required")


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


@dataclass(frozen=True)
class StepResult(Generic[T]):
    parsed: T | None
    hit_limit: bool = False
    paused: bool = False


class Reader:
    def __init__(self, deps: TurnDeps):
        self.fenced = FencedGateway(deps.gateway)
        self.health = ToolHealth(self.fenced)

    @property
    def seen_text(self) -> str:
        return self.fenced.seen_text

    async def run(self, deps: TurnDeps, system: str, user: str, tools: Sequence[McpTool], loop_calls: int,
                  parse: Callable[[str], T | None]) -> StepResult[T]:
        """At most `loop_calls` LLM calls in the loop, plus one repair call."""
        messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
        result = await run_tool_loop(deps.llm, deps.model, messages, tools, self.health, deps.traceparent,
                                     max_iterations=loop_calls, is_offered=self.health.is_offered)
        if result.status in _PAUSED:
            return StepResult(None, paused=True)
        hit_limit = result.status == "max_iterations"
        parsed = None if hit_limit else parse(result.text)
        if parsed is None and (self.seen_text or not hit_limit):
            log.info("step answer was not the agreed JSON (limit hit: %s); asking once for a rewrite", hit_limit)
            parsed = parse(await self._repair(deps, system, user, "" if hit_limit else result.text))
        return StepResult(parsed, hit_limit)

    async def _repair(self, deps: TurnDeps, system: str, user: str, previous: str) -> str:
        evidence = prompts.fence(self.seen_text) if self.seen_text else "(no tool results this turn)"
        earlier = f"\n\nYour previous answer:\n{prompts.fence(previous)}" if previous else ""
        messages = (
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "user", "content": f"Tool results from this turn:\n{evidence}{earlier}\n\n"
                                        f"{prompts.REPAIR_INSTRUCTION}"},
        )
        return await ask_once(deps, messages)
