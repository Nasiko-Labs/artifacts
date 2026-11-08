"""Pure: checked chat posts (or a pasted status update) -> report items for the Reality Check.

`reported` is re-derived in code from the verbatim status words; the model's category is used only when no
word matches. When several posts concern the same item, the most recent post is the report item and the
others that disagree on the category are listed as conflicting reports (never a disagreement).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import statuses as st
from checks import snippet
from linking import tokens
from records import Post, PostItem

HEADLINE_ITEMS = 3
_PRIORITY = re.compile(r"\b(?:critical|priority)\b", re.IGNORECASE)
_HINTS = frozenset({st.DONE, st.ON_TRACK, st.AT_RISK, st.BLOCKED, st.NOT_STARTED})


def reported(item: PostItem) -> str:
    by_words = st.reported_category(item.reported_words)
    if by_words != st.UNKNOWN:
        return by_words
    return item.hint if item.hint in _HINTS else st.UNKNOWN


def from_posts(posts: Sequence[Post]) -> tuple[list[dict], list[dict]]:
    """(report items, conflicting reports). Posts are in time order, oldest first."""
    raw = []
    for post in posts:
        for n, item in enumerate(post.items):
            raw.append(_entry(item, snippet(post.quote), n < HEADLINE_ITEMS or bool(_PRIORITY.search(post.quote)),
                              author=post.author, at=post.at.isoformat(), day=post.at.date().isoformat(),
                              permalink=post.permalink, channel=post.channel, source="chat"))
    return _latest_per_item(raw)


def from_paste(found: Sequence[PostItem], paste: str) -> tuple[list[dict], list[dict]]:
    raw = [_entry(item, snippet(_line_with(paste, item.reported_words)), n < HEADLINE_ITEMS, source="paste")
           for n, item in enumerate(found)]
    return _latest_per_item(raw)


def _entry(item: PostItem, quote: str, headline: bool, **where) -> dict:
    return {"item": item.item, "reported": reported(item), "reported_words": item.reported_words,
            "refs": [item.ref] if item.ref else [], "snippet": quote, "owner": item.owner, "headline": headline,
            "author": where.get("author") or "", "at": where.get("at") or "", "date": where.get("day") or "",
            "permalink": where.get("permalink") or "", "channel": where.get("channel") or "",
            "source": where.get("source") or ""}


def _key(entry: dict) -> str:
    if entry["refs"]:
        return "ref:" + entry["refs"][0].casefold()
    return "item:" + " ".join(sorted(tokens(entry["item"])))


def _latest_per_item(raw: list[dict]) -> tuple[list[dict], list[dict]]:
    groups: dict[str, list[dict]] = {}
    for entry in raw:
        groups = {**groups, _key(entry): [*groups.get(_key(entry), []), entry]}
    chosen, conflicts = [], []
    for entries in groups.values():
        latest = max(entries, key=lambda e: e["at"])  # stable: equal times keep the first
        chosen.append(latest)
        conflicts += [{"item": e["item"], "reported": e["reported_words"], "author": e["author"], "date": e["date"],
                       "permalink": e["permalink"], "latest_reported": latest["reported_words"]}
                      for e in entries if e is not latest and e["reported"] != latest["reported"]]
    ordered = sorted(chosen, key=raw.index)
    return [{**e, "id": f"r{n}"} for n, e in enumerate(ordered, start=1)], conflicts


def _line_with(text: str, words: str) -> str:
    needle = words.casefold()
    return next((line.strip() for line in text.splitlines() if needle in line.casefold()), words)
