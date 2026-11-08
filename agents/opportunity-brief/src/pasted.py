"""Pure: pasted material into evidence items. Items split on blank lines or on lines that start with a URL;
each kept item is capped at 1,500 chars with a visible mark. A bare URL with no text is never fetched: it is
listed as not read, except a Hacker News item link, whose digits-only id is handed to the HN source.
An earlier brief's envelope (JSON with items[].title and links/evidence) is accepted as pasted evidence."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from store import Item

MAX_PASTE_CHARS = 20_000
MAX_ITEM_CHARS = 1_500
MAX_TITLE_CHARS = 140
CUT_MARK = " [cut]"

_URL = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
_URL_LINE = re.compile(r"^\s*(?:[-*•]\s*)?https?://", re.IGNORECASE)
_HN_ITEM = re.compile(r"^https?://news\.ycombinator\.com/item\?id=(\d{1,12})$", re.IGNORECASE)
_ISO = re.compile(r"\b(20\d\d-[01]\d-[0-3]\d)\b")
_LONG = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}), (20\d\d)\b")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Pasted:
    items: tuple[Item, ...] = ()
    hn_ids: tuple[str, ...] = ()        # digits only
    not_read: tuple[str, ...] = ()      # bare URLs that were not fetched
    cut_items: int = 0
    truncated_chars: int = 0            # pasted text past MAX_PASTE_CHARS
    topic: str = ""                     # from an envelope, when given


def parse(text: str) -> Pasted:
    envelope = from_envelope(text)
    if envelope is not None:
        return envelope
    body = (text or "")[:MAX_PASTE_CHARS]
    truncated = max(0, len(text or "") - MAX_PASTE_CHARS)
    items: list[Item] = []
    hn_ids: list[str] = []
    not_read: list[str] = []
    cut = 0
    for chunk in chunks(body):
        urls = _URL.findall(chunk)
        words = _URL.sub(" ", chunk).strip(" -*•\n\t")
        if not words:
            for url in urls:
                match = _HN_ITEM.match(url.rstrip(".,"))
                (hn_ids if match else not_read).append(match.group(1) if match else url)
            continue
        is_long = len(chunk) > MAX_ITEM_CHARS
        kept = chunk[:MAX_ITEM_CHARS] + CUT_MARK if is_long else chunk
        cut += int(is_long)
        items.append(Item(source="pasted", title=_title(words), url=urls[0].rstrip(".,") if urls else "",
                          text=kept, date=found_date(chunk)))
    return Pasted(tuple(items), tuple(dict.fromkeys(hn_ids)), tuple(dict.fromkeys(not_read)), cut, truncated)


def chunks(text: str) -> list[str]:
    out: list[list[str]] = []
    for line in (text or "").splitlines():
        if not line.strip():
            out.append([])
            continue
        if _URL_LINE.match(line) and out and out[-1]:
            out.append([])
        if not out:
            out.append([])
        out[-1].append(line.strip())
    return [joined for joined in ("\n".join(block).strip() for block in out) if joined]


def found_date(text: str) -> str:
    iso = _ISO.search(text)
    if iso:
        return iso.group(1)
    long = _LONG.search(text)
    if not long:
        return ""
    try:
        return datetime.strptime(f"{long.group(1)} {long.group(2)} {long.group(3)}", "%b %d %Y").date().isoformat()
    except ValueError:
        return ""


def _title(words: str) -> str:
    first = words.strip().splitlines()[0].strip(" -*•#")
    return first if len(first) <= MAX_TITLE_CHARS else first[:MAX_TITLE_CHARS].rstrip() + "…"


def from_envelope(text: str) -> Pasted | None:
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        return None
    items = tuple(i for i in (_envelope_item(r) for r in raw_items) if i is not None)
    return Pasted(items=items, topic=str(data.get("topic") or "")) if items else None


def _envelope_item(raw: object) -> Item | None:
    if not isinstance(raw, dict) or not str(raw.get("title") or raw.get("label") or "").strip():
        return None
    title = str(raw.get("title") or raw.get("label")).strip()[:MAX_TITLE_CHARS]
    links = [str(x) for x in raw.get("links") or [] if isinstance(x, str)]
    links += [str(e.get("url")) for e in raw.get("evidence") or [] if isinstance(e, dict) and e.get("url")]
    quotes = [str(e.get("quote")) for e in raw.get("evidence") or [] if isinstance(e, dict) and e.get("quote")]
    text = "\n".join([title, str(raw.get("opportunity") or ""), *quotes]).strip()[:MAX_ITEM_CHARS]
    url = next((u for u in links if _URL.fullmatch(u)), "")
    return Item(source="pasted", title=title, url=url, text=text)
