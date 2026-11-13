"""The runbook-generator-stateless turn: plain async steps around LangChain runnables (see `llm.py`).

    intake ─┬─ run (extract -> check -> draft -> lint -> repair -> render)
            ├─ followup (rebuild from the history's source, then re-render / fill / add)
            ├─ scope (one classify call: decline / ask for the source)
            └─ fixed reply

Stateless: the turn state is built fresh from this turn's input, so any replica can answer any message.
Earlier turns come only from the platform's chat history (`history.py`). No route pauses: every turn ends
with a reply.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import turns
from inputs import Incoming
from llm import TurnDeps

Step = Callable[[dict, turns.Scope], Awaitable[dict]]
ROUTES: dict[str, Step] = {"run": turns.run, "followup": turns.followup, "scope": turns.scope_check}


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class RunbookAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        scope = turns.Scope(deps, incoming)
        state = {"route": "", "reply": "", "data": None, **turns.intake({}, scope)}
        step = ROUTES.get(state["route"])
        if step is not None:
            state = {**state, **await step(state, scope)}
        return TurnResult(state.get("reply") or "", state.get("data"))
