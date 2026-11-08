"""Pure: matching at-risk tickets to tracker issues by token overlap, and the search keywords for each
ticket (taken from the ticket only). A wrong bug in a customer reply is worse than none, so the bar is high."""

from __future__ import annotations

import re
from collections.abc import Sequence

from records import BugMatch, Issue, Ticket

MIN_SCORE = 0.4
MIN_SHARED = 2
MAX_KEYWORDS = 5
CLOSED_STATES = ("done", "closed", "completed", "complete", "canceled", "cancelled", "resolved", "released",
                 "fixed", "shipped", "duplicate", "won't fix", "wontfix")
STARTED_STATES = ("in progress", "started", "in review", "doing", "in development", "review", "fixing")
STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "cannot", "could", "did", "do", "does",
    "doing", "for", "from", "get", "gets", "got", "had", "has", "have", "having", "he", "her", "here", "him", "his",
    "how", "i", "if", "in", "into", "is", "it", "its", "just", "me", "more", "most", "my", "no", "not", "now", "of",
    "on", "once", "only", "or", "other", "our", "out", "over", "own", "same", "she", "should", "so", "some", "such",
    "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "those", "through", "to",
    "too", "under", "until", "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who",
    "why", "will", "with", "would", "you", "your", "yours", "hi", "hello", "hey", "please", "thanks", "thank",
    "help", "issue", "issues", "problem", "problems", "ticket", "tickets", "still", "again", "error", "errors",
    "bug", "bugs", "working", "work", "works", "broken", "anyone", "any", "all", "also", "after", "before",
})
_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in _TOKEN.findall((text or "").casefold()) if len(t) >= 3 and t not in STOP_WORDS)


def _first_customer_text(ticket: Ticket) -> str:
    return next((m.text for m in ticket.comments if m.author_role == "customer"), "")


def keywords(ticket: Ticket) -> tuple[str, ...]:
    """At most five distinctive words from the ticket's subject, in their order."""
    words = [t for t in _TOKEN.findall((ticket.subject or "").casefold()) if len(t) >= 3 and t not in STOP_WORDS]
    return tuple(dict.fromkeys(words))[:MAX_KEYWORDS]


def is_closed(state: str) -> bool:
    return (state or "").casefold() in CLOSED_STATES


def is_started(state: str) -> bool:
    return (state or "").casefold() in STARTED_STATES


def score(ticket: Ticket, issue: Issue) -> tuple[float, int]:
    title = tokens(issue.title)
    if not title:
        return 0.0, 0
    shared = tokens(f"{ticket.subject} {_first_customer_text(ticket)}") & title
    return round(len(shared) / len(title), 2), len(shared)


def match(ticket: Ticket, issues: Sequence[Issue], most: int) -> tuple[BugMatch, ...]:
    scored = []
    for issue in issues:
        value, shared = score(ticket, issue)
        if value >= MIN_SCORE and shared >= MIN_SHARED:
            scored.append((value, issue))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return tuple(BugMatch(ticket_id=ticket.id, issue_id=i.id, title=i.title, url=i.url, state=i.state or "unknown",
                          score=value, closed=is_closed(i.state)) for value, i in scored[:most])
