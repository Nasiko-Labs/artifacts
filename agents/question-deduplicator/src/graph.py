"""The question-deduplicator LangGraph.

    START → turn → END

One node; LangGraph is here for the checkpointer. State per A2A contextId (thread_id): the last
search (`report`, `ordered` answers, the stored `threads` for EXPAND, which were `shown`) and the
question with its scope and window, so follow-ups (MORE, EXPAND:n, WIDEN:n, a new #channel scope)
are answered from it. Prior state never comes from the chat-history prefix.

Before the graph, in code: a greeting gets the canned intro with zero tool and LLM calls. Inside the
turn: no traceparent → fixed reply before any call; arithmetic → fixed decline; a question under 3
words → fixed prompt; a write ask is refused (never acted on).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import parse
import paste
import prompts
import rank
import render
import run
import smalltalk
from deps import TurnDeps
from models import Outcome, PriorAnswer

log = logging.getLogger("dedup.graph")


class DedupState(TypedDict, total=False):
    query: str
    as_json: bool
    reply: str
    data: dict | None  # this turn's envelope, for the data part
    report: dict
    ordered: list
    threads: dict
    shown: list
    asked: dict  # {"question", "channels", "months"} of the last search


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None


class DedupAgent:
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
    async def turn(state: DedupState) -> dict:
        return await _turn(state, deps)

    graph = StateGraph(DedupState)
    graph.add_node("turn", turn)
    graph.add_edge(START, "turn")
    graph.add_edge("turn", END)
    return graph


def _reply(text: str) -> dict:
    return {"reply": text, "data": None}


async def _turn(state: DedupState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return _reply(prompts.NO_TRACE_CONTEXT)
    raw_question, pasted = paste.split(state["query"])
    request = parse.parse(raw_question, bool(state.get("as_json")))
    if parse.is_arithmetic(request.question):
        return _reply(smalltalk.OFF_TOPIC_REPLY)
    asked = state.get("asked")
    if pasted and request.word_count < parse.MIN_QUESTION_WORDS:
        return _reply(prompts.TOO_SHORT)
    if pasted:
        return _finish(await run.run_pasted(deps, request.question, pasted), request, None)
    followed = await _follow(state, request, asked, deps)
    if followed is not None:
        return followed
    if request.word_count < parse.MIN_QUESTION_WORDS:
        text = prompts.READ_ONLY_NOTE if request.wants_write else prompts.TOO_SHORT
        if request.wants_write and asked:
            text += f"\n\n> {render.clean(parse.restate(asked['question']), 1600)}"
        return _reply(text)
    notes = [f"Used the first {parse.QUERY_TEXT_CHARS:,} characters of your message to build the searches."] \
        if request.truncated else []
    outcome = await run.run(deps, request.question, request.channels, request.months, request.truncated, notes)
    asked_now = {"question": request.question, "channels": list(request.channels), "months": request.months}
    return _finish(outcome, request, asked_now)


async def _follow(state: DedupState, request: parse.Request, asked: dict | None, deps: TurnDeps) -> dict | None:
    """A follow-up decided in code, or None when the message is a new question."""
    follow = request.followup
    short = request.word_count < parse.MIN_QUESTION_WORDS
    rescope = short and (request.channels or request.explicit_months)
    if follow is None and not rescope:
        return None
    if not asked:
        return _reply(prompts.NO_PRIOR)
    if follow is not None and follow.kind == "more":
        return _more(state, request)
    if follow is not None and follow.kind == "expand":
        return _expand(state, follow.number, deps)
    months = follow.number if follow is not None else (request.months if request.explicit_months else asked["months"])
    channels = tuple(request.channels) or tuple(asked["channels"])
    outcome = await run.run(deps, asked["question"], channels, months, len(asked["question"]) > parse.QUERY_TEXT_CHARS)
    return _finish(outcome, request, {"question": asked["question"], "channels": list(channels), "months": months})


def _more(state: DedupState, request: parse.Request) -> dict:
    ordered = [PriorAnswer(**a) for a in state.get("ordered") or []]
    shown = list(state.get("shown") or [])
    rest = [a for a in ordered if a.thread_id not in shown][: rank.MAX_SHOWN]
    if not rest:
        return _reply("That's all I found for this question. Say 'expand 1' to see a whole thread.")
    report = {**state["report"], "prior_answers": [a.model_dump() for a in rest],
              "items": [a.model_dump() for a in rest], "coverage": []}
    text = render.as_json(report) if request.as_json else render.report(report, None, numbering_from=len(shown) + 1)
    return {"reply": text, "data": report, "shown": shown + [a.thread_id for a in rest]}


def _expand(state: DedupState, number: int, deps: TurnDeps) -> dict:
    shown = list(state.get("shown") or [])
    by_id = {a["thread_id"]: a for a in state.get("ordered") or []}
    if not 1 <= number <= len(shown) or shown[number - 1] not in by_id:
        return _reply(f"There is no answer {number} in the last result. Pick a number from the list.")
    thread_id = shown[number - 1]
    messages = (state.get("threads") or {}).get(thread_id, [])
    return _reply(render.thread_view(number, by_id[thread_id], messages, deps.now.date()))


def _finish(outcome: Outcome, request: parse.Request, asked: dict | None) -> dict:
    update: dict = {"reply": outcome.reply, "data": outcome.envelope, **outcome.state}
    if outcome.envelope is not None and asked is not None:
        update["asked"] = asked
    if outcome.envelope is not None and request.as_json:
        update["reply"] = render.as_json(outcome.envelope)
    if request.wants_write:
        update["reply"] = f"{prompts.READ_ONLY_NOTE}\n\n{update['reply']}"
    return update
