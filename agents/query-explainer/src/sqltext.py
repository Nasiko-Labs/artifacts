"""Pure: split one message into the request words, the SQL and schema hint lines, then parse.

    ```sql fenced blocks```                -> the SQL; everything else is the request
    otherwise                              -> SQL starts at the first line (or text after a colon) that opens
                                              a statement; the words before it are the request
    `orders.id is the primary key`, `index on orders(customer_id)` lines -> schema hints, never SQL
    trailing prose after the SQL           -> trimmed line by line until the SQL parses (at most 5 lines)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import dialects
import placeholders
import schema
from inputs import from_envelope, strip_markers

MAX_SQL_CHARS = 30_000
MAX_TRIM_LINES = 5
_FENCE = re.compile(r"```[ \t]*(?:sql|postgres|postgresql|mysql|bigquery|tsql|snowflake)?[ \t]*\n(.*?)```",
                    re.DOTALL | re.IGNORECASE)
_START = re.compile(r"(?:with\s+\w|select\s|insert\s+into\s|insert\s+overwrite\s|update\s+[\w`\"\[.]+|delete\s+from\s|"
                    r"delete\s+\w+\s+from\s|merge\s+into\s|create\s|alter\s+table\s|truncate\s|begin\b|\{\{|\{%|--|/\*|\(\s*select\s)",
                    re.IGNORECASE)
_COLON = re.compile(r":\s+")
_SQL_WORDS = frozenset(["select", "from", "where", "join", "left", "right", "inner", "outer", "full", "cross", "on", "and", "or", "group", "order", "by", "having", "limit", "offset", "union", "with", "as", "set", "values", "insert", "update", "delete", "merge", "when", "then", "else", "end", "case", "returning", "into"])
_PROSE = re.compile(r"^[A-Za-z][^;=(),<>*]*$")


@dataclass(frozen=True)
class Extracted:
    request: str
    sql: str
    hints: tuple[str, ...] = ()
    over_cap: bool = False


@dataclass(frozen=True)
class Parsed:
    masked: placeholders.Masked
    parsed: dialects.Parsed | None   # None = nothing parsed: the fallback path
    request: str


def extract(text: str) -> Extracted:
    body = strip_markers(text or "")
    enveloped = from_envelope(body)
    if enveloped is not None:
        return _finish("", enveloped)
    blocks = _FENCE.findall(body)
    if blocks:
        return _finish(_FENCE.sub(" ", body), "\n".join(b.strip() for b in blocks))
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if _START.match(line.lstrip()):
            return _finish("\n".join(lines[:index]), "\n".join(lines[index:]))
        for colon in _COLON.finditer(line):
            if _START.match(line[colon.end():]):
                head = "\n".join([*lines[:index], line[:colon.start()]])
                return _finish(head, "\n".join([line[colon.end():], *lines[index + 1:]]))
    return _finish(body, "")


def _finish(request: str, sql: str) -> Extracted:
    hint_lines = [ln.strip() for ln in (request + "\n" + sql).splitlines() if schema.is_hint_line(ln)]
    kept = "\n".join(ln for ln in sql.splitlines() if not schema.is_hint_line(ln)).strip()
    request_kept = "\n".join(ln for ln in request.splitlines() if not schema.is_hint_line(ln)).strip()
    over = len(kept) > MAX_SQL_CHARS
    if over:
        cut = kept.rfind(";", 0, MAX_SQL_CHARS)
        kept = kept[:cut + 1] if cut > 0 else kept[:MAX_SQL_CHARS]
    return Extracted(request_kept, kept, tuple(hint_lines), over)


def parse(extracted: Extracted, hint: str | None) -> Parsed:
    """Parse the SQL (masked); trailing prose lines move to the request when only they break the parse."""
    lines = extracted.sql.splitlines()
    request = extracted.request
    for trimmed in range(_prose_tail(lines) + 1):
        candidate = "\n".join(lines[:len(lines) - trimmed])
        masked = placeholders.mask(candidate)
        result = dialects.parse(masked.sql, hint)
        if result is not None:
            tail = "\n".join(lines[len(lines) - trimmed:]) if trimmed else ""
            return Parsed(masked, result, "\n".join(p for p in (request, tail) if p.strip()))
    return Parsed(placeholders.mask(extracted.sql), None, request)


def _prose_tail(lines: list[str]) -> int:
    """How many trailing lines read as prose (never more than MAX_TRIM_LINES, never the first line)."""
    count = 0
    for line in reversed(lines[1:]):
        words = line.strip().split()
        if count >= MAX_TRIM_LINES:
            break
        if not words:
            count += 1
            continue
        if not _PROSE.match(line.strip()):
            break
        if words[0].casefold() in _SQL_WORDS:
            break
        count += 1
    return count


def looks_like_sql(text: str) -> bool:
    return bool(extract(text).sql)
