"""Pure: the incident metrics, computed in code from the checked, timed events.

The start is the earliest `impact` event. time_to_detect = first detection - start; time_to_mitigate = first
mitigation - start; duration = first resolution - start. A metric exists only when both ends are timed and
share one timezone label, and the end is not before the start; otherwise it is "Not stated"."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from records import NOT_STATED, Event
from times import format_minutes, minutes_between

ENDS = (("time_to_detect", "detection"), ("time_to_mitigate", "mitigation"), ("duration", "resolution"))
MIXED_TZ_NOTE = ("The log mixes timezone labels ({labels}); I don't convert between them, so durations across "
                 "them are not computed.")


@dataclass(frozen=True)
class Metrics:
    values: dict[str, str]
    tz_label: str
    mixed: tuple[str, ...] = ()

    @property
    def note(self) -> str:
        return MIXED_TZ_NOTE.format(labels=", ".join(self.mixed)) if self.mixed else ""


def first_of(events: Sequence[Event], category: str) -> Event | None:
    timed = [e for e in events if e.category == category and e.stamp]
    return min(timed, key=lambda e: e.stamp) if timed else None


def compute(events: Sequence[Event]) -> Metrics:
    labels = sorted({e.tz for e in events if e.stamp})
    start = first_of(events, "impact")
    values = {}
    for key, category in ENDS:
        end = first_of(events, category)
        values[key] = _between(start, end)
    shown = labels[0] if len(labels) == 1 else ""
    return Metrics(values, shown, tuple(label or "no label" for label in labels) if len(labels) > 1 else ())


def _between(start: Event | None, end: Event | None) -> str:
    if start is None or end is None:
        return NOT_STATED
    minutes = minutes_between(_parse(start.stamp), start.tz, _parse(end.stamp), end.tz)
    if minutes is None or minutes < 0:
        return NOT_STATED
    return format_minutes(minutes)


def _parse(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp) if stamp else None
    except ValueError:
        return None


def untimed_share(events: Sequence[Event]) -> float:
    return sum(1 for e in events if not e.stamp) / len(events) if events else 1.0
