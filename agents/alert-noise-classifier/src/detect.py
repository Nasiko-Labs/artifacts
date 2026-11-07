"""Pure: find the alert data in a message and parse it with the right reader (JSON, CSV/TSV with a header,
alert-bot chat paste, plain lines). Anything before the data is the user's instruction."""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping
from dataclasses import dataclass

import parse_csv
import parse_json
import parse_lines
from fires import Fire

INSTRUCTION_MAX_CHARS = 300
MIN_FIELDS = 3
_FENCE = re.compile(r"^\s*```[a-z]*\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    instruction: str
    data: str


@dataclass(frozen=True)
class Parsed:
    fires: tuple[Fire, ...]
    shape: str                     # json | csv | bot | lines | none
    rows_seen: int = 0
    rows_skipped: int = 0
    missing: str = ""              # csv only: the role whose column could not be identified
    headers: tuple[str, ...] = ()
    old_envelope: bool = False
    notes: tuple[str, ...] = ()


def _fields(line: str) -> int:
    return len(next(csv.reader([line], delimiter=parse_csv.delimiter_of(line)), []))


def _csv_header_at(lines: list[str]) -> int | None:
    for i, line in enumerate(lines[:40]):
        if not line.strip() or parse_lines.TIME.search(line) or _FENCE.match(line):
            continue
        width = _fields(line)
        if width < MIN_FIELDS:
            continue
        nxt = next((x for x in lines[i + 1:] if x.strip() and not _FENCE.match(x)), None)
        if nxt is not None and _fields(nxt) == width and parse_csv.delimiter_of(nxt) == parse_csv.delimiter_of(line):
            return i
    return None


def split(text: str) -> Request:
    """(instruction, data) from one message; data is empty when nothing looks like alerts."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    json_at = next((i for i, x in enumerate(lines) if x.lstrip().startswith(("{", "[")) and not
                    parse_lines.is_bot_paste(x)), None)
    if json_at is not None and parse_json.load("\n".join(lines[json_at:])) is not None:
        return _cut(lines, json_at)
    header_at = _csv_header_at(lines)
    if header_at is not None:
        return _cut(lines, header_at)
    timed = next((i for i, x in enumerate(lines) if parse_lines.TIME.search(x)), None)
    if timed is not None:
        return _cut(lines, timed)
    return Request(" ".join((text or "").split())[:INSTRUCTION_MAX_CHARS], "")


def _cut(lines: list[str], at: int) -> Request:
    instruction = " ".join("\n".join(lines[:at]).split()).rstrip(":").strip()
    data = "\n".join(x for x in lines[at:] if not _FENCE.match(x)).strip()
    return Request(instruction[:INSTRUCTION_MAX_CHARS], data)


def parse(data: str, overrides: Mapping[str, str] | None = None) -> Parsed:
    if not data.strip():
        return Parsed((), "none")
    if data.lstrip().startswith(("{", "[")) and not parse_lines.is_bot_paste(data.splitlines()[0]):
        j = parse_json.parse(data)
        if j.is_json:
            return Parsed(j.fires, "json", j.rows_seen, j.rows_skipped, old_envelope=j.is_old_envelope,
                          notes=j.notes)
    if not parse_lines.is_bot_paste(data) and _csv_header_at(data.splitlines()) == 0:
        c = parse_csv.parse(data, overrides)
        return Parsed(c.fires, "csv", c.rows_seen, c.rows_skipped, c.missing, c.headers, notes=c.notes)
    lr = parse_lines.parse(data)
    return Parsed(lr.fires, "bot" if parse_lines.is_bot_paste(data) else "lines", lr.rows_seen, lr.rows_skipped,
                  notes=lr.notes)
