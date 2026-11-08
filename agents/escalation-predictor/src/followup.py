"""The follow-up step: one tool-less call that answers from the fenced stored result, or routes the turn
to a new pull (`NEEDS_PULL`) or to drafting (`DRAFT:<ids>`)."""

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

_TICKET_ID = re.compile(r"[A-Za-z0-9_-]+")


def requested_ids(answer: str) -> tuple[str, ...]:
    """`DRAFT:4812, #4790` → ("4812", "4790"), de-duplicated in order."""
    return tuple(dict.fromkeys(_TICKET_ID.findall(answer[len(prompts.DRAFT_PREFIX):])))


async def followup_step(deps: TurnDeps, state: Mapping[str, Any]) -> dict:
    listing = render.for_followup(state.get("envelope") or {})
    # Only the fenced stored result, never earlier replies.
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(listing)}"
    try:
        text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": state["query"]}))
    except TurnLimitReached:
        return {"reply": replies.STEP_LIMIT_NOTE, "route": "", "show_env": False}
    if text.upper().startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": "", "show_env": False}
    if text.upper().startswith(prompts.NEEDS_PULL):
        return {"route": "pull"}
    if text.upper().startswith(prompts.DRAFT_PREFIX):
        return {"route": "draft", "draft_ids": list(requested_ids(text))}
    return {"reply": text or replies.UNREADABLE_ANSWER, "route": "", "show_env": True}
