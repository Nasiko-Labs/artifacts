"""The task-breakdown-stateless turn, as plain async steps over LangChain runnables (the model calls are LCEL
chains in llm.py; there is no graph framework).

    intake ─┬─ plan   (map chunks -> plan -> repair, in planning.py) ─┐
            ├─ revise (revise -> reconcile ids -> repair) ────────────┼─> render
            ├─ render (an envelope loaded as the plan) ───────────────┘
            ├─ query  (answered in code from the recovered plan)
            └─ fixed reply

Stateless: every step works on this turn's input only. The plan a follow-up works on is the one recovered from
the platform's history slice (recall.py), so any replica can answer any message. Nothing pauses: every turn ends
with a reply.
"""

from __future__ import annotations

from dataclasses import dataclass

import turns
from inputs import Incoming
from llm import TurnDeps


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class BreakdownAgent:
    """Holds nothing between turns."""

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """`context_id` is only for logs and spans; no state is keyed by it."""
        scope = turns.Scope(deps, incoming)
        step = turns.intake(scope)
        if step.route in ("plan", "revise"):
            step = await (turns.plan if step.route == "plan" else turns.revise)(scope)
        if step.route == "render":
            step = turns.render_step(scope)
        elif step.route == "query":
            step = turns.query(scope)
        return TurnResult(step.reply, step.data)
