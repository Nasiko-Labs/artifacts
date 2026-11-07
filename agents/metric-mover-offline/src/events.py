"""Pure: events the user lists ("2026-10-14 deploy: new checkout", "Oct 12 campaign ended",
"Add event: Oct 14 deploy …"), the comparison window, and whether an event falls inside it.

The window is known only when the period labels are dates (long layout, or date-named columns): from the
before date to the end of the after period (periods assumed consecutive and of equal length). Without a
window every listed event is used, and the reply says so. Event text is kept verbatim; it is data."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from columns import iso_date

MAX_EVENTS = 10
MAX_EVENT_CHARS = 200
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), start=1)}
_PREFIX = re.compile(r"^\s*(?:[-*•]\s*)?(?:add(?:ed)?\s+(?:an?\s+)?event\s*[:\-]?\s*|event\s*[:\-]\s*)",
                     re.IGNORECASE)
_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})\b")
_MON_DAY = re.compile(r"^([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s*(\d{4}))?")
_DAY_MON = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})[a-z]*\.?\b(?:\s+(\d{4}))?")
_ADD_EVENT = re.compile(r"^\s*add(?:ed)?\s+(?:an?\s+)?event\b", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Event:
    id: str
    text: str             # the line as written (without an "add event:" prefix)
    when: str = ""        # ISO date when known
    date_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Event:
        return Event(str(data["id"]), str(data["text"]), str(data.get("when") or ""), str(data.get("date_text") or ""))


def is_add_event(text: str) -> bool:
    return bool(_ADD_EVENT.match(text or ""))


def parse(text: str, first_id: int = 1, year: int | None = None) -> list[Event]:
    """Event lines: a line that starts with a date, or any line after an "add event" prefix."""
    out: list[Event] = []
    for line in (text or "").splitlines():
        prefixed = bool(_PREFIX.match(line))
        body = _PREFIX.sub("", line).strip()[:MAX_EVENT_CHARS]
        found = _date(body, year)
        if not body or (found is None and not prefixed):
            continue
        when, date_text = found or ("", "")
        out.append(Event(f"ev{first_id + len(out)}", body, when, date_text))
        if len(out) >= MAX_EVENTS:
            break
    return out


def _date(body: str, year: int | None) -> tuple[str, str] | None:
    match = _ISO.match(body)
    if match:
        parsed = iso_date(match.group(1))
        return (parsed.isoformat() if parsed else "", match.group(1))
    for pattern, month_group, day_group in ((_MON_DAY, 1, 2), (_DAY_MON, 2, 1)):
        match = pattern.match(body)
        month = MONTHS.get(match.group(month_group)[:3].casefold()) if match else None
        if match and month:
            given = int(match.group(3)) if match.group(3) else year
            return (_safe(given, month, int(match.group(day_group))), match.group(0).strip())
    return None


def _safe(year: int | None, month: int, day: int) -> str:
    if year is None:
        return ""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def window(labels: tuple[str, str]) -> tuple[date, date] | None:
    start, after = iso_date(labels[0]), iso_date(labels[1])
    if not start or not after or after <= start:
        return None
    return start, after + (after - start) - timedelta(days=1)


def in_window(event: Event, span: tuple[date, date] | None) -> bool:
    """True when the event's date falls in the window, or when either is unknown."""
    when = iso_date(event.when)
    if span is None or when is None:
        return True
    return span[0] <= when <= span[1]


def named_segments(event: Event, segments: set[str]) -> list[str]:
    """Segment values the event text names as whole words (case-insensitive)."""
    words = " " + " ".join(_WORD.findall(event.text.casefold())) + " "
    found = []
    for name in sorted(segments):
        tokens = " ".join(_WORD.findall(name.casefold()))
        if tokens and f" {tokens} " in words:
            found.append(name)
    return found
