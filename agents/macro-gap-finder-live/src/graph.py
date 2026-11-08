"""The macro-gap-finder LangGraph.

    START -> intake ─┬─ analyse (scrub, cluster, [merge], draft, check, render) -> END
                     ├─ fetch (read the desk: tickets + macros) -> analyse as above -> END
                     ├─ followup (re-filter / show / boilerplate in code; redraft = 1 call) -> END
                     ├─ scope (one call: off-topic -> decline) -> END
                     └─ fixed reply / pause -> END

State is checkpointed per A2A contextId (thread_id): the stored result (scrubbed member replies, groups with
their counts, drafts), so follow-ups need no re-paste. The graph is compiled per turn because its nodes close
over that turn's input and dependencies; the checkpointer is shared. A pause lives on the A2A task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake
import live
import pauses
import pipeline
import prompts
import turns
from inputs import Incoming
from llm import TurnDeps
from report import Stored


class GapState(TypedDict, total=False):
    stored: dict          # Stored.to_state()
    route: str            # this turn only
    reply: str            # this turn only
    data: dict | None     # this turn only: the envelope for the data part
    pause: dict | None    # this turn only: pauses.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: pauses.Pending | None = None
    data: dict | None = None


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, pending: pauses.Pending | None = None):
        self.deps, self.incoming, self.pending = deps, incoming, pending
        self.plan: intake.Plan | None = None


class MacroGapAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: pauses.Pending, incoming: Incoming, context_id: str,
                     deps: TurnDeps) -> TurnResult:
        """An answer to one of this agent's questions, on the paused task."""
        return await self._run(_Scope(deps, incoming, pending=pending), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", pauses.Pending.from_metadata(final.get("pause")), final.get("data"))


def _build_graph(scope: _Scope) -> StateGraph:
    steps = {"intake": _intake, "analyse": _analyse, "fetch": _fetch, "followup": _followup, "scope": _scope_check}
    graph = StateGraph(GapState)
    for name, step in steps.items():
        graph.add_node(name, _bind(step, scope))
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END,
                                ["analyse", "fetch", "followup", "scope", END])
    for name in ("analyse", "fetch", "followup", "scope"):
        graph.add_edge(name, END)
    return graph


def _bind(step, scope: _Scope):
    async def node(state: GapState) -> dict:
        return await step(state, scope)
    return node


def _reply(text: str, scope: _Scope) -> dict:
    notes = scope.plan.notes if scope.plan else ()
    return {"route": "", "reply": "\n\n".join(p for p in (text, *notes) if p)}


async def _intake(state: GapState, scope: _Scope) -> dict:
    if scope.pending is not None:
        plan = intake.plan_resume(pauses.interpret(scope.pending, scope.incoming.text), scope.pending)
    else:
        plan = intake.plan_turn(scope.incoming, bool(state.get("stored")))
    scope.plan = plan
    if plan.route == "pause":
        return {"route": "", "pause": plan.pending.to_metadata()}
    if plan.route == "reply":
        return _reply(plan.reply, scope)
    if plan.route in ("analyse", "fetch") and not scope.deps.traceparent:
        return _reply(prompts.NO_TRACE_CONTEXT, scope)
    return {"route": plan.route}


async def _analyse(state: GapState, scope: _Scope) -> dict:
    plan = scope.plan
    stored = await pipeline.analyse(scope.deps, plan.replies, plan.macros, plan.min_freq, plan.top,
                                    scope.incoming.context, plan.notes)
    if stored is None:
        return _reply(prompts.NOTHING_TO_COMPARE, scope)
    text, data = turns.rendered(stored, scope.incoming.wants_json)
    return {"route": "", "reply": text, "data": data, "stored": stored.to_state()}


async def _fetch(state: GapState, scope: _Scope) -> dict:
    ask = scope.plan.live
    fetched = await live.fetch(scope.deps, ask)
    if fetched.reply is not None:
        return _reply(fetched.reply, scope)
    clamped = (f"The window was set to {ask.days} days (the range is 7 to 90).",) if ask.window_clamped else ()
    notes = (*clamped, *fetched.notes)
    about = f' about "{ask.topic}"' if ask.topic else ""
    headline = (f"Read {len(fetched.replies):,} agent replies on {fetched.tickets} solved tickets{about} "
                f"(last {ask.days} days).")
    if len(fetched.replies) < intake.MIN_REPLIES:
        return _reply("\n\n".join((prompts.TOO_FEW_LIVE.format(headline=headline), *notes)), scope)
    stored = await pipeline.analyse(scope.deps, fetched.replies, fetched.macros, ask.min_freq, ask.top,
                                    scope.incoming.context, notes,
                                    {"urls": fetched.urls, "cap_level": fetched.cap_level, "headline": headline})
    if stored is None:
        return _reply(prompts.NOTHING_TO_COMPARE, scope)
    text, data = turns.rendered(stored, scope.incoming.wants_json)
    return {"route": "", "reply": text, "data": data, "stored": stored.to_state()}


async def _followup(state: GapState, scope: _Scope) -> dict:
    stored = Stored.from_state(state.get("stored"))
    outcome = await turns.followup(scope.deps, stored, scope.plan.command, scope.incoming)
    result = _reply(outcome.text, scope)
    if outcome.data is not None:
        result["data"] = outcome.data
    if outcome.stored is not None:
        result["stored"] = outcome.stored.to_state()
    return result


async def _scope_check(state: GapState, scope: _Scope) -> dict:
    return _reply(await turns.scope_reply(scope.deps, scope.incoming.text, bool(state.get("stored"))), scope)
