"""The follow-up step: one tool-less call that answers from the fenced stored result, or routes the turn
to a new pull (`NEEDS_PULL`) or to drafting (`DRAFT:<ticket ids>`)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import prompts
import render
import replies
import smalltalk
from budget import TurnLimitReached
from deps import TurnDeps
from reader import ask_once
from records import Bug, ShipVerdict, Ticket, from_state

_TICKET_ID = re.compile(r"[A-Za-z0-9_-]+")


def requested_ids(answer: str) -> tuple[str, ...]:
    """`DRAFT:4711, #4712` → ("4711", "4712"), de-duplicated in order."""
    return tuple(dict.fromkeys(_TICKET_ID.findall(answer[len(prompts.DRAFT_PREFIX):])))


async def followup_step(deps: TurnDeps, state: Mapping[str, Any]) -> dict:
    if not deps.traceparent:
        return {"reply": replies.NO_TRACE_CONTEXT, "route": ""}
    listing = render.for_followup(from_state(Bug, state["bug"]), from_state(ShipVerdict, state["verdict"]),
                                  from_state(Ticket, state.get("tickets") or []), state.get("outcomes") or {})
    # Only the fenced result, never earlier replies: those carry ticket text outside a fence.
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(listing)}"
    try:
        text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": state["query"]}))
    except TurnLimitReached:
        return {"reply": replies.STEP_LIMIT_NOTE, "route": ""}
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.NEEDS_PULL):
        return {"route": "bug"}
    if text.startswith(prompts.DRAFT_PREFIX):
        instruction = state["query"][:deps.config.max_instruction_chars]
        return {"route": "draft", "draft_ids": list(requested_ids(text)), "instruction": instruction}
    return {"reply": text or replies.UNREADABLE_ANSWER, "route": ""}
