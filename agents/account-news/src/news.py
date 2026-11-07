"""Pure logic: read the model's JSON answer, enforce the output rules in code, render.

The post-check is the guarantee, not the prompt: an item survives only with an http(s) URL that
a tool actually returned this turn, and a known date inside the window."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

from prompts import MAX_ITEMS, WINDOW_DAYS

Status = Literal["ok", "no_search_tool"]
_STATUSES = ("ok", "no_search_tool")
_ISO_DATE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})")
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_URL_IN_TEXT = re.compile(r"""https?://[^\s"'<>\\\]\)]+""")
_URL_TRAILING = ".,;:/"
UNKNOWN_DATE = "date not stated"


@dataclass(frozen=True)
class NewsItem:
    headline: str
    source: str
    date: str | None
    url: str
    renewal_note: str


@dataclass(frozen=True)
class ParsedAnswer:
    status: Status
    items: tuple[NewsItem, ...]


def parse_answer(text: str) -> ParsedAnswer | None:
    """The model's JSON answer, or None when it is not the agreed shape."""
    data = _load_json_object(text)
    if data is None or data.get("status") not in _STATUSES:
        return None
    raw_items = data.get("items") or []
    if not isinstance(raw_items, list):
        return None
    items = tuple(i for i in (_item(raw) for raw in raw_items) if i is not None)
    return ParsedAnswer(status=data["status"], items=items)


def _load_json_object(text: str) -> dict | None:
    body = _CODE_FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _item(raw: Any) -> NewsItem | None:
    if not isinstance(raw, Mapping):
        return None
    return NewsItem(
        headline=_text(raw.get("headline")),
        source=_text(raw.get("source")),
        date=_text(raw.get("date")) or None,
        url=_text(raw.get("url")),
        renewal_note=_text(raw.get("renewal_note")),
    )


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def post_check(
    items: Iterable[NewsItem], *, today: date, seen_text: str, limit: int = MAX_ITEMS
) -> tuple[NewsItem, ...]:
    """Keep items with an http(s) URL some tool returned, not older than the window; cap at `limit`."""
    cutoff = today - timedelta(days=WINDOW_DAYS)
    seen = urls_in(seen_text)
    kept = [
        item for item in items
        if item.headline and _is_http_url(item.url) and _was_seen(item.url, seen) and not _is_stale(item, cutoff)
    ]
    return tuple(kept[:limit])


def _is_http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.netloc) and " " not in url


def urls_in(text: str) -> frozenset[str]:
    """Every http(s) URL in tool output, normalised; JSON-escaped slashes are unescaped first."""
    return frozenset(_normalise_url(m) for m in _URL_IN_TEXT.findall(text.replace("\\/", "/")))


def _normalise_url(url: str) -> str:
    parts = urlsplit(url.strip().rstrip(_URL_TRAILING))
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{query}"


def _was_seen(url: str, seen: frozenset[str]) -> bool:
    """Exact match against a URL a tool returned: a prefix or a truncated URL does not count."""
    return _normalise_url(url) in seen


def _is_stale(item: NewsItem, cutoff: date) -> bool:
    published = _parse_date(item.date)
    return published is not None and published < cutoff


def _parse_date(value: str | None) -> date | None:
    match = _ISO_DATE.match(value or "")
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def render_items(items: Sequence[NewsItem]) -> str:
    """The user-facing list: headline, source, date, URL, the renewal line."""
    return "\n\n".join(_render_one(n, item) for n, item in enumerate(items, start=1))


def _render_one(number: int, item: NewsItem) -> str:
    source = item.source or "source not stated"
    return (
        f"{number}. **{item.headline}** ({source}, {item.date or UNKNOWN_DATE})\n"
        f"   {item.url}\n"
        f"   Renewal: {item.renewal_note or 'no note'}"
    )


def items_to_state(items: Sequence[NewsItem]) -> list[dict]:
    return [asdict(item) for item in items]


def items_from_state(raw: Sequence[Mapping[str, Any]] | None) -> tuple[NewsItem, ...]:
    return tuple(NewsItem(**{k: item.get(k) for k in NewsItem.__dataclass_fields__}) for item in raw or ())
