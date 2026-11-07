"""The graph's node bodies: what each route does with the stored draft and this turn's input."""

from __future__ import annotations

import followup as followup_mod
import intake as intake_mod
import pipeline
import prompts
import render
import smalltalk
from finish import finish
from inputs import Incoming
from llm import LlmError, TurnDeps, complete_json

SCOPE_STATUSES = ("off_topic", "design_request")


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, bool(state.get("doc")))
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "replies": [route.reply]}
    return {"route": route.kind}


def _done(drafted: pipeline.Drafted, scope: Scope, notes: tuple[str, ...] = ()) -> dict:
    if drafted.status == "off_topic":
        return {"replies": [smalltalk.OFF_TOPIC_REPLY]}
    if drafted.status == "empty":
        return {"replies": [prompts.NO_REQUIREMENTS]}
    if drafted.status != "ok" or drafted.doc is None:
        return {"replies": [prompts.MODEL_FAILED]}
    done = finish(drafted.doc, scope.incoming.wants_json, notes)
    return {"doc": done.doc, "envelope": done.envelope, "data": done.envelope, "replies": list(done.replies)}


async def draft(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"replies": [prompts.NO_TRACEPARENT]}
    drafted = await pipeline.draft(scope.deps, scope.route.request, scope.incoming.policy)
    return _done(drafted, scope)


async def followup(state: dict, scope: Scope) -> dict:
    command, doc = scope.route.command, state.get("doc") or {}
    if command.kind == "json":
        return {"replies": render.render_json(state.get("envelope") or {}), "data": state.get("envelope")}
    if not scope.deps.traceparent:
        return {"replies": [prompts.NO_TRACEPARENT]}
    policy = scope.incoming.policy
    if command.kind == "answers":
        open_qs = doc.get("questions") or []
        unknown = followup_mod.unknown_ids(command.answers, open_qs)
        if unknown:
            listed = ", ".join(q["id"] for q in open_qs) or "none"
            return {"replies": [prompts.NO_SUCH_QUESTION.format(ids=", ".join(unknown), open=listed)]}
        answered = followup_mod.apply_answers(doc, command.answers, open_qs)
        instruction = followup_mod.revise_instruction(command.answers)
        closed = ", ".join(qid for qid, _ in command.answers)
        drafted = await pipeline.revise(scope.deps, answered, instruction, policy)
        return _done(drafted, scope, (f"_Updated with your answers; closed {closed}._",))
    if command.kind == "add":
        drafted = await pipeline.add_requirements(scope.deps, doc, command.text, policy)
        return _done(drafted, scope, ("_Requirements added; coverage and questions recomputed._",))
    drafted = await pipeline.revise(scope.deps, doc, command.text, policy)
    return _done(drafted, scope, ("_Revised as asked._",))


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"replies": [prompts.NO_TRACEPARENT]}

    def validate(data):
        status = str(data.get("status") or "").casefold() if isinstance(data, dict) else ""
        return {"status": status} if status in SCOPE_STATUSES else None

    try:
        verdict = await complete_json(scope.deps, prompts.scope_messages(scope.incoming.text, scope.incoming.policy),
                                      prompts.SCOPE_CONTRACT, validate, max_tokens=50)
    except LlmError:
        return {"replies": [prompts.MODEL_FAILED]}
    if verdict["status"] == "off_topic":
        return {"replies": [smalltalk.OFF_TOPIC_REPLY]}
    return {"pause": intake_mod.need_requirements(scope.incoming).to_metadata()}
