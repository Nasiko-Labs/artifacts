"""The issue-localizer LangGraph (Agentless's file → element → line hierarchy, no agent loop).

    START → intake ─┬─ discover → issue → candidates → pick_files → skeletons → pick_symbols → locate → triage → render → END
                    ├─ followup ─┬─ answered from stored state ──────────────────────────────────────────────→ END
                    │            └─ rerun ("look in X", "re-run with this trace") → discover …
                    └─ fixed reply / decline / pause (Q1 "Which repository?") ───────────────────────────────→ END

Any node may end the turn early by setting `reply` and an empty `route`. A greeting never enters the graph.
State is checkpointed per A2A contextId (thread_id): the issue, repository, listing, candidates, fetched files,
skeletons and the last envelope, so follow-ups work without re-reading. The graph is compiled per turn because
its nodes close over that turn's Scope; the checkpointer is shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import smalltalk
import turn_model as tm
import turn_read as rd
import turn_render as tr
from inputs import Incoming, RepoRef
from llm import TurnDeps


class LocalizerState(TypedDict, total=False):
    issue: dict
    issue_number: int | None
    repo: dict | None
    ref: str | None
    hints: list[str]
    pasted_only: bool
    listing: list[str]
    candidates: list[dict]
    search_terms: list[str]
    files: dict
    skeletons: dict
    envelope: dict
    coverage_lines: list[str]
    picks: list              # this turn
    symbols: list            # this turn
    locations: list          # this turn
    issue_kind: str          # this turn
    notes: list[str]         # this turn
    route: str               # this turn
    reply: str               # this turn
    data: dict | None        # this turn: the envelope for the data part
    pause: dict | None       # this turn: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


_NODES = (
    ("intake", rd.intake), ("discover", rd.discover_node), ("issue", rd.issue_node),
    ("candidates", rd.candidates_node), ("pick_files", tm.pick_files_node), ("skeletons", tm.skeletons_node),
    ("pick_symbols", tm.pick_symbols_node), ("locate", tm.locate_node), ("triage", tm.triage_node),
    ("render", tr.render_node), ("followup", tr.followup_node),
)


class LocalizerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:  # greeting, small talk or bare arithmetic: no tool call, no LLM call
            return TurnResult(canned)
        return await self._run(rd.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to Q1 "Which repository?" on the paused task: the original request with that repository."""
        repo: RepoRef | None = hitl.interpret(incoming.text)
        if repo is None:
            return TurnResult(rd.prompts.CANCELLED)
        original = Incoming(pending.request, incoming.policy, incoming.wants_json or pending.wants_json,
                            incoming.notes)
        return await self._run(rd.Scope(deps, original, resume_repo=repo), context_id)

    async def _run(self, scope: rd.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None, "notes": [], "picks": [], "symbols": [],
                 "locations": [], "issue_kind": ""}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: rd.Scope) -> StateGraph:
    graph = StateGraph(LocalizerState)
    for name, fn in _NODES:
        graph.add_node(name, _bind(fn, scope))
    graph.add_edge(START, "intake")
    names = [n for n, _ in _NODES]
    for name in names:
        targets = [n for n in names if n != "intake"] + [END]
        graph.add_conditional_edges(name, _route, targets)
    return graph


def _bind(fn, scope: rd.Scope):
    async def node(state: LocalizerState) -> dict:
        return await fn(state, scope)
    node.__name__ = fn.__name__
    return node


def _route(state: LocalizerState) -> str:
    return state.get("route") or END
