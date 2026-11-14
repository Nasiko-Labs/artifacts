"""The translator LangGraph, stateless.

    START → intake ─┬─ translate ─────────────────────────────→ END
                    ├─ scope (one call: text or off_topic) ─┬─→ translate → END
                    │                                       └─→ declined / "which language?" → END
                    └─ fixed reply / original ─────────────────→ END

There is no checkpointer: the graph's state lives for one turn and is built from that turn's input alone.
The last job ("now in French", "more formal", "show the original") is rebuilt from the platform's history
block by `intake` (via `recall`). Nothing pauses: every turn ends with a reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import envelope as env
import intake
import languages
import llm
import prompts
import render
import segments as segs
import smalltalk
import translate as engine
from inputs import Incoming


class TranslatorState(TypedDict, total=False):
    route: str
    reply: str
    data: dict | None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: llm.TurnDeps, incoming: Incoming):
        self.deps, self.incoming = deps, incoming
        self.plan: intake.Plan | None = None


class TranslatorAgent:
    async def turn(self, incoming: Incoming, deps: llm.TurnDeps) -> TurnResult:
        graph = _build_graph(_Scope(deps, incoming)).compile()
        final = await graph.ainvoke({"route": "", "reply": "", "data": None})
        return TurnResult(final.get("reply") or "", final.get("data"))


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(_state: TranslatorState) -> dict:
        return _route(intake.plan_turn(scope.incoming), scope)

    async def scope_node(_state: TranslatorState) -> dict:
        return await _scope_check(scope)

    async def translate_node(_state: TranslatorState) -> dict:
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


def _route(plan: intake.Plan, scope: _Scope) -> dict:
    scope.plan = plan
    if plan.route == "reply":
        return {"route": "", "reply": _join(plan.reply, *scope.incoming.notes)}
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
    plan = intake.next_step(intake.from_extraction(scope.plan.job, message, extraction), scope.incoming)
    scope.plan = plan
    return {"route": "translate" if plan.route == "translate" else "", "reply": plan.reply}


async def _translate(scope: _Scope) -> dict:
    job = scope.plan.job
    prepared = engine.prepare(job)
    results = await engine.run(scope.deps, job, prepared, scope.incoming.context)
    result = env.build(job, prepared, segs.chunk(prepared.segments), results)
    if result.status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if result.status == "already":
        language = languages.BY_CODE[job.target.casefold()].name
        return {"reply": prompts.ALREADY_IN.format(language=language, text=render.original(job.source, job.envelope))}
    data = {**result.envelope, **({"translated_envelope": result.translated_input} if result.translated_input else {})}
    reply = render.reply(result.envelope, result.translated_input, job.as_json)
    return {"reply": _join(reply, *scope.plan.notes, *scope.incoming.notes), "data": data}


def _join(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p)
