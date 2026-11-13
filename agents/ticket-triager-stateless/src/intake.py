"""Pure: decide what this turn does, before any model call. Nothing ever pauses: every route ends the task.

Order: greeting -> "add these: <tickets>" (triaged together with the batch found in history) -> a structured
paste (CSV / JSON / headed or `---` blocks) is a new batch -> a short request to reply to customers is
declined -> a follow-up command (on the triage or batch in history) -> plain blocks with a triage request ->
otherwise one scope call. Over 200 tickets: the first 200 are triaged and the rest named as left out. A CSV
with several text columns: the most body-like column is used, and the reply says which."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import followup
import parse_tickets
import prompts
import smalltalk
from inputs import Incoming
from parse_tickets import Parsed

SHORT_MESSAGE_WORDS = 25
MAX_TICKETS = 200
BODY_LIKE = ("description", "body", "message", "text", "details", "content", "comment", "comments")
STRUCTURED = ("csv", "json", "blocks")

_REPLY = re.compile(r"\b(?:reply|respond|answer) to\b|\bwrite (?:a |an |the )?(?:reply|response|answer|email)\b|"
                    r"\bdraft (?:a |an |the )?(?:reply|response|answer|email|message)s?\b", re.IGNORECASE)
_ASKS_TRIAGE = re.compile(r"\btriage\b|\bprioriti[sz]e\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                                   # reply | triage | followup | scope
    reply: str = ""
    parsed: Parsed | None = None
    command: followup.Command | None = None
    append: bool = False
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, has_prior: bool) -> Route:
    text = incoming.text
    canned = smalltalk.reply_to(text)
    if canned is not None:
        return Route("reply", canned)
    command = followup.parse(text)
    if command is not None and command.kind == "add":
        return triage(command.text, append=has_prior)
    parsed = parse_tickets.parse(text)
    if parsed.fmt in STRUCTURED:
        return triage(text)
    short = len(text.split()) <= SHORT_MESSAGE_WORDS
    if short and _REPLY.search(text):
        return Route("reply", prompts.NO_REPLIES)
    if command is not None:
        return Route("followup", command=command) if has_prior else Route("reply", ask_for_batch(incoming))
    if parsed.fmt == "plain":
        return triage(text)
    if short and _ASKS_TRIAGE.search(text):
        return Route("reply", prompts.NO_TICKETS)
    return Route("scope")


def ask_for_batch(incoming: Incoming) -> str:
    """No batch to work on: with earlier turns it was trimmed from what the platform sent, so say so."""
    return prompts.NOT_IN_HISTORY if incoming.history else prompts.NO_BATCH


def parse_batch(text: str) -> tuple[Parsed, tuple[str, ...]]:
    """The tickets in `text`, never ambiguous: with several text columns the most body-like one is used."""
    parsed = parse_tickets.parse(text)
    if not parsed.ambiguous:
        return parsed, ()
    candidates = parsed.body_candidates
    column = next((c for name in BODY_LIKE for c in candidates if c.strip().casefold() == name), candidates[0])
    note = (f"_Your export has more than one text column ({', '.join(candidates)}); I used \"{column}\" as the "
            "ticket text. Paste it with only the column you want if that's wrong._")
    return parse_tickets.parse(text, column), (note,)


def triage(text: str, append: bool = False) -> Route:
    parsed, notes = parse_batch(text)
    if not parsed.tickets:
        return Route("reply", prompts.NO_TICKETS)
    return Route("triage", parsed=parsed, append=append, notes=notes)


def capped(parsed: Parsed) -> tuple[Parsed, tuple[str, ...]]:
    """The first MAX_TICKETS tickets, and a note naming what was left out."""
    if len(parsed.tickets) <= MAX_TICKETS:
        return parsed, ()
    left = parsed.tickets[MAX_TICKETS:]
    shown = ", ".join(t.label for t in left[:10]) + (f" and {len(left) - 10} more" if len(left) > 10 else "")
    note = (f"_That's {len(parsed.tickets):,} tickets; I triage up to {MAX_TICKETS} at a time, so I triaged the "
            f"first {MAX_TICKETS} and left out {len(left)} ({shown}). Paste those as a second batch._")
    return replace(parsed, tickets=parsed.tickets[:MAX_TICKETS]), (note,)
