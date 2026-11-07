"""Data model: a calendar event as a tool returned it (strings only), the checked event with its
times, a free gap, one day's plan and the whole plan. All frozen; each has a JSON-safe dict form so the
checkpointer can keep the plan for follow-ups."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime


@dataclass(frozen=True)
class RawEvent:
    """One event exactly as a tool returned it. Times stay strings until the zone is known."""

    id: str
    title: str
    start: str
    end: str
    start_tz: str = ""            # a zone name the tool gave next to a naive start time
    status: str = ""              # confirmed / tentative / cancelled, as returned
    my_response: str = ""         # the user's own answer to the invitation, as returned
    attendees: tuple[str, ...] = ()
    attendee_emails: tuple[str, ...] = ()
    organizer: str = ""
    my_email: str = ""
    location: str = ""
    join_link: str = ""
    link: str = ""
    description: str = ""
    has_attachments: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> RawEvent:
        return RawEvent(**{**data, "attendees": tuple(data.get("attendees") or ()),
                           "attendee_emails": tuple(data.get("attendee_emails") or ())})


@dataclass(frozen=True)
class Event:
    raw: RawEvent
    start: datetime
    end: datetime
    status: str = "accepted"      # accepted | tentative | needs_response
    context: str | None = None
    evidence: str | None = None
    flags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()   # human text for flags that name another event

    @property
    def minutes(self) -> int:
        return max(0, int((self.end - self.start).total_seconds() // 60))

    def with_flags(self, flags: tuple[str, ...], notes: tuple[str, ...] = ()) -> Event:
        return replace(self, flags=tuple(dict.fromkeys((*self.flags, *flags))),
                       notes=tuple(dict.fromkeys((*self.notes, *notes))))

    def to_dict(self) -> dict:
        return {**asdict(self), "raw": self.raw.to_dict(), "start": self.start.isoformat(),
                "end": self.end.isoformat()}

    @staticmethod
    def from_dict(data: dict) -> Event:
        return Event(raw=RawEvent.from_dict(data["raw"]), start=datetime.fromisoformat(data["start"]),
                     end=datetime.fromisoformat(data["end"]), status=data.get("status") or "accepted",
                     context=data.get("context"), evidence=data.get("evidence"),
                     flags=tuple(data.get("flags") or ()), notes=tuple(data.get("notes") or ()))


@dataclass(frozen=True)
class Gap:
    start: datetime
    end: datetime
    after_event_id: str = ""      # the event this gap follows ("" at the start of the day)
    before_event_id: str = ""     # the event this gap precedes ("" at the end of the day)

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(),
                "after_event_id": self.after_event_id, "before_event_id": self.before_event_id}

    @staticmethod
    def from_dict(data: dict) -> Gap:
        return Gap(datetime.fromisoformat(data["start"]), datetime.fromisoformat(data["end"]),
                   data.get("after_event_id") or "", data.get("before_event_id") or "")


@dataclass(frozen=True)
class DayStats:
    meetings: int = 0
    meeting_minutes: int = 0
    free_minutes: int = 0
    longest_gap_min: int = 0
    back_to_back_runs: int = 0
    double_bookings: int = 0
    declined_dropped: int = 0


@dataclass(frozen=True)
class DayPlan:
    day: date
    events: tuple[Event, ...]
    all_day: tuple[RawEvent, ...]
    gaps: tuple[Gap, ...]
    best: Gap | None
    stats: DayStats
    hours: tuple[str, str]        # "09:00", "18:00": the working hours the gaps were computed in

    def to_dict(self) -> dict:
        return {"day": self.day.isoformat(), "events": [e.to_dict() for e in self.events],
                "all_day": [a.to_dict() for a in self.all_day], "gaps": [g.to_dict() for g in self.gaps],
                "best": self.best.to_dict() if self.best else None, "stats": asdict(self.stats),
                "hours": list(self.hours)}

    @staticmethod
    def from_dict(data: dict) -> DayPlan:
        return DayPlan(day=date.fromisoformat(data["day"]), events=tuple(Event.from_dict(e) for e in data["events"]),
                       all_day=tuple(RawEvent.from_dict(a) for a in data.get("all_day") or ()),
                       gaps=tuple(Gap.from_dict(g) for g in data.get("gaps") or ()),
                       best=Gap.from_dict(data["best"]) if data.get("best") else None,
                       stats=DayStats(**data.get("stats") or {}), hours=tuple(data.get("hours") or ("09:00", "18:00")))


@dataclass(frozen=True)
class Plan:
    days: tuple[DayPlan, ...]
    zone: dict                    # timewin.Zone.to_dict()
    coverage: tuple[str, ...] = ()
    pasted: bool = False
    context_dropped: int = 0
    tz_asked: bool = False
    tools_failed: bool = False

    def to_dict(self) -> dict:
        return {"days": [d.to_dict() for d in self.days], "zone": dict(self.zone), "coverage": list(self.coverage),
                "pasted": self.pasted, "context_dropped": self.context_dropped, "tz_asked": self.tz_asked,
                "tools_failed": self.tools_failed}

    @staticmethod
    def from_dict(data: dict) -> Plan:
        return Plan(days=tuple(DayPlan.from_dict(d) for d in data["days"]), zone=dict(data.get("zone") or {}),
                    coverage=tuple(data.get("coverage") or ()), pasted=bool(data.get("pasted")),
                    context_dropped=int(data.get("context_dropped") or 0), tz_asked=bool(data.get("tz_asked")),
                    tools_failed=bool(data.get("tools_failed")))
