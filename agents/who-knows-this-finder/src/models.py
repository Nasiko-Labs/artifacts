"""The records this agent works with. Every field of an `Evidence` was copied from a tool result (or a
pasted row) and checked in code; nothing here is written by the model about a person."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

Kind = Literal["commit", "pr", "review", "answer", "doc_author", "doc_edit"]
Source = Literal["code", "chat", "docs"]

KINDS: tuple[Kind, ...] = ("commit", "pr", "review", "answer", "doc_author", "doc_edit")
SOURCES: tuple[Source, ...] = ("code", "chat", "docs")
SOURCE_OF_KIND: Mapping[str, Source] = {
    "commit": "code", "pr": "code", "review": "code", "answer": "chat", "doc_author": "docs", "doc_edit": "docs",
}


@dataclass(frozen=True)
class Who:
    """A person as one source names them. Any field may be empty; `handle` is the source's own login."""

    name: str = ""
    email: str = ""
    handle: str = ""

    def is_empty(self) -> bool:
        return not (self.name or self.email or self.handle)

    def label(self) -> str:
        return self.name or self.handle or self.email or "unknown"


@dataclass(frozen=True)
class Evidence:
    """One piece of evidence. `detail` is the file path, pull request title, thread question or document
    title; `repo` / `channel` scope it; `state` is a review's state; `quote` is a verified answer quote."""

    kind: Kind
    who: Who
    detail: str
    day: date
    link: str = ""
    repo: str = ""
    channel: str = ""
    state: str = ""
    quote: str = ""

    @property
    def source(self) -> Source:
        return SOURCE_OF_KIND[self.kind]

    def to_dict(self) -> dict:
        data = asdict(self)
        return {**data, "day": self.day.isoformat()}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Evidence:
        who = data.get("who") or {}
        return Evidence(kind=data["kind"], who=Who(str(who.get("name") or ""), str(who.get("email") or ""),
                                                    str(who.get("handle") or "")),
                        detail=str(data.get("detail") or ""), day=date.fromisoformat(str(data["day"])),
                        link=str(data.get("link") or ""), repo=str(data.get("repo") or ""),
                        channel=str(data.get("channel") or ""), state=str(data.get("state") or ""),
                        quote=str(data.get("quote") or ""))
