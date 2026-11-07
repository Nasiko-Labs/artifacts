"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                         -> canned intro (no call)
    asks to judge, rate or compare the person     -> decline (no call)
    a prep stored, short message ─┬─ follow-up    -> followup (zero calls)
                                  └─ otherwise    -> ask (one call: answer from the notes, or decline)
    over 60,000 chars                             -> reply: trim it
    40+ words of notes and updates ─┬─ sensitive  -> pause Q2
                                    └─ otherwise  -> run
    short, asks for prep                          -> pause Q1 (no call)
    short, anything else                          -> scope check (decline, or pause Q1)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import followup
import pauses
import sensitive
import smalltalk
from inputs import Incoming, word_count
from judgement import asks_for_verdict
from split import Paste, split

TOO_BIG = ("That's over 60,000 characters. Paste only the last meeting's notes and the updates since then for one "
           "person.")
_ASKS_FOR_PREP = re.compile(r"\b(?:prep|prepare|1:1|one-on-one|notes|updates)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                               # reply | pause | run | followup | ask | scope
    reply: str = ""
    pending: pauses.Pending | None = None
    paste: Paste | None = None
    personal: int = 0                       # sentences left out
    sensitive_included: bool = False
    command: followup.Command | None = None


def plan_paste(paste: Paste, wants_json: bool) -> Route:
    flagged = sensitive.flagged(paste.text())
    if flagged:
        return Route("pause", pending=pauses.Pending(pauses.SENSITIVE, paste.to_dict(), tuple(flagged),
                                                     wants_json=wants_json))
    return Route("run", paste=paste)


def plan_turn(incoming: Incoming, has_result: bool, stored_notes: str = "") -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    if asks_for_verdict(incoming.text):
        return Route("reply", smalltalk.EVALUATION_REPLY)
    if len(incoming.text) > pauses.MAX_CHARS:
        return Route("reply", TOO_BIG)
    paste = split(incoming.text, stored_notes)
    words = word_count(paste.text())
    if has_result and word_count(incoming.text) < pauses.MIN_WORDS:
        command = followup.parse(incoming.text)
        return Route("followup", command=command) if command else Route("ask")
    if words >= pauses.MIN_WORDS:
        return plan_paste(paste, incoming.wants_json)
    if _ASKS_FOR_PREP.search(incoming.text):
        return Route("pause", pending=need_input(incoming))
    return Route("scope")


def need_input(incoming: Incoming) -> pauses.Pending:
    return pauses.Pending(pauses.NEED_INPUT, partial=incoming.text[:pauses.PARTIAL_MAX_CHARS],
                          wants_json=incoming.wants_json)


def plan_resume(pending: pauses.Pending, answer: pauses.Answer, stored_notes: str = "") -> Route:
    if answer.action == "cancel":
        return Route("reply", answer.reply)
    if pending.kind == pauses.NEED_INPUT:
        paste = split(answer.text, stored_notes)
        if word_count(paste.text()) < pauses.MIN_WORDS:
            return Route("reply", "That's still too little to prepare a 1:1 from, so I stopped here. Paste the "
                                  "notes and updates in a new message when you're ready.")
        return plan_paste(paste, pending.wants_json)
    paste = Paste.from_dict(pending.paste)
    if answer.action == "include":
        return Route("run", paste=paste, sensitive_included=True)
    drop = list(pending.flagged)
    cleaned = replace(paste, notes=sensitive.remove(paste.notes, drop), updates=sensitive.remove(paste.updates, drop))
    return Route("run", paste=cleaned, personal=len(drop))
