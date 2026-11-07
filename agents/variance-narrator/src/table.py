"""Pure: find the budget-vs-actual table in a message and read it. Delimiter sniffing (tab, `|`, comma,
2+ spaces), header detection by column synonyms, number parsing per cell. Text outside the table is kept:
it carries the instruction and any driver notes."""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from amounts import BAD, BLANK, NA, parse_amount

MAX_ITEMS = 200
MIN_HEADER_CELLS = 3
DELIMITERS = ("\t", "|", ",", "  ")
_IGNORE = re.compile(r"%|\bvar(?:iance)?\b|\bdiff(?:erence)?\b|\bdelta\b|\bfav\b|\bf ?/ ?u\b")
_ROLES = (
    ("prior", re.compile(r"\bprior\b|\bpy\b|\blast (?:year|period|month|quarter)\b|\bprevious\b")),
    ("actual", re.compile(r"\bactuals?\b|\bact\b|\bspend\b")),
    ("budget", re.compile(r"\bbudget\b|\bplan\b|\bbud\b|\bforecast\b|\bfcst\b")),
    ("driver", re.compile(r"\bdrivers?\b|\bcomments?\b|\bnotes?\b|\bexplanation\b|\breason\b|\bcommentary\b")),
    ("line", re.compile(r"\bitem\b|\bline\b|\bcategory\b|\baccount\b|\bname\b|\bdescription\b|\bdepartment\b|"
                        r"\bcost cent(?:er|re)\b")),
)
TOTAL_LABEL = re.compile(r"\b(?:grand total|sub-?total|total|net)\b", re.IGNORECASE)
_SEPARATOR_ROW = re.compile(r"^[\s|:+-]+$")
_EMPTY_DRIVERS = {"", "-", "n/a", "na", "none", "tbd", "?"}


@dataclass(frozen=True)
class Row:
    name: str
    budget: Decimal
    actual: Decimal
    prior: Decimal | None = None
    driver: str = ""
    is_total: bool = False


@dataclass(frozen=True)
class Table:
    rows: tuple[Row, ...] = ()
    columns: tuple[str, ...] = ()
    missing: str = ""                 # "actual" | "budget" when that column was not found
    symbol: str = ""
    outside: str = ""                 # message text outside the table
    skipped: tuple[str, ...] = ()     # rows with n/a in budget or actual
    unparseable: int = 0
    blanks: int = 0
    capped: int = 0
    mapping: Mapping[str, int] = field(default_factory=dict)

    @property
    def items(self) -> tuple[Row, ...]:
        return tuple(r for r in self.rows if not r.is_total)

    @property
    def data_rows(self) -> int:
        return len(self.rows) + len(self.skipped) + self.unparseable


def role_of(header: str) -> str:
    plain = " ".join(re.sub(r"[^a-z0-9%/ ]+", " ", (header or "").casefold()).split())
    if not plain or _IGNORE.search(plain):
        return ""
    return next((role for role, pattern in _ROLES if pattern.search(plain)), "")


def split_cells(line: str, delimiter: str) -> list[str]:
    if delimiter == "\t":
        cells = line.split("\t")
    elif delimiter == "|":
        cells = line.strip().strip("|").split("|") if line.count("|") >= 2 else [line]
    elif delimiter == ",":
        cells = next(csv.reader([line]), [])
    else:
        cells = re.split(r"\s{2,}", line.strip())
    return [c.strip().strip("*").strip() for c in cells]


def parse_table(text: str, forced: Mapping[str, int] | None = None) -> Table | None:
    """The table in `text`, or None when no header row names a budget or actual column."""
    lines = (text or "").splitlines()
    for delimiter in DELIMITERS:
        start = _header_index(lines, delimiter)
        if start is not None:
            return _read(lines, start, delimiter, dict(forced or {}))
    return None


def _header_index(lines: list[str], delimiter: str) -> int | None:
    for i, line in enumerate(lines):
        cells = split_cells(line, delimiter)
        roles = {role_of(c) for c in cells}
        if len(cells) >= MIN_HEADER_CELLS and roles & {"budget", "actual"}:
            return i
    return None


def _map_columns(header: list[str], forced: dict[str, int]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header):
        role = role_of(cell)
        if role and role not in mapping and index not in forced.values():
            mapping[role] = index
    mapping.update(forced)
    if "line" not in mapping:
        taken = set(mapping.values())
        mapping["line"] = next((i for i in range(len(header)) if i not in taken), 0)
    return mapping


def _read(lines: list[str], start: int, delimiter: str, forced: dict[str, int]) -> Table:
    header = split_cells(lines[start], delimiter)
    mapping = _map_columns(header, forced)
    missing = next((role for role in ("actual", "budget") if role not in mapping), "")
    end = start + 1
    body: list[list[str]] = []
    while end < len(lines):
        line = lines[end]
        if line.strip() and _SEPARATOR_ROW.match(line):
            end += 1
            continue
        cells = split_cells(line, delimiter) if line.strip() else []
        if line.strip() and (len(cells) < 2 or _is_prose(cells, mapping)):
            break
        if cells:
            body.append(cells + [""] * (len(header) - len(cells)))
        end += 1
    outside = "\n".join([*lines[:start], *lines[end:]]).strip()
    if missing:
        return Table(columns=tuple(header), missing=missing, outside=outside, mapping=mapping)
    return _rows(body, header, mapping, outside)


def _is_prose(cells: list[str], mapping: dict[str, int]) -> bool:
    """A line after the table that only split because it has a comma: neither number column parses."""
    def bad(role: str) -> bool:
        index = mapping.get(role)
        return index is None or index >= len(cells) or parse_amount(cells[index]).status == BAD
    return bad("budget") and bad("actual")


def _rows(body:list[list[str]], header: list[str], mapping: dict[str, int], outside: str) -> Table:
    rows: list[Row] = []
    skipped: list[str] = []
    unparseable = blanks = capped = 0
    symbol = ""
    for cells in body:
        name = cells[mapping["line"]].strip()
        if not name:
            continue
        budget, actual = parse_amount(cells[mapping["budget"]]), parse_amount(cells[mapping["actual"]])
        symbol = symbol or budget.symbol or actual.symbol
        if NA in (budget.status, actual.status):
            skipped.append(name)
            continue
        if BAD in (budget.status, actual.status):
            unparseable += 1
            continue
        blanks += (budget.status == BLANK) + (actual.status == BLANK)
        is_total = bool(TOTAL_LABEL.search(name))
        if not is_total and sum(not r.is_total for r in rows) >= MAX_ITEMS:
            capped += 1
            continue
        rows.append(Row(name, budget.value, actual.value, _prior(cells, mapping), _driver(cells, mapping), is_total))
    return Table(tuple(rows), tuple(header), "", symbol, outside, tuple(skipped), unparseable, blanks, capped, mapping)


def _prior(cells: list[str], mapping: dict[str, int]) -> Decimal | None:
    if "prior" not in mapping:
        return None
    cell = parse_amount(cells[mapping["prior"]])
    return cell.value if cell.status not in (NA, BAD) else None


def _driver(cells: list[str], mapping: dict[str, int]) -> str:
    if "driver" not in mapping:
        return ""
    text = " ".join(cells[mapping["driver"]].split())
    return "" if text.casefold() in _EMPTY_DRIVERS else text[:300]
