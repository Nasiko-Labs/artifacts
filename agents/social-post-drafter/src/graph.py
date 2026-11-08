"""The social-post-drafter LangGraph.

    START → intake ─┬─ fixed reply (facts used, publish, private message, voice settings, no trace) → END
                    ├─ voice sample → voice ─┬─ voice only ─────────────────────────────→ END
                    │                        └─ with a request ─┐
                    ├─ long material → map ──────────────────────┤
                    └────────────────────────────────────────────┴→ draft ─┬─ variants ──→ END
                                                                           ├─ ask → pause → END
                                                                           └─ off topic ─→ END

A greeting never enters the graph (smalltalk.py). State (voice profile, current post set and what grounds
it) is checkpointed per A2A contextId. The graph is compiled per turn because its nodes close over that
turn's dependencies; the checkpointer is shared. The pasted voice sample stays in the turn scope only.
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
from drafting import read_voice
from hitl import Pending, question
from llm import Budget, BudgetExhausted, TurnDeps
from mapping import map_material
from models import PostSet
from steps import draft_step
from voice import Voice, apply, settings_only, settings_patch

log = logging.getLogger("social_post_drafter.graph")
_LASTING = ("always", "from now on", "by default", "going forward", "in general", "all my posts")


class PostState(TypedDict, total=False):
    voice: dict                # the profile ("my voice"), persisted
    post_set: dict | None      # the current variants and their grounding, persisted
    reply: str                 # this turn
    data: dict | None          # this turn: the envelope
    pending: dict | None       # this turn: the pause to raise, if any
    route: str                 # this turn


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pending: Pending | None = None
    hitl: dict | None = None
    data: dict | None = None


class Scope:
    """One turn's inputs and scratch (never persisted: the voice sample lives only here)."""

    def __init__(self, deps: TurnDeps, request: i.Request):
        self.deps = deps
        self.request = request
        self.split = i.split_sample(request.text)
        self.budget = Budget()
        self.as_json = i.wants_json(request.text)
        self.patch: dict = {}
        self.mapped = None
        self.notes: list[str] = []


class SocialPostAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, request: i.Request, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(request.text) if not request.pauses else None
        if canned is not None:  # a greeting or small talk: no LLM call, state untouched
            return TurnResult(canned)
        scope = Scope(deps, request)
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"reply": "", "data": None, "pending": None, "route": ""}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pending = Pending.from_metadata(final.get("pending"))
        if pending is None:
            return TurnResult(final.get("reply") or prompts.COULD_NOT_DRAFT, data=final.get("data"))
        text, hitl = question()
        return TurnResult(text, pending, hitl)


def _build_graph(scope: Scope) -> StateGraph:
    async def intake(state: PostState) -> dict:
        return _intake(state, scope)

    async def voice(state: PostState) -> dict:
        return await _guarded(_voice(state, scope))

    async def map_(state: PostState) -> dict:
        return await _guarded(_map(scope))

    async def draft(state: PostState) -> dict:
        return await _guarded(draft_step(state, scope))

    graph = StateGraph(PostState)
    for name, node in (("intake", intake), ("voice", voice), ("map", map_), ("draft", draft)):
        graph.add_node(name, node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", _route, ["voice", "map", "draft", END])
    graph.add_conditional_edges("voice", _route, ["map", "draft", END])
    graph.add_conditional_edges("map", _route, ["draft", END])
    graph.add_edge("draft", END)
    return graph


def _route(state: PostState) -> str:
    return state.get("route") or END


async def _guarded(step) -> dict:
    try:
        return await step
    except (TimeoutError, BudgetExhausted):
        log.warning("turn ran out of calls or time")
        return {"reply": prompts.BUDGET_SPENT, "route": ""}


def _intake(state: PostState, scope: Scope) -> dict:
    text = scope.split.request
    post_set = PostSet.from_dict(state.get("post_set"))
    profile = Voice.from_dict(state.get("voice"))
    if not scope.split.sample:
        fixed = _fixed_reply(text, post_set)
        if fixed is not None:
            return {"reply": fixed, "route": ""}
    scope.patch = settings_patch(text)
    lasting = any(word in text.casefold() for word in _LASTING)
    if scope.patch and lasting:
        profile = apply(profile, scope.patch)
        scope.notes.append(f"Saved as your voice from now on: {profile.summary()}.")
    if scope.patch and not scope.split.sample and settings_only(text, scope.patch) and (lasting or post_set is None):
        profile = apply(profile, scope.patch) if not lasting else profile
        return {"voice": profile.to_dict(), "reply": render.voice_saved(profile, post_set is not None), "route": ""}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    return {"voice": profile.to_dict(), "route": _next(scope, post_set)}


def _fixed_reply(text: str, post_set: PostSet | None) -> str | None:
    """Zero-call answers: the facts used, "post it for me", a private message, plain arithmetic."""
    if i.asks_for_facts(text):
        return render.facts_reply(post_set)
    if i.asks_to_publish(text):
        return smalltalk.PUBLISH_REPLY
    if i.is_private_message(text):
        return smalltalk.PRIVATE_REPLY
    if i.is_arithmetic(text):
        return smalltalk.OFF_TOPIC_REPLY
    return None


def _next(scope: Scope, post_set: PostSet | None) -> str:
    if scope.split.sample:
        return "voice"
    if len(scope.split.request) > i.MAX_SOURCE_CHARS and not i.is_revision(scope.split.request, post_set is not None):
        return "map"
    return "draft"


async def _voice(state: PostState, scope: Scope) -> dict:
    has_set = state.get("post_set") is not None
    voice = await read_voice(scope.deps, scope.budget, scope.split.sample)
    if scope.split.sample_truncated:
        scope.notes.append(f"I read only the first {i.MAX_SAMPLE_CHARS:,} characters of your sample.")
    if voice is None:
        scope.notes.append("I couldn't read a style from that sample, so I kept your current voice.")
        voice = Voice.from_dict(state.get("voice"))
    if not i.has_material(scope.split.request) and not i.is_redo(scope.split.request):
        reply = render.voice_saved(voice, has_set, lead="Got your voice")
        return {"voice": voice.to_dict(), "reply": " ".join([*scope.notes, reply]), "route": ""}
    scope.notes.append(f"Matched your voice: {voice.summary()}.")
    route = "map" if len(scope.split.request) > i.MAX_SOURCE_CHARS else "draft"
    return {"voice": voice.to_dict(), "route": route}


async def _map(scope: Scope) -> dict:
    scope.mapped = await map_material(scope.deps, scope.budget, scope.split.request)
    return {"route": "draft"}
