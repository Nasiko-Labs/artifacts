"""Pure: the reply text. Times, dates and citations are rendered here from checked data, never taken
from the model's prose."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, tzinfo

import prep_prompts as prompts
import timewin
from cards import Item
from context_step import Prepared
from events import Meeting
from evidence import Evidence

_OWNER = {"me": "you owe", "them": "they owe", "unclear": "open"}
_QUOTE_CHARS = 60


@dataclass(frozen=True)
class Report:
    day: date
    zone: timewin.Zone
    prepared: tuple[Prepared, ...]
    overflow: tuple[Meeting, ...] = ()
    not_in_time: tuple[Meeting, ...] = ()
    failed: tuple[Meeting, ...] = ()
    notes: tuple[str, ...] = ()
    dropped: int = 0


@dataclass(frozen=True)
class Rendered:
    """The reply in its three parts, so a refreshed card can be swapped in later."""

    head: str
    cards: tuple[str, ...]
    tail: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n\n".join([self.head, *self.cards, *self.tail])

    def with_card(self, number: int, card: str) -> Rendered:
        return Rendered(self.head, (*self.cards[:number - 1], card, *self.cards[number:]), self.tail)

    def to_dict(self) -> dict:
        return {"head": self.head, "cards": list(self.cards), "tail": list(self.tail)}

    @staticmethod
    def from_dict(data: dict) -> Rendered:
        return Rendered(str(data.get("head") or ""), tuple(data.get("cards") or ()), tuple(data.get("tail") or ()))


def render(report: Report) -> Rendered:
    tz = report.zone.tz
    head = f"Meeting prep for {timewin.day_label(report.day)} (times in {report.zone.name})"
    if report.zone.assumed:
        head += "\nI couldn't tell your time zone, so I assumed UTC. Say e.g. \"in Europe/London\" to change it."
    cards = tuple(render_card(n, p, tz) for n, p in enumerate(report.prepared, start=1))
    return Rendered(head, cards, tuple(_tail(report, tz)))


def render_card(number: int, prepared: Prepared, tz: tzinfo) -> str:
    meeting, card = prepared.meeting, prepared.card
    known = {e.eid: e for e in prepared.evidence}
    title = meeting.event.title or "Untitled meeting"
    lines = [f"### #{number} {timewin.clock(meeting.start, tz)}-{timewin.clock(meeting.end, tz)} · {title}"]
    lines.append("People: " + ("; ".join(_person(p) for p in card.people) or "not listed"))
    purpose = _item(card.purpose, known, tz) if not card.purpose_from_invite else f"{card.purpose.text} (from the invite)"
    lines.append(f"Purpose: {purpose}")
    if card.last_time:
        last = max(known[c].when() for i in card.last_time for c in i.cites)
        lines.append(f"Last time ({timewin.short_date(last, tz)}): " + "; ".join(_item(i, known, tz) for i in card.last_time))
    if card.changed_since:
        label = "Since then" if card.changed_label == "since last time" else "Recent activity"
        lines.append(f"{label}: " + "; ".join(_item(i, known, tz) for i in card.changed_since))
    if card.open_items:
        lines.append("Open between you: " + "; ".join(f"{_OWNER[i.owner or 'unclear']} {_item(i, known, tz)}"
                                                      for i in card.open_items))
    if card.questions:
        lines.append("Ask: " + "  ".join(f"{n}. {_item(q, known, tz)}" for n, q in enumerate(card.questions, 1)))
    else:
        lines.append(prompts.NO_QUESTIONS)
    if prepared.evidence:
        lines.append("Sources: " + " · ".join(_source(e, tz) for e in prepared.evidence))
    if prepared.incomplete:
        lines.append(prompts.STEP_LIMIT_NOTE)
    return "\n".join(lines)


def _person(person) -> str:
    return f"{person.label}, {person.role_note} [{', '.join(person.cites)}]" if person.role_note else person.label


def _item(item: Item, known: Mapping[str, Evidence], tz: tzinfo) -> str:
    newest = max(known[c].when() for c in item.cites)
    return f"{item.text} ({timewin.short_date(newest, tz)}) [{', '.join(item.cites)}]"


def _source(e: Evidence, tz: tzinfo) -> str:
    quote = e.quote if len(e.quote) <= _QUOTE_CHARS else e.quote[:_QUOTE_CHARS - 1] + "…"
    status = f" ({e.status})" if e.status else ""
    return f"{e.eid} {e.label} {timewin.short_date(e.when(), tz)} \"{quote}\"{status} {e.link}"


def _listed(meetings: Sequence[Meeting], tz: tzinfo) -> str:
    return "; ".join(f"{timewin.clock(m.start, tz)} {m.event.title or 'Untitled meeting'}" for m in meetings)


def _tail(report: Report, tz: tzinfo) -> list[str]:
    tail: list[str] = []
    if report.overflow:
        first = timewin.clock(report.overflow[0].start, tz)
        tail.append(f"More meetings, not prepared: {_listed(report.overflow, tz)}. Ask \"prep the {first}\".")
    if report.not_in_time:
        first = timewin.clock(report.not_in_time[0].start, tz)
        tail.append(f"Not prepared in time: {_listed(report.not_in_time, tz)}. Ask \"prep the {first}\".")
    if report.failed:
        tail.append(f"Couldn't prepare (a platform call failed): {_listed(report.failed, tz)}.")
    tail.extend(report.notes)
    if report.dropped:
        tail.append(f"Left out {report.dropped} item(s) I couldn't back with what my tools returned.")
    tail.append(f"{prompts.READ_ONLY_FOOTER} {prompts.DRAFTER_HINT}")
    return tail
