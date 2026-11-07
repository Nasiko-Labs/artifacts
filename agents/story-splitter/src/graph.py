"""The story-splitter LangGraph.

    START -> intake ─┬─ run    (outline -> slice -> check+size -> resplit L -> acceptance -> render) -> END
                     ├─ who    (Q3 answered: slice from the stored outline)                       -> END
                     ├─ refine (split / merge / drop / rename / move / max / first / json)         -> END
                     ├─ scope  (one classify call: decline / ask for the epic)                    -> END
                     └─ fixed reply / pause ──────────────────────────────────────────────────────> END

State is checkpointed per A2A contextId (thread_id): the epic, its constraints, the validated outline, the
checked stories and the envelope, so "split story 3" works from the stored result. The graph is compiled
per turn because its nodes close over that turn's input and dependencies; the checkpointer is shared. A pause
leaves the graph as a `pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake
import pauses
import refining
import turns
from inputs import Incoming
from llm import TurnDeps


class SplitterState(TypedDict, total=False):
    epic: str
    constraints: list[str]
    outline: dict             # Outline.to_dict(): the grounding set
    stories: list[dict]       # Story.to_state() in order
    skeleton: str             # key of the walking skeleton
    envelope: dict            # the last result
    epic_words: int
    max_stories: int
    route: str                # this turn only
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part
    pause: dict | None        # this turn only: pauses.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: pauses.Pending | None = None
    data: dict | None = None


class StorySplitterAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: pauses.Pending, incoming: Incoming, context_id: str,
                     deps: TurnDeps) -> TurnResult:
        """An answer to one of this agent's questions, on the paused task."""
        route = intake.plan_resume(pauses.interpret(pending, incoming.text), pending)
        resumed = Incoming(incoming.text, incoming.policy, incoming.wants_json or pending.wants_json)
        return await self._run(turns.Scope(deps, resumed, forced=route), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", pauses.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: SplitterState) -> dict:
        return turns.intake(state, scope)

    async def run_node(state: SplitterState) -> dict:
        return await turns.run(state, scope)

    async def who_node(state: SplitterState) -> dict:
        return await turns.who(state, scope)

    async def refine_node(state: SplitterState) -> dict:
        return await refining.refine_turn(state, scope)

    async def scope_node(state: SplitterState) -> dict:
        return await turns.scope_check(state, scope)

    graph = StateGraph(SplitterState)
    for name, node in (("intake", intake_node), ("run", run_node), ("who", who_node), ("refine", refine_node),
                       ("scope", scope_node)):
        graph.add_node(name, node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["run", "who", "refine", "scope", END])
    for name in ("run", "who", "refine", "scope"):
        graph.add_edge(name, END)
    return graph
