"""Pure: the numbers and the proposal for one series. Every threshold is a named constant here, and
the rules are applied in a fixed order (first match wins), so a proposal can always be explained.

Rules:
1. `insufficient data`: fewer than 3 occurrences.
2. `cancel`: RSVP acceptance under 50 % and the notes were searched and record no decisions or action
   items (or no notes were found), and you organize it; a guest gets `ask the organizer` instead. When
   notes could not be searched this rule is skipped (a cancel needs the notes check).
3. `shrink`: 8+ invited, acceptance 50 %+, and the notes record no decisions.
4. `shorten`: 45+ minutes, notes exist and record no decisions: the next shorter standard slot.
5. `keep`: otherwise.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from records import ChatFollowup, Decisions, MeetingRoi, NotesFinding, Series

MIN_OCCURRENCES = 3
LOW_ATTENDANCE_RATE = 0.5
SHRINK_MIN_INVITED = 8
SHRINK_DECISION_GROUP = 6  # "trim to the decision makers": at most this many people
SHORTEN_MIN_MINUTES = 45
STANDARD_SLOTS = (15, 30, 45, 60, 90, 120)
FOLLOWUP_MIN_MESSAGES = 3
NOTES_FRESH_DAYS = 7
CONFIDENT_OCCURRENCES = 4  # fewer than this: low confidence
DAYS_PER_MONTH = 30
MAX_EVIDENCE_LINKS = 4
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

NotesState = Literal["read", "missing", "skipped"]


@dataclass(frozen=True)
class Occ:
    """One occurrence, reduced to what the maths needs."""

    start: datetime | None
    duration_h: float
    invited: int
    accepted: int
    tentative: int = 0
    declined: int = 0

    @property
    def attending(self) -> int:
        return self.accepted + self.tentative

    @property
    def person_hours(self) -> float:
        return self.duration_h * self.attending


@dataclass(frozen=True)
class SeriesFacts:
    title: str
    occs: tuple[Occ, ...]
    organizer: str = ""
    user_is_organizer: bool | None = None
    event_urls: tuple[str, ...] = ()
    attendees_capped: bool = False
    weekday: str = ""
    cadence: str = ""


@dataclass(frozen=True)
class Evidence:
    """What the other steps found for one series."""

    notes_state: NotesState = "missing"
    notes: NotesFinding | None = None
    decisions: Decisions = field(default_factory=Decisions)
    chat: ChatFollowup | None = None


def facts_from_series(series: Series) -> SeriesFacts:
    occs = tuple(Occ(start=e.start, duration_h=e.duration_h, invited=len(e.attendees), accepted=e.count("accepted"),
                     tentative=e.count("tentative"), declined=e.count("declined")) for e in series.events)
    starts = [e.start for e in series.events]
    days = Counter(WEEKDAYS[s.weekday()] for s in starts)
    return SeriesFacts(title=series.title, occs=occs, organizer=series.organizer_email,
                       user_is_organizer=series.user_is_organizer,
                       event_urls=tuple(dict.fromkeys(e.url for e in series.events if e.url)),
                       attendees_capped=any(e.attendees_capped for e in series.events),
                       weekday=days.most_common(1)[0][0] if days else "", cadence=cadence(starts))


def cadence(starts: Sequence[datetime]) -> str:
    ordered = sorted(starts)
    gaps = [(b - a).total_seconds() / 86400 for a, b in itertools.pairwise(ordered)]
    if not gaps:
        return "unknown"
    gap = statistics.median(gaps)
    for limit, name in ((1.5, "daily"), (3.5, "twice weekly"), (8, "weekly"), (16, "every two weeks")):
        if gap <= limit:
            return name
    return "monthly"


def per_month(value: float, window_days: int) -> float:
    return round(value / window_days * DAYS_PER_MONTH, 1)


def shorter_slot(minutes: int) -> int | None:
    shorter = [s for s in STANDARD_SLOTS if s < minutes]
    return shorter[-1] if shorter else None


def evaluate(facts: SeriesFacts, evidence: Evidence, window_days: int, rate: float | None = None) -> MeetingRoi:
    occs = facts.occs
    n = len(occs)
    with_guests = [o for o in occs if o.invited > 0]
    accept_rate = _mean([o.accepted / o.invited for o in with_guests])
    duration_min = round(statistics.median([o.duration_h for o in occs]) * 60) if occs else 0
    invited = round(statistics.median([o.invited for o in with_guests])) if with_guests else 0
    accepted = round(statistics.mean([o.accepted for o in with_guests]), 1) if with_guests else 0.0
    hours = per_month(sum(o.person_hours for o in occs), window_days)
    has_notes = None if evidence.notes_state != "read" else evidence.notes is not None
    decisions = evidence.decisions if evidence.notes is not None else Decisions()
    low = accept_rate is not None and accept_rate < LOW_ATTENDANCE_RATE and n >= MIN_OCCURRENCES
    proposal, minutes, size, flags = _propose(facts, evidence, decisions, low, accept_rate, invited, duration_min)
    savings = _savings(proposal, occs, hours, minutes, size, window_days)
    followup = _followup_rate(evidence.chat)
    flags = _flags(flags, low, has_notes, accept_rate, facts, evidence, followup)
    return MeetingRoi(
        series_title=facts.title, cadence=facts.cadence or "as pasted", weekday=facts.weekday,
        duration_min=duration_min, occurrences=n, invited=invited, accepted=accepted,
        accept_rate=_round(accept_rate), no_response_rate=_round(_mean([_share(o, "none") for o in with_guests])),
        decline_rate=_round(_mean([o.declined / o.invited for o in with_guests])), hours_per_month_person=hours,
        has_notes=has_notes, notes_url=evidence.notes.url if evidence.notes else None,
        notes_current=_notes_current(evidence.notes, occs), has_decisions=decisions.has_decisions,
        decision_quotes=decisions.decision_quotes, has_action_items=decisions.has_action_items,
        action_quotes=decisions.action_quotes, followup_rate=followup,
        followup_channel=evidence.chat.channel if evidence.chat else None, proposal=proposal,
        proposed_minutes=minutes, proposed_size=size, savings_hours_per_month=savings, organizer=facts.organizer,
        user_is_organizer=facts.user_is_organizer, confidence=_confidence(n, with_guests, evidence),
        flags=flags, evidence_links=_links(facts, evidence),
        cost_per_month=round(hours * rate, 2) if rate else None,
        savings_cost_per_month=round(savings * rate, 2) if rate else None)


def _propose(facts: SeriesFacts, evidence: Evidence, decisions: Decisions, low: bool, accept_rate: float | None,
             invited: int, duration_min: int) -> tuple[str, int | None, int | None, tuple[str, ...]]:
    if len(facts.occs) < MIN_OCCURRENCES:
        return "insufficient data", None, None, ()
    searched = evidence.notes_state == "read"
    nothing_recorded = evidence.notes is None or (
        decisions.has_decisions == "false" and decisions.has_action_items == "false")
    if low and searched and nothing_recorded:
        return ("cancel" if facts.user_is_organizer else "ask the organizer"), None, None, ()
    flags = ("CANCEL_NEEDS_NOTES_CHECK",) if low and not searched else ()
    no_decisions = decisions.has_decisions == "false"
    if invited >= SHRINK_MIN_INVITED and accept_rate is not None and accept_rate >= LOW_ATTENDANCE_RATE \
            and no_decisions:
        return "shrink", None, SHRINK_DECISION_GROUP, flags
    slot = shorter_slot(duration_min)
    if duration_min >= SHORTEN_MIN_MINUTES and evidence.notes is not None and no_decisions and slot:
        return "shorten", slot, None, flags
    return "keep", None, None, flags


def _savings(proposal: str, occs: Sequence[Occ], hours: float, minutes: int | None, size: int | None,
             window_days: int) -> float:
    if proposal in ("cancel", "ask the organizer"):
        return hours
    if proposal == "shorten" and minutes is not None:
        saved = sum(max(0.0, o.duration_h - minutes / 60) * o.attending for o in occs)
        return min(hours, per_month(saved, window_days))
    if proposal == "shrink" and size is not None:
        saved = sum(max(0, o.attending - size) * o.duration_h for o in occs)
        return min(hours, per_month(saved, window_days))
    return 0.0


def _followup_rate(chat: ChatFollowup | None) -> float | None:
    if chat is None or not chat.counts:
        return None
    return _round(sum(1 for c in chat.counts if c.count >= FOLLOWUP_MIN_MESSAGES) / len(chat.counts))


def _notes_current(notes: NotesFinding | None, occs: Sequence[Occ]) -> bool | None:
    """Touched after at least half the occurrences, and within 7 days of the latest one before it."""
    if notes is None or notes.modified_at is None:
        return None
    before = sorted(o.start for o in occs if o.start is not None and o.start <= notes.modified_at)
    if not occs or len(before) * 2 < len(occs):
        return False
    return notes.modified_at - before[-1] <= timedelta(days=NOTES_FRESH_DAYS)


def _confidence(n: int, with_guests: Sequence[Occ], evidence: Evidence) -> str:
    if n < CONFIDENT_OCCURRENCES or not with_guests:
        return "low"
    read = evidence.notes is not None and evidence.decisions.has_decisions not in ("unknown",)
    return "high" if read else "medium"


def _flags(base: tuple[str, ...], low: bool, has_notes: bool | None, accept_rate: float | None,
           facts: SeriesFacts, evidence: Evidence, followup: float | None) -> tuple[str, ...]:
    flags = list(base)
    flags += ["LOW_ATTENDANCE"] if low else []
    flags += ["NO_NOTES"] if has_notes is False else []
    flags += ["NO_RESPONSE_DATA"] if accept_rate is None else []
    flags += ["ATTENDEES_CAPPED"] if facts.attendees_capped else []
    flags += ["NO_FOLLOWUP"] if followup == 0 else []
    return tuple(flags)


def _links(facts: SeriesFacts, evidence: Evidence) -> tuple[str, ...]:
    links = list(facts.event_urls[:2])
    links += [evidence.notes.url] if evidence.notes else []
    links += [c.permalink for c in evidence.chat.counts if c.permalink][:1] if evidence.chat else []
    return tuple(dict.fromkeys(links))[:MAX_EVIDENCE_LINKS]


def _share(o: Occ, response: str) -> float:
    others = o.accepted + o.tentative + o.declined
    return max(0, o.invited - others) / o.invited if response == "none" else 0.0


def _mean(values: Sequence[float]) -> float | None:
    return statistics.mean(values) if values else None


def _round(value: float | None) -> float | None:
    return None if value is None or math.isnan(value) else round(value, 2)
