"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                          -> canned intro (no call)
    a PRD is stored ─┬─ zero-call command          -> re-render / drop / start over / json
                     ├─ short, or a revision cue   -> revise (1 call + <= 1 repair)
                     └─ otherwise                  -> a new brief
    nothing stored ─┬─ >= 40 words of brief        -> new PRD (extract -> compose -> check)
                    ├─ short and asks for a PRD    -> pause Q1 (no call)
                    ├─ a stored-PRD command        -> "no PRD yet" (no call)
                    └─ short, anything else        -> one scope call: decline, or pause Q1
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import pauses
import prompts
import smalltalk
from inputs import MIN_BRIEF_WORDS, Incoming, split_request, word_count

_PRD_CUE = re.compile(r"\b(?:prd|product requirements?|spec|brief|feature|product|customers?|users?|"
                      r"we want|launch|ship|roadmap)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    route: str                      # reply | pause | new | revise | command | scope | who
    reply: str = ""
    pending: pauses.Pending | None = None
    brief: str = ""
    instruction: str = ""
    command: followup.Command | None = None
    audience: str = ""


def plan_turn(incoming: Incoming, has_prd: bool) -> Plan:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned)
    text = prompts.strip_markers(incoming.text)
    command = followup.parse_command(text)
    if has_prd and command is not None:
        return Plan("command", command=command)
    request = split_request(text)
    if word_count(request.brief) >= MIN_BRIEF_WORDS and not (has_prd and followup.is_revision(text)
                                                             and not request.instruction):
        return Plan("new", brief=request.brief, instruction=request.instruction)
    if has_prd:
        return Plan("revise", instruction=text.strip())
    if command is not None:
        return Plan("reply", prompts.NO_PRD_YET)
    pending = pauses.Pending(pauses.TOO_SHORT, brief=text.strip(), wants_json=incoming.wants_json)
    if _PRD_CUE.search(text):
        return Plan("pause", pending=pending)
    return Plan("scope", pending=pending)


def plan_resume(answer: pauses.Answer, pending: pauses.Pending) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", prompts.GIVEN_UP)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    if answer.action == "who":
        return Plan("who", brief=pending.brief, instruction=pending.instruction, audience=answer.audience)
    request = split_request(answer.brief)
    return Plan("new", brief=request.brief, instruction=pending.instruction or request.instruction)
