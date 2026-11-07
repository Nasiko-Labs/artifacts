"""The spec-to-issues LangGraph: one turn's read side.

    START -> scope -+- off-topic -> END (fixed decline + intro)
                    +- "which parts are unclear?" with a stored proposal -> END (answered from it, in code)
                    +- doc (pasted spec, envelope summary, or the document tool loop) -> END

A greeting or small talk never enters the graph (smalltalk.py). State is checkpointed per A2A contextId: the
latest proposal is remembered for follow-ups. The rest of turn 1 (flow.py) and every resume (walk.py) run
outside the graph, their state on the A2A task.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import prompts
import replies
import smalltalk
from budget import TurnLimitReached
from deps import TurnDeps
from llmcall import ask_once, load_json_object
from reader import TurnReader
from records import Spec, issues_from
from request import Ask
from steps_doc import doc_step

_UNCLEAR = ("unclear", "vague", "ambiguous", "needs refinement")


class SpecState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    last: dict          # the latest proposal in this conversation: spec title + issues


@dataclass(frozen=True)
class TurnResult:
    reply: str = ""
    spec: Spec | None = None
    notes: tuple[str, ...] = ()
    salvaged: bool = False
    reader: TurnReader | None = None


class _Scope:
    def __init__(self, deps: TurnDeps, ask: Ask):
        self.deps = deps
        self.ask = ask
        self.reader = TurnReader(deps)
        self.result: TurnResult | None = None


class SpecAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, ask: Ask, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(ask.text) if not ask.pasted else None
        if canned is not None:  # greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        if not deps.traceparent:
            return TurnResult(replies.NO_TRACE_CONTEXT)
        scope = _Scope(deps, ask)
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        final = await graph.ainvoke({"query": ask.text, "reply": "", "route": ""}, _config(context_id),
                                    durability="exit")
        if final.get("reply"):
            return TurnResult(final["reply"])
        return scope.result or TurnResult(replies.UNREADABLE_ANSWER)

    async def remember(self, context_id: str, last: Mapping[str, Any]) -> None:
        if not last:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        await graph.aupdate_state(_config(context_id), {"last": dict(last)}, as_node="doc")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(scope: _Scope | None) -> StateGraph:
    async def check_scope(state: SpecState) -> dict:
        return await _check_scope(scope, state)

    async def doc(state: SpecState) -> dict:
        return await _doc(scope)

    graph = StateGraph(SpecState)
    graph.add_node("scope", check_scope)
    graph.add_node("doc", doc)
    graph.add_edge(START, "scope")
    graph.add_conditional_edges("scope", lambda s: s.get("route") or END, ["doc", END])
    graph.add_edge("doc", END)
    return graph


async def _check_scope(scope: _Scope, state: SpecState) -> dict:
    ask = scope.ask
    sample = ask.text or ask.pasted[:2_000]
    try:
        answer = await ask_once(scope.deps, ({"role": "system", "content": prompts.SCOPE_SYSTEM},
                                             {"role": "user", "content": prompts.fence_ask(sample)}))
    except TurnLimitReached:
        return {"reply": replies.STEP_LIMIT_NOTE, "route": ""}
    data = load_json_object(answer) or {}
    if data.get("status") == prompts.OFF_TOPIC and not ask.pasted:
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    last = state.get("last") or {}
    if not ask.pasted and not ask.link and last and any(w in ask.text.casefold() for w in _UNCLEAR):
        return {"reply": unclear_reply(last), "route": ""}
    return {"route": "doc"}


def unclear_reply(last: Mapping[str, Any]) -> str:
    unclear = [i for i in issues_from(last.get("issues")) if i.estimate is None]
    title = last.get("spec_title") or "the spec"
    if not unclear:
        return f'Nothing in "{title}" was marked unclear: every proposed issue has an estimate.'
    lines = [f'These parts of "{title}" leave the scope open, so they have no estimate:']
    lines += [f'- #{i.local_id} {i.title}: "{i.source_quote}"' for i in unclear]
    return "\n".join(lines)


async def _doc(scope: _Scope) -> dict:
    try:
        found = await doc_step(scope.deps, scope.reader, scope.ask)
    except TurnLimitReached:
        return {"reply": replies.STEP_LIMIT_NOTE, "route": ""}
    if found.reply is not None:
        return {"reply": found.reply, "route": ""}
    scope.result = TurnResult(spec=found.spec, notes=found.notes, salvaged=found.salvaged, reader=scope.reader)
    return {"route": ""}
