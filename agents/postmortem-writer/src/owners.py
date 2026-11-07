"""Pure: who owns an action item and when it is due, decided in code from the quoted log line.

Owner: first person ("I'll ...") = that line's speaker; else a speaker of the log, or a name written within
NEAR_WINDOW_CHARS of the quote; "we", "the team" and anything else = TBD. Due: kept verbatim only when
written near the quote, never converted to a date; else TBD."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import grounding as g
from records import TBD
from textclean import clean_text

GENERIC_OWNERS = frozenset({"we", "us", "our", "team", "the team", "everyone", "everybody", "all", "they", "them",
                            "someone", "somebody", "anyone", "i", "me", "you", "tbd", "tbc", "n/a", "na", "none",
                            "null", "unknown", "unassigned", "not stated", "nobody", "oncall", "on-call"})
_FIRST_PERSON = re.compile(r"\b(?:i'll|i will|i can|i'm going to|i am going to|let me|i'll take)\b")


def _clean(value: Any, view) -> str:
    return clean_text(value, view.text)


def owner_for(said: Any, snippet: str, view, span: tuple[int, int], speakers: Sequence[str]) -> str:
    """First person ("I'll ...") = the line's speaker; else a speaker of the log or a name written near the
    quote; "we", "the team" and anything else = TBD."""
    line = view.line_at(span[0])
    if _FIRST_PERSON.search(g.normalise(snippet)) and line.speaker:
        return line.speaker.lstrip("@")
    owner = _clean(said, view)
    if not owner or owner.casefold().strip(" .") in GENERIC_OWNERS:
        return TBD
    known = {s.casefold() for s in speakers}
    return owner if owner.casefold() in known or g.near(owner, view.norm, span) else TBD


def due_for(said: Any, view, span: tuple[int, int]) -> str:
    due = _clean(said, view)
    if not due or due.casefold() in ("tbd", "n/a", "not stated", "unknown"):
        return TBD
    return due if g.near(due, view.norm, span) else TBD
