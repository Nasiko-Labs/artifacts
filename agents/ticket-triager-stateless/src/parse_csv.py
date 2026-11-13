"""Pure: CSV/TSV ticket exports. The header row is found by its aliases; lines before it are the
instruction. Two or more text-like columns make the body column ambiguous (asked via HITL Q2)."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import ticket_fields
from model import Ticket

MIN_KNOWN_COLUMNS = 2


@dataclass(frozen=True)
class CsvResult:
    instruction: str
    rows: tuple[Ticket | None, ...]  # None = a row with neither subject nor body (skipped)
    body_candidates: tuple[str, ...] = ()
    ambiguous: bool = False


def _delimiter(line: str) -> str | None:
    if "\t" in line:
        return "\t"
    if "," in line:
        return ","
    return None


def _header(line: str) -> tuple[str, dict[int, str], list[str]] | None:
    delimiter = _delimiter(line)
    if delimiter is None:
        return None
    cells = next(csv.reader([line], delimiter=delimiter), [])
    mapping = {i: key for i, cell in enumerate(cells) if (key := ticket_fields.canonical(cell))}
    keys = set(mapping.values())
    if len(mapping) < MIN_KNOWN_COLUMNS or not keys & {"subject", "body"}:
        return None
    return delimiter, mapping, cells


def parse(text: str, body_column: str | None = None) -> CsvResult | None:
    """None when the text has no recognisable header row."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    for index, line in enumerate(lines):
        found = _header(line)
        if found:
            break
    else:
        return None
    delimiter, mapping, cells = found
    instruction = "\n".join(lines[:index]).strip()
    body_columns = [i for i, key in mapping.items() if key == "body"]
    candidates = tuple(cells[i].strip() for i in body_columns)
    chosen = _choose(body_columns, cells, body_column)
    if len(body_columns) > 1 and chosen is None:
        return CsvResult(instruction, (), candidates, ambiguous=True)
    reader = csv.reader(io.StringIO("\n".join(lines[index + 1:])), delimiter=delimiter)
    rows = []
    for number, row in enumerate(reader, start=1):
        if not any(cell.strip() for cell in row):
            continue
        values: dict[str, str] = {}
        for i, key in mapping.items():
            if i >= len(row) or (key == "body" and chosen is not None and i != chosen) or key in values:
                continue
            values[key] = row[i]
        rows.append(ticket_fields.build(values, f"T{number}"))
    return CsvResult(instruction, tuple(rows), candidates)


def _choose(body_columns: list[int], cells: list[str], wanted: str | None) -> int | None:
    if len(body_columns) == 1:
        return body_columns[0]
    if not wanted:
        return None
    for i in body_columns:
        if cells[i].strip().casefold() == wanted.strip().casefold():
            return i
    return None
