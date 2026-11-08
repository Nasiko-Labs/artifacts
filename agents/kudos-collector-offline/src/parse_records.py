"""Pure: the paste -> records, aliases, options and the user's instruction.

Labeled sections (`PRs:`, `Praise:`/`Kudos:`, `Shipped:`/`Features:`/`Launches:`, `Aliases:`) set the kind of
every line under them; options sit on their own line (`Period: Q3`, `People: Priya Dev, Sam Lee`, `Min: 2`).
Unlabeled lines are classified by shape. A PR section may be a CSV/TSV export or a JSON array. Lines before the
first record that are not records are the instruction; later unrecognised lines are counted, never guessed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import aliases as aliases_mod
import shapes
import tabular
from records import PR, PRAISE, SHIPPED, Record

ALIASES, PERIOD, PEOPLE, MIN = "aliases", "period", "people", "min"
SECTIONS = {
    **dict.fromkeys(("pr", "prs", "pull request", "pull requests", "merged prs", "merged", "code"), PR),
    **dict.fromkeys(("praise", "kudos", "shoutouts", "shout-outs", "slack praise", "thanks", "recognition"), PRAISE),
    **dict.fromkeys(("shipped", "features", "feature", "launches", "launched", "shipped features", "releases"),
                    SHIPPED),
    **dict.fromkeys(("aliases", "alias", "handles"), ALIASES),
    PERIOD: PERIOD, PEOPLE: PEOPLE, MIN: MIN, "minimum": MIN,
}
OPTIONS = (PERIOD, PEOPLE, MIN)
HEADER = re.compile(r"^\s*(?:#{1,6}\s*)?\**\s*(?P<name>[A-Za-z][A-Za-z -]{0,24}?)\s*\**\s*:\**\s*(?P<rest>.*)$")
FENCE_LINE = re.compile(r"^\s*```")
REQUEST = re.compile(r"^(?:please|can you|could you|collect|gather|find|pull|give me|list|show|make|build|get|"
                     r"compile|here are|here's|add these|add|and)\b", re.IGNORECASE)
DEFAULT_MIN = 1


@dataclass(frozen=True)
class Parsed:
    records: list[Record] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    options: dict[str, str] = field(default_factory=dict)
    instruction: str = ""
    unrecognized: int = 0
    dropped: int = 0
    duplicates: int = 0


def _blocks(text: str) -> tuple[list[tuple[str | None, list[str]]], dict[str, str]]:
    blocks: list[tuple[str | None, list[str]]] = [(None, [])]
    options: dict[str, str] = {}
    for raw in (text or "").replace("\r\n", "\n").replace("﻿", "").split("\n"):
        if FENCE_LINE.match(raw):
            continue
        m = HEADER.match(raw)
        kind = SECTIONS.get(" ".join(m["name"].casefold().split())) if m else None
        if kind in OPTIONS:
            options[kind] = m["rest"].strip()
            continue
        if kind:
            blocks.append((kind, [m["rest"]] if m["rest"].strip() else []))
            continue
        blocks[-1][1].append(raw)
    return blocks, options


def _section(kind: str, lines: list[str], today: date) -> tuple[list[Record], int]:
    body = [x for x in lines if x.strip()]
    if not body:
        return [], 0
    if kind == PR:
        if body[0].lstrip().startswith(("[", "{")):
            parsed = tabular.parse_json("\n".join(body), today)
            if parsed is not None:
                return parsed
        if tabular.is_header(body[0]):
            return tabular.parse_csv(body, today)
    return [shapes.parse_line(kind, shapes.strip_bullet(x), today) for x in body], 0


def _unlabeled(lines: list[str], today: date) -> tuple[list[Record], dict[str, str], list[str], int, int]:
    records: list[Record] = []
    found_aliases: dict[str, str] = {}
    instruction: list[str] = []
    unrecognized = dropped = 0
    body = [x for x in lines if x.strip()]
    if body and body[0].lstrip().startswith("[") and (parsed := tabular.parse_json("\n".join(body), today)):
        return parsed[0], {}, [], 0, parsed[1]
    header_at = next((i for i, x in enumerate(body) if tabular.is_header(x)), None)
    if header_at is not None:
        csv_records, dropped = tabular.parse_csv(body[header_at:], today)
        body, records = body[:header_at], csv_records
    for raw in body:
        line = shapes.strip_bullet(raw)
        alias = aliases_mod.parse_line(line)
        kind = None if line.endswith(":") or (not records and REQUEST.match(line)) else shapes.classify(line)
        if kind:
            records.append(shapes.parse_line(kind, line, today))
        elif alias:
            found_aliases[alias[0]] = alias[1]
        elif not records:
            instruction.append(line)
        else:
            unrecognized += 1
    return records, found_aliases, instruction, unrecognized, dropped


def dedupe(records: list[Record]) -> tuple[list[Record], int]:
    """PRs with the same number or link are kept once (the first)."""
    seen: set[str] = set()
    out = []
    for r in records:
        if r.kind == PR and r.keys & seen:
            continue
        seen |= r.keys if r.kind == PR else set()
        out.append(r)
    return out, len(records) - len(out)


def parse(text: str, today: date) -> Parsed:
    blocks, options = _blocks(text)
    records: list[Record] = []
    alias_map: dict[str, str] = {}
    instruction: list[str] = []
    unrecognized = dropped = 0
    for kind, lines in blocks:
        if kind is None:
            recs, found, words, unrec, drop = _unlabeled(lines, today)
            records, instruction = records + recs, instruction + words
            alias_map, unrecognized, dropped = {**alias_map, **found}, unrecognized + unrec, dropped + drop
        elif kind == ALIASES:
            alias_map = {**alias_map, **aliases_mod.parse(lines)}
        else:
            recs, drop = _section(kind, lines, today)
            records, dropped = records + recs, dropped + drop
    unique, duplicates = dedupe(records)
    return Parsed(unique, alias_map, options, " ".join(" ".join(instruction).split())[:400], unrecognized,
                  dropped, duplicates)

