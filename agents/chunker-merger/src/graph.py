"""The chunker-merger LangGraph (code only, zero model calls, zero network).

    START -> intake (classify, or read the answer to a pause) -> run (split | append | end | merge |
    follow-up; every split verified losslessly) -> END

State is checkpointed per A2A contextId (thread_id): the held text while a long paste arrives in
pieces, the last split's source and options, and the last merge's results, so "show part 3", "split
again with 10,000 chars" and "merge with this one" work. The graph is compiled per turn because its
nodes close over that turn's input; the checkpointer is shared. A pause leaves the graph as a `pause`
value; its state lives on the A2A task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import actions
import followup
import hitl
import replies
from classify import Request, classify
from envelope import Pasted, extract_values
from inputs import Incoming
from split import SplitOptions

FOLLOW_UPS = frozenset({"show_part", "list_parts", "as_json", "resplit", "merge_more", "merge_notes"})


class ChunkerState(TypedDict, total=False):
    held: list[str]          # text arriving over several messages, in pieces
    held_opts: dict
    source: list[str]        # the last split's input, in pieces
    split_opts: dict
    merge_set: list[dict]    # the last merge's results
    last: str                # split | merge
    reply: str               # this turn only
    envelope: dict | None    # this turn only
    pause: dict | None       # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None
    pause: hitl.Pending | None = None


class ChunkerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, pending: hitl.Pending | None = None) -> TurnResult:
        graph = _build_graph(incoming, pending).compile(checkpointer=self._checkpointer)
        fresh = {"reply": "", "envelope": None, "pause": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        reply = _with_notes(final.get("reply") or "", incoming.notes)
        return TurnResult(reply, final.get("envelope"), hitl.Pending.from_metadata(final.get("pause")))


def _build_graph(incoming: Incoming, pending: hitl.Pending | None) -> StateGraph:
    scope: dict[str, Any] = {}

    async def intake_node(state: ChunkerState) -> dict:
        scope["step"] = hitl.interpret(pending, incoming.text) if pending else classify(incoming.text)
        return {}

    async def run_node(state: ChunkerState) -> dict:
        step = scope["step"]
        if isinstance(step, hitl.Answer):
            return _resume(step, pending, dict(state))
        return _run(step, dict(state))

    graph = StateGraph(ChunkerState)
    graph.add_node("intake", intake_node)
    graph.add_node("run", run_node)
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "run")
    graph.add_edge("run", END)
    return graph


def _run(request: Request, state: dict) -> dict:
    if request.kind == "intro":
        return {"reply": request.reply}
    if request.kind in FOLLOW_UPS:
        return followup.handle(request, state)
    if request.kind == "split":
        return actions.split_request(state, request.payload, request.opts)
    if request.kind == "append":
        return actions.append(state, request.payload, request.opts)
    if request.kind == "end":
        return actions.end(state, request.payload, request.opts)
    if request.kind == "merge":
        return actions.merge_values(extract_values(request.payload))
    return {"reply": replies.OFF_TOPIC_REPLY}


def _resume(answer: hitl.Answer, pending: hitl.Pending, state: dict) -> dict:
    opts = SplitOptions.from_dict(pending.opts)
    if answer.action == "text":
        return actions.split(answer.text, opts)
    if answer.action == "values":
        return actions.merge_values(Pasted(answer.values, answer.problems), base=pending.envelopes)
    if answer.action == "split_held":
        return actions.end(state, "", opts)
    cleared = actions.CLEARED_HOLD if pending.kind == hitl.OVER_CAP else {}
    return {"reply": replies.CANCELLED, **cleared}


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
