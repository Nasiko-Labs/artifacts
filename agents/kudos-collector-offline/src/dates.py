"""Pure: dates found in record text, the Period option, and the short date shown in the reply. All date work
happens here, in code; the model never sees a date it has to compute with."""

from __future__ import annotations

import re
from datetime import date

MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_MON = r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
ISO = re.compile(r"\b(?P<y>20\d{2})[-/](?P<m>\d{1,2})[-/](?P<d>\d{1,2})(?:[T ][\d:.]+Z?)?\b")
MON_DAY = re.compile(rf"\b{_MON}\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<y>20\d{{2}}))?\b", re.IGNORECASE)
DAY_MON = re.compile(rf"\b(?P<d>\d{{1,2}})\s+{_MON}(?:\s+(?P<y>20\d{{2}}))?\b", re.IGNORECASE)
QUARTER = re.compile(r"^\s*(?:(?P<y1>20\d{2})[\s-]*)?q(?P<q>[1-4])(?:[\s-]*(?P<y2>20\d{2}))?\s*$", re.IGNORECASE)
RANGE = re.compile(r"^\s*(?P<a>\S+)\s*(?:to|-|–|—|\.\.)\s*(?P<b>\S+)\s*$", re.IGNORECASE)
QUARTER_STARTS = {1: (1, 3, 31), 2: (4, 6, 30), 3: (7, 9, 30), 4: (10, 12, 31)}


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find(text: str, today: date) -> tuple[str | None, tuple[int, int] | None]:
    """(ISO date, (start, end) span in text) of the first date in the text. A month-day without a year
    takes today's year, or last year when that would be in the future."""
    for pattern in (ISO, MON_DAY, DAY_MON):
        match = pattern.search(text or "")
        if not match:
            continue
        g = match.groupdict()
        month = int(g["m"]) if "m" in g and g.get("m") else MONTHS[g["mon"][:3].casefold()]
        year = int(g["y"]) if g.get("y") else today.year
        found = _safe(year, month, int(g["d"]))
        if found and not g.get("y") and found > today:
            found = _safe(year - 1, month, int(g["d"]))
        if found:
            return found.isoformat(), match.span()
    return None, None


def parse_period(value: str, today: date, years: list[int]) -> tuple[str, str] | None:
    """`2026-07-01 to 2026-09-30`, `Q3`, `Q3 2026`, `2026-Q3`. A bare quarter takes the latest year seen in
    the records, else today's year."""
    q = QUARTER.match(value or "")
    if q:
        year = int(q["y1"] or q["y2"] or (max(years) if years else today.year))
        m1, m2, d2 = QUARTER_STARTS[int(q["q"])]
        return date(year, m1, 1).isoformat(), date(year, m2, d2).isoformat()
    r = RANGE.match(value or "")
    if not r:
        return None
    a, _ = find(r["a"], today)
    b, _ = find(r["b"], today)
    return (min(a, b), max(a, b)) if a and b else None


def short(iso: str | None) -> str:
    if not iso:
        return "undated"
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}, {d.year}"
