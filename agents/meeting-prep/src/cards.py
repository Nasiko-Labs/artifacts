"""Pure: the card step's answer for one meeting, and the citation and date checks on it.

Every item must cite numbered evidence (E1..En) that exists; "since last time" may cite only evidence
newer than the last meeting; "last time" only the prior meeting or evidence within a day of it. Role
notes need a cite; people must be attendees. Dates on the card come from the evidence, never the model."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import timewin
from events import Attendee, Meeting
from evidence import Evidence
from verbatim import load_json_object, text_of

MAX_QUESTIONS = 3
MAX_ITEMS_PER_SECTION = 5
LAST_TIME_SLACK = timedelta(days=1)
OWNERS = ("me", "them", "unclear")
_CITE = re.compile(r"E?\s*(\d{1,3})", re.IGNORECASE)


@dataclass(frozen=True)
class Item:
    text: str
    cites: tuple[str, ...]
    owner: str = ""


@dataclass(frozen=True)
class Person:
    email: str
    label: str
    role_note: str = ""
    cites: tuple[str, ...] = ()


@dataclass(frozen=True)
class Card:
    """A checked card. `purpose_from_invite` marks a purpose taken from the event, not from evidence;
    `changed_label` is "since last time" or "recent activity" (no prior meeting found)."""

    purpose: Item
    purpose_from_invite: bool
    people: tuple[Person, ...]
    last_time: tuple[Item, ...] = ()
    changed_since: tuple[Item, ...] = ()
    changed_label: str = "recent activity"
    open_items: tuple[Item, ...] = ()
    questions: tuple[Item, ...] = ()
    dropped: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Card:
        def items(key: str) -> tuple[Item, ...]:
            return tuple(Item(i["text"], tuple(i["cites"]), i.get("owner", "")) for i in data.get(key) or ())

        purpose = data["purpose"]
        people = tuple(Person(p["email"], p["label"], p.get("role_note", ""), tuple(p.get("cites") or ()))
                       for p in data.get("people") or ())
        return Card(Item(purpose["text"], tuple(purpose["cites"])), bool(data.get("purpose_from_invite")), people,
                    items("last_time"), items("changed_since"), data.get("changed_label") or "recent activity",
                    items("open_items"), items("questions"), int(data.get("dropped") or 0))


def parse_card(text: str) -> dict | None:
    return load_json_object(text)


def check(raw: Mapping[str, Any] | None, meeting: Meeting, attendees: Sequence[Attendee],
          evidence: Sequence[Evidence], last_meeting_at: datetime | None, now: datetime) -> Card:
    """Keep only what is backed; a missing or unreadable answer still gives a card from the invite."""
    data = raw or {}
    known = {e.eid: e for e in evidence}
    counter = _Counter()

    purpose_raw = data.get("purpose") if isinstance(data.get("purpose"), Mapping) else {}
    purpose = _item(purpose_raw, known, counter)
    from_invite = purpose is None
    if purpose is None:
        purpose = Item(_invite_purpose(meeting), ())

    newer = (lambda e: e.when() > last_meeting_at) if last_meeting_at else (
        lambda e: e.when() >= now - timedelta(days=timewin.RECENT_DAYS))
    near_last = (lambda e: e.kind == "prior_meeting" or abs(e.when() - last_meeting_at) <= LAST_TIME_SLACK) \
        if last_meeting_at else (lambda e: False)

    return Card(
        purpose=purpose,
        purpose_from_invite=from_invite,
        people=_people(data.get("people"), attendees, known, counter),
        last_time=_section(data.get("last_time"), known, counter, near_last),
        changed_since=_section(data.get("changed_since"), known, counter, newer),
        changed_label="since last time" if last_meeting_at else "recent activity",
        open_items=_section(data.get("open_items"), known, counter, None, with_owner=True),
        questions=_section(data.get("questions"), known, counter, None, limit=MAX_QUESTIONS),
        dropped=counter.dropped,
    )


class _Counter:
    def __init__(self) -> None:
        self.dropped = 0


def cite_ids(value: Any, known: Mapping[str, Evidence]) -> tuple[str, ...]:
    """The cited ids that exist, normalised ("E2", "e2", 2, "[E2]" all mean E2), in order, once each."""
    raw = value if isinstance(value, list) else [value] if value is not None else []
    ids = []
    for cite in raw:
        match = _CITE.search(str(cite))
        eid = f"E{int(match.group(1))}" if match else ""
        if eid in known and eid not in ids:
            ids.append(eid)
    return tuple(ids)


def _item(raw: Any, known: Mapping[str, Evidence], counter: _Counter, allowed=None, *,
          with_owner: bool = False) -> Item | None:
    if not isinstance(raw, Mapping):
        return None
    text = text_of(raw.get("text"), 400)
    cites = cite_ids(raw.get("cites"), known)
    if allowed is not None and not all(allowed(known[c]) for c in cites):
        cites = ()  # one cite outside the section's time rule voids the item
    if not text or not cites:
        if text:
            counter.dropped += 1
        return None
    owner = text_of(raw.get("owner"), 10).casefold() if with_owner else ""
    return Item(text, cites, (owner if owner in OWNERS else "unclear") if with_owner else "")


def _section(raw: Any, known: Mapping[str, Evidence], counter: _Counter, allowed, *, with_owner: bool = False,
             limit: int = MAX_ITEMS_PER_SECTION) -> tuple[Item, ...]:
    items = [i for i in (_item(r, known, counter, allowed, with_owner=with_owner)
                         for r in (raw if isinstance(raw, list) else [])) if i is not None]
    return tuple(items[:limit])


def _people(raw: Any, attendees: Sequence[Attendee], known: Mapping[str, Evidence],
            counter: _Counter) -> tuple[Person, ...]:
    """Every attendee (other than the user) in invite order; a role note only when it is cited.
    Emails the model named that are not attendees are ignored."""
    notes: dict[str, tuple[str, tuple[str, ...]]] = {}
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping):
            continue
        email = text_of(entry.get("email"), 200).casefold()
        note = text_of(entry.get("role_note"), 200)
        cites = cite_ids(entry.get("cites"), known)
        if note and not cites:
            counter.dropped += 1
        if email and note and cites:
            notes[email] = (note, cites)
    return tuple(Person(a.email, a.label, *notes.get(a.email.casefold(), ("", ()))) for a in attendees)


def _invite_purpose(meeting: Meeting) -> str:
    event = meeting.event
    title = event.title or "Untitled meeting"
    return f"{title}: {event.description_excerpt}" if event.description_excerpt else title
