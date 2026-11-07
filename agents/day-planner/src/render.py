"""Pure: the markdown reply, rendered in code. No raw HTML, no images; links only from tool output."""

from __future__ import annotations

import re
from datetime import tzinfo

import prompts
import timewin
from events import attendees_shown
from models import DayPlan, Event, Gap, Plan

_NEWLINES = re.compile(r"[\r\n]+")
_MARKDOWN = re.compile(r"[`*_\[\]|]")
_LINK = re.compile(r"^https?://\S+$")
_FLAG_TEXT = {"back_to_back": "back-to-back", "long": "long", "external": "external",
              "needs_response": "needs response", "tentative": "tentative", "no_description": ""}


def safe(text: str) -> str:
    """One line of tool text, with characters that could break the markdown or inject HTML neutralised."""
    flat = _NEWLINES.sub(" ", text or "").replace("<", "").replace(">", "")
    return _MARKDOWN.sub(lambda m: "\\" + m.group(0), flat).strip()


def numbered(plan: Plan) -> list[tuple[int, Event]]:
    """Events across the whole plan, numbered from 1 in order: what "#2" refers to."""
    return list(enumerate((e for d in plan.days for e in d.events), start=1))


def render_plan(plan: Plan, tz: tzinfo) -> str:
    numbers = {id(e): n for n, e in numbered(plan)}
    blocks = [_day(day, tz, numbers) for day in plan.days]
    return "\n\n".join([*blocks, _footer(plan)])


def headline(day: DayPlan, tz: tzinfo) -> str:
    s = day.stats
    noun = "meeting" if s.meetings == 1 else "meetings"
    best = f" (best focus block {_span(day.best, tz)})" if day.best else ""
    return (f"{timewin.day_label(day.day)}: {s.meetings} {noun}, {timewin.duration(s.meeting_minutes)} booked, "
            f"{timewin.duration(s.free_minutes)} free{best}")


def _day(day: DayPlan, tz: tzinfo, numbers: dict) -> str:
    lines = [f"**{headline(day, tz)}**", ""]
    for kind, item in _agenda(day):
        lines.append(_event_line(item, tz, numbers[id(item)]) if kind == "event" else
                     f"- *free {timewin.duration(item.minutes)} ({_span(item, tz)})*")
    if not day.events:
        lines.append("- No meetings in this window.")
    if day.all_day:
        lines.append("")
        lines.append("All day: " + "; ".join(safe(a.title) for a in day.all_day))
    extras = _day_notes(day)
    return "\n".join([*lines, *([""] + extras if extras else [])])


def _agenda(day: DayPlan) -> list[tuple[str, Event | Gap]]:
    """Events and gaps interleaved by start time (an event before a gap that starts at the same time)."""
    items = [("event", e) for e in day.events] + [("gap", g) for g in day.gaps]
    return sorted(items, key=lambda kv: (kv[1].start, 0 if kv[0] == "event" else 1))


def _event_line(event: Event, tz: tzinfo, n: int) -> str:
    raw = event.raw
    parts = [f"- **{timewin.clock(event.start, tz)}-{timewin.clock(event.end, tz)}** #{n} {safe(raw.title)}"]
    people = len(raw.attendees)
    if people:
        parts.append(f"{people} {'person' if people == 1 else 'people'}")
    parts.append(f"\"{safe(event.context)}\"" if event.context else
                 "no description" if not raw.description else "no context in description")
    parts.extend(text for flag in event.flags if (text := _FLAG_TEXT.get(flag, "")))
    parts.extend(f"double-booked: {safe(note)}" for note in event.notes)
    if raw.location:
        parts.append(f"at {safe(raw.location)}")
    if _LINK.match(raw.join_link):
        parts.append(f"[join]({raw.join_link})")
    return " · ".join(parts)


def _day_notes(day: DayPlan) -> list[str]:
    s = day.stats
    notes = []
    if s.double_bookings:
        notes.append(f"Double bookings: {s.double_bookings}.")
    if s.back_to_back_runs:
        notes.append(f"Back-to-back runs: {s.back_to_back_runs}.")
    if s.declined_dropped:
        notes.append(f"Hidden: {s.declined_dropped} event(s) you declined.")
    return [" ".join(notes)] if notes else []


def _footer(plan: Plan) -> str:
    zone = plan.zone.get("name") or "UTC"
    hours = plan.days[0].hours if plan.days else ("09:00", "18:00")
    where = "as pasted" if plan.pasted else f"in {zone}"
    lines = [f"Times {where}; gaps inside working hours {hours[0]}-{hours[1]}.",
             "Coverage: " + (" ".join(plan.coverage) if plan.coverage else "all calendars read.")]
    if plan.context_dropped:
        lines.append(f"{plan.context_dropped} context line(s) left out: I couldn't match them to the event text.")
    lines.append(prompts.READ_ONLY_FOOTER)
    return "\n".join(lines)


def _span(gap: Gap | Event, tz: tzinfo) -> str:
    return f"{timewin.clock(gap.start, tz)}-{timewin.clock(gap.end, tz)}"


def event_detail(event: Event, n: int, tz: tzinfo) -> str:
    raw = event.raw
    day = timewin.day_label(event.start.astimezone(tz).date())
    lines = [f"**#{n} {safe(raw.title)}**, {day} {_span(event, tz)} ({timewin.duration(event.minutes)})"]
    if raw.attendees:
        lines.append("Attendees: " + ", ".join(safe(a) for a in attendees_shown(raw)))
    if raw.organizer:
        lines.append(f"Organizer: {safe(raw.organizer)}")
    lines.append(f"Status: {event.status.replace('_', ' ')}")
    if raw.location:
        lines.append(f"Location: {safe(raw.location)}")
    for label, link in (("Join", raw.join_link), ("Event", raw.link)):
        if _LINK.match(link):
            lines.append(f"{label}: {link}")
    lines.append(f"Context: {safe(event.context)}" if event.context else
                 "Context: no description" if not raw.description else "Context: not stated in the description")
    if event.notes:
        lines.append("Double-booked: " + "; ".join(safe(n) for n in event.notes))
    return "\n".join(f"- {line}" if i else line for i, line in enumerate(lines))
