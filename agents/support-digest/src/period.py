"""Pure: the digest period, parsed from the user's words in code (never by the model), and the previous
period of equal length. Dates are inclusive UTC calendar days; "last N days" means the N full days
before today. The length is clamped to the configured 1-31 days."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from config import Config

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10, "fourteen": 14,
          "thirty": 30}
_NUM = r"(\d{1,3}|" + "|".join(_WORDS) + r")"
_SPAN = re.compile(rf"\b(?:last|past|previous)\s+{_NUM}\s*(days?|weeks?)\b", re.IGNORECASE)
_ISO_RANGE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*(?:to|until|through|-|–|\.\.)\s*(\d{4}-\d{2}-\d{2})")
_ISO_DAY = re.compile(r"\b(?:on|for)\s+(\d{4}-\d{2}-\d{2})\b")


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def start_epoch(self) -> int:
        return int(datetime.combine(self.start, time.min, tzinfo=UTC).timestamp())

    @property
    def end_epoch(self) -> int:
        """The first second after the period."""
        return int(datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=UTC).timestamp())

    def holds(self, day: date) -> bool:
        return self.start <= day <= self.end

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


def previous(p: Period) -> Period:
    """The period of equal length that ends the day before `p` starts."""
    end = p.start - timedelta(days=1)
    return Period(start=end - timedelta(days=p.days - 1), end=end)


def _clamp(start: date, end: date, config: Config) -> Period:
    if end < start:
        start, end = end, start
    most = config.max_period_days
    if (end - start).days + 1 > most:
        start = end - timedelta(days=most - 1)
    return Period(start, end)


def _last_days(today: date, days: int) -> tuple[date, date]:
    end = today - timedelta(days=1)
    return end - timedelta(days=days - 1), end


def _named(text: str, today: date, config: Config) -> tuple[date, date] | None:
    low = text.casefold()
    if re.search(r"\byesterday\b", low):
        day = today - timedelta(days=1)
        return day, day
    if re.search(r"\btoday\b", low):
        return today, today
    if re.search(r"\bthis week\b", low):
        return today - timedelta(days=today.weekday()), today
    if re.search(r"\bthis month\b", low):
        return today.replace(day=1), today
    if re.search(r"\blast month\b|\bprevious month\b", low):
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if re.search(r"\b(?:last|past|previous) week\b", low):
        return _last_days(today, 7)
    return None


def _iso(text: str) -> tuple[date, date] | None:
    rng = _ISO_RANGE.search(text)
    try:
        if rng:
            return date.fromisoformat(rng.group(1)), date.fromisoformat(rng.group(2))
        one = _ISO_DAY.search(text)
        if one:
            day = date.fromisoformat(one.group(1))
            return day, day
    except ValueError:
        return None
    return None


def explicit_period(text: str, today: date, config: Config) -> Period | None:
    """The period the words name, or None when they name none."""
    found = _iso(text or "") or _named(text or "", today, config)
    if found is None:
        span = _SPAN.search(text or "")
        if span:
            n = _WORDS.get(span.group(1).casefold()) or int(span.group(1))
            n = n * 7 if span.group(2).casefold().startswith("week") else n
            found = _last_days(today, max(config.min_period_days, min(config.max_period_days, n)))
    return _clamp(*found, config) if found else None


def parse_period(text: str, today: date, config: Config) -> Period:
    """The named period, else the default: the last `period_days` full days."""
    return explicit_period(text, today, config) or Period(*_last_days(today, config.period_days))
