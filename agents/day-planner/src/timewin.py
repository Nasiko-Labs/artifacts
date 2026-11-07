"""Pure: instants, zones and windows, all computed in code (the model never does date maths).

Adapted from the meeting-prep agent's time module; owned here.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_IANA = re.compile(r"\b([A-Z][A-Za-z]+(?:/[A-Z][A-Za-z_\-]+){1,2}|UTC|GMT)\b")
# A small fixed map; ambiguous abbreviations resolve to the zone most users mean by them.
_ABBREVIATIONS = {
    "IST": "Asia/Kolkata", "UTC": "UTC", "GMT": "UTC", "BST": "Europe/London", "CET": "Europe/Paris",
    "CEST": "Europe/Paris", "EST": "America/New_York", "EDT": "America/New_York", "ET": "America/New_York",
    "CST": "America/Chicago", "CDT": "America/Chicago", "MST": "America/Denver", "MDT": "America/Denver",
    "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles", "PT": "America/Los_Angeles",
    "JST": "Asia/Tokyo", "SGT": "Asia/Singapore", "AEST": "Australia/Sydney", "AEDT": "Australia/Sydney",
}
_ABBREVIATION = re.compile(rf"\b({'|'.join(sorted(_ABBREVIATIONS, key=len, reverse=True))})\b")
_EPOCH = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")
_EPOCH_MS = re.compile(r"^\s*(\d{12,14})\s*$")
_DATE_ONLY = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")


@dataclass(frozen=True)
class Zone:
    """The resolved timezone and where it came from: stated | stored | calendar | events | pasted."""

    tz: tzinfo
    name: str
    source: str

    def to_dict(self) -> dict:
        offset = self.tz.utcoffset(None) if self.source == "events" else None
        minutes = int(offset.total_seconds() // 60) if offset is not None else None
        return {"name": self.name, "source": self.source, "offset_minutes": minutes}

    @staticmethod
    def from_dict(data: dict) -> Zone | None:
        minutes = data.get("offset_minutes")
        if minutes is not None:
            return Zone(timezone(timedelta(minutes=int(minutes))), str(data.get("name") or "UTC"), "events")
        name = valid_zone_name(data.get("name"))
        return Zone(zone_of(name), name, str(data.get("source") or "stored")) if name else None


@dataclass(frozen=True)
class Window:
    """[start, end) as UTC instants."""

    start: datetime
    end: datetime

    def contains(self, at: datetime) -> bool:
        return self.start <= at < self.end

    @property
    def iso_z(self) -> tuple[str, str]:
        return tuple(b.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") for b in (self.start, self.end))

    @property
    def rfc3339(self) -> tuple[str, str]:
        return tuple(b.astimezone(UTC).isoformat(timespec="seconds") for b in (self.start, self.end))


def zone_of(name: str) -> tzinfo:
    return UTC if name == "UTC" else ZoneInfo(name)


def valid_zone_name(name: object) -> str | None:
    candidate = str(name or "").strip()
    if candidate in ("UTC", "GMT", "Etc/UTC"):
        return "UTC"
    if not candidate or "/" not in candidate:
        return None
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return candidate


def stated_timezone(text: str) -> str | None:
    """An IANA zone or a known abbreviation the user wrote, validated; else None."""
    for match in _IANA.finditer(text or ""):
        name = valid_zone_name(match.group(1))
        if name:
            return name
    abbreviation = _ABBREVIATION.search(text or "")
    return _ABBREVIATIONS[abbreviation.group(1)] if abbreviation else None


def majority_offset(starts: Iterable[str]) -> timedelta | None:
    """The offset most explicit (non-"Z") start times carry; None when none carries one."""
    offsets: Counter = Counter()
    for text in starts:
        raw = (text or "").strip()
        if not raw or raw.endswith(("Z", "z")) or _DATE_ONLY.match(raw):
            continue
        parsed = _fromiso(raw)
        if parsed is not None and parsed.tzinfo is not None:
            offsets[parsed.utcoffset()] += 1
    return offsets.most_common(1)[0][0] if offsets else None


def offset_zone(offset: timedelta) -> Zone:
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return Zone(timezone(offset), f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}", "events")


def _fromiso(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.strip().replace(" ", "T", 1).replace("z", "Z"))
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


def parse_date(value: object) -> date | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def is_all_day(start: object) -> bool:
    return bool(_DATE_ONLY.match(str(start or "")))


def has_offset(value: object) -> bool:
    parsed = _fromiso(str(value or ""))
    return parsed is not None and parsed.tzinfo is not None


def at_local(day: date, clock: time, tz: tzinfo) -> datetime:
    return datetime.combine(day, clock, tzinfo=tz).astimezone(UTC)


def local_days(days: Iterable[date], tz: tzinfo) -> Window:
    """[00:00 of the first day, 24:00 of the last day) in `tz`, DST-correct."""
    ordered = sorted(days)
    return Window(at_local(ordered[0], time.min, tz), at_local(ordered[-1] + timedelta(days=1), time.min, tz))


def band(days: Iterable[date]) -> Window:
    """The read window before the zone is known: every zone's local version of these days lies
    inside (first day 00:00 UTC - 24 h, last day 24:00 UTC + 24 h), even when the local date differs from
    the UTC date; events outside the local days are
    dropped in code once the zone is known."""
    utc = local_days(days, UTC)
    return Window(utc.start - timedelta(hours=24), utc.end + timedelta(hours=24))


def clock(at: datetime, tz: tzinfo) -> str:
    return at.astimezone(tz).strftime("%H:%M")


def day_label(day: date) -> str:
    return f"{day.strftime('%A')} {day.day} {day.strftime('%b')}"


def duration(minutes: int) -> str:
    """135 -> "2h15", 45 -> "45m", 120 -> "2h00"."""
    hours, rest = divmod(max(0, int(minutes)), 60)
    return f"{hours}h{rest:02d}" if hours else f"{rest}m"
