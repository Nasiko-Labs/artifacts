"""The postmortem-writer LangGraph.

    START → intake ─┬─ write (pipeline: parse → extract → check → metrics → write → lint → assemble) → END
                    ├─ followup (re-render, edit, or one question) → END
                    └─ fixed reply / pause ─────────────────────────────────────────────────────────→ END

State is checkpointed per A2A contextId (thread_id): the last log and its checked postmortem, so follow-ups
("only the action items", "owner of a2 is Priya", "as json", a question) work from the stored result. The
graph is compiled per turn because its nodes close over that turn's input and dependencies; the
checkpointer is shared. A pause leaves the graph as a `pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import envelope
import followup
import hitl
import intake
import pipeline
import prompts
import render
import smalltalk
from inputs import Incoming
from llm import TurnDeps
from records import Postmortem

FIXED = {"off_topic": smalltalk.OFF_TOPIC_REPLY, "not_incident": prompts.NOT_INCIDENT,
         "failed": prompts.MODEL_FAILED, "nothing": prompts.NOTHING_BACKED}


class PostmortemState(TypedDict, total=False):
    log: str
    instruction: str
    postmortem: dict     # Postmortem.to_dict()
    route: str           # this turn only
    reply: str           # this turn only
    pause: dict | None   # this turn only
    data: dict | None    # this turn only: the envelope


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake.Plan | None = None,
                 wants_json: bool = False):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.wants_json = wants_json or incoming.wants_json
        self.plan: intake.Plan | None = None


class PostmortemAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        plan = intake.plan_resume(hitl.interpret(pending, incoming.text), pending)
        return await self._run(_Scope(deps, incoming, forced=plan, wants_json=pending.wants_json), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")), final.get("data"))


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(state: PostmortemState) -> dict:
        return _intake(state, scope)

    async def write_node(state: PostmortemState) -> dict:
        return await _write(scope)

    async def followup_node(state: PostmortemState) -> dict:
        return await _followup(state, scope)

    graph = StateGraph(PostmortemState)
    graph.add_node("intake", intake_node)
    graph.add_node("write", write_node)
    graph.add_node("followup", followup_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["write", "followup", END])
    graph.add_edge("write", END)
    graph.add_edge("followup", END)
    return graph


def _intake(state: PostmortemState, scope: _Scope) -> dict:
    plan = scope.forced or intake.plan_turn(scope.incoming, bool(state.get("postmortem")))
    scope.plan = plan
    if plan.route == "pause":
        return {"route": "", "pause": plan.pending.to_metadata()}
    if plan.route == "reply":
        return {"route": "", "reply": _with_notes(plan.reply, plan.notes)}
    return {"route": "write" if plan.route == "write" else "followup"}


async def _write(scope: _Scope) -> dict:
    plan = scope.plan
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    outcome = await pipeline.run(scope.deps, plan.log, plan.instruction, scope.incoming.context)
    if outcome.postmortem is None:  # the stored postmortem, if any, is kept
        return {"reply": _with_notes(FIXED[outcome.status], (*outcome.notes, *plan.notes))}
    pm = outcome.postmortem
    data = envelope.build(pm)
    reply = envelope.as_json_text(data) if scope.wants_json else render.render(pm, extra_notes=plan.notes)
    return {"reply": reply, "data": data, "log": plan.log, "instruction": plan.instruction,
            "postmortem": pm.to_dict()}


async def _followup(state: PostmortemState, scope: _Scope) -> dict:
    plan = scope.plan
    stored = state.get("postmortem")
    if plan.route == "rerender" or plan.route == "edit":
        pm = Postmortem.from_dict(stored)
        update: dict = {}
        notes = plan.notes
        view = plan.view
        if plan.route == "edit":
            edited, note = followup.apply_edit(pm, plan.edit)
            if edited is None:
                return {"reply": note}
            pm, notes, view, update = edited, (note, *notes), "actions", {"postmortem": edited.to_dict()}
        data = envelope.build(pm)
        json_view = view == "json" or scope.wants_json
        reply = envelope.as_json_text(data) if json_view else render.render(pm, view, notes)
        return {**update, "reply": reply, "data": data}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    answer = await followup.ask(scope.deps, scope.incoming.text, state.get("log") or "", scope.incoming.context)
    return {"reply": _with_notes(answer, plan.notes)}


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
