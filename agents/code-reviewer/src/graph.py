"""The code-reviewer LangGraph.

    START -> intake ─┬─ run (parse -> review map -> [spec] -> check -> merge -> render) -> END
                     ├─ followup (re-render, json, explain / fix one comment)          -> END
                     ├─ scope (one classify call: decline / ask for the diff)          -> END
                     └─ fixed reply / pause ─────────────────────────────────────────> END

State is checkpointed per A2A contextId (thread_id): the diff text, the spec/standards/focus and the last
envelope, so follow-ups work on any replica. The graph is compiled per turn because its nodes close over that
turn's input and dependencies; the checkpointer is shared. A pause leaves the graph as a `pause` value; its
state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import prompts
import turns
from inputs import Incoming
from llm import TurnDeps


class ReviewState(TypedDict, total=False):
    source: str               # the diff text as reviewed
    asks: dict                # spec, standards, focus
    truncated: bool
    envelope: dict            # the last result
    route: str                # this turn only
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part
    pause: dict | None        # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class CodeReviewerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """The answer to "Paste the diff", on the paused task."""
        text = hitl.answer_text(pending, incoming.text)
        if text is None:
            return TurnResult(prompts.CANCELLED)
        resumed = Incoming(text, incoming.policy, incoming.wants_json or pending.wants_json, incoming.notes)
        return await self._run(turns.Scope(deps, resumed, resumed=True), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: ReviewState) -> dict:
        return turns.intake(state, scope)

    async def run_node(state: ReviewState) -> dict:
        return await turns.run(state, scope)

    async def followup_node(state: ReviewState) -> dict:
        return await turns.followup(state, scope)

    async def scope_node(state: ReviewState) -> dict:
        return await turns.scope_check(state, scope)

    graph = StateGraph(ReviewState)
    graph.add_node("intake", intake_node)
    graph.add_node("run", run_node)
    graph.add_node("followup", followup_node)
    graph.add_node("scope", scope_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["run", "followup", "scope", END])
    for node in ("run", "followup", "scope"):
        graph.add_edge(node, END)
    return graph
