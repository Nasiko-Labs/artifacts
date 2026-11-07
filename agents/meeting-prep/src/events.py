"""Pure: the calendar step's answer, and the code post-check that decides which meetings get a card.

An event survives only if its id and start time appear in this turn's tool output, it starts inside
the requested local day, it is not all-day, cancelled or declined by the user, and someone other than
the user is invited. Attendees, links and the description excerpt are kept only when a tool returned
them verbatim."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, time, tzinfo
from typing import Any

import timewin
from verbatim import Seen, load_json_object, text_of

MAX_CARDS = 5
MAX_ATTENDEES = 25
MAX_LINKS = 8
_STATUSES = ("ok", "off_topic", "no_calendar_tool")
_CANCELLED = ("cancelled", "canceled")
_DECLINED = ("declined", "decline", "no")


@dataclass(frozen=True)
class Attendee:
    email: str
    name: str = ""
    response: str = ""

    @property
    def label(self) -> str:
        who = self.name or self.email
        return f"{who} ({self.response})" if self.response else who


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    start: str
    end: str
    series_id: str = ""
    status: str = ""
    my_response: str = ""
    attendees: tuple[Attendee, ...] = ()
    organizer: str = ""
    description_excerpt: str = ""
    links: tuple[str, ...] = ()
    conference_link: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Event:
        attendees = tuple(Attendee(**a) for a in data.get("attendees") or ())
        return Event(**{**data, "attendees": attendees, "links": tuple(data.get("links") or ())})


@dataclass(frozen=True)
class CalendarAnswer:
    status: str
    me: str
    calendar_timezone: str
    events: tuple[Event, ...]


@dataclass(frozen=True)
class Meeting:
    """A checked event with its parsed times; what a card is built for."""

    event: Event
    start: datetime
    end: datetime

    def to_dict(self) -> dict:
        return {"event": self.event.to_dict(), "start": self.start.isoformat(), "end": self.end.isoformat()}

    @staticmethod
    def from_dict(data: dict) -> Meeting:
        return Meeting(Event.from_dict(data["event"]), datetime.fromisoformat(data["start"]),
                       datetime.fromisoformat(data["end"]))


@dataclass(frozen=True)
class Selection:
    """The meetings that get a card, the ones only listed, and how many events the checks dropped."""

    cards: tuple[Meeting, ...]
    overflow: tuple[Meeting, ...]
    dropped: int


def parse_calendar(text: str) -> CalendarAnswer | None:
    data = load_json_object(text)
    if data is None or data.get("status") not in _STATUSES:
        return None
    raw = data.get("events") or []
    if not isinstance(raw, list):
        return None
    events = tuple(e for e in (_event(item) for item in raw) if e is not None)
    return CalendarAnswer(data["status"], text_of(data.get("me"), 200), text_of(data.get("calendar_timezone"), 80),
                          events)


def _event(raw: Any) -> Event | None:
    if not isinstance(raw, dict):
        return None
    event_id, start = text_of(raw.get("id"), 300), text_of(str(raw.get("start") or ""), 60)
    if not event_id or not start:
        return None
    attendees = tuple(a for a in (_attendee(x) for x in _list(raw.get("attendees"))[:MAX_ATTENDEES]) if a)
    links = tuple(link for link in (text_of(x, 500) for x in _list(raw.get("links"))[:MAX_LINKS]) if link)
    return Event(id=event_id, title=text_of(raw.get("title"), 200), start=start,
                 end=text_of(str(raw.get("end") or ""), 60), series_id=text_of(raw.get("series_id"), 300),
                 status=text_of(raw.get("status"), 40), my_response=text_of(raw.get("my_response"), 40),
                 attendees=attendees, organizer=text_of(raw.get("organizer"), 200),
                 description_excerpt=text_of(raw.get("description_excerpt"), 600), links=links,
                 conference_link=text_of(raw.get("conference_link"), 500))


def _attendee(raw: Any) -> Attendee | None:
    if not isinstance(raw, dict):
        return None
    email = text_of(raw.get("email"), 200)
    return Attendee(email, text_of(raw.get("name"), 120), text_of(raw.get("response"), 40)) if email else None


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def verified(answer: CalendarAnswer, seen: Seen) -> tuple[Event, ...]:
    """Events whose id and start a tool returned, with every unbacked detail removed."""
    return tuple(_backed(e, seen) for e in answer.events if seen.has(e.id) and seen.has(e.start))


def _backed(event: Event, seen: Seen) -> Event:
    return replace(
        event,
        title=event.title if seen.has(event.title) else "",
        end=event.end if seen.has(event.end) else "",
        series_id=event.series_id if seen.has(event.series_id) else "",
        attendees=tuple(a for a in event.attendees if seen.has(a.email)),
        organizer=event.organizer if seen.has(event.organizer) else "",
        description_excerpt=event.description_excerpt if seen.has(event.description_excerpt) else "",
        links=tuple(link for link in event.links if seen.has(link)),
        conference_link=event.conference_link if seen.has(event.conference_link) else "",
    )


def select(events: Sequence[Event], *, me: str, tz: tzinfo, day: timewin.Window, now: datetime,
           include_ended: bool, focus: time | None, total: int) -> Selection:
    """Filter to the meetings worth a card, soonest first; the first MAX_CARDS get one."""
    kept = sorted((m for m in (_meeting(e, tz) for e in _unique(events)) if m is not None
                   and _counts(m, me=me, day=day, now=now, include_ended=include_ended)), key=lambda m: m.start)
    if focus is not None:
        kept = [m for m in kept if _matches_focus(m, focus, tz)]
    dropped = max(0, total - len(kept)) if focus is None else 0
    return Selection(tuple(kept[:MAX_CARDS]), tuple(kept[MAX_CARDS:]), dropped)


def _unique(events: Sequence[Event]) -> list[Event]:
    by_key: dict[tuple[str, str], Event] = {}
    for event in events:
        by_key.setdefault((event.id, event.start), event)
    return list(by_key.values())


def _meeting(event: Event, tz: tzinfo) -> Meeting | None:
    if timewin.is_all_day(event.start):
        return None
    start = timewin.parse_instant(event.start, tz)
    end = timewin.parse_instant(event.end, tz) if event.end else None
    if start is None:
        return None
    return Meeting(event, start, end if end is not None and end > start else start)


def _counts(meeting: Meeting, *, me: str, day: timewin.Window, now: datetime, include_ended: bool) -> bool:
    event = meeting.event
    if event.status.casefold() in _CANCELLED or event.my_response.casefold() in _DECLINED:
        return False
    if _my_attendance(event, me) in _DECLINED:
        return False
    if not day.contains(meeting.start):
        return False
    if not include_ended and meeting.end <= now:
        return False
    return bool(others(event, me))


def _my_attendance(event: Event, me: str) -> str:
    mine = next((a for a in event.attendees if me and a.email.casefold() == me.casefold()), None)
    return mine.response.casefold() if mine else ""


def others(event: Event, me: str) -> tuple[Attendee, ...]:
    """Attendees other than the user. With the user's address unknown, a lone attendee who is also
    the organizer is the user's own block."""
    if me:
        return tuple(a for a in event.attendees if a.email.casefold() != me.casefold())
    if len(event.attendees) == 1 and event.attendees[0].email.casefold() == event.organizer.casefold():
        return ()
    return event.attendees


def _matches_focus(meeting: Meeting, focus: time, tz: tzinfo) -> bool:
    local = meeting.start.astimezone(tz)
    return local.hour == focus.hour and (focus.minute == 0 or local.minute == focus.minute)
