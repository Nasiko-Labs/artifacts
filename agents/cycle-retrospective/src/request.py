"""Pure: the fields read from the user's words in code (cycle, team, post channel, extra evidence
channels, JSON mode), and the envelope input of a previous workflow step (its `summary` only)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_CURRENT = re.compile(r"\b(?:current|this|ongoing|running|active)\s+(?:cycle|sprint)\b", re.IGNORECASE)
_LAST = re.compile(r"\b(?:last|previous|prior|latest|most recent)\s+(?:finished\s+)?(?:cycle|sprint)\b",
                   re.IGNORECASE)
_NUMBER = re.compile(r"\b(?:cycle|sprint)\s*#?\s*(\d{1,4})\b", re.IGNORECASE)
_NAMED = re.compile(r"\b(?:cycle|sprint)\s+[\"“']([^\"”']{1,60})[\"”']", re.IGNORECASE)
_TEAM = (re.compile(r"\bfor\s+(?:the\s+)?([A-Z][\w-]*(?:\s+[A-Z][\w-]*)?)"),
         re.compile(r"\bteam[:\s]+([A-Za-z][\w-]*)", re.IGNORECASE),
         re.compile(r"\b([A-Z][\w-]*)\s+team\b"))
_NOT_TEAM = frozenset({"the", "last", "current", "this", "cycle", "sprint", "retro", "a", "my", "our", "previous"})
_POST = re.compile(r"\b(?:post|share|send|publish)\b[^#\n]{0,40}?#([\w.-]{1,80})", re.IGNORECASE)
_POST_WORD = re.compile(r"\b(?:post|share|publish)\b", re.IGNORECASE)
_EVIDENCE = re.compile(r"\b(?:check|search|look in|read)\b((?:[\s,]*(?:and\s+)?#[\w.-]{1,80})+)", re.IGNORECASE)
_CHANNEL = re.compile(r"#([\w.-]{1,80})")
_JSON = re.compile(r"\b(?:as\s+json|json\s+only|in\s+json)\b", re.IGNORECASE)
_PULL = re.compile(r"\b(?:retro|retrospective|re-?run|refresh|again)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    cycle: str = "last"        # last | current | number:<n> | name:<text>
    explicit_cycle: bool = False
    team: str = ""
    channel: str = ""          # post target, without '#'
    evidence_channels: tuple[str, ...] = ()
    wants_post: bool = False


def from_envelope(text: str) -> str:
    """A previous step's envelope reads as its `summary`; anything else is returned unchanged."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    if not raw.startswith("{"):
        return text
    try:
        data = json.loads(raw)
    except ValueError:
        return text
    summary = data.get("summary") if isinstance(data, dict) else None
    return summary.strip() if isinstance(summary, str) and summary.strip() else text


def parse(text: str) -> Request:
    query = text or ""
    cycle, explicit = _cycle(query)
    post = _POST.search(query)
    evidence = _EVIDENCE.search(query)
    extra = tuple(dict.fromkeys(_CHANNEL.findall(evidence.group(1)))) if evidence else ()
    channel = post.group(1) if post else ""
    return Request(cycle=cycle, explicit_cycle=explicit, team=_team(query), channel=channel,
                   evidence_channels=tuple(c for c in extra if c != channel),
                   wants_post=bool(post or _POST_WORD.search(query)))


def wants_json(text: str) -> bool:
    return bool(_JSON.search(text or ""))


def asks_for_retro(text: str) -> bool:
    """A message that names a cycle, or asks for a (new) retro, starts a fresh pull."""
    return parse(text).explicit_cycle or bool(_PULL.search(text or ""))


def _cycle(query: str) -> tuple[str, bool]:
    if _CURRENT.search(query):
        return "current", True
    number = _NUMBER.search(query)
    if number:
        return f"number:{int(number.group(1))}", True
    named = _NAMED.search(query)
    if named:
        return f"name:{named.group(1).strip()}", True
    return "last", bool(_LAST.search(query))


def _team(query: str) -> str:
    for pattern in _TEAM:
        for match in pattern.finditer(query):
            words = [w for w in match.group(1).split() if w.casefold() not in _NOT_TEAM]
            if words and words[0].casefold() not in _NOT_TEAM:
                return " ".join(words)
    return ""


def describe_cycle(ref: str) -> str:
    if ref == "current":
        return "the current cycle"
    if ref.startswith("number:"):
        return f"cycle {ref.split(':', 1)[1]}"
    if ref.startswith("name:"):
        return f"the cycle named {ref.split(':', 1)[1]}"
    return "the last finished cycle"
