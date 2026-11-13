"""The summarizer LangGraph, stateless.

    START -> intake ─┬─ summarize -> render              -> END
                     ├─ ask (one quote-checked question) -> END
                     └─ fixed reply ─────────────────────> END

There is no checkpointer: the graph's state lives for one turn and is built from that turn's input alone.
Earlier turns reach this agent only through the platform's history block, which `intake` reads (via
`recall`). Nothing pauses: every turn ends with a reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import followup
import intake
import prompts
import render
import smalltalk
import summarize as summarize_step
from inputs import Incoming, word_count
from llm import TurnDeps


class SummaryState(TypedDict, total=False):
    route: str
    reply: str


@dataclass(frozen=True)
class TurnResult:
    reply: str


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps, self.incoming = deps, incoming
        self.plan: intake.Plan | None = None


class SummarizerAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        graph = _build_graph(_Scope(deps, incoming)).compile()
        final = await graph.ainvoke({"route": "", "reply": ""})
        return TurnResult(final.get("reply") or "")


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(_state: SummaryState) -> dict:
        return _intake(scope)

    async def summarize_node(_state: SummaryState) -> dict:
        return await _summarize(scope)

    async def ask_node(_state: SummaryState) -> dict:
        return await _ask(scope)

    graph = StateGraph(SummaryState)
    graph.add_node("intake", intake_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("ask", ask_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["summarize", "ask", END])
    graph.add_edge("summarize", END)
    graph.add_edge("ask", END)
    return graph


def _intake(scope: _Scope) -> dict:
    plan = intake.plan_turn(scope.incoming)
    scope.plan = plan
    if plan.route == "reply":
        return {"route": "", "reply": _with_notes(plan.reply, plan.notes)}
    return {"route": plan.route}


async def _summarize(scope: _Scope) -> dict:
    plan = scope.plan
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    result = await summarize_step.summarize(scope.deps, plan.source, plan.instruction, scope.incoming.context)
    fixed = {"off_topic": smalltalk.OFF_TOPIC_REPLY, "not_summarizable": prompts.NOT_SUMMARIZABLE,
             "failed": prompts.MODEL_FAILED}.get(result.status)
    if fixed is not None:
        return {"reply": _with_notes(fixed, plan.notes)}
    summary = result.summary
    if summary.is_empty:
        return {"reply": _with_notes(prompts.NOTHING_BACKED, (*summary.notes, *plan.notes))}
    view, with_sources = plan.view
    reply = render.render(summary, view, with_sources, words=word_count(plan.source), parts=result.parts,
                          extra_notes=plan.notes)
    return {"reply": reply}


async def _ask(scope: _Scope) -> dict:
    plan = scope.plan
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    answer = await followup.ask(scope.deps, scope.incoming.text, plan.source, scope.incoming.context)
    return {"reply": _with_notes(answer, plan.notes)}


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
