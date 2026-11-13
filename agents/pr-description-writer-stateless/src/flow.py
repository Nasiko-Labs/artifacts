"""The pr-description-writer-stateless turn: plain async steps around LangChain runnables (see `llm.py`).

    intake ─┬─ draft    (digest -> 1 draft call -> checks -> title -> render)
            ├─ restyle  (1 call over the last draft in the history, facts rebuilt from its diff)
            ├─ classify (no diff: decline or ask for the diff)
            └─ fixed reply / split advice / ask for the diff

A greeting never enters the flow (smalltalk.py). Stateless: the turn state is built fresh from this turn's
input, so any replica can answer any message. Earlier turns come only from the platform's chat history
(`history.py`, `recall.py`). No route pauses: every turn ends with a reply.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypedDict

import smalltalk
import turns
from inputs import Incoming
from llm import TurnDeps


class WriterState(TypedDict, total=False):
    route: str            # this turn only
    reply: str            # this turn only
    data: dict | None     # this turn only: the envelope to attach as a data part


Step = Callable[[dict, turns.Scope], Awaitable[dict]]
ROUTES: dict[str, Step] = {"draft": turns.draft, "restyle": turns.restyle, "classify": turns.classify}


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class WriterAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:  # a greeting or small talk: no LLM call
            return TurnResult(canned)
        scope = turns.Scope(deps, incoming)
        state = {"route": "", "reply": "", "data": None, **turns.intake({}, scope)}
        step = ROUTES.get(state["route"])
        if step is not None:
            state = {**state, **await step(state, scope)}
        return TurnResult(state.get("reply") or "", state.get("data"))

