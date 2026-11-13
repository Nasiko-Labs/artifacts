"""The summary's records."""

from __future__ import annotations

from dataclasses import dataclass, field

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
