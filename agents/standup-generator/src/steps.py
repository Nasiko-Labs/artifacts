"""One read step (code host or tracker): a bounded tool loop over that source's read tools, then the
answer parsed as JSON. A step that hits its loop limit, or answers in prose, gets one tool-less repair
call that turns this step's tool output into the JSON (still checked in code)."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import prompts
from checks import Seen, parse_answer
from deps import FETCH_DEADLINE_SECS, TurnDeps
from fence import FencedGateway
from llm import ask_once
from nasiko_mcp import McpTool
from readonly import ReadOnlyGateway
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("standup.steps")

LOOP_CALLS = {"code": 6, "tracker": 4}  # plus one repair call each: with intake and gist, <= 14 LLM calls
_PAUSED = ("approval_pending", "authorization_required")

Status = Literal["ok", "off_topic", "no_tool", "skipped", "no_tools", "paused", "unreadable"]


@dataclass(frozen=True)
class StepResult:
    source: str
    status: Status
    answer: Mapping[str, Any] = field(default_factory=dict)
    seen: Seen = field(default_factory=lambda: Seen(""))
    partial: bool = False  # hit the step limit: salvaged, may be incomplete
    offered: tuple[McpTool, ...] = ()
    dropped: Mapping[str, str] = field(default_factory=dict)


async def run_step(deps: TurnDeps, source: str, tools: Sequence[McpTool], writes: frozenset[str], system: str,
                   query: str) -> StepResult:
    if not tools:
        return StepResult(source, "no_tools")
    if deps.past(FETCH_DEADLINE_SECS):
        log.info("%s step skipped: past the fetch deadline", source)
        return StepResult(source, "skipped", offered=tuple(tools))
    fenced = FencedGateway(ReadOnlyGateway(deps.gateway, writes))
    health = ToolHealth(fenced)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": prompts.strip_markers(query)})
    result = await run_tool_loop(deps.llm, deps.model, messages, tuple(tools), health, deps.traceparent,
                                 max_iterations=LOOP_CALLS[source], is_offered=health.is_offered)
    done = {"offered": tuple(tools), "dropped": health.dropped}
    if result.status in _PAUSED:
        return StepResult(source, "paused", **done)
    hit_limit = result.status == "max_iterations"
    answer = None if hit_limit else parse_answer(result.text)
    if answer is None and (fenced.seen_text or not hit_limit):
        answer = await _repair(deps, system, query, result, fenced.seen_text)
    seen = Seen(fenced.seen_text)
    if answer is None:
        return StepResult(source, "unreadable", seen=seen, partial=hit_limit, **done)
    status = answer.get("status") if answer.get("status") in ("off_topic", "no_tool") else "ok"
    log.info("%s step: %s, %d tool output chars", source, status, len(seen.text))
    return StepResult(source, status, answer, seen, hit_limit, **done)


async def _repair(deps: TurnDeps, system: str, query: str, result: LoopResult, seen_text: str) -> dict | None:
    log.info("step answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen_text) if seen_text else "(no tool results this turn)"
    messages = (
        {"role": "system", "content": system},
        {"role": "user", "content": prompts.strip_markers(query)},
        {"role": "user", "content": f"Tool results from this step:\n{evidence}\n\nYour previous answer:\n"
                                    f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    )
    return parse_answer(await ask_once(deps, messages))
