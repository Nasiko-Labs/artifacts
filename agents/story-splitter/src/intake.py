"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                                -> canned intro (no call)
    stories stored, short message ─┬─ a refinement       -> refine
                                   └─ otherwise          -> scope check (decline or follow-up help)
    epic over 20,000 chars                               -> pause Q2: use the first 20,000 / cancel
    epic of 25+ words                                    -> run
    short, asks for stories                              -> pause Q1: what is the epic about (no call)
    short, anything else                                 -> scope check (decline, or pause Q1)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import pauses
import prompts
import refine
import smalltalk
from inputs import DEFAULT_MAX_STORIES, Incoming, Request, parse_request, word_count

_ASKS_FOR_STORIES = re.compile(r"\b(?:split|break (?:down|up)|slice)\b|\buser stories\b|\binto stories\b|\bepic\b",
                               re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                               # reply | pause | run | who | refine | scope
    reply: str = ""
    pending: pauses.Pending | None = None
    request: Request | None = None
    actor: str | None = None                # who: the answer to Q3
    command: refine.Command | None = None
    notes: tuple[str, ...] = ()


def cut_first(text: str, limit: int) -> str:
    """The first `limit` chars, cut back to the last paragraph or line break when there is one."""
    head = text[:limit]
    for boundary in ("\n\n", "\n", ". "):
        cut = head.rfind(boundary)
        if cut >= limit // 2:
            return head[:cut].rstrip()
    return head


def plan_turn(incoming: Incoming, has_stories: bool) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    request = parse_request(prompts.strip_markers(incoming.text))
    words = word_count(request.epic)
    if has_stories and words < pauses.MIN_EPIC_WORDS:
        command = refine.parse(incoming.text)
        return Route("refine", command=command) if command else Route("scope")
    if len(request.epic) > pauses.MAX_EPIC_CHARS:
        pending = _pending(pauses.TOO_BIG, request, incoming, cut_first(request.epic, pauses.MAX_EPIC_CHARS))
        return Route("pause", pending=replace(pending, chars=len(request.epic)))
    if words >= pauses.MIN_EPIC_WORDS:
        return Route("run", request=request)
    if _ASKS_FOR_STORIES.search(incoming.text):
        return Route("pause", pending=need_epic(incoming))
    return Route("scope")


def _pending(kind: str, request: Request, incoming: Incoming, epic: str) -> pauses.Pending:
    return pauses.Pending(kind, epic, request.constraints, request.max_stories, request.personas,
                          request.sprint_sized, incoming.wants_json)


def need_epic(incoming: Incoming) -> pauses.Pending:
    request = parse_request(prompts.strip_markers(incoming.text))
    epic = request.epic if _ASKS_FOR_STORIES.search(request.epic) is None else ""
    return _pending(pauses.NEED_EPIC, request, incoming, epic)


def plan_resume(answer: pauses.Answer, pending: pauses.Pending) -> Route:
    if pending.kind == pauses.WHO:
        request = Request(pending.epic, pending.constraints, pending.max_stories, pending.personas,
                          pending.sprint_sized)
        return Route("who", request=request, actor=answer.actor, pending=pending)
    if answer.action != "run":
        return Route("reply", answer.reply or prompts.CANCELLED)
    extra = parse_request(prompts.strip_markers(answer.epic))
    request = Request(extra.epic, (*pending.constraints, *extra.constraints),
                      pending.max_stories if extra.max_stories == DEFAULT_MAX_STORIES else extra.max_stories,
                      pending.personas or extra.personas, pending.sprint_sized or extra.sprint_sized)
    notes: tuple[str, ...] = ()
    if pending.kind == pauses.TOO_BIG and answer.epic == pending.epic:
        notes = (f"_The epic was {pending.chars:,} characters; I used the first ~20,000._",)
    return Route("run", request=request, notes=notes)
