"""The graph's node bodies: what each route does with the stored state and this turn's input."""

from __future__ import annotations

import envelope as envelope_mod
import followup as followup_mod
import intake as intake_mod
import pipeline
import prompts
import render
import smalltalk
from inputs import Incoming, word_count
from llm import TurnDeps
from plan import number_lines
from specific import Candidate


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, bool(state.get("envelope")))
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


async def run(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    lines = number_lines(route.plan)
    outcome = await pipeline.run(scope.deps, lines, route.context, scope.incoming.policy, route.count, route.focus)
    if outcome.status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if outcome.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    words = word_count(route.plan)
    stored = {"lines": list(lines), "context": list(route.context), "plan_words": words, "count": route.count,
              "focus": route.focus, "voice_failures": outcome.voice_failures}
    return {**stored, **_result(outcome, words, len(lines), scope, route.notes)}


def _result(outcome: pipeline.Outcome, words: int, line_count: int, scope: Scope, notes: tuple[str, ...]) -> dict:
    env = envelope_mod.build(outcome, words, line_count)
    survivors = [c.to_dict() for c in outcome.survivors]
    if not outcome.stories and not scope.incoming.wants_json:
        return {"envelope": env, "survivors": survivors, "data": env, "reply": prompts.NOTHING_SPECIFIC}
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(env, notes=notes)
    return {"envelope": env, "survivors": survivors, "data": env, "reply": reply}


async def followup(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    env = state.get("envelope") or {}
    modes = env.get("failure_modes") or []
    if command.kind == "lines":
        return {"reply": render.render_lines(state.get("lines") or [])}
    if command.kind == "json":
        return {"reply": render.render_json(env), "data": env}
    if command.kind in ("top", "all"):
        top = max(1, min(command.number, len(modes))) if command.kind == "top" else None
        view = {**env, "failure_modes": modes[:top], "items": modes[:top]} if top else env
        reply = render.render_json(view) if scope.incoming.wants_json else render.render(env, top=top)
        return {"reply": reply, "data": view}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    if command.kind == "deep":
        return await _deep(state, scope, modes, command.number)
    return await _add_context(state, scope, command.text)


async def _deep(state: dict, scope: Scope, modes: list[dict], number: int) -> dict:
    if not 1 <= number <= len(modes):
        return {"reply": f"There's no failure {number}; this pre-mortem has {len(modes)}."}
    lines, context = tuple(state.get("lines") or ()), tuple(state.get("context") or ())
    mode = modes[number - 1]
    causes = await followup_mod.deep_dive(scope.deps, mode, lines, context, pipeline.ground_of(lines, context),
                                          scope.incoming.policy)
    if causes is None:
        return {"reply": prompts.MODEL_FAILED}
    if not causes:
        return {"reply": f"None of the deeper causes I found for failure {number} could be tied to a line of "
                         "your plan, so I left them out."}
    return {"reply": render.render_deep(mode, causes)}


async def _add_context(state: dict, scope: Scope, text: str) -> dict:
    lines = tuple(state.get("lines") or ())
    context = (*(state.get("context") or ()), prompts.strip_markers(text))
    survivors = [Candidate.from_dict(c) for c in state.get("survivors") or ()]
    left_out = (state.get("envelope") or {}).get("left_out") or 0
    outcome = await pipeline.rerank(scope.deps, survivors, lines, context, scope.incoming.policy,
                                    state.get("count") or 7, state.get("focus") or "", left_out,
                                    state.get("voice_failures") or 0)
    note = ("_Context added. I re-checked the stored failure modes against it and rebuilt the assumptions and "
            "questions. Paste the plan again for a fresh run of all four lenses._")
    result = _result(outcome, state.get("plan_words") or 0, len(lines), scope, (note,))
    return {"context": list(context), **result}


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    status = await followup_mod.classify(scope.deps, scope.incoming.text, scope.incoming.policy)
    if status == followup_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if state.get("envelope"):
        return {"reply": prompts.FOLLOWUP_HELP}
    pending = intake_mod.need_plan(scope.incoming, scope.incoming.text)
    return {"pause": pending.to_metadata()}
