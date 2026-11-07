"""The graph's node bodies for a new epic, the Q3 resume and the scope check; refinements are in refining.py.
`result` turns checked stories into the stored state, the envelope (data part) and the reply."""

from __future__ import annotations

import logging
from dataclasses import replace

import envelope as envelope_mod
import intake as intake_mod
import pauses
import pipeline
import prompts
import render
import smalltalk
from envelope import Checks
from inputs import Incoming, Request, word_count
from llm import LlmError, TurnDeps, complete_json
from outline import Outline
from stories import Story, states

log = logging.getLogger("story-splitter.turns")

OFF_TOPIC, NEEDS_EPIC = "off_topic", "needs_epic"


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, bool(state.get("stories")))
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


def result(stories: list[Story], skeleton: str, outline: Outline, epic: str, words: int, checks: Checks,
           scope: Scope, notes: tuple[str, ...] = (), extra: dict | None = None) -> dict:
    env = envelope_mod.build(stories, skeleton, outline, words, checks)
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(env, epic, notes)
    return {**(extra or {}), "stories": states(stories), "skeleton": skeleton, "envelope": env, "data": env,
            "reply": reply}


async def run(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    request = scope.route.request
    outcome = await pipeline.start(scope.deps, request, scope.incoming.policy)
    if outcome.status == "ask_who":
        pending = pauses.Pending(pauses.WHO, request.epic, request.constraints, request.max_stories,
                                 request.personas, request.sprint_sized, scope.incoming.wants_json,
                                 outline=outcome.outline.to_dict())
        return {"pause": pending.to_metadata()}
    return _finish(outcome, request, scope)


async def who(state: dict, scope: Scope) -> dict:
    """Q3 answered: the stored outline plus the allowed actor, straight to slicing."""
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    request, actor = scope.route.request, scope.route.actor
    stored = Outline.from_dict(scope.forced.pending.outline if scope.forced.pending else {})
    found = replace(stored, extra_actors=(*stored.extra_actors, *([actor] if actor else [])))
    scope.deps.budget.allow_chunks(0)
    outcome = await pipeline.from_outline(scope.deps, request, found, scope.incoming.policy)
    return _finish(outcome, request, scope)


def _finish(outcome: pipeline.Outcome, request: Request, scope: Scope) -> dict:
    fixed = {"failed": prompts.MODEL_FAILED, "off_topic": smalltalk.OFF_TOPIC_REPLY,
             "nothing": prompts.NOTHING_FOUND, "no_stories": prompts.NO_STORIES}.get(outcome.status)
    if fixed is not None:  # the stored stories, if any, are kept
        return {"reply": fixed}
    words = word_count(request.epic)
    stored = {"epic": request.epic, "constraints": list(request.constraints), "outline": outcome.outline.to_dict(),
              "epic_words": words, "max_stories": request.max_stories}
    return result(list(outcome.stories), outcome.skeleton, outcome.outline, request.epic, words, outcome.checks,
                  scope, scope.route.notes, stored)


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    status = await classify(scope.deps, scope.incoming.text, scope.incoming.policy)
    if status == OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if state.get("stories"):
        return {"reply": prompts.FOLLOWUP_HELP}
    return {"pause": intake_mod.need_epic(scope.incoming).to_metadata()}


async def classify(deps: TurnDeps, text: str, policy=()) -> str:
    """A short message with no epic: off topic, or a request that still needs the epic. A failed call counts
    as needs_epic (asking for the epic never answers an off-topic question)."""
    def validate(data):
        ok = isinstance(data, dict) and data.get("status") in (OFF_TOPIC, NEEDS_EPIC)
        return data if ok else None
    try:
        data = await complete_json(deps, prompts.scope_messages(text, policy), prompts.SCOPE_CONTRACT, validate)
    except LlmError as e:
        log.warning("scope check failed: %s", e)
        return NEEDS_EPIC
    return data["status"]
