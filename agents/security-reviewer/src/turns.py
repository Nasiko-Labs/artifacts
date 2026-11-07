"""The graph's node bodies: what each route does with the stored state and this turn's input."""

from __future__ import annotations

import envelope as envelope_mod
import followup as followup_mod
import intake as intake_mod
import pipeline
import prompts
import render
import severity
import smalltalk
from diff_parse import CodeFile
from inputs import Incoming, fence_text
from llm import LlmError, TurnDeps, complete_json
from secret_scan import unmask_display


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
    outcome = await pipeline.review(scope.deps, route.parsed, scope.incoming.policy, route.focus)
    if outcome.status == "no_traceparent":
        return {"reply": prompts.NO_TRACEPARENT}
    if outcome.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    env = envelope_mod.build(outcome.review)
    notes = tuple(f"_{n}_" for n in scope.incoming.notes)
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(env, notes=notes)
    return {"files": [f.to_dict() for f in outcome.files], "findings": list(outcome.review.findings),
            "displays": outcome.displays, "context": list(outcome.context), "envelope": env, "data": env,
            "reply": reply}


async def followup(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    env = state.get("envelope") or {}
    if command.kind == "json":
        return {"reply": render.render_json(env), "data": env}
    if command.kind in ("floor", "all"):
        floor = command.level if command.kind == "floor" else None
        view = {**env, "findings": [f for f in env.get("findings") or ()
                                    if floor is None or severity.at_or_above(f["severity"], floor)]}
        view["items"] = view["findings"]
        reply = render.render_json(view) if scope.incoming.wants_json else render.render(env, floor=floor)
        return {"reply": reply, "data": view}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    files = [CodeFile.from_dict(f) for f in state.get("files") or ()]
    findings = list(state.get("findings") or ())
    if command.number and not any(f.get("number") == command.number for f in findings):
        return {"reply": f"There's no finding {command.number}; this review has {len(findings)}."}
    answer = await followup_mod.ask(scope.deps, command, findings, files, scope.incoming.policy)
    if answer is None:
        return {"reply": prompts.MODEL_FAILED}
    if answer["status"] != "ok":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    displays = state.get("displays") or {}
    text = render.clean(unmask_display(answer["answer"], displays))
    if answer["test_code"]:
        text += "\n\n```\n" + unmask_display(answer["test_code"], displays).replace("```", "'''").strip() + "\n```"
    return {"reply": text + "\n\n_Answered from the stored review and the masked code; nothing was run._"}


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    messages = [{"role": "system", "content": prompts.SCOPE_SYSTEM}, *prompts.policy_block(scope.incoming.policy),
                {"role": "user", "content": fence_text("INPUT", scope.incoming.text)}]
    try:
        data = await complete_json(scope.deps, messages, '{"status": "needs_code" | "off_topic"}', _scope_valid,
                                   max_tokens=50)
    except LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if data["status"] == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return {"pause": intake_mod.need_code(scope.incoming).to_metadata()}


def _scope_valid(data) -> dict | None:
    ok = isinstance(data, dict) and data.get("status") in ("needs_code", "off_topic")
    return data if ok else None
