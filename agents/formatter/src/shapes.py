"""Pure: shape detection. Walks the JSON in key order and lays each value out as a section.

Rules: a long string is a titled paragraph (Markdown only for `*_markdown`, `notes`, `formatted`);
short scalars next to each other share one "Details" section; a list of scalars is bullets; a list of
flat objects is a table (at most 6 distinct keys) or cards; nested objects are numbered cards; an object
is key/value lines. A value nested deeper than `MAX_DEPTH` is shown as JSON. Nothing is dropped except an
`items` list that repeats another field.
"""

from __future__ import annotations

from typing import Any

import templates
from model import (
    BULLETS,
    CARDS,
    FIELDS,
    JSON_BLOCK,
    PARAGRAPH,
    TABLE,
    Card,
    Document,
    Json,
    ScalarList,
    Section,
    header,
    is_scalar,
)

MAX_DEPTH = 6
MAX_TABLE_KEYS = 6
SHORT_SCALAR_CHARS = 80
SHORT_LIST_ITEMS = 8
FOOTER_KEYS = ("confidence", "needs_human")
MARKDOWN_KEYS = frozenset({"notes", "memo_markdown", "formatted"})
TITLE_KEYS = ("title", "name", "task", "story", "text", "decision", "to_whom", "option", "term")


def build_document(value: Any) -> Document:
    if isinstance(value, dict):
        return _from_object(value)
    if isinstance(value, list):
        return Document(sections=(section_for("items", value, "Items"),), deep=_deep_count({"items": value}))
    return Document(sections=(Section("Value", PARAGRAPH, text=value),))


def depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((depth(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((depth(v) for v in value), default=0)
    return 0


def _from_object(value: dict) -> Document:
    lead = value.get("summary") if isinstance(value.get("summary"), str) else None
    footer_given = [k for k in FOOTER_KEYS if k in value and is_scalar(value[k])]
    footer = (value.get("confidence"), value.get("needs_human")) if footer_given else None
    body = {k: v for k, v in value.items()
            if not (k == "summary" and lead is not None) and k not in footer_given}
    body = _without_repeated_items(body)
    match = templates.match(body)
    sections = _templated(body, match) if match else _generic(body)
    return Document(sections=sections, lead=lead, footer=footer, needs_human=value.get("needs_human") is True,
                    template=match.name if match else None, deep=_deep_count(body))


def _without_repeated_items(body: dict) -> dict:
    items = body.get("items")
    if not isinstance(items, list) or not any(k != "items" and v == items for k, v in body.items()):
        return body
    return {k: v for k, v in body.items() if k != "items"}


def _deep_count(body: dict) -> int:
    return sum(1 for v in body.values() if depth(v) > MAX_DEPTH)


def _templated(body: dict, match: templates.Template) -> tuple[Section, ...]:
    sections = [section_for(slot.key, body[slot.key], slot.heading, slot.hint, slot.columns)
                for slot in match.slots if slot.key in body]
    known = {slot.key for slot in match.slots}
    leftover = tuple((k, to_cell(v)) for k, v in body.items() if k not in known)
    if leftover:
        sections.append(Section("Other fields", FIELDS, lines=leftover))
    return tuple(sections)


def _generic(body: dict) -> tuple[Section, ...]:
    sections: list[Section] = []
    details: list[tuple[str, Any]] = []
    for key, value in body.items():
        if _is_short_scalar(value):
            details.append((key, value))
            continue
        if details:
            sections.append(Section("Details", FIELDS, lines=tuple(details)))
            details = []
        sections.append(section_for(key, value, header(key)))
    if details:
        sections.append(Section("Details", FIELDS, lines=tuple(details)))
    return tuple(sections)


def _is_short_scalar(value: Any) -> bool:
    if not is_scalar(value):
        return False
    return not isinstance(value, str) or (len(value) <= SHORT_SCALAR_CHARS and "\n" not in value)


def section_for(key: str, value: Any, title: str, hint: str | None = None, columns: tuple = ()) -> Section:
    if depth(value) > MAX_DEPTH:
        return Section(title, JSON_BLOCK, value=value)
    if is_scalar(value):
        is_md = isinstance(value, str) and (hint == "markdown" or key.endswith("_markdown") or key in MARKDOWN_KEYS)
        return Section(title, PARAGRAPH, text=value, markdown=is_md)
    if isinstance(value, dict):
        return Section(title, FIELDS, lines=tuple((k, to_cell(v)) for k, v in value.items()))
    return _list_section(value, title, hint, columns)


def _list_section(values: list, title: str, hint: str | None, columns: tuple) -> Section:
    if all(is_scalar(v) for v in values):
        return Section(title, BULLETS, items=tuple(values))
    if not all(isinstance(v, dict) for v in values):
        return Section(title, BULLETS, items=tuple(to_cell(v) for v in values))
    keys = _ordered_keys(values, columns)
    if hint != "cards" and all(_is_flat(v) for v in values) and len(keys) <= MAX_TABLE_KEYS:
        rows = tuple(tuple(to_cell(v[k]) if k in v else None for k in keys) for v in values)
        return Section(title, TABLE, columns=tuple(keys), rows=rows)
    return Section(title, CARDS, cards=tuple(_card(v) for v in values))


def _ordered_keys(values: list[dict], preferred: tuple) -> list[str]:
    seen: list[str] = []
    for value in values:
        seen += [k for k in value if k not in seen]
    first = [k for k in preferred if k in seen]
    return first + [k for k in seen if k not in first]


def _is_flat(value: dict) -> bool:
    return bool(value) and all(is_scalar(v) or _is_short_list(v) for v in value.values())


def _is_short_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) <= SHORT_LIST_ITEMS and all(is_scalar(v) for v in value)


def _card(value: dict) -> Card:
    title_key = next((k for k in TITLE_KEYS if k in value and is_scalar(value[k]) and value[k] is not None), None)
    lines = tuple((k, to_cell(v)) for k, v in value.items() if k != title_key)
    return Card(title=value[title_key] if title_key else None, lines=lines)


def to_cell(value: Any) -> Any:
    if is_scalar(value):
        return value
    if isinstance(value, list) and all(is_scalar(v) for v in value):
        return ScalarList(tuple(value))
    return Json(value)
