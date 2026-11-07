"""The graph's node bodies: what each route does with the stored state and this turn's input."""

from __future__ import annotations

import envelope as envelope_mod
import followup as followup_mod
import intake as intake_mod
import pipeline
import prompts
import render
import smalltalk
from inputs import Incoming
from llm import TurnDeps
from split import Paste


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def stored_notes(state: dict) -> str:
    return Paste.from_dict(state.get("paste")).notes


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, bool(state.get("envelope")), stored_notes(state))
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


def _reply(env: dict, scope: Scope, view: str = "all") -> dict:
    text = render.render_json(env) if scope.incoming.wants_json else render.render(env, view)
    return {"reply": text, "data": env}


async def run(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    outcome = await pipeline.run(scope.deps, route.paste, scope.incoming.policy, route.personal,
                                 route.sensitive_included)
    if outcome.status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if outcome.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return {"paste": route.paste.to_dict(), "envelope": outcome.envelope, **_reply(outcome.envelope, scope)}


async def followup(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    env = state.get("envelope") or {}
    if command.kind == "json":
        return {"reply": render.render_json(env), "data": env}
    if command.kind in ("questions", "recognize", "sources", "all"):
        return _reply(env, scope, command.kind)
    if command.kind == "topic":
        point = {"id": f"m{sum(1 for p in env['talking_points'] if p['source'] == 'manager') + 1}",
                 "text": command.text, "source": "manager", "quote": command.text}
        new = envelope_mod.refresh(env, points=[point, *env["talking_points"]])
        return {"envelope": new, **_reply(new, scope)}
    points = env["talking_points"]
    if not 1 <= command.number <= len(points):
        return {"reply": f"There's no talking point {command.number}; this prep has {len(points)}."}
    new = envelope_mod.refresh(env, points=[p for n, p in enumerate(points, start=1) if n != command.number])
    return {"envelope": new, **_reply(new, scope)}


async def ask(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    paste = Paste.from_dict(state.get("paste"))
    updates = paste.notes if paste.mixed else paste.updates
    status, answer, quote = await followup_mod.ask(scope.deps, scope.incoming.text, paste.notes, updates,
                                                   scope.incoming.policy)
    if status == followup_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if status == followup_mod.FAILED:
        return {"reply": prompts.MODEL_FAILED}
    if status != followup_mod.OK:
        return {"reply": prompts.NOT_IN_NOTES + " " + prompts.FOLLOWUP_HELP}
    lead = f"{render.clean(answer)}\n\n" if answer else ""
    return {"reply": f'{lead}From what you pasted: "{render.clean(quote)}"'}


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    status = await followup_mod.classify(scope.deps, scope.incoming.text, scope.incoming.policy)
    if status == followup_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return {"pause": intake_mod.need_input(scope.incoming).to_metadata()}
