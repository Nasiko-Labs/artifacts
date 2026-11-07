"""The config-explainer LangGraph.

    START → intake ─┬─ fixed reply / code-only reply (cron, parse error, follow-up probe) ─→ END
                    ├─ explain (1 LLM call + ≤ 1 repair) ─────────────────────────────────→ END
                    ├─ followup (1 LLM call: a question about the stored config) ─────────→ END
                    ├─ screen (1 LLM call: no config pasted → off topic or Q1) ────────────→ END
                    └─ pause Q1 (kind ambiguous) ──────────────────────────────────────────→ END

A greeting never enters the graph (smalltalk.py). The last explained config (kind + text) is checkpointed
per A2A contextId, so follow-ups work on any replica. The graph is compiled per turn because its nodes close
over that turn's dependencies; the checkpointer is shared."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import prompts
import smalltalk
from llm import Budget, BudgetExhausted, MissingTraceparent, TurnDeps
from turns import Request, Turn

log = logging.getLogger("config_explainer.graph")


class ExplainState(TypedDict, total=False):
    last: dict | None           # {"kind", "config", "certain"} of the last explained config
    reply: str                  # this turn
    data: dict | None           # this turn: the envelope
    pending: dict | None        # this turn: the pause to raise, if any
    route: str                  # this turn


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None
    pending: dict | None = None
    has_config: bool = False


class ConfigExplainerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, request: Request, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(request.text) if request.forced is None and not request.pauses else None
        if canned is not None:  # a greeting or small talk: no LLM call, state untouched
            return TurnResult(canned)
        turn = Turn(deps, request, Budget())
        graph = _build_graph(turn).compile(checkpointer=self._checkpointer)
        fresh = {"reply": "", "data": None, "pending": None, "route": ""}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or prompts.COULD_NOT_EXPLAIN, final.get("data"), final.get("pending"),
                          turn.has_config)


def _build_graph(turn: Turn) -> StateGraph:
    async def intake(state: ExplainState) -> dict:
        return turn.intake(state.get("last"))

    async def explain(state: ExplainState) -> dict:
        return await _guarded(turn.explain())

    async def followup(state: ExplainState) -> dict:
        return await _guarded(turn.followup(state.get("last") or {}))

    async def screen(state: ExplainState) -> dict:
        return await _guarded(turn.screen())

    graph = StateGraph(ExplainState)
    for name, node in (("intake", intake), ("explain", explain), ("followup", followup), ("screen", screen)):
        graph.add_node(name, node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", _route, ["explain", "followup", "screen", END])
    for name in ("explain", "followup", "screen"):
        graph.add_edge(name, END)
    return graph


def _route(state: ExplainState) -> str:
    return state.get("route") or END


async def _guarded(step) -> dict:
    try:
        return await step
    except MissingTraceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    except (TimeoutError, BudgetExhausted):
        log.warning("turn ran out of calls or time")
        return {"reply": prompts.BUDGET_SPENT, "route": ""}
