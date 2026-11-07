"""Pure: dates, times and timezones, all computed in code (the model is never asked to do date maths).

- which day the user asked about ("today", "tomorrow", a weekday), and whether earlier meetings count;
- the timezone, resolved in a fixed order (user-stated, the calendar's own, the events' offsets, the
  agent's DEFAULT_TIMEZONE, else UTC with a visible note);
- the window the calendar step reads, the local day the events are filtered to, and the evidence and
  prior-meeting windows, each given in every format a tool may want.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EVIDENCE_LOOKBACK_DAYS = 14
PRIOR_LOOKBACK_DAYS = 35
RECENT_DAYS = 7
# Tool timestamps may run a little ahead of our clock; anything later than this is not "recent".
FUTURE_SLACK = timedelta(minutes=5)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_ALL_OF_DAY = re.compile(r"\b(?:all (?:of )?(?:today|the day|day)|whole day|entire day|full day|"
                         r"including (?:earlier|past|the ones? (?:that )?already)|earlier (?:ones|meetings))\b",
                         re.IGNORECASE)
_TOMORROW = re.compile(r"\btomorrow\b", re.IGNORECASE)
_WEEKDAY = re.compile(rf"\b({'|'.join(_WEEKDAYS)})\b", re.IGNORECASE)
_IANA = re.compile(r"\b([A-Z][A-Za-z]+(?:/[A-Z][A-Za-z_\-]+){1,2}|UTC|GMT)\b")
# A small fixed map; ambiguous abbreviations resolve to the zone most users mean by them.
_ABBREVIATIONS = {
    "IST": "Asia/Kolkata", "UTC": "UTC", "GMT": "UTC", "BST": "Europe/London", "CET": "Europe/Paris",
    "CEST": "Europe/Paris", "EST": "America/New_York", "EDT": "America/New_York", "ET": "America/New_York",
    "CST": "America/Chicago", "CDT": "America/Chicago", "CT": "America/Chicago", "MST": "America/Denver",
    "MDT": "America/Denver", "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "PT": "America/Los_Angeles", "JST": "Asia/Tokyo", "SGT": "Asia/Singapore", "AEST": "Australia/Sydney",
    "AEDT": "Australia/Sydney",
}
_ABBREVIATION = re.compile(rf"\b({'|'.join(sorted(_ABBREVIATIONS, key=len, reverse=True))})\b")
_FOCUS = re.compile(r"\b(?:the |at |my )?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\b(?:the |at )(\d{1,2}):(\d{2})\b",
                    re.IGNORECASE)
_EPOCH = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")
_EPOCH_MS = re.compile(r"^\s*(\d{12,14})\s*$")
_DATE_ONLY = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")


@dataclass(frozen=True)
class DayRequest:
    """What the user asked for, parsed from their words."""

    offset_days: int = 0          # 0 = today, 1 = tomorrow, ...
    include_ended: bool = False   # "all of today": meetings that already ended count too
    focus: time | None = None     # "only the 3pm"
    stated_tz: str | None = None  # an IANA name the user gave ("in IST" -> Asia/Kolkata)


@dataclass(frozen=True)
class Zone:
    """The resolved timezone and where it came from (shown on the reply)."""

    tz: tzinfo
    name: str
    source: str  # "stated" | "calendar" | "events" | "default" | "assumed"

    @property
    def assumed(self) -> bool:
        return self.source == "assumed"


@dataclass(frozen=True)
class Bound:
    """One instant, in every format a tool may want. `day` is the user's local calendar date for date-only
    formats (an instant stored in UTC may fall on the previous UTC date)."""

    at: datetime
    day: date | None = None

    @property
    def iso_z(self) -> str:
        return self.at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def rfc3339(self) -> str:
        return self.at.astimezone(UTC).isoformat(timespec="seconds")

    @property
    def epoch(self) -> int:
        return int(self.at.timestamp())

    @property
    def iso_date(self) -> str:
        return (self.day or self.at.date()).isoformat()

    @property
    def search_after(self) -> str:
        return (self.day or self.at.date()).strftime("after:%Y/%m/%d")


@dataclass(frozen=True)
class Window:
    start: Bound
    end: Bound

    def contains(self, at: datetime) -> bool:
        return self.start.at <= at < self.end.at


def parse_request(text: str, now: datetime) -> DayRequest:
    lowered = text or ""
    offset = 1 if _TOMORROW.search(lowered) else _weekday_offset(lowered, now)
    return DayRequest(offset_days=offset, include_ended=bool(_ALL_OF_DAY.search(lowered)),
                      focus=parse_focus(lowered), stated_tz=stated_timezone(lowered))


def _weekday_offset(text: str, now: datetime) -> int:
    match = _WEEKDAY.search(text)
    if not match:
        return 0
    wanted = _WEEKDAYS.index(match.group(1).casefold())
    return (wanted - now.weekday()) % 7


def parse_focus(text: str) -> time | None:
    """"the 3pm", "4:30 pm", "the 14:00" -> a local time of day; None when no time is named."""
    match = _FOCUS.search(text or "")
    if not match:
        return None
    if match.group(1):
        hour, minute, half = int(match.group(1)), int(match.group(2) or 0), match.group(3).casefold()
        if not 1 <= hour <= 12 or minute > 59:
            return None
        return time((hour % 12) + (12 if half == "pm" else 0), minute)
    hour, minute = int(match.group(4)), int(match.group(5))
    return time(hour, minute) if hour <= 23 and minute <= 59 else None


def stated_timezone(text: str) -> str | None:
    """An IANA zone or a known abbreviation the user wrote, validated; else None."""
    for match in _IANA.finditer(text or ""):
        name = valid_zone_name(match.group(1))
        if name:
            return name
    abbreviation = _ABBREVIATION.search(text or "")
    return _ABBREVIATIONS[abbreviation.group(1)] if abbreviation else None


def valid_zone_name(name: str | None) -> str | None:
    candidate = (name or "").strip()
    if candidate in ("UTC", "GMT"):
        return "UTC"
    if not candidate or "/" not in candidate:
        return None
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return candidate


def resolve_zone(*, stated: str | None, calendar_tz: str | None, calendar_tz_seen: bool,
                 event_starts: Iterable[str], default_tz: str | None) -> Zone:
    """First hit wins: user-stated, the calendar's own (only if a tool returned it verbatim), the UTC
    offset most event start times carry, the agent's DEFAULT_TIMEZONE, else UTC (assumed, noted)."""
    if stated:
        return Zone(_zone(stated), stated, "stated")
    calendar = valid_zone_name(calendar_tz) if calendar_tz_seen else None
    if calendar:
        return Zone(_zone(calendar), calendar, "calendar")
    offset = majority_offset(event_starts)
    if offset is not None:
        return Zone(timezone(offset), _offset_name(offset), "events")
    default = valid_zone_name(default_tz)
    if default:
        return Zone(_zone(default), default, "default")
    return Zone(UTC, "UTC", "assumed")


