"""The graph's node bodies: what each route does with the stored state and this turn's input."""

from __future__ import annotations

import envelope as envelope_mod
import facts as facts_mod
import followup as followup_mod
import hitl
import intake as intake_mod
import prompts
import render
import smalltalk
from draft import draft
from inputs import Incoming, word_count
from llm import TurnDeps
from model import Runbook


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, bool(state.get("runbook")))
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


def _notes(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(n for group in groups for n in group if n)


def _italic(notes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"_{n}_" for n in notes)


async def run(state: dict, scope: Scope) -> dict:
    route, deps, policy = scope.route, scope.deps, scope.incoming.policy
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    extracted = await facts_mod.extract(deps, route.source, route.mode, route.instruction, policy)
    fixed = {"off_topic": smalltalk.OFF_TOPIC_REPLY, "no_material": prompts.NO_MATERIAL,
             "failed": prompts.MODEL_FAILED}.get(extracted.status)
    if fixed is not None:
        return {"reply": fixed}
    checked = extracted.checked
    drafted = await draft(deps, checked.facts, checked.service, route.mode, route.instruction, route.source, policy)
    if drafted.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if drafted.status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if not drafted.steps:
        return {"reply": prompts.NOTHING_USABLE}
    dropped = checked.dropped + drafted.dropped
    notes = _notes(route.notes, extracted.notes, _drop_note(dropped, drafted.rewritten))
    runbook = Runbook(title=drafted.title or f"Runbook: {checked.service}", purpose=drafted.purpose,
                      service=checked.service, mode=route.mode, steps=drafted.steps, facts=checked.facts,
                      words=word_count(route.source), dropped=dropped, rewritten=drafted.rewritten,
                      repair_failed=drafted.repair_failed or bool(extracted.notes), notes=notes)
    return {"source": route.source, **_result(runbook, scope.incoming.wants_json)}


def _drop_note(dropped: int, rewritten: int) -> tuple[str, ...]:
    out = []
    if dropped:
        out.append(f"{dropped} item(s) were left out: facts I couldn't find in your text, or suggested destructive "
                   "commands.")
    if rewritten:
        out.append(f"{rewritten} state-changing step(s) had no known rollback and now ask for the service owner's "
                   "approval first.")
    return tuple(out)


def _result(runbook: Runbook, wants_json: bool, view: str = render.FULL, extra: tuple[str, ...] = ()) -> dict:
    notes = _italic(_notes(runbook.notes, extra))
    markdown = render.render(runbook, render.FULL, notes)
    env = envelope_mod.build(runbook, markdown, render.fixed_lines(runbook))
    if wants_json:
        reply = render.render_json(env)
    else:
        reply = markdown if view == render.FULL else render.render(runbook, view, _italic(extra))
    return {"runbook": runbook.to_dict(), "envelope": env, "data": env, "reply": reply}


async def followup(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    runbook = Runbook.from_dict(state.get("runbook"))
    wants_json = scope.incoming.wants_json
    if command.kind == "json":
        return _result(runbook, True)
    if command.kind == "view":
        return _result(runbook, wants_json, command.view)
    if command.kind == "fill":
        updated, changed = followup_mod.apply_fill(runbook, command.name, command.value)
        if not changed:
            return {"reply": f"There's no {command.name} placeholder in this runbook."}
        return _result(updated, wants_json, extra=(f"Filled in {command.name} with your value.",))
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    status, updated = await followup_mod.add_step(scope.deps, runbook, command.text, state.get("source") or "",
                                                  scope.incoming.policy)
    if status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if status == "dropped":
        note = ("I left out the suggested step: it was destructive or unusable, and destructive commands are only "
                "shown when your own text used them.")
        return {"runbook": updated.to_dict(), "reply": note}
    return _result(updated, wants_json, extra=("Added one step; it went through the same safety checks.",))


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    status = await followup_mod.classify(scope.deps, scope.incoming.text, scope.incoming.policy)
    if status == followup_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if status == followup_mod.ABOUT_RUNBOOK and state.get("runbook"):
        return {"reply": prompts.FOLLOWUP_HELP}
    pending = hitl.need_source(scope.incoming.text, scope.incoming.wants_json)
    return {"pause": pending.to_metadata()}
