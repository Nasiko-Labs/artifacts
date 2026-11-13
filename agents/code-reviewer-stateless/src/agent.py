"""One code-review turn (stateless), as plain async routing around the crew:

    intake (code) ─┬─ run      -> crew: reviewer -> verifier [-> spec checker] (2-3 calls), then code checks,
                   │              merge and render
                   ├─ followup -> re-review the recalled diff (views / json, same crew), or explain / fix one
                   │              comment of the recalled review (one-step crew, 1 call)
                   ├─ scope    -> one classify call: decline / ask for the diff
                   └─ fixed reply (0 calls)

Nothing is kept between turns: earlier work comes from the platform's chat history (`recall.Prior`). No turn
ever pauses, so any replica can serve any message.
"""

from __future__ import annotations

from dataclasses import dataclass

import turns
from inputs import Incoming
from llm import TurnDeps

ROUTES = {"run": turns.run, "followup": turns.followup, "scope": turns.scope_check}


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class CodeReviewerAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        scope = turns.Scope(deps, incoming)
        routed = turns.intake(scope)
        handler = ROUTES.get(routed.get("route") or "")
        result = await handler(scope) if handler else routed
        return TurnResult(result.get("reply") or "", result.get("data"))
