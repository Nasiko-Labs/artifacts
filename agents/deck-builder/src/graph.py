"""The deck-builder LangGraph.

    START → intake ─┬─ fixed reply / command on the stored deck (script, outline, json, upload, rename) → END
                    ├─ content missing → Q1 pause ─────────────────────────────────────────────────────→ END
                    └─ compose (map chunks → outline, or edit) ─┬─ off topic / unusable ───────────────→ END
                                                                └─ finish (check → repair → build → verify → render) → END

A greeting never enters the graph (smalltalk.py). The deck spec, parsed tables and grounding text are
checkpointed per A2A contextId, so edits are incremental and survive replicas; the .pptx bytes are rebuilt
deterministically from the spec. The upload offer and its approval walk run outside the graph (walk.py)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import compose as c
import inputs
import intake as ik
import prompts
import render
import smalltalk
from deps import TurnDeps
from llm import Budget, BudgetExhausted, MissingTraceparent
from session import Built, stored_from

log = logging.getLogger("deck_builder.graph")


class DeckState(TypedDict, total=False):
    deck: dict | None
    tables: list[dict]
    parts: list[str]
    injected: list[str]
    notes: str
    theme: dict
    file_name: str
    folder: str | None
    report: dict
    chart_failures: int
    reply: str          # this turn
    route: str          # this turn
    pause: dict | None  # this turn: the content question


@dataclass
class TurnResult:
    reply: str
    built: Built | None = None
    pause: dict | None = None
    upload: bool = False
    as_json: bool = False


class Scope:
    """One turn's inputs and scratch (never persisted)."""

    def __init__(self, deps: TurnDeps, text: str, accept_content: bool, force_upload: bool):
        self.deps = deps
        self.text = text
        self.accept_content = accept_content
        self.upload = force_upload or inputs.wants_upload(text)
        self.as_json = inputs.wants_json(text)
        self.budget = Budget()
        self.built: Built | None = None
        self.work: c.Work | None = None


class DeckAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, text: str, context_id: str, deps: TurnDeps, *, accept_content: bool = False,
                   force_upload: bool = False) -> TurnResult:
        canned = smalltalk.reply_to(text)
        if canned is not None:  # a greeting or small talk: no tool or LLM call, state untouched
            return TurnResult(canned)
        scope = Scope(deps, text, accept_content, force_upload)
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        final = await graph.ainvoke({"reply": "", "route": "", "pause": None}, _config(context_id), durability="exit")
        return TurnResult(final.get("reply") or prompts.COULD_NOT_BUILD, scope.built, final.get("pause"),
                          scope.upload and scope.built is not None, scope.as_json)

    async def stored(self, context_id: str):
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        snapshot = await graph.aget_state(_config(context_id))
        return stored_from(dict(snapshot.values or {}))


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(scope: Scope | None) -> StateGraph:
    async def intake(state: DeckState) -> dict:
        return _intake(state, scope)

    async def compose(state: DeckState) -> dict:
        return await _guarded(c.compose(state, scope))

    async def finish(state: DeckState) -> dict:
        return await _guarded(c.finish(state, scope))

    graph = StateGraph(DeckState)
    for name, node in (("intake", intake), ("compose", compose), ("finish", finish)):
        graph.add_node(name, node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", _route, ["compose", END])
    graph.add_conditional_edges("compose", _route, ["finish", END])
    graph.add_edge("finish", END)
    return graph


def _route(state: DeckState) -> str:
    return state.get("route") or END


async def _guarded(step) -> dict:
    try:
        return await step
    except (TimeoutError, BudgetExhausted):
        log.warning("turn ran out of calls or time")
        return {"reply": prompts.BUDGET_SPENT, "route": ""}
    except MissingTraceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}


def _intake(state: DeckState, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    text = scope.text
    stored = stored_from(dict(state))
    if stored is not None:
        command = _command(stored, state, scope)
        if command is not None:
            return command
    elif inputs.is_script(text) or inputs.is_outline(text) or ik.is_json_only(text) or inputs.is_upload_only(text):
        return {"reply": prompts.NO_DECK, "route": ""}
    parsed = inputs.parse(text, first_table=len(state.get("tables") or []) + 1)
    if stored is None and not scope.accept_content and ik.content_missing(parsed):
        lead, hitl = ik.content_question()
        return {"pause": {"text": lead, "hitl": hitl, "stage": "content", "request": text}, "route": ""}
    scope.work = c.Work(parsed=parsed)
    return {"route": "compose"}


def _command(stored, state: DeckState, scope: Scope) -> dict | None:
    """A command that needs no model call on the stored deck, or None for an edit."""
    text = scope.text
    rename, folder = inputs.file_name(text), inputs.folder_name(text)
    updates: dict = {}
    if rename:
        updates["file_name"] = render.file_name(stored.deck, rename)
    if folder:
        updates["folder"] = folder
    if inputs.is_script(text):
        scope.built = c.rebuild(stored, state)
        return {"reply": render.script_block(c.script_for(stored, state)), "route": ""}
    if inputs.is_outline(text):
        scope.built = c.rebuild(stored, state)
        return {"reply": render.outline(stored.deck, stored.report, lead="Current deck"), "route": ""}
    if ik.is_json_only(text):
        scope.built, scope.as_json = c.rebuild(stored, state), True
        return {"reply": "", "route": ""}
    if inputs.is_upload_only(text) or ik.is_rename_or_folder_only(text):
        scope.built, scope.upload = c.rebuild(stored, {**state, **updates}), True
        return {**updates, "reply": "", "route": ""}
    return None
