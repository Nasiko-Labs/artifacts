"""Pure: the dashboard index. Rows come from a spreadsheet's values (first row = header) or from a list
the user pasted (`name | url | description`, tabs work too). A row needs a name and an http(s) url,
else it is dropped and counted. Tokens (name, description, metrics, dimensions, tags) are lowercased,
stop words removed and lightly stemmed, the same way as request terms, so code can check coverage."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import Any

from models import Index, Row

STALE_DAYS = 30
MAX_ROWS = 300
_WORD = re.compile(r"[a-z0-9]+")
_URL = re.compile(r"https?://[^\s|<>\"')\]]+")
_SEPARATOR_ROW = re.compile(r"^\s*\|?\s*:?-{2,}")
STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "by", "per", "with", "from", "at", "as", "is",
    "are", "be", "this", "that", "these", "those", "it", "its", "our", "we", "you", "your", "my", "me", "i", "can",
    "could", "would", "please", "pull", "get", "give", "show", "need", "want", "some", "any", "all", "what", "whats",
    "how", "many", "much", "there", "dashboard", "dashboards", "report", "numbers", "number", "data", "split",
    "broken", "down", "breakdown", "s", "do", "does", "have", "has", "someone", "anyone", "us", "know", "let",
})
# Header words → field. Checked as whole cells after lowercasing and trimming.
HEADERS: Mapping[str, tuple[str, ...]] = {
    "name": ("name", "dashboard", "dashboard name", "title"),
    "url": ("url", "link", "dashboard url", "dashboard link", "href"),
    "description": ("description", "desc", "about", "summary", "what it shows"),
    "owner": ("owner", "owned by", "team"),
    "metrics": ("metrics", "metric", "measures", "measure", "kpis", "kpi"),
    "dimensions": ("dimensions", "dimension", "breakdowns", "breakdown", "split by", "by"),
    "tags": ("tags", "tag", "keywords", "audience", "topics"),
    "last_refreshed": ("last_refreshed", "last refreshed", "refreshed", "last updated", "updated", "updated at",
                       "last refresh", "refreshed at"),
}
_DATE_FORMATS = ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%Y/%m/%d", "%m/%d/%Y")


def stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text: str) -> tuple[str, ...]:
    """Lowercased, stop words removed, lightly stemmed; order kept, duplicates kept."""
    return tuple(stem(w) for w in _WORD.findall((text or "").casefold()) if w not in STOP_WORDS)


def _field_of(cell: str) -> str | None:
    label = " ".join(str(cell).casefold().replace("_", " ").split())
    return next((f for f, names in HEADERS.items() if label in names or label == f.replace("_", " ")), None)


def _is_header(cells: Sequence[str]) -> bool:
    fields = [_field_of(c) for c in cells]
    return "url" in fields and ("name" in fields or "description" in fields)


def from_table(table: Sequence[Sequence[Any]], title: str, source: str) -> Index:
    """Rows from a table whose first non-empty row may be a header; without a header, by position."""
    rows = [[str(c).strip() for c in r] for r in table if any(str(c).strip() for c in r)]
    if not rows:
        return Index(title, (), source)  # type: ignore[arg-type]
    if _is_header(rows[0]):
        mapping = {i: _field_of(c) for i, c in enumerate(rows[0])}
        records = [{mapping[i]: v for i, v in enumerate(r) if mapping.get(i)} for r in rows[1:]]
    else:
        records = [_by_position(r) for r in rows]
    return _index(records[:MAX_ROWS], title, source, max(0, len(records) - MAX_ROWS))


def _by_position(cells: Sequence[str]) -> dict[str, str]:
    url_at = next((i for i, c in enumerate(cells) if _URL.fullmatch(c.strip())), None)
    if url_at is None:
        return {"name": cells[0] if cells else ""}
    if url_at == 1:
        names = ("name", "url", "description", "metrics", "dimensions", "last_refreshed")
        return {f: v for f, v in zip(names, cells, strict=False)}
    rest = [c for i, c in enumerate(cells) if i != url_at]
    return {"name": rest[0] if rest else "", "url": cells[url_at], "description": "; ".join(rest[1:])}


def _index(records: Iterable[Mapping[str, str]], title: str, source: str, over: int = 0) -> Index:
    kept: list[Row] = []
    dropped = over
    for record in records:
        name, url = (record.get("name") or "").strip(), (record.get("url") or "").strip()
        if not name or not _URL.fullmatch(url):
            dropped += 1
            continue
        values = {k: (record.get(k) or "").strip() for k in ("description", "owner", "metrics", "dimensions",
                                                              "tags", "last_refreshed")}
        row = Row(id=f"R{len(kept) + 1}", name=name, url=url, **values)
        kept.append(replace(row, tokens=frozenset(tokens(row.text))))
    return Index(title, tuple(kept), source, dropped)  # type: ignore[arg-type]


def split_paste(text: str) -> tuple[str, Index | None]:
    """(the request text, the pasted index or None). Index lines hold `|` or a tab and either a url or
    the header; everything else is the request."""
    request_lines, table = [], []
    for line in (text or "").splitlines():
        if _SEPARATOR_ROW.match(line) and "-" in line and not _URL.search(line):
            continue
        tabular = ("|" in line or "\t" in line) and len(_cells(line)) >= 2
        if tabular and (table or _URL.search(line) or _is_header(_cells(line))):
            table.append(_cells(line))
        else:
            request_lines.append(line)
    request = " ".join(" ".join(request_lines).split())
    if not table:
        return request, None
    return request, from_table(table, "pasted list", "paste")


def _cells(line: str) -> list[str]:
    sep = "\t" if "\t" in line and "|" not in line else "|"
    cells = [c.strip() for c in line.strip().strip("|").split(sep)]
    return cells


# --- tool output ----------------------------------------------------------------------------------

def load(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def walk(node: Any) -> Iterable[Any]:
    yield node
    if isinstance(node, Mapping):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


_ID_KEYS = ("spreadsheetId", "spreadsheet_id", "fileId", "file_id", "id")
_TITLE_KEYS = ("name", "title")


def sheets_in(text: str) -> tuple[tuple[str, str], ...]:
    """(id, title) of every spreadsheet-shaped object in a search result, first occurrence per id."""
    found: dict[str, str] = {}
    for node in walk(load(text)):
        if not isinstance(node, Mapping):
            continue
        sid = next((node[k] for k in _ID_KEYS if isinstance(node.get(k), str) and node[k].strip()), None)
        props = node.get("properties") if isinstance(node.get("properties"), Mapping) else {}
        title = next((v for v in (*(node.get(k) for k in _TITLE_KEYS), props.get("title"))
                      if isinstance(v, str) and v.strip()), None)
        mime = node.get("mimeType") or node.get("mime_type")
        if sid is None or title is None or (isinstance(mime, str) and "spreadsheet" not in mime):
            continue
        found.setdefault(sid.strip(), title.strip())
    return tuple(found.items())


def matching_sheets(sheets: Sequence[tuple[str, str]], wanted: str | None) -> tuple[tuple[str, str], ...]:
    """Sheets whose title contains the title the user gave, else any title naming dashboards."""
    needle = (wanted or "").casefold().strip()
    if needle:
        return tuple(s for s in sheets if needle in s[1].casefold())
    return tuple(s for s in sheets if "dashboard" in s[1].casefold())


def values_in(text: str) -> list[list[Any]] | None:
    """The first list-of-lists `values` table in a read result."""
    for node in walk(load(text)):
        if isinstance(node, Mapping):
            values = node.get("values")
            if isinstance(values, list) and values and all(isinstance(r, list) for r in values):
                return values
    return None


# --- dates ----------------------------------------------------------------------------------------

def parse_day(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def day_label(day: date, today: date) -> str:
    label = f"{day.day} {day.strftime('%b')}"
    return label if day.year == today.year else f"{label} {day.year}"


def staleness_note(row: Row, today: date) -> str | None:
    """'Last refreshed 4 Oct.' when the index says the dashboard is older than 30 days, else None."""
    day = parse_day(row.last_refreshed)
    if day is None or (today - day).days <= STALE_DAYS:
        return None
    return f"Last refreshed {day_label(day, today)}."
