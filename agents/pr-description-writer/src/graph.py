"""The PR description writer LangGraph.

    START -> intake ─┬─ draft    (digest -> 1 draft call -> checks -> title -> render) -> END
                     ├─ restyle  (1 call over the stored draft -> the same checks)     -> END
                     ├─ classify (no diff, no draft: decline or pause for the diff)     -> END
                     └─ fixed reply / JSON re-render / split advice / pause ──────────> END

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId (thread_id): the
facts, the checked draft, the user's context, the template, the style and the diff, so follow-ups work on any
replica without recomputing. The graph is compiled per turn because its nodes close over that turn's input and
dependencies; the checkpointer is shared. A pause leaves the graph as a `pause` value; its state lives on the
A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import smalltalk
import turns
from inputs import Incoming
from llm import TurnDeps


class WriterState(TypedDict, total=False):
    facts: dict
    draft: dict
    context: str
    template: str
    style: dict
    diff: str
    envelope: dict
    run: dict
    route: str            # this turn only
    reply: str            # this turn only
    data: dict | None     # this turn only: the envelope to attach as a data part
    pause: dict | None    # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class WriterAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:  # a greeting or small talk: no LLM call, state untouched
            return TurnResult(canned)
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to the paste-the-diff question, on the paused task."""
        answer = hitl.interpret(pending, incoming.text)
        if answer.action != "run":
            return TurnResult(answer.reply)
        resumed = Incoming(answer.text, incoming.policy, incoming.wants_json or pending.wants_json)
        return await self._run(turns.Scope(deps, resumed), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: WriterState) -> dict:
        return turns.intake(state, scope)

    async def draft_node(state: WriterState) -> dict:
        return await turns.draft(state, scope)

    async def restyle_node(state: WriterState) -> dict:
        return await turns.restyle(state, scope)

    async def classify_node(state: WriterState) -> dict:
        return await turns.classify(state, scope)

    graph = StateGraph(WriterState)
    graph.add_node("intake", intake_node)
    graph.add_node("draft", draft_node)
    graph.add_node("restyle", restyle_node)
    graph.add_node("classify", classify_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["draft", "restyle", "classify", END])
    for node in ("draft", "restyle", "classify"):
        graph.add_edge(node, END)
    return graph
