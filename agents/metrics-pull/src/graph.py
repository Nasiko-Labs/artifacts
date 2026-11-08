"""The metrics-pull LangGraph.

    START -+- (a pause answered) -> resume -> END
           +- (a request) -------> pull ----> END     (flow.run: source -> read -> spec -> execute -> render)

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId and holds only
the last sheet (id, title, tab) and the last validated spec, so a follow-up ("as json", "now by plan",
"compare this week vs last") re-reads the same sheet fresh and refines the spec. No values are kept: every
request re-reads. The graph is compiled per turn because its nodes close over that turn's dependencies;
the checkpointer is shared.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import flow
import parse
import smalltalk
from deps import TurnDeps
from flow import TurnResult

__all__ = ["MetricsPullAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("metrics_pull.graph")

TURN_DEADLINE_SECS = 120
OUT_OF_TIME = "I ran out of time before finishing this pull. Try a smaller range or a single tab."


class PullState(TypedDict, total=False):
    request: dict
    pause: dict | None
    answer: str
    source: dict          # remembered: {"sheet": {...}, "tab": "..."}
    spec: dict            # remembered: the last validated spec


class MetricsPullAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps,
                   metadata: Mapping[str, Any] | None = None) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        request = parse.parse(query, metadata)
        return await self._invoke({"request": request.to_dict(), "pause": None, "answer": ""}, context_id, deps)

    async def resume(self, pause: Mapping[str, Any], answer: str, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._invoke({"request": {}, "pause": dict(pause), "answer": answer}, context_id, deps)

    async def _invoke(self, fresh: dict, context_id: str, deps: TurnDeps) -> TurnResult:
        # The turn's answer leaves through `outcome`, not the state: the table can be large and is never
        # checkpointed (every request re-reads the sheet anyway).
        outcome: list[TurnResult] = []
        graph = _build_graph(deps, outcome.append).compile(checkpointer=self._checkpointer)
        try:
            await asyncio.wait_for(graph.ainvoke(fresh, _config(context_id), durability="exit"), TURN_DEADLINE_SECS)
        except TimeoutError:
            log.warning("turn passed its %ss deadline", TURN_DEADLINE_SECS)
            return TurnResult(OUT_OF_TIME)
        return outcome[-1] if outcome else TurnResult(OUT_OF_TIME)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _memory(state: PullState) -> dict:
    return {"source": state.get("source") or {}, "spec": state.get("spec") or {}}


def _build_graph(deps: TurnDeps, emit: Callable[[TurnResult], None]) -> StateGraph:
    async def pull(state: PullState) -> dict:
        request = parse.Request.from_dict(state.get("request") or {"text": ""})
        result = await flow.run(deps, request, _memory(state))
        emit(result)
        return dict(result.remember)

    async def resume(state: PullState) -> dict:
        result = await flow.resume_pause(deps, state.get("pause") or {}, state.get("answer") or "", _memory(state))
        emit(result)
        return dict(result.remember)

    graph = StateGraph(PullState)
    graph.add_node("pull", pull)
    graph.add_node("resume", resume)
    graph.add_conditional_edges(START, lambda s: "resume" if s.get("pause") else "pull", ["pull", "resume"])
    graph.add_edge("pull", END)
    graph.add_edge("resume", END)
    return graph
