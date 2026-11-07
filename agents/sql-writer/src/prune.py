"""Pure: a schema over 40 tables is pruned in code before the model sees it: the tables whose names or columns share
a word with the question, plus tables one foreign-key hop away, at most 15. The reply names what was considered."""

from __future__ import annotations

import re

from schema import Schema, Table

PRUNE_ABOVE = 40
KEEP_MAX = 15
_WORD = re.compile(r"[a-z][a-z0-9]+")
_STOP = frozenset(re.findall(r"\w+", (
    "the and for from with what which show give list per each by of in on to all how many much top count "
    "total sum average avg table tables column columns query sql write me my our"
)))


def _stem(word: str) -> str:
    for suffix in ("ies", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _words(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall((text or "").casefold()) if w not in _STOP}


def _table_words(table: Table) -> set[str]:
    names = [table.name, *(c.name for c in table.columns)]
    return {_stem(p) for n in names for p in re.split(r"[_\W]+", n.casefold()) if len(p) > 2}


def prune(schema: Schema, question: str) -> tuple[Schema, list[str]]:
    """(schema the model sees, names of the tables considered; empty when nothing was pruned)."""
    if len(schema.tables) <= PRUNE_ABOVE:
        return schema, []
    asked = _words(question)
    scored = sorted(((len(asked & _table_words(t)) + (2 if _stem(t.name.casefold()) in asked else 0), i, t)
                     for i, t in enumerate(schema.tables)), key=lambda s: (-s[0], s[1]))
    picked = [t for score, _, t in scored if score > 0][:KEEP_MAX]
    names = {t.name.casefold() for t in picked}
    for table in list(picked):
        for neighbour in _neighbours(schema, table):
            if len(picked) < KEEP_MAX and neighbour.name.casefold() not in names:
                picked.append(neighbour)
                names.add(neighbour.name.casefold())
    kept = tuple(t for t in schema.tables if t.name.casefold() in names)
    indexes = tuple(i for i in schema.indexes if i.table.casefold() in names)
    return Schema(kept, indexes, schema.skipped), [t.name for t in kept]


def _neighbours(schema: Schema, table: Table) -> list[Table]:
    out = [schema.table(fk.ref_table) for fk in table.foreign_keys]
    out += [t for t in schema.tables if any(fk.ref_table.casefold() == table.name.casefold() for fk in t.foreign_keys)]
    return [t for t in out if t is not None]
