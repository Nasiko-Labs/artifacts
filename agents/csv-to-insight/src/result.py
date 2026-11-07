"""Pure data: what one analysis returns. Facts are code-built sentences with exact, formatted numbers;
the model may only reword them, citing their ids."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

MAX_TABLE_ROWS = 20


@dataclass(frozen=True)
class Fact:
    text: str
    values: tuple[str, ...] = ()
    id: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "values": list(self.values)}

    @staticmethod
    def from_dict(data: dict) -> Fact:
        return Fact(str(data.get("text") or ""), tuple(str(v) for v in data.get("values") or ()),
                    str(data.get("id") or ""))


@dataclass(frozen=True)
class Result:
    kind: str
    columns: dict[str, str]
    title: str
    headers: tuple[str, ...] = ()
    table: tuple[tuple[str, ...], ...] = ()
    facts: tuple[Fact, ...] = ()
    chart: dict | None = None         # {"kind": "line"|"bar", "title", "x": [...], "series": {name: [...]}}
    flags: tuple[str, ...] = ()       # data-quality caveats found while running
    id: str = ""
    extra: dict = field(default_factory=dict)

    def numbered(self, rid: str, first_fact: int) -> Result:
        facts = tuple(replace(f, id=f"f{first_fact + n}") for n, f in enumerate(self.facts))
        return replace(self, id=rid, facts=facts)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "columns": dict(self.columns), "title": self.title,
                "headers": list(self.headers), "table": [list(r) for r in self.table],
                "facts": [f.to_dict() for f in self.facts], "chart": plain(self.chart), "flags": list(self.flags)}

    @staticmethod
    def from_dict(data: dict) -> Result:
        return Result(kind=str(data.get("kind") or ""), columns=dict(data.get("columns") or {}),
                      title=str(data.get("title") or ""), headers=tuple(data.get("headers") or ()),
                      table=tuple(tuple(r) for r in data.get("table") or ()),
                      facts=tuple(Fact.from_dict(f) for f in data.get("facts") or ()), chart=data.get("chart"),
                      flags=tuple(data.get("flags") or ()), id=str(data.get("id") or ""))


def plain(value):
    """Built-in types only (numpy scalars → Python), so the checkpointer can store a chart."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    item = getattr(value, "item", None)
    return item() if callable(item) and not isinstance(value, str | bytes) else value


def rows(table: list[list[str]] | list[tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(str(c) for c in r) for r in table[:MAX_TABLE_ROWS])
