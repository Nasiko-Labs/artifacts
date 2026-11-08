"""The weekly-numbers-narrator LangGraph.

    START -+- (a draft in this context, no new source) -> followup -+- answered (why / rewrite) -> END
           |                                                         +- POST / CHANGE_CHANNEL -> post -> END
           |                                                         +- compare changed -> compose (code + narrate)
           |                                                         +- NEW_RUN -> build
           +- (no draft yet, or a new sheet / pasted table) ------------> build -+- draft -> END
                                                                                  +- post asked -> post -> END

build = scope check (only when the message names nothing of this job) -> pasted table or envelope, else
discover -> locate/read the sheet (tool loop, post-checked) -> table (code) -> periods + deltas + movers +
facts (code) -> narrate (1 call, placeholders; 1 repair; template) -> render.
post = post tool + channel (list member channels when none was named) -> the approval card.
A greeting never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId, so a follow-up rewrites, explains or posts the draft already
shown. The graph is compiled per turn because its nodes close over that turn's dependencies; the
checkpointer is shared. The approval card and the post walk run outside the graph (agent_executor, post.py).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import coverage
import models
import movers as moving
import narrate
import parse
import paste
import periods
import post
import prompts
import render
import sheet
import smalltalk
from deps import TurnDeps
from discovery import discover
from facts import Facts, build
from llm import ask_once
from table import Table

__all__ = ["SHEET_KEY", "NarratorAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("numbers_narrator.graph")

TURN_DEADLINE_SECS = 180
MAX_SEEN_CHARS = 30_000
SHEET_KEY = "wnn.sheet_pick"
OUT_OF_TIME = "I ran out of time before finishing this run. Try again, or paste the table."
POST_HINT = 'Say "post it to #channel" to post it after you approve the exact text.'


class NarratorState(TypedDict, total=False):
    query: str
    request: dict
    pick: dict | None          # this turn: the sheet the user picked {"id", "title"}
    table: dict                # table.Table of the last draft
    facts: dict                # facts.Facts of the last draft
    skeleton: str
    narrative: str
    used_template: bool
    first_try: bool
    dropped: int
    seen: str
    post: dict                 # {"channel", "state", "link"}
    coverage: list[str]
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


class NarratorAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, metadata: Mapping[str, Any] | None = None,
                   pick: Mapping[str, str] | None = None) -> TurnResult:
        canned = smalltalk.reply_to(query) if pick is None else None
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "request": parse.parse(query, metadata).to_dict(), "pick": dict(pick or {}) or None,
                 "reply": "", "route": "", "card": None, "question": None, "envelope": None}
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
        """Record a post decision (and an edited text) on the conversation's state."""
        values = await self._values(context_id)
        if not values.get("facts"):
            return
        changes = {k: v for k, v in dict(update).items() if v is not None and k in ("post", "narrative")}
        if not changes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        await graph.aupdate_state(_config(context_id), changes, as_node="build")

    async def _values(self, context_id: str) -> dict:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    async def followup(state: NarratorState) -> dict:
        return await _followup(state, deps)

    async def build_node(state: NarratorState) -> dict:
        return await _build(state, deps)

    async def post_node(state: NarratorState) -> dict:
        return await _post(state, deps)

    graph = StateGraph(NarratorState)
    graph.add_node("followup", followup)
    graph.add_node("build", build_node)
    graph.add_node("post", post_node)
    graph.add_conditional_edges(START, _entry, ["followup", "build"])
    graph.add_conditional_edges("followup", _route, ["build", "post", END])
    graph.add_conditional_edges("build", _route, ["post", END])
    graph.add_edge("post", END)
    return graph


def _entry(state: NarratorState) -> str:
    request = parse.Request.from_dict(state.get("request") or {})
    if state.get("pick") or not state.get("facts") or request.sheet:
        return "build"
    return "build" if paste.read(state["query"]).table is not None else "followup"


def _route(state: NarratorState) -> str:
    return state.get("route") or END


def _request(state: NarratorState) -> parse.Request:
    return parse.Request.from_dict(state.get("request") or {})


# --- build: read the numbers and draft ----------------------------------------------------------------------

async def _build(state: NarratorState, deps: TurnDeps) -> dict:
    query, request = state["query"], _request(state)
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    found = paste.read(query)
    context = request.context or found.context
    if found.table is not None:
        return await _compose(state, deps, found.table, request.compare, context, query)
    pick = state.get("pick") or {}
    if not pick and not request.sheet and not parse.mentions_domain(query) and await _off_topic(deps, query):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    catalog, failure = await discover(deps, ("read",))
    if failure is not None:
        return {"reply": failure if failure != prompts.NO_SHEET_TOOL else prompts.NO_SHEET_TOOL, "route": ""}
    reader = sheet.reader_for(deps, catalog)
    outcome = await sheet.read(deps, reader, query, request, pick.get("id"), pick.get("title"))
    if outcome.off_topic:
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if outcome.candidates:
        return {"question": _sheet_question(outcome.candidates, query), "reply": "", "route": ""}
    if outcome.table is None:
        return {"reply": outcome.reply or prompts.SHEET_UNREADABLE, "route": ""}
    seen = f"{reader.fenced.seen_text}\n{query}"
    return await _compose(state, deps, outcome.table, request.compare, context, seen)


