"""The chain's step bodies: what each route does with this turn's input and the ranking re-read from history
(`recall.py`; the chain's state lives for this turn only). Every reply is recomputed from the tasks + ratings
(`views.result`), so a re-rank of a ranking found in history never calls the model; a bare list found there
is rated first."""

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

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps, self.incoming = deps, incoming
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = intake_mod.plan_turn(scope.incoming, bool(state.get("tasks")))
    scope.route = route
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
              "defaulted": result.defaulted, "failed": result.failed_batches, "instruction": parsed.instruction,
              "rated": True}
    return {**stored, **views.result(stored, scope, notes=route.notes)}


async def followup(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    kind = command.kind
    if kind == "unknown_preset":
        return {"reply": prompts.PRESET_HELP}
    rated = bool(state.get("rated"))
    if (kind in ("add", "rerate") or not rated) and not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    if kind == "add":
        return await (_add(state, scope, command.text) if rated else _add_unrated(state, scope, command.text))
    if kind == "rerate":
        return await _rerate(state, scope, note="Re-rated every task.")
    if not rated:  # a bare list found in history: rate it, then apply the command
        state, failure = await _rate_all(state, scope)
        if failure:
            return {"reply": failure}
    changes = views.apply(state, command, scope)
    if "reply" in changes:
        return changes
    merged = {**state, **changes}
    return {**changes, **views.result(merged, scope, view=views.view_of(command))}


async def _add(state: dict, scope: Scope, text: str) -> dict:
    tasks = list(state.get("tasks") or [])
    if len(tasks) >= MAX_TASKS:
        return {"reply": prompts.FULL}
    new = _new_task(tasks, text)
    result = await rate_mod.rate(scope.deps, [new], state.get("goals") or [], "", scope.incoming.policy)
    if result.status == rate_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if result.status == rate_mod.FAILED:
        return {"reply": prompts.MODEL_FAILED}
    stored = {**state, "tasks": [*tasks, new], "ratings": {**(state.get("ratings") or {}), **result.ratings},
              "defaulted": (state.get("defaulted") or 0) + result.defaulted}
    changes = {k: stored[k] for k in ("tasks", "ratings", "defaulted")}
    return {**changes, **views.result(stored, scope, notes=(f"Added #{len(stored['tasks'])} to the list.",))}


def _new_task(tasks: list[dict], text: str) -> dict:
    return {"id": f"t{_next_id(tasks)}", "text": " ".join(prompts.strip_markers(text).split())}


async def _add_unrated(state: dict, scope: Scope, text: str) -> dict:
    """A list found in history was never rated in this turn: add the task, then rate them all at once."""
    tasks = list(state.get("tasks") or [])
    if len(tasks) >= MAX_TASKS:
        return {"reply": prompts.FULL}
    added = {**state, "tasks": [*tasks, _new_task(tasks, text)]}
    return await _rerate(added, scope, note=f"Added #{len(added['tasks'])} to the list.")


async def _rate_all(state: dict, scope: Scope) -> tuple[dict, str]:
    """(the state with every task rated again, "") or (the state unchanged, the reply) when rating fails or
    is off topic."""
    result = await rate_mod.rate(scope.deps, list(state.get("tasks") or []), state.get("goals") or [],
                                 state.get("instruction") or "", scope.incoming.policy)
    if result.status != rate_mod.OK:
        return state, smalltalk.OFF_TOPIC_REPLY if result.status == rate_mod.OFF_TOPIC else prompts.MODEL_FAILED
    return {**state, "ratings": result.ratings, "defaulted": result.defaulted, "failed": result.failed_batches,
            "rated": True}, ""


async def _rerate(state: dict, scope: Scope, note: str = "") -> dict:
    stored, failure = await _rate_all(state, scope)
    if failure:
        return {"reply": failure}
    return {**stored, **views.result(stored, scope, notes=(note,) if note else ())}


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
    return {"reply": intake_mod.ask_for_list(scope.incoming)}
