"""LLM plumbing. `ask_once` is one tool-less call; `run_step` is one read step (email or documents): a
bounded tool loop over that source's read tools, then the answer parsed as JSON. A step that hits its
loop limit, or answers in prose, gets one tool-less repair call over this step's tool output."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import prompts
from checks import Verbatim, parse_answer
from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpTool
from readonly import ReadOnlyGateway
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("hiring_loop.steps")

LOOP_CALLS = 5  # per source, plus one repair call
_PAUSED = ("approval_pending", "authorization_required")

Status = Literal["ok", "no_tool", "skipped", "no_tools", "paused", "unreadable"]


@dataclass(frozen=True)
class StepResult:
    source: str
    status: Status
    answer: Mapping[str, Any] = field(default_factory=dict)
    seen: Verbatim = field(default_factory=lambda: Verbatim(""))
    partial: bool = False  # hit the step limit: salvaged, may be incomplete
    offered: tuple[McpTool, ...] = ()
    dropped: Mapping[str, str] = field(default_factory=dict)
    cut: int = 0
    redacted: int = 0


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, system: str, user: str) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


async def ask_json(deps: TurnDeps, system: str, user: str) -> dict | None:
    return parse_answer(await ask_once(deps, system, user))


async def run_step(deps: TurnDeps, source: str, tools: Sequence[McpTool], writes: frozenset[str], system: str,
                   query: str) -> StepResult:
    if not tools:
        return StepResult(source, "no_tools")
    if deps.past_deadline():
        log.info("%s step skipped: past the turn deadline", source)
        return StepResult(source, "skipped", offered=tuple(tools))
    fenced = FencedGateway(ReadOnlyGateway(deps.gateway, writes))
    health = ToolHealth(fenced)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result = await run_tool_loop(deps.llm, deps.model, messages, tuple(tools), health, deps.traceparent,
                                 max_iterations=LOOP_CALLS, is_offered=health.is_offered)
    done = {"offered": tuple(tools), "dropped": health.dropped, "cut": fenced.cut, "redacted": fenced.redacted}
    if result.status in _PAUSED:
        return StepResult(source, "paused", **done)
    hit_limit = result.status == "max_iterations"
    answer = None if hit_limit else parse_answer(result.text)
    if answer is None and (fenced.seen_text or not hit_limit):
        answer = await _repair(deps, system, query, result, fenced.seen_text)
    seen = Verbatim(fenced.seen_text)
    if answer is None:
        return StepResult(source, "unreadable", seen=seen, partial=hit_limit, **done)
    status = "no_tool" if answer.get("status") == "no_tool" else "ok"
    log.info("%s step: %s, %d tool output chars", source, status, len(fenced.seen_text))
    return StepResult(source, status, answer, seen, hit_limit, **done)


async def _repair(deps: TurnDeps, system: str, query: str, result: LoopResult, seen_text: str) -> dict | None:
    log.info("step answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen_text) if seen_text else "(no tool results this turn)"
    user = (f"{query}\n\nTool results from this step:\n{evidence}\n\nYour previous answer:\n"
            f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}")
    return await ask_json(deps, system, user)
