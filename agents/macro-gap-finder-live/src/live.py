"""The live desk fetch: discover -> tickets tool loop -> macros tool loop -> verified replies and macros.

Bounded: tickets <= 8 LLM calls + 1 repair and <= 70 tool calls; macros <= 3 + 1 and <= 5 tool calls; the
whole fetch stops in time to leave the draft step its window. Read-only: discovery never offers a write.
The model's answers are verified in desk.py; coverage is decided from calls, never from the model's prose."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import OpenAIError

import coverage
import desk
import live_prompts as lp
from discovery import RECOVER_META, Catalog, discover, fits
from fence import FencedGateway
from llm import DRAFT_RESERVE_SECS, TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk
from parse_replies import Reply
from prompts import fence
from request import LiveAsk
from tool_health import ToolHealth, failure_text
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("macro_gap_finder_live.live")

TICKETS_LOOP_CALLS, TICKETS_TOOL_CALLS = 8, 70
MACROS_LOOP_CALLS, MACROS_TOOL_CALLS = 3, 5
LOOP_MAX_TOKENS = 12_000
DROPPED_LOW_SHARE = 0.20
MODEL_FAILED = "I couldn't reach the language model to read your desk. Please try again."
UNREADABLE = ("I read your support desk but couldn't turn what it returned into a list of replies. Try a "
              "narrower window, for example \"macro candidates from the last 7 days\".")
_HELPERS = frozenset({RECOVER_META})


@dataclass(frozen=True)
class Fetched:
    reply: str | None                       # a fixed reply that ends the turn
    replies: tuple[Reply, ...] = ()
    macros: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    urls: dict = field(default_factory=dict)
    tickets: int = 0
    cap_level: str = ""                     # "", "medium" or "low": the live caps on confidence


class Calls:
    """Counts one step's tool calls, refuses past its budget, and remembers which tools returned data."""

    def __init__(self, inner: Any, limit: int):
        self._inner, self._limit, self.count, self.succeeded = inner, limit, 0, frozenset()

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        if self.count >= self._limit:
            return ToolOk(content=({"type": "text", "text": lp.TOOL_BUDGET_NOTE},), is_error=True, raw={})
        self.count += 1
        result = await self._inner.call_tool(name, arguments, traceparent)
        if isinstance(result, ToolOk) and failure_text(result) is None:
            self.succeeded = self.succeeded | {name}
        return result


class _NoTools:
    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def _loop(deps: TurnDeps, tools: tuple[McpTool, ...], caller: Any, health: ToolHealth, system: str,
                request: str, max_iterations: int) -> LoopResult | None:
    """The loop's result, or None when the turn's read window closed first."""
    messages = ({"role": "system", "content": system}, {"role": "user", "content": request})
    window = max(1.0, deps.remaining() - DRAFT_RESERVE_SECS * 2)
    try:
        return await asyncio.wait_for(run_tool_loop(
            deps.llm, deps.model, messages, tools, caller, deps.traceparent, max_iterations=max_iterations,
            is_offered=health.is_offered, max_tokens=LOOP_MAX_TOKENS), timeout=window)
    except TimeoutError:
        log.warning("a desk read hit the turn's read window")
        return None


async def _answer(deps: TurnDeps, result: LoopResult | None, system: str, request: str, seen_text: str,
                  parse: Callable[[str], Any]) -> tuple[Any, bool]:
    """(parsed answer or None, salvaged): one tool-less repair call when the answer is not the contract."""
    salvaged = result is None or result.status != "completed"
    parsed = None if salvaged else parse(result.text)
    if parsed is None and (seen_text or not salvaged):
        messages = lp.repair_messages(system, request, seen_text, result.text if result else "")
        repaired = await run_tool_loop(deps.llm, deps.model, messages, (), _NoTools(), deps.traceparent,
                                       max_iterations=1, max_tokens=LOOP_MAX_TOKENS)
        parsed = parse(repaired.text)
    return parsed, salvaged


async def fetch(deps: TurnDeps, ask: LiveAsk) -> Fetched:
    catalog, failure = await discover(deps)
    if failure is not None:
        return Fetched(failure)
    try:
        return await _read(deps, ask, catalog)
    except McpGatewayError as e:
        log.warning("desk read failed at the gateway: %s", type(e).__name__)
        return Fetched(lp.GATEWAY_UNREACHABLE)
    except (OpenAIError, httpx.HTTPError) as e:  # the model provider: one fixed reply, never a guess
        log.warning("desk read failed: %s", type(e).__name__)
        return Fetched(MODEL_FAILED)


async def _read(deps: TurnDeps, ask: LiveAsk, catalog: Catalog) -> Fetched:
    fenced_seen = FencedGateway(deps.gateway)  # this turn's tool output
    health = ToolHealth(fenced_seen)
    request = fence("USER_REQUEST", ask.text)
    tools = catalog.tools_for("tickets")
    calls = Calls(health, TICKETS_TOOL_CALLS)
    system = lp.tickets_system(deps.today, ask.days, ask.topic)
    result = await _loop(deps, tools, calls, health, system, request, TICKETS_LOOP_CALLS)
    answer, salvaged = await _answer(deps, result, system, request, fenced_seen.seen_text, desk.parse_tickets)
    said_none = answer is not None and answer.status == "no_tool"
    status = coverage.assess("tickets", tools, health.dropped, calls.succeeded, said_none, _HELPERS)
    if not status.read:
        return Fetched(f"{lp.NO_DESK_PASTE_HINT}\n\n- {coverage.line(status)}")
    if answer is None:
        return Fetched(UNREADABLE)
    verified = desk.verify_tickets(answer, fenced_seen.seen_text, deps.today, ask.days, lp.MAX_TICKETS)
    macros, macro_notes, macros_read = await _macros(deps, catalog, health, request, fenced_seen)
    notes = [*macro_notes]
    if verified.capped:
        notes.append(lp.CAP_NOTE)
    if salvaged:
        notes.append(lp.STEP_LIMIT_NOTE)
    share = verified.dropped / verified.extracted if verified.extracted else 0.0
    level = "low" if salvaged or share > DROPPED_LOW_SHARE else (
        "medium" if verified.capped or not macros_read or verified.dropped or macro_notes else "")
    return Fetched(None, verified.replies, macros, tuple(notes), verified.urls, len(verified.tickets), level)


async def _macros(deps: TurnDeps, catalog: Catalog, health: ToolHealth, request: str,
                  fenced: FencedGateway) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    tools = catalog.tools_for("macros")
    tools = tools if fits(tools, "macros") else ()
    calls = Calls(health, MACROS_TOOL_CALLS)
    system = lp.macros_system()
    result = await _loop(deps, tools, calls, health, system, request, MACROS_LOOP_CALLS) if tools else None
    answer, _ = await _answer(deps, result, system, request, fenced.seen_text, desk.parse_macros) if result else (None, True)
    said_none = answer is None or answer.status == "no_tool"
    status = coverage.assess("macros", tools, health.dropped, calls.succeeded, said_none, _HELPERS)
    if not status.read:
        return (), (lp.NO_MACROS_NOTE,), False
    macros, titles_only = desk.verify_macros(answer, fenced.seen_text, lp.MAX_MACROS)
    return macros, ((lp.MACRO_TITLES_NOTE,) if titles_only else ()), True
