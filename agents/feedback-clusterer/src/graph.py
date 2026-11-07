"""The feedback-clusterer LangGraph.

    START -> intake ─┬─ discover -> merge -> assign -> finish (metrics, quotes, churn, render) -> END
                     ├─ followup (re-render in code, or one scoped question) ----------------> END
                     └─ fixed reply / pause ------------------------------------------------> END

State is checkpointed per A2A contextId (thread_id): the parsed items, the themes, the item -> theme map,
quote ids and churn flags, so follow-ups recompute every number in code. The graph is compiled per turn
because its nodes close over that turn's input and dependencies; the checkpointer is shared. A pause
leaves the graph as a `pause` value; its state lives on the A2A task."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import batching
import churn
import filters
import followup
import hitl
import intake
import metrics
import prompts
import quotes
import smalltalk
import themes as theme_steps
from inputs import Incoming
from llm import TurnDeps
from report import Report, Stored, View, assemble


class ClusterState(TypedDict, total=False):
    stored: dict          # Stored.to_state()
    route: str            # this turn only
    reply: str            # this turn only
    data: dict | None     # this turn only: the envelope for the data part
    pause: dict | None    # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class _Scope:
    """One turn's input, dependencies and intermediate results (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, pending: hitl.Pending | None = None):
        self.deps, self.incoming, self.pending = deps, incoming, pending
        self.plan: intake.Plan | None = None
        self.assigned: theme_steps.Assignment | None = None
        self.filtered: tuple = ()
        self.themes: list[dict] = []
        self.sampled = False

    def today(self):
        return filters.today_from(self.incoming.text, self.deps.today())


class ClustererAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to one of this agent's questions, on the paused task."""
        return await self._run(_Scope(deps, incoming, pending=pending), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")), final.get("data"))


def _build_graph(scope: _Scope) -> StateGraph:
    steps = {"intake": _intake, "discover": _discover, "merge": _merge, "assign": _assign, "finish": _finish,
             "followup": _followup}
    graph = StateGraph(ClusterState)
    for name, step in steps.items():
        graph.add_node(name, _bind(step, scope))
    graph.add_edge(START, "intake")
    for name, targets in (("intake", ["discover", "followup"]), ("discover", ["merge"]), ("merge", ["assign"])):
        graph.add_conditional_edges(name, lambda s: s.get("route") or END, [*targets, END])
    graph.add_edge("assign", "finish")
    graph.add_edge("finish", END)
    graph.add_edge("followup", END)
    return graph


def _bind(step, scope: _Scope):
    async def node(state: ClusterState) -> dict:
        return await step(state, scope)
    return node


def _reply(text: str, scope: _Scope) -> dict:
    notes = scope.plan.notes if scope.plan else ()
    return {"route": "", "reply": "\n\n".join(p for p in (text, *notes) if p)}


async def _intake(state: ClusterState, scope: _Scope) -> dict:
    stored = Stored.from_state(state.get("stored"))
    if scope.pending is not None:
        plan = intake.plan_resume(hitl.interpret(scope.pending, scope.incoming.text), scope.pending, stored)
    else:
        plan = intake.plan_turn(scope.incoming, stored)
    scope.plan = plan
    if plan.route == "pause":
        return {"route": "", "pause": plan.pending.to_metadata()}
    if plan.route == "reply":
        return _reply(plan.reply, scope)
    if plan.route in ("rerender", "ask"):
        return {"route": "followup"}
    if not scope.deps.traceparent:
        return _reply(prompts.NO_TRACE_CONTEXT, scope)
    scope.filtered = filters.apply(plan.items, plan.focus, scope.today())
    if len(scope.filtered) < 3:
        return _reply(prompts.NOTHING_AFTER_FILTER if plan.focus.describe() else prompts.TOO_FEW, scope)
    return {"route": "discover"}


async def _discover(state: ClusterState, scope: _Scope) -> dict:
    pool = batching.discovery_pool(scope.filtered)
    scope.sampled = len(scope.filtered) > len(pool)
    found = await theme_steps.discover(scope.deps, pool, scope.plan.focus.hint, scope.incoming.context)
    if found.off_topic:
        return _reply(smalltalk.OFF_TOPIC_REPLY, scope)
    if not found.candidates:
        return _reply(prompts.MODEL_FAILED if found.failed else prompts.NO_THEME, scope)
    scope.themes = list(found.candidates)
    return {"route": "merge"}


async def _merge(state: ClusterState, scope: _Scope) -> dict:
    keys = {i.customer_key for i in scope.plan.items if i.customer_key}
    scope.themes = await theme_steps.merge(scope.deps, scope.themes, keys, scope.incoming.context)
    return {"route": "assign"} if scope.themes else _reply(prompts.NO_THEME, scope)


async def _assign(state: ClusterState, scope: _Scope) -> dict:
    scope.assigned = await theme_steps.assign(scope.deps, scope.plan.items, scope.themes, scope.incoming.context)
    return {}


async def _finish(state: ClusterState, scope: _Scope) -> dict:
    plan, assigned, deps = scope.plan, scope.assigned, scope.deps
    stats, _ = metrics.compute(scope.filtered, scope.themes, assigned.theme_of)
    by_id = {i.id: i for i in plan.items}
    quote_ids = await quotes.pick(deps, stats, by_id, scope.incoming.context)
    checked = await churn.confirm(deps, plan.items, scope.incoming.context)
    run = dict(plan.run, unreached=list(assigned.unreached), cut=assigned.cut, churn_unchecked=checked.unchecked,
               discovery_sampled=scope.sampled)
    stored = Stored(plan.items, tuple(scope.themes), assigned.theme_of, quote_ids, checked.flagged, run, plan.focus)
    report = assemble(stored, View(as_json=scope.incoming.wants_json), scope.today())
    return {**_done(report, scope), "stored": stored.to_state()}


async def _followup(state: ClusterState, scope: _Scope) -> dict:
    stored = Stored.from_state(state.get("stored"))
    command = scope.plan.command
    if command is not None:
        if command.focus is not None:
            stored = stored.with_focus(command.focus)
        view = replace(command.view, as_json=command.view.as_json or scope.incoming.wants_json)
        return {**_done(assemble(stored, view, scope.today()), scope), "stored": stored.to_state()}
    shown = assemble(stored, View(), scope.today()).envelope["themes"] if stored else []
    answer = followup.code_answer(scope.incoming.text, shown)
    if answer is None:
        if not scope.deps.traceparent:
            return _reply(prompts.NO_TRACE_CONTEXT, scope)
        answer = await followup.ask(scope.deps, scope.incoming.text, shown, scope.incoming.context)
    return _reply(answer, scope)


def _done(report: Report, scope: _Scope) -> dict:
    return {**_reply(report.text, scope), "data": report.envelope}