async def _off_topic(deps: TurnDeps, query: str) -> bool:
    answer = await ask_once(deps, ({"role": "system", "content": prompts.SCOPE_SYSTEM},
                                   {"role": "user", "content": query}))
    return not answer.strip().upper().startswith(prompts.IN_SCOPE)


def _sheet_question(candidates: tuple[tuple[str, str], ...], query: str) -> dict:
    from nasiko_hitl import question_metadata

    hitl = question_metadata(prompts.SHEET_HEADER, [(title[:200], "") for _, title in candidates],
                             multi_select=False, allow_custom_input=False)
    names = {title[:200]: cid for cid, title in candidates}
    return {"text": "I found more than one spreadsheet that could be it. " + prompts.SHEET_HEADER, "hitl": hitl,
            "metadata": {SHEET_KEY: {"names": names, "query": query}}}


async def _compose(state: NarratorState, deps: TurnDeps, table: Table, compare: str, context: str | None,
                   seen: str) -> dict:
    """Code computes everything; one narrate call (plus one repair) writes the words."""
    request = _request(state)
    selection = periods.select(table, compare)
    movement = moving.measure(table, selection)
    facts = build(table, selection, movement, context)
    base = {"table": table.to_dict(), "seen": seen[-MAX_SEEN_CHARS:], "coverage": [],
            "post": {"channel": None, "state": "not_requested", "link": None}}
    if selection.previous is None:
        reply = selection.compare_note or prompts.ONE_PERIOD
        env = models.envelope(facts, "", "low", (), models.Post())
        return {**base, "facts": {}, "reply": render.json_block(env) if request.json_mode else reply,
                "envelope": env, "route": ""}
    narrative = await narrate.write(deps, facts, seen)
    return _drafted(state, base, facts, narrative)


def _drafted(state: NarratorState, base: dict, facts: Facts, narrative: narrate.Narrative) -> dict:
    request = _request(state)
    wants_post = request.wants_post or request.dry_run
    stored = {**base, "facts": facts.to_dict(), "skeleton": narrative.skeleton, "narrative": narrative.text,
              "used_template": narrative.used_template, "first_try": narrative.first_try,
              "dropped": narrative.dropped}
    env = _envelope({**state, **stored})
    hint = "" if wants_post else POST_HINT
    reply = (render.json_block(env) if request.json_mode
             else render.draft(narrative.text, facts, env["confidence"], (), hint))
    return {**stored, "reply": reply, "envelope": env, "route": "post" if wants_post else ""}


def _envelope(state: Mapping[str, Any], post_state: Mapping[str, Any] | None = None) -> dict:
    facts = Facts.from_dict(state["facts"])
    table = Table.from_dict(state.get("table") or {})
    table_rows = len(periods.select(table, facts.compare).order) if table is not None else 0
    confidence = models.confidence_of(facts, table_rows, bool(state.get("used_template")),
                                      bool(state.get("first_try")), int(state.get("dropped") or 0))
    stored_post = dict(post_state or state.get("post") or {})
    post_out = models.Post(channel=stored_post.get("channel"), state=stored_post.get("state") or "not_requested",
                           link=stored_post.get("link"))
    return models.envelope(facts, str(state.get("narrative") or ""), confidence,
                           narrate.cited_notes(str(state.get("skeleton") or "")), post_out,
                           tuple(state.get("coverage") or ()))


# --- post: the card, the channel pick, or copyable text ------------------------------------------------------

async def _post(state: NarratorState, deps: TurnDeps) -> dict:
    request = _request(state)
    lead = state.get("reply") or ""
    if not state.get("facts"):
        return {"reply": prompts.NO_DRAFT_YET, "route": ""}
    channel = request.channel or _stored_channel(state, request)
    plan = await post.prepare(deps, str(state.get("narrative") or ""), channel, request.dry_run)
    if plan.copy_reason:
        posted = {"channel": f"#{channel}" if channel else None, "state": "copyable", "link": None}
        text = f"{plan.copy_reason}\n\n{render.copyable(str(state.get('narrative') or ''))}"
        lines = _coverage_lines(plan)
        return {"reply": "\n\n".join(p for p in (lead, text, *lines) if p), "post": posted,
                "envelope": _envelope({**state, "coverage": lines}, posted), "route": ""}
    if plan.question is not None:
        text, hitl, metadata = plan.question
        return {"question": {"text": text, "hitl": hitl, "metadata": metadata}, "route": ""}
    awaiting = {"channel": plan.draft.label, "state": "awaiting_approval", "link": None}
    return {"card": plan.draft.to_dict(), "envelope": _envelope(state, awaiting), "route": ""}


