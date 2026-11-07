"""The graph's node bodies: what each route does with the stored state and this turn's input."""

from __future__ import annotations

import logging
from typing import Any

import checks
import intake as intake_mod
import prompts
import render
import request
import results
import smalltalk
import write
from census import Census, build, turn_language
from checks import Context
from inputs import Incoming
from llm import LlmError, TurnDeps, complete_json

log = logging.getLogger("test-writer.turns")


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, state)
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


def _source(files) -> str:
    return "\n\n".join(f.text for f in files if not f.generated)


def _numbers(census: Census, files, *texts: str) -> frozenset[float]:
    return frozenset(checks.allowed_numbers(_source(files), *texts, *(c.label for c in census.cases)))


async def run(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    language = turn_language(route.files)
    framework, note, explicit, loose, module = intake_mod.options(route, language)
    everything = build(route.files)
    names = {n.casefold() for c in everything.callables for n in (c.name, c.short, c.owner) if n}
    explicit = tuple(t for t in explicit if t != module)  # the module path is not a callable
    targets = tuple(t for t in (*explicit, *loose) if t.casefold() in names)
    notes = [note]
    if explicit and not targets:
        notes.append("None of the names you gave match the code (" + ", ".join(explicit) + "); I tested every "
                     "public function instead.")
    census = build(route.files, targets) if targets else everything
    if not census.selected:
        return {"reply": prompts.NO_CALLABLES}
    outcome = await write.write_tests(
        scope.deps, census, framework, _source(route.files), route.instruction, route.existing,
        scope.incoming.policy, _numbers(census, route.files, route.instruction), request.existing_names(route.existing),
        module=module)
    if outcome.status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if outcome.status == prompts.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    base = {"files": results.files_to_state(route.files), "instruction": route.instruction,
            "existing": route.existing}
    return results.finish(census, outcome, framework, module, [*notes, *census.notes], scope.incoming.wants_json, base)


async def followup(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    if command.kind == "json":
        env = state.get("envelope") or {}
        return {"reply": render.render_json(env), "data": env}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    files = results.files_from_state(state.get("files"))
    old = build(files, tuple(state.get("selected") or ()))
    base = {k: state.get(k) for k in ("files", "instruction", "existing")}
    if command.kind == "more":
        return await _more(state, scope, files, old, command.targets, base)
    return await _revise(state, scope, files, old, command.text, base)


async def _more(state: dict, scope: Scope, files, old: Census, targets, base: dict) -> dict:
    fresh = build(files, tuple(targets))
    previous = results.stored_outcome(state)
    framework = state.get("framework") or request.DEFAULT_FRAMEWORK[old.lang]
    outcome = await write.write_tests(
        scope.deps, fresh, framework, _source(files), str(state.get("instruction") or ""),
        str(state.get("existing") or ""), scope.incoming.policy,
        _numbers(fresh, files, str(state.get("instruction") or "")),
        request.existing_names(str(state.get("existing") or "")), {t.name for t in previous.tests},
        str(state.get("module") or ""))
    if outcome.status != "ok":
        return {"reply": smalltalk.OFF_TOPIC_REPLY if outcome.status == prompts.OFF_TOPIC else prompts.MODEL_FAILED}
    names = (*old.selected_names(), *fresh.selected_names())
    merged = build(files, names, cap=len(names))
    outcome.tests = results.remap(previous.tests, old, merged) + results.remap(outcome.tests, fresh, merged)
    outcome.helpers = previous.helpers + outcome.helpers
    outcome.imports = previous.imports + outcome.imports
    return results.finish(merged, outcome, framework, str(state.get("module") or ""), list(merged.notes),
                          scope.incoming.wants_json, base)


async def _revise(state: dict, scope: Scope, files, census: Census, text: str, base: dict) -> dict:
    named = request.framework_in(text)
    framework = named if named in request.FRAMEWORKS_FOR[census.lang] else state.get("framework")
    stored = [{k: v for k, v in t.items() if k != "imports"} for t in state.get("tests") or ()]
    messages = prompts.followup_messages(write.payload(census, census.selected, framework), stored, text,
                                         write.source_for(census, census.selected, _source(files)), framework,
                                         scope.incoming.policy)
    try:
        answer = await complete_json(scope.deps, messages, prompts.WRITE_CONTRACT, write.validate)
    except LlmError as e:
        log.warning("follow-up failed: %s", e)
        return {"reply": prompts.FOLLOWUP_FAILED}
    if answer["status"] == prompts.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    outcome = write.Outcome()
    ctx = Context(census, framework, tuple(c.name for c in census.selected),
                  _numbers(census, files, str(state.get("instruction") or ""), text),
                  request.existing_names(str(state.get("existing") or "")))
    await write.absorb(scope.deps, answer, ctx, set(), outcome, repairable=False)
    write.finish_imports(outcome, census, str(state.get("module") or ""))
    if not outcome.tests:
        return {"reply": prompts.FOLLOWUP_FAILED}
    return results.finish(census, outcome, framework, str(state.get("module") or ""), list(census.notes),
                          scope.incoming.wants_json, base)


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}

    def validate(data: Any) -> dict | None:
        ok = isinstance(data, dict) and data.get("status") in (prompts.OFF_TOPIC, "needs_code")
        return data if ok else None
    try:
        data = await complete_json(scope.deps, prompts.scope_messages(scope.incoming.text, scope.incoming.policy),
                                   prompts.SCOPE_CONTRACT, validate)
    except LlmError as e:
        log.warning("scope check failed: %s", e)  # asking for code never answers an off-topic question
        data = {"status": "needs_code"}
    if data["status"] == prompts.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return {"pause": intake_mod.need_code(scope.incoming).to_metadata()}
