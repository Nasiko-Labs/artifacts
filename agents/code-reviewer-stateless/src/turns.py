"""What each route does with this turn's input and what `recall` found in the chat
history. Nothing is stored: a follow-up re-reads the recalled diff (and, for one comment, the recalled review)."""

from __future__ import annotations

import followup as followup_mod
import intake as intake_mod
import pipeline
import prompts
import render
import smalltalk
from diff_parse import parse
from inputs import MAX_DIFF_CHARS, Incoming, read_asks
from llm import LlmError, TurnDeps, UnusableAnswer
from recall import Prior, comment_from_review, recall


class Scope:
    """One turn's input, dependencies and recalled history (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps, self.incoming = deps, incoming
        self.prior: Prior = recall(incoming.history)
        self.route: intake_mod.Route | None = None


def intake(scope: Scope) -> dict:
    route = intake_mod.plan_turn(scope.incoming, scope.prior)
    scope.route = route
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    if not scope.deps.traceparent:
        return {"route": "", "reply": prompts.NO_TRACEPARENT_REPLY}
    return {"route": route.kind}


async def _review(scope: Scope, parsed, asks, truncated: bool) -> dict | str:
    """The envelope, or a fixed reply."""
    try:
        outcome = await pipeline.run(scope.deps, parsed, asks, scope.incoming.policy, truncated)
    except LlmError:
        return prompts.MODEL_FAILED
    if outcome.status == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    if outcome.status == "empty":
        return prompts.NOTHING_TO_REVIEW
    if outcome.status == "unusable":
        return prompts.MODEL_UNUSABLE
    if outcome.status == "failed" or outcome.envelope is None:
        return prompts.MODEL_FAILED
    return outcome.envelope


async def run(scope: Scope) -> dict:
    route = scope.route
    env = await _review(scope, route.parsed, route.asks, route.truncated)
    if isinstance(env, str):
        return {"reply": env}
    notes = list(scope.incoming.notes)
    if route.truncated:
        notes.append(f"_The diff was over {MAX_DIFF_CHARS:,} characters; I reviewed the first part only._")
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(env, notes=notes)
    return {"data": env, "reply": reply}


async def _recalled_review(scope: Scope) -> dict | str:
    source = scope.prior.source[:MAX_DIFF_CHARS]
    parsed = parse(source)
    return await _review(scope, parsed, read_asks(parsed.rest), len(scope.prior.source) > MAX_DIFF_CHARS)


async def followup(scope: Scope) -> dict:
    command, prior = scope.route.command, scope.prior
    if command.kind in ("explain", "fix"):
        return await _one(scope, command.number)
    if not prior.source:  # trimmed: only our last review is left
        if command.kind == "all" and prior.review:
            return {"reply": prior.review}
        return {"reply": prompts.NEED_REPASTE}
    env = await _recalled_review(scope)
    if isinstance(env, str):
        return {"reply": env}
    if command.kind == "json":
        return {"reply": render.render_json(env), "data": env}
    severities = command.severities if command.kind == "filter" else None
    view = env if severities is None else {**env, "items": [c for c in env["comments"] if c["severity"] in severities]}
    reply = render.render_json(view) if scope.incoming.wants_json else render.render(env, severities)
    return {"reply": reply, "data": view}


async def _one(scope: Scope, number: int) -> dict:
    """Comment N as the user saw it in our last review; failing that, from a fresh review of the recalled diff."""
    prior = scope.prior
    comment = comment_from_review(prior.review, number)
    if comment is None and prior.review:
        return {"reply": f"There's no comment {number} in my last review."}
    if comment is None and prior.source:
        env = await _recalled_review(scope)
        if isinstance(env, str):
            return {"reply": env}
        comments = env.get("comments") or []
        if not 1 <= number <= len(comments):
            return {"reply": f"There's no comment {number}; this review has {len(comments)}."}
        comment = comments[number - 1]
    if comment is None:
        return {"reply": prompts.NEED_REPASTE}
    return await _one_comment(scope, comment, number)


async def _one_comment(scope: Scope, comment: dict, number: int) -> dict:
    parsed = parse(scope.prior.source[:MAX_DIFF_CHARS])
    target = {f.path: f for f in parsed.files}.get(comment["file"])
    lines = {**target.context, **target.added} if target is not None else {}
    if lines and not comment.get("quote"):
        comment = {**comment, "quote": lines.get(comment["line"], "")}
    ctx = pipeline.context_for(parsed.files, read_asks(parsed.rest))
    command = scope.route.command
    try:
        result = await followup_mod.answer(scope.deps, command, comment, lines, ctx, scope.incoming.policy)
    except UnusableAnswer:
        return {"reply": prompts.MODEL_UNUSABLE}
    except LlmError:
        result = None
    if result is None:
        return {"reply": prompts.MODEL_FAILED}
    return {"reply": render.render_one(number, comment, *result)}


async def scope_check(scope: Scope) -> dict:
    status = await followup_mod.classify(scope.deps, scope.incoming.text, scope.incoming.policy)
    if status == followup_mod.FAILED:
        return {"reply": prompts.MODEL_FAILED}
    if status == followup_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if scope.prior.has_result:
        return {"reply": prompts.FOLLOWUP_HELP}
    return {"reply": prompts.NO_DIFF_LEAD}
