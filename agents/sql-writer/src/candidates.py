"""Pure: the options offered when the model asks which column holds a concept, or how two tables join. Every
option is computed from the pasted schema, never taken from the model."""

from __future__ import annotations

import re

from schema import Schema

MAX = 9


def _parts(text: str) -> set[str]:
    return {p for p in re.split(r"[_\W]+", (text or "").casefold()) if len(p) > 2}


def columns(schema: Schema, concept: str, tables: list[str]) -> list[str]:
    """Columns sharing a word with the concept; else every column of the tables the model named."""
    wanted = _parts(concept)
    out = [f"{t.name}.{c.name}" for t in schema.tables for c in t.columns if wanted & _parts(c.name)]
    if not out:
        named = [schema.table(n) for n in tables if isinstance(n, str)]
        out = [f"{t.name}.{c.name}" for t in named if t is not None for c in t.columns]
    return list(dict.fromkeys(out))[:MAX]


def joins(schema: Schema, a: str, b: str) -> list[str]:
    """Key paths between two tables: declared foreign keys, `<b>_id = <b>.id` names, then shared column names."""
    ta, tb = schema.table(a), schema.table(b)
    if ta is None or tb is None:
        return []
    out = []
    for x, y in ((ta, tb), (tb, ta)):
        for fk in x.foreign_keys:
            if fk.ref_table.casefold() == y.name.casefold():
                out += [f"{x.name}.{c} = {y.name}.{r}" for c, r in zip(fk.columns, fk.ref_columns)]
    for x, y in ((ta, tb), (tb, ta)):
        stem = y.name.casefold().rstrip("s")
        for c in x.columns:
            if c.name.casefold() in (f"{stem}_id", f"{y.name.casefold()}_id") and y.column("id") is not None:
                out.append(f"{x.name}.{c.name} = {y.name}.id")
    shared = [c.name for c in ta.columns if tb.column(c.name) is not None]
    out += [f"{ta.name}.{n} = {tb.name}.{n}" for n in shared]
    return list(dict.fromkeys(out))[:MAX]
