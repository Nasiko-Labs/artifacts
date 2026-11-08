"""Pure: dates in code. The launch date from the user's words (relative to the turn's clock), and the dates
tools return (ISO, ISO date-time, epoch seconds or ms, "Nov 18 2026"). Unparseable -> None, never a guess."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}
_MONTH = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|" \
         r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_ISO = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
_MONTH_DAY = re.compile(rf"\b{_MONTH}\.? (?P<day>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,? (?P<year>\d{{4}}))?", re.IGNORECASE)
_DAY_MONTH = re.compile(rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)? {_MONTH}\b\.?(?:,? (?P<year>\d{{4}}))?", re.IGNORECASE)
_WEEKDAY = re.compile(rf"\b(?P<rel>next |this |on )?(?P<wd>{'|'.join(_WEEKDAYS)})\b", re.IGNORECASE)
_IN_N = re.compile(r"\bin (?P<n>\d{1,3}) (?P<unit>days?|weeks?)\b", re.IGNORECASE)
_WORD_DAY = re.compile(r"\b(?P<w>today|tomorrow)\b", re.IGNORECASE)
_ISO_PREFIX = re.compile(r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?:$|[T ])")
_EPOCH_MS = 10_000_000_000


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _month_day(match: re.Match, today: date) -> date | None:
    month, day = _MONTHS[match["mon"][:3].casefold()], int(match["day"])
    if match["year"]:
        return _safe(int(match["year"]), month, day)
    found = _safe(today.year, month, day)
    return _safe(today.year + 1, month, day) if found and found < today else found


def _weekday(match: re.Match, today: date) -> date:
    ahead = (_WEEKDAYS.index(match["wd"].casefold()) - today.weekday()) % 7
    rel = (match["rel"] or "").strip().casefold()
    if ahead == 0 and rel != "this":  # "next Friday" / "on Friday" on a Friday means a week out
        ahead = 7
    return today + timedelta(days=ahead)


def launch_date(text: str, today: date) -> tuple[date | None, str]:
    """(the first date the message names, the matched words) or (None, "")."""
    finders = (
        (_ISO, lambda m: _safe(int(m["y"]), int(m["m"]), int(m["d"]))),
        (_MONTH_DAY, lambda m: _month_day(m, today)),
        (_DAY_MONTH, lambda m: _month_day(m, today)),
        (_WORD_DAY, lambda m: today + timedelta(days=0 if m["w"].casefold() == "today" else 1)),
        (_IN_N, lambda m: today + timedelta(days=int(m["n"]) * (7 if m["unit"].startswith("week") else 1))),
        (_WEEKDAY, lambda m: _weekday(m, today)),
    )
    hits = [(m.start(), m, fn) for rx, fn in finders for m in [rx.search(text)] if m]
    for _, match, fn in sorted(hits, key=lambda h: h[0]):
        found = fn(match)
        if found is not None:
            return found, match.group(0)
    return None, ""


def tool_date(value: object) -> date | None:
    """A date as a tool gives it; None when it can't be read without guessing."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > _EPOCH_MS else value
        try:
            return datetime.fromtimestamp(seconds, UTC).date()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        return tool_date(int(text))
    iso = _ISO_PREFIX.match(text)
    if iso:
        return _safe(int(iso["y"]), int(iso["m"]), int(iso["d"]))
    named = _MONTH_DAY.search(text) or _DAY_MONTH.search(text)
    if named and named["year"]:
        return _safe(int(named["year"]), _MONTHS[named["mon"][:3].casefold()], int(named["day"]))
    return None
