"""The reply-drafter LangGraph, stateless.

    START -> intake (code) -+- fixed reply (tone saved, envelope re-render, sum, no trace,
                            |               "what should it say?", "paste it again") ----------> END
                            +- compose: draft(1) -> check(code) -> [repair(1)] -> render --------> END

A greeting never enters the graph (smalltalk.py). There is no checkpointer: the graph's starting state
(measured style, the incoming message, the user's words, the current reply) is rebuilt for each turn from
the platform's history block (`recall.py`), so any replica can answer any message. Nothing ever pauses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import envelope as env
import inputs as i
import prompts
import recall
import render
import smalltalk
from drafting import compose, target
from llm import Budget, BudgetExhausted, TurnDeps
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
    route: str                 # this turn


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class ReplyDrafterAgent:
    async def turn(self, request: i.Request, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(request.text)
        if canned is not None:  # a greeting or small talk: no LLM call
            return TurnResult(canned)
        scope = _Scope(deps, request)
        graph = _build_graph(scope).compile()
        start = {**recall.state_from(request.history), "out": "", "data": None, "route": ""}
        final = await graph.ainvoke(start)
        return TurnResult(final.get("out") or prompts.COULD_NOT_DRAFT, data=final.get("data"))


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
    if not scope.deps.traceparent:
        return {"out": prompts.NO_TRACE_CONTEXT, "route": ""}
    return {**plan.updates, "route": "compose"}


async def _compose(scope: _Scope) -> dict:
    plan = scope.plan
    job = plan.job
    result = await compose(scope.deps, scope.budget, job)
    if result.status == "off_topic":
        return {"out": smalltalk.OFF_TOPIC_REPLY}
    if result.status == "needs_info":
        if job.incoming is None and job.current is None:
            return {"out": prompts.NEED_INCOMING}
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
