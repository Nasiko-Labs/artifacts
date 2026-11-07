"""The graph's node bodies: what each route does with the stored state and this turn's input."""

from __future__ import annotations

import intake as intake_mod
import prompts
import render
import review
import smalltalk
from inputs import Incoming, options
from llm import TurnDeps
from pauses import Pending


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, bool(state.get("envelope")))
    scope.route = route
    if route.kind == "pause":
        pending = route.pending
        if scope.incoming.wants_json and not pending.wants_json:
            pending = Pending(pending.kind, pending.instruction, pending.prd, True)
        return {"route": "", "pause": pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


async def run_review(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    opts = options(route.instruction)
    result = await review.run(scope.deps, route.prd, opts, scope.incoming.policy, route.one_block)
    if result.status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if result.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    env = result.envelope
    shown = render.view(env, opts.focus) if opts.focus else env
    reply = render.render_json(shown) if scope.incoming.wants_json else render.render(env, opts.focus)
    return {"prd": route.prd, "instruction": route.instruction, "envelope": env, "data": shown, "reply": reply}


async def followup(state: dict, scope: Scope) -> dict:
    kind = scope.route.command.kind
    env = state.get("envelope") or {}
    if kind == "versions":
        return {"reply": prompts.NO_VERSIONS}
    if kind == "breakdown":
        return {"reply": render.render_breakdown(env), "data": env}
    if kind == "json":
        return {"reply": render.render_json(env), "data": env}
    focus = "" if kind == "all" else kind
    shown = render.view(env, focus) if focus else env
    reply = render.render_json(shown) if scope.incoming.wants_json else render.render(env, focus)
    return {"reply": reply, "data": shown}


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    status = await review.classify_scope(scope.deps, scope.incoming.text, scope.incoming.policy)
    if status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if state.get("envelope"):
        return {"reply": prompts.FOLLOWUP_HELP}
    return {"pause": intake_mod.need_prd(scope.incoming).to_metadata()}
