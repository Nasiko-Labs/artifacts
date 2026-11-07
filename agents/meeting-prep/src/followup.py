"""A follow-up on cards already given in this conversation: one tool-less call over the fenced cached
cards. It answers from them, or says the message is off-topic, needs one meeting refreshed, or needs
a new prep (another day, the calendar again)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import prep_prompts as prompts
from deps import TurnDeps
from reader import ask_once

Kind = Literal["answer", "off_topic", "refresh", "new"]
_REFRESH = re.compile(rf"^{re.escape(prompts.NEEDS_REFRESH)}\s*#?(\d{{1,2}})")


@dataclass(frozen=True)
class Decision:
    kind: Kind
    text: str = ""
    number: int = 0


def classify_reply(text: str) -> Decision:
    reply = (text or "").strip()
    if reply.startswith(prompts.OFF_TOPIC):
        return Decision("off_topic")
    if reply.startswith(prompts.NEW_PREP) or not reply:
        return Decision("new")
    refresh = _REFRESH.match(reply)
    if refresh:
        return Decision("refresh", number=int(refresh.group(1)))
    if reply.startswith(prompts.NEEDS_REFRESH):
        return Decision("new")
    return Decision("answer", reply)


async def decide(deps: TurnDeps, query: str, cards_text: str) -> Decision:
    # Only the fenced cards, never earlier replies outside a fence.
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_cards(cards_text)}"
    return classify_reply(await ask_once(deps, ({"role": "system", "content": system},
                                                {"role": "user", "content": query})))
