"""The launch-readiness LangGraph.

    START ─┬─ (a result in this context, the message names no launch) → followup ─┬─ answered → END
           │                                                                     └─ NEEDS_PULL ─┐
           └─ (otherwise, or the answer to "Which launch?") ────────────────────────────────────→ pull → END
    pull: gatekeep (write asks, off-topic, no launch name → the one question) → discover → four area steps
          concurrently → rules, verdict, render (code)

A greeting or small talk never enters the graph (smalltalk.py). State is checkpointed per A2A contextId, so a
follow-up sees the last result; the graph is compiled per turn because its nodes close over that turn's
dependencies (LLM client, gateway, inbound traceparent); the checkpointer is shared.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import prompts
import question
import render
import replies
import smalltalk
from deps import TurnDeps
from pull import pull
from reader import ask_once
from scope import DOMAIN, WRITE_ASK, Request, parse, wants_json, with_answer

__all__ = ["LaunchAgent", "TurnResult"]


class LaunchState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    resume: dict | None        # this turn: the answered pause {kind, request, answer}
    json_mode: bool            # this turn: message or metadata asked for json
    pause: dict | None         # this turn: the question to pause on
    envelope: dict | None      # this turn: the envelope
    scoped: bool               # this turn: the follow-up call already judged the message in scope
    result: dict | None        # kept: the last envelope
    result_text: str           # kept: the last rendered result
    last_request: dict         # kept: the request behind the last result ("check again")


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: question.Pause | None = None
    envelope: dict | None = None


class LaunchAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, json_mode: bool = False) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        return await self._run(query, None, context_id, deps, json_mode)

    async def resume(self, pause: Mapping[str, Any], answer: str, context_id: str, deps: TurnDeps) -> TurnResult:
        if question.is_cancel(answer):
            return TurnResult(replies.CANCELLED)
        return await self._run(answer, {**dict(pause), "answer": answer}, context_id, deps, False)

    async def _run(self, query: str, resume: dict | None, context_id: str, deps: TurnDeps,
                   json_mode: bool) -> TurnResult:
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "resume": resume, "json_mode": json_mode,
                 "pause": None, "envelope": None, "scoped": False}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pause = final.get("pause")
        return TurnResult(final.get("reply") or "", question.Pause(**pause) if pause else None, final.get("envelope"))


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup(state: LaunchState) -> dict:
        return await _followup(state, deps)

    async def pull_node(state: LaunchState) -> dict:
        return await _pull(state, deps)

    graph = StateGraph(LaunchState)
    graph.add_node("followup", followup)
    graph.add_node("pull", pull_node)
    graph.add_conditional_edges(START, lambda s: _entry(s, deps), ["followup", "pull"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["pull", END])
    graph.add_edge("pull", END)
    return graph


def _entry(state: LaunchState, deps: TurnDeps) -> str:
    if state.get("resume"):
        return "pull"
    named = parse(state["query"], deps.today).launch
    return "followup" if state.get("result") and not named else "pull"


def _reply(text: str) -> dict:
    return {"reply": text, "route": ""}


async def _followup(state: LaunchState, deps: TurnDeps) -> dict:
    query, result = state["query"], state.get("result") or {}
    if wants_json(query) and len(query.split()) <= 4:  # "as json": the stored envelope, no call
        return {**_reply(render.as_json(result)), "envelope": result}
    if WRITE_ASK.search(query):
        return _reply(replies.READ_ONLY_REPLY)
    if not deps.traceparent:
        return _reply(replies.NO_TRACE_CONTEXT)
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(state.get('result_text') or '')}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    if text.startswith(prompts.OFF_TOPIC):
        return _reply(smalltalk.OFF_TOPIC_REPLY)
    if text.startswith(prompts.NEEDS_PULL):
        return {"route": "pull", "scoped": True}
    return _reply(text or replies.UNREADABLE_ANSWER)


def _request(state: LaunchState, deps: TurnDeps) -> Request:
    resume = state.get("resume") or {}
    if resume:
        return with_answer(Request.from_state(resume.get("request")), str(resume.get("answer") or ""), deps.today)
    parsed = parse(state["query"], deps.today, bool(state.get("json_mode")))
    last = state.get("last_request")
    if not parsed.launch and state.get("scoped") and last:  # "check again": the same launch
        old = Request.from_state(last)
        return Request(old.launch, old.launch_date, old.hints, parsed.json_mode, parsed.pasted or old.pasted)
    return parsed


async def _pull(state: LaunchState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return _reply(replies.NO_TRACE_CONTEXT)
    request = _request(state, deps)
    query = state["query"]
    if not state.get("resume"):
        if WRITE_ASK.search(query) and not request.launch:
            return _reply(replies.READ_ONLY_REPLY)
        if not state.get("scoped") and not request.from_envelope and (not request.launch or not DOMAIN.search(query)):
            verdict = await ask_once(deps, ({"role": "system", "content": prompts.SCOPE_CHECK_SYSTEM},
                                            {"role": "user", "content": query}))
            if verdict.startswith(prompts.OFF_TOPIC):
                return _reply(smalltalk.OFF_TOPIC_REPLY)
    if not request.launch:
        return {"reply": "", "route": "", "pause": asdict(question.launch_question(request.to_state()))}
    done = await pull(deps, request, query)
    if done.envelope is None:
        return _reply(done.reply)
    return {"reply": done.reply, "route": "", "envelope": done.envelope, "result": done.envelope,
            "result_text": done.text, "last_request": request.to_state()}
