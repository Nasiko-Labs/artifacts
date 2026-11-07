"""The escalation-summarizer LangGraph.

    START → intake ─┬─ summarize (extract → check → compose → check → render) → END
                    ├─ followup (view / who said / restyle / one question)     → END
                    └─ fixed reply / pause ────────────────────────────────────→ END

State is checkpointed per A2A contextId (thread_id): the last thread, its messages, the checked facts, the
envelope and the style, so "show sources", "only the ask", "shorter", "for the CEO" and questions work from
the stored result. The graph is compiled per turn (its nodes close over that turn's input and
dependencies); the checkpointer is shared. A pause leaves the graph as a `pause` value; its state lives on
the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import checks
import envelope
import extract as extract_step
import facts as fx
import followup
import intake
import messages as msgs
import pauses
import prompts
import render
import smalltalk
import style as st
import timeline as tl
from compose import compose
from inputs import Incoming
from llm import TurnDeps

UNATTRIBUTED = "I couldn't tell the messages apart (no headers I recognise), so speakers aren't attributed."
FACTS_LEFT_OUT = "{n} extracted facts were left out: their quotes aren't in the thread."


class EscalationState(TypedDict, total=False):
    thread: str
    messages: list[dict]
    facts: list[dict]
    envelope: dict
    style: dict
    partial: bool
    route: str           # this turn only
    reply: str           # this turn only
    data: dict | None    # this turn only: the envelope for the DataPart
    pause: dict | None   # this turn only: pauses.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: pauses.Pending | None = None
    data: dict | None = None


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake.Plan | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.plan: intake.Plan | None = None


class EscalationAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: pauses.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        plan = intake.plan_resume(pauses.interpret(pending, incoming.text))
        return await self._run(_Scope(deps, incoming, forced=plan), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", pauses.Pending.from_metadata(final.get("pause")), final.get("data"))


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(state: EscalationState) -> dict:
        return _intake(state, scope)

    async def summarize_node(state: EscalationState) -> dict:
        return await _summarize(scope)

    async def followup_node(state: EscalationState) -> dict:
        return await _followup(state, scope)

    graph = StateGraph(EscalationState)
    graph.add_node("intake", intake_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("followup", followup_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["summarize", "followup", END])
    graph.add_edge("summarize", END)
    graph.add_edge("followup", END)
    return graph


def _intake(state: EscalationState, scope: _Scope) -> dict:
    plan = scope.forced or intake.plan_turn(scope.incoming, bool(state.get("envelope")))
    scope.plan = plan
    if plan.route == "pause":
        return {"route": "", "pause": plan.pending.to_metadata()}
    if plan.route == "reply":
        return {"route": "", "reply": _with_notes(plan.reply, plan.notes)}
    return {"route": "summarize" if plan.route == "summarize" else "followup"}


async def _summarize(scope: _Scope) -> dict:
    plan, deps, context = scope.plan, scope.deps, scope.incoming.context
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    messages = msgs.parse(plan.thread)
    found = await extract_step.extract(deps, plan.thread, messages, msgs.labelled(messages), context)
    fixed = {"off_topic": smalltalk.OFF_TOPIC_REPLY, "not_thread": prompts.NOT_A_THREAD,
             "failed": prompts.MODEL_FAILED}.get(found.status)
    if fixed is not None or not found.facts:
        return {"reply": _with_notes(fixed or prompts.NOTHING_BACKED, plan.notes)}
    style = st.parse(plan.instruction)
    notes = [*found.notes(), *([FACTS_LEFT_OUT.format(n=found.dropped)] if found.dropped else [])]
    if len(msgs.own(messages)) == 1 and not messages[0].speaker:
        notes.append(UNATTRIBUTED)
    env = await _compose_envelope(deps, found.facts, plan.thread, messages, style, context, notes, found.partial)
    return {"reply": _reply(env, scope, plan.notes), "data": env, "thread": plan.thread, "envelope": env,
            "messages": msgs.to_state(messages), "facts": fx.to_state(found.facts), "style": style.to_dict(),
            "partial": found.partial}


async def _compose_envelope(deps, facts, thread, messages, style, context, notes, partial) -> dict:
    timeline = tl.describe(messages)
    composed = await compose(deps, facts, thread, timeline, style, checks.is_resolved(messages), context)
    conflicts = [checks.conflict_line(pair) for pair in checks.conflicts(facts)]
    return envelope.build(composed, facts, timeline, conflicts, notes, partial, style.audience)


async def _followup(state: EscalationState, scope: _Scope) -> dict:
    plan, deps = scope.plan, scope.deps
    env = state.get("envelope") or {}
    facts = fx.from_state(state.get("facts"))
    if plan.route == "view":
        view = "json" if scope.incoming.wants_json else plan.view
        return {"reply": _with_notes(render.render(env, view), plan.notes), "data": env}
    if plan.route == "who":
        return {"reply": _with_notes(followup.who_said_reply(plan.kinds, facts), plan.notes)}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    if plan.route == "restyle":
        style = st.parse(plan.instruction, st.Style.from_dict(state.get("style")))
        messages = msgs.from_state(state.get("messages"))
        new_env = await _compose_envelope(deps, facts, state.get("thread") or "", messages, style,
                                          scope.incoming.context, env.get("notes") or [], bool(state.get("partial")))
        return {"reply": _reply(new_env, scope, plan.notes), "data": new_env, "envelope": new_env,
                "style": style.to_dict()}
    answer = await followup.ask(deps, scope.incoming.text, state.get("thread") or "", facts, scope.incoming.context)
    return {"reply": _with_notes(answer, plan.notes)}


def _reply(env: dict, scope: _Scope, notes: tuple[str, ...]) -> str:
    return render.render(env, "json" if scope.incoming.wants_json else "default", extra_notes=notes)


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
