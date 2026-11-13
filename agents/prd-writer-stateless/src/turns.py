"""The turn's handlers: intake (pure routing), run (new PRD, revision, scope) and command (follow-ups). Each
returns this turn's state. A revision starts from the PRD the user last saw (`revision.py`); other follow-ups
rebuild the PRD from what `recall` found in the history, in one crew kickoff."""

from __future__ import annotations

from dataclasses import replace

import envelope
import followup
import intake
import pipeline
import prompts
import render
import revision
import smalltalk
from inputs import Incoming, word_count
from llm import TurnDeps
from prd import Prd, facts_from_state
from recall import DRAFT_MARK, Prior, recall

NOT_FOUND = "I can't find that entry in the PRD. Ask for the PRD again to see the current numbering."


class Scope:
    """One turn's input, dependencies and recalled history (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps, self.incoming = deps, incoming
        self.prior: Prior = recall(incoming.history)
        self.plan: intake.Plan | None = None


def intake_node(scope: Scope) -> dict:
    plan = intake.plan_turn(scope.incoming, scope.prior)
    scope.plan = plan
    if plan.route == "reply":
        return {"route": "", "reply": plan.reply}
    return {"route": "command" if plan.route == "command" else "run"}


async def run_node(scope: Scope) -> dict:
    plan, deps, incoming = scope.plan, scope.deps, scope.incoming
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    if plan.route == "scope":
        return await pipeline.scope_check(deps, incoming)
    if plan.route == "new":
        return await pipeline.new_prd(deps, incoming, plan.brief, plan.instruction)
    # An earlier PRD does not make an unrelated question a revision. A rebuild needs all three regular calls, so
    # there the check runs on the turn's spare (repair) call.
    if plan.route == "scope_then_revise":
        check = pipeline.scope_status if scope.prior.shown is not None else pipeline.scope_gate
        status = await check(deps, incoming)
        if status != "prd":
            return {"reply": prompts.MODEL_FAILED if status == "failed" else smalltalk.OFF_TOPIC_REPLY}
    source, instruction = _source(scope)
    if scope.prior.shown is not None:  # start from the PRD the user last saw
        update = await revision.revise_shown_prd(deps, incoming, source, instruction, plan.instruction,
                                                 scope.prior.shown)
    else:  # it is not in the history any more: rebuild it from the brief first, and say so
        update = await revision.rebuild_and_revise(deps, incoming, source, instruction, plan.instruction)
    return _from_draft(scope, update)


async def command_node(scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    state = await _rebuilt(scope)
    if not state.get("prd"):
        return state
    return _from_draft(scope, _command(state, scope.plan.command, scope.incoming.wants_json))


def _source(scope: Scope) -> tuple[str, str]:
    """(the recalled brief and its instruction), or our last draft when the brief was trimmed."""
    prior = scope.prior
    if prior.brief:
        return prior.brief, prior.instruction
    title = prior.draft.splitlines()[0].removeprefix(DRAFT_MARK).strip()
    return prior.draft, f"Write a PRD for {title}"


async def _rebuilt(scope: Scope) -> dict:
    """The PRD state again from recalled material; a dict without "prd" is a fixed reply to send instead."""
    source, instruction = _source(scope)
    built = await pipeline.new_prd(scope.deps, replace(scope.incoming, wants_json=False), source, instruction)
    return {**built, "notes": []} if built.get("prd") else built


def _from_draft(scope: Scope, update: dict) -> dict:
    """Says so when the brief was trimmed and the PRD was rebuilt from our last draft."""
    if scope.prior.brief or not update.get("prd") or update.get("reply", "").startswith("```"):
        return update
    return {**update, "reply": f"{update['reply']}\n\n_{prompts.FROM_DRAFT_NOTE}_"}


def _command(state: dict, command: followup.Command, wants: bool) -> dict:
    prd = Prd.from_dict(state.get("prd"))
    cap = state.get("cap") or render.View().cap
    if command.kind == "json":
        return {"reply": envelope.as_json_text(state.get("envelope") or {}), "data": state.get("envelope"),
                "prd": state.get("prd")}
    if command.kind in ("questions", "sources"):
        return _rerender(state, prd, cap, command.kind, wants)
    if command.kind == "cap":
        return _rerender(state, prd, command.cap, "full", wants)
    changed = followup.drop(prd, command)
    if changed is None:
        return {"reply": NOT_FOUND}
    return _rerender(state, changed, cap, "full", wants)


def _rerender(state: dict, prd: Prd, cap: int, kind: str, wants_json: bool) -> dict:
    facts = facts_from_state(state.get("facts"))
    dropped = state.get("dropped") or 0
    full = render.render(prd, facts, render.View("full", cap), dropped, state.get("notes") or ())
    env = envelope.build(prd, facts, word_count(state.get("brief") or ""), full, dropped)
    reply = full if kind == "full" else render.render(prd, facts, render.View(kind, cap), dropped)
    if wants_json:
        reply = envelope.as_json_text(env)
    return {"reply": reply, "data": env, "prd": prd.to_dict()}
