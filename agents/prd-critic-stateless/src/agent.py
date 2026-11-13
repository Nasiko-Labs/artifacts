"""One PRD-critic turn (stateless), as plain async routing around the crew:

    intake (code) ─┬─ review   -> crew: [locator ->] PRD critic -> skeptical review lead, then the rubric in code
                   ├─ followup -> a view of the review in history (0 calls), or review the PRD again
                   ├─ scope    -> one classify call: decline / ask for the PRD
                   └─ fixed reply (0 calls)

Nothing is kept between turns: the PRD and review are re-read from the conversation history the platform sends
(`recall.py`), so any replica can answer any message, and every turn ends the task.
"""

from __future__ import annotations

from dataclasses import dataclass

import recall
import turns
from inputs import Incoming
from llm import TurnDeps

ROUTES = {"review": turns.run_review, "followup": turns.followup, "scope": turns.scope_check}


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class CriticAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        prior = recall.read(incoming.history)
        scope = turns.Scope(deps, incoming, prior)
        state = turns.seed(prior)
        routed = turns.intake(state, scope)
        handler = ROUTES.get(routed.get("route") or "")
        result = await handler(state, scope) if handler else routed
        return TurnResult(result.get("reply") or "", result.get("data"))
