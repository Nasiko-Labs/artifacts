"""The sheet-watcher LangGraph.

    START -+- (a check in this context, no new source) -> followup -+- CHECK / RULES / RESET -> check
           |                                                         +- POST / CHANGE_CHANNEL -> post -> END
           |                                                         +- answered (watch later, off-topic) -> END
           +- (no check yet, a sheet named, a pasted table, a pick) --> check -+- events -> END
                                                                                +- post asked -> post -> END

check = pasted table or envelope, else discover -> locate/read the sheet (tool loop, post-checked) -> table
(code) -> rules (code; one model call only for phrases code could not match) -> snapshot load -> events,
priority, repeats (code) -> render -> the new snapshot is stored with the turn's state (only after render).
post = post tool + channel (list member channels when none was named) -> the approval card.
A greeting never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId: the rules, the sheet, the last snapshot, the last events' signatures
and the alert text. So a follow-up diffs against the last check, and a new conversation starts without a
baseline. The graph is compiled per turn because its nodes close over that turn's dependencies; the
checkpointer is shared. The approval card and the post walk run outside the graph (agent_executor, post.py).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import paste
import post
import prompts
import render
import smalltalk
from deps import TurnDeps
from parse import Request
from parse import parse as parse_request
from watch import KEY_KEY, SHEET_KEY, check, followup

__all__ = ["KEY_KEY", "SHEET_KEY", "TurnDeps", "TurnResult", "WatcherAgent"]

log = logging.getLogger("sheet_watcher.graph")

TURN_DEADLINE_SECS = 150
OUT_OF_TIME = "I ran out of time before finishing this check. Try again, or paste the table."
_STORED = ("post", "alert")


class WatchState(TypedDict, total=False):
    query: str
    request: dict
    pick: dict | None          # this turn: the sheet the user picked {"id", "title"}
    key_pick: str | None       # this turn: the key column the user picked
    rules: dict                # stored: models.RuleSpec in the user's words
    source: dict               # stored: {"id", "title", "tab", "kind"}
    snapshot: dict             # stored: snapshot.build(...) of the last fully rendered check
    reported: list[str]        # stored: the last check's event signatures (for repeats)
    alert: str                 # stored: the alert text for the last check
    last_envelope: dict        # stored: the last check's envelope (a later post turn reports on it)
    post: dict                 # stored: {"channel", "state", "link"}
    rules_text: str            # this turn: a rule change routed by the follow-up
    reset: bool                # this turn: start a new baseline
    reply: str                 # this turn
    route: str
    card: dict | None          # this turn: a post.Draft for the approval card
    question: dict | None      # this turn: {"text", "hitl", "metadata"}
    envelope: dict | None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    card: post.Draft | None = None
    question: dict | None = None
    envelope: dict | None = None


class WatcherAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, metadata: Mapping[str, Any] | None = None,
                   pick: Mapping[str, str] | None = None, key_pick: str | None = None) -> TurnResult:
        canned = smalltalk.reply_to(query) if pick is None and key_pick is None else None
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "request": parse_request(query, metadata).to_dict(), "pick": dict(pick or {}) or None,
                 "key_pick": key_pick, "rules_text": "", "reset": False, "reply": "", "route": "", "card": None,
                 "question": None, "envelope": None}
        try:
            final = await asyncio.wait_for(graph.ainvoke(fresh, _config(context_id), durability="exit"),
                                           TURN_DEADLINE_SECS)
        except TimeoutError:
            log.warning("turn passed its %ss deadline", TURN_DEADLINE_SECS)
            return TurnResult(OUT_OF_TIME)
        card = final.get("card")
        return TurnResult(final.get("reply") or "", post.Draft.from_dict(card) if card else None,
                          final.get("question"), final.get("envelope"))

    async def remember(self, context_id: str, update: Mapping[str, Any]) -> None:
        """Record a post decision (and an edited alert) on the conversation's state."""
        values = await self.values(context_id)
        changes = {k: v for k, v in dict(update).items() if v is not None and k in _STORED}
        if not values.get("snapshot") or not changes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        await graph.aupdate_state(_config(context_id), changes, as_node="check")

    async def values(self, context_id: str) -> dict:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    async def followup_node(state: WatchState) -> dict:
        return await followup(state, deps)

    async def check_node(state: WatchState) -> dict:
        return await check(state, deps)

    async def post_node(state: WatchState) -> dict:
        return await _post(state, deps)

    graph = StateGraph(WatchState)
    graph.add_node("followup", followup_node)
    graph.add_node("check", check_node)
    graph.add_node("post", post_node)
    graph.add_conditional_edges(START, _entry, ["followup", "check"])
    graph.add_conditional_edges("followup", _route, ["check", "post", END])
    graph.add_conditional_edges("check", _route, ["post", END])
    graph.add_edge("post", END)
    return graph


def _entry(state: WatchState) -> str:
    request = Request.from_dict(state.get("request") or {})
    source = state.get("source") or {}
    if state.get("pick") or state.get("key_pick") or not state.get("snapshot"):
        return "check"
    if paste.read(state["query"]) is not None:
        return "check"
    if request.sheet and request.sheet.casefold() not in str(source.get("title") or "").casefold():
        return "check"
    return "followup"


def _route(state: WatchState) -> str:
    return state.get("route") or END


# --- post: the card, the channel pick, or copyable text ------------------------------------------------------

async def _post(state: WatchState, deps: TurnDeps) -> dict:
    request = Request.from_dict(state.get("request") or {})
    lead = state.get("reply") or ""
    if not state.get("snapshot"):
        return {"reply": prompts.NO_CHECK_YET, "route": ""}
    text = str(state.get("alert") or "")
    if not text:
        return {"reply": "\n\n".join(p for p in (lead, prompts.NO_EVENTS_TO_POST) if p), "route": ""}
    channel = request.channel or _stored_channel(state, request)
    plan = await post.prepare(deps, text, channel, request.dry_run)
    if plan.copy_reason:
        posted = {"channel": f"#{channel}" if channel else None, "state": "copyable", "link": None}
        body = f"{plan.copy_reason}\n\n{render.copyable(text)}"
        return {"reply": "\n\n".join(p for p in (lead, body) if p), "post": posted,
                "envelope": _with_post(state, posted), "route": ""}
    if plan.question is not None:
        question_text, hitl, metadata = plan.question
        return {"question": {"text": question_text, "hitl": hitl, "metadata": metadata}, "route": ""}
    awaiting = {"channel": plan.draft.label, "state": "awaiting_approval", "link": None}
    return {"card": plan.draft.to_dict(), "envelope": _with_post(state, awaiting), "route": ""}


def _stored_channel(state: WatchState, request: Request) -> str | None:
    """A plain "post it" reuses the channel of the last post decision; "change channel" asks again."""
    if request.change_channel:
        return None
    stored = (state.get("post") or {}).get("channel")
    return str(stored).lstrip("#") if stored else None


def _with_post(state: WatchState, post_state: Mapping[str, Any]) -> dict | None:
    envelope = state.get("envelope") or state.get("last_envelope")
    return {**envelope, "post": dict(post_state)} if envelope else None
