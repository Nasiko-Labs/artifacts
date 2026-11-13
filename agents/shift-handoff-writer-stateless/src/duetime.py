"""Pure: a fixed grammar for times. "Now" is the container clock unless the user's instruction says
`handoff at 18:00` or `as of 2026-11-04 17:30`; a due text parses to a moment relative to that, or to None
(shown as written, never guessed).

Grammar: `today`, `tonight`, `EOD`/`end of day`, `tomorrow [<time>]`, `<time>` (`17:30`, `5pm`, `5:30 pm`,
`at 4`), `noon`, ISO `2026-11-05 [<time>]`, each optionally after `by`/`at`/`before`/`due`. A bare hour 1-7
means the afternoon (a support shift's day); 8-11 the morning."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta

HOT_WINDOW = timedelta(hours=12)
EOD = time(18, 0)
END_OF_DAY = time(23, 59)
TONIGHT = time(21, 0)
NOON = time(12, 0)
AFTERNOON_HOURS = range(1, 8)

_CLOCK = r"(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?m?\.?"
_TIME = re.compile(rf"^{_CLOCK}$", re.IGNORECASE)
_LEAD = re.compile(r"^(?:due\s+)?(?:by|at|before|until|around|~)?\s*", re.IGNORECASE)
_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T](.+))?$")
_TOMORROW = re.compile(r"^tomorrow(?:\s+(?:at\s+|by\s+|morning\b|am\b)?\s*(.*))?$", re.IGNORECASE)
_AS_OF = re.compile(r"\b(?:as of|handoff at|hand-off at|hand over at|handover at|at)\s+"
                    rf"(\d{{4}}-\d{{2}}-\d{{2}}[ T])?({_CLOCK})", re.IGNORECASE)
_NEXT_SHIFT = re.compile(rf"\bnext shift (?:starts?|begins?)(?: at)?\s+({_CLOCK})", re.IGNORECASE)
_MENTION = re.compile(r"\b(?:at|by|before|around)\s+(\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?)(?![\d:/-])"
                      r"|\b(\d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?|\d{1,2}\s*[ap]\.?m\.?)\b", re.IGNORECASE)


def clock_time(text: str) -> time | None:
    match = _TIME.match((text or "").strip())
    if not match:
        return None
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), (match.group(3) or "").casefold()
    if half == "p" and hour < 12:
        hour += 12
    elif half == "a" and hour == 12:
        hour = 0
    elif not half and not match.group(2) and hour in AFTERNOON_HOURS:
        hour += 12
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def handoff_time(instruction: str, now: datetime) -> datetime:
    """The handoff moment: the instruction's `as of`/`handoff at` time on today's date, else now."""
    match = _AS_OF.search(instruction or "")
    if not match:
        return now
    at = clock_time(match.group(2))
    if at is None:
        return now
    day = now.date()
    if match.group(1):
        try:
            day = datetime.strptime(match.group(1).strip(), "%Y-%m-%d").date()
        except ValueError:
            return now
    return datetime.combine(day, at)


def next_shift(instruction: str) -> str | None:
    match = _NEXT_SHIFT.search(instruction or "")
    return match.group(1).strip() if match else None


def parse_due(text: str | None, now: datetime) -> datetime | None:
    """The moment a due text names, or None when the grammar does not cover it."""
    body = _LEAD.sub("", " ".join((text or "").casefold().split())).strip(" .")
    if not body:
        return None
    named = {"today": END_OF_DAY, "end of today": END_OF_DAY, "tonight": TONIGHT, "eod": EOD,
             "end of day": EOD, "cob": EOD, "close of business": EOD, "noon": NOON, "midday": NOON}
    if body in named:
        return datetime.combine(now.date(), named[body])
    tomorrow = _TOMORROW.match(body)
    if tomorrow:
        rest = (tomorrow.group(1) or "").strip()
        at = clock_time(_LEAD.sub("", rest)) if rest else END_OF_DAY
        return datetime.combine(now.date() + timedelta(days=1), at) if at else None
    iso = _ISO.match(body)
    if iso:
        try:
            day = datetime.strptime(iso.group(1), "%Y-%m-%d").date()
        except ValueError:
            return None
        at = clock_time(_LEAD.sub("", iso.group(2))) if iso.group(2) else END_OF_DAY
        return datetime.combine(day, at) if at else None
    at = clock_time(body)
    return datetime.combine(now.date(), at) if at else None


def within_window(moment: datetime, now: datetime) -> bool:
    """Due within the next 12 hours of the handoff, or already past."""
    return moment <= now + HOT_WINDOW


def time_mention(text: str) -> str | None:
    """A time as written in a sentence ("at 4", "16:00", "by 5pm"), or None."""
    match = _MENTION.search(text or "")
    if not match:
        return None
    return (match.group(1) or match.group(2)).strip()


def hhmm(moment: datetime) -> str:
    return moment.strftime("%H:%M")
