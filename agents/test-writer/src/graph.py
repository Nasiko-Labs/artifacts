"""The test-writer LangGraph.

    START -> intake ─┬─ run (census -> write batches -> check -> repair -> assemble -> render) -> END
                     ├─ followup (json / more callables / one revise call)                      -> END
                     ├─ scope (one classify call: decline / ask for the code)                  -> END
                     └─ fixed reply / pause ──────────────────────────────────────────────────> END

State is checkpointed per A2A contextId (thread_id): the pasted source files, the options, the checked tests
and the envelope, so follow-ups work on any replica. The graph is compiled per turn because its nodes close
over that turn's input and dependencies; the checkpointer is shared. A pause leaves the graph as a `pause`
value; its state lives on the A2A task.
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


class TestWriterState(TypedDict, total=False):
    files: list[dict]          # the pasted source files
    instruction: str
    existing: str              # the user's existing tests (style sample)
    framework: str
    module: str
    selected: list[str]        # callables tested
    known: list[str]           # every callable parsed
    remaining: list[str]       # over the per-turn cap
    tests: list[dict]
    helpers: list[str]
    imports: list[str]
    envelope: dict
    route: str                 # this turn only
    reply: str                 # this turn only
    data: dict | None          # this turn only: the envelope to attach as a data part
    pause: dict | None         # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class TestWriterAgent:
    __test__ = False  # not a pytest test class

    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to this agent's question, on the paused task."""
        answer = hitl.interpret(pending, incoming.text)
        route = intake.plan_resume(answer)
        resumed = Incoming(answer.text or incoming.text, incoming.policy, incoming.wants_json or pending.wants_json)
        return await self._run(turns.Scope(deps, resumed, forced=route), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: TestWriterState) -> dict:
        return turns.intake(state, scope)

    async def run_node(state: TestWriterState) -> dict:
        return await turns.run(state, scope)

    async def followup_node(state: TestWriterState) -> dict:
        return await turns.followup(state, scope)

    async def scope_node(state: TestWriterState) -> dict:
        return await turns.scope_check(state, scope)

    graph = StateGraph(TestWriterState)
    graph.add_node("intake", intake_node)
    graph.add_node("run", run_node)
    graph.add_node("followup", followup_node)
    graph.add_node("scope", scope_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["run", "followup", "scope", END])
    for node in ("run", "followup", "scope"):
        graph.add_edge(node, END)
    return graph
