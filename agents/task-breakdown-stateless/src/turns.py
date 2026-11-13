"""The turn's steps: what each route does with the recovered plan and this turn's input. No step ever pauses;
every route ends with a reply. A step returns the next route ("" = done) and, when done, the reply."""

from __future__ import annotations

from dataclasses import dataclass

import answers
import envelope
import fit
import intake as intake_mod
import planning
import prompts
import queries
import render
import smalltalk
from inputs import Incoming
from llm import TurnDeps

REPLACED = "_This replaces the earlier plan in this conversation._"
REBUILT = ("_The earlier plan wasn't in the conversation history I received, so I rebuilt it from your goal with "
           "this change._")


@dataclass(frozen=True)
class Step:
    route: str = ""             # plan | revise | render | query | "" (done)
    reply: str = ""
    data: dict | None = None    # the envelope to attach as a data part


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps, self.incoming = deps, incoming
        self.route: intake_mod.Route | None = None
        self.outcome: planning.Outcome | None = None


def intake(scope: Scope) -> Step:
    route = intake_mod.plan_turn(scope.incoming)
    scope.route = route
    if route.kind == "reply":
        return Step(reply=route.reply)
    if route.kind == "loaded":
        scope.outcome = planning.Outcome("ok", route.plan)
        return Step("render")
    if route.kind in ("plan", "revise") and not scope.deps.traceparent:
        return Step(reply=prompts.NO_TRACEPARENT)
    return Step(route.kind)


async def plan(scope: Scope) -> Step:
    outcome = await planning.new_plan(scope.deps, scope.route.brief, scope.incoming.policy)
    if scope.route.rebuilt and outcome.status == "ok":
        outcome = planning.Outcome("ok", fit.fit(outcome.plan))
    scope.outcome = outcome
    return _status_step(scope)


async def revise(scope: Scope) -> Step:
    route = scope.route
    scope.outcome = await planning.revise(scope.deps, route.plan, route.instruction, scope.incoming.policy)
    return _status_step(scope)


def _status_step(scope: Scope) -> Step:
    status = scope.outcome.status
    if status == "ok":
        return Step("render")
    if status == "off_topic":
        return Step(reply=smalltalk.OFF_TOPIC_REPLY)
    if status == "needs_info":
        return Step(reply=prompts.STILL_VAGUE)
    return Step(reply=prompts.MODEL_FAILED)


def render_step(scope: Scope) -> Step:
    plan_obj = scope.outcome.plan
    env = envelope.build(plan_obj)
    lead = (REBUILT,) if scope.route.rebuilt else (REPLACED,) if scope.route.replaced else ()
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(plan_obj, env, lead)
    return Step(reply=reply, data=env)


def query(scope: Scope) -> Step:
    route = scope.route
    changed = queries.with_change(route.plan, route.command)
    reply, data = answers.answer(changed or route.plan, route.command, scope.incoming.wants_json)
    return Step(reply=reply, data=data)
