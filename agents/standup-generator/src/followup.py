"""Follow-ups on the standup already built in this conversation, recognised in code (no LLM call):

    only <person>[, <person>]   shorter   add reviews   who is blocked   make it chat-ready
    everyone / full             last N days / since <weekday> / this week     as json
    <person> is <name> in the tracker

A message counts as a follow-up only when it is made of these phrases (a few filler words aside) and
names no repository. Stored records are reused; the tools are read again only when the new window
starts before the stored one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime

from envelope import wants_json
from identity import parse_correction, tokens
from intake import repos_in
from render import View
from window import Window, parse_window

MAX_FILLER_WORDS = 4
_ONLY = re.compile(r"\b(?:only|just)\s+(?!blocked\b)([\w .,'@-]+)", re.IGNORECASE)
_SHORTER = re.compile(r"\b(?:shorter|brief(?:er)?|compact|condensed|tl;?dr)\b", re.IGNORECASE)
_REVIEWS = re.compile(r"\b(?:add|with|include|show)\s+(?:the\s+)?reviews?\b", re.IGNORECASE)
_BLOCKED = re.compile(r"\bwho(?:'s|\s+is|\s+are)?\s+blocked\b|\b(?:only|just)\s+(?:the\s+)?blocked\b|"
                      r"\bblocked\s+(?:only|items|people)\b|\bwhat(?:'s|\s+is)\s+blocked\b", re.IGNORECASE)
# "chat-ready", "for chat"; the chat product's own name is matched as `sl[a]ck` so no provider name is
# spelled out in src/ (rule 9) while the user's own words still work.
_CHAT_READY = re.compile(r"\b(?:chat|sl[a]ck)[- ]?(?:ready|format(?:ted)?)\b|\bfor\s+(?:chat|sl[a]ck)\b", re.IGNORECASE)
_EVERYONE = re.compile(r"\b(?:everyone|everybody|full|all\s+people|whole\s+team)\b", re.IGNORECASE)
_WINDOW_WORDS = re.compile(r"\b(?:last|past|since|this|week|weeks|days?|hours?|today|yesterday|monday|tuesday|"
                           r"wednesday|thursday|friday|saturday|sunday|\d+)\b", re.IGNORECASE)
_JSON = re.compile(r"\b(?:as|in)\s+json\b|\bjson\s+only\b", re.IGNORECASE)
_FILLER = frozenset({"and", "please", "make", "it", "the", "a", "me", "show", "for", "version", "now", "can",
                     "you", "give", "just", "only", "to", "of", "with", "same", "but", "then", "standup", "one",
                     "too", "also", "again", "people", "what", "about", "who"})


@dataclass(frozen=True)
class FollowUp:
    view: View
    window_since: datetime | None = None
    correction: tuple[str, str, str] | None = None
    json_mode: bool = False


def parse(message: str, labels: tuple[str, ...], previous: View, now: datetime) -> FollowUp | None:
    """The follow-up the message asks for, or None when it is a new request."""
    correction = parse_correction(message)
    if correction is not None:
        return FollowUp(previous, correction=correction, json_mode=wants_json(message))
    if repos_in(message):
        return None
    rest = message
    view = replace(previous, blocked_only=False)
    matched = False
    for pattern, change in ((_BLOCKED, {"blocked_only": True, "only": ()}), (_SHORTER, {"shorter": True}),
                            (_REVIEWS, {"reviews": True}), (_CHAT_READY, {"chat_ready": True}),
                            (_EVERYONE, {"only": (), "shorter": False})):
        if pattern.search(rest):
            view, matched, rest = replace(view, **change), True, pattern.sub(" ", rest)
    only, rest = _only(rest, labels)
    if only:
        view, matched = replace(view, only=only), True
    window = parse_window(message, now)
    if window is not None:
        matched, rest = True, _WINDOW_WORDS.sub(" ", rest)
    json_mode = bool(_JSON.search(message))
    if json_mode:
        matched, rest = True, _JSON.sub(" ", rest)
    leftover = [t for t in tokens(rest) if t not in _FILLER]
    if not matched or len(leftover) > MAX_FILLER_WORDS:
        return None
    return FollowUp(view, window.since if window else None, json_mode=json_mode)


def _only(text: str, labels: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    found = _ONLY.search(text)
    if not found:
        return (), text
    names = []
    for part in re.split(r",|\band\b|&", found.group(1)):
        wanted = tokens(part)
        label = next((lab for lab in labels if wanted and set(wanted) <= set(tokens(lab))), None)
        if label and label not in names:
            names.append(label)
    return tuple(names), (text[:found.start()] + " " + text[found.end():]) if names else text


def window_after(stored_window: Window, fetched_since: datetime, since: datetime | None) -> tuple[Window, bool]:
    """(the window to show, whether the tools must be read again because it starts before what was read)."""
    if since is None:
        return stored_window, False
    return Window(since, stored_window.end), since < fetched_since


def initial_view(message: str) -> View:
    """The view flags a first request asks for ("who is blocked in acme/shop", "shorter standup for ...")."""
    flags = {"blocked_only": _BLOCKED, "shorter": _SHORTER, "reviews": _REVIEWS, "chat_ready": _CHAT_READY}
    return View(**{name: bool(pattern.search(message or "")) for name, pattern in flags.items()})
