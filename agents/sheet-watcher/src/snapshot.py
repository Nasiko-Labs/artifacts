"""Pure: the snapshot one check leaves for the next. It holds, per row key, the tracked columns' values, a
display label and a hash of the whole row, plus when and from which sheet it was taken. It lives in the
conversation's checkpointer (encrypted at rest by the state API) and is replaced only after a check rendered
fully; nothing else about the sheet is kept.

Row identity: the key column's value, trimmed and lower-cased; a repeated key gets a `#2`, `#3` suffix and a
`duplicate_key` note. With no key column, rows are keyed by row number (reordering then breaks the diff, so
confidence is low)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rules import Resolved
from table import Table

HASH_CHARS = 16  # a truncated SHA-256 keeps a 5,000-row snapshot well inside the state API's size limit


@dataclass(frozen=True)
class Row:
    key: str
    label: str
    values: Mapping[str, str]
    row_hash: str
    order: int


@dataclass(frozen=True)
class Keyed:
    rows: tuple[Row, ...]
    duplicates: tuple[str, ...]      # keys that appeared more than once (as written)


def key_rows(table: Table, resolved: Resolved) -> Keyed:
    key_at = table.index(resolved.key_column)
    label_at = table.index(resolved.display_column)
    tracked = [(c, table.index(c)) for c in resolved.tracked]
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    rows: list[Row] = []
    for order, (cells, number) in enumerate(zip(table.rows, table.row_numbers, strict=True)):
        raw = cells[key_at].strip() if key_at is not None else ""
        base = raw.casefold() if raw else f"row {number}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        key = base if count == 1 else f"{base}#{count}"
        if count == 2:
            duplicates.append(raw or base)
        label = (cells[label_at].strip() if label_at is not None else "") or raw or f"row {number}"
        values = {c: cells[i] for c, i in tracked if i is not None}
        rows.append(Row(key, label, values, row_hash(cells), order))
    return Keyed(tuple(rows), tuple(duplicates))


def row_hash(cells: tuple[str, ...]) -> str:
    return hashlib.sha256("\x1f".join(c.strip() for c in cells).encode("utf-8")).hexdigest()[:HASH_CHARS]


def build(keyed: Keyed, resolved: Resolved, source: Mapping[str, Any], header: tuple[str, ...],
          taken_at: datetime) -> dict:
    return {
        "taken_at": taken_at.isoformat(),
        "source": dict(source),
        "key_column": resolved.key_column,
        "columns": list(header),
        "rows": {r.key: {"label": r.label, "values": dict(r.values), "hash": r.row_hash, "order": r.order}
                 for r in keyed.rows},
    }


def rows_of(snapshot: Mapping[str, Any]) -> dict[str, Row]:
    out: dict[str, Row] = {}
    for key, data in (snapshot.get("rows") or {}).items():
        if isinstance(data, Mapping):
            out[str(key)] = Row(str(key), str(data.get("label") or key), dict(data.get("values") or {}),
                                str(data.get("hash") or ""), int(data.get("order") or 0))
    return out


def same_source(snapshot: Mapping[str, Any] | None, source: Mapping[str, Any]) -> bool:
    if not snapshot:
        return False
    before = snapshot.get("source") or {}
    if str(before.get("id") or "") != str(source.get("id") or ""):
        return False
    tabs = (str(before.get("tab") or "").casefold(), str(source.get("tab") or "").casefold())
    return not all(tabs) or tabs[0] == tabs[1]  # a tab the read did not name is the same tab
