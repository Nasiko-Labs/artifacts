"""The turn steps: what each route does with this turn's input. Nothing is stored: a follow-up rebuilds
the runbook from the source the intake found in the chat history, replays the earlier fills, then applies the
command."""

from __future__ import annotations

import envelope as envelope_mod
import facts as facts_mod
import followup as followup_mod
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

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps, self.incoming = deps, incoming
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = intake_mod.plan_turn(scope.incoming)
    scope.route = route
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


def _notes(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(n for group in groups for n in group if n)


def _italic(notes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"_{n}_" for n in notes)


async def run(state: dict, scope: Scope) -> dict:
    built = await _build(scope)
    if isinstance(built, dict):
        return built
    return _result(built, scope.incoming.wants_json)


async def _build(scope: Scope) -> Runbook | dict:
    """The checked runbook for this turn's source, or a fixed-reply dict."""
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
    return Runbook(title=drafted.title or f"Runbook: {checked.service}", purpose=drafted.purpose,
                   service=checked.service, mode=route.mode, steps=drafted.steps, facts=checked.facts,
                   words=word_count(route.source), dropped=dropped, rewritten=drafted.rewritten,
                   repair_failed=drafted.repair_failed or bool(extracted.notes), notes=notes)


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
    return {"data": env, "reply": reply}


async def followup(state: dict, scope: Scope) -> dict:
    built = await _build(scope)
    if isinstance(built, dict):
        return built
    runbook = _replay_fills(built, scope.route.fills)
    return await _apply_command(runbook, scope, (prompts.REBUILT,))


def _replay_fills(runbook: Runbook, fills: tuple[followup_mod.Command, ...]) -> Runbook:
    for fill in fills:
        runbook, _ = followup_mod.apply_fill(runbook, fill.name, fill.value)
    return runbook


async def _apply_command(runbook: Runbook, scope: Scope, extra: tuple[str, ...]) -> dict:
    command, wants_json = scope.route.command, scope.incoming.wants_json
    if command.kind == "json":
        return _result(runbook, True)
    if command.kind == "view":
        return _result(runbook, wants_json, command.view, extra)
    if command.kind == "fill":
        updated, changed = followup_mod.apply_fill(runbook, command.name, command.value)
        if not changed:
            return {"reply": f"There's no {command.name} placeholder in this runbook."}
        return _result(updated, wants_json, extra=(*extra, f"Filled in {command.name} with your value."))
    status, updated = await followup_mod.add_step(scope.deps, runbook, command.text, scope.route.source,
                                                  scope.incoming.policy)
    if status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if status == "dropped":
        return {"reply": ("I left out the suggested step: it was destructive or unusable, and destructive commands "
                          "are only shown when your own text used them.")}
    return _result(updated, wants_json, extra=(*extra, "Added one step; it went through the same safety checks."))


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    status = await followup_mod.classify(scope.deps, scope.incoming.text, scope.incoming.policy)
    if status == followup_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if status == followup_mod.ABOUT_RUNBOOK and scope.incoming.history:
        found = intake_mod.earlier_source(scope.incoming.history) is not None
        return {"reply": prompts.FOLLOWUP_HELP if found else prompts.SOURCE_TRIMMED}
    return {"reply": prompts.ASK_SOURCE}
