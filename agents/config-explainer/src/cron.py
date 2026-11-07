"""Pure: parsing a cron schedule (5-field Vixie/Kubernetes/GitHub Actions, macros, CRON_TZ/TZ prefix, AWS
EventBridge `cron(...)`) and the one definition of when it runs, `matches()`. Run times are found by
scanning with that definition, so the code cannot disagree with itself. No dependency."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
DAYS = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")
MACROS = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *", "@weekly": "0 0 * * 0",
          "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *"}
FIELD_NAMES = ("minute", "hour", "day-of-month", "month", "day-of-week")
# (low, high) per field; day-of-week accepts 7 (= Sunday) in the 5-field format.
BOUNDS = {"minute": (0, 59), "hour": (0, 23), "day-of-month": (1, 31), "month": (1, 12), "day-of-week": (0, 7),
          "year": (1970, 2199)}

_TZ_PREFIX = re.compile(r"^\s*(?:CRON_TZ|TZ)=(\S+)\s+", re.IGNORECASE)
_AWS = re.compile(r"^\s*cron\((.*)\)\s*$", re.IGNORECASE | re.DOTALL)
_QUARTZ_CHARS = re.compile(r"[LW#]")
_ITEM = re.compile(r"^(\*|\?|[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?)(?:/(\d+))?$")


class CronError(ValueError):
    """The text is not a cron schedule this parser accepts."""


@dataclass(frozen=True)
class CronSpec:
    raw: str
    fields: tuple[str, ...]                 # minute hour dom month dow (+ year for AWS)
    minutes: frozenset[int] = frozenset()
    hours: frozenset[int] = frozenset()
    doms: frozenset[int] = frozenset()
    months: frozenset[int] = frozenset()
    dows: frozenset[int] = frozenset()      # 0 = Sunday
    years: frozenset[int] | None = None
    dom_star: bool = True
    dow_star: bool = True
    aws: bool = False
    tz: str | None = None
    macro: str | None = None
    reboot: bool = False
    quartz: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def or_rule(self) -> bool:
        """Vixie rule: when both day fields are restricted, a day matches when EITHER matches."""
        return not self.aws and not self.dom_star and not self.dow_star


def parse(text: str) -> CronSpec:
    raw = " ".join((text or "").split())
    tz = None
    prefix = _TZ_PREFIX.match(raw)
    if prefix:
        tz, raw_body = prefix.group(1), raw[prefix.end():]
    else:
        raw_body = raw
    lowered = raw_body.strip().casefold()
    if lowered == "@reboot":
        return CronSpec(raw=raw, fields=(), reboot=True, tz=tz, macro="@reboot")
    if lowered in MACROS:
        spec = _parse_fields(MACROS[lowered].split(), raw, tz, aws=False)
        return _with(spec, macro=lowered)
    aws = _AWS.match(raw_body)
    if aws:
        parts = aws.group(1).split()
        if len(parts) != 6:
            raise CronError(f"AWS cron(...) needs 6 fields (minute hour day-of-month month day-of-week year); "
                            f"got {len(parts)}")
        return _parse_fields(parts, raw, tz, aws=True)
    parts = raw_body.split()
    if len(parts) in (6, 7) or (len(parts) == 5 and _QUARTZ_CHARS.search(raw_body)):
        return CronSpec(raw=raw, fields=tuple(parts), tz=tz, quartz=True)
    if len(parts) != 5:
        raise CronError(f"a cron schedule has 5 fields; this has {len(parts)}")
    return _parse_fields(parts, raw, tz, aws=False)


def _with(spec: CronSpec, **changes) -> CronSpec:
    data = {name: getattr(spec, name) for name in spec.__dataclass_fields__}
    return CronSpec(**{**data, **changes})


def _parse_fields(parts: list[str], raw: str, tz: str | None, aws: bool) -> CronSpec:
    if _QUARTZ_CHARS.search(" ".join(parts)):
        return CronSpec(raw=raw, fields=tuple(parts), tz=tz, aws=aws, quartz=True)
    minute, hour, dom, month, dow = parts[:5]
    if aws and (dom == "?") == (dow == "?"):
        raise CronError("AWS cron needs '?' in exactly one of day-of-month and day-of-week")
    dows = _field(dow, "day-of-week", DAYS, aws_dow=aws)
    return CronSpec(
        raw=raw, fields=tuple(parts), aws=aws, tz=tz,
        minutes=_field(minute, "minute"), hours=_field(hour, "hour"),
        doms=_field(dom, "day-of-month"), months=_field(month, "month", MONTHS),
        dows=frozenset(d % 7 for d in dows),
        years=_field(parts[5], "year") if aws else None,
        dom_star=dom.startswith(("*", "?")), dow_star=dow.startswith(("*", "?")),
    )


def _field(text: str, name: str, names: tuple[str, ...] = (), aws_dow: bool = False) -> frozenset[int]:
    low, high = BOUNDS[name]
    if aws_dow:
        low, high = 1, 7
    values: set[int] = set()
    for item in text.split(","):
        match = _ITEM.match(item)
        if not match:
            raise CronError(f"can't read {item!r} in the {name} field")
        body, step = match.group(1), int(match.group(2) or 1)
        if step < 1:
            raise CronError(f"step 0 in the {name} field")
        if body in ("*", "?"):
            start, end = low, (6 if name == "day-of-week" and not aws_dow else high)
        elif "-" in body:
            start, end = (_value(v, name, names, low, high, aws_dow) for v in body.split("-"))
        else:
            start = _value(body, name, names, low, high, aws_dow)
            end = high if match.group(2) else start
        if start > end:
            raise CronError(f"range {body} in the {name} field runs backwards")
        values.update(range(start, end + 1, step))
    if aws_dow:  # AWS: 1 = Sunday … 7 = Saturday
        return frozenset(v - 1 for v in values)
    return frozenset(values)


def _value(token: str, name: str, names: tuple[str, ...], low: int, high: int, aws_dow: bool) -> int:
    upper = token.upper()
    if names and upper in names:
        index = names.index(upper)
        return index + 1 if (name == "month" or aws_dow) else index
    if not token.isdigit():
        raise CronError(f"{token!r} is not a value for the {name} field")
    value = int(token)
    if not low <= value <= high:
        raise CronError(f"{value} is outside {low}-{high} in the {name} field")
    return value


def day_matches(spec: CronSpec, year: int, month: int, day: int, weekday: int) -> bool:
    """`weekday` is cron's: 0 = Sunday."""
    if month not in spec.months or (spec.years is not None and year not in spec.years):
        return False
    dom_ok, dow_ok = day in spec.doms, weekday in spec.dows
    if spec.or_rule:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def matches(spec: CronSpec, moment: datetime) -> bool:
    """The definition: does the schedule fire at this local minute?"""
    weekday = (moment.weekday() + 1) % 7
    return (moment.minute in spec.minutes and moment.hour in spec.hours
            and day_matches(spec, moment.year, moment.month, moment.day, weekday))
