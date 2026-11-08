"""The agent's records: pasted blocks, changelog entries, price facts, roadmap matches and changes. Frozen;
`to_dict` gives the JSON-safe shape kept in the checkpointer and the envelope."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

UNNAMED = "Unnamed competitor"
CATEGORIES = ("feature", "pricing", "integration", "deprecation", "security", "other")


@dataclass(frozen=True)
class Block:
    competitor: str
    source: str            # "changelog", "pricing", "fetched" or the word the user wrote
    body: str              # the current text (the `After:` part, or the whole body)
    before: str | None = None
    named: bool = True

    @property
    def key(self) -> str:
        return f"{self.competitor.casefold()}|{self.source}"

    @property
    def is_pricing(self) -> bool:
        return self.source == "pricing"

    def to_dict(self) -> dict:
        return asdict(self)


def block_from(raw: dict[str, Any]) -> Block:
    return Block(competitor=str(raw["competitor"]), source=str(raw["source"]), body=str(raw["body"]),
                 before=raw.get("before"), named=bool(raw.get("named", True)))


@dataclass(frozen=True)
class Entry:
    id: str
    competitor: str
    source: str
    text: str
    date: str | None = None   # ISO yyyy-mm-dd, parsed in code


@dataclass(frozen=True)
class PriceFact:
    plan: str
    kind: str                 # changed | added | removed | limit
    old: str | None = None    # the price or limit as written, e.g. "$20/seat/mo"
    new: str | None = None
    delta_pct: float | None = None
    line: str = ""            # the verbatim line the fact comes from

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RoadmapMatch:
    id: str
    title: str
    url: str
    state: str
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Change:
    competitor: str
    source: str
    category: str
    what: str
    quote: str | None
    date: str | None = None
    keywords: tuple[str, ...] = ()
    price_fact: PriceFact | None = None
    matches: tuple[RoadmapMatch, ...] = field(default=())
    entry_id: str = ""

    @property
    def affects(self) -> RoadmapMatch | None:
        return self.matches[0] if self.matches else None

    def to_dict(self) -> dict:
        return {"competitor": self.competitor, "what": self.what,
                "affects_roadmap_item": self.affects.to_dict() if self.affects else None,
                "category": self.category, "date": self.date, "source": self.source, "quote": self.quote,
                "price_fact": self.price_fact.to_dict() if self.price_fact else None,
                "roadmap_matches": [m.to_dict() for m in self.matches], "keywords": list(self.keywords),
                "entry_id": self.entry_id}
