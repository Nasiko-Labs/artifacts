"""One PRD-writer turn (stateless), as plain async routing around the crew:

    intake (code) ─┬─ run     -> new PRD: crew fact extractor -> PRD author -> PRD editor (3 calls), then code
                   │             revision: crew extractor -> reviser on the PRD last shown (2 calls); only when
                   │             it is not in history: extractor -> author -> reviser on the brief (3 calls)
                   │             short unclear message: one scope call
                   ├─ command -> the PRD rebuilt by the crew (3 calls), then re-render / drop / json in code
                   └─ fixed reply (0 calls)

Nothing is kept between turns: earlier work comes from the platform's chat history (`recall.Prior`), so any
replica can serve any message, and no turn ever pauses.
"""

from __future__ import annotations

from dataclasses import dataclass

import turns
from inputs import Incoming
from llm import TurnDeps

ROUTES = {"run": turns.run_node, "command": turns.command_node}


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class PrdWriterAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        scope = turns.Scope(deps, incoming)
        routed = turns.intake_node(scope)
        handler = ROUTES.get(routed.get("route") or "")
        result = await handler(scope) if handler else routed
        return TurnResult(result.get("reply") or "", result.get("data"))
