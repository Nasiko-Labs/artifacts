"""Pure: what the user's message asks for, parsed in code (never by the model).

Which day(s), a part of the day, the minimum gap, working hours, a stated timezone, JSON output, and the
`date` / `timezone` fields of an envelope handed over by an earlier workflow step.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, time, timedelta

import timewin

DEFAULT_MIN_GAP = 15
MAX_DAYS = 5
DEFAULT_HOURS = (time(9, 0), time(18, 0))
LUNCH = time(12, 0)
_AFTERNOON_START = time(12, 0)
_EVENING_START = time(17, 0)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_TODAY = re.compile(r"\btoday\b|\btonight\b", re.IGNORECASE)
_TOMORROW = re.compile(r"\btomorrow\b", re.IGNORECASE)
_NEXT_WEEKDAY = re.compile(rf"\bnext\s+({'|'.join(_WEEKDAYS)})\b", re.IGNORECASE)
_WEEKDAY = re.compile(rf"\b({'|'.join(_WEEKDAYS)})\b", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_THIS_WEEK = re.compile(r"\b(?:this|the)\s+week\b|\brest of (?:the|this) week\b", re.IGNORECASE)
_NEXT_N_DAYS = re.compile(r"\bnext\s+(\d{1,2})\s+days\b", re.IGNORECASE)
_MINUTES = re.compile(r"\b(\d{1,3})\s*(?:-\s*)?(?:minutes?|mins?|m)\b", re.IGNORECASE)
_HOURS_GAP = re.compile(r"\b(an|one|two|three|\d)\s*(?:-\s*)?(?:hours?|hrs?|h)\b", re.IGNORECASE)
_HALF_HOUR = re.compile(r"\bhalf an hour\b|\bhalf-hour\b", re.IGNORECASE)
_MORNING = re.compile(r"\bmorning\b|\bbefore lunch\b|\bbefore noon\b", re.IGNORECASE)
_AFTERNOON = re.compile(r"\bafternoon\b|\bafter lunch\b", re.IGNORECASE)
_EVENING = re.compile(r"\bevening\b", re.IGNORECASE)
_CLOCK = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
_AFTER = re.compile(rf"\bafter\s+{_CLOCK}\b", re.IGNORECASE)
_BEFORE = re.compile(rf"\bbefore\s+{_CLOCK}\b", re.IGNORECASE)
_WORK_HOURS = re.compile(
    rf"\b(?:my day is|my day runs|i work|work(?:ing)? hours?(?: are| is)?|hours are|my hours are)\s*(?:from\s*)?"
    rf"{_CLOCK}\s*(?:to|-|until|till)\s*{_CLOCK}", re.IGNORECASE)
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_NUMBER_WORDS = {"an": 1, "one": 1, "two": 2, "three": 3}


@dataclass(frozen=True)
class Request:
    """`days` is None when the message names no day (today, resolved once the zone is known)."""

    days: tuple[date, ...] | None = None
    day_words: str = ""                       # "tomorrow", "monday", ... resolved against the local today
    span: int = 1
    part: tuple[time, time] | None = None     # a part of the day the user asked about
    min_gap: int = DEFAULT_MIN_GAP
    hours: tuple[time, time] | None = None    # working hours the user stated in this message
    stated_tz: str | None = None
    as_json: bool = False

    @property
    def names_day(self) -> bool:
        return self.days is not None or bool(self.day_words) or self.span > 1


def parse_request(text: str) -> Request:
    body = text or ""
    envelope = _envelope(body)
    if envelope is not None:
        return _from_envelope(envelope, body)
    days, words, span = _day_spec(body)
    return Request(days=days, day_words=words, span=span, part=part_of_day(body), min_gap=min_gap(body),
                   hours=working_hours(body), stated_tz=timewin.stated_timezone(body),
                   as_json=bool(_JSON_WORDS.search(body)))


def _day_spec(text: str) -> tuple[tuple[date, ...] | None, str, int]:
    iso = _ISO_DATE.search(text)
    if iso:
        parsed = timewin.parse_date(iso.group(1))
        if parsed is not None:
            return (parsed,), "", 1
    n_days = _NEXT_N_DAYS.search(text)
    if n_days:
        return None, "today", max(1, min(MAX_DAYS, int(n_days.group(1))))
    if _THIS_WEEK.search(text):
        return None, "week", MAX_DAYS
    nxt = _NEXT_WEEKDAY.search(text)
    if nxt:
        return None, "next " + nxt.group(1).casefold(), 1
    if _TOMORROW.search(text):
        return None, "tomorrow", 1
    weekday = _WEEKDAY.search(text)
    if weekday:
        return None, weekday.group(1).casefold(), 1
    return None, ("today" if _TODAY.search(text) else ""), 1


def resolve_days(request: Request, today: date) -> tuple[date, ...]:
    """The local dates asked for. "Monday" is the coming Monday (today if today is Monday); "next
    Friday" is the Friday of next week; "this week" is the weekdays left this week, at most 5 days."""
    if request.days is not None:
        return request.days
    words = request.day_words
    if words == "week":
        left = 5 - today.weekday()
        count = left if left > 0 else MAX_DAYS
        return tuple(today + timedelta(days=i) for i in range(min(MAX_DAYS, count)))
    first = _first_day(words, today)
    return tuple(first + timedelta(days=i) for i in range(min(MAX_DAYS, max(1, request.span))))


