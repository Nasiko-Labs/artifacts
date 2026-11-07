"""The graph's node bodies: intake (pure routing), run (model-calling paths) and command (zero-call follow-ups
on the stored PRD). Each returns a state update."""

from __future__ import annotations

import envelope
import followup
import intake
import pauses
import pipeline
import prompts
import render
from inputs import Incoming, word_count
from llm import TurnDeps
from prd import Prd, facts_from_state

CLEARED = {"brief": "", "instruction": "", "answers": [], "facts": [], "prd": None, "envelope": None,
           "dropped": 0, "notes": []}
NOT_FOUND = "I can't find that entry in the PRD. Ask for the PRD again to see the current numbering."


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake.Plan | None = None,
                 pending: pauses.Pending | None = None):
        self.deps, self.incoming, self.forced, self.pending = deps, incoming, forced, pending
        self.plan: intake.Plan | None = None


def intake_node(state: dict, scope: Scope) -> dict:
    plan = scope.forced or intake.plan_turn(scope.incoming, bool(state.get("prd")))
    scope.plan = plan
    if plan.route == "reply":
        return {"route": "", "reply": plan.reply}
    if plan.route == "pause":
        return {"route": "", "pause": plan.pending.to_metadata()}
    if plan.route == "command":
        return {"route": "command"}
    return {"route": "run"}


async def run_node(state: dict, scope: Scope) -> dict:
    plan, deps, incoming = scope.plan, scope.deps, scope.incoming
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    if plan.route == "scope":
        return await pipeline.scope_check(deps, incoming, plan.pending)
    if plan.route == "who":
        return await pipeline.answer_who(deps, incoming, scope.pending, plan.audience)
    if plan.route == "revise":
        return await pipeline.revise_prd(deps, incoming, state, plan.instruction)
    return await pipeline.new_prd(deps, incoming, plan.brief, plan.instruction)


def command_node(state: dict, scope: Scope) -> dict:
    command = scope.plan.command
    prd = Prd.from_dict(state.get("prd"))
    cap = state.get("cap") or render.View().cap
    wants = scope.incoming.wants_json
    if command.kind == "start_over":
        return {**CLEARED, "reply": prompts.STARTED_OVER}
    if command.kind == "json":
        return {"reply": envelope.as_json_text(state.get("envelope") or {}), "data": state.get("envelope")}
    if command.kind == "questions":
        return _rerender(state, prd, cap, "questions", wants)
    if command.kind == "sources":
        return _rerender(state, prd, cap, "sources", wants)
    if command.kind == "cap":
        return {**_rerender(state, prd, command.cap, "full", wants), "cap": command.cap}
    changed = followup.drop(prd, command)
    if changed is None:
        return {"reply": NOT_FOUND}
    return {**_rerender(state, changed, cap, "full", wants), "prd": changed.to_dict()}


def _rerender(state: dict, prd: Prd, cap: int, kind: str, wants_json: bool) -> dict:
    facts = facts_from_state(state.get("facts"))
    dropped = state.get("dropped") or 0
    full = render.render(prd, facts, render.View("full", cap), dropped, state.get("notes") or ())
    env = envelope.build(prd, facts, word_count(state.get("brief") or ""), full, dropped)
    reply = full if kind == "full" else render.render(prd, facts, render.View(kind, cap), dropped)
    if wants_json:
        reply = envelope.as_json_text(env)
    return {"reply": reply, "envelope": env, "data": env}

