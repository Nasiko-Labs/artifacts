"""The board-deck-fact-checker LangGraph.

    START -+- (answer to the sheet pick) -------------------------------> resume --> END
           +- (a report exists, nothing new given) -> followup -+- answered / re-compared -> END
           |                                                    +- NEEDS_PULL -> recheck --> END
           +- (otherwise) ---------------------------------------------> check ----> END

check = deck -> extract_claims -> sheets -> index -> match -> compare -> render (pipeline.py). Before the
graph, in code: a greeting gets the canned intro and a request to change the deck or a sheet gets the
read-only decline, both with zero tool and LLM calls.

State is checkpointed per A2A contextId; only what follow-ups need is kept (claims, the cells that were
matched, the matches, notes, the spreadsheets read), never raw tool output. The graph is compiled per turn
because its nodes close over that turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import pipeline
import prompts
import render
import request as request_
import smalltalk
from deps import TurnDeps
from steps import ask_once

__all__ = ["DeckAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("board_deck.graph")

TURN_DEADLINE_SECS = 420
RECOMPARE_MAX_CHARS = 200
OUT_OF_TIME = "I ran out of time before finishing this check. Try fewer slides (\"only check slides 4 to 9\")."


class DeckState(TypedDict, total=False):
    query: str
    answer: str | None
    reply: str
    route: str
    envelope: dict | None
    question: dict | None
    stored: dict | None
    pending: dict | None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None
    question: dict | None = None


@dataclass(frozen=True)
class _Scope:
    deps: TurnDeps
    req: request_.Request


class DeckAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps,
                   metadata: Mapping[str, Any] | None = None) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:
            return TurnResult(canned)
        req = request_.parse(query, metadata)
        if req.wants_write:
            log.info("declined a request to change the deck or a sheet")
            return TurnResult(prompts.READ_ONLY_DECLINE)
        return await self._run(_Scope(deps, req), context_id, {"query": query, "answer": None, "pending": None})

    async def resume(self, answer: str, context_id: str, deps: TurnDeps) -> TurnResult:
        """The answer to "Which sheet is the source?" on the paused task."""
        return await self._run(_Scope(deps, request_.parse("")), context_id, {"query": answer, "answer": answer})

    async def _run(self, scope: _Scope, context_id: str, fresh: dict) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": context_id}}
        start = {**fresh, "reply": "", "route": "", "envelope": None, "question": None}
        try:
            final = await asyncio.wait_for(graph.ainvoke(start, config, durability="exit"), TURN_DEADLINE_SECS)
        except TimeoutError:
            log.warning("turn passed its %ss deadline", TURN_DEADLINE_SECS)
            return TurnResult(OUT_OF_TIME)
        return TurnResult(final.get("reply") or prompts.UNREADABLE_ANSWER, final.get("envelope"),
                          final.get("question"))


def _build_graph(scope: _Scope) -> StateGraph:
    async def check(state: DeckState) -> dict:
        return _apply(await pipeline.check(scope.deps, scope.req))

    async def followup(state: DeckState) -> dict:
        return await _followup(state, scope)

    async def recheck(state: DeckState) -> dict:
        return _apply(await pipeline.recheck(scope.deps, state.get("stored") or {}))

    async def resume(state: DeckState) -> dict:
        pending = state.get("pending")
        if not pending:
            return {"reply": prompts.NO_PRIOR, "route": ""}
        return {**_apply(await pipeline.resume(scope.deps, pending, state.get("answer") or "")), "pending": None}

    graph = StateGraph(DeckState)
    for name, node in (("check", check), ("followup", followup), ("recheck", recheck), ("resume", resume)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, lambda s: _entry(s, scope.req), ["resume", "followup", "check"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["recheck", END])
    for name in ("check", "recheck", "resume"):
        graph.add_edge(name, END)
    return graph


def _entry(state: DeckState, req: request_.Request) -> str:
    if state.get("answer") is not None:
        return "resume"
    fresh_input = req.deck_text or req.source_rows or req.urls or req.claims
    return "followup" if state.get("stored") and not fresh_input else "check"


def _apply(outcome: pipeline.Outcome) -> dict:
    update: dict = {"reply": outcome.reply, "envelope": outcome.envelope, "question": outcome.question,
                    "route": ""}
    if outcome.stored is not None:
        update["stored"] = outcome.stored
    if outcome.pending is not None:
        update["pending"] = outcome.pending
    return update


async def _followup(state: DeckState, scope: _Scope) -> dict:
    stored, req = state.get("stored") or {}, scope.req
    if req.mode != "default" and len(req.message) <= RECOMPARE_MAX_CHARS:
        log.info("re-comparing the stored claims in %s mode (no new pull)", req.mode)
        return _apply(pipeline.rendered(stored, req.mode, req.json_mode))
    if req.json_mode and len(req.message) <= RECOMPARE_MAX_CHARS:
        return _apply(pipeline.rendered(stored, str(stored.get("mode") or "default"), True))
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    report = pipeline.report_of(stored, str(stored.get("mode") or "default"))
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(render.markdown(report))}"
    text = await ask_once(scope.deps, ({"role": "system", "content": system},
                                       {"role": "user", "content": state["query"]}))
    if text.startswith(prompts.NEEDS_PULL):
        return {"route": "recheck"}
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}
