"""Pure: every date this agent uses, computed in code. The model is handed exact values and never
computes "30 days ago" itself.

    window = parse_window("anything slipped in the last 12 months?", today)
    window.start            # date 12 calendar months back, day clamped
    window.stale_before     # today - 30 days
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

DEFAULT_MONTHS = 6
MIN_MONTHS = 1
MAX_MONTHS = 12
STALE_DAYS = 30
EVIDENCE_LOOKBACK_DAYS = 14

_MONTHS = {name.casefold(): n for n, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.casefold(): n for n, name in enumerate(calendar.month_abbr) if name})
_N_MONTHS = re.compile(r"\b(\d{1,3})\s*(?:-\s*)?months?\b", re.IGNORECASE)
_N_YEARS = re.compile(r"\b(?:(\d{1,2})|a|one|past|last)\s+years?\b", re.IGNORECASE)
_SINCE_MONTH = re.compile(r"\bsince\s+([A-Za-z]{3,9})\b", re.IGNORECASE)
_THIS_YEAR = re.compile(r"\bthis year\b|\byear to date\b|\bytd\b", re.IGNORECASE)
_TZ_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")
_FRACTION = re.compile(r"(\.\d+)")
_EPOCH = re.compile(r"^\d{9,13}(?:\.\d+)?$")


def add_months(day: date, months: int) -> date:
    """`day` moved by whole calendar months; the day of month is clamped (Mar 31 - 1 month = Feb 28)."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(day.day, last))


def clamp_months(months: int) -> int:
    return max(MIN_MONTHS, min(MAX_MONTHS, months))


@dataclass(frozen=True)
class Window:
    today: date
    months: int
    start: date

    @property
    def stale_before(self) -> date:
        return self.today - timedelta(days=STALE_DAYS)

    def values(self) -> str:
        """The exact values every prompt carries."""
        return "\n".join((
            "Use these exact values; never compute or guess a date yourself.",
            f"- TODAY: {_formats(self.today)}",
            f"- WINDOW_START (postmortems created on or after this): {_formats(self.start)}",
            f"- STALE_BEFORE: {_formats(self.stale_before)}",
        ))


def _formats(day: date) -> str:
    return f"{day.isoformat()} (ISO {day.isoformat()}T00:00:00Z, Unix seconds {epoch(day)})"


def epoch(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=UTC).timestamp())


def parse_window(text: str, today: date) -> Window:
    """The window the message asks for: "last N months" / "N years" / "since <month>" / "this year";
    6 months by default; always clamped to 1-12 months back."""
    message = text or ""
    since = _since_month(message, today)
    if since is not None:
        return since
    if _THIS_YEAR.search(message):
        return _from_start(date(today.year, 1, 1), today)
    months = _months_asked(message)
    clamped = clamp_months(months if months is not None else DEFAULT_MONTHS)
    return Window(today=today, months=clamped, start=add_months(today, -clamped))


def _months_asked(message: str) -> int | None:
    match = _N_MONTHS.search(message)
    if match:
        return int(match.group(1))
    years = _N_YEARS.search(message)
    if years:
        return 12 * int(years.group(1) or 1)
    return None


def _since_month(message: str, today: date) -> Window | None:
    match = _SINCE_MONTH.search(message)
    month = _MONTHS.get(match.group(1).casefold()) if match else None
    if month is None:
        return None
    year = today.year if month <= today.month else today.year - 1
    return _from_start(date(year, month, 1), today)


def _from_start(start: date, today: date) -> Window:
    """A start date asked for by name, clamped to the 12-month limit."""
    earliest = add_months(today, -MAX_MONTHS)
    start = max(start, earliest)
    months = clamp_months((today.year - start.year) * 12 + today.month - start.month or 1)
    return Window(today=today, months=months, start=start)


def parse_date(value: object) -> date | None:
    """A UTC date from an ISO date/datetime (Z, +00:00 or +0000 offsets) or epoch seconds/millis;
    None when it does not parse."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    text = str(value).strip()
    if not text:
        return None
    if _EPOCH.match(text):
        return _from_epoch(float(text))
    return _from_iso(text)


def _from_epoch(seconds: float) -> date | None:
    if seconds > 1e11:  # milliseconds
        seconds /= 1000
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def _from_iso(text: str) -> date | None:
    cleaned = text.replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2} \d", text) else text
    cleaned = cleaned[:-1] + "+00:00" if cleaned.endswith(("Z", "z")) else cleaned
    cleaned = _TZ_NO_COLON.sub(r"\1:\2", cleaned) if "T" in cleaned else cleaned
    cleaned = _FRACTION.sub(lambda m: m.group(1)[:7], cleaned)
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.date()


def days_between(earlier: date, later: date) -> int:
    return (later - earlier).days
