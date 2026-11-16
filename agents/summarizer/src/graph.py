"""The summarizer LangGraph.

    START → intake ─┬─ summarize ─→ render → END
                    ├─ followup (re-render or one question) → END
                    └─ fixed reply / pause ─────────────────→ END

State is checkpointed per A2A contextId (thread_id): the last source and its checked summary, so
follow-ups ("only the action items", "show sources", a question) work from the stored result. The
graph is compiled per turn because its nodes close over that turn's input and dependencies; the
checkpointer is shared. A pause leaves the graph as a `pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import followup
import hitl
import intake
import mcp_tools
import prompts
import render
import smalltalk
import summarize as summarize_step
from inputs import Incoming, word_count
from items import Summary, items_from_state, items_to_state
from llm import TurnDeps


class SummaryState(TypedDict, total=False):
    source: str          # the text last summarized (fenced into follow-up questions)
    instruction: str
    summary: dict        # Summary.to_dict(): what re-render commands draw from
    pool: list[dict]     # map-reduce: every checked item, for follow-up questions
    words: int
    parts: int
    route: str           # this turn only
    reply: str           # this turn only
    pause: dict | None   # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake.Plan | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.plan: intake.Plan | None = None


class SummarizerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to one of this agent's questions, on the paused task."""
        plan = intake.plan_resume(hitl.interpret(pending, incoming.text))
        return await self._run(_Scope(deps, incoming, forced=plan), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pause = hitl.Pending.from_metadata(final.get("pause"))
        return TurnResult(final.get("reply") or "", pause)


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(state: SummaryState) -> dict:
        return _intake(state, scope)

    async def summarize_node(state: SummaryState) -> dict:
        return await _summarize(state, scope)

    async def followup_node(state: SummaryState) -> dict:
        return await _followup(state, scope)

    graph = StateGraph(SummaryState)
    graph.add_node("intake", intake_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("followup", followup_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["summarize", "followup", END])
    graph.add_edge("summarize", END)
    graph.add_edge("followup", END)
    return graph


def _intake(state: SummaryState, scope: _Scope) -> dict:
    plan = scope.forced or intake.plan_turn(scope.incoming, state.get("source") or "")
    if plan.route == "together":
        joined = f"{state.get('source') or ''}\n\n{plan.source}".strip()
        plan = intake.Plan("summarize", source=joined, instruction=plan.instruction, notes=plan.notes)
    scope.plan = plan
    if plan.route == "pause":
        return {"route": "", "pause": plan.pending.to_metadata()}
    if plan.route == "reply":
        return {"route": "", "reply": _with_notes(plan.reply, plan.notes)}
    return {"route": "summarize" if plan.route == "summarize" else "followup"}


async def _summarize(state: SummaryState, scope: _Scope) -> dict:
    plan = scope.plan
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    result = await summarize_step.summarize(scope.deps, plan.source, plan.instruction, scope.incoming.context)
    if result.status == "off_topic":
        # Not something to summarize: try the operator's attached MCP tools, else a generic reply.
        reply = await mcp_tools.offer_or_decline(scope.deps, scope.incoming.text, smalltalk.INTRO)
        return {"reply": _with_notes(reply, plan.notes)}
    fixed = {"not_summarizable": prompts.NOT_SUMMARIZABLE,
             "failed": prompts.MODEL_FAILED}.get(result.status)
    if fixed is not None:  # the stored summary, if any, is kept
        return {"reply": _with_notes(fixed, plan.notes)}
    summary = result.summary
    words = word_count(plan.source)
    if summary.is_empty:
        return {"reply": _with_notes(prompts.NOTHING_BACKED, (*summary.notes, *plan.notes))}
    reply = render.render(summary, words=words, parts=result.parts, extra_notes=plan.notes)
    return {"reply": reply, "source": plan.source, "instruction": plan.instruction, "summary": summary.to_dict(),
            "pool": items_to_state(result.pool), "words": words, "parts": result.parts}


async def _followup(state: SummaryState, scope: _Scope) -> dict:
    plan = scope.plan
    if plan.route == "rerender":
        view, with_sources = plan.view
        reply = render.render(Summary.from_dict(state.get("summary")), view, with_sources,
                              words=state.get("words") or 0, parts=state.get("parts") or 1, extra_notes=plan.notes)
        return {"reply": reply}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    answer = await followup.ask(scope.deps, scope.incoming.text, state.get("source") or "",
                                items_from_state(state.get("pool")), scope.incoming.context)
    return {"reply": _with_notes(answer, plan.notes)}


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