def _zone(name: str) -> tzinfo:
    return UTC if name == "UTC" else ZoneInfo(name)


def majority_offset(starts: Iterable[str]) -> timedelta | None:
    """The offset most explicit (non-"Z") start times carry; None when none carries one."""
    offsets = Counter()
    for text in starts:
        raw = (text or "").strip()
        if not raw or raw.endswith(("Z", "z")) or _DATE_ONLY.match(raw):
            continue
        parsed = _fromiso(raw)
        if parsed is not None and parsed.tzinfo is not None:
            offsets[parsed.utcoffset()] += 1
    return offsets.most_common(1)[0][0] if offsets else None


def _offset_name(offset: timedelta) -> str:
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _fromiso(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.strip().replace(" ", "T", 1))
    except ValueError:
        return None


def parse_instant(value: object, tz: tzinfo) -> datetime | None:
    """An ISO date-time (naive ones read in `tz`) or epoch seconds/milliseconds, as an aware datetime.
    A bare date is not an instant (all-day events) and gives None."""
    text = str(value or "").strip()
    if not text or _DATE_ONLY.match(text):
        return None
    epoch = _EPOCH.match(text)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=UTC)
    epoch_ms = _EPOCH_MS.match(text)
    if epoch_ms:
        return datetime.fromtimestamp(int(epoch_ms.group(1)) / 1000, tz=UTC)
    parsed = _fromiso(text)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=tz)


def is_all_day(start: object) -> bool:
    return bool(_DATE_ONLY.match(str(start or "")))


def target_date(now: datetime, tz: tzinfo, offset_days: int) -> date:
    return now.astimezone(tz).date() + timedelta(days=offset_days)


def local_day(day: date, tz: tzinfo) -> Window:
    """[00:00, 24:00) of `day` in `tz`; DST-correct (a 23- or 25-hour day stays a calendar day)."""
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return Window(Bound(start), Bound(end))  # UTC instants, so arithmetic and comparisons are real time


def read_window(now: datetime, request: DayRequest) -> Window:
    """What the calendar step reads, before the timezone is known.

    With a user-stated zone it is that local day exactly. Otherwise it is anchored on `now`: whatever
    the user's zone, their "today" lies inside (now - 24 h, now + 24 h), so this band (shifted by the
    requested day) always contains the right local day; events outside it are dropped in code later.
    """
    if request.stated_tz:
        tz = _zone(request.stated_tz)
        return local_day(target_date(now, tz, request.offset_days), tz)
    shift = timedelta(days=request.offset_days)
    base = now.astimezone(UTC).replace(second=0, microsecond=0)
    return Window(Bound(base - timedelta(hours=24) + shift), Bound(base + timedelta(hours=24) + shift))


def evidence_window(day: date, tz: tzinfo, now: datetime) -> Window:
    """From the local day EVIDENCE_LOOKBACK_DAYS before the meeting day, up to now."""
    since_day = day - timedelta(days=EVIDENCE_LOOKBACK_DAYS)
    since = datetime.combine(since_day, time.min, tzinfo=tz).astimezone(UTC)
    return Window(Bound(since, since_day), Bound(now + FUTURE_SLACK))


def prior_window(meeting_start: datetime) -> Window:
    """Where the previous occurrence of a meeting is looked for: 35 days back, up to a minute before it."""
    return Window(Bound(meeting_start - timedelta(days=PRIOR_LOOKBACK_DAYS)),
                  Bound(meeting_start - timedelta(minutes=1)))


def short_date(at: datetime, tz: tzinfo) -> str:
    local = at.astimezone(tz)
    return f"{local.day} {local.strftime('%b')}"


def clock(at: datetime, tz: tzinfo) -> str:
    return at.astimezone(tz).strftime("%H:%M")


def day_label(day: date) -> str:
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b %Y')}"


def offset_minutes(zone: Zone) -> int | None:
    """The fixed offset of an offset-only zone (to store it); None for a named zone."""
    if zone.source != "events":
        return None
    offset = zone.tz.utcoffset(None)
    return int(offset.total_seconds() // 60) if offset is not None else 0


def restore_zone(name: str, source: str, minutes: int | None) -> Zone:
    """A Zone stored by `offset_minutes` and its name; an unknown name falls back to UTC."""
    if source == "events" and minutes is not None:
        return Zone(timezone(timedelta(minutes=minutes)), name, source)
    valid = valid_zone_name(name)
    return Zone(_zone(valid), valid, source) if valid else Zone(UTC, "UTC", "assumed")
