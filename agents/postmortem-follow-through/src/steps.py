"""Bounded read steps: a tool loop with an LLM-call cap and a wall-clock cap, whose JSON answer is
salvaged by one tool-less repair call (the step's last call) when the loop stops early or answers in
prose. Every step has its own fence, so its records are its own; tool health is shared by the turn."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import step_prompts
from corpus import Corpus
from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpTool
from prompts import fence
from records import load_json_object
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("postmortem.steps")

_PAUSED = ("approval_pending", "authorization_required")
_REPAIR_EVIDENCE_CHARS = 30_000


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the kit loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


class Deadline:
    """The turn's hard deadline; each step gets min(its own cap, what is left)."""

    def __init__(self, seconds: float):
        self._end = time.monotonic() + seconds

    def budget(self, cap: float) -> float:
        return max(0.0, min(cap, self._end - time.monotonic()))


@dataclass(frozen=True)
class StepResult:
    data: dict | None
    corpus: Corpus
    fenced: FencedGateway
    hit_limit: bool = False
    paused: bool = False


async def run_step(deps: TurnDeps, tools: Sequence[McpTool], health: ToolHealth, system: str, user: str,
                   calls: int, budget: float) -> StepResult:
    """`calls` LLM calls at most (the last one reserved for a repair), within `budget` seconds."""
    fenced = FencedGateway(health)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
    loop_secs = max(budget - deps.limits.repair_secs, budget / 2)
    result = None
    if budget > 0:
        try:
            result = await asyncio.wait_for(
                run_tool_loop(deps.llm, deps.model, messages, tuple(tools), fenced, deps.traceparent,
                              max_iterations=max(1, calls - 1), is_offered=health.is_offered), loop_secs)
        except TimeoutError:
            log.info("step timed out after %.0fs; salvaging what the tools returned", loop_secs)
    if result is not None and result.status in _PAUSED:
        return StepResult(None, fenced.corpus(), fenced, paused=True)
    hit_limit = result is None or result.status == "max_iterations"
    data = load_json_object(result.text) if result is not None and not hit_limit else None
    if data is None and (fenced.records or not hit_limit) and budget > 0:
        data = await _repair(deps, messages, result.text if result else "", fenced)
    return StepResult(data, fenced.corpus(), fenced, hit_limit)


async def _repair(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], answer: str,
                  fenced: FencedGateway) -> dict | None:
    log.info("step answer was not the agreed JSON; asking once for a rewrite")
    evidence = "\n".join(r.body for r in fenced.records)[:_REPAIR_EVIDENCE_CHARS]
    repair = (*messages, {"role": "user", "content": (
        f"Tool results from this step:\n{fence(evidence) if evidence else '(none)'}\n\n"
        f"Your previous answer:\n{fence(answer)}\n\n{step_prompts.REPAIR_INSTRUCTION}")})
    try:
        return load_json_object(await asyncio.wait_for(ask_once(deps, repair), deps.limits.repair_secs))
    except TimeoutError:
        log.info("repair call timed out")
        return None
