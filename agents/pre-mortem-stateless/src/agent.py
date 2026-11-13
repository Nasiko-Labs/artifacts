"""One pre-mortem turn (stateless), as plain async routing around the crew:

    intake (code) ─┬─ run      -> crew: failure-mode finder -> skeptical reviewer (2 calls), then code checks
                   ├─ followup -> re-render (0 calls), deep dive (1 call), or add context (2 calls)
                   ├─ scope    -> one classify call: decline / ask
                   └─ fixed reply (0 calls)

Nothing is kept between turns: the plan and pre-mortem are re-read from the conversation history the platform
sends (`recall.py`), so any replica can answer any message, and every turn ends the task.
"""

from __future__ import annotations

from dataclasses import dataclass

import recall
import turns
from inputs import Incoming
from llm import TurnDeps

ROUTES = {"run": turns.run, "followup": turns.followup, "scope": turns.scope_check}


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class PreMortemAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        prior = recall.read(incoming.history)
        scope = turns.Scope(deps, incoming, prior)
        state = turns.seed(prior)
        routed = turns.intake(state, scope)
        handler = ROUTES.get(routed.get("route") or "")
        result = await handler(state, scope) if handler else routed
        return TurnResult(result.get("reply") or "", result.get("data"))
