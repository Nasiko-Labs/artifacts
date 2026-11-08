"""The model calls: `ask_once` (one tool-less call) and `run_step` (a bounded read-only tool loop over one
source's tools, whose JSON answer gets one tool-less repair call when it hits the loop limit or answers in
prose). Every call carries this request's traceparent."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import prompts
from checks import parse_answer
from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpTool
from readonly import ReadOnlyGateway
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("board_deck.steps")

LLM_TIMEOUT_SECS = 60.0
_PAUSED = ("approval_pending", "authorization_required")


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1,
                                 timeout=LLM_TIMEOUT_SECS)
    return result.text.strip()


@dataclass(frozen=True)
class StepResult:
    status: str  # ok | off_topic | no_tool | not_found | paused | unreadable | no_tools | skipped
    answer: Mapping[str, Any] = field(default_factory=dict)
    results: tuple[tuple[str, str], ...] = ()
    partial: bool = False
    dropped: Mapping[str, str] = field(default_factory=dict)

    @property
    def seen_text(self) -> str:
        return "\n".join(body for _, body in self.results)


async def run_step(deps: TurnDeps, tools: Sequence[McpTool], writes: frozenset[str], system: str, query: str,
                   max_calls: int) -> StepResult:
    if not tools:
        return StepResult("no_tools")
    if deps.past_deadline():
        return StepResult("skipped")
    fenced = FencedGateway(ReadOnlyGateway(deps.gateway, writes))
    health = ToolHealth(fenced)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result = await run_tool_loop(deps.llm, deps.model, messages, tuple(tools), health, deps.traceparent,
                                 max_iterations=max_calls, is_offered=health.is_offered, timeout=LLM_TIMEOUT_SECS)
    done = {"results": fenced.results, "dropped": health.dropped}
    if result.status in _PAUSED:
        return StepResult("paused", **done)
    hit_limit = result.status == "max_iterations"
    answer = None if hit_limit else parse_answer(result.text)
    if answer is None and (fenced.results or not hit_limit):
        answer = await _repair(deps, system, query, result, fenced.seen_text)
    if answer is None:
        return StepResult("unreadable", partial=hit_limit, **done)
    status = str(answer.get("status") or "ok")
    status = status if status in ("off_topic", "no_tool", "not_found") else "ok"
    log.info("step finished %s with %d tool results", status, len(fenced.results))
    return StepResult(status, answer, partial=hit_limit, **done)


async def _repair(deps: TurnDeps, system: str, query: str, result: LoopResult, seen_text: str) -> dict | None:
    log.info("step answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen_text[:24_000]) if seen_text else "(no tool results this turn)"
    messages = (
        {"role": "system", "content": system},
        {"role": "user", "content": query},
        {"role": "user", "content": f"Tool results from this step:\n{evidence}\n\nYour previous answer:\n"
                                    f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    )
    return parse_answer(await ask_once(deps, messages))
