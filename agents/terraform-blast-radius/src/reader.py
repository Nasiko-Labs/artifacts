"""One turn's read side: the per-step tool loops, bounded in LLM calls and wall-clock time.

Each step discovers its own tools, runs a tool loop through a `RecordingGateway` (raw output into the
turn's ledger, fenced copies to the model) in front of the turn's shared `ToolHealth`, and reports what
went wrong as coverage notes. Code reads the facts out of the ledger afterwards; a step that hits its
call or time limit keeps whatever its tools already returned (no "ran out of steps" reply, no repair).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass

import prompts
from deps import TurnDeps
from discovery import Discoverer, Step
from fence import Ledger, RecordingGateway
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("terraform_blast_radius.reader")

_PAUSED = ("approval_pending", "authorization_required")
_WHAT = {"search": "code search", "files": "file contents", "owners": "commit history"}


@dataclass(frozen=True)
class StepRun:
    step: str
    status: str = "completed"   # completed | no_tools | deadline | timeout | paused | max_iterations | budget
    note: str = ""              # a coverage line when the step could not do its whole job


class TurnReader:
    """Shared across one turn's steps: the discoverer, the ledger, the tool-health tracker."""

    def __init__(self, deps: TurnDeps, discoverer: Discoverer):
        self.deps = deps
        self.discoverer = discoverer
        self.ledger = Ledger()
        self.health = ToolHealth(deps.gateway)

    async def run(self, step: Step, system: str, max_calls: int, max_secs: float) -> StepRun:
        budget = min(max_secs, self.deps.remaining())
        calls = min(max_calls, self.deps.budget.left())
        if budget <= 0:
            return StepRun(step, "deadline", f"{_WHAT[step]} skipped: out of time for this turn")
        if calls <= 0:
            return StepRun(step, "budget", f"{_WHAT[step]} skipped: the model-call budget for this turn is spent")
        catalog = await self.discoverer.catalog(step)
        tools = catalog.read_tools()
        log.info("step %s offered: %s", step, [t.name for t in tools])
        if not tools:
            note = (prompts.NO_CODE_HOST if step == "search"
                    else f"{_WHAT[step]}: no code-host tool for it is available to this agent")
            return StepRun(step, "no_tools", note)
        return await self._loop(step, tools, system, calls, budget)

    async def _loop(self, step: Step, tools: tuple[McpTool, ...], system: str, calls: int,
                    budget: float) -> StepRun:
        gateway = RecordingGateway(self.health, self.ledger, step)
        messages = ({"role": "system", "content": system}, {"role": "user", "content": "Go."})
        names = tuple(t.name for t in tools)
        try:
            result = await asyncio.wait_for(
                run_tool_loop(self.deps.llm, self.deps.model, messages, tools, gateway, self.deps.traceparent,
                              max_iterations=calls, is_offered=self.health.is_offered), timeout=budget)
        except TimeoutError:
            self.deps.budget.take(calls)
            log.warning("step %s hit its %.0fs time limit", step, budget)
            return StepRun(step, "timeout", f"{_WHAT[step]} ran out of time; this uses what it had fetched")
        self.deps.budget.take(sum(1 for m in result.messages if m.get("role") == "assistant"))
        dropped = {n: r for n, r in self.health.dropped.items() if n in names}
        note = _dropped_note(step, dropped) if dropped else ""
        if result.status in _PAUSED:
            return StepRun(step, "paused", prompts.READ_NEEDS_APPROVAL)
        if result.status == "max_iterations":
            log.info("step %s hit its call limit; keeping what its tools returned", step)
            return StepRun(step, "max_iterations", note or f"{_WHAT[step]} stopped at its call limit; partial")
        return StepRun(step, "completed", note)


def _dropped_note(step: str, dropped: Mapping[str, str]) -> str:
    reasons = ", ".join(dict.fromkeys(dropped.values()))
    return f"{_WHAT[step]} is not available for your connection ({reasons})"
