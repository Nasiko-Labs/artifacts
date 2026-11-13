"""What each route does with this turn's input and the plan and pre-mortem re-read from history
(`recall.py`; the turn's state dict lives for this turn only). A view of a pre-mortem found in
history costs no call; when only the plan survived, the pre-mortem is run again on it."""

from __future__ import annotations

import envelope as envelope_mod
import followup as followup_mod
import intake as intake_mod
import pipeline
import prompts
import render
import smalltalk
from inputs import Incoming, word_count
from llm import TurnDeps, UnusableAnswer
from plan import number_lines
from recall import Prior

FAILED_REPLIES = {"failed": prompts.MODEL_FAILED, "unusable": prompts.MODEL_UNUSABLE}


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, prior: Prior | None = None):
        self.deps, self.incoming, self.prior = deps, incoming, prior or Prior()
        self.route: intake_mod.Route | None = None


def seed(prior: Prior) -> dict:
    """The starting state for one turn: what history still holds."""
    state: dict = {}
    if prior.plan is not None:
        state.update(lines=list(number_lines(prior.plan.text)), context=list(prior.plan.context),
                     plan_words=prior.plan.words)
    if prior.envelope is not None:
        state["envelope"] = prior.envelope
    return state


def intake(state: dict, scope: Scope) -> dict:
    route = intake_mod.plan_turn(scope.incoming, scope.prior)
    scope.route = route
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


async def run(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    lines = number_lines(route.plan)
    outcome = await pipeline.run(scope.deps, lines, route.context, scope.incoming.policy, route.count, route.focus)
    if outcome.status in FAILED_REPLIES:
        return {"reply": FAILED_REPLIES[outcome.status]}
    if outcome.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    words = word_count(route.plan)
    stored = {"lines": list(lines), "context": list(route.context), "plan_words": words}
    return {**stored, **_result(outcome, words, len(lines), scope, route.notes)}


def _result(outcome: pipeline.Outcome, words: int, line_count: int, scope: Scope, notes: tuple[str, ...]) -> dict:
    env = envelope_mod.build(outcome, words, line_count)
    if not outcome.stories and not scope.incoming.wants_json:
        return {"envelope": env, "data": env, "reply": prompts.NOTHING_SPECIFIC}
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(env, notes=notes)
    return {"envelope": env, "data": env, "reply": reply}


async def followup(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    lines = state.get("lines") or []
    if command.kind == "lines":
        return {"reply": render.render_lines(lines) if lines else prompts.NOT_IN_HISTORY}
    if not state.get("envelope") and not lines:
        return {"reply": prompts.NOT_IN_HISTORY}
    if command.kind != "context" and state.get("envelope"):
        return await _on_result(state, scope, command)
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    if command.kind == "context":  # a fresh run on the plan with the new context
        context = (*(state.get("context") or ()), prompts.strip_markers(command.text))
        result = await _fresh(state, scope, context, prompts.CONTEXT_NOTE)
        return {"context": list(context), **result}
    if command.kind == "deep":
        return await _fresh_then_deep(state, scope, command)
    result = await _fresh(state, scope, tuple(state.get("context") or ()), prompts.RERUN_NOTE)
    if command.kind in ("top", "all", "json") and "envelope" in result:
        return await _on_result({**state, **result}, scope, command)
    return result


async def _fresh_then_deep(state: dict, scope: Scope, command) -> dict:
    """The pre-mortem was lost from history but the plan was not: run it again, then go deeper on the asked
    failure of the NEW run. The new pre-mortem follows the deep dive so later turns can work from it."""
    result = await _fresh(state, scope, tuple(state.get("context") or ()), "")
    if "envelope" not in result:
        return result
    deep = await _on_result({**state, **result}, scope, command)
    reply = "\n\n".join((prompts.RERUN_DEEP_NOTE, deep["reply"], result["reply"]))
    return {**result, "reply": reply}


async def _on_result(state: dict, scope: Scope, command) -> dict:
    """A command on a pre-mortem in hand: re-render (zero calls) or one deep-dive call."""
    env = state["envelope"]
    modes = env.get("failure_modes") or []
    if command.kind == "json":
        return {"reply": render.render_json(env), "data": env}
    if command.kind in ("top", "all"):
        top = max(1, min(command.number, len(modes))) if command.kind == "top" else None
        view = {**env, "failure_modes": modes[:top], "items": modes[:top]} if top else env
        reply = render.render_json(view) if scope.incoming.wants_json else render.render(env, top=top)
        return {"reply": reply, "data": view}
    if not state.get("lines"):
        return {"reply": prompts.NOT_IN_HISTORY}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    return await _deep(state, scope, modes, command.number)


async def _fresh(state: dict, scope: Scope, context: tuple[str, ...], note: str) -> dict:
    lines = tuple(state.get("lines") or ())
    count, focus = intake_mod.options("")
    outcome = await pipeline.run(scope.deps, lines, context, scope.incoming.policy, count, focus)
    if outcome.status in FAILED_REPLIES:
        return {"reply": FAILED_REPLIES[outcome.status]}
    if outcome.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return _result(outcome, state.get("plan_words") or 0, len(lines), scope, (note,))


async def _deep(state: dict, scope: Scope, modes: list[dict], number: int) -> dict:
    if not 1 <= number <= len(modes):
        return {"reply": f"There's no failure {number}; this pre-mortem has {len(modes)}."}
    lines, context = tuple(state.get("lines") or ()), tuple(state.get("context") or ())
    mode = modes[number - 1]
    try:
        causes = await followup_mod.deep_dive(scope.deps, mode, lines, context, pipeline.ground_of(lines, context),
                                              scope.incoming.policy)
    except UnusableAnswer:
        return {"reply": prompts.MODEL_UNUSABLE}
    if causes is None:
        return {"reply": prompts.MODEL_FAILED}
    if not causes:
        return {"reply": f"None of the deeper causes I found for failure {number} could be tied to a line of "
                         "your plan, so I left them out."}
    return {"reply": render.render_deep(mode, causes)}


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    status = await followup_mod.classify(scope.deps, scope.incoming.text, scope.incoming.policy)
    if status == followup_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if state.get("envelope") or state.get("lines"):
        return {"reply": prompts.FOLLOWUP_HELP}
    return {"reply": intake_mod.ask_for_plan(scope.incoming)}
