"""The graph's node bodies. Each returns a state update; `reply`, `data` and `pause` are this turn only, while
`brief` (the stored brief: sources, claims, findings, unknowns) persists per conversation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import envelope
import followup
import intake
import llm
import pauses
import pipeline
import prompts
import render
import smalltalk
from inputs import Incoming
from model import Claim

SYNTH_FIXED = {"off_topic": smalltalk.OFF_TOPIC_REPLY, "failed": prompts.MODEL_FAILED, "empty": prompts.NOTHING_BACKED}


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: llm.TurnDeps, incoming: Incoming, forced: intake.Plan | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.plan: intake.Plan | None = None


def intake_node(state: Mapping[str, Any], scope: Scope) -> dict:
    stored = state.get("brief")
    next_source = len((stored or {}).get("sources", ())) + 1
    plan = scope.forced or intake.plan_turn(scope.incoming, stored, next_source)
    scope.plan = plan
    if plan.route == "pause":
        return {"route": "", "pause": plan.pending.to_metadata()}
    if plan.route == "reply":
        return {"route": "", "reply": plan.reply}
    if plan.route == "view":
        return {"route": "", **_shown(stored, plan.view, scope.incoming.wants_json)}
    if plan.route in ("synth", "add", "ask", "scope") and not scope.deps.traceparent:
        return {"route": "", "reply": prompts.NO_TRACE_CONTEXT}
    return {"route": plan.route}


async def synth_node(state: Mapping[str, Any], scope: Scope) -> dict:
    plan = scope.plan
    notes = (*scope.incoming.notes, *((prompts.LINKS_NOTE,) if plan.has_links else ()))
    prior = state.get("brief") if plan.route == "add" else None
    if prior:
        notes = tuple(dict.fromkeys((*prior.get("notes", ()), *notes)))
    outcome = await pipeline.run(scope.deps, plan.question, plan.sources, scope.incoming.context, notes, prior)
    if outcome.status != "ok":  # the stored brief, if any, is kept
        return {"reply": SYNTH_FIXED[outcome.status]}
    return {"brief": outcome.brief, **_shown(outcome.brief, "default", scope.incoming.wants_json)}


async def ask_node(state: Mapping[str, Any], scope: Scope) -> dict:
    brief = state["brief"]
    claims = [Claim.from_dict(c) for c in brief["claims"]]
    answer = await followup.ask(scope.deps, scope.incoming.text, claims, scope.incoming.context)
    if not answer.not_covered:
        return {"reply": answer.reply}
    unknown = f"What do sources say about {answer.not_covered}? (the sources don't cover it)"
    updated = {**brief, "unknowns": list(dict.fromkeys([*brief["unknowns"], unknown])),
               "extra_unknowns": list(dict.fromkeys([*brief.get("extra_unknowns", []), unknown]))}
    return {"brief": updated, "reply": answer.reply}


async def scope_node(state: Mapping[str, Any], scope: Scope) -> dict:
    """No sources and no brief: decline an off-topic message, else ask for the sources (Q2)."""
    msgs = prompts.messages("SCOPE", "Decide whether the user's message is a research request this agent could "
                            "serve once the user pastes sources.", prompts.SCOPE_CONTRACT,
                            [prompts.fence("USER_REQUEST", scope.incoming.text)], scope.incoming.context)
    try:
        parsed = await llm.complete_json(scope.deps, msgs, prompts.SCOPE_CONTRACT, parse_scope)
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if parsed["status"] == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    question = " ".join(prompts.strip_markers(scope.incoming.text).split())[:300]
    pending = pauses.Pending(pauses.NEED_SOURCES, question=question, wants_json=scope.incoming.wants_json)
    return {"pause": pending.to_metadata()}


def parse_scope(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ("in_scope", "off_topic"):
        return None
    return {"status": data["status"]}


def _shown(brief: dict | None, view: str, wants_json: bool) -> dict:
    if not brief:
        return {"reply": prompts.NO_BRIEF_YET}
    markdown = render.render(brief)
    env = envelope.build(brief, markdown)
    reply = render.render_json(env) if wants_json else (markdown if view == "default" else render.render(brief, view))
    return {"reply": reply, "data": env}
