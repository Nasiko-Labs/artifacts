"""Pure: pasted text -> tickets. First match wins: (1) CSV/TSV with a header row, (2) JSON array or envelope,
(3) blocks with `Ticket #123` / `#123:` headers or `---` separators, (4) plain blocks separated by blank lines
(generated ids T1..Tn, flagged). Text before the tickets is the instruction. Later duplicates of an id win."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import parse_csv
import ticket_fields
from inputs import is_config_line
from model import Ticket

INSTRUCTION_MAX_CHARS = 300
MIN_PLAIN_BLOCKS = 3

_HEADER = re.compile(r"^\s*(?:\*\*)?(?:ticket\s*(?:#|no\.?|id:?)?\s*([A-Za-z]*-?\d[\w-]*)|#(\d[\w-]*)(?=\s*:))"
                     r"\s*(?:\*\*)?\s*(?:[:\-–—]\s*(?:\*\*)?\s*(.*?))?\s*(?:\*\*)?\s*$", re.IGNORECASE)
_SEPARATOR = re.compile(r"^\s*-{3,}\s*$")
_LABEL = re.compile(r"^\s*([A-Za-z][A-Za-z _#]{0,20}?)\s*:\s*(.*)$")
_PARAGRAPH = re.compile(r"\n\s*\n")
_TRIAGE = re.compile(r"\btriage\b|\btickets?\b|\bprioriti[sz]e\b|\bclassify\b|\bescalat|\bsupport queue\b",
                     re.IGNORECASE)
_FENCE = re.compile(r"^```[a-z]*\s*\n?|\n?```\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Parsed:
    fmt: str                      # csv | json | blocks | plain | none
    instruction: str = ""
    tickets: tuple[Ticket, ...] = ()
    skipped: int = 0
    duplicates: int = 0
    body_candidates: tuple[str, ...] = ()
    ambiguous: bool = False

    @property
    def generated_ids(self) -> int:
        return sum(1 for t in self.tickets if t.generated_id)


def parse(text: str, body_column: str | None = None) -> Parsed:
    text = (text or "").replace("\r\n", "\n")
    found = parse_csv.parse(text, body_column)
    if found is not None:
        if found.ambiguous:
            return Parsed("csv", found.instruction, body_candidates=found.body_candidates, ambiguous=True)
        return _finish("csv", found.instruction, found.rows)
    for parser in (_json, _headed_blocks, _separated_blocks, _plain_blocks):
        result = parser(text)
        if result is not None:
            return result
    return Parsed("none", text.strip())


def _finish(fmt: str, instruction: str, rows) -> Parsed:
    kept: dict[str, Ticket] = {}
    skipped = duplicates = 0
    for row in rows:
        if row is None:
            skipped += 1
            continue
        if row.id in kept:
            duplicates += 1
        kept[row.id] = row
    return Parsed(fmt, instruction.strip(), tuple(kept.values()), skipped, duplicates)


def _json(text: str) -> Parsed | None:
    match = re.search(r"^\s*(?:```[a-z]*\s*\n)?\s*[\[{]", text, re.MULTILINE)
    if not match:
        return None
    body = _FENCE.sub("", text[match.start():].strip())
    try:
        data = json.loads(body)
    except ValueError:
        return None
    records = data.get("items") or data.get("tickets") if isinstance(data, dict) else data
    if not isinstance(records, list) or not records or not all(isinstance(r, dict) for r in records):
        return None
    rows = []
    for number, record in enumerate(records, start=1):
        values = {}
        for key, value in record.items():
            name = ticket_fields.canonical(key)
            if name and name not in values and not isinstance(value, (dict, list)):
                values[name] = value
        rows.append(ticket_fields.build(values, f"T{number}"))
    return _finish("json", text[:match.start()], rows)


def _labelled(lines: list[str], ticket_id: str = "", subject: str = "", fallback: str = "") -> Ticket | None:
    values: dict[str, str] = {"id": ticket_id, "subject": subject}
    body: list[str] = []
    for line in lines:
        label = _LABEL.match(line)
        name = ticket_fields.canonical(label.group(1)) if label else None
        if name and name != "body" and not values.get(name):
            values[name] = label.group(2)
        elif name == "body":
            body.append(label.group(2))
        else:
            body.append(line)
    values["body"] = "\n".join(body).strip()
    return ticket_fields.build(values, fallback)


def _headed_blocks(text: str) -> Parsed | None:
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if _HEADER.match(line)]
    if not starts:
        return None
    rows = []
    for n, start in enumerate(starts, start=1):
        end = starts[n] if n < len(starts) else len(lines)
        header = _HEADER.match(lines[start])
        block = [ln for ln in lines[start + 1:end] if not _SEPARATOR.match(ln)]
        rows.append(_labelled(block, header.group(1) or header.group(2), header.group(3) or "", f"T{n}"))
    return _finish("blocks", "\n".join(lines[:starts[0]]), rows)


def _separated_blocks(text: str) -> Parsed | None:
    segments, current = [], []
    for line in text.split("\n"):
        if _SEPARATOR.match(line):
            segments.append(current)
            current = []
        else:
            current.append(line)
    segments.append(current)
    segments = [s for s in segments if "\n".join(s).strip()]
    if len(segments) < 2:
        return None
    instruction = ""
    first = "\n".join(segments[0]).strip()
    if len(first) <= INSTRUCTION_MAX_CHARS and _TRIAGE.search(first) and len(segments) > 2:
        instruction, segments = first, segments[1:]
    rows = [_labelled(seg, fallback=f"T{n}") for n, seg in enumerate(segments, start=1)]
    return _finish("blocks", instruction, rows)


def _plain_blocks(text: str) -> Parsed | None:
    paragraphs = [p.strip() for p in _PARAGRAPH.split(text.strip()) if p.strip()]
    if not paragraphs:
        return None
    instruction, rest = _split_instruction(paragraphs)
    if not rest or not (_TRIAGE.search(instruction) or len(rest) >= MIN_PLAIN_BLOCKS):
        return None
    rows = [_labelled(p.split("\n"), fallback=f"T{n}") for n, p in enumerate(rest, start=1)]
    return _finish("plain", instruction, rows)


def _split_instruction(paragraphs: list[str]) -> tuple[str, list[str]]:
    first, rest = paragraphs[0], paragraphs[1:]
    lines = first.split("\n")
    config = [ln for ln in lines if is_config_line(ln)]
    if config and rest:
        head = "\n".join(ln for ln in lines if not is_config_line(ln) and _TRIAGE.search(ln))
        return "\n".join([head, *config]).strip(), rest
    if len(first) <= INSTRUCTION_MAX_CHARS and _TRIAGE.search(first) and rest:
        return first, rest
    head, colon, tail = first.partition(":")
    if colon and tail.strip() and len(head) <= INSTRUCTION_MAX_CHARS and _TRIAGE.search(head):
        return head.strip(), [tail.strip(), *rest]
    return "", paragraphs
