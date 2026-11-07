"""Reading the user's request: code first, the model only when code can't tell.

Turn 1: the window (dates.py), a team/project/service filter, and an incident to match. When the
message has none of the known cues it may be off topic, so one tool-less call decides (`intake`).
Later turns: "nudge #2 and #5", "nudge all untouched", "reopen #4", "does INC-881 match an old
postmortem?", "only the payments team", "check again" are parsed here; anything else goes to the
follow-up step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import step_prompts
from dates import Window
from deps import TurnDeps
from records import TICKET_KEY, load_json_object, text_of
from steps import ask_once

CUES = re.compile(r"post[- ]?mortem|action items?|slipp|stale|follow[- ]?(?:through|ups?)|\bRCA\b|incident|"
                  r"outage|repeat|nudge|reopen|overdue|remediation|untouched|unassigned", re.IGNORECASE)
_FILTER = re.compile(r"\bonly (?:for )?(?:the )?([\w-]+(?: [\w-]+)?) (?:team|service|project)\b|"
                     r"\b(?:project|team|service|component)\s+([A-Za-z0-9_-]{2,})\b", re.IGNORECASE)
_MATCH = re.compile(r"\bmatch(?:es)?\b|\bsame as\b|\bhappened before\b|\bseen (?:this|it) before\b", re.IGNORECASE)
_MATCH_TEXT = re.compile(r"\b(?:does|did|is)\s+(.+?)\s+(?:match|the same)", re.IGNORECASE)
_HASH_REF = re.compile(r"(?<![\w#])#\d{3,}\b")
_NUMBER = re.compile(r"#?\b(\d{1,3})\b")
_NUDGE = re.compile(r"\b(?:nudge|remind|ping|message|dm)\b", re.IGNORECASE)
_REOPEN = re.compile(r"\breopen\b", re.IGNORECASE)
_ALL = re.compile(r"\ball\b(?:\s+(unassigned|untouched|stale|closed|idle))?", re.IGNORECASE)
_REFRESH = re.compile(r"\b(?:check again|refresh|re-?run|search again|start over|look again)\b|"
                      r"\b(?:last|past)\s+\d+\s+months?\b|\bsince\s+[A-Za-z]{3,9}\b|\bthis year\b", re.IGNORECASE)
_NOT_FILTER_WORDS = frozenset({"the", "a", "my", "our", "this", "that", "new", "old"})


@dataclass(frozen=True)
class Intent:
    status: str = "ok"            # "ok" | "off_topic"
    filter: str | None = None
    match_ref: str | None = None  # an incident id to fetch
    match_text: str | None = None  # a short description to match by title when there is no id


def incident_ref(text: str) -> str | None:
    found = TICKET_KEY.search(text or "") or _HASH_REF.search(text or "")
    return found.group(0) if found else None


def filter_of(text: str) -> str | None:
    match = _FILTER.search(text or "")
    value = (match.group(1) or match.group(2)) if match else None
    return value if value and value.casefold() not in _NOT_FILTER_WORDS else None


def match_request(text: str) -> tuple[str | None, str | None] | None:
    """(id, description) when the message asks whether an incident matches an old postmortem."""
    if not _MATCH.search(text or ""):
        return None
    ref = incident_ref(text)
    if ref:
        return ref, None
    described = _MATCH_TEXT.search(text)
    return None, (described.group(1).strip(" ?.") if described else text.strip(" ?."))


def parse_request(text: str) -> tuple[Intent, bool]:
    """The intent code can read, and whether the message carries a known cue."""
    match = match_request(text)
    intent = Intent(filter=filter_of(text), match_ref=match[0] if match else None,
                    match_text=match[1] if match else None)
    return intent, bool(CUES.search(text or ""))


async def intake(deps: TurnDeps, text: str, window: Window) -> Intent:
    """At most one tool-less call, only when code found no cue."""
    intent, has_cue = parse_request(text)
    if has_cue or not deps.traceparent:  # without a trace context the next step refuses with no call
        return intent
    answer = load_json_object(await ask_once(deps, (
        {"role": "system", "content": step_prompts.intake_system(window)}, {"role": "user", "content": text})))
    if answer is None:
        return intent
    if answer.get("status") == "off_topic":
        return Intent(status="off_topic")
    asked = text_of(answer.get("match_incident")) or None
    ref = incident_ref(asked) if asked else None
    return Intent(filter=intent.filter or text_of(answer.get("filter")) or None,
                  match_ref=intent.match_ref or ref,
                  match_text=intent.match_text or (asked if asked and not ref else None))


CommandKind = Literal["nudge", "nudge_all", "reopen", "match", "refresh", "filter", "off_topic", "answer", "other"]


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    ids: tuple[str, ...] = ()
    arg: str | None = None


def numbers(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_NUMBER.findall(text or "")))


def parse_command(text: str) -> Command:
    """A follow-up code can act on without a model call, or `other`."""
    if _NUDGE.search(text):
        everything = _ALL.search(text)
        ids = numbers(text)
        if everything and not ids:
            return Command("nudge_all", arg=(everything.group(1) or "").casefold() or None)
        if ids:
            return Command("nudge", ids)
    if _REOPEN.search(text) and numbers(text):
        return Command("reopen", numbers(text))
    match = match_request(text)
    if match:
        return Command("match", arg=match[0] or match[1])
    if _REFRESH.search(text):
        return Command("refresh")
    wanted = filter_of(text)
    if wanted and re.search(r"\bonly\b", text, re.IGNORECASE):
        return Command("filter", arg=wanted)
    return Command("other")
