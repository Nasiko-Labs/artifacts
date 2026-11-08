"""The follow-up step, after a retro was given in this conversation. Code first: "as json" returns the stored
envelope, "post it to #channel" offers the post card, a named cycle or team pulls again; anything else is
one tool-less call over the fenced stored retro that answers, or routes (`NEEDS_PULL`, `POST`, `OFF_TOPIC`)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

import post
import prompts
import render
import replies
import request
import smalltalk
from budget import TurnLimitReached
from deps import TurnDeps
from discovery import discover
from reader import ask_once
from retro import Retro


async def post_tools(deps: TurnDeps) -> tuple[str, ...]:
    """The chat post tools granted to this agent now (no LLM call)."""
    catalog, failure = await discover(deps, "post")
    return () if failure is not None else tuple(t.name for t in catalog.send_tools())


async def offer_post(deps: TurnDeps, retro: Retro, channel: str, lead: str) -> dict:
    """The post card when a chat post tool is granted, else the retro as copyable text with the reason."""
    tools = await post_tools(deps)
    if not tools:
        return {"reply": "\n\n".join(p for p in (lead, replies.NO_WRITE_TOOL) if p), "route": ""}
    pause = post.post_pause(retro, channel, tools, (), deps.config.max_post_chars, lead=lead)
    return {"pause": asdict(pause), "reply": lead, "route": ""}


async def followup_step(deps: TurnDeps, state: Mapping[str, Any]) -> dict:
    if not deps.traceparent:
        return {"reply": replies.NO_TRACE_CONTEXT, "route": ""}
    query = state["query"]
    retro = Retro.from_state(state["retro"])
    markdown = state.get("markdown") or render.retro_markdown(retro)
    req = request.parse(query)
    stored = {"reply": markdown, "envelope": render.envelope(retro, markdown), "route": ""}
    if request.wants_json(query) and not request.asks_for_retro(query):
        return stored
    if req.wants_post:
        return await _post(deps, retro, req.channel or state.get("channel") or "")
    if request.asks_for_retro(query) or (req.team and req.team.casefold() != retro.team.name.casefold()):
        return {"route": "cycle"}
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(markdown)}"
    try:
        text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    except TurnLimitReached:
        return {"reply": replies.STEP_LIMIT_NOTE, "route": ""}
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.NEEDS_PULL):
        return {"route": "cycle"}
    if text.startswith(prompts.POST):
        return await _post(deps, retro, state.get("channel") or "")
    return {"reply": text or replies.UNREADABLE_ANSWER, "route": ""}


async def _post(deps: TurnDeps, retro: Retro, channel: str) -> dict:
    if not channel:
        return {"reply": replies.NO_CHANNEL, "route": ""}
    return {**await offer_post(deps, retro, channel, ""), "channel": channel}
