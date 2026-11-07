"""The promise-extractor LangGraph.

    START → intake ─┬─ scan (extract → check → state → merge) → render → END
                    ├─ followup (re-render, zero calls; or one question) → END
                    └─ fixed reply / pause ──────────────────────────────→ END

State is checkpointed per A2A contextId (thread_id): the merged promise list, the run's counts, the
as-of date and the pasted text, so follow-ups and a second paste work from it. The graph is compiled per
turn because its nodes close over that turn's input and dependencies; the checkpointer is shared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import envelope
import followup
import hitl
import intake
import ledger as ledgers
import prompts
import render
import smalltalk
from inputs import MAX_SOURCE_CHARS, Incoming
from llm import TurnDeps
from promises import from_state, to_state
from scan import scan

FIXED = {"off_topic": smalltalk.OFF_TOPIC_REPLY, "failed": prompts.MODEL_FAILED, "no_messages": prompts.NONE_FOUND}


class ExtractorState(TypedDict, total=False):
    promises: list[dict]
    stats: dict
    as_of: str           # an explicit as-of the user gave ("" = today's date each turn)
    source: str          # every paste so far (capped), for follow-up questions
    route: str           # this turn only
    reply: str           # this turn only
    data: dict | None    # this turn only: the envelope
    pause: dict | None   # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict = field(default_factory=dict)


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake.Plan | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.plan: intake.Plan | None = None


class PromiseExtractorAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        answer = hitl.interpret(pending, incoming.text)
        plan = intake.plan_resume(answer, deps.today())
        plan = intake.Plan(**{**plan.__dict__, "notes": (*plan.notes, *intake.cut_note(pending, answer))})
        return await self._run(_Scope(deps, incoming, forced=plan), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")),
                          final.get("data") or {})


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(state: ExtractorState) -> dict:
        return _intake(state, scope)

    async def scan_node(state: ExtractorState) -> dict:
        return await _scan(state, scope)

    async def followup_node(state: ExtractorState) -> dict:
        return await _followup(state, scope)

    graph = StateGraph(ExtractorState)
    graph.add_node("intake", intake_node)
    graph.add_node("scan", scan_node)
    graph.add_node("followup", followup_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["scan", "followup", END])
    graph.add_edge("scan", END)
    graph.add_edge("followup", END)
    return graph


def _intake(state: ExtractorState, scope: _Scope) -> dict:
    stored = from_state(state.get("promises"))
    recipients = sorted({p.to_whom for p in stored})
    plan = scope.forced or intake.plan_turn(scope.incoming, bool(state.get("stats")), recipients, scope.deps.today())
    scope.plan = plan
    if plan.route == "pause":
        return {"route": "", "pause": plan.pending.to_metadata()}
    if plan.route == "reply":
        return {"route": "", "reply": _with_notes(plan.reply, plan.notes)}
    return {"route": "scan" if plan.route == "scan" else "followup"}


async def _scan(state: ExtractorState, scope: _Scope) -> dict:
    plan, deps = scope.plan, scope.deps
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    as_of = plan.as_of or _stored_as_of(state) or deps.today()
    explicit = plan.as_of is not None or bool(state.get("as_of"))
    result = await scan(deps, plan.source, plan.instruction, as_of, explicit, from_state(state.get("promises")),
                        scope.incoming.context)
    if result.status != "ok":
        return {"reply": _with_notes(FIXED[result.status], plan.notes)}
    stats = envelope.Stats.from_dict(state.get("stats")).plus(result.stats)
    source = f"{state.get('source') or ''}\n\n{plan.source}".strip()[-MAX_SOURCE_CHARS:]
    reply, data = _present(result.promises, stats, as_of, render.View(), scope.incoming.wants_json, plan.notes)
    return {"reply": reply, "data": data, "promises": to_state(result.promises), "stats": stats.to_dict(),
            "as_of": as_of.isoformat() if explicit else "", "source": source}


async def _followup(state: ExtractorState, scope: _Scope) -> dict:
    plan, deps = scope.plan, scope.deps
    promises = from_state(state.get("promises"))
    stats = envelope.Stats.from_dict(state.get("stats"))
    if plan.route == "rerender":
        command = plan.command
        as_of = command.as_of or _stored_as_of(state) or deps.today()
        wants_json = command.as_json or scope.incoming.wants_json
        reply, data = _present(promises, stats, as_of, command.view, wants_json, plan.notes)
        stored = {"as_of": command.as_of.isoformat()} if command.as_of else {}
        return {"reply": reply, "data": data, **stored}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    as_of = _stored_as_of(state) or deps.today()
    listing = render.render(ledgers.build(promises, as_of), stats, render.View(sources=True)) if stats.messages else ""
    answer = await followup.ask(deps, scope.incoming.text, listing, state.get("source") or "", scope.incoming.context)
    return {"reply": _with_notes(answer, plan.notes)}


def _present(promises, stats, as_of: date, view: render.View, wants_json: bool, notes) -> tuple[str, dict]:
    built = ledgers.build(promises, as_of)
    data = envelope.build(built, stats)
    if wants_json:
        return render.render_json(data), data
    return render.render(built, stats, view, notes), data


def _stored_as_of(state: ExtractorState) -> date | None:
    value = state.get("as_of") or ""
    return date.fromisoformat(value) if value else None


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
