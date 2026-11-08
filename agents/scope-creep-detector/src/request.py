"""Pure: what the user asked for, parsed in code. Team words, sprint selector, grace override, JSON mode,
or the same fields from a previous workflow step's envelope."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from scope import MAX_GRACE_HOURS

_NOT_TEAMS = frozenset({
    "this", "the", "our", "my", "your", "their", "current", "active", "last", "previous", "next", "a", "that",
    "same", "whole", "entire", "which", "what", "each", "every", "new", "old", "running", "ongoing", "prior",
    "in", "for", "to", "of", "with", "from", "on", "at", "about", "since", "during", "and", "or", "into", "per",
    "planning", "scope", "creep", "added", "upcoming", "two", "week", "weekly", "s",
})
_TEAM = re.compile(r"\b([A-Za-z][\w-]*)\s+(?:team|squad|sprint|cycle)s?\b", re.IGNORECASE)
_TEAM_AFTER = re.compile(r"\bteam\s+([A-Z][\w-]*)")
_LAST = re.compile(r"\b(?:last|previous|prior)\s+(?:sprint|cycle|iteration)\b", re.IGNORECASE)
_NUMBER = re.compile(r"\b(?:sprint|cycle|iteration)\s*#?\s*(\d{1,4})\b", re.IGNORECASE)
_NO_GRACE = re.compile(r"\b(?:no|without|zero|skip the)\s+grace\b|\bgrace\s*(?:period\s*)?(?:of\s*)?0\b|"
                       r"\bignore the first day\b|\bcount the first day\b", re.IGNORECASE)
_GRACE = re.compile(r"\bgrace(?:\s+period)?(?:\s+of)?\s+(\d{1,3})\s*(days?|d|hours?|hrs?|h)\b", re.IGNORECASE)
_JSON = re.compile(r"\bas json\b|\bjson only\b", re.IGNORECASE)
_DATE_LIKE = re.compile(r"\b\d{4}-\d{2}-\d{2}")
PASTE_MIN_CHARS = 200
PASTE_MIN_DATES = 3


@dataclass(frozen=True)
class ScopeRequest:
    text: str
    team: str | None = None
    selector: str = "current"  # current | last | a sprint number
    grace_hours: int | None = None  # None = the default
    as_json: bool = False
    from_envelope: bool = False

    @property
    def looks_pasted(self) -> bool:
        """A pasted issue list: long, several ISO dates, several lines."""
        return (len(self.text) >= PASTE_MIN_CHARS and len(_DATE_LIKE.findall(self.text)) >= PASTE_MIN_DATES
                and self.text.count("\n") >= PASTE_MIN_DATES)


def parse(text: str, as_json: bool = False) -> ScopeRequest:
    envelope = _envelope(text)
    if envelope is not None:
        return envelope
    return ScopeRequest(text=text, team=team_in(text), selector=selector_in(text), grace_hours=grace_in(text),
                        as_json=as_json or bool(_JSON.search(text)))


def team_in(text: str) -> str | None:
    for match in _TEAM.finditer(text):
        word = match.group(1)
        if word.casefold() not in _NOT_TEAMS and not word.isdigit():
            return word
    after = _TEAM_AFTER.search(text)
    return after.group(1) if after else None


def selector_in(text: str) -> str:
    if _LAST.search(text):
        return "last"
    number = _NUMBER.search(text)
    return number.group(1) if number else "current"


def grace_in(text: str) -> int | None:
    if _NO_GRACE.search(text):
        return 0
    match = _GRACE.search(text)
    if not match:
        return None
    value = int(match.group(1)) * (24 if match.group(2).casefold().startswith("d") else 1)
    return min(value, MAX_GRACE_HOURS)


def _envelope(text: str) -> ScopeRequest | None:
    """A previous step's JSON (bare or fenced): reads `team` and `sprint` (a name/number or an object)."""
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or not ({"team", "sprint"} & data.keys()):
        return None
    sprint = data.get("sprint")
    name = sprint.get("name") if isinstance(sprint, dict) else sprint
    team = data.get("team") or (sprint.get("team") if isinstance(sprint, dict) else None)
    selector = selector_in(f"sprint {name}") if isinstance(name, (str, int)) and str(name).strip() else "current"
    if isinstance(name, str) and _LAST.search(name):
        selector = "last"
    return ScopeRequest(text=text, team=str(team) if isinstance(team, str) and team.strip() else None,
                        selector=selector, from_envelope=True)
