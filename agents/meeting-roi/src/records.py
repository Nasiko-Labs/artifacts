"""Pure data: what the checked tool output becomes, and what the report is made of. Frozen; every
record here was checked in code (checks.py) before it was built."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

Response = Literal["accepted", "tentative", "declined", "none"]
Tri = Literal["true", "false", "unclear", "unknown"]
Proposal = Literal["cancel", "ask the organizer", "shrink", "shorten", "keep", "insufficient data"]


@dataclass(frozen=True)
class Attendee:
    email: str
    response: Response
    is_self: bool = False


@dataclass(frozen=True)
class Event:
    """One occurrence. `attendees` is the guest list as the tool returned it (capped)."""

    id: str
    title: str
    start: datetime
    end: datetime
    series_id: str | None = None
    url: str = ""
    organizer_email: str = ""
    organizer_self: bool = False
    attendees: tuple[Attendee, ...] = ()
    attachments: tuple[tuple[str, str], ...] = ()  # (title, url)
    channel_hint: str = ""
    attendees_capped: bool = False

    @property
    def duration_h(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    def count(self, response: Response) -> int:
        return sum(1 for a in self.attendees if a.response == response)

    @property
    def self_declined(self) -> bool:
        return any(a.is_self and a.response == "declined" for a in self.attendees)


@dataclass(frozen=True)
class Series:
    title: str
    events: tuple[Event, ...]
    organizer_email: str = ""
    user_is_organizer: bool | None = None  # None: not known (pasted data)
    channel_hint: str = ""

    @property
    def attachments(self) -> tuple[tuple[str, str], ...]:
        return tuple(dict.fromkeys(a for e in self.events for a in e.attachments))


@dataclass(frozen=True)
class NotesFinding:
    series_title: str
    doc_title: str
    url: str
    modified_at: datetime | None
    text: str = ""


@dataclass(frozen=True)
class Decisions:
    has_decisions: Tri = "unknown"
    decision_quotes: tuple[str, ...] = ()
    has_action_items: Tri = "unknown"
    action_quotes: tuple[str, ...] = ()


@dataclass(frozen=True)
class OccurrenceCount:
    occurrence_start: datetime
    count: int
    permalink: str | None = None


@dataclass(frozen=True)
class ChatFollowup:
    series_title: str
    channel: str
    counts: tuple[OccurrenceCount, ...] = ()


@dataclass(frozen=True)
class MeetingRoi:
    """One audited series, as shown and as returned in `meetings[]`. Every number is computed in code."""

    series_title: str
    cadence: str
    weekday: str
    duration_min: int
    occurrences: int
    invited: int
    accepted: float
    accept_rate: float | None
    no_response_rate: float | None
    decline_rate: float | None
    hours_per_month_person: float
    has_notes: bool | None  # None: notes could not be searched
    notes_url: str | None
    notes_current: bool | None
    has_decisions: Tri
    decision_quotes: tuple[str, ...]
    has_action_items: Tri
    action_quotes: tuple[str, ...]
    followup_rate: float | None
    followup_channel: str | None
    proposal: Proposal
    proposed_minutes: int | None
    proposed_size: int | None
    savings_hours_per_month: float
    organizer: str
    user_is_organizer: bool | None
    confidence: Literal["high", "medium", "low"]
    flags: tuple[str, ...] = ()
    evidence_links: tuple[str, ...] = ()
    cost_per_month: float | None = None
    savings_cost_per_month: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))

    @staticmethod
    def from_dict(data: dict[str, Any]) -> MeetingRoi:
        names = MeetingRoi.__dataclass_fields__
        values = {k: tuple(v) if isinstance(v, list) else v for k, v in data.items() if k in names}
        return MeetingRoi(**values)


@dataclass(frozen=True)
class Report:
    meetings: tuple[MeetingRoi, ...]
    window_days: int
    window_start: datetime
    window_end: datetime
    excluded: dict[str, int] = field(default_factory=dict)
    coverage: tuple[str, ...] = ()
    sources_read: frozenset[str] = frozenset()
    capped: bool = False
    pasted: bool = False
    rate: float | None = None
    filters: tuple[str, ...] = ()

    @property
    def total_hours(self) -> float:
        return round(sum(m.hours_per_month_person for m in self.meetings), 1)

    @property
    def total_savings(self) -> float:
        return round(sum(m.savings_hours_per_month for m in self.meetings), 1)


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value
