"""Pure: the stored handoff (what the checkpointer keeps per conversation), classification over it, the
code ordering, and the output envelope. `confidence` and `needs_human` are computed here, never taken
from the model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime

import hot
import know
import status
from entries import Entry
from items import OPEN, PROMISE, RESOLVED, WAITING, Instruction, Item

HIGH_MIN_ANCHORED = 0.7
LOW_MAX_FALLBACK = 0.25


@dataclass(frozen=True)
class Handoff:
    entries: tuple[Entry, ...]
    items: tuple[Item, ...]
    instructions: tuple[Instruction, ...]
    handoff_at: str
    instruction: str = ""
    next_shift: str | None = None
    headline: str | None = None
    know_order: tuple[str, ...] = ()
    dropped: int = 0
    windows: int = 1
    done: int = 1
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict:
        return _plain({"entries": [e.to_dict() for e in self.entries], "items": [i.to_dict() for i in self.items],
                "instructions": [i.to_dict() for i in self.instructions], "handoff_at": self.handoff_at,
                "instruction": self.instruction, "next_shift": self.next_shift, "headline": self.headline,
                "know_order": list(self.know_order), "dropped": self.dropped, "windows": self.windows,
                "done": self.done, "notes": list(self.notes)})

    @staticmethod
    def from_dict(data: dict) -> Handoff:
        return Handoff(entries=tuple(Entry.from_dict(e) for e in data.get("entries") or ()),
                       items=tuple(Item.from_dict(i) for i in data.get("items") or ()),
                       instructions=tuple(Instruction.from_dict(i) for i in data.get("instructions") or ()),
                       handoff_at=str(data.get("handoff_at") or ""), instruction=str(data.get("instruction") or ""),
                       next_shift=data.get("next_shift"), headline=data.get("headline"),
                       know_order=tuple(data.get("know_order") or ()), dropped=int(data.get("dropped") or 0),
                       windows=int(data.get("windows") or 1), done=int(data.get("done") or 1),
                       notes=tuple(data.get("notes") or ()))

    @property
    def at(self) -> datetime:
        return datetime.fromisoformat(self.handoff_at)

    @property
    def by_id(self) -> dict[str, Entry]:
        return {e.id: e for e in self.entries}


def _plain(data: dict) -> dict:
    """JSON-safe (tuples become lists), as the checkpointer and the A2A data part need."""
    return json.loads(json.dumps(data))


def classify(items: tuple[Item, ...], by_id: dict[str, Entry], at: datetime) -> tuple[Item, ...]:
    return tuple(hot.classify(status.classify(i, by_id), by_id, at) for i in items)


def ordered(items: tuple[Item, ...], by_id: dict[str, Entry]) -> list[Item]:
    def first(item: Item) -> int:
        return by_id[item.entries[0]].order if item.entries and item.entries[0] in by_id else 0

    def key(item: Item) -> tuple:
        if item.hot:
            return (0, item.due_at is None, item.due_at or "", -len(item.entries), first(item))
        group = {OPEN: 1, WAITING: 2, RESOLVED: 3}[item.status]
        return (group, False, "", 0, first(item))
    return sorted(items, key=key)


def need_to_know(handoff: Handoff) -> list[know.Know]:
    return know.build(handoff.instructions, handoff.items, handoff.by_id, list(handoff.know_order))


def confidence(handoff: Handoff) -> str:
    items = handoff.items
    stamps = any(e.stamp for e in handoff.entries)
    refs = any(e.refs for e in handoff.entries)
    fell_back = sum(1 for i in items if i.fallback)
    if not items or handoff.done < handoff.windows or fell_back > LOW_MAX_FALLBACK * len(items) or not (stamps or refs):
        return "low"
    clean = not any(i.repointed or i.fallback for i in items) and not any(k.fallback for k in handoff.instructions)
    anchored = sum(1 for i in items if i.refs or i.owner)
    if clean and anchored >= HIGH_MIN_ANCHORED * len(items) and stamps:
        return "high"
    return "medium"


def needs_human(handoff: Handoff, level: str) -> bool:
    unowned_hot = any(i.hot and not i.owner for i in handoff.items)
    untimed = any(k.kind == PROMISE and not know.promise_time(k) for k in handoff.instructions)
    return level == "low" or unowned_hot or untimed


def counts(handoff: Handoff) -> dict[str, int]:
    items = handoff.items
    return {"hot": sum(1 for i in items if i.hot), "resolved": sum(1 for i in items if i.status == RESOLVED),
            "open": sum(1 for i in items if i.status == OPEN and not i.hot),
            "waiting": sum(1 for i in items if i.status == WAITING and not i.hot),
            "know": len(need_to_know(handoff))}


def summary(handoff: Handoff) -> str:
    c = counts(handoff)
    live = c["hot"] + c["open"] + c["waiting"]
    return (f"{live} items: {c['hot']} hot, {c['open']} open, {c['waiting']} waiting; {c['resolved']} resolved today; "
            f"{c['know']} things to know")


def envelope(handoff: Handoff, markdown: str) -> dict:
    level = confidence(handoff)
    return _plain({"summary": summary(handoff), "items": [i.to_dict() for i in ordered(handoff.items, handoff.by_id)],
            "handoff_markdown": markdown, "headline": handoff.headline,
            "instructions": [i.to_dict() for i in handoff.instructions],
            "need_to_know": [k.to_dict() for k in need_to_know(handoff)], "as_of": handoff.handoff_at,
            "confidence": level, "needs_human": needs_human(handoff, level), "notes": list(handoff.notes)})


def mark_resolved(handoff: Handoff, item_ids: set[str]) -> Handoff:
    items = tuple(replace(i, marked=RESOLVED) if i.id in item_ids else i for i in handoff.items)
    return replace(handoff, items=classify(items, handoff.by_id, handoff.at))
