"""The draft node: a new set (from the message, an envelope or mapped facts) or a revision of the
current set, then the reply and the envelope, both rendered by code.

Stateless: the current set and the voice come from the chat history (`memory.recall`). When the variants
were trimmed out of the history, a revision is redrafted from the newest material still there; when that is
gone too, the reply says so and asks for the material again. A missing-material question is a plain reply."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import ask
import envelope
import inputs as i
import memory
import prompts
import render
import smalltalk
import turns
from drafting import Composed, compose, read_voice
from models import PostSet
from voice import Voice, apply, strip_settings

_STATUS_REPLIES = {"off_topic": smalltalk.OFF_TOPIC_REPLY, "private_message": smalltalk.PRIVATE_REPLY,
                   "publish": smalltalk.PUBLISH_REPLY, "failed": prompts.COULD_NOT_DRAFT}


async def draft_step(state: Mapping[str, Any], scope) -> dict:
    text = scope.split.request
    post_set = PostSet.from_dict(state.get("post_set"))
    profile = await _recalled_voice(Voice.from_dict(state.get("voice")), scope)
    recalled = scope.recalled
    if post_set is None and recalled.drafted_before and scope.mapped is None and memory.looks_like_revision(text):
        if not recalled.material:
            return {"reply": prompts.SET_TRIMMED}
        scope.notes.append(prompts.REDRAFTED)
        return await _new_set(f"{recalled.material}\n\n{text}", profile, scope)
    if scope.mapped is None and post_set is not None and i.is_revision(text, True) \
            and envelope.material_from(text) is None:
        return await _revise(text, post_set, profile, scope)
    return await _new_set(text, profile, scope)


async def _new_set(text: str, profile: Voice, scope) -> dict:
    request = scope.request
    material = ""
    if (carried := envelope.material_from(text)) is not None:
        material, text = carried
    voice = apply(profile, scope.patch, source="settings") if scope.patch else profile
    if not (material or scope.mapped or i.has_material(strip_settings(text))):
        return _ask(text, request)
    facts = scope.mapped.facts if scope.mapped else ()
    job = turns.new_job(text, material, facts, request, voice)
    result = await compose(scope.deps, scope.budget, job)
    if result.status == "needs_info":
        return _ask(text, request)
    if result.status != "ok":
        return {"reply": _STATUS_REPLIES.get(result.status, prompts.COULD_NOT_DRAFT)}
    coverage = scope.mapped.coverage if scope.mapped else ""
    post_set = PostSet(result.variants, turns.platform_of(result.variants), job.goal or "announce", voice.to_dict(),
                       job.ground, coverage)
    return _done(post_set, result, scope, ())


async def _revise(text: str, post_set: PostSet, profile: Voice, scope) -> dict:
    voice = profile if i.is_redo(text) else Voice.from_dict(post_set.voice) if post_set.voice else profile
    if scope.patch:
        voice = apply(voice, scope.patch, source="settings")
    targets = turns.revision_targets(text, post_set)
    job = turns.revision_job(text, post_set, scope.request, voice, targets)
    result = await compose(scope.deps, scope.budget, job)
    if result.status == "needs_info":
        return {"reply": prompts.COULD_NOT_DRAFT}
    if result.status != "ok":
        return {"reply": _STATUS_REPLIES.get(result.status, prompts.COULD_NOT_DRAFT)}
    revised = turns.merged(post_set, targets, result.variants, voice, job.ground)
    return _done(revised, result, scope, targets[:len(result.variants)])


def _done(post_set: PostSet, result: Composed, scope, revised: tuple[int, ...]) -> dict:
    data = envelope.build(post_set.variants, post_set.platform, post_set.goal, post_set.voice, result.repaired)
    if scope.as_json:
        reply = envelope.fenced(data)
    else:
        reply = render.reply(post_set, data["summary"], [*scope.notes, *result.notes], revised)
    return {"post_set": post_set.to_dict(), "reply": reply, "data": data}


async def _recalled_voice(profile: Voice, scope) -> Voice:
    """A voice sample pasted earlier (and still in the history) is read again: one call, since nothing was kept.
    The settings replayed from the history go on top of it."""
    sample = scope.recalled.sample
    if scope.split.sample or not sample:
        return profile
    sampled = await read_voice(scope.deps, scope.budget, sample)
    users = tuple(t.text for t in scope.request.history if t.role == "user")
    return memory.replay_voice(sampled, users) if sampled is not None else profile


def _ask(text: str, request: i.Request) -> dict:
    """No material: ask for it in a plain reply (the task completes). Asked already: say what is missing."""
    return {"reply": prompts.NEED_MATERIAL if request.asked else ask.LEAD}
