"""The kudos-collector LangGraph.

    START -> intake -+- collect (records: attribute -> write -> check -> group -> render) -> END
                     +- followup (filters/views in code, or one scope call)            -> END
                     +- canned reply (greeting)                                         -> END

State is checkpointed per A2A contextId (thread_id): the parsed records, attributions, written examples and
options, so follow-ups re-group in code on any replica. The graph is compiled per turn because its nodes close
over that turn's input and dependencies; the checkpointer is shared. A pause leaves the graph as
`TurnResult.pause`; what to resume from lives on the A2A task's metadata."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import followup
import parse_records
import pauses
import prompts
import smalltalk
import turns
from inputs import Incoming
from llm import TurnDeps
from pipeline import Stored
from turns import TurnResult


class KudosState(TypedDict, total=False):
    stored: dict      # Stored.to_state()
    route: str        # this turn only


class _Scope:
    """One turn's input, dependencies and output (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, answer: pauses.Answer | None = None,
                 pending: pauses.Pending | None = None):
        self.deps, self.incoming, self.answer, self.pending = deps, incoming, answer, pending
        self.parsed = parse_records.Parsed()
        self.out = TurnResult("")


class KudosAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: pauses.Pending, incoming: Incoming, context_id: str,
                     deps: TurnDeps) -> TurnResult:
        """An answer to one of this agent's questions, on the paused task."""
        answer = pauses.interpret(pending, incoming.text)
        return await self._run(_Scope(deps, incoming, answer, pending), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        await graph.ainvoke({"route": ""}, {"configurable": {"thread_id": context_id}}, durability="exit")
        return scope.out


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(state: KudosState) -> dict:
        return _intake(scope)

    async def collect_node(state: KudosState) -> dict:
        prior = Stored.from_state(state.get("stored"))
        if scope.answer is not None:
            scope.out = await _resumed(scope, prior)
        else:
            add = bool(turns.ADD.search(scope.parsed.instruction))
            scope.out = await turns.run_paste(scope.incoming, scope.deps, scope.parsed, prior, add=add)
        return {"stored": scope.out.stored} if scope.out.stored else {}

    async def followup_node(state: KudosState) -> dict:
        scope.out = await followup.respond(scope.incoming, scope.deps, Stored.from_state(state.get("stored")))
        return {}

    graph = StateGraph(KudosState)
    graph.add_node("intake", intake_node)
    graph.add_node("collect", collect_node)
    graph.add_node("followup", followup_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["collect", "followup", END])
    graph.add_edge("collect", END)
    graph.add_edge("followup", END)
    return graph


def _intake(scope: _Scope) -> dict:
    if scope.answer is not None:
        return {"route": "collect"}
    canned = smalltalk.reply_to(scope.incoming.text)
    if canned is not None:
        scope.out = turns.reply(canned, scope.incoming.notes)
        return {"route": ""}
    scope.parsed = parse_records.parse(scope.incoming.text, scope.deps.today)
    return {"route": "collect" if scope.parsed.records else "followup"}


async def _resumed(scope: _Scope, prior: Stored | None) -> TurnResult:
    answer, pending = scope.answer, scope.pending
    if answer.action == "cancel":
        return turns.reply(prompts.CANCELLED)
    text = pending.text
    if answer.action == "aliases":
        text += "\nAliases:\n" + "\n".join(f"{handle} = {name}" for handle, name in answer.aliases)
    parsed = parse_records.parse(text, scope.deps.today)
    incoming = Incoming(text, scope.incoming.context, (), scope.incoming.wants_json)
    return await turns.run_paste(incoming, scope.deps, parsed, prior, add=pending.add,
                                 use_recent=answer.action == "use_recent", names_given=answer.action == "aliases")
