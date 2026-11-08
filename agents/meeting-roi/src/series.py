"""Pure: group checked events into recurring series, then drop what is not audited.

A series is the events sharing a recurrence id, or (without one) the same normalised title with an
attendee-set overlap of at least 0.7, seen at least 3 times. One-time events are dropped. One-on-ones
(two people or fewer) and series with a guest outside the user's own domain are excluded and counted.
Over 25 series, the 25 with the most person-hours are kept."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from checks import with_series_channel
from records import Event, Series

MIN_OCCURRENCES_UNGROUPED = 3
OVERLAP_THRESHOLD = 0.7
MAX_SERIES = 25
ONE_ON_ONE_MAX_PEOPLE = 2
_NON_WORD = re.compile(r"[^a-z0-9]+")
_DATE_IN_TITLE = re.compile(r"\b\d{1,4}[/.-]\d{1,2}(?:[/.-]\d{1,4})?\b")


@dataclass(frozen=True)
class Grouped:
    series: tuple[Series, ...]
    excluded: dict[str, int] = field(default_factory=dict)
    capped: int = 0  # series left out by the 25 cap


def normalise_title(title: str) -> str:
    return _NON_WORD.sub(" ", _DATE_IN_TITLE.sub(" ", title.casefold())).strip()


def overlap(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def group(events: Sequence[Event]) -> tuple[Series, ...]:
    by_id: dict[str, list[Event]] = {}
    loose: dict[str, list[list[Event]]] = {}
    for event in events:
        if event.series_id:
            by_id.setdefault(event.series_id, []).append(event)
            continue
        clusters = loose.setdefault(normalise_title(event.title), [])
        home = next((c for c in clusters if overlap(_emails(c[0]), _emails(event)) >= OVERLAP_THRESHOLD), None)
        if home is None:
            clusters.append([event])
        else:
            home.append(event)
    groups = [*by_id.values(), *(c for cs in loose.values() for c in cs if len(c) >= MIN_OCCURRENCES_UNGROUPED)]
    return tuple(_series(sorted(g, key=lambda e: e.start)) for g in groups)


def _series(events: list[Event]) -> Series:
    latest = events[-1]
    organizer = next((e.organizer_email for e in reversed(events) if e.organizer_email), "")
    is_org = any(e.organizer_self for e in events)
    return with_series_channel(Series(title=latest.title, events=tuple(events), organizer_email=organizer,
                                      user_is_organizer=is_org))


def _emails(event: Event) -> frozenset[str]:
    return frozenset(a.email for a in event.attendees)


def user_domain(events: Sequence[Event]) -> str | None:
    """The domain of the user's own address (the attendee flagged as self, else the self organizer)."""
    own = [a.email for e in events for a in e.attendees if a.is_self]
    own += [e.organizer_email for e in events if e.organizer_self and e.organizer_email]
    domains = Counter(email.rsplit("@", 1)[-1] for email in own if "@" in email)
    return domains.most_common(1)[0][0] if domains else None


def select(series: Sequence[Series], domain: str | None, *, only_organizer: bool = False,
           min_people: int | None = None) -> Grouped:
    excluded: Counter[str] = Counter()
    kept: list[Series] = []
    for s in series:
        reason = _exclusion(s, domain, only_organizer, min_people)
        if reason:
            excluded[reason] += 1
        else:
            kept.append(s)
    ranked = sorted(kept, key=lambda s: -person_hours(s))
    return Grouped(tuple(ranked[:MAX_SERIES]), dict(excluded), max(0, len(ranked) - MAX_SERIES))


def _exclusion(s: Series, domain: str | None, only_organizer: bool, min_people: int | None) -> str | None:
    size = max((len(e.attendees) for e in s.events), default=0)
    if 0 < size <= ONE_ON_ONE_MAX_PEOPLE:
        return "one_on_ones"
    if domain and any(not a.email.endswith("@" + domain) for e in s.events for a in e.attendees):
        return "external_guests"
    if only_organizer and not s.user_is_organizer:
        return "not_organized_by_you"
    if min_people is not None and size <= min_people:
        return "below_size_filter"
    return None


def person_hours(s: Series) -> float:
    return sum(e.duration_h * (e.count("accepted") + e.count("tentative")) for e in s.events)
