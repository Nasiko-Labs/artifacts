"""One read step: a bounded tool loop over the chat read tools, then the answer parsed as JSON. A step
that hits its loop limit, or answers in prose, gets one tool-less repair call that turns this step's
tool output into the JSON (still checked in code). Also the tool-less `ask_once` used for every other
model call, so the traceparent travels the same way everywhere."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import prompts
from deps import TurnDeps
from fence import CallBudget, CallRecord, FencedGateway
from nasiko_mcp import McpTool
from readonly import ReadOnlyGateway
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("catchup.steps")

_PAUSED = ("approval_pending", "authorization_required")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")

Status = Literal["ok", "off_topic", "skipped", "no_tools", "paused", "unreadable"]


@dataclass(frozen=True)
class StepResult:
    status: Status
    answer: Mapping[str, Any] = field(default_factory=dict)
    calls: tuple[CallRecord, ...] = ()
    partial: bool = False  # hit the loop limit: salvaged, may be incomplete
    dropped: Mapping[str, str] = field(default_factory=dict)

    @property
    def seen_text(self) -> str:
        return "\n".join(c.text for c in self.calls if not c.is_error)


@dataclass(frozen=True)
class StepSpec:
    system: str
    query: str
    iterations: int
    call_limit: int
    wants_json: bool = True  # False: the answer is just "done"; the tool output is what counts
    drop_unavailable: bool = True


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


def parse_answer(text: str) -> dict | None:
    """The JSON object in a model answer (code fences and surrounding prose tolerated), or None."""
    body = _FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def run_step(deps: TurnDeps, tools: Sequence[McpTool], writes: frozenset[str], spec: StepSpec) -> StepResult:
    if not tools:
        return StepResult("no_tools")
    if deps.past_deadline():
        log.info("step skipped: past the turn deadline")
        return StepResult("skipped")
    fenced = FencedGateway(ReadOnlyGateway(deps.gateway, writes))
    health = ToolHealth(CallBudget(fenced, spec.call_limit), drop_unavailable=spec.drop_unavailable)
    messages = ({"role": "system", "content": spec.system}, {"role": "user", "content": spec.query})
    result = await run_tool_loop(deps.llm, deps.model, messages, tuple(tools), health, deps.traceparent,
                                 max_iterations=spec.iterations, is_offered=health.is_offered)
    done = {"calls": fenced.calls, "dropped": health.dropped}
    if result.status in _PAUSED:
        return StepResult("paused", **done)
    hit_limit = result.status == "max_iterations"
    if not spec.wants_json:
        return StepResult("ok", partial=hit_limit, **done)
    answer = None if hit_limit else parse_answer(result.text)
    if answer is None and (fenced.seen_text or not hit_limit):
        answer = await _repair(deps, spec, result, fenced.seen_text)
    if answer is None:
        return StepResult("unreadable", partial=hit_limit, **done)
    status: Status = "off_topic" if answer.get("status") == "off_topic" else "ok"
    log.info("step: %s, %d calls", status, len(fenced.calls))
    return StepResult(status, answer, partial=hit_limit, **done)


async def _repair(deps: TurnDeps, spec: StepSpec, result: LoopResult, seen_text: str) -> dict | None:
    log.info("step answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen_text) if seen_text else "(no tool results this turn)"
    messages = (
        {"role": "system", "content": spec.system},
        {"role": "user", "content": spec.query},
        {"role": "user", "content": f"Tool results from this step:\n{evidence}\n\nYour previous answer:\n"
                                    f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    )
    return parse_answer(await ask_once(deps, messages))
