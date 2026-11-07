"""Pure: the `Fire` record every parser produces, and the value parsers they share (times with their own
offsets, durations, severities). Naive times are read as UTC for arithmetic; an aware time keeps its own
offset, so the off-hours share uses the hour the responder saw."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime

SHORT_SECS = 300
PAGING = frozenset({"page", "paging", "critical", "crit", "high", "p1", "p2", "sev1", "sev2", "sev-1", "sev-2",
                    "emergency", "urgent", "disaster"})
RESOLVED = frozenset({"resolved", "closed", "ok", "recovered", "auto-resolved", "auto_resolved", "cleared"})
ACKED = frozenset({"acknowledged", "acked", "ack"})
NULL_TIMES = ("0001-01-01",)
SELECTED_LABELS = ("env", "environment", "cluster")
_OFFSET = re.compile(r"([+-]\d{2})(\d{2})$")
_TZ_NAME = re.compile(r"\s*(?:UTC|GMT)$", re.IGNORECASE)
_EPOCH = re.compile(r"^\d{10}(?:\d{3})?(?:\.\d+)?$")
_FORMATS = ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%d %b %Y %H:%M",
            "%b %d, %Y %H:%M", "%b %d %Y %H:%M")
_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hours?|m|min|mins|minutes?|s|sec|secs|seconds?)\b", re.IGNORECASE)
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class Fire:
    row: int                       # 1-based row index in the paste
    name: str
    t_fire: datetime
    service: str = ""
    severity: str = ""
    labels: tuple[tuple[str, str], ...] = ()   # selected labels (env, cluster): part of the group key
    instance: str = ""             # host / pod / instance: never part of the group key
    t_resolve: datetime | None = None
    duration_s: float | None = None
    acked: bool = False
    acked_by: str = ""
    note: str = ""
    status: str = ""
    value: float | None = None
    threshold: float | None = None
    raw: str = ""                  # the source row, for evidence

    @property
    def is_resolved(self) -> bool:
        return self.t_resolve is not None or self.duration_s is not None or self.status in RESOLVED

    @property
    def is_page(self) -> bool:
        return is_paging(self.severity)


def is_paging(severity: str) -> bool:
    return severity.casefold().strip() in PAGING


def parse_time(text: str | None) -> datetime | None:
    value = (text or "").strip().strip('"')
    if not value or value.startswith(NULL_TIMES):
        return None
    if _EPOCH.match(value):
        seconds = float(value) / (1000 if len(value.split(".")[0]) == 13 else 1)
        return datetime.fromtimestamp(seconds, UTC)
    value = _OFFSET.sub(r"\1:\2", _TZ_NAME.sub("Z", value).replace(" Z", "Z"))
    parsed = _iso(value) or _formatted(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _formatted(value: str) -> datetime | None:
    for fmt in _FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def parse_duration(text: str | None, bare_unit_secs: float = 1.0) -> float | None:
    """'3m', '2h 5m', '00:03:00', '180' (bare numbers in `bare_unit_secs`), '3.5 minutes'."""
    value = (text or "").strip().strip('"')
    if not value:
        return None
    if _NUMBER.match(value):
        return float(value) * bare_unit_secs
    clock = _CLOCK.match(value)
    if clock:
        h_or_m, m_or_s, s = clock.groups()
        if s is None:
            return int(h_or_m) * 60 + int(m_or_s)
        return int(h_or_m) * 3600 + int(m_or_s) * 60 + int(s)
    parts = _UNIT.findall(value)
    if not parts:
        return None
    total = 0.0
    for amount, unit in parts:
        u = unit.casefold()
        total += float(amount) * (3600 if u.startswith("h") else 60 if u.startswith("m") else 1)
    return total


def finish(fire: Fire) -> Fire:
    """Derive the duration from the two times when only they are given."""
    if fire.duration_s is None and fire.t_resolve is not None:
        delta = (fire.t_resolve - fire.t_fire).total_seconds()
        if delta >= 0:
            return replace(fire, duration_s=delta)
    return fire


def to_float(text: object) -> float | None:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def fmt_dur(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{round(seconds)}s"
    if seconds < 3600:
        return f"{_trim(seconds / 60)} min"
    return f"{_trim(seconds / 3600)} h"


def _trim(value: float) -> str:
    return f"{value:.1f}".removesuffix(".0")


def fmt_time(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M")
