"""Follow-ups on the stored notes, parsed in code, and the one scope call for a short message.

    zero calls: "drop item 4", "move item 3 to Fixed", "only fixes", "show internal", "version 2.5.0",
                "date 2026-11-05", "as json", "send it to #releases", "put the breaking change first"
    model calls: "audience developers", "friendlier" (Slack + email again), "shorter Slack post" (Slack only),
                 "include internal" (a fresh run), anything else -> one scope call
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from inputs import normalise_audience
from llm import LlmError, TurnDeps, complete_json

log = logging.getLogger("release-notes.followup")

OFF_TOPIC, NEEDS_CHANGES, REVISE = "off_topic", "needs_changes", "revise"
BLOCK_WORDS = {"fix": "fixed", "fixes": "fixed", "fixed": "fixed", "bug": "fixed", "bugs": "fixed", "new": "new",
               "features": "new", "feature": "new", "improved": "improved", "improvements": "improved",
               "improvement": "improved", "deprecated": "deprecated", "deprecations": "deprecated",
               "security": "security", "breaking": "breaking"}
_BLOCK = "|".join(sorted(BLOCK_WORDS, key=len, reverse=True))

_SEND = re.compile(r"^\W*(?:(?:please|can you|could you|now)\s+)*(?:send|post|publish|share|announce|email|"
                   r"tweet|deliver)\b", re.IGNORECASE)
_JSON = re.compile(r"^\W*(?:(?:show|give|return|send)(?: me)?(?: it| this| that| them| the notes)? )?"
                   r"(?:as|in) json\W*$|^\W*json(?: only)?\W*$", re.IGNORECASE)
_DROP = re.compile(r"^\W*(?:drop|remove|delete|cut|leave out)\s+(?:item\s*|#|no\.?\s*|number\s*)?(\d{1,3})\b",
                   re.IGNORECASE)
_MOVE = re.compile(rf"\bmove\s+(?:item\s*|#|no\.?\s*)?(\d{{1,3}})\s+(?:to|into|under)\s+(?:the\s+)?({_BLOCK})\b",
                   re.IGNORECASE)
_ONLY = re.compile(rf"^\W*(?:show\s+(?:me\s+)?)?(?:only|just)\s+(?:the\s+)?({_BLOCK})\b", re.IGNORECASE)
_INTERNAL = re.compile(r"\b(?:show|list|see|what)\b.*\binternal\b|^\W*internal(?: changes)?\W*$|\bleft out\b",
                       re.IGNORECASE)
_INCLUDE = re.compile(r"\binclude (?:the )?internal\b", re.IGNORECASE)
_VERSION = re.compile(r"^\W*(?:set\s+(?:the\s+)?)?version\s*(?:to|=|:|is)?\s*(v?\d+(?:\.\d+){1,3}[\w.+-]*?)\W*$",
                      re.IGNORECASE)
_DATE = re.compile(r"^\W*(?:set\s+(?:the\s+)?)?(?:release\s+date|date|released(?: on)?)\s*(?:to|=|:|is)?\s*"
                   r"(\d{4}-\d{2}-\d{2})\W*$", re.IGNORECASE)
_AUDIENCE = re.compile(r"\baudience\s*(?:to|=|:|is)?\s*(admins?|developers?|end[ -]users?|all)\b", re.IGNORECASE)
_SHORTER = re.compile(r"\bshort(?:er)?\b|\bbrief(?:er)?\b|\bconcise\b|\btrim\b|\btighter\b", re.IGNORECASE)
_TONES = (("friendly", re.compile(r"\bfriendl(?:y|ier)\b|\bwarm(?:er)?\b|\bcasual\b", re.IGNORECASE)),
          ("formal", re.compile(r"\bformal\b|\bprofessional\b", re.IGNORECASE)),
          ("neutral", re.compile(r"\bneutral\b|\bplain(?:er)?\b", re.IGNORECASE)))
_FIRST = re.compile(r"\bbreaking\b.*\bfirst\b|\bfirst\b.*\bbreaking\b", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str            # send json drop move only internal include version date audience shorter tone first
    number: int = 0
    value: str = ""      # block, version, date, audience, tone, or "slack" / "email" / "both" for shorter


def _target(text: str) -> str:
    lowered = text.casefold()
    slack, email = "slack" in lowered or "post" in lowered, "email" in lowered or "mail" in lowered
    return "slack" if slack and not email else "email" if email and not slack else "both"


def parse(text: str) -> Command | None:
    message = " ".join((text or "").split())
    if not message or len(message) > 300:
        return None
    simple = ((_JSON, "json"), (_INCLUDE, "include"))
    if _SEND.match(message) and not _SHORTER.search(message):
        return Command("send")
    for pattern, kind in simple:
        if pattern.search(message):
            return Command(kind)
    if m := _DROP.match(message):
        return Command("drop", int(m.group(1)))
    if m := _MOVE.search(message):
        return Command("move", int(m.group(1)), BLOCK_WORDS[m.group(2).casefold()])
    if m := _ONLY.match(message):
        return Command("only", value=BLOCK_WORDS[m.group(1).casefold()])
    if _INTERNAL.search(message):
        return Command("internal")
    if m := _VERSION.match(message):
        return Command("version", value=m.group(1))
    if m := _DATE.match(message):
        return Command("date", value=m.group(1))
    if m := _AUDIENCE.search(message):
        return Command("audience", value=normalise_audience(m.group(1)))
    if _SHORTER.search(message):
        return Command("shorter", value=_target(message))
    for tone, pattern in _TONES:
        if pattern.search(message):
            return Command("tone", value=tone)
    if _FIRST.search(message):
        return Command("first")
    return None


async def classify(deps: TurnDeps, text: str, has_notes: bool, policy: Sequence[str] = ()) -> str:
    """off_topic | needs_changes | revise. A failed call counts as needs_changes: asking for the changes
    never answers an off-topic question."""
    allowed = (OFF_TOPIC, NEEDS_CHANGES, REVISE) if has_notes else (OFF_TOPIC, NEEDS_CHANGES)

    def validate(data: Any) -> dict | None:
        return data if isinstance(data, dict) and data.get("status") in allowed else None

    try:
        data = await complete_json(deps, prompts.scope_messages(text, has_notes, policy), prompts.SCOPE_CONTRACT,
                                   validate)
    except LlmError as e:
        log.warning("scope check failed: %s", e)
        return NEEDS_CHANGES
    return data["status"]
