"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                              -> canned intro (no call)
    a draft stored + a follow-up command               -> followup
    5+ evidence items (or a Kudos envelope)            -> scan -> [Q2 pause] -> run
    short, asks for a rating / promotion / pay         -> fixed decline (no call)
    short, a draft stored                              -> scope check (decline or follow-up help)
    short, asks for a review / has header lines        -> Q1 pause: at least 5 examples
    short, anything else                               -> scope check (decline, or Q1 pause)
"""

from __future__ import annotations

from dataclasses import dataclass

import followup
import pauses
import prompts
import smalltalk
from evidence import MAX_ITEMS, Evidence, from_envelope, looks_like_request, parse
from inputs import Incoming
from scan import categories, scan

MIN_ITEMS = 5


@dataclass(frozen=True)
class Route:
    kind: str                                  # reply | pause | run | followup | scope
    reply: str = ""
    pending: pauses.Pending | None = None
    items: tuple[Evidence, ...] = ()
    header: dict | None = None
    left_out: tuple[dict, ...] = ()
    notes: tuple[str, ...] = ()
    command: followup.Command | None = None
    text: str = ""


def plan_turn(incoming: Incoming, has_draft: bool, year: int) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    if has_draft and (command := followup.parse(incoming.text)):
        return Route("followup", command=command)
    text = from_envelope(incoming.text) or incoming.text
    paste = parse(text, year)
    if len(paste.items) >= MIN_ITEMS:
        return analyse(text, incoming.wants_json, year)
    if followup.asks_for_a_decision(incoming.text):
        return Route("reply", smalltalk.DECISION_REPLY)
    if has_draft:
        return Route("scope", text=text)
    if looks_like_request(paste.instruction or text) or paste.header:
        return Route("pause", pending=pauses.Pending(pauses.NEED_EVIDENCE, text, incoming.wants_json))
    return Route("scope", text=text)


def analyse(text: str, wants_json: bool, year: int, ask_q1: bool = True, ask_q2: bool = True) -> Route:
    paste = parse(text, year)
    if len(paste.items) < MIN_ITEMS:
        if ask_q1:
            return Route("pause", pending=pauses.Pending(pauses.NEED_EVIDENCE, text, wants_json))
        return Route("reply", prompts.TOO_LITTLE)
    marked, left_out = scan(paste.items)
    if left_out and ask_q2:
        flagged = tuple(f"{x['id']}: {', '.join(categories(_text(marked, x['id'])))}" for x in left_out)
        return Route("pause", pending=pauses.Pending(pauses.PERSONAL, text, wants_json, flagged))
    notes: list[str] = []
    if paste.total > MAX_ITEMS:
        notes.append(f"_You pasted {paste.total} items; I read the first {MAX_ITEMS}. Paste the rest separately "
                     "or trim them._")
    if paste.over_chars:
        notes.append("_The paste is over 60,000 characters; very long items were read only in part._")
    clean = tuple(_cleaned(e) for e in marked)
    return Route("run", items=clean, header=paste.header, left_out=tuple(left_out), notes=tuple(notes))


def _text(items, item_id: str) -> str:
    return next((i.text for i in items if i.id == item_id), "")


def _cleaned(item: Evidence) -> Evidence:
    """Fence markers never survive into stored evidence."""
    return item.with_tags(text=prompts.strip_markers(item.text).strip() or item.text)


def plan_resume(pending: pauses.Pending, answer: pauses.Answer, year: int) -> Route:
    if answer.action != "run":
        return Route("reply", prompts.CANCELLED)
    if pending.kind == pauses.PERSONAL:
        return analyse(answer.text, pending.wants_json, year, ask_q1=False, ask_q2=False)
    return analyse(answer.text, pending.wants_json, year, ask_q1=False, ask_q2=True)
