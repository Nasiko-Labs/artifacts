"""Pure: decide what this turn does, before any model call.

Order: greeting -> "add these: <tickets>" appends -> a structured paste (CSV / JSON / headed or `---`
blocks) is a new batch -> a short request to reply to customers is declined -> a follow-up command (zero
calls) -> plain blocks with a triage request -> otherwise one scope call. Over the cap or an ambiguous CSV text column pauses with one question."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import followup
import parse_tickets
import pauses
import prompts
import smalltalk
from inputs import Incoming
from parse_tickets import Parsed

SHORT_MESSAGE_WORDS = 25
STRUCTURED = ("csv", "json", "blocks")

_REPLY = re.compile(r"\b(?:reply|respond|answer) to\b|\bwrite (?:a |an |the )?(?:reply|response|answer|email)\b|"
                    r"\bdraft (?:a |an |the )?(?:reply|response|answer|email|message)s?\b", re.IGNORECASE)
_ASKS_TRIAGE = re.compile(r"\btriage\b|\bprioriti[sz]e\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                                   # reply | pause | triage | followup | scope
    reply: str = ""
    pending: pauses.Pending | None = None
    parsed: Parsed | None = None
    command: followup.Command | None = None
    append: bool = False


def plan_turn(incoming: Incoming, has_batch: bool) -> Route:
    text = incoming.text
    canned = smalltalk.reply_to(text)
    if canned is not None:
        return Route("reply", canned)
    command = followup.parse(text)
    if command is not None and command.kind == "add":
        added = parse_tickets.parse(command.text)
        return triage(added, command.text, incoming.wants_json, append=has_batch)
    parsed = parse_tickets.parse(text)
    if parsed.ambiguous:
        return Route("pause", pending=pauses.Pending(pauses.COLUMN, text=text, columns=parsed.body_candidates,
                                                     wants_json=incoming.wants_json))
    if parsed.fmt in STRUCTURED:
        return triage(parsed, text, incoming.wants_json)
    short = len(text.split()) <= SHORT_MESSAGE_WORDS
    if short and _REPLY.search(text):
        return Route("reply", prompts.NO_REPLIES)
    if command is not None:
        return Route("followup", command=command) if has_batch else Route("reply", prompts.NO_BATCH)
    if parsed.fmt == "plain":
        return triage(parsed, text, incoming.wants_json)
    if short and _ASKS_TRIAGE.search(text):
        return Route("reply", prompts.NO_TICKETS)
    return Route("scope")


def triage(parsed: Parsed, text: str, wants_json: bool, append: bool = False, cap: bool = False,
           body_column: str = "") -> Route:
    if parsed.ambiguous:
        return Route("pause", pending=pauses.Pending(pauses.COLUMN, text=text, columns=parsed.body_candidates,
                                                     wants_json=wants_json))
    if not parsed.tickets:
        return Route("reply", prompts.NO_TICKETS)
    over = len(parsed.tickets) > pauses.MAX_TICKETS or len(text) > pauses.MAX_CHARS
    if over and not cap:
        return Route("pause", pending=pauses.Pending(pauses.TOO_MANY, text=text, count=len(parsed.tickets),
                                                     body_column=body_column, wants_json=wants_json))
    if cap:
        parsed = replace(parsed, tickets=parsed.tickets[:pauses.MAX_TICKETS])
    return Route("triage", parsed=parsed, append=append)


def plan_resume(answer: pauses.Answer, pending: pauses.Pending) -> Route:
    if answer.action != "run":
        return Route("reply", prompts.CANCELLED)
    parsed = parse_tickets.parse(answer.text, answer.body_column or None)
    return triage(parsed, answer.text, pending.wants_json, cap=answer.cap, body_column=answer.body_column)
