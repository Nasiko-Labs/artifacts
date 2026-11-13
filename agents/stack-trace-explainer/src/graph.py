"""The stack-trace-explainer LangGraph.

    START -> intake ─┬─ explain (parse -> explain per trace -> check -> repair? -> render) -> END
                     ├─ attach   (pasted code for the stored trace: explain again)        -> END
                     ├─ followup (one call over the stored result)                       -> END
                     ├─ scope    (no error text: decline, or pause "Paste the error")     -> END
                     └─ fixed reply / json re-render ───────────────────────────────────> END

State is checkpointed per A2A contextId (thread_id): the parsed traces, the code blocks and the last envelope, so
follow-ups work on any replica. The graph is compiled per turn because its nodes close over that turn's input; the
checkpointer is shared. A pause leaves the graph as a `pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import intake
import prompts
import turns
from inputs import Incoming
from llm import TurnDeps


class ExplainerState(TypedDict, total=False):
    traces: list[dict]        # parsed traces (Trace.to_dict), paste order
    primary: int
    blocks: list[dict]        # pasted code blocks (CodeBlock.to_dict)
    words: str                # the user's own words from the turn that pasted the trace
    envelope: dict            # the last result
    last_reply: str           # the last answer shown (explanation or follow-up): what a follow-up builds on
    route: str                # this turn only
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part
    pause: dict | None        # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class ExplainerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """The answer to "Paste the error", on the paused task: Cancel stops; any text is taken as the trace."""
        answer = hitl.answer_text(incoming.text)
        if answer is None:
            return TurnResult(prompts.CANCELLED_REPLY)
        pasted = intake.resume_paste(pending.request, answer)
        forced = intake.Route("explain", paste=pasted, trust_libraries=intake.trusts_libraries(pasted.words))
        resumed = Incoming(answer, incoming.policy, incoming.wants_json or pending.wants_json, incoming.notes)
        return await self._run(turns.Scope(deps, resumed, forced=forced), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: ExplainerState) -> dict:
        return turns.route(state, scope)

    async def explain_node(state: ExplainerState) -> dict:
        return await turns.explain_paste(state, scope)

    async def attach_node(state: ExplainerState) -> dict:
        return await turns.attach(state, scope)

    async def followup_node(state: ExplainerState) -> dict:
        return await turns.followup(state, scope)

    async def scope_node(state: ExplainerState) -> dict:
        return await turns.scope_check(state, scope)

    graph = StateGraph(ExplainerState)
    graph.add_node("intake", intake_node)
    nodes = {"explain": explain_node, "attach": attach_node, "followup": followup_node, "scope": scope_node}
    for name, node in nodes.items():
        graph.add_node(name, node)
        graph.add_edge(name, END)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, [*nodes, END])
    return graph
