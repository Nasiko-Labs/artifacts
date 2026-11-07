"""Pure: every date in this agent is computed here, never by the model.

`resolve(phrase, base)` turns the deadline words of a promise ("by Friday", "EOM", "Nov 14") into a date
against `base` (the message's sent date, else the as-of date). Anything vague ("soon", "ASAP") is no date.
`parse_as_of` reads an explicit "as of 2026-11-05" / "today is Nov 3" from the user's words.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MONTHS = {name.casefold(): n for n, name in enumerate(calendar.month_name) if name} | {
    name.casefold(): n for n, name in enumerate(calendar.month_abbr) if name} | {"sept": 9}
FRIDAY = 4
DUE_PAST_GRACE_DAYS = 7      # an undated-year day this far before base means next year
SENT_FUTURE_GRACE_DAYS = 30  # a sent day this far after as-of means last year

_MONTH = r"(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b\.?"
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY = re.compile(rf"\b({_MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.IGNORECASE)
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH})(?:,?\s+(\d{{4}}))?", re.IGNORECASE)
_SLASH = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?(?![\d/])")
_AS_OF = re.compile(r"\b(?:as of|as at|today is|today's date is|date today is)\s+(.{3,40})", re.IGNORECASE)

_WD = "|".join(WEEKDAYS)
_NEXT_WEEKDAY = re.compile(rf"\bnext\s+({_WD})\b", re.IGNORECASE)
_WEEKDAY = re.compile(rf"\b({_WD})\b", re.IGNORECASE)
_NEXT_WEEK = re.compile(r"\bnext week\b", re.IGNORECASE)
_NEXT_MONTH = re.compile(r"\bnext month\b", re.IGNORECASE)
_EOM = re.compile(r"\b(?:end of (?:the )?month|eom)\b", re.IGNORECASE)
_EOQ = re.compile(r"\b(?:end of (?:the )?quarter|eoq)\b", re.IGNORECASE)
_EOW = re.compile(r"\b(?:this week|end of (?:the )?week|eow)\b", re.IGNORECASE)
_IN_N = re.compile(r"\b(?:in|within)\s+(\d{1,3}|a|one|two|three|four)\s+(days?|weeks?)\b", re.IGNORECASE)
_TOMORROW = re.compile(r"\b(?:tomorrow|tmrw|tmr)\b", re.IGNORECASE)
_TODAY = re.compile(r"\b(?:today|tonight|eod|end of (?:the )?day|cob|close of business)\b", re.IGNORECASE)
_NUMBER_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}


def safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def month_day(text: str) -> tuple[int, int, int | None] | None:
    """(month, day, year or None) of the first explicit date in `text`."""
    if match := _ISO.search(text):
        return int(match.group(2)), int(match.group(3)), int(match.group(1))
    if match := _MONTH_DAY.search(text):
        month = MONTHS.get(_month_key(match.group(1)))
        return (month, int(match.group(2)), _year(match.group(3))) if month else None
    if match := _DAY_MONTH.search(text):
        month = MONTHS.get(_month_key(match.group(2)))
        return (month, int(match.group(1)), _year(match.group(3))) if month else None
    if match := _SLASH.search(text):
        return int(match.group(1)), int(match.group(2)), _year(match.group(3))
    return None


def _month_key(word: str) -> str:
    return word.casefold().rstrip(".")


def _year(text: str | None) -> int | None:
    if not text:
        return None
    year = int(text)
    return year + 2000 if year < 100 else year


def explicit_due(text: str, base: date) -> date | None:
    """An explicit day; without a year, base's year, or next year when that is over a week before base."""
    parts = month_day(text)
    if parts is None:
        return None
    month, day, year = parts
    if year is not None:
        return safe_date(year, month, day)
    found = safe_date(base.year, month, day)
    if found is not None and found < base - timedelta(days=DUE_PAST_GRACE_DAYS):
        return safe_date(base.year + 1, month, day)
    return found


def explicit_sent(text: str, as_of: date) -> date | None:
    """A sent day; without a year, as-of's year, or last year when that is over 30 days after as-of."""
    parts = month_day(text)
    if parts is None:
        return None
    month, day, year = parts
    if year is not None:
        return safe_date(year, month, day)
    found = safe_date(as_of.year, month, day)
    if found is not None and found > as_of + timedelta(days=SENT_FUTURE_GRACE_DAYS):
        return safe_date(as_of.year - 1, month, day)
    return found


def parse_as_of(text: str, today: date) -> date | None:
    match = _AS_OF.search(text or "")
    if not match:
        return None
    lowered = match.group(1).casefold()
    if lowered.startswith(("today", "now")):
        return today
    return explicit_sent(match.group(1), today) if month_day(match.group(1)) else None


def last_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def resolve(phrase: str, base: date) -> date | None:
    """The date a deadline phrase means against `base`, or None when it names no date."""
    text = phrase or ""
    if not text.strip():
        return None
    explicit = explicit_due(text, base)
    if explicit is not None:
        return explicit
    for rule in _RULES:
        found = rule(text, base)
        if found is not False:
            return found
    return None


def _next_weekday(text: str, base: date):
    match = _NEXT_WEEKDAY.search(text)
    return week_start(base) + timedelta(days=7 + WEEKDAYS.index(match.group(1).casefold())) if match else False


def _next_week(text: str, base: date):
    return week_start(base) + timedelta(days=7 + FRIDAY) if _NEXT_WEEK.search(text) else False


def _next_month(text: str, base: date):
    if not _NEXT_MONTH.search(text):
        return False
    year, month = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
    return last_of_month(year, month)


def _eom(text: str, base: date):
    return last_of_month(base.year, base.month) if _EOM.search(text) else False


def _eoq(text: str, base: date):
    return last_of_month(base.year, ((base.month - 1) // 3) * 3 + 3) if _EOQ.search(text) else False


def _eow(text: str, base: date):
    if not _EOW.search(text):
        return False
    return base if base.weekday() >= FRIDAY else week_start(base) + timedelta(days=FRIDAY)


def _in_n(text: str, base: date):
    match = _IN_N.search(text)
    if not match:
        return False
    count = _NUMBER_WORDS.get(match.group(1).casefold()) or int(match.group(1))
    return base + timedelta(days=count * (7 if match.group(2).casefold().startswith("week") else 1))


def _weekday(text: str, base: date):
    match = _WEEKDAY.search(text)
    if not match:
        return False
    return base + timedelta(days=(WEEKDAYS.index(match.group(1).casefold()) - base.weekday()) % 7)


def _tomorrow(text: str, base: date):
    return base + timedelta(days=1) if _TOMORROW.search(text) else False


def _today(text: str, base: date):
    return base if _TODAY.search(text) else False


_RULES = (_next_weekday, _next_week, _next_month, _eom, _eoq, _eow, _in_n, _weekday, _tomorrow, _today)
