"""Pure: find the schema in pasted text and normalise it to DDL. Three shapes are read:

    CREATE TABLE / CREATE INDEX statements          -> kept as written
    orders(id int pk, customer_id int fk customers.id, total numeric)   -> CREATE TABLE
    psql `\\d orders` output (Table "public.orders" + a | table)       -> CREATE TABLE + CREATE INDEX
"""

from __future__ import annotations

import re

_CREATE = re.compile(r"\bcreate\s+(?:or\s+replace\s+)?(?:unique\s+)?(?:temp\w*\s+)?(?:external\s+)?(table|index)\b",
                     re.IGNORECASE)
_TAIL_CLAUSE = re.compile(r"^\s*(?:partition|cluster|engine|order\s+by|primary\s+key|options|with|distkey|sortkey|"
                          r"diststyle|inherits|tablespace|settings|comment|\))", re.IGNORECASE)
_LIST_LINE = re.compile(r"^\s*(?:[-*]\s+|\d+[.)]\s+)?([A-Za-z_][\w.]*)\s*\((.+)\)\s*[;,.]?\s*$")
_DESCRIBE = re.compile(r'^\s*Table\s+"([\w.]+)"\s*$', re.MULTILINE)
_NOT_COLUMN = re.compile(r"\b(?:select|from|where|count|sum|avg|min|max)\b|\*", re.IGNORECASE)
_PK = re.compile(r"\b(?:pk|primary\s+key)\b", re.IGNORECASE)
_NN = re.compile(r"\bnot\s+null\b", re.IGNORECASE)
_FK = re.compile(r"(?:\bfk\b|->|\breferences\b)\s*(?:to\s+)?([A-Za-z_]\w*)\s*(?:\.\s*(\w+)|\(\s*(\w+)\s*\))?",
                 re.IGNORECASE)
_UNIQUE = re.compile(r"\bunique\b", re.IGNORECASE)
UNKNOWN_TYPE = "UNKNOWN_TYPE"


def create_statements(text: str) -> list[tuple[int, int]]:
    """(start, end) spans of CREATE TABLE / INDEX statements, ending at `;` or after the closing paren."""
    spans, pos = [], 0
    while (m := _CREATE.search(text, pos)) is not None:
        end = _statement_end(text, m.start(), m.group(1).casefold() == "table")
        spans.append((m.start(), end))
        pos = end
    return spans


def _statement_end(text: str, start: int, is_table: bool) -> int:
    depth, opened, i = 0, False, start
    while i < len(text):
        ch = text[i]
        if ch == ";" and depth == 0:
            return i + 1
        if ch == "(":
            depth, opened = depth + 1, True
        elif ch == ")":
            depth -= 1
        elif ch == "\n" and depth == 0 and (opened or not is_table):
            nxt = text[i + 1:].split("\n", 1)[0]
            if not is_table or not _TAIL_CLAUSE.match(nxt):
                return i
        i += 1
    return len(text)


def to_ddl(text: str) -> str:
    """Every schema shape in `text` as DDL statements separated by `;`."""
    out, rest = [], text or ""
    for start, end in create_statements(rest):
        out.append(rest[start:end].strip().rstrip(";"))
    rest = _remove_spans(rest, create_statements(rest))
    described, rest = _describe_blocks(rest)
    out += described
    out += [ddl for line in rest.splitlines() if (ddl := list_line_ddl(line))]
    return ";\n".join(out) + (";" if out else "")


def has_list_or_describe(text: str) -> bool:
    return bool(_DESCRIBE.search(text or "")) or any(list_line_ddl(ln) for ln in (text or "").splitlines())


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    for start, end in reversed(spans):
        text = text[:start] + text[end:]
    return text


def list_line_ddl(line: str) -> str | None:
    """`orders(id int pk, customer_id int fk customers.id)` -> CREATE TABLE, or None when not that shape."""
    m = _LIST_LINE.match(line)
    if not m or _NOT_COLUMN.search(m.group(2)):
        return None
    items = [i.strip() for i in split_top(m.group(2)) if i.strip()]
    if not items or not all(re.match(r"^[A-Za-z_]\w*\b", i) for i in items):
        return None
    return f"CREATE TABLE {m.group(1)} ({', '.join(_list_column(i) for i in items)})"


def split_top(text: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in text:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    return [*parts, "".join(cur)]


def _list_column(item: str) -> str:
    name, _, rest = item.partition(" ")
    fk = _FK.search(rest)
    plain = _FK.sub(" ", _NN.sub(" ", _PK.sub(" ", _UNIQUE.sub(" ", rest))))
    type_text = " ".join(plain.split()) or UNKNOWN_TYPE
    bits = [name, type_text]
    bits += ["NOT NULL"] if _NN.search(rest) else []
    bits += ["PRIMARY KEY"] if _PK.search(rest) else []
    if fk:
        bits.append(f"REFERENCES {fk.group(1)}({fk.group(2) or fk.group(3) or 'id'})")
    return " ".join(bits)


def _describe_blocks(text: str) -> tuple[list[str], str]:
    heads = list(_DESCRIBE.finditer(text))
    if not heads:
        return [], text
    out = []
    for n, head in enumerate(heads):
        end = heads[n + 1].start() if n + 1 < len(heads) else len(text)
        out += _describe(head.group(1).rsplit(".", 1)[-1], text[head.end():end])
    return out, text[:heads[0].start()]


def _describe(table: str, body: str) -> list[str]:
    cols, extra, indexes, section = [], [], [], ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.endswith(":"):
            section = stripped[:-1].casefold()
        elif "|" in line and not section:
            cells = [c.strip() for c in line.split("|")]
            if cells[0] and cells[0].casefold() != "column" and not set(cells[0]) <= set("-+"):
                nn = " NOT NULL" if len(cells) > 3 and "not null" in cells[3].casefold() else ""
                cols.append(f"{cells[0]} {cells[1] or UNKNOWN_TYPE}{nn}")
        elif section == "indexes" and (m := re.search(r'"(\w+)"\s+(PRIMARY KEY,)?.*?\(([^)]*)\)', stripped)):
            if m.group(2):
                extra.append(f"PRIMARY KEY ({m.group(3)})")
            else:
                indexes.append(f"CREATE INDEX {m.group(1)} ON {table} ({m.group(3)})")
        elif section.startswith("foreign") and (m := re.search(r"FOREIGN KEY \(([^)]*)\) REFERENCES (\w+)\(([^)]*)\)",
                                                               stripped)):
            extra.append(f"FOREIGN KEY ({m.group(1)}) REFERENCES {m.group(2)}({m.group(3)})")
    if not cols:
        return []
    return [f"CREATE TABLE {table} ({', '.join(cols + extra)})", *indexes]