def _stored_channel(state: NarratorState, request: parse.Request) -> str | None:
    """A plain "post it" reuses the channel of the last post decision; "change channel" asks again."""
    if re.search(r"\b(?:another|different|other) channel\b|\bchange (?:the )?channel\b", state["query"], re.IGNORECASE):
        return None
    stored = (state.get("post") or {}).get("channel")
    return str(stored).lstrip("#") if stored else None


def _coverage_lines(plan: post.Plan) -> tuple[str, ...]:
    if plan.copy_reason == prompts.NO_CHAT_TOOL:
        return (coverage.line(coverage.SourceStatus("chat", "missing")),)
    return ()


# --- follow-ups --------------------------------------------------------------------------------------------

async def _followup(state: NarratorState, deps: TurnDeps) -> dict:
    query, request = state["query"], _request(state)
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    facts = Facts.from_dict(state["facts"])
    if request.compare_named and request.compare != facts.compare:
        return await _recompute(state, deps, request)
    if request.wants_post or request.dry_run:
        return {"route": "post"}
    if request.why:
        return {"reply": _why(state, query), "route": ""}
    if request.rewrite:
        return await _rewrite(state, deps, query)
    answer = await ask_once(deps, ({"role": "system", "content": prompts.FOLLOWUP_SYSTEM},
                                   {"role": "user", "content": query}))
    return await _sentinel(state, deps, answer.strip(), query)


async def _sentinel(state: NarratorState, deps: TurnDeps, answer: str, query: str) -> dict:
    head = answer.split(":", 1)[0].strip().upper()
    rest = answer.split(":", 1)[1].strip() if ":" in answer else ""
    if head == "POST":
        return {"route": "post"}
    if head == "CHANGE_CHANNEL":
        return {"route": "post", "post": {**dict(state.get("post") or {}), "channel": None}}
    if head == "WHY":
        return {"reply": _why(state, rest or query), "route": ""}
    if head in ("REWRITE", "TONE"):
        return await _rewrite(state, deps, rest or query)
    if head == "NEW_RUN":
        return {"route": "build"}
    if head == prompts.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    return {"reply": prompts.UNCLEAR, "route": ""}


async def _recompute(state: NarratorState, deps: TurnDeps, request: parse.Request) -> dict:
    table = Table.from_dict(state.get("table") or {})
    if table is None:
        return {"route": "build"}
    previous = Facts.from_dict(state["facts"])
    context = request.context or next((n.text for n in previous.notes if n.source == "user"), None)
    return await _compose(state, deps, table, request.compare, context, str(state.get("seen") or ""))


async def _rewrite(state: NarratorState, deps: TurnDeps, instruction: str) -> dict:
    facts = Facts.from_dict(state["facts"])
    found = await narrate.rewrite(deps, facts, str(state.get("skeleton") or ""), instruction,
                                  str(state.get("seen") or ""))
    if found is not None and found.off_topic:
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if found is None:
        current = str(state.get("narrative") or "")
        return {"reply": f"{prompts.REWRITE_FAILED}\n\n{current}", "route": ""}
    base = {"table": state.get("table"), "seen": state.get("seen"), "coverage": [],
            "post": {"channel": None, "state": "not_requested", "link": None}}
    return _drafted(state, base, facts, found)


def _why(state: NarratorState, question: str) -> str:
    facts = Facts.from_dict(state["facts"])
    metric = next((m for m in sorted(facts.metric_names, key=len, reverse=True)
                   if re.search(rf"\b{re.escape(m)}\b", question, re.IGNORECASE)), None)
    mover = next((m for m in facts.movers if m.metric == metric), None) if metric else facts.top
    metric = metric or (mover.metric if mover else None)
    if metric is None:
        return "Name the metric you're asking about. " + prompts.UNCLEAR
    change = (f"{metric} {mover.word} {mover.pct} ({mover.prev} to {mover.cur}) from {facts.period_previous} to "
              f"{facts.period_current}." if mover else f"{metric} isn't one of this period's movers.")
    own = [n for n in facts.notes if n.metric == metric or n.source == "user"]
    if own:
        return f"{change} What the data gives me: " + " ".join(narrate.quote_note(n) for n in own)
    general = [n for n in facts.notes if n.metric is None and n.source != "user"]
    tail = (" The only note for the period is " + " ".join(narrate.quote_note(n) for n in general) +
            f", which doesn't mention {metric}." if general else "")
    return (f"{change} The sheet has no notes explaining the change in {metric}, so I won't guess a cause.{tail} "
            "If you know why, tell me with \"context: ...\" and I'll include it, attributed to you.")
