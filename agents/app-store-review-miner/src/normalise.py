"""Pure: rating, date and store normalisers for one review field."""

from __future__ import annotations

import re
from datetime import date, datetime

_STAR_GLYPHS = "★⭐"
_RATING = re.compile(r"^\s*(\d(?:\.0+)?)\s*(?:/\s*5|out of 5|stars?|\*)?\s*$", re.IGNORECASE)
_ISO = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_MONTH_FIRST = re.compile(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})")
_DAY_FIRST = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?,?\s+(\d{4})")
_IOS = ("ios", "iphone", "ipad", "app store", "apple")
_ANDROID = ("android", "google play", "play store", "play")


def rating(raw: object) -> int | None:
    """`4`, `4/5`, `4 stars`, `4.0`, `★★★★☆` → 4. Anything else (or outside 1-5) → None."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    stars = sum(text.count(g) for g in _STAR_GLYPHS)
    if stars:
        return stars if 1 <= stars <= 5 else None
    match = _RATING.match(text)
    if not match:
        return None
    value = int(float(match.group(1)))
    return value if 1 <= value <= 5 else None


def iso_date(raw: object) -> str | None:
    """ISO / `yyyy/mm/dd`, `Oct 6, 2026`, `6 Oct 2026` → `2026-10-06`. Ambiguous `06/10/2026` → None."""
    text = str(raw or "").strip()
    found = _ISO.search(text)
    if found:
        return _safe(int(found.group(1)), int(found.group(2)), int(found.group(3)))
    found = _MONTH_FIRST.search(text)
    if found and found.group(1).casefold() in _MONTHS:
        return _safe(int(found.group(3)), _MONTHS[found.group(1).casefold()], int(found.group(2)))
    found = _DAY_FIRST.search(text)
    if found and found.group(2).casefold() in _MONTHS:
        return _safe(int(found.group(3)), _MONTHS[found.group(2).casefold()], int(found.group(1)))
    return None


def to_date(iso: str | None) -> date | None:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date() if iso else None
    except ValueError:
        return None


def store(raw: object) -> str | None:
    text = str(raw or "").strip().casefold()
    if not text:
        return None
    if any(word in text for word in _IOS):
        return "ios"
    if any(word in text for word in _ANDROID):
        return "android"
    return None


def _safe(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None
