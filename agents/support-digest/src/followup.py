"""The follow-up step: one tool-less call that answers from the fenced stored digest (and its breach list),
or routes the turn to a fresh pull (`NEEDS_PULL`)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import prompts
import render
import replies
import smalltalk
from budget import TurnLimitReached
from deps import TurnDeps
from reader import ask_once


async def followup_step(deps: TurnDeps, state: Mapping[str, Any]) -> dict:
    listing = render.for_followup(state.get("envelope") or {})
    # Only the fenced stored digest, never earlier replies.
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(listing)}"
    try:
        text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": state["query"]}))
    except TurnLimitReached:
        return {"reply": replies.STEP_LIMIT_NOTE, "route": "", "show_env": False}
    if text.upper().startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": "", "show_env": False}
    if text.upper().startswith(prompts.NEEDS_PULL):
        return {"route": "pull"}
    return {"reply": text or replies.UNREADABLE_ANSWER, "route": "", "show_env": True}
