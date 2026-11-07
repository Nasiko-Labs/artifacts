"""Pure: which quarter is audited, and its boundaries in every format a search tool may want.

Quarters are calendar quarters unless FISCAL_YEAR_START_MONTH moves the fiscal year's first month.
A quarter is labelled by the calendar year its fiscal year starts in ("Q4 2026"). Dates and the share
of the quarter elapsed are computed here, never by the model."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

DEFAULT_FISCAL_START_MONTH = 1
_LAST = re.compile(r"\b(?:last|previous|prior)\s+quarter\b", re.IGNORECASE)
_NEXT = re.compile(r"\bnext\s+quarter\b", re.IGNORECASE)
_Q_THEN_YEAR = re.compile(r"\bQ([1-4])\b(?:\s*(?:of\s+)?(?:FY\s*)?'?(\d{4}|\d{2})\b)?", re.IGNORECASE)
_YEAR_THEN_Q = re.compile(r"\b(?:FY\s*)?'?(\d{4}|\d{2})\s*Q([1-4])\b", re.IGNORECASE)


@dataclass(frozen=True)
class Quarter:
    number: int        # 1-4 within the fiscal year
    fiscal_year: int   # the calendar year the fiscal year starts in
    start: date
    end: date          # the last day, inclusive

    @property
    def label(self) -> str:
        return f"Q{self.number} {self.fiscal_year}"

    @property
    def start_slashed(self) -> str:
        return self.start.strftime("%Y/%m/%d")

    @property
    def end_slashed(self) -> str:
        return self.end.strftime("%Y/%m/%d")

    @property
    def start_epoch(self) -> int:
        return int(datetime.combine(self.start, time.min, tzinfo=UTC).timestamp())

    @property
    def end_epoch(self) -> int:
        """The last second of the last day."""
        return int(datetime.combine(self.end, time.max, tzinfo=UTC).timestamp())

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def to_dict(self) -> dict:
        return {"number": self.number, "fiscal_year": self.fiscal_year, "start": self.start.isoformat(),
                "end": self.end.isoformat()}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Quarter:
        return Quarter(int(data["number"]), int(data["fiscal_year"]), date.fromisoformat(str(data["start"])),
                       date.fromisoformat(str(data["end"])))


def fiscal_start_month(raw: str | None) -> int:
    """FISCAL_YEAR_START_MONTH as 1-12; anything else falls back to January."""
    try:
        month = int((raw or "").strip())
    except ValueError:
        return DEFAULT_FISCAL_START_MONTH
    return month if 1 <= month <= 12 else DEFAULT_FISCAL_START_MONTH


def quarter_of(day: date, start_month: int = DEFAULT_FISCAL_START_MONTH) -> Quarter:
    fiscal_year = day.year if day.month >= start_month else day.year - 1
    number = (day.month - start_month) % 12 // 3 + 1
    return build(fiscal_year, number, start_month)


def build(fiscal_year: int, number: int, start_month: int = DEFAULT_FISCAL_START_MONTH) -> Quarter:
    start = _month_start(fiscal_year, start_month + 3 * (number - 1))
    following = _month_start(fiscal_year, start_month + 3 * number)
    return Quarter(number, fiscal_year, start, following - timedelta(days=1))


def _month_start(year: int, month_index: int) -> date:
    """`month_index` may run past 12 into the next year."""
    return date(year + (month_index - 1) // 12, (month_index - 1) % 12 + 1, 1)


def shifted(quarter: Quarter, by: int, start_month: int = DEFAULT_FISCAL_START_MONTH) -> Quarter:
    index = quarter.fiscal_year * 4 + quarter.number - 1 + by
    return build(index // 4, index % 4 + 1, start_month)


def parse_quarter(text: str, today: date, start_month: int = DEFAULT_FISCAL_START_MONTH) -> Quarter:
    """The quarter the user named ("Q3", "Q1 2027", "last quarter"); the current one by default."""
    current = quarter_of(today, start_month)
    message = text or ""
    if _LAST.search(message):
        return shifted(current, -1, start_month)
    if _NEXT.search(message):
        return shifted(current, 1, start_month)
    year_first = _YEAR_THEN_Q.search(message)
    if year_first:
        return build(_year(year_first.group(1)), int(year_first.group(2)), start_month)
    named = _Q_THEN_YEAR.search(message)
    if named:
        year = _year(named.group(2)) if named.group(2) else current.fiscal_year
        return build(year, int(named.group(1)), start_month)
    return current


def _year(raw: str) -> int:
    value = int(raw)
    return 2000 + value if value < 100 else value


def elapsed_share(start: date, deadline: date, today: date) -> float:
    """(today − start) / (deadline − start), clamped to 0..1; a deadline on or before the start is 1."""
    span = (deadline - start).days
    if span <= 0:
        return 1.0
    return min(1.0, max(0.0, (today - start).days / span))
