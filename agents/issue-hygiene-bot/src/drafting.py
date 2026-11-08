"""Drafting the picked owners' messages: one tool-less call per owner words the intro sentence (checked in
code, replaced by a fixed one on any detail), the rest is code. The send tool is chosen here and named on
every card, so the user approves how the message goes out too. Nothing is sent from here."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import dm
import prompts
import replies
from budget import TurnLimitReached
from deps import TurnDeps
from reader import ask_once
from records import Owner
from sender import send_tool

log = logging.getLogger("issue_hygiene.drafting")


@dataclass(frozen=True)
class Drafted:
    """`dms` go to the approval walk; `reply` carries what cannot be sent (copyable text) or a fixed reply."""

    dms: tuple[dm.Dm, ...]
    reply: str = ""


async def intro_for(deps: TurnDeps, owner: Owner, note: str) -> str:
    """One tool-less call; any failure or limit falls back to the fixed sentence."""
    user = "Write the opening sentence." + (f"\nTone note from the user:\n{prompts.fence_user(note)}" if note else "")
    try:
        text = await ask_once(deps, ({"role": "system", "content": prompts.INTRO_SYSTEM},
                                     {"role": "user", "content": user}))
    except TurnLimitReached:
        log.info("turn limit reached before the intro for %s; using the fixed sentence", owner.key)
        return dm.FALLBACK_INTRO
    return dm.checked_intro(text)


def fixed_messages(owners: Sequence[Owner], deps: TurnDeps) -> tuple[dm.Dm, ...]:
    """Messages with the fixed intro, no LLM call: for copyable text when nothing can be sent."""
    return tuple(dm.build(o, dm.FALLBACK_INTRO, deps.config) for o in owners)


async def draft(deps: TurnDeps, owners: Sequence[Owner], note: str) -> Drafted:
    if not owners:
        return Drafted((), replies.NOTHING_PICKED)
    if not deps.traceparent:
        return Drafted((), replies.NO_TRACE_CONTEXT)
    tool, failure = await send_tool(deps)
    sendable = [o for o in owners if o.user_id]
    copy_only = [o for o in owners if not o.user_id]
    parts: list[str] = []
    if copy_only:
        parts.append(dm.copyable_all(fixed_messages(copy_only, deps), replies.NO_CHAT_USER_NOTE))
    if tool is None:
        log.info("no chat send tool: %s", failure)
        parts.insert(0, dm.copyable_all(fixed_messages(sendable, deps), replies.NO_CHAT_NOTE))
        return Drafted((), "\n\n".join(parts))
    intros = [await intro_for(deps, o, note) for o in sendable]
    dms = tuple(dm.build(o, intro, deps.config, tool.name) for o, intro in zip(sendable, intros, strict=True))
    return Drafted(dms, "\n\n".join(parts))
