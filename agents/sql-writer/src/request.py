"""Pure: split one message into the request words, the query, the schema text and EXPLAIN output.

    ```fenced blocks```   -> classified whole: EXPLAIN plan, schema (CREATE / list / \\d), or a query
    CREATE TABLE / INDEX  -> schema (to the `;` or the closing paren)
    plan-shaped lines     -> EXPLAIN (Seq Scan, cost=, Sort Method, a MySQL EXPLAIN table)
    a SELECT / WITH / DML -> the query: from its first line (or the text after a colon) to a blank line
                             followed by prose, a plan or a schema line
    the rest              -> the request (the user's question)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import schema_text
from inputs import from_envelope, strip_markers

MAX_QUERY_CHARS = 30_000
_FENCE = re.compile(r"```[ \t]*([\w-]*)[ \t]*\n(.*?)```", re.DOTALL)
_QUERY_START = re.compile(r"(?:with\s+\w+\s+as\s*\(|with\s+recursive\b|select\s|\(\s*select\s|insert\s+into\s|"
                          r"update\s+[\w`\"\[.]+\s+set\b|delete\s+from\s|merge\s+into\s|truncate\s|drop\s+table\s)",
                          re.IGNORECASE)
_PLAN = re.compile(r"(?:\bSeq Scan\b|\bIndex (?:Only )?Scan\b|\bBitmap (?:Heap|Index) Scan\b|\bHash Join\b|"
                   r"\bNested Loop\b|\bMerge Join\b|\bSort Method\b|\bPlanning Time\b|\bExecution Time\b|"
                   r"\bQUERY PLAN\b|\(cost=\d|\bactual time=|\bRows Removed by\b|\bBuffers: shared\b|"
                   r"\bselect_type\b|^\s*\|\s*\d+\s*\|\s*(?:SIMPLE|PRIMARY|SUBQUERY|DERIVED|UNION)\b|^\s*->\s+\w)",
                   re.IGNORECASE | re.MULTILINE)
_PLAN_DETAIL = re.compile(r"^\s*(?:Filter|Index Cond|Hash Cond|Join Filter|Sort Key|Group Key|Recheck Cond|"
                          r"Heap Blocks|Workers|Output|Hash|Sort|Aggregate|Limit|Gather|Planning|Execution)\b|"
                          r"^\s*\+[-+]+\+\s*$", re.IGNORECASE)
_COLON = re.compile(r":\s+")
_CONTINUE = re.compile(r"^\s|^(?:from|where|join|left|right|inner|outer|full|cross|on|and|or|group|order|having|"
                       r"limit|offset|union|select|with|\)|,|case|when|then|else|end|qualify|window|fetch)\b",
                       re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    words: str
    query: str = ""
    schema: str = ""
    plan: str = ""
    from_envelope: bool = False
    over_cap: bool = False


def split(text: str) -> Request:
    body = strip_markers(text or "")
    enveloped = from_envelope(body)
    if enveloped is not None:
        return Request("", enveloped[:MAX_QUERY_CHARS], from_envelope=True)
    words, query, schema, plan = [], [], [], []
    rest = body
    for lang, block in _FENCE.findall(body):
        _classify_block(lang, block.strip(), query, schema, plan)
    rest = _FENCE.sub("\n", rest)
    spans = schema_text.create_statements(rest)
    schema += [rest[s:e] for s, e in spans]
    for start, end in reversed(spans):
        rest = rest[:start] + "\n" + rest[end:]
    _scan_lines(rest.splitlines(), words, query, schema, plan)
    joined = "\n".join(query).strip()
    return Request("\n".join(words).strip(), joined[:MAX_QUERY_CHARS], "\n".join(schema).strip(),
                   "\n".join(plan).strip(), over_cap=len(joined) > MAX_QUERY_CHARS)


def _classify_block(lang: str, block: str, query: list, schema: list, plan: list) -> None:
    if _PLAN.search(block) and not _QUERY_START.match(block):
        plan.append(block)
    elif schema_text.create_statements(block) or schema_text.has_list_or_describe(block):
        spans = schema_text.create_statements(block)
        schema.append(block)
        leftover = block
        for start, end in reversed(spans):
            leftover = leftover[:start] + leftover[end:]
        if _QUERY_START.search(leftover):
            query.append(leftover[_QUERY_START.search(leftover).start():].strip())
    else:
        query.append(block)


def _scan_lines(lines: list[str], words: list, query: list, schema: list, plan: list) -> None:
    in_query = in_describe = False
    for n, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r'^Table\s+"[\w.]+"\s*$', stripped):
            in_describe, in_query = True, False
        if in_describe:
            if not stripped and n + 1 < len(lines) and not lines[n + 1].startswith(" "):
                in_describe = False
            schema.append(line)
            continue
        if _PLAN.search(line) or (plan and _PLAN_DETAIL.match(line) and not in_query):
            plan.append(line)
            in_query = False
            continue
        if not query and _QUERY_START.match(stripped):
            in_query = True
        elif not query and (colon := _first_query_colon(line)) is not None:
            words.append(line[:colon.start()])
            query.append(line[colon.end():])
            in_query = True
            continue
        if in_query and not stripped:
            nxt = next((ln for ln in lines[n + 1:] if ln.strip()), "")
            in_query = bool(nxt) and bool(_CONTINUE.match(nxt)) and not _PLAN.search(nxt)
            continue
        if in_query:
            query.append(line)
        elif schema_text.list_line_ddl(line):
            schema.append(line)
        else:
            words.append(line)


def _first_query_colon(line: str):
    return next((c for c in _COLON.finditer(line) if _QUERY_START.match(line[c.end():])), None)
