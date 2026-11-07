"""Pure: one pasted message -> (status report, ticket export).

1. Labels: `Report:` / `Status report:` and `Tickets:` / `Export:` / `Ticket export:` start sections; text
   before the first label is the request line (or the report, when only the export is labelled).
2. Without labels: a delimited block (CSV/TSV whose header names a status column, or a markdown table with
   id + title + status columns) or a JSON block is the export; the rest is the report.
3. A report given as another agent's envelope (JSON with `memo_markdown` / `text` / `summary`) is unwrapped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

REPORT, EXPORT = "report", "export"
REPORT_ENVELOPE_FIELDS = ("memo_markdown", "text", "summary", "brief_markdown")
STATUS_HEADERS = frozenset({"status", "state"})
ID_HEADERS = frozenset({"key", "id", "issue key", "ticket", "issue id"})
TITLE_HEADERS = frozenset({"summary", "title", "name"})
MIN_DELIMITERS = 3

_LABEL = re.compile(r"^\s*(?:#+\s*)?(?:\*\*)?(status report|report|ticket export|tickets?|export|ticket list)"
                    r"(?:\*\*)?\s*:(?:\*\*)?\s*(.*)$", re.IGNORECASE)
_FENCE = re.compile(r"^\s*```")
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}")


@dataclass(frozen=True)
class Split:
    report: str
    export: str
    preamble: str = ""


def split(text: str) -> Split:
    lines = (text or "").replace("\r\n", "\n").split("\n")
    labelled = _by_labels(lines)
    if labelled is not None:
        return labelled
    start, end = _export_block(lines)
    if start is None:
        return Split(unwrap_report("\n".join(lines).strip()), "")
    report = "\n".join(lines[:start] + lines[end:]).strip()
    return Split(unwrap_report(report), _unfence("\n".join(lines[start:end])))


def unwrap_report(text: str) -> str:
    body = _unfence(text)
    if not body.startswith("{"):
        return text.strip()
    try:
        data = json.loads(body)
    except ValueError:
        return text.strip()
    if isinstance(data, dict):
        for name in REPORT_ENVELOPE_FIELDS:
            value = data.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return text.strip()


def header_cells(line: str) -> tuple[str, list[str]] | None:
    """(delimiter, normalised cells) when `line` looks like a delimited header."""
    stripped = line.strip()
    if stripped.startswith("|"):
        return "|", [_norm(c) for c in stripped.strip("|").split("|")]
    for delimiter in ("\t", ",", ";"):
        if stripped.count(delimiter) >= MIN_DELIMITERS - 1:
            return delimiter, [_norm(c) for c in stripped.split(delimiter)]
    return None


def _by_labels(lines: list[str]) -> Split | None:
    sections: list[tuple[str, list[str]]] = []
    preamble: list[str] = []
    for line in lines:
        match = _LABEL.match(line)
        if match:
            kind = REPORT if "report" in match.group(1).casefold() else EXPORT
            sections.append((kind, [match.group(2)] if match.group(2).strip() else []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    if not any(kind == EXPORT for kind, _ in sections):
        return None
    report = "\n".join("\n".join(body) for kind, body in sections if kind == REPORT).strip()
    export = "\n".join("\n".join(body) for kind, body in sections if kind == EXPORT)
    lead = "\n".join(preamble).strip()
    if not report:  # only the export is labelled: what comes before it is the report
        return Split(unwrap_report(lead), _unfence(export))
    return Split(unwrap_report(report), _unfence(export), lead)


def _export_block(lines: list[str]) -> tuple[int | None, int]:
    for i, line in enumerate(lines):
        json_end = _json_from(lines, i) if line.lstrip().startswith(("[", "{")) else None
        if json_end is not None:
            return i, json_end
        found = header_cells(line)
        if found and _is_export_header(*found):
            return i, _rows_end(lines, i, found[0])
    return None, len(lines)


def _is_export_header(delimiter: str, cells: list[str]) -> bool:
    has_status = bool(STATUS_HEADERS & set(cells))
    if delimiter == "|":
        return has_status and bool(ID_HEADERS & set(cells)) and bool(TITLE_HEADERS & set(cells))
    return has_status and len(cells) >= MIN_DELIMITERS


def _rows_end(lines: list[str], start: int, delimiter: str) -> int:
    end = start + 1
    in_quotes = lines[start].count('"') % 2 == 1
    while end < len(lines):
        line = lines[end]
        if not in_quotes and (not line.strip() or (delimiter not in line and not TABLE_RULE.match(line))):
            break
        in_quotes ^= line.count('"') % 2 == 1  # a quoted cell spanning lines
        end += 1
    return end


def _json_from(lines: list[str], start: int) -> int | None:
    """The end line index of an export-shaped JSON value starting at line `start`, else None."""
    rest = "\n".join(lines[start:])
    offset = len(rest) - len(rest.lstrip())
    try:
        value, consumed = json.JSONDecoder().raw_decode(rest, offset)
    except ValueError:
        return None
    if not _looks_like_export(value):
        return None
    return start + rest[:consumed].count("\n") + 1


def _looks_like_export(value: object) -> bool:
    if isinstance(value, dict):
        value = value.get("items") or value.get("tickets") or value.get("issues")
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def _unfence(text: str) -> str:
    return "\n".join(line for line in (text or "").split("\n") if not _FENCE.match(line)).strip()


def _norm(cell: str) -> str:
    return " ".join(cell.strip().strip('"').casefold().replace("_", " ").split())
