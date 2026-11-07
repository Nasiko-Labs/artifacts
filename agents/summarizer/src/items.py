"""The summary's records and their plain-dict form (stored in the checkpointer)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

KINDS = ("tldr", "decision", "action")
NOT_STATED = "Not stated"


@dataclass(frozen=True)
class Item:
    """One checked line: `snippet` is a verbatim quote found in the source; `owner`/`due` are actions only."""

    kind: str
    text: str
    snippet: str
    owner: str = ""
    due: str = ""
    item_id: str = ""


@dataclass(frozen=True)
class Summary:
    tldr: tuple[Item, ...] = ()
    decisions: tuple[Item, ...] = ()
    actions: tuple[Item, ...] = ()
    dropped: int = 0   # items the code could not back with the source
    capped: int = 0    # items over the section caps
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.tldr

    def all_items(self) -> tuple[Item, ...]:
        return (*self.tldr, *self.decisions, *self.actions)

    def to_dict(self) -> dict:
        return {"tldr": items_to_state(self.tldr), "decisions": items_to_state(self.decisions),
                "actions": items_to_state(self.actions), "dropped": self.dropped, "capped": self.capped,
                "notes": list(self.notes)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Summary:
        data = data or {}
        return Summary(tldr=items_from_state(data.get("tldr")), decisions=items_from_state(data.get("decisions")),
                       actions=items_from_state(data.get("actions")), dropped=int(data.get("dropped") or 0),
                       capped=int(data.get("capped") or 0), notes=tuple(str(n) for n in data.get("notes") or ()))


def items_to_state(items: Sequence[Item]) -> list[dict]:
    return [asdict(item) for item in items]


def items_from_state(data: Sequence[Mapping[str, Any]] | None) -> tuple[Item, ...]:
    return tuple(Item(**{k: str(v or "") for k, v in dict(d).items() if k in Item.__dataclass_fields__})
                 for d in data or ())
