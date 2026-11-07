"""The graph's node bodies: what each route does with the stored state and this turn's input."""

from __future__ import annotations

import followup as followup_mod
import intake as intake_mod
import pipeline
import prompts
import render
import smalltalk
from diff_parse import parse
from hitl import Pending
from inputs import MAX_DIFF_CHARS, Incoming
from llm import LlmError, TurnDeps


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, resumed: bool = False):
        self.deps, self.incoming, self.resumed = deps, incoming, resumed
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = intake_mod.plan_turn(scope.incoming, state, scope.resumed)
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": _pause(scope)}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    if route.kind != "followup" and not scope.deps.traceparent:
        return {"route": "", "reply": prompts.NO_TRACEPARENT_REPLY}
    return {"route": route.kind}


def _pause(scope: Scope) -> dict:
    return Pending(scope.incoming.text, scope.incoming.wants_json).to_metadata()


async def run(state: dict, scope: Scope) -> dict:
    route = scope.route
    try:
        outcome = await pipeline.run(scope.deps, route.parsed, route.asks, scope.incoming.policy, route.truncated)
    except LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if outcome.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if outcome.status == "empty":
        return {"reply": prompts.NOTHING_TO_REVIEW}
    if outcome.status == "failed" or outcome.envelope is None:
        return {"reply": prompts.MODEL_FAILED}
    env = outcome.envelope
    notes = list(scope.incoming.notes)
    if route.truncated:
        notes.append(f"_The diff was over {MAX_DIFF_CHARS:,} characters; I reviewed the first part only._")
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(env, notes=notes)
    return {"source": route.source, "asks": intake_mod.asks_to_dict(route.asks), "truncated": route.truncated,
            "envelope": env, "data": env, "reply": reply}


async def followup(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    env = state.get("envelope") or {}
    if command.kind == "json":
        return {"reply": render.render_json(env), "data": env}
    if command.kind in ("filter", "all"):
        severities = command.severities if command.kind == "filter" else None
        view = env if severities is None else {**env, "items": [c for c in env["comments"]
                                                                if c["severity"] in severities]}
        reply = render.render_json(view) if scope.incoming.wants_json else render.render(env, severities)
        return {"reply": reply, "data": view}
    comments = env.get("comments") or []
    if not 1 <= command.number <= len(comments):
        return {"reply": f"There's no comment {command.number}; this review has {len(comments)}."}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT_REPLY}
    return await _one_comment(state, scope, comments[command.number - 1])


async def _one_comment(state: dict, scope: Scope, comment: dict) -> dict:
    parsed = parse(state.get("source") or "")
    files = {f.path: f for f in parsed.files}
    target = files.get(comment["file"])
    if target is None:
        return {"reply": prompts.MODEL_FAILED}
    lines = {**target.context, **target.added}
    ctx = pipeline.context_for(parsed.files, intake_mod.asks_from(state.get("asks")))
    try:
        result = await followup_mod.answer(scope.deps, scope.route.command, comment, lines, ctx, scope.incoming.policy)
    except LlmError:
        result = None
    if result is None:
        return {"reply": prompts.MODEL_FAILED}
    return {"reply": render.render_one(scope.route.command.number, comment, *result)}


async def scope_check(state: dict, scope: Scope) -> dict:
    status = await followup_mod.classify(scope.deps, scope.incoming.text, scope.incoming.policy)
    if status == followup_mod.FAILED:
        return {"reply": prompts.MODEL_FAILED}
    if status == followup_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if state.get("envelope"):
        return {"reply": prompts.FOLLOWUP_HELP}
    return {"pause": _pause(scope)}
