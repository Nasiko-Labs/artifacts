"""Pure time maths: parse the incident start, build the windows, format their values for prompts.

Every time the model or a prompt sees is computed here; the model is told never to compute one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

MIN = timedelta(minutes=1)
OBS_BEFORE = 60 * MIN
OBS_AFTER = 30 * MIN
MAX_OBS_BEFORE = 6 * 60 * MIN
MIN_OBS_BEFORE = 15 * MIN
HOT = 15 * MIN
CHANGE_BEFORE = 24 * 60 * MIN
PAST_BEFORE = timedelta(days=180)
PAST_GAP = 60 * MIN

_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)\s*(Z|UTC|[+-]\d{2}:?\d{2})?",
                  re.IGNORECASE)
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\s*(UTC|GMT|Z|[+-]\d{2}:?\d{2})(?![\w:])", re.IGNORECASE)
_AGO = re.compile(r"\b(\d{1,4})\s*(minutes?|mins?|m|hours?|hrs?|h)\s+ago\b", re.IGNORECASE)
_LOOKBACK = re.compile(r"\blast\s+(\d{1,3})\s*(minutes?|mins?|hours?|hrs?|h)\b", re.IGNORECASE)
_EPOCH_SECS = (1e9, 1e10)
_EPOCH_MS = (1e12, 1e13)


def parse_time(value: Any) -> datetime | None:
    """An ISO 8601 string, Unix seconds or Unix milliseconds (number or numeric string) as aware UTC."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return _from_epoch(float(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _from_epoch(number: float) -> datetime | None:
    if _EPOCH_SECS[0] <= number < _EPOCH_SECS[1]:
        return datetime.fromtimestamp(number, UTC)
    if _EPOCH_MS[0] <= number < _EPOCH_MS[1]:
        return datetime.fromtimestamp(number / 1000, UTC)
    return None


def parse_start(text: str, now: datetime) -> datetime | None:
    """The start time a user wrote: an ISO time, `HH:MM` with UTC or an offset, or "N minutes/hours ago".
    A time later than now is refused (a clock time later today means yesterday)."""
    iso = _ISO.search(text or "")
    if iso:
        zone = (iso.group(3) or "Z").upper().replace("UTC", "Z")
        found = parse_time(f"{iso.group(1)}T{iso.group(2)}{'' if zone == 'Z' else zone}")
        return found if found and found <= now else None
    clock = _CLOCK.search(text or "")
    if clock:
        return _clock_time(int(clock.group(1)), int(clock.group(2)), clock.group(3), now)
    ago = _AGO.search(text or "")
    if ago:
        amount = int(ago.group(1))
        return now - (amount * 60 * MIN if ago.group(2).lower().startswith("h") else amount * MIN)
    return None


def _clock_time(hour: int, minute: int, zone: str, now: datetime) -> datetime | None:
    if hour > 23 or minute > 59:
        return None
    offset = timedelta(0)
    if zone.upper() not in ("UTC", "GMT", "Z"):
        sign = -1 if zone.startswith("-") else 1
        digits = zone[1:].replace(":", "")
        offset = sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:] or 0))
    local_now = now + offset
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0) - offset
    return candidate if candidate <= now else candidate - timedelta(days=1)


def parse_lookback(text: str) -> timedelta | None:
    """"last 2 hours" widens the observability window before the start, clamped to 15 min-6 h."""
    match = _LOOKBACK.search(text or "")
    if not match:
        return None
    amount = int(match.group(1))
    span = amount * 60 * MIN if match.group(2).lower().startswith("h") else amount * MIN
    return min(max(span, MIN_OBS_BEFORE), MAX_OBS_BEFORE)


@dataclass(frozen=True)
class Window:
    name: str
    start: datetime
    end: datetime

    def contains(self, moment: datetime | None) -> bool:
        return moment is not None and self.start <= moment <= self.end

    @property
    def minutes(self) -> float:
        return max((self.end - self.start).total_seconds() / 60, 1e-9)


@dataclass(frozen=True)
class Windows:
    t0: datetime
    now: datetime
    obs: Window
    base: Window
    hot: Window
    change: Window
    err24: Window
    past: Window


def windows(t0: datetime, now: datetime, lookback: timedelta | None = None) -> Windows:
    before = lookback or OBS_BEFORE
    return Windows(
        t0=t0, now=now,
        obs=Window("observability", t0 - before, min(now, t0 + OBS_AFTER)),
        base=Window("baseline", t0 - OBS_BEFORE, t0 - HOT),
        hot=Window("around start", t0 - HOT, t0 + HOT),
        change=Window("changes", t0 - CHANGE_BEFORE, t0 + HOT),
        err24=Window("error baseline", t0 - CHANGE_BEFORE, t0 - HOT),
        past=Window("history", t0 - PAST_BEFORE, t0 - PAST_GAP),
    )


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def describe(window: Window) -> str:
    """Every format a tool may want, for one window. Passed verbatim into prompts."""
    start, end = window.start.astimezone(UTC), window.end.astimezone(UTC)
    return (f"{window.name} window: from {iso(start)} to {iso(end)} | Unix seconds {int(start.timestamp())} to "
            f"{int(end.timestamp())} | Unix milliseconds {int(start.timestamp() * 1000)} to "
            f"{int(end.timestamp() * 1000)} | dates {start.date().isoformat()} to {end.date().isoformat()}")


def minutes_between(earlier: datetime, later: datetime) -> float:
    return (later - earlier).total_seconds() / 60
