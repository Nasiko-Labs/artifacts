"""The stack-trace-explainer-stateless turn: plain async steps around LangChain runnables (see `llm.py`).

    intake ─┬─ explain  (parse -> explain per trace -> check -> repair? -> render)
            ├─ attach   (pasted code for the earlier trace: explain again)
            ├─ followup (one call over the earlier trace, code and answer)
            ├─ scope    (no error text: decline, or ask for the error in a reply)
            └─ fixed reply

Stateless: the turn state is built fresh from this turn's input, so any replica can answer any message.
Earlier turns come only from the platform's chat history (`history.py`); the earlier trace is re-parsed from it
in code. No route pauses: every turn ends with a reply.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypedDict

import turns
from inputs import Incoming
from llm import TurnDeps


class ExplainerState(TypedDict, total=False):
    route: str                # this turn only
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part


Step = Callable[[dict, turns.Scope], Awaitable[dict]]
ROUTES: dict[str, Step] = {"explain": turns.explain_paste, "attach": turns.attach, "followup": turns.followup, "scope": turns.scope_check}


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class ExplainerAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        scope = turns.Scope(deps, incoming)
        state = {"route": "", "reply": "", "data": None, **turns.route({}, scope)}
        step = ROUTES.get(state["route"])
        if step is not None:
            state = {**state, **await step(state, scope)}
        return TurnResult(state.get("reply") or "", state.get("data"))

