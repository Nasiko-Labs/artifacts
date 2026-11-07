"""The calendar step: a bounded tool loop over calendar read tools, its answer post-checked in code.

At most CALENDAR_LOOP_CALLS LLM calls in the loop plus one tool-less repair (or step-limit salvage)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import events as ev
import prep_prompts as prompts
import smalltalk
import timewin
from deps import TurnDeps
from discovery import RECOVER_META, Catalog
from nasiko_mcp import McpTool
from reader import PAUSED, ask_once, read_loop
from tool_health import ToolHealth
from tool_loop import LoopResult
from verbatim import Seen

log = logging.getLogger("meeting_prep.calendar")

CALENDAR_LOOP_CALLS = 4


@dataclass(frozen=True)
class CalendarOutcome:
    """Either a fixed reply that ends the turn, or the verified events and what the timezone rests on."""

    reply: str | None
    events: tuple[ev.Event, ...] = ()
    total: int = 0
    me: str = ""
    calendar_tz: str = ""
    calendar_tz_seen: bool = False


def calendar_tools(catalog: Catalog) -> tuple[McpTool, ...]:
    return catalog.read_tools(("calendar",))


async def read_calendar(deps: TurnDeps, catalog: Catalog, health: ToolHealth, query: str,
                        window: timewin.Window) -> CalendarOutcome:
    tools = calendar_tools(catalog)
    if not tools:
        return CalendarOutcome(prompts.NO_CALENDAR_TOOL)
    system = prompts.calendar_system(window)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result, fenced = await read_loop(deps, messages, tools, health, CALENDAR_LOOP_CALLS)
    dead = _dead_reason(tools, health)
    if dead is not None:
        return CalendarOutcome(prompts.calendar_unavailable(dead))
    if result.status in PAUSED:
        return CalendarOutcome(prompts.READ_NEEDS_APPROVAL)
    hit_limit = result.status == "max_iterations"
    parsed = None if hit_limit else ev.parse_calendar(result.text)
    if parsed is None and (fenced.seen_text or not hit_limit):
        parsed = await _repair(deps, system, query, result, fenced.seen_text, hit_limit)
    return _checked(parsed, Seen(fenced.seen_text))


def _dead_reason(tools: Sequence[McpTool], health: ToolHealth) -> str | None:
    """The reason(s) when every calendar tool was dropped as unavailable this turn, else None."""
    real = [t for t in tools if t.name != RECOVER_META]
    dropped = health.dropped
    if not real or any(t.name not in dropped for t in real):
        return None
    return ", ".join(dict.fromkeys(dropped[t.name] for t in real))


def _checked(parsed: ev.CalendarAnswer | None, seen: Seen) -> CalendarOutcome:
    if parsed is None:
        return CalendarOutcome(prompts.UNREADABLE_CALENDAR)
    if parsed.status == "off_topic":
        return CalendarOutcome(smalltalk.OFF_TOPIC_REPLY)
    if parsed.status == "no_calendar_tool":
        return CalendarOutcome(prompts.NO_CALENDAR_TOOL)
    kept = ev.verified(parsed, seen)
    if len(kept) < len(parsed.events):
        log.info("calendar post-check dropped %d event(s) no tool returned", len(parsed.events) - len(kept))
    return CalendarOutcome(None, kept, len(parsed.events), parsed.me if seen.has(parsed.me) else "",
                           parsed.calendar_timezone, seen.has(parsed.calendar_timezone))


async def _repair(deps: TurnDeps, system: str, query: str, result: LoopResult, seen_text: str,
                  hit_limit: bool) -> ev.CalendarAnswer | None:
    """One tool-less call that restates the answer as JSON from this step's (fenced) tool output."""
    log.info("calendar answer %s; one tool-less rewrite", "hit the step limit" if hit_limit else "was not the JSON")
    evidence = prompts.fence(seen_text) if seen_text else "(no tool results this turn)"
    instruction = prompts.SALVAGE_INSTRUCTION if hit_limit else prompts.REPAIR_INSTRUCTION
    messages = (
        {"role": "system", "content": system},
        {"role": "user", "content": query},
        {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                    f"{prompts.fence(result.text)}\n\n{instruction}"},
    )
    return ev.parse_calendar(await ask_once(deps, messages))
