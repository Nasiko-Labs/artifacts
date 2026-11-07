"""Pure: a ticket export (CSV / TSV / markdown table / JSON array / `items[]` envelope) -> ticket dicts.

Columns are found by case-insensitive aliases. Title and status are required; two different columns that
both could be the status (or the title) is a question for the user, never a guess. Dates are parsed here
(see dates.py for the day/month order rule); a cell that can't be read is kept as unparsed.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field

import dates
import split

ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("key", "issue key", "id", "ticket", "issue id", "ticket id"),
    "title": ("summary", "title", "name"),
    "status": ("status", "state"),
    "assignee": ("assignee", "owner"),
    "created": ("created", "created at", "created date"),
    "updated": ("updated", "last updated", "modified", "updated at"),
    "due": ("due", "due date", "target date", "deadline"),
    "priority": ("priority",),
    "labels": ("labels", "label", "flags", "flagged"),
    "parent": ("parent", "parent key", "parent id", "epic link", "epic"),
    "type": ("type", "issue type"),
    "resolved": ("resolved", "resolution date"),
}
REQUIRED = ("title", "status")
DATE_FIELDS = ("created", "updated", "due", "resolved")
_CUSTOM = re.compile(r"^custom field \((.+)\)$")
_LABEL_SPLIT = re.compile(r"[;,|]\s*|\s+(?=\S)")


@dataclass(frozen=True)
class ParsedExport:
    tickets: list[dict] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)
    ambiguous: tuple[str, tuple[str, ...]] | None = None  # (field, candidate headers)
    error: str = ""


def parse(text: str, chosen: dict[str, str] | None = None) -> ParsedExport:
    """`chosen` = {field: header} answers to an earlier ambiguity question."""
    headers, rows = _table(text)
    if not headers or not rows:
        return ParsedExport(error="I couldn't read any ticket rows in the export.")
    columns, ambiguous, caveats = _columns(headers, chosen or {})
    if ambiguous:
        return ParsedExport(headers=headers, ambiguous=ambiguous)
    missing = [f for f in REQUIRED if f not in columns]
    if missing:
        return ParsedExport(headers=headers, error=(
            f"The export needs a {' and a '.join(missing)} column; I found: {', '.join(headers[:20])}."))
    order = dates.slash_order(row.get(columns[f], "") for row in rows for f in DATE_FIELDS if f in columns)
    tickets = [_ticket(n, row, columns, order) for n, row in enumerate(rows, start=1)]
    tickets = [t for t in tickets if t["title"] or t["status"]]
    tickets = _with_parents(tickets)
    unread = sum(1 for t in tickets if t["unparsed"])
    if unread and order is None and any(dates.is_slashed(row.get(columns.get(f, ""), "")) for row in rows
                                        for f in DATE_FIELDS):
        caveats.append("Dates like 01/02/2026 are ambiguous (day or month first) and nothing in the export "
                       "settles it, so they were left unread.")
    return ParsedExport(tickets, headers, columns, caveats)


def most_recent(tickets: list[dict], limit: int) -> list[dict]:
    """The `limit` most recently updated rows (unreadable or missing `updated` last), in export order."""
    ranked = sorted(tickets, key=lambda t: (t.get("updated") or "", -t["row"]), reverse=True)[:limit]
    return sorted(ranked, key=lambda t: t["row"])


def _table(text: str) -> tuple[list[str], list[dict]]:
    body = (text or "").strip()
    if body.startswith(("[", "{")):
        return _json_table(body)
    lines = [line for line in body.split("\n") if line.strip()]
    if not lines:
        return [], []
    found = split.header_cells(lines[0])
    delimiter = found[0] if found else ","
    if delimiter == "|":
        return _pipe_table(lines)
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    raw = [row for row in reader if any(cell.strip() for cell in row)]
    headers = [cell.strip() for cell in raw[0]] if raw else []
    return headers, [_row(headers, cells) for cells in raw[1:]]


def _pipe_table(lines: list[str]) -> tuple[list[str], list[dict]]:
    cells = [[c.strip() for c in line.strip().strip("|").split("|")] for line in lines
             if not split.TABLE_RULE.match(line)]
    return (cells[0], [_row(cells[0], row) for row in cells[1:]]) if cells else ([], [])


def _json_table(body: str) -> tuple[list[str], list[dict]]:
    try:
        data = json.loads(body)
    except ValueError:
        return [], []
    if isinstance(data, dict):
        data = data.get("items") or data.get("tickets") or data.get("issues") or []
    rows = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    headers: list[str] = []
    for row in rows:
        headers += [str(k) for k in row if str(k) not in headers]
    return headers, [{str(k): _cell(v) for k, v in row.items()} for row in rows]


def _row(headers: list[str], cells: list[str]) -> dict:
    row: dict[str, str] = {}
    for header, cell in zip(headers, cells, strict=False):
        value = cell.strip()
        row[header] = f"{row[header]}; {value}" if header in row and value else row.get(header) or value
    return row


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(_cell(v) for v in value)
    if isinstance(value, dict):
        return str(value.get("name") or value.get("key") or value.get("displayName") or "")
    return str(value)


def _columns(headers: list[str], chosen: dict[str, str]) -> tuple[dict, tuple | None, list[str]]:
    columns: dict[str, str] = {}
    caveats: list[str] = []
    for name, aliases in ALIASES.items():
        if chosen.get(name) in headers:
            columns[name] = chosen[name]
            continue
        found = sorted({h for h in headers if _norm(h) in aliases}, key=lambda h: (aliases.index(_norm(h)),
                                                                                  headers.index(h)))
        if len(found) > 1 and name in REQUIRED:
            return {}, (name, tuple(found)), []
        if found:
            columns[name] = found[0]
        if len(found) > 1:
            caveats.append(f"Columns {', '.join(found)} could all be the {name}; I used {found[0]}.")
    return columns, None, caveats


def _ticket(n: int, row: dict, columns: dict[str, str], order: str | None) -> dict:
    def cell(name: str) -> str:
        return (row.get(columns[name]) or "").strip() if name in columns else ""

    ticket = {"row": n, "id": cell("id") or f"row {n}", "title": cell("title"), "status": cell("status"),
              "assignee": cell("assignee") if "assignee" in columns else None, "priority": cell("priority"),
              "type": cell("type"), "parent": cell("parent"), "parent_id": None, "unparsed": [],
              "labels": [p for p in _LABEL_SPLIT.split(cell("labels")) if p]}
    for name in DATE_FIELDS:
        raw = cell(name)
        parsed = dates.parse(raw, order)
        ticket[name] = dates.iso(parsed)
        if raw and parsed is None:
            ticket["unparsed"].append(name)
    return ticket


def _with_parents(tickets: list[dict]) -> list[dict]:
    """Parent cells name a key (or, in some exports, the epic's title): resolve to the parent's id."""
    by_id = {t["id"].casefold(): t["id"] for t in tickets}
    by_title = {t["title"].casefold(): t["id"] for t in tickets if t["title"]}
    return [{**t, "parent_id": (by_id.get(t["parent"].casefold()) or by_title.get(t["parent"].casefold()))
             if t["parent"] else None} for t in tickets]


def _norm(header: str) -> str:
    text = " ".join(header.strip().casefold().replace("_", " ").split())
    match = _CUSTOM.match(text)
    return match.group(1) if match else text
