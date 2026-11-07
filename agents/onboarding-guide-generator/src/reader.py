"""One turn's read side: the discovered catalog, the fence adapter that records tool output, the call
budget, and the failure tracker in front of them (shared by every read step, so a tool dropped in one
step stays dropped). Also the tool-less single call (`ask_once`) every other LLM call goes through.

Budgets per turn (PLAN.md "Turn flow"): at most 50 tool calls reach the gateway (listing 6, files 14,
ticket 3, locate 6, prs 6, kb 4) and at most 5 tool-less LLM calls (intake, keywords, welcome,
lessons, one spare)."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import coverage
from deps import TurnDeps
from discovery import RECOVER_META, Catalog
from fence import FencedGateway
from nasiko_mcp import McpTool, ToolOk, ToolResult
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("onboarding.reader")

HELPERS = frozenset({RECOVER_META})
MAX_TOOL_CALLS = 50
MAX_EXTRA_LLM_CALLS = 5
STEP_SOURCE = {"listing": "code", "files": "code", "ticket": "tracker", "locate": "code", "prs": "code",
               "kb": "docs"}
STEP_TOOL_CALLS = {"listing": 6, "files": 14, "ticket": 3, "locate": 6, "prs": 6, "kb": 4}
STEP_LLM_ITERATIONS = {"listing": 3, "files": 3, "ticket": 2, "locate": 3, "prs": 2, "kb": 2}
_BUDGET_NOTE = "The tool-call budget for this step is used up. Do not call any tool again; reply DONE."


class BudgetExceeded(RuntimeError):
    """A tool-less LLM call beyond the turn's budget was refused."""


class TurnBudget:
    """Counts this turn's gateway tool calls (per step and in all) and its tool-less LLM calls."""

    def __init__(self, max_tools: int = MAX_TOOL_CALLS, max_llm: int = MAX_EXTRA_LLM_CALLS):
        self._max_tools = max_tools
        self._max_llm = max_llm
        self._tools = 0
        self._llm = 0
        self._step = ""
        self._step_used = 0

    @property
    def tool_calls(self) -> int:
        return self._tools

    @property
    def llm_calls(self) -> int:
        return self._llm

    def start_step(self, step: str) -> None:
        self._step, self._step_used = step, 0

    def take_tool(self) -> bool:
        limit = STEP_TOOL_CALLS.get(self._step, MAX_TOOL_CALLS)
        if self._tools >= self._max_tools or self._step_used >= limit:
            return False
        self._tools, self._step_used = self._tools + 1, self._step_used + 1
        return True

    def take_llm(self) -> bool:
        if self._llm >= self._max_llm:
            return False
        self._llm += 1
        return True


class BudgetedGateway:
    """Forwards a call only while the budget allows; otherwise answers with a note telling the model to stop."""

    def __init__(self, inner: Any, budget: TurnBudget):
        self._inner = inner
        self._budget = budget

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if not self._budget.take_tool():
            log.info("tool budget used up; refused %s", name)
            return ToolOk(content=({"type": "text", "text": json.dumps({"error": _BUDGET_NOTE})},), is_error=False,
                          raw={})
        return await self._inner.call_tool(name, arguments, traceparent)


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], budget: TurnBudget) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    if not budget.take_llm():
        raise BudgetExceeded("tool-less LLM call budget used up")
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1,
                                 temperature=0)
    return result.text.strip()


@dataclass(frozen=True)
class Reader:
    catalog: Catalog
    fenced: FencedGateway
    health: ToolHealth
    budget: TurnBudget

    @staticmethod
    def over(deps: TurnDeps, catalog: Catalog, budget: TurnBudget) -> Reader:
        fenced = FencedGateway(deps.gateway)
        return Reader(catalog, fenced, ToolHealth(BudgetedGateway(fenced, budget)), budget)

    def tools(self, step: str, extra_source: str | None = None) -> tuple[McpTool, ...]:
        own = self.catalog.read_tools(STEP_SOURCE[step])
        if extra_source is None:
            return own
        names = {t.name for t in own}
        return (*own, *(t for t in self.catalog.read_tools(extra_source) if t.name not in names))

    def has_source(self, source: str) -> bool:
        return any(self.catalog.placed(t) == source for t in self.catalog.read_tools(source)
                   if t.name not in HELPERS)

    def status(self, source: str) -> coverage.SourceStatus:
        statuses = coverage.assess(self.catalog.read_tools(), self.catalog.placed, self.health.dropped, HELPERS)
        return statuses[source]

    async def loop(self, deps: TurnDeps, step: str, messages: Sequence[Mapping[str, Any]],
                   tools: Sequence[McpTool]) -> LoopResult:
        self.budget.start_step(step)
        return await run_tool_loop(deps.llm, deps.model, messages, tools, self.health, deps.traceparent,
                                   max_iterations=STEP_LLM_ITERATIONS[step], is_offered=self.health.is_offered,
                                   temperature=0)
