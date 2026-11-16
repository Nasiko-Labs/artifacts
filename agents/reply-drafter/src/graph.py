"""The reply-drafter LangGraph.

    START -> intake (code) -+- fixed reply (tone saved, envelope re-render, sum, no trace) --> END
                            +- pause (incoming / purpose / channel) ---------------------------> END
                            +- compose: draft(1) -> check(code) -> [repair(1)] -> render --------> END

A greeting never enters the graph (smalltalk.py). State (measured style, the incoming message, the user's
words, the current reply and its envelope) is checkpointed per A2A contextId, so revisions and the style
survive across turns and replicas. The examples themselves are never stored: only their metrics. The graph
is compiled per turn because its nodes close over that turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import envelope as env
import inputs as i
import mcp_tools
import prompts
import render
import smalltalk
from drafting import compose, target
from llm import Budget, BudgetExhausted, TurnDeps
from questions import MAX_PAUSES, Pending, question
from turns import Plan, plan_turn

log = logging.getLogger("reply_drafter.graph")


class ReplyState(TypedDict, total=False):
    metrics: dict
    sign_off_as: str
    incoming: dict | None
    channel: str
    words: list[str]
    reply: dict | None
    envelope: dict | None
    out: str                   # this turn
    data: dict | None          # this turn's envelope
    pending: dict | None       # this turn: the pause to raise, if any
    route: str                 # this turn


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pending: Pending | None = None
    hitl: dict | None = None
    data: dict | None = None


class ReplyDrafterAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, request: i.Request, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(request.text) if not request.slots else None
        if canned is not None:  # a greeting or small talk: no LLM call, state untouched
            return TurnResult(canned)
        scope = _Scope(deps, request)
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"out": "", "data": None, "pending": None, "route": ""}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pending = Pending.from_metadata(final.get("pending"))
        if pending is None:
            return TurnResult(final.get("out") or prompts.COULD_NOT_DRAFT, data=final.get("data"))
        text, hitl = question(pending.slot)
        return TurnResult(text, pending, hitl)


class _Scope:
    """One turn's inputs and scratch (never persisted)."""

    def __init__(self, deps: TurnDeps, request: i.Request):
        self.deps = deps
        self.request = request
        self.budget = Budget()
        self.plan: Plan | None = None
        self.wants_json = request.wants_json or i.wants_json(request.text)


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake(state: ReplyState) -> dict:
        return _intake(state, scope)

    async def draft(state: ReplyState) -> dict:
        try:
            return await _compose(scope)
        except (TimeoutError, BudgetExhausted):
            log.warning("turn ran out of calls or time")
            return {"out": prompts.BUDGET_SPENT}

    graph = StateGraph(ReplyState)
    graph.add_node("intake", intake)
    graph.add_node("compose", draft)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["compose", END])
    graph.add_edge("compose", END)
    return graph


def _intake(state: ReplyState, scope: _Scope) -> dict:
    plan = plan_turn(state, scope.request)
    scope.plan = plan
    if plan.kind == "fixed":
        return {**plan.updates, "out": plan.reply, "route": ""}
    if plan.kind == "pause":
        return {**plan.updates, **_pause(scope.request, plan.slot), "route": ""}
    if not scope.deps.traceparent:
        return {"out": prompts.NO_TRACE_CONTEXT, "route": ""}
    return {**plan.updates, "route": "compose"}


def _pause(request: i.Request, slot: str) -> dict:
    if request.pauses >= MAX_PAUSES:
        return {"out": f"I still need the {slot} to write this reply. Tell me and I'll draft it."}
    pending = Pending(slot, request.text, request.org, dict(request.slots), request.pauses + 1, request.wants_json)
    return {"pending": pending.to_metadata()}


async def _compose(scope: _Scope) -> dict:
    plan, request = scope.plan, scope.request
    job = plan.job
    result = await compose(scope.deps, scope.budget, job)
    if result.status == "off_topic":
        # Not a reply-drafting request: try the operator's attached MCP tools, else a generic reply.
        out = await mcp_tools.offer_or_decline(scope.deps, request.text, smalltalk.INTRO)
        return {"out": out}
    if result.status == "needs_info":
        if job.incoming is None and job.current is None:
            return _pause(request, "incoming")
        return {"out": prompts.COULD_NOT_DRAFT}
    if result.status != "ok" or result.reply is None:
        return {"out": prompts.COULD_NOT_DRAFT}
    reply, tgt = result.reply, target(job)
    notes = env.tone_notes(job.metrics, tgt, reply)
    level = env.confidence(job.metrics, reply, tgt, result.code_fixed, result.ungrounded_after_repair)
    data = env.build(reply, job.channel, tgt.sender, job.metrics, notes, level)
    extra = [*plan.notes, *result.notes] + ([] if job.metrics.measured else [render.EXAMPLES_HINT])
    out = env.fenced(data) if scope.wants_json else render.reply_text(data, job.metrics, extra)
    return {"reply": reply.to_dict(), "envelope": data, "data": data, "out": out}
