"""Pure: due phrases in a task resolved against `as_of` (code only; the model never sets urgency), the
urgency bands, blocking words and the as-of date the user may give ("today is 2026-11-05")."""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

PAST_GRACE_DAYS = 7
URGENT_FLOOR = 4
_MONTHS = {m.casefold()[:3]: n for n, m in enumerate(calendar.month_abbr) if m}
_MONTH = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|"
          r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.IGNORECASE)
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH}", re.IGNORECASE)
_SLASH = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])")
_TODAY = re.compile(r"\b(?:today|tonight|eod|cob|end of (?:the )?day)\b", re.IGNORECASE)
_TOMORROW = re.compile(r"\btomorrow\b", re.IGNORECASE)
_WEEKDAY = re.compile(rf"\b({'|'.join(_WEEKDAYS)})\b", re.IGNORECASE)
_NEXT_WEEK = re.compile(r"\bnext week\b", re.IGNORECASE)
_THIS_WEEK = re.compile(r"\b(?:this week|eow|end of (?:the )?week)\b", re.IGNORECASE)
_IN_N = re.compile(r"\bin (\d{1,3}|a|an|one|two|three|four|five) (days?|weeks?)\b", re.IGNORECASE)
_NEXT_MONTH = re.compile(r"\b(?:end of )?next month\b", re.IGNORECASE)
_EOM = re.compile(r"\b(?:end of (?:the |this )?month|eom)\b", re.IGNORECASE)
_URGENT = re.compile(r"\b(asap|urgent(?:ly)?|immediately|blocking)\b", re.IGNORECASE)
_BLOCKING = re.compile(r"\b(?:blocking|blocked|blocker|blockers|waiting on me|unblock\w*|outage|escalat\w*|p0|p1|"
                       r"customer is|customers are)\b", re.IGNORECASE)
_AS_OF = re.compile(r"\b(?:today is|as of|assume today is|it is now)\s+(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)


@dataclass(frozen=True)
class Due:
    date: date | None = None
    phrase: str | None = None
    urgent: bool = False   # ASAP / urgent / immediately / blocking: urgency floor, no date


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _calendar(month: int, day: int, as_of: date) -> date | None:
    found = _safe(as_of.year, month, day)
    if found is not None and found < as_of - timedelta(days=PAST_GRACE_DAYS):
        found = _safe(as_of.year + 1, month, day)
    return found


def _end_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _monday(as_of: date) -> date:
    return as_of - timedelta(days=as_of.weekday())


def _next_month_end(as_of: date) -> date:
    year, month = (as_of.year + 1, 1) if as_of.month == 12 else (as_of.year, as_of.month + 1)
    return _end_of_month(year, month)


def _in_n(m: re.Match, as_of: date) -> date:
    raw = m.group(1).casefold()
    count = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
    return as_of + timedelta(days=count * (7 if m.group(2).casefold().startswith("week") else 1))


def _weekday(m: re.Match, as_of: date) -> date:
    target = _WEEKDAYS.index(m.group(1).casefold())
    return as_of + timedelta(days=(target - as_of.weekday()) % 7)


_RULES: tuple[tuple[re.Pattern, Callable[[re.Match, date], date | None]], ...] = (
    (_ISO, lambda m, d: _safe(int(m.group(1)), int(m.group(2)), int(m.group(3)))),
    (_MONTH_DAY, lambda m, d: _calendar(_MONTHS[m.group(1).casefold()[:3]], int(m.group(2)), d)),
    (_DAY_MONTH, lambda m, d: _calendar(_MONTHS[m.group(2).casefold()[:3]], int(m.group(1)), d)),
    (_SLASH, lambda m, d: _calendar(int(m.group(1)), int(m.group(2)), d)),
    (_TODAY, lambda m, d: d),
    (_TOMORROW, lambda m, d: d + timedelta(days=1)),
    (_NEXT_WEEK, lambda m, d: _monday(d) + timedelta(days=11)),
    (_THIS_WEEK, lambda m, d: _monday(d) + timedelta(days=4)),
    (_IN_N, _in_n),
    (_NEXT_MONTH, lambda m, d: _next_month_end(d)),
    (_EOM, lambda m, d: _end_of_month(d.year, d.month)),
    (_WEEKDAY, _weekday),
)


def resolve(text: str, as_of: date) -> Due:
    """The earliest-written due phrase in `text`, resolved against `as_of` (weeks start Monday)."""
    found: list[tuple[int, date, str]] = []
    for pattern, rule in _RULES:
        for m in pattern.finditer(text or ""):
            resolved = rule(m, as_of)
            if resolved is not None:
                found.append((m.start(), resolved, m.group(0).strip()))
                break
    if found:
        _, when, phrase = min(found, key=lambda f: f[0])
        return Due(when, phrase, False)
    urgent = _URGENT.search(text or "")
    return Due(None, urgent.group(1) if urgent else None, bool(urgent))


def days_left(due: date | None, as_of: date) -> int | None:
    return None if due is None else (due - as_of).days


def urgency_for(days: int | None) -> int:
    if days is None or days > 30:
        return 1
    if days <= 1:
        return 5
    if days <= 3:
        return 4
    return 3 if days <= 7 else 2


def has_blocking_word(text: str) -> bool:
    return bool(_BLOCKING.search(text or ""))


def urgency(text: str, as_of: date) -> tuple[int, Due, bool]:
    """(urgency, due, blocking) for one task: the band from its date, raised to 4 by an urgent or blocking word."""
    due = resolve(text, as_of)
    blocking = has_blocking_word(text)
    band = urgency_for(days_left(due.date, as_of))
    return (max(band, URGENT_FLOOR) if due.urgent or blocking else band), due, blocking


def parse_as_of(text: str) -> date | None:
    m = _AS_OF.search(text or "")
    if not m:
        return None
    year, month, day = (int(p) for p in m.group(1).split("-"))
    return _safe(year, month, day)


def strip_as_of(text: str) -> str:
    return _AS_OF.sub("", text or "")
