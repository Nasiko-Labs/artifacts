"""Pure: what code reads from the message before any model call: the sheet title (quoted, or after
`from`/`sheet`), a sheet link's id, the tab, an A1 range, "as json", a pasted table, a "why" question, and
an envelope from an earlier workflow step (`spec`, `source`, `table`/`rows`, `output`)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

import paste as pastes

_JSON_MODE = re.compile(r"\b(?:as|in|return|output)\s+json\b|\bjson\s+only\b|^\s*json\s*$", re.IGNORECASE)
_QUOTED = re.compile(r"[\"“']([^\"“”']{2,80})[\"”']")
_LINK_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{10,})")
_FROM = re.compile(r"\b(?:from|sheet|spreadsheet)\s+(?:the\s+|my\s+|our\s+)?(?P<title>[^,.;?!]+)", re.IGNORECASE)
_IN_SHEET = re.compile(r"\b(?:in|on|of)\s+(?:the\s+|my\s+|our\s+)?(?P<title>[A-Za-z0-9][^,.;?!]*?\s(?:sheet|spreadsheet))\b",
                       re.IGNORECASE)
_TITLE_STOP = re.compile(r"\s+(?:as|for|by|where|since|tab|during|over|between|with|and show|grouped|sorted|"
                         r"last|this|in the|limit|top)\b.*$", re.IGNORECASE)
_TABS = (
    re.compile(r"\btab\s+[\"“']([^\"”']+)[\"”']", re.IGNORECASE),
    re.compile(r"[\"“']([^\"”']+)[\"”']\s+tab\b", re.IGNORECASE),
    re.compile(r"\btab\s+([A-Za-z0-9_]+)\b", re.IGNORECASE),
    re.compile(r"\b((?!sheet\b|the\b|a\b|this\b|that\b)[A-Za-z0-9_]+)\s+tab\b", re.IGNORECASE),
)
_RANGE = re.compile(r"\b([A-Z]{1,3}\d+:[A-Z]{1,3}\d+)\b")
_WHY = re.compile(r"^\s*(?:why\b|what (?:caused|made|drove)\b|explain why\b|how come\b)", re.IGNORECASE)
_NOT_TITLES = re.compile(r"^(?:last|this|the last|past|previous|today|yesterday|since|october|november|december|"
                         r"january|february|march|april|june|july|august|september|q[1-4]|\d)", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    text: str                       # the request words (the message without a pasted table)
    title: str = ""
    sheet_id: str = ""
    tab: str = ""
    cell_range: str = ""
    json_mode: bool = False
    why: bool = False
    spec: Mapping[str, Any] | None = None
    table_rows: tuple[tuple[object, ...], ...] = field(default=())   # an envelope's own table, header first
    pasted: str = ""                # the pasted table text, verbatim

    def to_dict(self) -> dict:
        data = asdict(self)
        data["table_rows"] = [list(r) for r in self.table_rows]
        data["spec"] = dict(self.spec) if self.spec else None
        return data

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Request:
        values = dict(data)
        values["table_rows"] = tuple(tuple(r) for r in values.get("table_rows") or ())
        return Request(**{k: v for k, v in values.items() if k in Request.__dataclass_fields__})

    @property
    def names_source(self) -> bool:
        return bool(self.title or self.sheet_id)


def parse(message: str, metadata: Mapping[str, Any] | None = None) -> Request:
    meta = dict(metadata or {})
    json_mode = str(meta.get("output") or "").casefold() == "json"
    envelope = _envelope(message)
    if envelope is not None:
        return _from_envelope(envelope, json_mode)
    pasted = pastes.find(message)
    text = pasted.request if pasted else message
    title, sheet_id = _title(text)
    return Request(text=text, title=title, sheet_id=sheet_id, tab=_tab(text), cell_range=_range(text),
                   json_mode=json_mode or bool(_JSON_MODE.search(text)), why=bool(_WHY.search(text)),
                   pasted=pasted.text if pasted else "")


def _title(text: str) -> tuple[str, str]:
    link = _LINK_ID.search(text)
    if link:
        return "", link.group(1)
    for m in _QUOTED.finditer(text):
        before = text[:m.start()].rstrip().casefold()
        if not before.endswith("tab"):
            after = text[m.end():].lstrip().casefold()
            if not after.startswith("tab"):
                return m.group(1).strip(), ""
    for m in _FROM.finditer(text):
        title = _TITLE_STOP.sub("", m.group("title")).strip().strip("'\"")
        if title and not _NOT_TITLES.match(title) and len(title) <= 80:
            return title, ""
    m = _IN_SHEET.search(text)
    if m and not _NOT_TITLES.match(m.group("title")):
        return m.group("title").strip(), ""
    return "", ""


def _tab(text: str) -> str:
    for pattern in _TABS:
        m = pattern.search(text)
        if m:
            return m.group(1).strip()
    return ""


def _range(text: str) -> str:
    m = _RANGE.search(text)
    return m.group(1) if m else ""


def _envelope(message: str) -> Mapping[str, Any] | None:
    body = (message or "").strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or not ({"spec", "source", "table", "rows", "items"} & data.keys()):
        return None
    return data


def _from_envelope(data: Mapping[str, Any], json_mode: bool) -> Request:
    source = data.get("source") if isinstance(data.get("source"), Mapping) else {}
    raw_spec = data.get("spec") if isinstance(data.get("spec"), Mapping) else None
    output_json = str(data.get("output") or "").casefold() == "json"
    return Request(text=str(data.get("request") or data.get("summary") or ""),
                   title=str(source.get("title") or ""), sheet_id=str(source.get("id") or ""),
                   tab=str(source.get("tab") or ""), cell_range=str(source.get("range") or ""),
                   json_mode=json_mode or output_json, spec=raw_spec, table_rows=_table_rows(data))


def _table_rows(data: Mapping[str, Any]) -> tuple[tuple[object, ...], ...]:
    """An envelope's table as header + rows: `table{columns, rows}`, or `rows`/`items` as objects."""
    table = data.get("table") if isinstance(data.get("table"), Mapping) else None
    if table and isinstance(table.get("columns"), list) and isinstance(table.get("rows"), list):
        header = tuple(c.get("name") if isinstance(c, Mapping) else str(c) for c in table["columns"])
        return (header, *(tuple(r) for r in table["rows"] if isinstance(r, list)))
    for key in ("rows", "items"):
        items = data.get(key)
        if isinstance(items, list) and items and all(isinstance(i, Mapping) for i in items):
            header = tuple(k for k in items[0] if k != "src_rows")
            return (header, *(tuple(i.get(h, "") for h in header) for i in items))
    return ()
