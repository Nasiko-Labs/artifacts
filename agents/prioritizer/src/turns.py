"""The graph's node bodies: what each route does with the stored state and this turn's input. Every reply is
recomputed from the stored tasks + ratings (`views.result`), so re-ranks never call the model."""

from __future__ import annotations

import followup as followup_mod
import intake as intake_mod
import prompts
import rate as rate_mod
import smalltalk
import views
from inputs import Incoming
from llm import TurnDeps
from tasks import MAX_TASKS


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, bool(state.get("tasks")))
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
    parsed = route.parsed
    task_list = [{"id": f"t{n}", "text": text} for n, text in enumerate(parsed.tasks, start=1)]
    result = await rate_mod.rate(scope.deps, task_list, parsed.goals, parsed.instruction, scope.incoming.policy)
    if result.status == rate_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if result.status == rate_mod.FAILED:
        return {"reply": prompts.MODEL_FAILED}
    stored = {"tasks": task_list, "ratings": result.ratings, "goals": list(parsed.goals),
              "preset": route.preset or "default", "pins": [], "as_of": route.as_of or "",
              "defaulted": result.defaulted, "failed": result.failed_batches, "instruction": parsed.instruction}
    return {**stored, **views.result(stored, scope, notes=route.notes)}


async def followup(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    kind = command.kind
    if kind in ("add", "rerate"):
        if not scope.deps.traceparent:
            return {"reply": prompts.NO_TRACEPARENT}
        return await (_add(state, scope, command.text) if kind == "add" else _rerate(state, scope))
    if kind == "unknown_preset":
        return {"reply": prompts.PRESET_HELP}
    changes = views.apply(state, command, scope)
    if "reply" in changes:
        return changes
    merged = {**state, **changes}
    return {**changes, **views.result(merged, scope, view=views.view_of(command))}


async def _add(state: dict, scope: Scope, text: str) -> dict:
    tasks = list(state.get("tasks") or [])
    if len(tasks) >= MAX_TASKS:
        return {"reply": prompts.FULL}
    new = {"id": f"t{_next_id(tasks)}", "text": " ".join(prompts.strip_markers(text).split())}
    result = await rate_mod.rate(scope.deps, [new], state.get("goals") or [], "", scope.incoming.policy)
    if result.status == rate_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if result.status == rate_mod.FAILED:
        return {"reply": prompts.MODEL_FAILED}
    stored = {**state, "tasks": [*tasks, new], "ratings": {**(state.get("ratings") or {}), **result.ratings},
              "defaulted": (state.get("defaulted") or 0) + result.defaulted}
    changes = {k: stored[k] for k in ("tasks", "ratings", "defaulted")}
    return {**changes, **views.result(stored, scope, notes=(f"Added #{len(stored['tasks'])} to the list.",))}


async def _rerate(state: dict, scope: Scope) -> dict:
    tasks = list(state.get("tasks") or [])
    result = await rate_mod.rate(scope.deps, tasks, state.get("goals") or [], state.get("instruction") or "",
                                 scope.incoming.policy)
    if result.status != rate_mod.OK:
        return {"reply": smalltalk.OFF_TOPIC_REPLY if result.status == rate_mod.OFF_TOPIC else prompts.MODEL_FAILED}
    stored = {**state, "ratings": result.ratings, "defaulted": result.defaulted, "failed": result.failed_batches}
    changes = {k: stored[k] for k in ("ratings", "defaulted", "failed")}
    return {**changes, **views.result(stored, scope, notes=("Re-rated every task.",))}


def _next_id(tasks: list[dict]) -> int:
    numbers = [int(t["id"][1:]) for t in tasks if str(t.get("id", "")).startswith("t") and t["id"][1:].isdigit()]
    return max(numbers, default=0) + 1


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    status = await followup_mod.classify(scope.deps, scope.incoming.text, scope.incoming.policy)
    if status == followup_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if state.get("tasks"):
        return {"reply": prompts.FOLLOWUP_HELP}
    return {"pause": scope.route.pending.to_metadata()}
