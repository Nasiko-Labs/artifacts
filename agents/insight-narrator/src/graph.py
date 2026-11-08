"""The insight-narrator LangGraph.

    START → intake ─┬─ narrate (parse → facts → write → check → lint → render) ─→ END
                    ├─ follow  (re-render / longer / rewrite / question)        ─→ END
                    ├─ scope   (one call: off-topic or pause)                   ─→ END
                    └─ fixed reply / pause ────────────────────────────────────→ END

State is checkpointed per A2A contextId (thread_id): the facts, the audience/length asked for and the
checked envelope, so follow-ups work from the stored result. The graph is compiled per turn because its
nodes close over that turn's input and dependencies; the checkpointer is shared. A pause leaves the graph as
a `pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import intake
import turns
from inputs import Incoming
from llm import TurnDeps


class NarratorState(TypedDict, total=False):
    facts: dict          # FactSet.to_dict(): what every follow-up draws from
    ask: dict            # audience, length and the user's instruction
    parsing: dict        # parse flags behind the confidence
    envelope: dict       # the checked result
    source: str          # the user's input (capped), only to allow links that appeared in it
    route: str           # this turn only
    reply: str           # this turn only
    pause: dict | None   # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class NarratorAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id, None)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to this agent's question, on the paused task."""
        forced = intake.plan_resume(hitl.interpret(pending, incoming.text))
        return await self._run(turns.Scope(deps, incoming), context_id, forced)

    async def _run(self, scope: turns.Scope, context_id: str, forced: intake.Plan | None) -> TurnResult:
        graph = _build_graph(scope, forced).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pause = hitl.Pending.from_metadata(final.get("pause"))
        return TurnResult(final.get("reply") or "", pause, scope.data)


def _build_graph(scope: turns.Scope, forced: intake.Plan | None) -> StateGraph:
    async def intake_node(state: NarratorState) -> dict:
        plan = forced or intake.plan_turn(scope.incoming, bool(state.get("envelope")))
        scope.plan = plan
        scope.notes.extend(plan.notes)
        if plan.route == "pause":
            return {"route": "", "pause": plan.pending.to_metadata()}
        if plan.route == "reply":
            return {"route": "", "reply": "\n\n".join([plan.reply, *plan.notes])}
        return {"route": plan.route}

    async def narrate_node(state: NarratorState) -> dict:
        return await turns.narrate(state, scope)

    async def follow_node(state: NarratorState) -> dict:
        return await turns.follow(state, scope)

    async def scope_node(state: NarratorState) -> dict:
        return await turns.scope_check(state, scope)

    graph = StateGraph(NarratorState)
    for name, node in (("intake", intake_node), ("narrate", narrate_node), ("follow", follow_node),
                       ("scope", scope_node)):
        graph.add_node(name, node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["narrate", "follow", "scope", END])
    for name in ("narrate", "follow", "scope"):
        graph.add_edge(name, END)
    return graph
