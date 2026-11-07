"""Pure: timestamps on log lines, parsed in code.

Formats: ISO 8601 (`2026-11-02T10:32:00Z`, offsets), `2026-11-02 10:32`, `[10:32]`, `10:32 AM`, `10:32:15`,
`10:32 UTC`, chat copy headers (`Name  10:32 AM` on its own line, the messages below inherit it) and relative
`T+12m` / `+12 min`. A date carries forward from the nearest earlier full date; a time lower than the previous
one by more than ROLLOVER_HOURS bumps the day. Timezone labels are kept as written and never converted (an
explicit numeric offset is exact, so it becomes UTC). Durations exist only between times sharing one label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone

ROLLOVER_HOURS = 6
UNDATED = date(2000, 1, 1)
RELATIVE_LABEL = "T+"
UTC_LABELS = frozenset({"UTC", "GMT", "Z"})
TZ_LABELS = ("UTC", "GMT", "PST", "PDT", "EST", "EDT", "CST", "CDT", "MST", "MDT", "IST", "CET", "CEST", "BST",
             "EET", "EEST", "WET", "WEST", "AEST", "AEDT", "JST", "SGT", "HKT", "KST", "NZST", "NZDT", "AKST",
             "AKDT", "HST", "ET", "PT", "CT", "MT")
_TZ_ALT = "|".join(sorted(TZ_LABELS, key=len, reverse=True))
_LEAD = r"^\s*(?:[-*•>]\s*)?(?:\*\*)?[\[(]?\s*"
_CLOCK = (r"(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2})(?:\.\d+)?)?"
          r"(?:\s*(?P<ampm>[AaPp]\.?[Mm]\.?)(?![A-Za-z]))?"
          rf"(?:\s*(?P<tz>Z|[+-]\d{{2}}:?\d{{2}}|(?:{_TZ_ALT}))(?![A-Za-z]))?")
_STAMP = re.compile(_LEAD + r"(?:(?P<date>\d{4}-\d{2}-\d{2})(?:T|\s+))?" + _CLOCK + r"\s*[\])]?(?:\*\*)?")
_CLOCK_ONLY = re.compile(r"^\s*" + _CLOCK + r"\s*$")
_DATE_ONLY = re.compile(_LEAD + r"(?P<date>\d{4}-\d{2}-\d{2})\b")
_RELATIVE = re.compile(_LEAD + r"(?P<rel>T?\s*\+\s*(?P<n>\d{1,4})\s*(?P<u>minutes?|mins?|m|hours?|hrs?|h))\b"
                       r"\s*[\])]?")
_CHAT_HEADER = re.compile(r"^\s*(?P<sp>[A-Z][\w.'-]*(?: [A-Z][\w.'-]*){0,3})\s+"
                           r"(?P<t>\d{1,2}:\d{2}(?::\d{2})?\s*[AaPp]\.?[Mm]\.?)\s*$")
_SPEAKER = re.compile(r"^(?P<sp>@?[A-Za-z][\w.'@-]{0,30}(?: [A-Z][\w.'-]*){0,2})\s*:\s+(?P<text>\S.*)$")
_LEADING_PUNCT = " -–—:|*"
NOT_SPEAKERS = frozenset({"note", "notes", "update", "todo", "ai", "action", "action item", "follow up", "impact",
                          "status", "resolution", "root cause", "summary", "fyi", "re", "http", "https", "cause",
                          "timeline", "severity", "sev", "title", "incident", "service", "customer impact"})


@dataclass(frozen=True)
class Line:
    n: int
    raw: str
    text: str
    speaker: str = ""
    time_text: str = ""
    stamp: datetime | None = None
    tz: str = ""
    day: date = UNDATED
    dated: bool = False
    inherited: bool = False   # the time came from a chat header line above


def parse_clock(text: str, day: date = UNDATED) -> tuple[datetime, str] | None:
    """A bare time as written ("10:32", "10:32 PM", "10:32 UTC") on `day`, or None."""
    match = _CLOCK_ONLY.match(text or "")
    return _resolve(match, day) if match else None


def _resolve(match: re.Match, day: date) -> tuple[datetime, str] | None:
    hour, minute, second = int(match["h"]), int(match["m"]), int(match["s"] or 0)
    ampm = (match["ampm"] or "").replace(".", "").casefold()
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    stamp = datetime.combine(day, time(hour, minute, second))
    tz = match["tz"] or ""
    if tz and tz[0] in "+-":
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        return (stamp.replace(tzinfo=timezone(sign * offset)).astimezone(UTC).replace(tzinfo=None), "UTC")
    return stamp, ("UTC" if tz == "Z" else tz)


def _speaker(rest: str) -> tuple[str, str]:
    match = _SPEAKER.match(rest)
    if not match or match["sp"].casefold() in NOT_SPEAKERS:
        return "", rest
    return match["sp"], match["text"]


class _Parser:
    def __init__(self) -> None:
        self.current: date | None = None
        self.offset = 0
        self.prev: datetime | None = None
        self.anchor: tuple[datetime, str] | None = None
        self.header: Line | None = None

    @property
    def day(self) -> date:
        return (self.current or UNDATED) + timedelta(days=self.offset)

    def line(self, n: int, raw: str) -> Line:
        match = _STAMP.match(raw)
        if match:
            return self._stamped(n, raw, match)
        relative = _RELATIVE.match(raw)
        if relative:
            return self._relative(n, raw, relative)
        header = _CHAT_HEADER.match(raw)
        if header:
            resolved = parse_clock(header["t"], self.day)
            if resolved:
                self.header = self._timed(n, raw, "", header["sp"], header["t"].strip(), resolved, dated=False)
                return self.header
        date_only = _DATE_ONLY.match(raw)
        if date_only:
            self._set_date(date_only["date"])
        speaker, text = _speaker(raw.strip(_LEADING_PUNCT + " "))
        if self.header and raw.strip():
            h = self.header
            return Line(n, raw, text, speaker or h.speaker, h.time_text, h.stamp, h.tz, h.day, h.dated, True)
        return Line(n, raw, text, speaker, day=self.day, dated=self.current is not None)

    def _stamped(self, n: int, raw: str, match: re.Match) -> Line:
        explicit = bool(match["date"]) and self._set_date(match["date"])
        resolved = _resolve(match, self.day)
        if resolved is None:
            return Line(n, raw, raw.strip(), day=self.day, dated=self.current is not None)
        if not explicit and self.prev and resolved[0] < self.prev - timedelta(hours=ROLLOVER_HOURS):
            self.offset += 1
            resolved = _resolve(match, self.day)
        self.header = None
        speaker, text = _speaker(raw[match.end():].strip(_LEADING_PUNCT))
        time_text = raw[match.start("h") if not match["date"] else match.start("date"):match.end()].strip(" []()*")
        line = self._timed(n, raw, text, speaker, time_text, resolved, dated=self.current is not None)
        self.anchor = self.anchor or (line.stamp, line.tz)
        return line

    def _relative(self, n: int, raw: str, match: re.Match) -> Line:
        minutes = int(match["n"]) * (60 if match["u"].startswith("h") else 1)
        base, label = self.anchor or (datetime.combine(UNDATED, time(0, 0)), RELATIVE_LABEL)
        speaker, text = _speaker(raw[match.end():].strip(_LEADING_PUNCT))
        return Line(n, raw, text, speaker, match["rel"].strip(), base + timedelta(minutes=minutes), label,
                    base.date(), self.anchor is not None and self.current is not None)

    def _timed(self, n, raw, text, speaker, time_text, resolved, dated) -> Line:
        stamp, tz = resolved
        self.prev = stamp
        return Line(n, raw, text, speaker, time_text, stamp, tz, stamp.date(), dated)

    def _set_date(self, value: str) -> bool:
        try:
            self.current = date.fromisoformat(value)
        except ValueError:
            return False
        self.offset = 0
        return True


def parse_lines(text: str) -> tuple[Line, ...]:
    parser = _Parser()
    return tuple(parser.line(n, raw) for n, raw in enumerate((text or "").splitlines(), start=1))


def minutes_between(a: datetime | None, a_tz: str, b: datetime | None, b_tz: str) -> int | None:
    """Whole minutes from a to b, only when both exist and share one timezone label."""
    if a is None or b is None or a_tz != b_tz:
        return None
    return int((b - a).total_seconds() // 60)


def format_minutes(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def utc_iso(stamp: datetime | None, tz: str, dated: bool) -> str:
    return stamp.isoformat() + "Z" if stamp is not None and dated and tz in UTC_LABELS else ""


def speakers(lines: tuple[Line, ...]) -> tuple[str, ...]:
    return tuple(sorted({line.speaker.lstrip("@") for line in lines if line.speaker}))


def tz_labels(lines) -> frozenset[str]:
    return frozenset(line.tz for line in lines if line.stamp is not None)
