"""Pure: what the user's message asks for, parsed in code. Window (clamped 7 to 56 days, default 28),
filters, an hourly rate, JSON mode, an optional notes folder link, an envelope from an earlier run, and
a pasted meeting list (the fallback when no calendar can be read)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

DEFAULT_WINDOW_DAYS = 28
MIN_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 56
MAX_PASTED_ROWS = 300

_WINDOW = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3}|one|two|three|four|five|six|seven|eight)\s+"
                     r"(day|days|week|weeks|month|months)\b", re.IGNORECASE)
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 28}
_ORGANIZE = re.compile(r"\b(?:i|I)\s+(?:organi[sz]e|own|run|host)\b|\bmy own meetings\b|\borgani[sz]ed by me\b")
_MIN_PEOPLE = re.compile(r"\b(?:more than|over|at least|>=?)\s*(\d{1,3})\s+(?:people|attendees|invitees|guests)\b",
                         re.IGNORECASE)
_RATE = re.compile(r"\bat\s+(?:[$€£]\s*)?(\d{1,5}(?:\.\d{1,2})?)\s*(?:[$€£]\s*)?(?:an|per|/)\s*(?:hour|hr|h)\b",
                   re.IGNORECASE)
_JSON_MODE = re.compile(r"\b(?:as json|json only|in json)\b", re.IGNORECASE)
_NOTES_LINK = re.compile(r"\bnotes?\b[^\n]{0,40}?(https?://\S+)", re.IGNORECASE)
_SEPARATORS = re.compile(r"\s*[,\t|;]\s*")
_NUMBER = re.compile(r"^\d{1,4}$")
_DURATION = re.compile(r"^(\d{1,3})\s*(?:min|mins|minutes|m)?$", re.IGNORECASE)


@dataclass(frozen=True)
class PastedRow:
    title: str
    day: str
    duration_min: int
    invited: int
    accepted: int
    times: int = 1


@dataclass(frozen=True)
class Request:
    text: str
    window_days: int = DEFAULT_WINDOW_DAYS
    window_clamped: bool = False
    only_organizer: bool = False
    min_people: int | None = None
    rate: float | None = None
    as_json: bool = False
    notes_link: str | None = None
    prior_meetings: tuple[Mapping[str, Any], ...] = ()
    pasted: tuple[PastedRow, ...] = field(default_factory=tuple)

    @property
    def filters(self) -> tuple[str, ...]:
        out = ("only meetings you organize",) if self.only_organizer else ()
        return (*out, f"only meetings with more than {self.min_people} people") if self.min_people else out


def parse(text: str) -> Request:
    days, clamped = window_days(text)
    people = _MIN_PEOPLE.search(text)
    rate = _RATE.search(text)
    link = _NOTES_LINK.search(text)
    return Request(text=text, window_days=days, window_clamped=clamped, only_organizer=bool(_ORGANIZE.search(text)),
                   min_people=int(people.group(1)) if people else None,
                   rate=float(rate.group(1)) if rate and float(rate.group(1)) > 0 else None,
                   as_json=bool(_JSON_MODE.search(text)), notes_link=link.group(1).rstrip(".,)") if link else None,
                   prior_meetings=prior_meetings(text), pasted=pasted_rows(text))


def window_days(text: str) -> tuple[int, bool]:
    """The asked window in days, clamped to 7..56; (28, False) when none was asked."""
    match = _WINDOW.search(text or "")
    if not match:
        return DEFAULT_WINDOW_DAYS, False
    raw = match.group(1).casefold()
    count = _WORD_NUMBERS.get(raw) or int(raw)
    days = count * _UNIT_DAYS[match.group(2).casefold().rstrip("s")]
    clamped = min(MAX_WINDOW_DAYS, max(MIN_WINDOW_DAYS, days))
    return clamped, clamped != days


def prior_meetings(text: str) -> tuple[Mapping[str, Any], ...]:
    """`meetings[]` from an envelope pasted in the message (an earlier run), for comparison."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return ()
    try:
        body = json.loads(text[start:end + 1])
    except ValueError:
        return ()
    meetings = body.get("meetings") if isinstance(body, Mapping) else None
    if not isinstance(meetings, list):
        return ()
    return tuple(m for m in meetings if isinstance(m, Mapping) and isinstance(m.get("series_title"), str))


def pasted_rows(text: str) -> tuple[PastedRow, ...]:
    """Lines shaped `title, day, duration, invited, accepted[, times]` (comma, tab, pipe or semicolon)."""
    rows = []
    for line in (text or "").splitlines():
        row = _row(line.strip().strip("|").strip("-* "))
        if row is not None:
            rows.append(row)
    return tuple(rows[:MAX_PASTED_ROWS])


def _row(line: str) -> PastedRow | None:
    cells = [c for c in _SEPARATORS.split(line) if c]
    if len(cells) not in (5, 6):
        return None
    title, day, duration, invited, accepted = cells[:5]
    times = cells[5] if len(cells) == 6 else "1"
    minutes = _DURATION.match(duration)
    if not minutes or not all(_NUMBER.match(v) for v in (invited, accepted, times)):
        return None
    row = PastedRow(title=title, day=day, duration_min=int(minutes.group(1)), invited=int(invited),
                    accepted=int(accepted), times=int(times))
    valid = 0 < row.duration_min <= 480 and 0 <= row.accepted <= row.invited and row.times >= 1
    return row if valid else None
