"""One turn's read side: the per-step tool loops, bounded in LLM calls and wall-clock time.

Each step discovers its own tools, runs a tool loop through a `RecordingGateway` (raw output into the
turn's ledger, fenced copies to the model) in front of the turn's shared `ToolHealth`, and reports what
went wrong as coverage notes. Code reads the facts out of the ledger afterwards; a step that hits its
call or time limit keeps whatever its tools already returned.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from deps import TurnDeps
from discovery import STEP_SOURCE, Discoverer, Step
from fence import Ledger, RecordingGateway
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("ci_detective.reader")

TURN_DEADLINE_SECS = 330.0
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
class StepRun:
    step: str
    text: str = ""           # the model's final answer ("" when stopped early)
    status: str = "completed"
    tools: tuple[str, ...] = ()
    note: str = ""           # a coverage line when the step could not do its job


class TurnReader:
    """Shared across one turn's steps: the discoverer, the ledger, the tool-health tracker, the deadline."""

    def __init__(self, deps: TurnDeps, discoverer: Discoverer, clock=time.monotonic):
        self.deps = deps
        self.discoverer = discoverer
        self.ledger = Ledger()
        self.health = ToolHealth(deps.gateway)
        self._clock = clock
        self._started = clock()

    def remaining(self) -> float:
        return TURN_DEADLINE_SECS - (self._clock() - self._started)

    async def run(self, step: Step, system: str, user: str, max_calls: int, max_secs: float) -> StepRun:
        budget = min(max_secs, self.remaining())
        if budget <= 0:
            return StepRun(step, status="deadline", note=f"Skipped the {step} step: out of time for this turn.")
        catalog = await self.discoverer.catalog(step)
        tools = catalog.read_tools()
        log.info("step %s offered: %s", step, [t.name for t in tools])
        if not tools:
            return StepRun(step, status="no_tools", note=_no_tool_note(step))
        return await self._loop(step, tools, system, user, max_calls, budget)

    async def _loop(self, step: Step, tools: tuple[McpTool, ...], system: str, user: str, max_calls: int,
                    budget: float) -> StepRun:
        gateway = RecordingGateway(self.health, self.ledger, step)
        messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
        names = tuple(t.name for t in tools)
        try:
            result = await asyncio.wait_for(
                run_tool_loop(self.deps.llm, self.deps.model, messages, tools, gateway, self.deps.traceparent,
                              max_iterations=max_calls, is_offered=self.health.is_offered), timeout=budget)
        except TimeoutError:
            log.warning("step %s hit its %.0fs time limit", step, budget)
            return StepRun(step, status="timeout", tools=names,
                           note=f"The {step} step ran out of time; this uses what it had fetched.")
        if result.status in _PAUSED:
            return StepRun(step, status="paused", tools=names, note=prompts.READ_NEEDS_APPROVAL)
        dropped = {n: r for n, r in self.health.dropped.items() if n in names}
        note = _dropped_note(step, dropped) if dropped else ""
        if result.status == "max_iterations":
            log.info("step %s hit its call limit; keeping what its tools returned", step)
            return StepRun(step, status="max_iterations", tools=names, note=note)
        return StepRun(step, text=result.text, tools=names, note=note)


def _no_tool_note(step: str) -> str:
    return (f"No {STEP_SOURCE.get(step, 'CI')} tool for the {step} step is available to this agent: check that "
            "the code-host connector is enabled for it.")


def _dropped_note(step: str, dropped: Mapping[str, str]) -> str:
    reasons = ", ".join(dict.fromkeys(dropped.values()))
    return f"A {STEP_SOURCE.get(step, 'CI')} capability for the {step} step isn't available for your connection ({reasons})."
