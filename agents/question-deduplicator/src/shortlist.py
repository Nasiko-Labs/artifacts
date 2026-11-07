"""Pure: search hits merged by thread root and scored in code, top 8 kept.

score = distinct queries that found the thread x 2 + Jaccard(question terms, root text terms) x 5
        + 1 if the thread has a reply + 1 if it has a positive reaction.
A thread the tool says has no replies is left out (no answer can be in it); an unknown reply count is
kept until the thread is read. Ties break on newer first, then the thread key, so the order is fixed."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from models import Hit, Message, Thread
from terms import jaccard, terms

SHORTLIST = 8
QUERY_WEIGHT = 2.0
OVERLAP_WEIGHT = 5.0
POSITIVE_REACTIONS = frozenset({"+1", "thumbsup", "white_check_mark", "heavy_check_mark", "ballot_box_with_check",
                                "pray", "tada", "raised_hands", "100", "heart", "clap", "ok_hand", "bow"})


def group(hits: Iterable[Hit]) -> tuple[Thread, ...]:
    by_key: dict[str, list[Hit]] = {}
    for hit in hits:
        by_key.setdefault(hit.message.key, []).append(hit)
    return tuple(_thread(key, found) for key, found in by_key.items())


def _thread(key: str, hits: list[Hit]) -> Thread:
    first = hits[0].message
    root = next((h.message for h in hits if h.message.ts == first.root_ts), None)
    replies = tuple(sorted((h.message for h in hits if h.message.ts != first.root_ts), key=lambda m: m.time or 0.0))
    counts = [h.message.reply_count for h in hits if h.message.reply_count is not None]
    queries = frozenset().union(*(h.queries for h in hits))
    return Thread(key=key, channel_id=first.channel_id, channel_label=first.channel_label, root_ts=first.root_ts,
                  root=root, replies=replies, queries=queries, reply_count=max(counts) if counts else None)


def has_positive_reaction(messages: Iterable[Message]) -> bool:
    return any(r.split("::")[0] in POSITIVE_REACTIONS for m in messages for r in m.reactions)


def lead_text(thread: Thread) -> str:
    """The root's text, else the earliest message found."""
    if thread.root is not None:
        return thread.root.text
    return thread.replies[0].text if thread.replies else ""


def score(thread: Thread, question_terms: frozenset[str]) -> float:
    overlap = jaccard(question_terms, terms(lead_text(thread)))
    return (len(thread.queries) * QUERY_WEIGHT + overlap * OVERLAP_WEIGHT + (1 if thread.has_replies else 0)
            + (1 if has_positive_reaction(thread.messages) else 0))


def shortlist(threads: Iterable[Thread], question_terms: frozenset[str], limit: int = SHORTLIST) -> tuple[Thread, ...]:
    candidates = [t for t in threads if t.reply_count != 0 or t.replies]
    scored = [replace(t, score=round(score(t, question_terms), 4)) for t in candidates]
    ordered = sorted(scored, key=lambda t: (-t.score, -_time(t), t.key))
    return tuple(ordered[:limit])


def _time(thread: Thread) -> float:
    return max((m.time or 0.0 for m in thread.messages), default=0.0)
