"""Pure: a step's JSON answer turned into checked records.

The model only transcribes. A record is kept only when its key, link, title, quote and permalink appear
verbatim in THIS step's tool output, its dates parse and lie in range, and it is not a duplicate. Chat
posts must come from a kept (status-like, non-direct) channel; items in a post must quote the post.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import channels as channels_
from clean import MAX_QUOTE_CHARS
from dates import Window, parse_day, parse_time
from records import STATUS_TYPES, Blocker, Channel, Event, Issue, Post, PostItem

MAX_ISSUES_PER_TEAM = 150
MAX_TEAMS = 8
MAX_POSTS = 200
MAX_ITEMS_PER_POST = 12
MAX_ITEM_WORDS = 12
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True)
class Seen:
    """This step's raw tool output, the only source a record may come from."""

    text: str

    def has(self, value: object) -> bool:
        return isinstance(value, str) and bool(value.strip()) and value.strip() in self.text


def parse_answer(text: str) -> dict | None:
    """The JSON object in a model answer (code fences and surrounding prose tolerated), or None."""
    body = _FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def items(answer: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    raw = answer.get(key)
    return [i for i in raw if isinstance(i, Mapping)] if isinstance(raw, list) else []


def strings(answer: Mapping[str, Any], key: str, seen: Seen) -> tuple[str, ...]:
    raw = answer.get(key)
    values = [v.strip() for v in raw if isinstance(v, str) and seen.has(v)] if isinstance(raw, list) else []
    return tuple(dict.fromkeys(values))


# --- tracker ----------------------------------------------------------------------------------------------

def check_issues(answer: Mapping[str, Any], seen: Seen,
                 teams: Sequence[str] = ()) -> tuple[tuple[Issue, ...], tuple[str, ...]]:
    """(issues, teams whose 150-issue cap was hit). With `teams`, only those teams' issues are kept."""
    wanted = {t.casefold() for t in teams}
    kept: dict[str, Issue] = {}
    per_team: dict[str, int] = {}
    capped: list[str] = []
    for raw in items(answer, "issues"):
        issue = _issue(raw, seen)
        if issue is None or issue.key in kept or (wanted and issue.team.casefold() not in wanted):
            continue
        count = per_team.get(issue.team, 0)
        if count >= MAX_ISSUES_PER_TEAM:
            capped = capped if issue.team in capped else [*capped, issue.team]
            continue
        per_team = {**per_team, issue.team: count + 1}
        kept = {**kept, issue.key: issue}
    return tuple(kept.values()), tuple(capped)


def _issue(raw: Mapping[str, Any], seen: Seen) -> Issue | None:
    key, url, title, team = raw.get("key"), raw.get("url"), raw.get("title"), raw.get("team")
    if not (seen.has(key) and seen.has(title) and seen.has(team)):
        return None
    if url not in (None, "") and not seen.has(url):
        return None  # a link the tools never returned
    status_type = str(raw.get("status_type") or "").casefold() or None
    status_type = status_type if status_type in STATUS_TYPES else None
    # Completed outside the window is kept: it is not "shipped" this week, but an epic's done children count.
    completed = parse_day(raw.get("completed_at")) if status_type == "completed" else None
    updated_raw = raw.get("updated_at")
    updated = parse_day(updated_raw)
    return Issue(key=key.strip(), url=(url or "").strip(), title=title.strip(), team=team.strip(),
                 status=_seen_str(raw.get("status"), seen), status_type=status_type,
                 assignee=_seen_str(raw.get("assignee"), seen) or None, created_at=parse_day(raw.get("created_at")),
                 updated_at=updated, completed_at=completed, due=parse_day(raw.get("due")),
                 labels=tuple(v.strip() for v in _list(raw.get("labels")) if seen.has(v)),
                 parent_key=_seen_str(raw.get("parent_key"), seen) or None,
                 blocked_by=tuple(_blockers(raw, seen)), last_comment_at=parse_day(raw.get("last_comment_at")),
                 updated_unparsed=updated is None and bool(updated_raw))


def _blockers(raw: Mapping[str, Any], seen: Seen) -> list[Blocker]:
    out = []
    for b in _list(raw.get("blocked_by")):
        if isinstance(b, Mapping) and seen.has(b.get("key")):
            kind = str(b.get("status_type") or "").casefold()
            out.append(Blocker(b["key"].strip(), kind if kind in STATUS_TYPES else None))
    return out


def visible_teams(answer: Mapping[str, Any], seen: Seen, issues: Sequence[Issue]) -> tuple[str, ...]:
    """The teams the tracker showed, alphabetically: listed ones plus any team an issue belongs to."""
    names = {t.casefold(): t for t in (*strings(answer, "teams", seen), *(i.team for i in issues))}
    return tuple(sorted(names.values(), key=str.casefold))


# --- chat -------------------------------------------------------------------------------------------------

def check_channels(answer: Mapping[str, Any], seen: Seen, teams: Sequence[str],
                   named: frozenset[str]) -> tuple[Channel, ...]:
    return channels_.keep(items(answer, "channels"), seen.has, teams, named)


def check_posts(answer: Mapping[str, Any], seen: Seen, window: Window, kept: Sequence[Channel]) -> tuple[Post, ...]:
    by_name = {c.name: c for c in kept}
    by_id = {c.id: c for c in kept if c.id}
    out: dict[str, Post] = {}
    for raw in items(answer, "posts"):
        channel = by_name.get(channels_.bare(str(raw.get("channel") or ""))) or by_id.get(str(raw.get("channel_id") or ""))
        if channel is None or channels_.is_direct(str(raw.get("channel_id") or channel.id),
                                                  raw.get("channel_type") or "public"):
            continue  # a direct or group conversation, by id prefix or by the type the tool reported
        post = _post(raw, seen, window, channel)
        if post is not None:
            out.setdefault(post.permalink, post)
    return tuple(sorted(out.values(), key=lambda p: p.at))[-MAX_POSTS:]


def _post(raw: Mapping[str, Any], seen: Seen, window: Window, channel: Channel) -> Post | None:
    permalink, quote, author, ts = raw.get("permalink"), raw.get("text_quote"), raw.get("author"), raw.get("ts")
    at = parse_time(ts)
    if not (seen.has(permalink) and seen.has(quote) and seen.has(author)) or at is None:
        return None
    if not window.in_window(at.date()) or not (seen.has(str(ts)) or str(int(at.timestamp())) in seen.text):
        return None
    found = tuple(p for p in (_post_item(i, quote) for i in items(raw, "items")[:MAX_ITEMS_PER_POST]) if p)
    if not found:
        return None
    return Post(channel=channel.name, author=author.strip(), at=at, permalink=permalink.strip(),
                quote=quote.strip(), items=found)


def _post_item(raw: Mapping[str, Any], source: str) -> PostItem | None:
    """An item the source text actually says: name and status words quoted from it (case-insensitive)."""
    item, words = raw.get("item"), raw.get("reported_words")
    if not _in(item, source) or not _in(words, source) or len(item.split()) > MAX_ITEM_WORDS:
        return None
    ref, owner, hint = raw.get("ref"), raw.get("owner"), raw.get("category")
    return PostItem(item=item.strip(), reported_words=words.strip(), ref=ref.strip() if _in(ref, source) else None,
                    owner=owner.strip() if _in(owner, source) else None,
                    hint=hint if isinstance(hint, str) else None)


def check_paste(answer: Mapping[str, Any], paste: str) -> tuple[PostItem, ...]:
    return tuple(p for p in (_post_item(i, paste) for i in items(answer, "items")) if p)


# --- calendar ---------------------------------------------------------------------------------------------

def check_events(answer: Mapping[str, Any], seen: Seen, window: Window) -> tuple[Event, ...]:
    out: dict[str, Event] = {}
    for raw in items(answer, "events"):
        eid, title, url = raw.get("id"), raw.get("title"), raw.get("url")
        start: date | None = parse_day(raw.get("start"))
        if not (seen.has(eid) and seen.has(title)) or not window.in_next_week(start):
            continue
        count = raw.get("attendee_count")
        out.setdefault(eid.strip(), Event(id=eid.strip(), title=title.strip(), url=url.strip() if seen.has(url) else "",
                                          start=start,
                                          attendee_count=count if isinstance(count, int) and not isinstance(count, bool)
                                          else None))
    return tuple(sorted(out.values(), key=lambda e: (e.start, e.title)))


def _in(value: object, source: str) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.strip().casefold() in (source or "").casefold()


def _seen_str(value: object, seen: Seen) -> str:
    return value.strip() if seen.has(value) else ""


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def snippet(quote: str) -> str:
    return quote if len(quote) <= MAX_QUOTE_CHARS else quote[: MAX_QUOTE_CHARS - 1].rstrip() + "…"
