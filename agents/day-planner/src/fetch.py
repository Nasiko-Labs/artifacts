"""The fetch step: the model picks the calendar tool(s) and fills in the window computed in code; code
then reads the events out of the tool output (events.py). The model never retypes an event.

Budget per turn: at most MAX_FETCH_ROUNDS LLM calls here and MAX_TOOL_CALLS gateway tool calls. A round
only follows when the previous one produced no events yet (for example it listed the calendars first)."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import events as ev
import prompts
import timewin
from deps import TurnDeps
from discovery import Catalog
from fence import FencedGateway
from llm import LLM_TIMEOUT_SECS
from nasiko_mcp import ToolOk, ToolResult
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("day_planner.fetch")

MAX_TOOL_CALLS = 4
MAX_FETCH_ROUNDS = 2
_PAUSED = ("approval_pending", "authorization_required")

Status = Literal["ok", "off_topic", "paused", "dead"]


class BudgetedCaller:
    """Forwards at most MAX_TOOL_CALLS tool calls per turn; later ones get a note, not the gateway."""

    def __init__(self, gateway: Any, limit: int = MAX_TOOL_CALLS):
        self._gateway = gateway
        self._limit = limit
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if self._used >= self._limit:
            note = json.dumps({"error": "The tool-call budget for this turn is used up. Reply DONE now."})
            return ToolOk(content=({"type": "text", "text": note},), is_error=True, raw={})
        self._used += 1
        return await self._gateway.call_tool(name, arguments, traceparent)


@dataclass(frozen=True)
class FetchResult:
    status: Status
    extracted: ev.Extracted = field(default_factory=lambda: ev.Extracted((), None))
    dropped: Mapping[str, str] = field(default_factory=dict)
    llm_calls: int = 0
    reason: str = ""


async def fetch(deps: TurnDeps, catalog: Catalog, window: timewin.Window, zone_name: str | None,
                query: str) -> FetchResult:
    budget = BudgetedCaller(deps.gateway)
    health = ToolHealth(budget)
    fenced = FencedGateway(health)
    tools = catalog.offered()
    messages: tuple = ({"role": "system", "content": prompts.fetch_system(window, zone_name)},
                       {"role": "user", "content": query})
    calls = 0
    for _ in range(MAX_FETCH_ROUNDS):
        result = await run_tool_loop(deps.llm, deps.model, messages, tools, fenced, deps.traceparent,
                                     max_iterations=1, is_offered=health.is_offered, timeout=LLM_TIMEOUT_SECS)
        calls += 1
        if result.status in _PAUSED:
            return FetchResult("paused", llm_calls=calls)
        if result.status == "completed":
            if result.text.strip().upper().startswith(prompts.OFF_TOPIC) and not fenced.bodies:
                return FetchResult("off_topic", llm_calls=calls)
            break
        if ev.extract(fenced.bodies).events or budget.used >= MAX_TOOL_CALLS:
            break
        messages = result.messages
    if not fenced.bodies:  # nothing usable came back: never shown as an empty day
        reason = ", ".join(dict.fromkeys(health.dropped.values())) or "no calendar data came back"
        return FetchResult("dead", dropped=health.dropped, llm_calls=calls, reason=reason)
    return FetchResult("ok", ev.extract(fenced.bodies), health.dropped, calls)
