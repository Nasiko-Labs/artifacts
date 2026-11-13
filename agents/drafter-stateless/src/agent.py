"""The drafter turn, stateless, as plain async steps over LangChain runnables (the model calls are LCEL
chains in llm.py; there is no graph framework).

    intake ─┬─ fixed reply (facts used, tone settings, draft lost from history, no trace)
            ├─ tone sample → tone ─┬─ tone only
            │                      └─ with a request ─┐
            └─────────────────────────────────────────┴→ compose ─┬─ draft
                                                                  ├─ ask (plain reply)
                                                                  └─ off topic

A greeting never reaches the steps (smalltalk.py). Nothing is stored: the turn state is a plain dict built
fresh from the platform's history slice (recall.py: the current draft,
the tone settings, the newest writing sample, what grounds a revision), so any replica can answer any
message. Nothing pauses: a missing detail, a too-long paste or a lost draft gets a plain reply. A pasted
tone sample stays in the turn scope and is never stored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TypedDict

import inputs as i
import prompts
import recall
import render
import smalltalk
from draft import Draft
from drafting import Composed, compose, read_tone
from jobs import build_job
from llm import Budget, BudgetExhausted, MissingTraceparent, TurnDeps
from tone import Profile, apply_patch, settings_only, settings_patch

log = logging.getLogger("drafter-stateless.agent")

REDO_INSTRUCTION = "Rewrite the current draft in my tone as described below. Keep the same facts and recipient."


class DraftState(TypedDict, total=False):
    profile: dict
    draft: dict | None         # the draft recovered from the history, then this turn's
    ground: list[str]          # what the user gave (facts may come from here)
    addressing: list[str]      # what may name the recipient
    reply: str
    route: str


@dataclass(frozen=True)
class TurnResult:
    reply: str


class _Scope:
    """One turn's inputs and scratch (never persisted: the tone sample lives only here)."""

    def __init__(self, deps: TurnDeps, request: i.Request, memory: recall.Memory):
        self.deps = deps
        self.request = request
        self.memory = memory
        self.split = i.split_sample(request.text)
        self.budget = Budget()
        self.instruction: str | None = None
        self.notes: list[str] = []


class DrafterAgent:
    """Holds nothing between turns."""

    async def turn(self, request: i.Request, context_id: str, deps: TurnDeps) -> TurnResult:
        """`context_id` is only for logs and spans; no state is keyed by it."""
        canned = smalltalk.reply_to(request.text)
        if canned is not None:  # a greeting or small talk: no LLM call
            return TurnResult(canned)
        memory = recall.remember(request.history)
        scope = _Scope(deps, request, memory)
        fresh = {"draft": memory.draft.to_dict() if memory.draft else None, "ground": list(memory.ground),
                 "addressing": list(memory.addressing), "profile": {}, "reply": "", "route": ""}
        final = await _run(fresh, scope)
        return TurnResult(final.get("reply") or prompts.COULD_NOT_DRAFT)


async def _run(state: DraftState, scope: _Scope) -> DraftState:
    """intake, then tone and/or compose when the previous step routed there. Each step's update is merged
    into a new dict (a step that sets no route ends the turn)."""
    state = {**state, "route": "", **await _intake(state, scope)}
    if state["route"] == "tone":
        state = {**state, "route": "", **await _guarded(_tone(state, scope))}
    if state["route"] == "compose":
        state = {**state, "route": "", **await _guarded(_compose(state, scope))}
    return state


async def _guarded(step) -> dict:
    try:
        return await step
    except (TimeoutError, BudgetExhausted):
        log.warning("turn ran out of calls or time")
        return {"reply": prompts.BUDGET_SPENT, "route": ""}


async def _intake(state: DraftState, scope: _Scope) -> dict:
    text, memory = scope.split.request, scope.memory
    draft = Draft.from_dict(state.get("draft"))
    if scope.split.sample:  # this message's own sample replaces the tone: nothing earlier is re-read
        profile, _ = recall.base_profile(memory, None)
        return _truncated_or(scope, {"profile": profile.to_dict(), "route": "tone"})
    if i.asks_for_facts(text):
        return {"reply": _facts_reply(draft, memory), "route": ""}
    if draft is None and memory.trimmed and i.looks_like_revision(text):
        return {"reply": prompts.DRAFT_NOT_IN_HISTORY, "route": ""}
    if draft is not None and i.is_redo(text):
        scope.instruction = REDO_INSTRUCTION
    profile = await _profile(scope)
    patch, lasting = settings_patch(text), i.is_profile_scope(text)
    only_settings = settings_only(text, patch)
    if patch and (lasting or (draft is None and only_settings)):
        profile = apply_patch(profile, patch)
        if draft is None and only_settings:
            return {"profile": profile.to_dict(), "reply": render.tone_saved(profile, False), "route": ""}
        scope.notes.append(f"Saved as your tone from now on: {profile.summary()}.")
    return _truncated_or(scope, {"profile": profile.to_dict(), "route": "compose"})


def _truncated_or(scope: _Scope, update: dict) -> dict:
    """Too long: use the first part and say what was left out (never a question). No trace: a fixed reply."""
    text = scope.split.request
    if i.too_long(text):
        scope.split = replace(scope.split, request=text[:i.MAX_CONTEXT_CHARS])
        scope.notes.append(prompts.TOO_LONG_NOTE.format(used=i.MAX_CONTEXT_CHARS, left=len(text) - i.MAX_CONTEXT_CHARS))
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    return update


async def _profile(scope: _Scope) -> Profile:
    """The tone before this message's settings: the newest sample still in the history re-read (one call,
    only when there is one and the model can be reached), then the settings that stuck after it."""
    memory, from_sample = scope.memory, None
    if memory.sample and scope.deps.traceparent:
        try:
            from_sample = await read_tone(scope.deps, scope.budget, memory.sample)
        except (TimeoutError, BudgetExhausted, MissingTraceparent):
            log.warning("could not re-read the writing sample from the history")
    profile, note = recall.base_profile(memory, from_sample)
    if note:
        scope.notes.append(note)
    return profile


def _facts_reply(draft: Draft | None, memory: recall.Memory) -> str:
    if draft is not None:
        return prompts.FACTS_NOT_KEPT
    return prompts.DRAFT_NOT_IN_HISTORY if memory.trimmed else render.facts_reply(None)


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
        return {"reply": prompts.need_details(result.missing)}
    if result.status != "ok" or result.draft is None:
        return {"reply": prompts.COULD_NOT_DRAFT}
    reply = render.draft_reply(result.draft, job.profile, [*scope.notes, *result.notes])
    return {"draft": result.draft.to_dict(), "reply": reply}


