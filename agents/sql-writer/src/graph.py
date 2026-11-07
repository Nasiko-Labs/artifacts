"""The sql-writer LangGraph.

    START -> intake ─┬─ write    (generate -> validate -> repair* -> lint -> render)            -> END
                     ├─ optimize (lint + EXPLAIN -> rewrite -> validate -> repair* -> compare)   -> END
                     ├─ followup (json, sql only, pretty, one line, transpile; "show the rows")  -> END
                     ├─ classify (one call: decline, or ask for the schema)                      -> END
                     └─ simple   (canned reply, write decline, schema stored, schema pause)      -> END

State is checkpointed per A2A contextId (thread_id): the parsed schema, the dialect, the last envelope and a
declined write request, so follow-ups work from the stored result on any replica. The graph is compiled per turn
because its nodes close over that turn's input; the checkpointer is shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import intake
import turn_followup
import turns
from inputs import Incoming
from llm import TurnDeps
from schema import Schema

ROUTE_NODE = {"write": "write", "optimize": "optimize", "followup": "followup", "classify": "classify"}


class WriterState(TypedDict, total=False):
    schema: dict
    dialect: dict
    envelope: dict
    pending_write: str
    question: str
    route: str                # this turn only
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part
    pause: dict | None        # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class SqlWriterAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to a paused question (schema, column or join), on the paused task."""
        config = {"configurable": {"thread_id": context_id}}
        snapshot = await self._compiled(turns.Scope(deps, incoming)).aget_state(config)
        schema = Schema.from_dict((snapshot.values or {}).get("schema"))
        answer = hitl.interpret(pending, incoming.text, schema)
        if answer.action != "run":
            return TurnResult(answer.reply)
        resumed = Incoming(answer.text, incoming.policy, incoming.wants_json or pending.wants_json, incoming.notes)
        return await self._run(turns.Scope(deps, resumed, asked=pending.asked), context_id)

    def _compiled(self, scope: turns.Scope):
        return _build_graph(scope).compile(checkpointer=self._checkpointer)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await self._compiled(scope).ainvoke(fresh, {"configurable": {"thread_id": context_id}},
                                                    durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")), final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    def intake_node(state: WriterState) -> dict:
        scope.route = intake.plan_turn(scope.incoming, dict(state))
        return {"route": ROUTE_NODE.get(scope.route.kind, "simple")}

    async def write_node(state: WriterState) -> dict:
        return await turns.write(dict(state), scope)

    async def optimize_node(state: WriterState) -> dict:
        return await turns.run_optimize(dict(state), scope)

    async def followup_node(state: WriterState) -> dict:
        return await turn_followup.run(dict(state), scope)

    async def classify_node(state: WriterState) -> dict:
        return await turns.classify(dict(state), scope)

    def simple_node(state: WriterState) -> dict:
        return turns.simple(dict(state), scope)

    graph = StateGraph(WriterState)
    nodes = {"intake": intake_node, "write": write_node, "optimize": optimize_node, "followup": followup_node,
             "classify": classify_node, "simple": simple_node}
    for name, fn in nodes.items():
        graph.add_node(name, fn)
    graph.add_edge(START, "intake")
    targets = [n for n in nodes if n != "intake"]
    graph.add_conditional_edges("intake", lambda s: s.get("route") or "simple", targets)
    for name in targets:
        graph.add_edge(name, END)
    return graph
