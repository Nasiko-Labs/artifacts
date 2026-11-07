"""Pure: pasted feedback -> items, first matching shape wins: CSV/TSV with a header row, JSON (an array of
objects or another agent's envelope), `[Acme | enterprise | 2026-10-03 | $120k] text` lines, plain blocks.
Nothing here guesses a value: a field that isn't there stays empty and is reported as unknown."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field, replace

import fields as f
from items import FeedbackItem, make_item, numbered

MIN_LINE_CHARS = 15
ROLE_ALIASES = {
    "text": ("text", "feedback", "comment", "body", "message", "description", "verbatim"),
    "customer": ("customer", "account", "company", "org", "user", "email"),
    "date": ("date", "created", "created_at", "submitted"),
    "tier": ("tier", "plan", "segment"),
    "arr": ("arr", "acv", "mrr", "revenue", "value"),
    "ref": ("id", "ticket", "key"),
    "source": ("source", "channel"),
}
ENVELOPE_TEXT = ("text", "quote", "summary")
ENVELOPE_CUSTOMER = ("customer", "account")
_PREFIXED = re.compile(r"^\s*\[([^\]\n]{1,200})\]\s*(.+)$")
_BLANK = re.compile(r"\n\s*\n")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Parsed:
    items: tuple[FeedbackItem, ...] = ()
    shape: str = "none"
    unparsed: int = 0                       # lines dropped as noise or rows without text
    ambiguous: tuple[str, ...] = ()         # candidate text columns when two or more match
    column_default: bool = False            # the text column was picked by a default, not by its name
    notes: tuple[str, ...] = field(default_factory=tuple)


def parse(text: str, text_column: str | None = None) -> Parsed:
    body = (text or "").strip()
    if not body:
        return Parsed()
    for attempt in (_json, lambda b: _csv(b, text_column), _prefixed):
        result = attempt(body)
        if result is not None:
            return result
    return _plain(body)


def _plain_name(name: str) -> str:
    return " ".join(str(name).casefold().replace("_", " ").replace("-", " ").split())


def column_roles(header: list[str], text_column: str | None = None) -> tuple[dict[str, int], tuple[str, ...]]:
    """(role -> column index, ambiguous text candidates when two or more columns could be the text)."""
    names = [_plain_name(h) for h in header]
    aliases = {role: {_plain_name(a) for a in words} for role, words in ROLE_ALIASES.items()}
    exact = {role: [i for i, n in enumerate(names) if n in aliases[role]] for role in aliases}
    loose = {role: [i for i, n in enumerate(names) if set(n.split()) & aliases[role]] for role in aliases}
    roles: dict[str, int] = {}
    if text_column is not None:
        wanted = [i for i, n in enumerate(names) if n == _plain_name(text_column)]
        if wanted:
            roles["text"] = wanted[0]
    else:
        candidates = exact["text"] or loose["text"]
        if len(candidates) > 1:
            return {}, tuple(header[i].strip() for i in candidates)
        if candidates:
            roles["text"] = candidates[0]
    for role in aliases:
        if role == "text":
            continue
        free = [i for i in (exact[role] or loose[role]) if i not in roles.values()]
        if free:
            roles[role] = free[0]
    return roles, ()


def _csv(body: str, text_column: str | None) -> Parsed | None:
    first = body.splitlines()[0]
    delimiter = next((d for d in ("\t", ",", ";", "|") if d in first), None)
    if delimiter is None or "\n" not in body:
        return None
    rows = [r for r in csv.reader(io.StringIO(body), delimiter=delimiter) if any(c.strip() for c in r)]
    header = [h.strip() for h in rows[0]]
    if len(header) < 2 or any(len(h) > 40 or not h for h in header) or len(rows) < 2:
        return None
    roles, ambiguous = column_roles(header, text_column)
    if ambiguous:
        return Parsed(shape="csv", ambiguous=ambiguous)
    by_default = False
    if "text" not in roles:
        if len(roles) < 2:
            return None
        rest = [i for i in range(len(header)) if i not in roles.values()]
        if not rest:
            return None
        roles["text"] = max(rest, key=lambda i: sum(len(r[i]) for r in rows[1:] if i < len(r)))
        by_default = True
    items, unparsed = [], 0
    for row in rows[1:]:
        cell = {role: (row[i].strip() if i < len(row) else "") for role, i in roles.items()}
        if not cell["text"]:
            unparsed += 1
            continue
        items.append(make_item(cell["text"], cell.get("customer", ""), cell.get("date", ""), cell.get("tier", ""),
                               cell.get("arr") or None, cell.get("source", ""), cell.get("ref", "")))
    note = (f'No column is named like feedback text, so I read "{header[roles["text"]]}" as the text.',) \
        if by_default else ()
    return Parsed(numbered(items), "csv", unparsed, column_default=by_default, notes=note)


def _json(body: str) -> Parsed | None:
    fenced = _FENCED_JSON.match(body)
    text = fenced.group(1) if fenced else body
    if not text.startswith(("[", "{")):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    rows = data.get("items") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None
    items, unparsed = [], 0
    for row in rows:
        item = _from_object(row, envelope=isinstance(data, dict))
        if item is None:
            unparsed += 1
        else:
            items.append(item)
    return Parsed(numbered(items), "envelope" if isinstance(data, dict) else "json", unparsed)


def _from_object(row: object, envelope: bool) -> FeedbackItem | None:
    if isinstance(row, str):
        return make_item(row) if row.strip() else None
    if not isinstance(row, dict):
        return None
    keys = {_plain_name(k): k for k in row}
    if envelope:
        text_key = next((keys[k] for k in ENVELOPE_TEXT if k in keys and isinstance(row[keys[k]], str)), None)
        customer_key = next((keys[k] for k in ENVELOPE_CUSTOMER if k in keys), None)
        roles = {"customer": customer_key} if customer_key else {}
    else:
        index, _ = column_roles(list(row), None)
        names = list(row)
        roles = {role: names[i] for role, i in index.items() if role != "text"}
        text_key = names[index["text"]] if "text" in index else None
    value = str(row.get(text_key) or "").strip() if text_key else ""
    if not value:
        return None
    get = {role: row.get(key) for role, key in roles.items()}
    return make_item(value, str(get.get("customer") or ""), str(get.get("date") or ""), str(get.get("tier") or ""),
                     get.get("arr"), str(get.get("source") or ""), str(get.get("ref") or ""))


def _prefixed(body: str) -> Parsed | None:
    lines = [line for line in body.splitlines() if line.strip()]
    hits = [_PREFIXED.match(line) for line in lines]
    if sum(1 for h in hits if h) < 3 or sum(1 for h in hits if h) * 2 < len(lines):
        return None
    items: list[FeedbackItem] = []
    for line, hit in zip(lines, hits, strict=True):
        if hit:
            items.append(_prefixed_item(hit.group(1), hit.group(2)))
        elif items:  # a continuation line belongs to the item above
            items[-1] = replace(items[-1], text=f"{items[-1].text} {line.strip()}")
    return Parsed(numbered(items), "prefixed", sum(1 for h in hits if not h) if not items else 0)


def _prefixed_item(prefix: str, text: str) -> FeedbackItem:
    found = {"customer": "", "date": "", "tier": "", "arr": None}
    for raw in (p.strip() for p in prefix.split("|")):
        if not raw:
            continue
        if f.parse_date(raw) and not found["date"]:
            found["date"] = raw
        elif f.looks_like_amount(raw) and found["arr"] is None:
            found["arr"] = raw
        elif f.is_tier_word(raw) and not found["tier"]:
            found["tier"] = raw
        elif not found["customer"]:
            found["customer"] = raw
    return make_item(text, found["customer"], found["date"], found["tier"], found["arr"])


def _plain(body: str) -> Parsed:
    blocks = [b.strip() for b in _BLANK.split(body) if b.strip()]
    pieces = blocks if len(blocks) > 1 else [line.strip() for line in body.splitlines() if line.strip()]
    kept = [p for p in pieces if len(p) >= MIN_LINE_CHARS]
    return Parsed(numbered([make_item(p) for p in kept]), "plain", len(pieces) - len(kept))
