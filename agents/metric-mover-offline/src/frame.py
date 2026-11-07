"""The analysable table: one row per segment cell with its before/after values, and its plain-dict form
(stored in the checkpointer, so follow-ups recompute from it without the paste).

Values: additive rows are `(before, after)`; rate rows are `(before_num, before_den, after_num, after_den)`."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class Row:
    keys: tuple[str, ...]
    vals: tuple[float, ...]


@dataclass(frozen=True)
class Frame:
    kind: str                         # additive | rate
    layout: str                       # wide | rate | long
    dims: tuple[str, ...]
    rows: tuple[Row, ...]
    value_cols: tuple[str, ...]
    labels: tuple[str, str] = ("before", "after")
    stated_total: tuple[float, ...] | None = None
    notes: tuple[str, ...] = ()
    skipped: int = 0
    source_rows: int = 0
    excluded: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"kind": self.kind, "layout": self.layout, "dims": list(self.dims),
                "rows": [[list(r.keys), list(r.vals)] for r in self.rows], "value_cols": list(self.value_cols),
                "labels": list(self.labels), "stated_total": list(self.stated_total) if self.stated_total else None,
                "notes": list(self.notes), "skipped": self.skipped, "source_rows": self.source_rows,
                "excluded": list(self.excluded)}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Frame:
        total = data.get("stated_total")
        return Frame(kind=str(data["kind"]), layout=str(data.get("layout") or "wide"),
                     dims=tuple(str(d) for d in data["dims"]),
                     rows=tuple(Row(tuple(str(k) for k in keys), tuple(float(v) for v in vals))
                                for keys, vals in data["rows"]),
                     value_cols=tuple(str(c) for c in data.get("value_cols") or ()),
                     labels=tuple(str(x) for x in data.get("labels") or ("before", "after")),
                     stated_total=tuple(float(v) for v in total) if total else None,
                     notes=tuple(str(n) for n in data.get("notes") or ()), skipped=int(data.get("skipped") or 0),
                     source_rows=int(data.get("source_rows") or 0),
                     excluded=tuple(str(x) for x in data.get("excluded") or ()))

    def segment_names(self) -> set[str]:
        return {k for row in self.rows for k in row.keys}

    def without(self, segments: Iterable[str]) -> Frame:
        """A new frame without the rows whose any segment value is one of `segments` (case-insensitive).
        The stated total no longer applies to what is left."""
        drop = {s.casefold() for s in segments}
        kept = tuple(r for r in self.rows if not any(k.casefold() in drop for k in r.keys))
        names = tuple(dict.fromkeys((*self.excluded, *(s for s in segments))))
        return replace(self, rows=kept, excluded=names, stated_total=None)


def merge(rows: Iterable[Row]) -> tuple[list[Row], int]:
    """Rows with the same keys summed, and how many duplicates were folded in."""
    sums: dict[tuple[str, ...], list[float]] = {}
    duplicates = 0
    for row in rows:
        if row.keys in sums:
            duplicates += 1
            sums[row.keys] = [a + b for a, b in zip(sums[row.keys], row.vals, strict=True)]
        else:
            sums[row.keys] = list(row.vals)
    return [Row(k, tuple(v)) for k, v in sums.items()], duplicates
