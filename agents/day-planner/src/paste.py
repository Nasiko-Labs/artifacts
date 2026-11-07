"""Pure: the fallback when no calendar can be read. Meetings the user pasted, one per line
("09:30-10:00 Standup - Ana, Bo"), parsed in code; the same gap logic then runs on them."""

from __future__ import annotations

import re
from datetime import date, datetime, time, tzinfo

import prompts
from models import RawEvent

MAX_LINES = 60
_CLOCK = r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
_LINE = re.compile(rf"^\s*(?:[-*•]\s*)?({_CLOCK})\s*(?:-|–|—|to)\s*({_CLOCK})\s+(.+?)\s*$", re.IGNORECASE)
_PARTS = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE)


def looks_pasted(text: str) -> bool:
    return any(_LINE.match(line) for line in (text or "").splitlines())


def parse(text: str, day: date, tz: tzinfo) -> tuple[RawEvent, ...]:
    events = []
    for n, line in enumerate((text or "").splitlines()[:MAX_LINES]):
        match = _LINE.match(prompts.strip_markers(line))
        if not match:
            continue
        start, end = _clock(match.group(1)), _clock(match.group(2))
        if start is None or end is None:
            continue
        if end <= start and end.hour < 12 and "m" not in match.group(2).casefold():
            end = time(end.hour + 12, end.minute)  # "1:30-2" after noon
        title, people = _split(match.group(3))
        events.append(RawEvent(id=f"pasted-{n + 1}", title=title, start=_iso(day, start, tz), end=_iso(day, end, tz),
                               attendees=people))
    return tuple(events)


def _split(rest: str) -> tuple[str, tuple[str, ...]]:
    title, _, who = rest.partition(" - ")
    people = tuple(p.strip() for p in re.split(r",|;| and ", who) if p.strip())
    return title.strip() or "(no title)", people


def _clock(raw: str) -> time | None:
    match = _PARTS.fullmatch(raw.strip())
    if not match:
        return None
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half.casefold() == "pm" else 0)
    return time(hour, minute) if hour <= 23 and minute <= 59 else None


def _iso(day: date, clock: time, tz: tzinfo) -> str:
    return datetime.combine(day, clock, tzinfo=tz).isoformat()
