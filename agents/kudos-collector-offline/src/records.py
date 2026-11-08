"""The record model: one PR, one praise message or one shipped feature, as parsed in code from the paste.
JSON-safe (to_dict/from_dict) so records are checkpointed for follow-ups and carried in a pause."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

PR, PRAISE, SHIPPED = "pr", "praise", "shipped"
KINDS = (PR, PRAISE, SHIPPED)
ID_PREFIX = {PR: "p", PRAISE: "k", SHIPPED: "f"}


@dataclass(frozen=True)
class Record:
    id: str
    kind: str
    text: str                       # the record as pasted (what the model sees, fenced)
    title: str = ""                 # PR title / feature name
    author: str = ""                # PR author as written
    speaker: str = ""               # praise: who wrote the message
    body: str = ""                  # praise: the message after the speaker; PR: description
    owners: tuple[str, ...] = ()    # shipped: owners as written
    date: str | None = None         # ISO date
    url: str = ""
    number: str = ""                # PR number, digits only
    refs: tuple[str, ...] = field(default_factory=tuple)   # shipped: PR numbers / links it names

    def to_dict(self) -> dict:
        data = asdict(self)
        data["owners"], data["refs"] = list(self.owners), list(self.refs)
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Record:
        names = {f.name for f in fields(Record)}
        clean = {k: v for k, v in data.items() if k in names}
        clean["owners"] = tuple(str(o) for o in clean.get("owners") or ())
        clean["refs"] = tuple(str(r) for r in clean.get("refs") or ())
        if clean.get("number") is not None:
            clean["number"] = str(clean["number"]).split(".")[0]
        return Record(**clean)

    @property
    def keys(self) -> set[str]:
        """Identifiers used to merge a PR with a feature that names it (number or link)."""
        out = {f"#{self.number}"} if self.number else set()
        return out | ({self.url.rstrip("/").casefold()} if self.url else set())


def renumber(records: list[Record], start: dict[str, int] | None = None) -> list[Record]:
    """Stable ids per kind (p1, k1, f1, ...), continuing from `start` (used by "add these")."""
    counters = dict(start or {})
    out = []
    for r in records:
        counters[r.kind] = counters.get(r.kind, 0) + 1
        out.append(Record(**{**r.__dict__, "id": f"{ID_PREFIX[r.kind]}{counters[r.kind]}"}))
    return out


def counters_of(records: list[Record]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        n = int(r.id[1:]) if r.id[1:].isdigit() else 0
        out[r.kind] = max(out.get(r.kind, 0), n)
    return out
