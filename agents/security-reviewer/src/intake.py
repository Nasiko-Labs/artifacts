"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                         -> canned intro (no call)
    asks for an exploit, payload or attack        -> fixed decline (no call)
    code, a diff or a manifest in the message     -> review
    a review stored, no new code                  -> follow-up (views cost no call)
    asks for a review but pasted nothing          -> pause: paste the code (no call)
    anything else                                 -> scope check (one call: decline or pause)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import hitl
import smalltalk
from diff_parse import Parsed, parse
from inputs import Incoming, assemble, from_envelope

_ASKS_REVIEW = re.compile(r"\b(?:review|audit|scan|check|look (?:at|over)|analy[sz]e|inspect|find (?:bugs|issues|vulns?))"
                          r"\b.*\b(?:code|diff|pr|pull request|patch|file|function|view|endpoint|snippet|this|my|"
                          r"requirements|package|manifest|dependenc\w*|commit)\b|\bsecurity review\b", re.IGNORECASE)
_FOCUS_WORD = re.compile(r"\b(?:only|just|focus(?:ed)? on)\b", re.IGNORECASE)
_FOCI = (("secrets", re.compile(r"\bsecrets?\b|\bcredentials?\b", re.IGNORECASE)),
         ("dependencies", re.compile(r"\bdependenc(?:y|ies)\b|\bpackages?\b", re.IGNORECASE)),
         ("injection", re.compile(r"\binjection\b|\bsqli\b|\bxss\b", re.IGNORECASE)),
         ("auth", re.compile(r"\bauth\w*\b|\bpermissions?\b|\baccess control\b", re.IGNORECASE)))


@dataclass(frozen=True)
class Route:
    kind: str                       # reply | pause | run | followup | scope
    reply: str = ""
    pending: hitl.Pending | None = None
    parsed: Parsed | None = None
    focus: str = ""
    command: followup.Command | None = None


def focus_of(instruction: str) -> str:
    if not _FOCUS_WORD.search(instruction or ""):
        return ""
    hits = [name for name, rx in _FOCI if rx.search(instruction)]
    return hits[0] if len(hits) == 1 else ""


def plan_turn(incoming: Incoming, has_result: bool) -> Route:
    canned = smalltalk.reply_to(incoming.text) if not incoming.files else None
    if canned is not None:
        return Route("reply", canned)
    enveloped = from_envelope(incoming.text)
    parsed = parse(enveloped if enveloped is not None else assemble(incoming.text, incoming.files))
    words = " ".join((parsed.instruction, *parsed.context))
    if smalltalk.asks_for_attack(words):
        return Route("reply", smalltalk.EXPLOIT_REPLY)
    if parsed.files:
        return Route("run", parsed=parsed, focus=focus_of(parsed.instruction))
    if has_result:
        return Route("followup", command=followup.parse(incoming.text))
    if _ASKS_REVIEW.search(incoming.text):
        return Route("pause", pending=need_code(incoming))
    return Route("scope")


def need_code(incoming: Incoming) -> hitl.Pending:
    return hitl.Pending(hitl.NEED_CODE, " ".join(incoming.text.split())[:300], incoming.wants_json)


def plan_resume(answer: hitl.Answer) -> Route:
    if answer.action != "run":
        return Route("reply", answer.reply)
    parsed = parse(answer.text)
    if smalltalk.asks_for_attack(" ".join((parsed.instruction, *parsed.context))):
        return Route("reply", smalltalk.EXPLOIT_REPLY)
    return Route("run", parsed=parsed, focus=focus_of(parsed.instruction))
