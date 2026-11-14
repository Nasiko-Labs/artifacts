"""The humanizer LangGraph, stateless.

    START → intake ─┬─ work (humanize / envelope / scope call) → END
                    └─ fixed reply ─────────────────────────────→ END

There is no checkpointer: the graph's state lives for one turn and is built from that turn's input alone.
Earlier drafts and versions reach this agent only through the platform's history block, which `intake` reads
(via `recall`). Nothing pauses: every turn ends with a reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import envelope
import intake
import llm
import options as opts
import prompts
import recall
import render
import rewrite
import smalltalk
from inputs import Incoming
from natural import verbatim_draft
from outcome import MODEL_FAILED, OFF_TOPIC, Outcome


class HumanizerState(TypedDict, total=False):
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
        self.budget = llm.CallBudget()


class HumanizerAgent:
    async def turn(self, incoming: Incoming, deps: llm.TurnDeps) -> TurnResult:
        graph = _build_graph(_Scope(deps, incoming)).compile()
        final = await graph.ainvoke({"route": "", "reply": "", "data": None})
        return TurnResult(final.get("reply") or "", final.get("data"))


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(_state: HumanizerState) -> dict:
        scope.plan = intake.plan_turn(scope.incoming)
        if scope.plan.route == "reply":
            return {"route": "", "reply": _join(scope.plan.reply, scope.plan.notes)}
        return {"route": "work"}

    async def work_node(_state: HumanizerState) -> dict:
        return await _work(scope)

    graph = StateGraph(HumanizerState)
    graph.add_node("intake", intake_node)
    graph.add_node("work", work_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["work", END])
    graph.add_edge("work", END)
    return graph


async def _work(scope: _Scope) -> dict:
    plan = scope.plan
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    if plan.route == "classify":
        return await _classify(scope)
    parsed = opts.parse(plan.request, plan.base)
    as_json = parsed.as_json or plan.command == "json"
    if plan.route == "envelope":
        return await _envelope(scope, plan.envelope or {}, parsed.options, as_json)
    return await _free(scope, plan.draft, parsed.options, as_json)


async def _free(scope: _Scope, draft: str, options: opts.Options, as_json: bool) -> dict:
    result = await rewrite.humanize(scope.deps, scope.budget, draft, options, scope.incoming.context)
    if result.status in (OFF_TOPIC, MODEL_FAILED):
        return {"reply": _join(render.outcome(result), scope.plan.notes)}
    data = envelope.build(result)
    command = scope.plan.command
    if command in ("changes", "patterns"):
        body = render.changes(result) if command == "changes" else render.patterns_removed(result)
        return {"reply": _join(body, scope.plan.notes), "data": data}
    reply = envelope.fenced(data) if as_json else render.outcome(result, scope.plan.notes)
    return {"reply": reply, "data": data}


async def _envelope(scope: _Scope, source: dict, options: opts.Options, as_json: bool) -> dict:
    outcomes: dict[str, Outcome] = {}
    for name in envelope.fields_of(source):
        outcomes[name] = await rewrite.humanize(scope.deps, scope.budget, source[name], options,
                                                scope.incoming.context)
    if all(o.status == OFF_TOPIC for o in outcomes.values()):
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    data = envelope.build_from(source, outcomes)
    primary, command = outcomes[next(iter(outcomes))], scope.plan.command
    if command in ("changes", "patterns"):
        body = render.changes(primary) if command == "changes" else render.patterns_removed(primary)
        return {"reply": _join(body, scope.plan.notes), "data": data}
    reply = envelope.fenced(data) if as_json else render.envelope_text(outcomes, scope.plan.notes)
    return {"reply": reply, "data": data}


async def _classify(scope: _Scope) -> dict:
    try:
        answer = await llm.call_json(scope.deps, scope.budget, prompts.classify_messages(scope.plan.request))
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED}
    data = answer.data if isinstance(answer.data, dict) else {}
    status = data.get("status")
    if status == "has_draft":
        found = verbatim_draft(scope.plan.request, data.get("draft"))
        if found is not None:   # copied from the message: humanize it, the rest of the message is the request
            parsed = opts.parse(found[0])
            return await _free(scope, found[1], parsed.options, parsed.as_json)
        status = "needs_draft"  # not in the message, or too short: never edit text the user did not send
    if status != "needs_draft":  # fail closed: anything unclear is declined
        return {"reply": _join(smalltalk.OFF_TOPIC_REPLY, scope.plan.notes)}
    if recall.memory(scope.incoming.history).has_any:
        return {"reply": prompts.FOLLOWUP_HELP}
    return {"reply": prompts.NEED_DRAFT}


def _join(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(p for p in (text, *notes) if p)
