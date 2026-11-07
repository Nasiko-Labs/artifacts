"""Pure: the reporting period the user asked for, parsed in code on a fixed clock (UTC dates).

Default is the last full calendar month. Understood: "last/previous month", "this month", "last/this
quarter", "Q3" / "Q3 2026", "last N days" / "last N weeks", "March 2026" / "March", explicit ISO dates
("2026-03-01 to 2026-03-31"). A period never ends in the future and never spans more than 13 months."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

MAX_MONTHS = 13
MAX_DAYS = 400
_MONTHS = {name.casefold(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.casefold(): i for i, name in enumerate(calendar.month_abbr) if name})
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_NAMED_MONTH = re.compile(rf"\b({_MONTH_NAMES})\.?(?:\s+(\d{{4}}))?\b", re.IGNORECASE)
_QUARTER = re.compile(r"\bq([1-4])(?:\s*(?:of\s+)?(\d{4}))?\b", re.IGNORECASE)
_LAST_QUARTER = re.compile(r"\b(?:last|previous|prior)\s+quarter\b", re.IGNORECASE)
_THIS_QUARTER = re.compile(r"\b(?:this|current)\s+quarter\b|\bqtd\b", re.IGNORECASE)
_THIS_MONTH = re.compile(r"\b(?:this|current)\s+month\b|\bmtd\b", re.IGNORECASE)
_LAST_DAYS = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s+days?\b", re.IGNORECASE)
_LAST_WEEKS = re.compile(r"\b(?:last|past|previous)\s+(\d{1,2})\s+weeks?\b", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str
    clamped: bool = False

    def t0(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=UTC)

    def t1(self, now: datetime) -> datetime:
        """End of the end day (inclusive), or now when the period ends today."""
        end = datetime.combine(self.end, time(23, 59, 59), tzinfo=UTC)
        return min(end, now)

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "label": self.label,
                "clamped": self.clamped}

    @staticmethod
    def from_dict(data: dict) -> Period:
        return Period(date.fromisoformat(data["start"]), date.fromisoformat(data["end"]), str(data["label"]),
                      bool(data.get("clamped")))


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def last_full_month(today: date) -> Period:
    first = today.replace(day=1)
    end = first - timedelta(days=1)
    return Period(end.replace(day=1), end, f"{calendar.month_name[end.month]} {end.year}")


def parse(text: str, now: datetime) -> Period:
    """The period named in `text`, clamped to today and to 13 months."""
    today = now.date()
    found = _explicit(text or "", today) or last_full_month(today)
    return _clamp(found, today)


def _explicit(text: str, today: date) -> Period | None:
    for reader in (_dates, _quarter, _days, _month):
        found = reader(text, today)
        if found is not None:
            return found
    return None


def _dates(text: str, today: date) -> Period | None:
    days = []
    for y, m, d in _ISO_DATE.findall(text):
        try:
            days.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    if len(days) < 2:
        return None
    start, end = sorted(days[:2])
    return Period(start, end, f"{start.isoformat()} to {end.isoformat()}")


def _quarter(text: str, today: date) -> Period | None:
    current_q = (today.month - 1) // 3 + 1
    if _THIS_QUARTER.search(text):
        start = date(today.year, 3 * current_q - 2, 1)
        return Period(start, today, f"Q{current_q} {today.year} to date")
    if _LAST_QUARTER.search(text):
        year, q = (today.year, current_q - 1) if current_q > 1 else (today.year - 1, 4)
        return _full_quarter(year, q)
    match = _QUARTER.search(text)
    if not match:
        return None
    q = int(match.group(1))
    year = int(match.group(2)) if match.group(2) else today.year
    if not match.group(2) and date(year, 3 * q - 2, 1) > today:
        year -= 1  # "Q4" in May means last year's Q4
    return _full_quarter(year, q)


def _full_quarter(year: int, q: int) -> Period:
    return Period(date(year, 3 * q - 2, 1), month_end(year, 3 * q), f"Q{q} {year}")


def _days(text: str, today: date) -> Period | None:
    match = _LAST_DAYS.search(text)
    weeks = _LAST_WEEKS.search(text)
    if match:
        n = int(match.group(1))
    elif weeks:
        n = 7 * int(weeks.group(1))
    else:
        return None
    n = max(1, min(n, MAX_DAYS))
    start = today - timedelta(days=n)
    return Period(start, today, f"last {n} days ({start.isoformat()} to {today.isoformat()})")


def _month(text: str, today: date) -> Period | None:
    if _THIS_MONTH.search(text):
        start = today.replace(day=1)
        return Period(start, today, f"{calendar.month_name[today.month]} {today.year} to date")
    match = _NAMED_MONTH.search(text)
    if not match or _ambiguous_may(match, text):
        return None
    month = _MONTHS[match.group(1).casefold()]
    year = int(match.group(2)) if match.group(2) else today.year
    if not match.group(2) and date(year, month, 1) > today:
        year -= 1  # "March" in February means last March
    end = month_end(year, month)
    label = f"{calendar.month_name[month]} {year}"
    return Period(date(year, month, 1), end, label if end <= today else f"{label} to date")


def _ambiguous_may(match: re.Match, text: str) -> bool:
    """'may' is a month only with a year after it or a capital M."""
    word = match.group(1)
    return word.casefold() == "may" and not match.group(2) and not word.startswith("M")


def _clamp(found: Period, today: date) -> Period:
    end = min(found.end, today)
    earliest = add_months(end, -MAX_MONTHS)
    start = max(found.start, earliest)
    start = min(start, end)
    clamped = start != found.start
    label = found.label if not clamped else f"{start.isoformat()} to {end.isoformat()} (clamped to 13 months)"
    return Period(start, end, label, clamped)
