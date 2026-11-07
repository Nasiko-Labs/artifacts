"""Pure: index suggestions as text (never executed), from the Q02 / Q17 lint findings and the X01 / X03 / X04 plan
findings. Equality columns come first, the range column last. Without an index list or an EXPLAIN there is
nothing to ground them in, so the agent asks for the indexes instead."""

from __future__ import annotations

import re

from findings import Finding
from schema import Schema

ASK = "Tell me your existing indexes (or paste EXPLAIN output) and I'll suggest specific ones."
CAVEAT = "Each index slows writes to the table and takes space; without row counts this advice is generic."
_SOURCES = ("Q02", "Q17", "X01", "X03", "X04")
_RANGE = re.compile(r"<|>|\bbetween\b|\blike\b|date\(|trunc", re.IGNORECASE)


def suggest(findings: list[Finding], schema: Schema, has_plan: bool) -> tuple[list[dict], str]:
    """(suggestions, note)."""
    if not schema.indexes and not has_plan:
        return [], ASK
    per_table: dict[str, list[tuple[bool, str, str]]] = {}
    for f in findings:
        if f.rule not in _SOURCES or not f.table or not f.columns:
            continue
        table = schema.table(f.table)
        name = table.name if table else f.table
        for col in f.columns:
            if table is not None and table.column(col) is None:
                continue
            per_table.setdefault(name, []).append((bool(_RANGE.search(f.evidence)), col, f.id))
    out = []
    for table, entries in per_table.items():
        ordered = [c for _, c, _ in sorted(entries, key=lambda e: e[0])]
        cols = list(dict.fromkeys(c.casefold() for c in ordered))[:3]
        if _exists(schema, table, cols):
            continue
        ids = sorted({fid for _, _, fid in entries})
        out.append({"sql": f"CREATE INDEX idx_{table}_{'_'.join(cols)} ON {table} ({', '.join(cols)});",
                    "reason": f"Serves the filter in {', '.join(ids)}.", "caveat": CAVEAT})
    return out, ""


def _exists(schema: Schema, table: str, cols: list[str]) -> bool:
    for index in schema.indexes:
        lead = [c.casefold() for c in index.columns[:len(cols)]]
        if index.table.casefold() == table.casefold() and lead == cols:
            return True
    found = schema.table(table)
    return bool(found and [c.casefold() for c in found.primary_key[:len(cols)]] == cols)
