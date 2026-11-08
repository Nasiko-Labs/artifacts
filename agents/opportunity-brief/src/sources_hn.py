"""Hacker News, read by agent code from ONE constant host. The user's words only ever become a URL-encoded
query parameter; item ids are digits only. https, no redirects, 15 s timeout, 1 MB response cap, at most 2
requests per turn (deps.Budget). Unreachable = a coverage line, never a failed turn.

This module is the only place the host appears (a test enforces it)."""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import timedelta

import httpx

from deps import BudgetSpent, TurnDeps
from nasiko_trace import suppressed_http_instrumentation
from store import Item

log = logging.getLogger("opportunity_brief.hn")

HN_HOST = "hn.algolia.com"
HN_BASE = f"https://{HN_HOST}/api/v1"
HN_ITEM_PAGE = "https://news.ycombinator.com/item?id={}"
TIMEOUT_SECS = 15.0
MAX_BYTES = 1_000_000
HITS_PER_PAGE = 25
MAX_TEXT_CHARS = 600
TOP_COMMENTS = 5
_DIGITS = re.compile(r"^\d{1,12}$")
_TAG = re.compile(r"<[^>]+>")
UNREACHABLE = "Hacker News wasn't reachable from this agent, so the brief uses the other sources."


class HnError(RuntimeError):
    pass


@dataclass(frozen=True)
class HnResult:
    items: tuple[Item, ...] = ()
    notes: tuple[str, ...] = ()
    read: bool = False


def search_url(by_date: bool) -> str:
    return f"{HN_BASE}/{'search_by_date' if by_date else 'search'}"


async def collect(deps: TurnDeps, topic: str | None, window_days: int, hn_ids: tuple[str, ...]) -> HnResult:
    """Relevance search first (when there is a topic), then a pasted item link, else the by-date search."""
    plan: list[tuple[str, str]] = [("search", "")] if topic else []
    plan += [("item", i) for i in hn_ids]
    if topic and not hn_ids:
        plan.append(("by_date", ""))
    items: list[Item] = []
    read = False
    skipped = 0
    for kind, value in plan:
        if deps.budget.left("hn") <= 0:
            skipped += int(kind == "item")
            continue
        try:
            got = await (_item(deps, value) if kind == "item" else _search(deps, topic or "", window_days,
                                                                            kind == "by_date"))
        except HnError as e:
            log.warning("hacker news %s failed: %s", kind, e)
            return HnResult(tuple(items), (UNREACHABLE,), read)
        except BudgetSpent:
            continue
        read = True
        items.extend(got)
    notes = (f"{skipped} pasted Hacker News link(s) not read: the per-turn request budget was spent.",) if skipped else ()
    return HnResult(tuple(items), notes, read)


async def _search(deps: TurnDeps, topic: str, window_days: int, by_date: bool) -> list[Item]:
    start = int((deps.now - timedelta(days=window_days)).timestamp())
    params = {"query": topic, "tags": "story", "numericFilters": f"created_at_i>{start}",
              "hitsPerPage": str(HITS_PER_PAGE)}
    body = await _get(deps, search_url(by_date), params)
    hits = body.get("hits") if isinstance(body, dict) else None
    return [i for i in (_from_hit(h) for h in hits or []) if i is not None]


async def _item(deps: TurnDeps, item_id: str) -> list[Item]:
    if not _DIGITS.match(item_id or ""):
        return []
    body = await _get(deps, f"{HN_BASE}/items/{item_id}", None)
    if not isinstance(body, dict) or not body.get("title"):
        return []
    children = [c for c in body.get("children") or [] if isinstance(c, dict) and c.get("text")]
    comments = [_plain(c["text"])[:MAX_TEXT_CHARS] for c in children[:TOP_COMMENTS]]
    text = "\n".join([str(body["title"]), *comments])
    return [Item(source="hn", title=str(body["title"]), url=HN_ITEM_PAGE.format(item_id), text=text,
                 date=str(body.get("created_at") or ""), points=_int(body.get("points")),
                 comments=len(body.get("children") or []))]


async def _get(deps: TurnDeps, url: str, params: dict | None) -> object:
    deps.budget.take("hn")
    request = deps.hn_http.build_request("GET", url, params=params, timeout=TIMEOUT_SECS)
    if request.url.host != HN_HOST or request.url.scheme != "https":
        raise HnError("refused a request off the fixed host")
    try:
        with suppressed_http_instrumentation():
            response = await deps.hn_http.send(request, stream=True, follow_redirects=False)
            try:
                if response.status_code != 200:
                    raise HnError(f"status {response.status_code}")
                raw = await _read_capped(response)
            finally:
                await response.aclose()
    except httpx.HTTPError as e:
        raise HnError(type(e).__name__) from e
    try:
        return json.loads(raw)
    except ValueError as e:
        raise HnError("not JSON") from e


async def _read_capped(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_BYTES:
            raise HnError("response over the size cap")
        chunks.append(chunk)
    return b"".join(chunks)


def _from_hit(hit: object) -> Item | None:
    if not isinstance(hit, dict) or not hit.get("title") or not _DIGITS.match(str(hit.get("objectID") or "")):
        return None
    story = _plain(str(hit.get("story_text") or ""))[:MAX_TEXT_CHARS]
    return Item(source="hn", title=str(hit["title"]), url=HN_ITEM_PAGE.format(hit["objectID"]),
                text="\n".join(t for t in (str(hit["title"]), story) if t), date=str(hit.get("created_at") or ""),
                points=_int(hit.get("points")), comments=_int(hit.get("num_comments")))


def _plain(text: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", text)).split())


def _int(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0

