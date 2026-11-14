"""The humanizer LangGraph.

    START → intake ─┬─ work (humanize / envelope / re-run / scope call / show) → END
                    └─ fixed reply / pause ─────────────────────────────────────→ END

State is checkpointed per A2A contextId (thread_id): the last original draft (or envelope), the options it
used and the checked outcome, so "show the changes" costs nothing and "keep my em dashes" re-runs from the
original. The graph is compiled per turn because its nodes close over that turn's input and dependencies;
the checkpointer is shared. A pause leaves the graph as a `pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import checks
import envelope
import hitl
import intake
import llm
import options as opts
import prompts
import render
import rewrite
import smalltalk
from inputs import Incoming
from natural import verbatim_draft
from outcome import MODEL_FAILED, OFF_TOPIC, Outcome
from patterns import word_count


class HumanizerState(TypedDict, total=False):
    original: str          # the last free-text draft, as the user gave it
    options: dict          # the options that pass used
    outcome: dict          # Outcome.to_dict() of that pass
    envelope_in: dict      # or: the last input envelope ...
    fields: dict           # ... and each humanized field's Outcome.to_dict()
    route: str             # this turn only
    reply: str             # this turn only
    data: dict | None      # this turn only: the envelope for the artifact's data part
    pause: dict | None     # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: llm.TurnDeps, incoming: Incoming, forced: intake.Plan | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.plan: intake.Plan | None = None
        self.budget = llm.CallBudget()


class HumanizerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: llm.TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str,
                     deps: llm.TurnDeps) -> TurnResult:
        plan = intake.plan_resume(hitl.interpret(pending, incoming.text))
        return await self._run(_Scope(deps, incoming, forced=plan), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")), final.get("data"))


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(state: HumanizerState) -> dict:
        stored = bool(state.get("outcome") or state.get("fields"))
        scope.plan = scope.forced or intake.plan_turn(scope.incoming, stored)
        if scope.plan.route == "pause":
            return {"route": "", "pause": scope.plan.pending.to_metadata()}
        if scope.plan.route == "reply":
            return {"route": "", "reply": _join(scope.plan.reply, scope.plan.notes)}
        return {"route": "work"}

    async def work_node(state: HumanizerState) -> dict:
        return await _work(state, scope)

    graph = StateGraph(HumanizerState)
    graph.add_node("intake", intake_node)
    graph.add_node("work", work_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["work", END])
    graph.add_edge("work", END)
    return graph


async def _work(state: HumanizerState, scope: _Scope) -> dict:
    plan = scope.plan
    if plan.route == "show":
        return {"reply": _show(state, plan.command)}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    if plan.route == "classify":
        return await _classify(state, scope)
    if plan.route == "envelope":
        parsed = opts.parse(plan.request)
        return await _envelope(scope, plan.envelope, parsed.options, parsed.as_json)
    if plan.route == "rerun":
        parsed = opts.parse(plan.request, opts.Options.from_dict(state.get("options")))
        if state.get("fields"):
            return await _envelope(scope, state.get("envelope_in") or {}, parsed.options, parsed.as_json)
        if opts.parse(plan.request).options.shorter:   # this turn asks for shorter: shorten the version shown
            return await _shorten_last(state, scope, parsed)
        return await _free(scope, state.get("original") or "", parsed.options, parsed.as_json)
    parsed = opts.parse(plan.request)
    return await _free(scope, plan.draft, parsed.options, parsed.as_json)


async def _shorten_last(state: HumanizerState, scope: _Scope, parsed: opts.Parsed) -> dict:
    """"Make it shorter" shortens the stored version the user saw, capped below its length, so the result is
    measurably shorter than that version and a failed pass falls back to it, not to the AI draft. The stored
    original stays the user's draft."""
    original = state.get("original") or ""
    last = Outcome.from_dict(state.get("outcome"))
    version = last.text if last is not None and last.text.strip() else original
    capped = replace(parsed.options, max_words=checks.shorter_cap(word_count(version)))
    return await _free(scope, version, capped, parsed.as_json, original=original or version)


async def _free(scope: _Scope, draft: str, options: opts.Options, as_json: bool, original: str = "") -> dict:
    result = await rewrite.humanize(scope.deps, scope.budget, draft, options, scope.incoming.context)
    if result.status in (OFF_TOPIC, MODEL_FAILED):  # nothing edited: the stored draft, if any, is kept
        return {"reply": _join(render.outcome(result), scope.plan.notes)}
    data = envelope.build(result)
    reply = envelope.fenced(data) if as_json else render.outcome(result, scope.plan.notes)
    return {"reply": reply, "data": data, "original": original or draft, "options": options.to_dict(),
            "outcome": result.to_dict(), "envelope_in": {}, "fields": {}}


async def _envelope(scope: _Scope, source: dict, options: opts.Options, as_json: bool) -> dict:
    outcomes: dict[str, Outcome] = {}
    for name in envelope.fields_of(source):
        outcomes[name] = await rewrite.humanize(scope.deps, scope.budget, source[name], options,
                                                scope.incoming.context)
    if all(o.status == OFF_TOPIC for o in outcomes.values()):
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    data = envelope.build_from(source, outcomes)
    reply = envelope.fenced(data) if as_json else render.envelope_text(outcomes, scope.plan.notes)
    return {"reply": reply, "data": data, "envelope_in": dict(source), "options": options.to_dict(),
            "fields": {name: o.to_dict() for name, o in outcomes.items()}, "original": "", "outcome": {}}


async def _classify(state: HumanizerState, scope: _Scope) -> dict:
    try:
        answer = await llm.call_json(scope.deps, scope.budget, prompts.classify_messages(scope.plan.request))
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED}
    data = answer.data if isinstance(answer.data, dict) else {}
    status = data.get("status")
    if status == "has_draft":
        found = verbatim_draft(scope.plan.request, data.get("draft"))
        if found is not None:   # copied from the message: humanize it, the rest of the message is the request
            parsed = opts.parse(found[0])
            return await _free(scope, found[1], parsed.options, parsed.as_json)
        status = "needs_draft"  # not in the message, or too short: never edit text the user did not send
    if status != "needs_draft":  # fail closed: anything unclear is declined
        return {"reply": _join(smalltalk.OFF_TOPIC_REPLY, scope.plan.notes)}
    if state.get("outcome") or state.get("fields"):
        return {"reply": prompts.FOLLOWUP_HELP}
    return {"pause": hitl.need_draft(scope.plan.request).to_metadata()}


def _show(state: HumanizerState, command: str) -> str:
    fields = {k: Outcome.from_dict(v) for k, v in (state.get("fields") or {}).items()}
    primary = Outcome.from_dict(state.get("outcome")) or next(iter(fields.values()), None)
    if command == "changes":
        return render.changes(primary)
    if command == "patterns":
        return render.patterns_removed(primary)
    if fields:
        return envelope.fenced(envelope.build_from(state.get("envelope_in") or {}, fields))
    return envelope.fenced(envelope.build(primary)) if primary else prompts.NOTHING_STORED


def _join(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(p for p in (text, *notes) if p)