def _first_day(words: str, today: date) -> date:
    if words == "tomorrow":
        return today + timedelta(days=1)
    if words.startswith("next "):
        wanted = _WEEKDAYS.index(words[5:])
        next_monday = today + timedelta(days=7 - today.weekday())
        return next_monday + timedelta(days=wanted)
    if words in _WEEKDAYS:
        return today + timedelta(days=(_WEEKDAYS.index(words) - today.weekday()) % 7)
    return today


def min_gap(text: str) -> int:
    if _HALF_HOUR.search(text or ""):
        return 30
    minutes = _MINUTES.search(text or "")
    if minutes:
        return max(5, min(600, int(minutes.group(1))))
    hours = _HOURS_GAP.search(text or "")
    if hours:
        raw = hours.group(1).casefold()
        return 60 * (_NUMBER_WORDS.get(raw) or int(raw))
    return DEFAULT_MIN_GAP


def part_of_day(text: str) -> tuple[time, time] | None:
    """Limits on the gaps the user asked about; the working hours clip them further."""
    after, before = _AFTER.search(text or ""), _BEFORE.search(text or "")
    if after or before:
        start = _clock(after, pm_bias=True) if after else None
        end = _clock(before, pm_bias=True) if before else None
        return (start or time.min, end or time.max)
    if _MORNING.search(text or ""):
        return (time.min, LUNCH)
    if _AFTERNOON.search(text or ""):
        return (_AFTERNOON_START, _EVENING_START)
    if _EVENING.search(text or ""):
        return (_EVENING_START, time.max)
    return None


def working_hours(text: str) -> tuple[time, time] | None:
    """"my day is 8 to 4" -> 08:00-16:00 (a bare end not after the start is read as pm)."""
    match = _WORK_HOURS.search(text or "")
    if not match:
        return None
    start = _to_time(match.group(1), match.group(2), match.group(3))
    end = _to_time(match.group(4), match.group(5), match.group(6))
    if start is None or end is None:
        return None
    if end <= start and not match.group(6) and end.hour < 12:
        end = time(end.hour + 12, end.minute)
    return (start, end) if end > start else None


def _clock(match: re.Match, pm_bias: bool) -> time | None:
    parsed = _to_time(match.group(1), match.group(2), match.group(3))
    if parsed is not None and pm_bias and not match.group(3) and 1 <= parsed.hour <= 7:
        return time(parsed.hour + 12, parsed.minute)  # "after 3" means 15:00 in a working day
    return parsed


def _to_time(hour: str, minute: str | None, half: str | None) -> time | None:
    h, m = int(hour), int(minute or 0)
    if m > 59:
        return None
    if half:
        if not 1 <= h <= 12:
            return None
        h = (h % 12) + (12 if half.casefold() == "pm" else 0)
    return time(h, m) if h <= 23 else None


def _envelope(text: str) -> dict | None:
    body = text.strip()
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    inner = data.get("envelope")
    return inner if isinstance(inner, dict) else data


def _from_envelope(data: dict, raw: str) -> Request:
    """An envelope from an earlier step: reads `date` and `timezone` only."""
    day = timewin.parse_date(data.get("date"))
    return Request(days=(day,) if day else None, stated_tz=timewin.valid_zone_name(data.get("timezone")),
                   as_json=bool(_JSON_WORDS.search(raw)))
