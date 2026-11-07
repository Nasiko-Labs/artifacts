"""Pure: what the user typed. The first paragraph is the request; every later block (separated by a blank line)
is a pasted feedback item with optional `customer:`, `date:`, `tier:` and `arr:` lines. A JSON envelope from a
previous step is recognised by its `themes`/`items` lists. Channels (`#name`) and `team:` are read here."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from arr import parse_arr
from checks import parse_day
from records import Item
from themes import normalise_tier

MIN_ITEM_CHARS = 10
_BLOCKS = re.compile(r"\n\s*\n")
_FIELD = re.compile(r"^\s*(customer|company|account|date|tier|plan|arr)\s*:\s*(.*)$", re.IGNORECASE)
_CHANNEL = re.compile(r"(?<![\w&])#([a-z0-9][a-z0-9_-]{0,79})", re.IGNORECASE)
_TEAM = re.compile(r"\bteam\s*:\s*([\w .-]{1,40}?)(?:[,;\n]|$)", re.IGNORECASE)
_JSON_MODE = re.compile(r"\b(?:as json|json only)\b", re.IGNORECASE)
_FENCED = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class Request:
    text: str                      # the request paragraph
    pasted: tuple[Item, ...] = ()
    unparsed: int = 0
    channels: tuple[str, ...] = ()
    team: str = ""
    json_mode: bool = False
    envelope: dict | None = None
    truncated: bool = False


def read(message: str, max_chars: int = 100_000, json_meta: bool = False) -> Request:
    raw = message or ""
    truncated = len(raw) > max_chars
    raw = raw[:max_chars]
    envelope = _envelope(raw)
    blocks = [b.strip() for b in _BLOCKS.split(raw.strip()) if b.strip()]
    head = blocks[0] if blocks else ""
    items, unparsed = _items(blocks[1:]) if envelope is None else ((), 0)
    team = _TEAM.search(head)
    return Request(text=head, pasted=items, unparsed=unparsed,
                   channels=tuple(dict.fromkeys(c.casefold() for c in _CHANNEL.findall(head))),
                   team=team.group(1).strip() if team else "",
                   json_mode=json_meta or bool(_JSON_MODE.search(head)), envelope=envelope, truncated=truncated)


def _items(blocks: list[str]) -> tuple[tuple[Item, ...], int]:
    items: list[Item] = []
    unparsed = 0
    for block in blocks:
        item = _item(block)
        if item is None:
            unparsed += 1
        else:
            items.append(item)
    numbered = tuple(Item(**{**item.to_dict(), "id": f"p{n}", "ref": f"pasted {n}"})
                     for n, item in enumerate(items, start=1))
    return numbered, unparsed


def _item(block: str) -> Item | None:
    found: dict[str, str] = {}
    text_lines = []
    for line in block.splitlines():
        match = _FIELD.match(line)
        if match:
            found[match.group(1).casefold()] = match.group(2).strip()
        else:
            text_lines.append(line.strip())
    text = " ".join(t for t in text_lines if t)
    if len(text) < MIN_ITEM_CHARS:
        return None
    day = parse_day(found.get("date"))
    customer = found.get("customer") or found.get("company") or found.get("account") or ""
    return Item(id="", source="pasted", text=text, customer=customer, date=day.isoformat() if day else "",
                tier=normalise_tier(found.get("tier") or found.get("plan")), arr=parse_arr(found.get("arr")))


def _envelope(raw: str) -> dict | None:
    text = raw.strip()
    match = _FENCED.search(text)
    body = match.group(1) if match else text
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("themes") or data.get("items"), list):
        return data
    return None
