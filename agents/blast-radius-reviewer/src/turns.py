"""The graph's node bodies: intake routing, analyse (code), judge (LLM map), merge + render, scope check."""

from __future__ import annotations

import analyse as analyse_mod
import hitl
import intake as intake_mod
import judge as judge_mod
import llm
import merge as merge_mod
import prompts
import render
import smalltalk
from inputs import MAX_CONTEXT_CHARS, Incoming

OFF_TOPIC, IN_SCOPE = "off_topic", "in_scope"


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: llm.TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, state.get("fired") if state.get("envelope")
                                                 else None)
    scope.route = route
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "json":
        env = state.get("envelope") or {}
        return {"route": "", "reply": render.render_json(env), "data": env}
    if not scope.deps.traceparent:
        return {"route": "", "reply": prompts.NO_TRACEPARENT}
    if route.kind == "rejudge":
        context = "\n".join(x for x in (state.get("context") or "", route.added_context) if x)
        note = f"_Re-checked {len(route.ids)} item(s) against the context you added; the others are unchanged._"
        return {"route": "judge", "to_judge": list(route.ids), "context": context[-MAX_CONTEXT_CHARS:],
                "notes": [note]}
    return {"route": "analyse" if route.kind == "run" else route.kind}


def analyse(state: dict, scope: Scope) -> dict:
    request = scope.route.request
    result = analyse_mod.analyse(request.diff, request.context)
    return {"diff": request.diff, "context": request.context, "intent": request.intent, "fired": result.fired,
            "coverage": result.coverage, "judgements": {}, "to_judge": [f["id"] for f in result.fired],
            "notes": list(scope.route.notes)}


async def judge(state: dict, scope: Scope) -> dict:
    wanted = set(state.get("to_judge") or ())
    fired = [f for f in state.get("fired") or () if f["id"] in wanted]
    if not fired:
        return {}
    run = await judge_mod.run(scope.deps, fired, state.get("context") or "", state.get("intent") or "",
                              scope.incoming.policy)
    judgements = {k: v for k, v in (state.get("judgements") or {}).items() if k not in wanted}
    return {"judgements": {**judgements, **run.judgements}}


async def merge(state: dict, scope: Scope) -> dict:
    fired, diff, context = state.get("fired") or [], state.get("diff") or "", state.get("context") or ""
    merged = merge_mod.merge(fired, state.get("judgements") or {}, diff, context)
    fact, _ = await merge_mod.one_fact(scope.deps, merged.items, diff, context, scope.incoming.policy)
    coverage = {**(state.get("coverage") or {}), "unjudged": len(merged.items) - sum(
        i["applies"] != "unjudged" for i in merged.items)}
    repaired = scope.deps.budget.repairs_left < llm.MAX_REPAIRS_PER_TURN
    env = merge_mod.envelope(merged, fact=fact, fired_count=len(fired), coverage=coverage, repaired=repaired,
                             context_given=bool(context.strip()), intent=state.get("intent") or "")
    notes = tuple(state.get("notes") or ())
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(env, notes)
    return {"envelope": env, "data": env, "reply": reply, "to_judge": [], "notes": []}


async def scope_check(state: dict, scope: Scope) -> dict:
    def validate(answer):
        status = answer.get("status") if isinstance(answer, dict) else None
        return {"status": status} if status in (OFF_TOPIC, IN_SCOPE) else None

    try:
        answer = await llm.complete_json(scope.deps, prompts.scope_messages(scope.incoming.text, scope.incoming.policy),
                                         prompts.SCOPE_SYSTEM, validate, max_tokens=50)
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if answer["status"] == OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if state.get("envelope"):
        return {"reply": prompts.NO_MATCHING_ITEM}
    return {"pause": hitl.Pending(wants_json=scope.incoming.wants_json).to_metadata()}
