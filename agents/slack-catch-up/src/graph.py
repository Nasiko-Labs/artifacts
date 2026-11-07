"""The slack-catch-up LangGraph.

    START → turn → END

One node; LangGraph is here for the checkpointer. State per A2A contextId (thread_id): the last
envelope shown (`report`, what follow-ups are answered from) and the `since` marker (end of the last
fully rendered catch-up, where the next one starts). No raw tool output is stored.

Before the graph, in code: a greeting gets the canned intro with zero tool and LLM calls. Inside the
turn: no traceparent → fixed reply before any call; a write ask (reply, post, react, mark read) →
fixed read-only reply; a follow-up decided in code where it can be; anything that is not clearly a
catch-up goes through the scope check (OFF_TOPIC → fixed decline).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import catchup
import followup
import parse
import prompts
import render
import smalltalk
from deps import TurnDeps
from models import Outcome
from steps import ask_once

log = logging.getLogger("catchup.graph")


class CatchupState(TypedDict, total=False):
    query: str
    as_json: bool
    reply: str
    data: dict | None  # this turn's envelope, for the data part
    report: dict  # the last catch-up envelope in this context
    since: str  # ISO; where the next catch-up starts


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None


class CatchupAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, as_json: bool = False) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": context_id}}
        final = await graph.ainvoke({"query": query, "as_json": as_json, "reply": "", "data": None}, config,
                                    durability="exit")
        return TurnResult(final.get("reply") or prompts.UNREADABLE_ANSWER, final.get("data"))


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def turn(state: CatchupState) -> dict:
        return await _turn(state, deps)

    graph = StateGraph(CatchupState)
    graph.add_node("turn", turn)
    graph.add_edge(START, "turn")
    graph.add_edge("turn", END)
    return graph


async def _turn(state: CatchupState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "data": None}
    request = parse.parse(state["query"], deps.now, bool(state.get("as_json")))
    if request.wants_write:
        return {"reply": prompts.READ_ONLY_REPLY, "data": None}
    prior = state.get("report")
    stored_since = _iso(state.get("since"))
    if prior and not parse.pasted_lines(request.text):
        route = followup.in_code(request, prior) or await followup.with_model(deps, request, prior)
        return await _follow(route, request, prior, stored_since, deps)
    unclear = not request.looks_like_catch_up and not parse.pasted_lines(request.text)
    if unclear and await _off_topic(deps, request.text):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "data": None}
    return _stored(await catchup.run(deps, request, stored_since), request)


async def _follow(route: followup.Route, request: parse.Request, prior: dict, stored_since: datetime | None,
                  deps: TurnDeps) -> dict:
    if route.kind == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "data": None}
    if route.kind == "reply":
        return {"reply": route.text, "data": None}
    if route.kind == "render":
        text = render.as_json(prior) if request.as_json else render.report(prior, only=route.only,
                                                                          channel=route.channel)
        return {"reply": text, "data": prior}
    run_request = route.request or request
    # A wider or explicit window starts from that window, not from the stored marker.
    since = None if run_request.explicit_window else stored_since
    return _stored(await catchup.run(deps, run_request, since), run_request)


def _stored(outcome: Outcome, request: parse.Request) -> dict:
    update: dict = {"reply": outcome.reply, "data": outcome.envelope}
    if outcome.envelope is not None:
        update["report"] = outcome.envelope
        if request.as_json:
            update["reply"] = render.as_json(outcome.envelope)
    if outcome.since_marker:
        update["since"] = outcome.since_marker
    return update


async def _off_topic(deps: TurnDeps, text: str) -> bool:
    answer = await ask_once(deps, ({"role": "system", "content": prompts.SCOPE_CHECK_SYSTEM},
                                   {"role": "user", "content": text}))
    log.info("scope check: %s", answer[:20])
    return answer.upper().startswith(prompts.OFF_TOPIC)


def _iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
