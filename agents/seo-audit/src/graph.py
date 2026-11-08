"""The seo-audit LangGraph.

    START -> intake -+- fetch -> audit -> END     (a URL: guarded fetch, then the audit)
                     +- audit -> END              (pasted HTML)
                     +- followup -> END           (from the stored audit)
                     +- classify -> END           (one scope call: decline / pause / help)
                     +- END                        (greeting, pause, envelope kept)

State is checkpointed per A2A contextId: the last audit's envelope plus the page text it was built from, so
follow-ups cost no new fetch. The graph is compiled per turn (nodes close over that turn's input); the
checkpointer is shared. A pause leaves the graph as a `pause` value; its state lives on the A2A task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import steps
import turns
from inputs import Incoming
from llm import TurnDeps


class AuditState(TypedDict, total=False):
    last: dict | None      # the last audit's envelope
    text: str              # its visible page text (grounding for follow-ups)
    html: str              # its HTML, capped (grounding for follow-ups)
    route: str             # this turn only
    reply: str             # this turn only
    envelope: dict | None  # this turn only: the DataPart
    pause: dict | None     # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None
    pause: hitl.Pending | None = None


class SeoAuditAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(steps.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(steps.Scope(deps, incoming, resume=pending), context_id)

    async def _run(self, scope: steps.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "envelope": None, "pause": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", final.get("envelope"), hitl.Pending.from_metadata(final.get("pause")))


def _build_graph(scope: steps.Scope) -> StateGraph:
    async def intake(state: AuditState) -> dict:
        has_audit = bool(state.get("last"))
        if scope.resume is not None:
            scope.plan = turns.plan_resume(scope.resume, scope.incoming.text, has_audit)
        else:
            scope.plan = turns.plan_turn(scope.incoming, has_audit)
        plan = scope.plan
        if plan.route == "reply":
            return {"route": "", "reply": plan.reply}
        if plan.route == "pause":
            return {"route": "", "pause": plan.pending.to_metadata()}
        if plan.route == "envelope":
            return {"route": "", "last": plan.request.envelope, "text": "", "html": "",
                    "reply": f"Got the earlier audit ({plan.request.envelope.get('summary', '')}). Paste the new "
                             "version's HTML or URL with \"audit again\" and I'll compare."}
        return {"route": plan.route}

    async def fetch(state: AuditState) -> dict:
        return await steps.fetch(scope)

    async def audit(state: AuditState) -> dict:
        return await steps.audit(scope, state)

    async def followup(state: AuditState) -> dict:
        return await steps.followup(scope, state)

    async def classify(state: AuditState) -> dict:
        return await steps.classify(scope, state)

    graph = StateGraph(AuditState)
    for name, node in (("intake", intake), ("fetch", fetch), ("audit", audit), ("followup", followup),
                       ("classify", classify)):
        graph.add_node(name, node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END,
                                ["fetch", "audit", "followup", "classify", END])
    graph.add_conditional_edges("fetch", lambda s: "audit" if s.get("route") == "audit" else END, ["audit", END])
    for name in ("audit", "followup", "classify"):
        graph.add_edge(name, END)
    return graph

