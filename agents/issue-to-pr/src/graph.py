"""The issue-to-pr LangGraph.

    turn 1:  START → intake → discover → issue → candidates → pick_files → skeletons → pick_symbols → locate
             → assess ─[Q3]→ fetch → change (engine, diff, guards, tests) → propose → CARD 1 (pause) → END
    resume:  START → intake ─┬─ approve ─┬─ Approve → one guarded write → next card (pause) | report → END
                             │           ├─ Skip → leftovers → END
                             │           └─ custom text on card 1 → change → propose → CARD 1 again
                             ├─ rediscover → candidates … (closed issue: Continue) | fetch … (Q3 answer)
                             └─ (Q1/Q2 answers re-run the original request with the repository/branch)

Any node may end the turn by setting `reply` (or `pause`) and an empty `route`. A greeting never enters
the graph. State is checkpointed per A2A contextId: the issue, listing, localization, originals, the kept
change, the tests record, the plan with how many steps ran, so each resumed turn continues exactly there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import prompts
import smalltalk
import turn_approve as ta
import turn_change as tc
import turn_model as tm
import turn_propose as tp
import turn_read as rd
from inputs import Incoming, RepoRef, parse_request, repo_in
from llm import TurnDeps


class IssueToPrState(TypedDict, total=False):
    request_text: str
    issue: dict
    issue_number: int | None
    repo: dict | None
    ref: str | None
    hints: list[str]
    constraints: dict
    pasted_only: bool
    can_open_pr: bool
    listing: list[str]
    candidates: list[dict]
    search_terms: list[str]
    files: dict
    skeletons: dict
    picks: list
    symbols: list
    locations: list
    localization: dict | None
    originals: dict
    blob_shas: dict
    excluded: list
    session_id: str | None
    revisions: int
    changes: list
    discarded: list
    warnings: list
    tests: dict
    done_line: str
    timed_out: bool
    title: str
    body: str
    plan: dict | None
    leftovers: str
    envelope: dict | None
    notes: list[str]
    coverage: list[str]
    route: str               # this turn
    reply: str               # this turn
    data: dict | None        # this turn
    pause: dict | None       # this turn
    pause_text: str          # this turn


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


_NODES = (
    ("intake", rd.intake), ("discover", rd.discover_node), ("rediscover", rd.rediscover_node),
    ("issue", rd.issue_node), ("candidates", rd.candidates_node), ("pick_files", tm.pick_files_node),
    ("skeletons", tm.skeletons_node), ("pick_symbols", tm.pick_symbols_node), ("locate", tm.locate_node),
    ("assess", tm.assess_node), ("fetch", tc.fetch_node), ("change", tc.change_node),
    ("propose", tp.propose_node), ("approve", ta.approve_node), ("followup", tp.followup_node),
)


class IssueToPrAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:  # greeting, small talk or bare arithmetic: no tool call, no LLM call
            return TurnResult(canned)
        return await self._run(rd.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        if pending.kind in ("need_repo", "base_branch"):
            return await self._answer_repo(pending, incoming, context_id, deps)
        if pending.kind == "closed":
            answer = hitl.answer_text(incoming.text)
            if not (answer == hitl.CONTINUE or hitl.is_yes(answer)):
                return TurnResult(prompts.CANCELLED)
        scope = rd.Scope(deps, replace(incoming, wants_json=incoming.wants_json or pending.wants_json), pending)
        return await self._run(scope, context_id)

    async def _answer_repo(self, pending: hitl.Pending, incoming: Incoming, context_id: str,
                           deps: TurnDeps) -> TurnResult:
        """Q1 (repository) or Q2 (base branch): the original request again with the answer applied."""
        answer = hitl.answer_text(incoming.text)
        if hitl.is_stop(answer):
            return TurnResult(prompts.CANCELLED)
        original = Incoming(pending.request, incoming.policy, incoming.wants_json or pending.wants_json,
                            incoming.notes)
        if pending.kind == "need_repo":
            repo = repo_in(answer) or _loose_repo(answer)
            if repo is None:
                return TurnResult(prompts.CANCELLED)
            return await self._run(rd.Scope(deps, original, resume_repo=repo), context_id)
        branch = answer.split()[0].strip("`") if answer.split() else ""
        request = parse_request(pending.request)
        if request.repo is None or not branch:
            return TurnResult(prompts.CANCELLED)
        repo = RepoRef(request.repo.owner, request.repo.name, branch)
        return await self._run(rd.Scope(deps, original, resume_repo=repo), context_id)

    async def _run(self, scope: rd.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "pause_text": "", "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pending = hitl.Pending.from_metadata(final.get("pause"))
        if pending is not None:
            pending = replace(pending, text=final.get("pause_text") or "")
        return TurnResult(final.get("reply") or "", pending, final.get("data"))


def _loose_repo(answer: str) -> RepoRef | None:
    match = re.search(r"(?<![\w/.-])([A-Za-z0-9][\w-]*)/([A-Za-z0-9][\w.-]*)(?:@([\w./-]+))?", answer)
    return RepoRef(match.group(1), match.group(2).rstrip("."), match.group(3)) if match else None


def _build_graph(scope: rd.Scope) -> StateGraph:
    graph = StateGraph(IssueToPrState)
    for name, fn in _NODES:
        graph.add_node(name, _bind(fn, scope))
    graph.add_edge(START, "intake")
    names = [n for n, _ in _NODES]
    for name in names:
        graph.add_conditional_edges(name, _route, [n for n in names if n != "intake"] + [END])
    return graph


def _bind(fn, scope: rd.Scope):
    async def node(state: IssueToPrState) -> dict:
        return await fn(state, scope)
    node.__name__ = fn.__name__
    return node


def _route(state: IssueToPrState) -> str:
    return state.get("route") or END
