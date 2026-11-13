"""Pure: decide what one turn does, before any LLM call. Nothing is stored between turns: earlier work is
whatever `recall.Prior` finds in the platform's chat history. No turn ever pauses.

    greeting / small talk                          -> canned intro (no call)
    "start over"                                   -> fixed reply; recall ignores everything before it
    our last reply asked for more detail           -> this message is appended to that request
    earlier PRD in the history ─┬─ zero-call command -> rebuild it, then re-render / drop / json
                                ├─ a revision cue          -> rebuild it, then revise (1 call + <= 1 repair)
                                ├─ short, no cue           -> scope check: decline off-topic, else revise
                                └─ otherwise              -> a new brief
    nothing recalled ─┬─ enough brief              -> new PRD (extract -> compose -> check); enough = 40+ words,
                      │                               or 20+ naming 4 of who/need/constraint/deadline/metric
                      ├─ a follow-up, history sent -> "paste the brief again" (trimmed)
                      ├─ a PRD command, no history -> "no PRD yet" (no call)
                      ├─ short and asks for a PRD  -> ask for more detail in the reply (no call)
                      └─ short, anything else      -> one scope call: decline, or ask for more detail
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import prompts
import smalltalk
from inputs import Incoming, enough_brief, split_request
from recall import Prior

_PRD_CUE = re.compile(r"\b(?:prd|product requirements?|spec|brief|feature|product|customers?|users?|"
                      r"we want|launch|ship|roadmap)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    route: str                      # reply | new | revise | scope_then_revise | command | scope
    reply: str = ""
    brief: str = ""
    instruction: str = ""
    command: followup.Command | None = None


def plan_turn(incoming: Incoming, prior: Prior) -> Plan:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned)
    text = prompts.strip_markers(incoming.text)
    command = followup.parse_command(text)
    if command is not None and command.kind == "start_over":
        return Plan("reply", prompts.STARTED_OVER)
    has_prd = prior.has_material
    if has_prd and command is not None:
        return Plan("command", command=command)
    if prior.short_brief and command is None:
        combined = split_request(f"{prior.short_brief}\n\n{text.strip()}")
        if enough_brief(combined.brief):
            return Plan("new", brief=combined.brief, instruction=combined.instruction)
    request = split_request(text)
    if enough_brief(request.brief) and not (has_prd and followup.is_revision(text)
                                                             and not request.instruction):
        return Plan("new", brief=request.brief, instruction=request.instruction)
    if has_prd:  # no revision cue ("what's 2+2?", "what about mobile?"): the scope check decides first
        cued = followup.has_revision_cue(text)
        return Plan("revise" if cued else "scope_then_revise", instruction=text.strip())
    return _nothing_recalled(incoming, text, command)


def _nothing_recalled(incoming: Incoming, text: str, command: followup.Command | None) -> Plan:
    if incoming.had_history and (command is not None or followup.has_revision_cue(text)):
        return Plan("reply", prompts.NEED_REPASTE)
    if command is not None:
        return Plan("reply", prompts.NO_PRD_YET)
    if _PRD_CUE.search(text):
        return Plan("reply", prompts.NEED_MORE)
    return Plan("scope")
