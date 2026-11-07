"""The editor's answer, checked: only sourced items, at most five, and a pick from among them.

Pure functions, no CrewAI import. The editor is asked for JSON; anything it returns is parsed
tolerantly and then held to the rules here, so a model slip cannot put an unsourced headline
or a pick outside the list in front of the seller.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlparse

MAX_ITEMS = 5
_HTTP_SCHEMES = frozenset({"http", "https"})
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

NOTHING_FOUND_TEXT = (
    "I found no sourced items from the last seven days on that topic. I won't list headlines "
    "without a link I can point you to."
)


@dataclass(frozen=True)
class BriefItem:
    headline: str
    source: str
    date: str
    url: str


@dataclass(frozen=True)
class Briefing:
    items: tuple[BriefItem, ...]
    key_index: int | None  # zero-based index into items
    key_reason: str

    @property
    def key_item(self) -> BriefItem | None:
        if self.key_index is None or not 0 <= self.key_index < len(self.items):
            return None
        return self.items[self.key_index]


def has_http_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme.lower() in _HTTP_SCHEMES and bool(parsed.netloc)


def appears_in(url: str, evidence: str) -> bool:
    """The URL (ignoring a trailing slash) occurs verbatim in what the tools returned."""
    core = url.strip().rstrip("/")
    return bool(core) and core in evidence


def post_check(briefing: Briefing, evidence: str) -> Briefing:
    """Keep items with an http(s) URL that a tool actually returned in this run, the first five
    of them, and the pick only if it survived. No evidence means nothing is kept: an item the
    model wrote without a tool result is an answer from memory."""
    key_item = briefing.key_item
    sourced = (i for i in briefing.items if has_http_url(i.url) and appears_in(i.url, evidence))
    kept = tuple(sourced)[:MAX_ITEMS]
    key_index = next((i for i, item in enumerate(kept) if item is key_item), None)
    return replace(briefing, items=kept, key_index=key_index)


def parse_editor_output(text: str) -> Briefing:
    """Parse the editor's JSON. Anything unparseable yields an empty briefing, never a guess."""
    data = _load_json(text)
    raw_items = data.get("items") if isinstance(data, dict) else None
    items = tuple(_item(raw) for raw in raw_items if isinstance(raw, dict)) if isinstance(raw_items, list) else ()
    key = data.get("key_item") if isinstance(data, dict) else None
    key_index = key - 1 if isinstance(key, int) and not isinstance(key, bool) and key >= 1 else None
    reason = str(data.get("key_reason") or "").strip() if isinstance(data, dict) else ""
    return Briefing(items=items, key_index=key_index, key_reason=reason)


def format_briefing(briefing: Briefing) -> str:
    if not briefing.items:
        return NOTHING_FOUND_TEXT
    lines = ["Market pulse: the last seven days.", ""]
    for number, item in enumerate(briefing.items, start=1):
        lines.append(f"{number}. {item.headline} — {item.source}, {item.date}")
        lines.append(f"   {item.url}")
    lines.append("")
    key_item = briefing.key_item
    if key_item is None:
        lines.append("No single item was picked: the editor's choice had no source I could keep.")
    else:
        lines.append(f"The one that matters: #{briefing.key_index + 1} {key_item.headline}")
        if briefing.key_reason:
            lines.append(briefing.key_reason)
    return "\n".join(lines)


def _item(raw: dict[str, Any]) -> BriefItem:
    return BriefItem(
        headline=_text(raw.get("headline")),
        source=_text(raw.get("source")),
        date=_text(raw.get("date")),
        url=_text(raw.get("url")),
    )


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _load_json(text: str) -> Any:
    fenced = _FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(candidate[start:end + 1])
    except ValueError:
        return None
