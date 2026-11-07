"""Pure: calendar events read straight out of the tools' own JSON output, in code.

The model only decides which tool to call with which window; it never retypes an event, so every title,
attendee, link and time below is verbatim tool output. Field names are matched by common shapes
(start/end objects or strings, attendees with response states, join links), never by provider.

Also: the calendar's own timezone if a tool returned one, dropping cancelled and declined events,
deduping one event seen on two calendars, and placing events on the user's local days.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta, tzinfo
from typing import Any

import timewin
from models import Event, RawEvent

MAX_ATTENDEES_SHOWN = 6
_START_KEYS = ("start", "startTime", "start_time", "startDateTime", "start_datetime")
_END_KEYS = ("end", "endTime", "end_time", "endDateTime", "end_datetime")
_INSTANT_KEYS = ("dateTime", "date_time", "datetime", "dateTimeRaw")
_TITLE_KEYS = ("summary", "title", "subject", "name")
_ID_KEYS = ("id", "eventId", "event_id", "iCalUID", "uid")
_DESCRIPTION_KEYS = ("description", "bodyPreview", "body", "notes", "agenda")
_LINK_KEYS = ("htmlLink", "webLink", "html_link", "web_link", "url", "link")
_JOIN_KEYS = ("hangoutLink", "joinUrl", "join_url", "meetingUrl", "meeting_url", "conference_link", "onlineMeetingUrl")
_TZ_KEYS = ("timeZone", "timezone", "time_zone")
_CANCELLED = frozenset({"cancelled", "canceled"})
_DECLINED = frozenset({"declined", "decline", "no"})
_TENTATIVE = frozenset({"tentative", "tentativelyaccepted", "maybe"})
_NEEDS = frozenset({"needsaction", "needs_action", "notresponded", "none", "pending", "invited"})


@dataclass(frozen=True)
class Extracted:
    events: tuple[RawEvent, ...]
    calendar_tz: str | None


def extract(bodies: Iterable[str]) -> Extracted:
    """Every event in the given tool outputs, plus the first valid zone a tool gave outside an event."""
    found: list[RawEvent] = []
    zones: list[str] = []
    for body in bodies:
        data = _json(body)
        if data is None:
            continue
        for node, in_event in _walk(data, False):
            event = _event(node) if not in_event else None
            if event is not None:
                found.append(event)
            elif not in_event:
                zones.extend(_zones(node))
    return Extracted(tuple(found), next((z for z in zones if timewin.valid_zone_name(z)), None))


def _json(body: str) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        start = body.find("{")
        try:
            return json.loads(body[start:]) if start >= 0 else None
        except ValueError:
            return None


def _walk(node: Any, in_event: bool) -> Iterator[tuple[Mapping, bool]]:
    if isinstance(node, Mapping):
        yield node, in_event
        here = in_event or _is_event(node)
        for value in node.values():
            yield from _walk(value, here)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, in_event)


def _is_event(node: Mapping) -> bool:
    return _first(node, _START_KEYS) is not None and _first(node, _END_KEYS) is not None


def _zones(node: Mapping) -> list[str]:
    zones = [str(node[k]) for k in _TZ_KEYS if isinstance(node.get(k), str)]
    key = str(node.get("id") or node.get("key") or node.get("setting") or "").casefold()
    if key in ("timezone", "time_zone") and isinstance(node.get("value"), str):
        zones.append(node["value"])
    return zones


def _first(node: Mapping, keys: Sequence[str]) -> Any:
    return next((node[k] for k in keys if node.get(k) not in (None, "", [], {})), None)


def _event(node: Mapping) -> RawEvent | None:
    if not _is_event(node):
        return None
    start, start_tz = _instant(_first(node, _START_KEYS))
    end, _ = _instant(_first(node, _END_KEYS))
    if not start:
        return None
    people = [p for p in node.get("attendees") or () if isinstance(p, Mapping)]
    me = next((p for p in people if p.get("self") is True), None)
    organizer = node.get("organizer")
    return RawEvent(
        id=_text(_first(node, _ID_KEYS)), title=_text(_first(node, _TITLE_KEYS)) or "(no title)",
        start=start, end=end, start_tz=start_tz, status=_text(node.get("status") or node.get("showAs")),
        my_response=_text(_response(me) if me else node.get("responseStatus") or node.get("myResponseStatus")),
        attendees=tuple(_person_label(p) for p in people), attendee_emails=tuple(e for e in map(_email, people) if e),
        organizer=_person_label(organizer) if isinstance(organizer, Mapping) else _text(organizer),
        my_email=_email(me) if me else (_email(organizer) if isinstance(organizer, Mapping)
                                         and organizer.get("self") is True else ""),
        location=_location(node.get("location")), join_link=_join_link(node), link=_text(_first(node, _LINK_KEYS)),
        description=_description(node), has_attachments=bool(node.get("attachments")),
    )


def _instant(value: Any) -> tuple[str, str]:
    if isinstance(value, Mapping):
        instant = _first(value, _INSTANT_KEYS)
        tz = _text(_first(value, _TZ_KEYS))
        return (_text(instant), tz) if instant else (_text(value.get("date")), tz)
    return _text(value), ""


def _text(value: Any) -> str:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value).strip()
    return ""


def _email(person: Mapping | None) -> str:
    if not isinstance(person, Mapping):
        return ""
    address = person.get("emailAddress")
    if isinstance(address, Mapping):
        return _text(address.get("address"))
    return _text(person.get("email") or address)


def _person_label(person: Mapping) -> str:
    address = person.get("emailAddress")
    name = _text(person.get("displayName") or person.get("name")
                 or (address.get("name") if isinstance(address, Mapping) else ""))
    return name or _email(person)


def _response(person: Mapping) -> str:
    status = person.get("status")
    nested = status.get("response") if isinstance(status, Mapping) else status
    return _text(person.get("responseStatus") or person.get("response") or nested)


def _location(value: Any) -> str:
    if isinstance(value, Mapping):
        return _text(value.get("displayName") or value.get("name"))
    return _text(value)


def _join_link(node: Mapping) -> str:
    direct = _text(_first(node, _JOIN_KEYS))
    if direct:
        return direct
    online = node.get("onlineMeeting")
    if isinstance(online, Mapping) and _text(online.get("joinUrl")):
        return _text(online.get("joinUrl"))
    conference = node.get("conferenceData")
    points = conference.get("entryPoints") if isinstance(conference, Mapping) else None
    for point in points or ():
        if isinstance(point, Mapping) and point.get("entryPointType") in (None, "video") and _text(point.get("uri")):
            return _text(point.get("uri"))
    return ""


def _description(node: Mapping) -> str:
    value = _first(node, _DESCRIPTION_KEYS)
    if isinstance(value, Mapping):
        value = value.get("content")
    return _text(value)


# --- placing events on the user's days ------------------------------------------------------------------

@dataclass(frozen=True)
class Sorted:
    """Events on the requested local days: timed (with their instants), all-day, and what was dropped."""

    timed: tuple[Event, ...]
    all_day: tuple[RawEvent, ...]
    declined: int
    cancelled: int


def timezone_from(extracted: Extracted, stated: str | None, stored: dict | None) -> timewin.Zone | None:
    """First hit wins: user-stated, stored for this conversation, the calendar's own, the offset most
    event start times carry. None means the zone is unknown and must be asked."""
    if stated:
        return timewin.Zone(timewin.zone_of(stated), stated, "stated")
    restored = timewin.Zone.from_dict(stored) if stored else None
    if restored is not None:
        return restored
    calendar = timewin.valid_zone_name(extracted.calendar_tz) or next(
        (z for z in (timewin.valid_zone_name(e.start_tz) for e in extracted.events) if z), None)
    if calendar:
        return timewin.Zone(timewin.zone_of(calendar), calendar, "calendar")
    offset = timewin.majority_offset(e.start for e in extracted.events)
    return timewin.offset_zone(offset) if offset is not None else None


def place(events: Sequence[RawEvent], days: Sequence[date], tz: tzinfo) -> Sorted:
    """Drop cancelled and declined events (counted), dedupe by (start, end, title), and keep those that
    overlap the requested local days. Tentative and unanswered invitations are marked."""
    window = timewin.local_days(days, tz)
    wanted = set(days)
    timed: dict[tuple, Event] = {}
    all_day: dict[tuple, RawEvent] = {}
    declined = cancelled = 0
    for raw in events:
        if raw.status.casefold() in _CANCELLED:
            cancelled += 1
            continue
        if raw.my_response.casefold() in _DECLINED:
            declined += 1
            continue
        if timewin.is_all_day(raw.start):
            if covers(raw, wanted):
                all_day.setdefault((raw.start, raw.title.casefold()), raw)
            continue
        event = _timed(raw, tz)
        if event is not None and event.start < window.end and event.end > window.start:
            timed.setdefault((event.start, event.end, raw.title.casefold()), event)
    ordered = tuple(sorted(timed.values(), key=lambda e: (e.start, e.end)))
    return Sorted(ordered, tuple(all_day.values()), declined, cancelled)


def covers(raw: RawEvent, days: set[date]) -> bool:
    first = timewin.parse_date(raw.start)
    last = timewin.parse_date(raw.end) or (first + timedelta(days=1) if first else None)
    if first is None:
        return False
    return any(first <= d < max(last, first + timedelta(days=1)) for d in days)


def _timed(raw: RawEvent, tz: tzinfo) -> Event | None:
    zone = timewin.zone_of(raw.start_tz) if timewin.valid_zone_name(raw.start_tz) else tz
    start = timewin.parse_instant(raw.start, zone)
    end = timewin.parse_instant(raw.end, zone) if raw.end else None
    if start is None:
        return None
    end = end if end is not None and end > start else start
    return Event(raw=raw, start=start, end=end, status=_status(raw))


def _status(raw: RawEvent) -> str:
    response = raw.my_response.casefold().replace(" ", "")
    if response in _TENTATIVE or raw.status.casefold() == "tentative":
        return "tentative"
    if response in _NEEDS:
        return "needs_response"
    return "accepted"


def attendees_shown(raw: RawEvent) -> list[str]:
    """First six as returned, then "+N"."""
    shown = list(raw.attendees[:MAX_ATTENDEES_SHOWN])
    extra = len(raw.attendees) - len(shown)
    return [*shown, f"+{extra}"] if extra > 0 else shown
