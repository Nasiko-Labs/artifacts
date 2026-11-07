"""The data the brief is built from. Frozen dataclasses; `to_dict` / `from_dict` round-trip through the
checkpointer (plain JSON-able dicts)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any

KINDS = ("fact", "statistic", "opinion", "prediction", "recommendation", "method")
VIEW_KINDS = frozenset({"opinion", "prediction", "recommendation"})   # rendered as "X says", never as fact
CONFLICT_KINDS = frozenset({"fact", "statistic", "prediction"})
SOURCE_TYPES = ("interview", "article", "report", "forum", "notes", "internal")


def _from(cls, data: Mapping[str, Any]):
    names = {f.name for f in fields(cls)}
    values = {k: v for k, v in dict(data or {}).items() if k in names}
    for key in ("claim_ids", "source_ids"):
        if key in values:
            values[key] = tuple(values[key] or ())
    return cls(**values)


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    text: str = ""
    type: str = ""
    date: str = ""            # ISO yyyy-mm-dd as stated, else ""
    author: str = ""
    words: int = 0
    read: str = "full"        # full | partial | not_read

    def to_dict(self, with_text: bool = True) -> dict:
        data = asdict(self)
        return data if with_text else {k: v for k, v in data.items() if k != "text"}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Source:
        return _from(Source, data)


@dataclass(frozen=True)
class Claim:
    id: str
    source_id: str
    kind: str
    text: str
    quote: str
    topic_hint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Claim:
        return _from(Claim, data)


@dataclass(frozen=True)
class Finding:
    id: str
    statement: str
    claim_ids: tuple[str, ...]
    source_ids: tuple[str, ...] = ()
    agreement: str = "single"                 # agree | single | conflict
    conflict: dict | None = None              # {"a": claim id, "b": claim id, "difference": str}
    confidence: str = "low"
    agree_pairs: int = 0
    conflict_pairs: int = 0
    is_view: bool = False                     # every claim is an opinion / prediction / recommendation

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Finding:
        return _from(Finding, data)


@dataclass(frozen=True)
class Drops:
    """What the checks removed, reported in the brief."""

    quotes: int = 0           # claims whose quote (or a number in them) is not in their source
    irrelevant: int = 0
    unclustered: int = 0
    empty_findings: int = 0
    relations: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Drops:
        return _from(Drops, data)
