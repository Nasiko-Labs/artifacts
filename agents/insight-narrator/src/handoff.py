"""Pure: find the source of a turn — a previous workflow step's envelope or a pasted table.

Envelope fields read: `findings[]` or `items[]` with a `text` (their `values` / `display` strings become the
facts' display strings, used as facts directly), else a `table` (text) or `rows` / `items` (flat objects)
field read as a table. Anything else is not an envelope."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

import prompts
from fact import KINDS, Fact, FactSet
from sniff import find_table, records_from_json

ENVELOPE_NOTE = "I used the findings from the earlier step as the facts."


@dataclass(frozen=True)
class Source:
    instruction: str
    records: tuple[tuple[str, ...], ...] = ()
    facts: FactSet | None = None


def find_source(text: str, files: tuple[tuple[str, str], ...] = ()) -> Source | None:
    clean = prompts.strip_markers(text)
    for _, file_text in files:
        found = from_envelope(file_text) or _table(prompts.strip_markers(file_text))
        if found is not None:
            return Source(clean.strip(), found.records, found.facts)
    return from_envelope(clean) or _table(clean)


def _table(text: str) -> Source | None:
    found = find_table(text)
    return Source(found.instruction, found.records) if found else None


def from_envelope(text: str) -> Source | None:
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, Mapping):
        return None
    instruction = (text[:start] + "\n" + text[end + 1:]).replace("```json", "").replace("```", "").strip()
    items = data.get("findings") or data.get("items")
    if isinstance(items, list) and items and all(isinstance(i, Mapping) and isinstance(i.get("text"), str)
                                                 for i in items):
        return Source(instruction, facts=_facts(items))
    table = data.get("table")
    if isinstance(table, str) and (found := find_table(table)) is not None:
        return Source(instruction, found.records)
    for rows in (table, data.get("rows"), items):
        records = records_from_json(rows) if isinstance(rows, list) else None
        if records is not None and len(records) >= 2:
            return Source(instruction, records)
    return None


def _facts(items: list) -> FactSet:
    facts = []
    for n, item in enumerate(items, start=1):
        shown = item.get("display") or item.get("values") or ()
        shown = shown if isinstance(shown, list) else list(shown.values()) if isinstance(shown, Mapping) else []
        kind = str(item.get("kind") or "finding")
        facts.append(Fact(f"f{n}", kind if kind in KINDS else "finding", prompts.strip_markers(item["text"]).strip(),
                          tuple(str(s) for s in shown if isinstance(s, str | int | float))))
    return FactSet("findings", tuple(facts), notes=(ENVELOPE_NOTE,), points=len(facts))
