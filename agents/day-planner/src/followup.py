"""A follow-up on a plan already shown in this conversation. One small LLM call routes the message to a
sentinel; the answer itself is computed in code from the stored plan, with zero tool calls. A message
that names another day, new working hours or pasted meetings goes straight back to planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time, timedelta, tzinfo

import gaps as g
import parse
import paste
import prompts
import render
import smalltalk
import timewin
from deps import TurnDeps
from llm import ask_once
from models import Gap, Plan


@dataclass(frozen=True)
class Answer:
    reply: str = ""
    replan: bool = False


async def answer(deps: TurnDeps, query: str, plan: Plan) -> Answer:
    request = parse.parse_request(query)
    if request.names_day or request.hours or paste.looks_pasted(query):
        return Answer(replan=True)
    if not deps.traceparent:
        return Answer(prompts.NO_TRACE_CONTEXT)
    route = (await ask_once(deps, [{"role": "system", "content": prompts.FOLLOWUP_SYSTEM},
                                   {"role": "user", "content": query}])).strip().upper()
    zone = timewin.Zone.from_dict(plan.zone) or timewin.Zone(timewin.zone_of("UTC"), "UTC", "pasted")
    if route.startswith(prompts.OFF_TOPIC):
        return Answer(smalltalk.OFF_TOPIC_REPLY)
    if route.startswith(prompts.OTHER_DAY):
        return Answer(replan=True)
    if route.startswith(prompts.FIND_GAP):
        return Answer(find_gap(plan, zone.tz, parse.min_gap(query), parse.part_of_day(query)))
    if route.startswith(prompts.DETAIL):
        return Answer(_detail(plan, zone.tz, route[len(prompts.DETAIL):]))
    if route.startswith(prompts.BOOK):
        return Answer(copyable_block(plan, zone.tz, zone.name))
    return Answer(prompts.UNREADABLE)


def find_gap(plan: Plan, tz: tzinfo, minutes: int, part) -> str:
    """Stored gaps of at least `minutes`, narrowed to the part of the day asked about. Code only."""
    found: list[tuple[str, Gap]] = []
    for day in plan.days:
        hours = (time.fromisoformat(day.hours[0]), time.fromisoformat(day.hours[1]))
        start, end = g.hours_window(day.day, hours, part, tz)
        for gap in day.gaps:
            clipped = Gap(max(gap.start, start), min(gap.end, end))
            if clipped.end - clipped.start >= timedelta(minutes=minutes):
                found.append((timewin.day_label(day.day), clipped))
    want = timewin.duration(minutes)
    if not found:
        return f"No: there's no free block of {want} in that part of the day in the plan I showed you."
    lines = "\n".join(f"- {label}: {timewin.clock(gap.start, tz)}-{timewin.clock(gap.end, tz)} "
                      f"({timewin.duration(gap.minutes)})" for label, gap in found)
    return f"Yes, you have {len(found)} free block(s) of at least {want}:\n{lines}"


def _detail(plan: Plan, tz: tzinfo, number: str) -> str:
    events = render.numbered(plan)
    digits = "".join(ch for ch in number if ch.isdigit())
    n = int(digits) if digits else 0
    match = next((e for i, e in events if i == n), None)
    if match is None:
        return f"There's no meeting #{n or '?'} in the plan; the plan lists #1 to #{len(events)}."
    return render.event_detail(match, n, tz)


def copyable_block(plan: Plan, tz: tzinfo, zone_name: str) -> str:
    """Read-only: the best focus block as text the user can paste into their own calendar."""
    day = next((d for d in plan.days if d.best is not None), None)
    if day is None:
        return "I don't create or change calendar events, and the plan has no free block to suggest."
    best = day.best
    text = (f"Focus time\n{timewin.day_label(day.day)}, {timewin.clock(best.start, tz)}-{timewin.clock(best.end, tz)} "
            f"({zone_name})")
    return ("I don't create or change calendar events. Here is your best focus block to add yourself:\n\n"
            f"```\n{text}\n```")
