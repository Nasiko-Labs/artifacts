"""The drafter LangGraph.

    START → intake ─┬─ fixed reply (facts used, tone settings, too long → pause, no trace) ──→ END
                    ├─ tone sample → tone ─┬─ tone only ─────────────────────────────────→ END
                    │                      └─ with a request ─┐
                    └─────────────────────────────────────────┴→ compose ─┬─ draft ─────→ END
                                                                          ├─ ask → pause → END
                                                                          └─ off topic ─→ END

A greeting never enters the graph (smalltalk.py). State (tone profile, current draft, the facts the
user gave) is checkpointed per A2A contextId, so revisions and the tone survive across turns and
replicas. The graph is compiled per turn because its nodes close over that turn's dependencies; the
checkpointer is shared. The pasted tone sample stays in the turn scope and is never checkpointed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import inputs as i
import prompts
import render
import smalltalk
from draft import Draft
from drafting import Composed, compose, read_tone
from jobs import build_job
from llm import Budget, BudgetExhausted, TurnDeps
from questions import MAX_PAUSES, Pending, question
from tone import Profile, apply_patch, settings_only, settings_patch

log = logging.getLogger("drafter.graph")

MAX_GROUND_ENTRIES = 20
REDO_INSTRUCTION = "Rewrite the current draft in my tone as described below. Keep the same facts and recipient."


class DraftState(TypedDict, total=False):
    profile: dict
    draft: dict | None
    ground: list[str]          # what the user gave for the current draft (facts may come from here)
    addressing: list[str]      # what may name the recipient of the current draft
    reply: str                 # this turn
    pending: dict | None       # this turn: the pause to raise, if any
    route: str                 # this turn


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pending: Pending | None = None
    hitl: dict | None = None


class _Scope:
    """One turn's inputs and scratch (never persisted: the tone sample lives only here)."""

    def __init__(self, deps: TurnDeps, request: i.Request):
        self.deps = deps
        self.request = request
        self.split = i.split_sample(request.text)
        self.budget = Budget()
        self.instruction: str | None = None
        self.notes: list[str] = []


class DrafterAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, request: i.Request, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(request.text) if not request.slots and not request.accept_long else None
        if canned is not None:  # a greeting or small talk: no LLM call, state untouched
            return TurnResult(canned)
        scope = _Scope(deps, request)
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"reply": "", "pending": None, "route": ""}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pending = Pending.from_metadata(final.get("pending"))
        if pending is None:
            return TurnResult(final.get("reply") or prompts.COULD_NOT_DRAFT)
        text, hitl = question(pending.slot)
        return TurnResult(text, pending, hitl)


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake(state: DraftState) -> dict:
        return _intake(state, scope)

    async def tone(state: DraftState) -> dict:
        return await _guarded(_tone(state, scope))

    async def compose(state: DraftState) -> dict:
        return await _guarded(_compose(state, scope))

    graph = StateGraph(DraftState)
    for name, node in (("intake", intake), ("tone", tone), ("compose", compose)):
        graph.add_node(name, node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", _route, ["tone", "compose", END])
    graph.add_conditional_edges("tone", _route, ["compose", END])
    graph.add_edge("compose", END)
    return graph


def _route(state: DraftState) -> str:
    return state.get("route") or END


async def _guarded(step) -> dict:
    try:
        return await step
    except (TimeoutError, BudgetExhausted):
        log.warning("turn ran out of calls or time")
        return {"reply": prompts.BUDGET_SPENT, "route": ""}


def _intake(state: DraftState, scope: _Scope) -> dict:
    text, request = scope.split.request, scope.request
    draft = Draft.from_dict(state.get("draft"))
    profile = Profile.from_dict(state.get("profile"))
    if not scope.split.sample:
        if draft is not None and i.asks_for_facts(text):
            return {"reply": render.facts_reply(draft), "route": ""}
        if draft is not None and i.is_redo(text):
            scope.instruction = REDO_INSTRUCTION
        patch, lasting = settings_patch(text), i.is_profile_scope(text)
        only_settings = settings_only(text, patch)
        if patch and (lasting or (draft is None and only_settings)):
            profile = apply_patch(profile, patch)
            if draft is None and only_settings:
                return {"profile": profile.to_dict(), "reply": render.tone_saved(profile, False), "route": ""}
            scope.notes.append(f"Saved as your tone from now on: {profile.summary()}.")
    if i.too_long(text) and not request.accept_long:
        pending = Pending("context", text, request.org, dict(request.slots), request.pauses + 1)
        return {"profile": profile.to_dict(), "pending": pending.to_metadata(), "route": ""}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    return {"profile": profile.to_dict(), "route": "tone" if scope.split.sample else "compose"}


async def _tone(state: DraftState, scope: _Scope) -> dict:
    has_draft = Draft.from_dict(state.get("draft")) is not None
    profile = await read_tone(scope.deps, scope.budget, scope.split.sample)
    if scope.split.sample_truncated:
        scope.notes.append(f"I read only the first {i.MAX_SAMPLE_CHARS:,} characters of your sample.")
    if profile is None:
        scope.notes.append("I couldn't read a style from that sample, so I kept your current tone.")
        profile = Profile.from_dict(state.get("profile"))
    if not i.has_words(scope.split.request):  # the sample alone: save the tone, no draft
        reply = render.tone_saved(profile, has_draft, lead="Got your style")
        return {"profile": profile.to_dict(), "reply": " ".join([*scope.notes, reply]), "route": ""}
    scope.notes.append(f"Matched your style: {profile.summary()}.")
    return {"profile": profile.to_dict(), "route": "compose"}


async def _compose(state: DraftState, scope: _Scope) -> dict:
    request = scope.request
    job = build_job(state, scope.split.request, scope.instruction, request)
    result: Composed = await compose(scope.deps, scope.budget, job)
    if result.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if result.status == "needs_info":
        if request.pauses >= MAX_PAUSES:
            return {"reply": f"I still need the {result.missing[0]} to write this. Tell me and I'll draft it."}
        pending = Pending(result.missing[0], scope.split.request, request.org, dict(request.slots), request.pauses + 1)
        return {"pending": pending.to_metadata()}
    if result.status != "ok" or result.draft is None:
        return {"reply": prompts.COULD_NOT_DRAFT}
    reply = render.draft_reply(result.draft, job.profile, [*scope.notes, *result.notes])
    return {"draft": result.draft.to_dict(), "ground": list(result.ground)[-MAX_GROUND_ENTRIES:],
            "addressing": list(result.addressing)[-MAX_GROUND_ENTRIES:], "reply": reply}


