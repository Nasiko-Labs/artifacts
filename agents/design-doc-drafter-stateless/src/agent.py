"""One design-doc turn (stateless), as plain async routing around the crew:

    intake (code) ─┬─ draft    -> crew: requirements analyst -> software architect -> technical writer (3 calls)
                   ├─ followup -> the same crew on the recalled requirements with the user's request (3 calls)
                   ├─ scope    -> one classify call: decline / ask what you're designing
                   └─ fixed reply (0 calls)

Nothing is kept between turns: earlier work comes from the platform's chat history (`recall.Prior`), so any
replica can serve any message, and no turn ever pauses.
"""

from __future__ import annotations

from dataclasses import dataclass

import turns
from inputs import Incoming
from llm import TurnDeps

ROUTES = {"draft": turns.draft, "followup": turns.followup, "scope": turns.scope_check}


@dataclass(frozen=True)
class TurnResult:
    replies: tuple[str, ...]
    data: dict | None = None


class DesignDocAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        scope = turns.Scope(deps, incoming)
        routed = turns.intake(scope)
        handler = ROUTES.get(routed.get("route") or "")
        result = await handler(scope) if handler else routed
        return TurnResult(tuple(result.get("replies") or ()), result.get("data"))
