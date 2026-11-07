"""Pure: the period, the granularity and the two comparison windows (always of equal length).

monthly data (every date on the 1st)  → latest month vs the month before
daily, span ≥ 60 days                 → last 30 days vs the 30 before
daily, span 14–59 days                → last 7 days vs the 7 before
daily, span < 14 days                 → no comparison
A user window ("last 14 days") overrides when the data spans twice that."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

LONG_SPAN_DAYS, LONG_WINDOW = 60, 30
SHORT_SPAN_DAYS, SHORT_WINDOW = 14, 7
NO_COMPARISON = "Need at least 14 days to compare"
_USER_WINDOW = re.compile(r"\b(?:last|past|previous|recent)\s+(\d{1,3})\s+days?\b|\b(\d{1,3})\s*-?\s*days?\s+vs\b",
                          re.IGNORECASE)
_WEEK = re.compile(r"\b(?:vs\.?|versus|than|over)\s+(?:last|the previous|previous|prior)\s+week\b|\bweek over week\b|"
                   r"\blast week\b", re.IGNORECASE)


@dataclass(frozen=True)
class Windows:
    start: pd.Timestamp
    end: pd.Timestamp
    days: int
    monthly: bool
    cur: tuple[pd.Timestamp, pd.Timestamp] | None = None   # inclusive bounds
    prev: tuple[pd.Timestamp, pd.Timestamp] | None = None
    label: str = ""
    note: str = ""

    @property
    def comparable(self) -> bool:
        return self.cur is not None


def user_window(text: str) -> int | None:
    match = _USER_WINDOW.search(text or "")
    if match:
        days = int(match.group(1) or match.group(2))
        return days if days >= 1 else None
    return SHORT_WINDOW if _WEEK.search(text or "") else None


def compute(dates: pd.Series, wanted_days: int | None = None) -> Windows:
    start, end = dates.min(), dates.max()
    days = int((end - start).days) + 1
    monthly = bool((dates.dt.day == 1).all()) and dates.nunique() > 1
    if monthly:
        return _monthly(dates, start, end, days)
    base = Windows(start, end, days, False)
    note = ""
    if wanted_days:
        if days >= 2 * wanted_days:
            return _daily(base, wanted_days)
        note = f"Your {wanted_days}-day window needs {2 * wanted_days} days of data; this export has {days}."
    if days >= LONG_SPAN_DAYS:
        chosen = _daily(base, LONG_WINDOW)
    elif days >= SHORT_SPAN_DAYS:
        chosen = _daily(base, SHORT_WINDOW)
    else:
        return Windows(start, end, days, False, note=" ".join(p for p in (note, NO_COMPARISON + ".") if p))
    return Windows(**{**chosen.__dict__, "note": note})


def _daily(base: Windows, n: int) -> Windows:
    cur = (base.end - pd.Timedelta(days=n - 1), base.end)
    prev = (base.end - pd.Timedelta(days=2 * n - 1), base.end - pd.Timedelta(days=n))
    return Windows(base.start, base.end, base.days, False, cur, prev, f"last {n} days vs the {n} before")


def _monthly(dates: pd.Series, start: pd.Timestamp, end: pd.Timestamp, days: int) -> Windows:
    months = sorted(dates.dt.to_period("M").unique())
    last, before = months[-1], months[-2]
    cur = (last.start_time, last.start_time)
    prev = (before.start_time, before.start_time)
    label = f"{last.strftime('%Y-%m')} vs {before.strftime('%Y-%m')}"
    end_of_month = last.end_time.normalize()
    return Windows(start, end_of_month, (end_of_month - start).days + 1, True, cur, prev, label)


def in_window(dates: pd.Series, bounds: tuple[pd.Timestamp, pd.Timestamp]) -> pd.Series:
    return (dates >= bounds[0]) & (dates <= bounds[1])
