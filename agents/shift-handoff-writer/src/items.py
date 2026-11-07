"""Pure: the item and instruction records. The model proposes the words; code fills status, hot-ness,
reasons and every check flag. Records are frozen; changes make new copies (`dataclasses.replace`)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

OPEN, WAITING, RESOLVED = "open", "waiting", "resolved"
INSTRUCTION, PROMISE = "instruction", "promise"
OWNER_NOT_STATED = "owner not stated"


@dataclass(frozen=True)
class Reason:
    cue: str
    quote: str


@dataclass(frozen=True)
class Item:
    id: str
    what: str
    snippet: str
    snippet_entry: str
    entries: tuple[str, ...]
    refs: tuple[str, ...] = ()
    owner: str | None = None
    customer: str | None = None
    status_words: str | None = None
    next_step: str | None = None
    due: str | None = None
    history: tuple[str, ...] = ()
    repointed: bool = False
    fallback: bool = False
    marked: str | None = None          # a user's "mark #4812 resolved" (code override)
    status: str = OPEN
    waiting_on: str | None = None
    hot: bool = False
    hot_reasons: tuple[Reason, ...] = ()
    due_at: str | None = None          # ISO, when the due text parsed
    due_unparsed: bool = False

    def to_dict(self) -> dict:
        data = asdict(self)
        data["hot_reasons"] = [asdict(r) for r in self.hot_reasons]
        return data

    @staticmethod
    def from_dict(data: dict) -> Item:
        known = {f.name for f in fields(Item)}
        values = {k: v for k, v in data.items() if k in known}
        for name in ("entries", "refs", "history"):
            values[name] = tuple(values.get(name) or ())
        values["hot_reasons"] = tuple(Reason(**r) for r in values.get("hot_reasons") or ())
        return Item(**values)


@dataclass(frozen=True)
class Instruction:
    id: str
    text: str
    kind: str
    entry: str
    snippet: str
    time: str | None = None
    fallback: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Instruction:
        known = {f.name for f in fields(Instruction)}
        return Instruction(**{k: v for k, v in data.items() if k in known})


def owner_text(item: Item) -> str:
    return item.owner or OWNER_NOT_STATED


def label(item: Item) -> str:
    """How an item is named in a list: its refs, then its words."""
    refs = " ".join(item.refs)
    return f"{refs} {item.what}".strip()
