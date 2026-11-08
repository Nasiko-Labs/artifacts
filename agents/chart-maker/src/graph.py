"""The chart-maker LangGraph.

    START → intake ─┬─ fixed reply / follow-up re-render / resumed column choice ──→ END
                    ├─ classify (1 call; + 1 repair call for a diagram) ─┬─ chart ──→ END
                    │                                                    ├─ diagram → END
                    │                                                    └─ pause ──→ END
                    └─ patch (1 call: a diagram edit) ─────────────────────────────→ END

A greeting never enters the graph (smalltalk.py). The dataset + chart spec, or the diagram graph +
the user's description, are checkpointed per A2A contextId, so "line chart", "top 3" or "add a step"
need no re-paste. The graph is compiled per turn because its nodes close over the turn's scope; the
checkpointer is shared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import prompts
import render
import smalltalk
import steps
from hitl import Pending
from inputs import Incoming
from intake import intake, patch_step
from llm import BudgetExhausted, MissingTraceparent, TurnDeps
from render import Reply
from scope import Scope

log = logging.getLogger("chart-maker.graph")


class ChartState(TypedDict, total=False):
    dataset: dict | None
    spec: dict | None
    graph: dict | None
    description: str | None
    route: str            # this turn


@dataclass(frozen=True)
class TurnResult:
    reply: Reply
    pending: Pending | None = None


class ChartMakerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps,
                   resume: tuple[Pending, str] | None = None) -> TurnResult:
        if resume is None and not incoming.file_text:
            canned = smalltalk.reply_to(incoming.text)
            if canned is not None:  # a greeting or small talk: no model call, state untouched
                return TurnResult(Reply(canned))
        scope = Scope(deps, incoming, resume)
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        await graph.ainvoke({"route": ""}, {"configurable": {"thread_id": context_id}}, durability="exit")
        if scope.pending is not None:
            return TurnResult(Reply(""), scope.pending)
        return TurnResult(scope.reply or render.message(prompts.COULD_NOT, scope.as_json))


def _build_graph(scope: Scope) -> StateGraph:
    async def intake_node(state: ChartState) -> dict:
        update, route = intake(dict(state), scope)
        return {**update, "route": route}

    async def classify_node(state: ChartState) -> dict:
        return {**await _guarded(scope, steps.classify_step(scope)), "route": ""}

    async def patch_node(state: ChartState) -> dict:
        return {**await _guarded(scope, patch_step(dict(state), scope)), "route": ""}

    graph = StateGraph(ChartState)
    graph.add_node("intake", intake_node)
    graph.add_node("classify", classify_node)
    graph.add_node("patch", patch_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["classify", "patch", END])
    graph.add_edge("classify", END)
    graph.add_edge("patch", END)
    return graph


async def _guarded(scope: Scope, step) -> dict:
    try:
        return await step
    except (TimeoutError, BudgetExhausted):
        log.warning("turn ran out of calls or time")
        return steps.say(scope, prompts.BUDGET_SPENT)
    except MissingTraceparent:
        return steps.say(scope, prompts.NO_TRACE_CONTEXT)
