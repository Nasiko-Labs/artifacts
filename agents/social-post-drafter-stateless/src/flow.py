"""The social-post-drafter-stateless turn: plain async steps around LangChain runnables (see `llm.py`).

    intake ─┬─ fixed reply (facts used, publish, private message, voice settings, no trace)
            ├─ voice sample → voice ─┬─ voice only (reply)
            │                        └─ with a request ─┐
            ├─ long material → map ──────────────────────┤
            └────────────────────────────────────────────┴→ draft ─┬─ variants
                                                                   ├─ ask (a plain reply)
                                                                   └─ off topic

`_NEXT` lists the steps each step may hand over to; a step's `route` outside that list ends the turn.
A greeting never enters the flow (smalltalk.py). Stateless: the turn state is built fresh each turn from what
the platform's chat history still holds (`memory.recall`: the current post set parsed from the last reply, the
voice settings replayed, the newest voice sample). Any replica can answer any message, and no turn pauses.
The pasted voice sample stays in the turn scope only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TypedDict

import ask
import inputs as i
import memory
import prompts
import render
import smalltalk
from drafting import read_voice
from llm import Budget, BudgetExhausted, TurnDeps
from mapping import map_material
from models import PostSet
from steps import draft_step
from voice import Voice, apply, settings_only, settings_patch

log = logging.getLogger("social_post_drafter_stateless.flow")
_LASTING = memory.LASTING


class PostState(TypedDict, total=False):
    voice: dict                # the profile ("my voice"), replayed from the history this turn
    post_set: dict | None      # the current variants, parsed from the history this turn
    reply: str                 # this turn
    data: dict | None          # this turn: the envelope
    route: str                 # this turn


@dataclass(frozen=True)
class TurnResult:
    reply: str
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
        self.recalled = memory.recall(request.history)


class SocialPostAgent:
    async def turn(self, request: i.Request, deps: TurnDeps) -> TurnResult:
        if ask.answering(request.history) and not request.asked:
            answered = ask.answer(request)
            if isinstance(answered, str):
                return TurnResult(answered)
            request = answered
        canned = smalltalk.reply_to(request.text) if not request.asked else None
        if canned is not None:  # a greeting or small talk: no LLM call
            return TurnResult(canned)
        scope = Scope(deps, request)
        recalled = scope.recalled
        fresh = {"reply": "", "data": None, "route": "", "voice": recalled.voice.to_dict(),
                 "post_set": recalled.post_set.to_dict() if recalled.post_set else None}
        final = await _run(fresh, scope)
        return TurnResult(final.get("reply") or prompts.COULD_NOT_DRAFT, data=final.get("data"))


_NEXT = {"intake": ("voice", "map", "draft"), "voice": ("map", "draft"), "map": ("draft",), "draft": ()}


async def _run(state: PostState, scope: Scope) -> PostState:
    """intake, then each step its `route` names while `_NEXT` allows it; every step's update is merged into a
    new state dict."""
    steps = {"voice": lambda s: _voice(s, scope), "map": lambda s: _map(scope), "draft": lambda s: draft_step(s, scope)}
    state, step = {**state, **_intake(state, scope)}, "intake"
    while (route := state.get("route") or "") in _NEXT[step]:
        state, step = {**state, **await _guarded(steps[route](state))}, route
    return state


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
        fixed = _fixed_reply(text, post_set, scope.recalled)
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


def _fixed_reply(text: str, post_set: PostSet | None, recalled: memory.Recalled) -> str | None:
    """Zero-call answers: the facts used, "post it for me", a private message, plain arithmetic."""
    if i.asks_for_facts(text):
        return render.facts_reply(post_set, recalled.drafted_before)
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
