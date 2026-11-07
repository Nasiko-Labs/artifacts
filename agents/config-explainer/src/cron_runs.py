"""Pure: when a parsed cron schedule runs. Next runs by scanning day by day with `cron.day_matches`, then the
minutes of each matching day; frequency (runs per day/week/366 days, gap set) and DST skips/repeats from
`zoneinfo` transitions. Every number here is computed, never estimated."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cron import CronSpec, day_matches

YEAR_DAYS = 366
NEXT_RUNS_MAX_DAYS = 8 * 366       # long enough to reach the next 29 February from any date
DEFAULT_RUNS = 5
MAX_RUNS = 20
MINUTES_PER_DAY = 1440


@dataclass(frozen=True)
class Frequency:
    runs_per_day: int        # times of day on a matching day
    days_per_year: int       # matching days in the next 366 days
    runs_per_year: int
    runs_per_week: float
    gaps: tuple[int, ...]    # distinct minutes between consecutive runs, ascending
    months_never: tuple[int, ...]


@dataclass(frozen=True)
class DstNote:
    day: date
    kind: str                # "skipped" | "repeated"
    local: str               # "02:30"


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name) if name else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone {name!r}") from exc


def _cron_weekday(day: date) -> int:
    return (day.weekday() + 1) % 7


def matching_days(spec: CronSpec, start: date, days: int):
    for offset in range(days):
        day = start + timedelta(days=offset)
        if day_matches(spec, day.year, day.month, day.day, _cron_weekday(day)):
            yield day


def times_of_day(spec: CronSpec) -> list[tuple[int, int]]:
    return [(h, m) for h in sorted(spec.hours) for m in sorted(spec.minutes)]


def _local(day: date, hour: int, minute: int, tz: ZoneInfo) -> tuple[datetime, str | None]:
    """The aware time and its DST status: None, "skipped" (in a spring-forward gap) or "repeated"."""
    naive = datetime(day.year, day.month, day.day, hour, minute)
    aware = naive.replace(tzinfo=tz)
    if aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive:
        return aware, "skipped"
    if aware.replace(fold=0).utcoffset() != aware.replace(fold=1).utcoffset():
        return aware, "repeated"
    return aware, None


def next_runs(spec: CronSpec, start: datetime, count: int = DEFAULT_RUNS) -> tuple[list[datetime], list[DstNote]]:
    """The next `count` runs strictly after `start` (aware), in the spec's zone. Skipped local times are
    not runs; they are returned as notes."""
    tz = zone(spec.tz)
    after = start.astimezone(tz)
    runs: list[datetime] = []
    notes: list[DstNote] = []
    for day in matching_days(spec, after.date(), NEXT_RUNS_MAX_DAYS):
        for hour, minute in times_of_day(spec):
            moment, status = _local(day, hour, minute, tz)
            if moment <= after:
                continue
            if status is not None:
                notes.append(DstNote(day, status, f"{hour:02d}:{minute:02d}"))
            if status == "skipped":
                continue
            runs.append(moment)
            if len(runs) >= count:
                return runs, notes
    return runs, notes


def frequency(spec: CronSpec, start: date) -> Frequency:
    days = list(matching_days(spec, start, YEAR_DAYS))
    minutes = [h * 60 + m for h, m in times_of_day(spec)]
    gaps: set[int] = {b - a for a, b in pairwise(minutes)}
    for first, second in pairwise(days):
        gaps.add((second - first).days * MINUTES_PER_DAY - minutes[-1] + minutes[0])
    per_year = len(days) * len(minutes)
    return Frequency(runs_per_day=len(minutes), days_per_year=len(days), runs_per_year=per_year,
                     runs_per_week=round(per_year / (YEAR_DAYS / 7), 1), gaps=tuple(sorted(gaps)),
                     months_never=months_never(spec))


def months_never(spec: CronSpec) -> tuple[int, ...]:
    """Months in the schedule on which no listed day-of-month exists (Feb counts its leap day). Only when
    the day-of-month alone decides the day (no either/or with day-of-week)."""
    if spec.dom_star or spec.or_rule:
        return ()
    longest = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
    return tuple(m for m in sorted(spec.months) if min(spec.doms) > longest[m])


def dst_notes(spec: CronSpec, start: date) -> list[DstNote]:
    """Skipped or repeated local run times in the next 366 days (only on days whose UTC offset changes)."""
    tz = zone(spec.tz)
    notes: list[DstNote] = []
    for day in matching_days(spec, start, YEAR_DAYS):
        morning = datetime(day.year, day.month, day.day, tzinfo=tz)
        if morning.utcoffset() == (morning + timedelta(hours=23, minutes=59)).utcoffset():
            continue
        for hour, minute in times_of_day(spec):
            _, status = _local(day, hour, minute, tz)
            if status is not None:
                notes.append(DstNote(day, status, f"{hour:02d}:{minute:02d}"))
    return notes
