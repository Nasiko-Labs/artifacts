"""The translator LangGraph.

    START → intake ─┬─ translate ─────────────────────────────→ END
                    ├─ scope (one call: text or off_topic) ─┬─→ translate → END
                    │                                       └─→ declined / pause → END
                    └─ fixed reply / pause / original / json ──→ END

State is checkpointed per A2A contextId (thread_id): the last job (source, target, register, glossary)
and its result, so "now in French", "more formal" and "show the original" work from the stored text. The
graph is compiled per turn because its nodes close over that turn's input and dependencies; the
checkpointer is shared. A pause leaves the graph as a `pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import envelope as env
import hitl
import intake
import languages
import llm
import prompts
import render
import segments as segs
import smalltalk
import translate as engine
from inputs import Incoming
from request import Job


class TranslatorState(TypedDict, total=False):
    job: dict            # Job.to_dict() of the last translation
    result: dict         # its output envelope
    translated_input: dict | None
    route: str           # this turn only
    reply: str           # this turn only
    data: dict | None    # this turn only: the DataPart
    pause: dict | None   # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: llm.TurnDeps, incoming: Incoming, forced: intake.Plan | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.plan: intake.Plan | None = None


class TranslatorAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: llm.TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str,
                     deps: llm.TurnDeps) -> TurnResult:
        plan = intake.plan_resume(hitl.interpret(pending, incoming.text))
        return await self._run(_Scope(deps, incoming, forced=plan), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")), final.get("data"))


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(state: TranslatorState) -> dict:
        return _intake(state, scope)

    async def scope_node(state: TranslatorState) -> dict:
        return await _scope_check(scope)

    async def translate_node(state: TranslatorState) -> dict:
        return await _translate(scope)

    graph = StateGraph(TranslatorState)
    graph.add_node("intake", intake_node)
    graph.add_node("scope", scope_node)
    graph.add_node("translate", translate_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["scope", "translate", END])
    graph.add_conditional_edges("scope", lambda s: s.get("route") or END, ["translate", END])
    graph.add_edge("translate", END)
    return graph


def _stored(state: TranslatorState) -> Job | None:
    return Job.from_dict(state.get("job"))


def _intake(state: TranslatorState, scope: _Scope) -> dict:
    plan = scope.forced or intake.plan_turn(scope.incoming, _stored(state))
    return _route(plan, state, scope)


def _route(plan: intake.Plan, state: TranslatorState, scope: _Scope) -> dict:
    scope.plan = plan
    notes = "\n\n".join(scope.incoming.notes)
    if plan.route == "pause":
        return {"route": "", "pause": plan.pending.to_metadata()}
    if plan.route == "reply":
        return {"route": "", "reply": _join(plan.reply, notes)}
    if plan.route == "original":
        stored = _stored(state)
        return {"route": "", "reply": render.original(stored.source, stored.envelope) if stored else prompts.NOTHING_STORED}
    if plan.route == "json":
        result = state.get("result")
        if not result:
            return {"route": "", "reply": prompts.NOTHING_STORED}
        data = {**result, **({"translated_envelope": state["translated_input"]} if state.get("translated_input") else {})}
        return {"route": "", "reply": render.fenced_json(data), "data": data}
    if not scope.deps.traceparent:
        return {"route": "", "reply": prompts.NO_TRACEPARENT}
    return {"route": "scope" if plan.route == "scope" else "translate"}


async def _scope_check(scope: _Scope) -> dict:
    try:
        message = scope.plan.job.source
        extraction = await llm.scope(scope.deps, prompts.scope_messages(message, scope.incoming.context))
    except llm.LlmError:
        return {"route": "", "reply": prompts.MODEL_FAILED}
    if extraction.status != "translate":
        return {"route": "", "reply": smalltalk.OFF_TOPIC_REPLY}
    plan = intake.next_step(intake.from_extraction(scope.plan.job, message, extraction))
    scope.plan = plan
    if plan.route == "pause":
        return {"route": "", "pause": plan.pending.to_metadata()}
    return {"route": "translate" if plan.route == "translate" else "", "reply": plan.reply}


async def _translate(scope: _Scope) -> dict:
    job = scope.plan.job
    prepared = engine.prepare(job)
    results = await engine.run(scope.deps, job, prepared, scope.incoming.context)
    result = env.build(job, prepared, segs.chunk(prepared.segments), results)
    if result.status == "failed":
        return {"reply": prompts.MODEL_FAILED}  # the stored translation, if any, is kept
    if result.status == "already":
        language = languages.BY_CODE[job.target.casefold()].name
        return {"reply": prompts.ALREADY_IN.format(language=language, text=render.original(job.source, job.envelope))}
    data = {**result.envelope, **({"translated_envelope": result.translated_input} if result.translated_input else {})}
    reply = render.reply(result.envelope, result.translated_input, job.as_json)
    return {"reply": _join(reply, "\n\n".join(scope.incoming.notes)), "data": data,
            "job": {**job.to_dict(), "as_json": False}, "result": result.envelope,
            "translated_input": result.translated_input}


def _join(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p)
