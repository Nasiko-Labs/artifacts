"""The graph's node bodies: what each route does with the stored plan and this turn's input."""

from __future__ import annotations

import answers
import envelope
import intake as intake_mod
import mcp_tools
import planning
import prompts
import queries
import render
import smalltalk
from inputs import Incoming
from llm import TurnDeps
from models import Plan

REPLACED = "_This replaces the earlier plan in this conversation._"


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None
        self.outcome: planning.Outcome | None = None


async def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, Plan.from_state(state.get("plan")))
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "offer_tools":
        # A request this agent does not plan (e.g. "file these in the tracker"): try whatever MCP
        # tools the operator attached, otherwise a generic "connect a tool" reply.
        reply = await mcp_tools.offer_or_decline(scope.deps, scope.incoming.text, smalltalk.INTRO)
        return {"route": "", "reply": reply}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    if route.kind == "loaded":
        scope.outcome = planning.Outcome("ok", route.plan)
        return {"route": "render"}
    if route.kind in ("plan", "revise") and not scope.deps.traceparent:
        return {"route": "", "reply": prompts.NO_TRACEPARENT}
    return {"route": route.kind}


async def plan(state: dict, scope: Scope) -> dict:
    route = scope.route
    scope.outcome = await planning.new_plan(scope.deps, route.brief, scope.incoming.policy, route.vague_used)
    return await _status_reply(scope, may_pause=not route.vague_used)


async def revise(state: dict, scope: Scope) -> dict:
    route = scope.route
    scope.outcome = await planning.revise(scope.deps, route.plan, route.instruction, scope.incoming.policy)
    return await _status_reply(scope, may_pause=False)


async def _status_reply(scope: Scope, may_pause: bool) -> dict:
    status = scope.outcome.status
    if status == "ok":
        return {"route": "render"}
    if status == "off_topic":
        # Out of this agent's scope: try the operator's attached MCP tools, else a generic reply.
        reply = await mcp_tools.offer_or_decline(scope.deps, scope.incoming.text, smalltalk.INTRO)
        return {"route": "", "reply": reply}
    if status == "needs_info" and may_pause:
        return {"route": "", "pause": intake_mod.need_goal(scope.incoming).to_metadata()}
    if status == "needs_info":
        return {"route": "", "reply": prompts.STILL_VAGUE}
    return {"route": "", "reply": prompts.MODEL_FAILED}


def render_node(state: dict, scope: Scope) -> dict:
    plan_obj = scope.outcome.plan
    env = envelope.build(plan_obj)
    lead = (REPLACED,) if scope.route.replaced else ()
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(plan_obj, env, lead)
    return {"plan": plan_obj.to_state(), "reply": reply, "data": env}


def query(state: dict, scope: Scope) -> dict:
    route = scope.route
    changed = queries.with_change(route.plan, route.command)
    current = changed or route.plan
    reply, data = answers.answer(current, route.command, scope.incoming.wants_json)
    update = {"reply": reply, "data": data}
    return {**update, "plan": current.to_state()} if changed else update
