"""Pure: a table the user pasted, or a previous workflow step's envelope (`table` from a metrics-pull style
step, `narrative_context` as the user's context). Never requires either."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import table as tables
from parse import without_context

_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class Input:
    table: tables.Table | None
    context: str | None = None
    source: str = ""          # "pasted" | "envelope" | ""


def read(text: str) -> Input:
    envelope = _envelope(text)
    if envelope is not None:
        found = from_envelope(envelope)
        if found.table is not None:
            return found
    found_table = tables.from_text(without_context(text), "pasted")
    return Input(found_table, None, "pasted" if found_table else "")


def from_envelope(data: Mapping[str, Any]) -> Input:
    context = data.get("narrative_context")
    raw = data.get("table")
    rows = _rows(raw)
    found = tables.from_rows(rows, "envelope") if rows else None
    return Input(found, str(context).strip() if isinstance(context, str) and context.strip() else None,
                 "envelope" if found else "")


def _rows(raw: Any) -> list[list[Any]]:
    """`{"columns": [...], "rows": [[...]]}`, `{"header": [...], "rows": [...]}`, a list of row objects, or a
    list of lists (header first)."""
    if isinstance(raw, Mapping):
        header = raw.get("columns") or raw.get("header")
        body = raw.get("rows")
        if isinstance(header, list) and isinstance(body, list):
            names = [h.get("name") if isinstance(h, Mapping) else h for h in header]
            return [names, *[_row(r, names) for r in body]]
    if isinstance(raw, list) and raw:
        if all(isinstance(r, Mapping) for r in raw):
            names = list(dict.fromkeys(k for r in raw for k in r))
            return [names, *[[r.get(k) for k in names] for r in raw]]
        if all(isinstance(r, list) for r in raw):
            return [list(r) for r in raw]
    return []


def _row(row: Any, names: list) -> list:
    if isinstance(row, Mapping):
        return [row.get(n) for n in names]
    return list(row) if isinstance(row, list) else []


def _envelope(text: str) -> Mapping[str, Any] | None:
    body = (text or "").strip()
    fenced = _FENCED_JSON.search(body)
    candidate = fenced.group(1) if fenced else body
    if not candidate.startswith("{"):
        return None
    try:
        data = json.loads(candidate)
    except ValueError:
        return None
    return data if isinstance(data, Mapping) else None


