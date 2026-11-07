"""Follow-ups on the stored draft (parsed in code) and the scope check for short messages.

    "show the evidence", "show evidence behind the strengths", "only the gaps",
    "shorter", "as json"                          -> zero calls
    "remove e5"                                   -> re-draft: 1 call (+ at most one repair)
    "add: <example>"                              -> tag the new item + re-draft: 2 calls
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from llm import LlmError, TurnDeps, complete_json

log = logging.getLogger("performance-review-drafter.followup")

OFF_TOPIC, NEEDS_EVIDENCE = "off_topic", "needs_evidence"

_EVIDENCE = re.compile(r"\b(?:show|list|see|what(?:'s| is))\b.*\b(?:evidence|quotes|sources)\b|\bevidence behind\b",
                       re.IGNORECASE)
_GAPS = re.compile(r"\b(?:only|just|show|list)\b.*\bgaps?\b|\bwhat evidence am i missing\b|\bmissing evidence\b|"
                   r"^\W*gaps?\W*$", re.IGNORECASE)
_SHORTER = re.compile(r"\bshorter\b|\bshorten\b|\bmake it short\b|\btrim it\b", re.IGNORECASE)
_REMOVE = re.compile(r"\b(?:remove|drop|exclude|ignore|delete)\s+((?:e\d{1,3}\b[\s,]*(?:and\s+)?)+)", re.IGNORECASE)
_ADD = re.compile(r"^\W*add(?: (?:this|an?|another) example)?\s*[:\-]\s*(.+)$", re.IGNORECASE | re.DOTALL)
_JSON = re.compile(r"^\W*(?:(?:show|give|return|send)(?: me)?(?: it| this| that| the draft| the review)? )?"
                   r"(?:as|in) json\W*$|^\W*json(?: only)?\W*$", re.IGNORECASE)
_SECTION = (("strengths", re.compile(r"\bstrengths?\b", re.IGNORECASE)),
            ("growth", re.compile(r"\bgrowth|weakness|improve", re.IGNORECASE)),
            ("summary", re.compile(r"\bsummary\b", re.IGNORECASE)))
DECISION = re.compile(r"\b(?:rate|rating|score|grade|rank)\b|\bpromot\w*|\b(?:raise|salary|pay|bonus|compensation|"
                      r"fire|firing|terminate|pip|lay ?off|demote)\b|\b\d\s*(?:to|-|/|out of)\s*(?:5|10)\b",
                      re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str                       # evidence | gaps | shorter | remove | add | json
    section: str = ""
    ids: tuple[str, ...] = ()
    text: str = ""


def parse(text: str) -> Command | None:
    message = (text or "").strip()
    if added := _ADD.match(message):
        return Command("add", text=added.group(1).strip())
    if _JSON.match(message):
        return Command("json")
    if removed := _REMOVE.search(message):
        return Command("remove", ids=tuple(dict.fromkeys(i.casefold() for i in re.findall(r"e\d+", removed.group(1),
                                                                                         re.IGNORECASE))))
    if _GAPS.search(message):
        return Command("gaps")
    if _EVIDENCE.search(message):
        section = next((name for name, rx in _SECTION if rx.search(message)), "")
        return Command("evidence", section=section)
    if _SHORTER.search(message):
        return Command("shorter")
    return None


def asks_for_a_decision(text: str) -> bool:
    """Rating, promotion, pay or other decisions about a person: declined in code, no call."""
    return bool(DECISION.search(text or ""))


async def classify(deps: TurnDeps, text: str, policy: Sequence[str] = ()) -> str:
    """A short message with no evidence: off topic, or a review request that still needs evidence. A failed
    call counts as needs_evidence (asking for evidence never answers an off-topic question)."""
    def validate(data: Any) -> dict | None:
        ok = isinstance(data, dict) and data.get("status") in (OFF_TOPIC, NEEDS_EVIDENCE)
        return data if ok else None
    try:
        data = await complete_json(deps, prompts.scope_messages(text, policy), prompts.SCOPE_CONTRACT, validate)
    except LlmError as e:
        log.warning("scope check failed: %s", e)
        return NEEDS_EVIDENCE
    return data["status"]
