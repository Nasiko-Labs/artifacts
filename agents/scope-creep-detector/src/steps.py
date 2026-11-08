"""One read step (teams, sprint, issues or history): a bounded tool loop over the tracker's read tools,
then the answer parsed as JSON. A step that hits its loop limit, or answers in prose, gets one tool-less
repair call that turns this step's tool output into the JSON (still checked in code)."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import prompts
from checks import Seen, parse_answer
from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpTool
from readonly import ReadOnlyGateway
from replay import ReplayCaller
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("scope_creep.steps")

LOOP_CALLS = {"teams": 3, "sprint": 4, "issues": 5, "history": 6}  # plus one repair call each
# The history step reads every issue's history in one replayed result; allow it more room.
RESULT_CHARS = {"history": 60_000}
DEFAULT_RESULT_CHARS = 16_000
REPAIR_EVIDENCE_CHARS = 60_000
_PAUSED = ("approval_pending", "authorization_required")

Status = Literal["ok", "off_topic", "no_tool", "skipped", "no_tools", "paused", "unreadable"]


@dataclass(frozen=True)
class StepResult:
    step: str
    status: Status
    answer: Mapping[str, Any] = field(default_factory=dict)
    seen: Seen = field(default_factory=lambda: Seen(""))
    partial: bool = False  # hit the step limit: salvaged, may be incomplete
    dropped: Mapping[str, str] = field(default_factory=dict)


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


async def run_step(deps: TurnDeps, step: str, tools: Sequence[McpTool], writes: frozenset[str], system: str,
                   query: str, replay_keys: Sequence[str] = ()) -> StepResult:
    if not tools:
        return StepResult(step, "no_tools")
    if deps.past_deadline():
        log.info("%s step skipped: past the turn deadline", step)
        return StepResult(step, "skipped")
    fenced = FencedGateway(ReadOnlyGateway(deps.gateway, writes))
    health = ToolHealth(ReplayCaller(fenced, replay_keys))
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result = await run_tool_loop(deps.llm, deps.model, messages, tuple(tools), health, deps.traceparent,
                                 max_iterations=LOOP_CALLS[step], is_offered=health.is_offered,
                                 max_result_chars=RESULT_CHARS.get(step, DEFAULT_RESULT_CHARS))
    dropped = health.dropped
    if result.status in _PAUSED:
        return StepResult(step, "paused", dropped=dropped)
    hit_limit = result.status == "max_iterations"
    answer = None if hit_limit else parse_answer(result.text)
    if answer is None and (fenced.seen_text or not hit_limit):
        answer = await _repair(deps, system, query, result, fenced.seen_text)
    seen = Seen(fenced.seen_text)
    if answer is None:
        return StepResult(step, "unreadable", seen=seen, partial=hit_limit, dropped=dropped)
    status = answer.get("status") if answer.get("status") in ("off_topic", "no_tool") else "ok"
    log.info("%s step: %s, %d tool output chars", step, status, len(seen.text))
    return StepResult(step, status, answer, seen, hit_limit, dropped)


async def _repair(deps: TurnDeps, system: str, query: str, result: LoopResult, seen_text: str) -> dict | None:
    log.info("step answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen_text[:REPAIR_EVIDENCE_CHARS]) if seen_text else "(no tool results this turn)"
    messages = (
        {"role": "system", "content": system},
        {"role": "user", "content": query},
        {"role": "user", "content": f"Tool results from this step:\n{evidence}\n\nYour previous answer:\n"
                                    f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    )
    return parse_answer(await ask_once(deps, messages))
