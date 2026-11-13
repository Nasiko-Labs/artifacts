"""Pure: follow-up commands on the memo already in this conversation.

    "as json", "just the recommendation", "shorter"          -> re-render, zero calls
    "recommend B instead"                                    -> write call with the user's override
    "add option C: ..."                                      -> analyse + score + write again
    "add data: ..."                                          -> write again with the larger grounding set
    "change the weights: cost 60%"                           -> code re-scores, one write call
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import criteria as crit

FOLLOWUP_MAX_CHARS = 300

_JSON = re.compile(r"^(?:(?:show|give|return|send)\s+(?:me\s+)?(?:it|this|the memo)?\s*)?(?:as|in)\s+json[.!]?$|"
                   r"^json(?:\s+only)?[.!]?$", re.IGNORECASE)
_JUST_REC = re.compile(r"^(?:(?:show|give)\s+(?:me\s+)?)?(?:just|only)\s+the\s+recommendation[.!]?$|"
                       r"^recommendation\s+only[.!]?$", re.IGNORECASE)
_SHORTER = re.compile(r"^(?:(?:please|can you|could you)\s+)?(?:make\s+(?:it|this|the memo)\s+)?"
                      r"(?:shorter|more concise|tighter|briefer)|^(?:trim|shorten|cut)\s+(?:it|this|the memo)",
                      re.IGNORECASE)
_OVERRIDE = re.compile(r"^(?:please\s+)?(?:recommend|go with|pick|choose)\s+(?P<what>.+?)\s+instead[.!]?$|"
                       r"^switch\s+(?:the\s+)?recommendation\s+to\s+(?P<what2>.+?)[.!]?$", re.IGNORECASE)
_ADD_OPTION = re.compile(r"^add\s+(?:an?(?:other)?\s+)?option(?:\s+[A-Ta-t])?\s*[:\-]\s*(?P<what>.+)$",
                         re.IGNORECASE | re.DOTALL)
_ADD_DATA = re.compile(r"^add\s+(?:this\s+)?(?:data|facts?|context|info(?:rmation)?)\s*[:\-]\s*(?P<what>.+)$",
                       re.IGNORECASE | re.DOTALL)
_WEIGHTS = re.compile(r"^(?:(?:change|set|update|adjust)\s+(?:the\s+)?weights?(?:\s+to)?|weights?)\s*[:\-]?\s*"
                      r"(?P<what>.+)$", re.IGNORECASE | re.DOTALL)
_PCT_ITEM = re.compile(r"(?P<name>[A-Za-z][\w /&-]*?)\s*(?:to|=|:)?\s*(?P<pct>\d+(?:\.\d+)?)\s*%")


@dataclass(frozen=True)
class Command:
    kind: str           # json | just_rec | shorter | override | add_option | add_data | weights | ""
    text: str = ""


def parse(message: str) -> Command:
    text = " ".join((message or "").split()) if len(message or "") <= FOLLOWUP_MAX_CHARS else (message or "").strip()
    for kind, pattern in (("add_option", _ADD_OPTION), ("add_data", _ADD_DATA)):
        m = pattern.match(text)
        if m:
            return Command(kind, m.group("what").strip())
    if len(text) > FOLLOWUP_MAX_CHARS:
        return Command("")
    if _JSON.match(text):
        return Command("json")
    if _JUST_REC.match(text):
        return Command("just_rec")
    if _SHORTER.match(text):
        return Command("shorter")
    m = _OVERRIDE.match(text)
    if m:
        return Command("override", (m.group("what") or m.group("what2") or "").strip())
    m = _WEIGHTS.match(text)
    if m:
        return Command("weights", m.group("what").strip())
    return Command("")


def weight_changes(text: str, criteria: tuple[crit.Criterion, ...]) -> tuple[dict[str, float], list[str]]:
    """({criterion: new %}, names that match no criterion)."""
    changes: dict[str, float] = {}
    unknown: list[str] = []
    for m in _PCT_ITEM.finditer(text):
        name = re.sub(r"^(?:and|,)\s+", "", m.group("name").strip(" ,;"), flags=re.IGNORECASE)
        key = crit.key_of(name, criteria)
        if key:
            changes[key] = float(m.group("pct"))
        else:
            unknown.append(name)
    return changes, unknown
