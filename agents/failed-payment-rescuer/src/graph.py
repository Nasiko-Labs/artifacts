"""The failed-payment-rescuer LangGraph.

    START ─┬─ (a list was given in this context) → followup ─┬─ answered ──────────────→ END
           │                                                 ├─ DRAFT:<numbers> → draft → END (walk)
           │                                                 ├─ POST_DIGEST → post → END (walk or channel question)
           │                                                 └─ NEEDS_FETCH ─┐
           └─ (no list yet) ────────────────────────────────→ fetch ←───────┘
              fetch ─┬─ fixed reply → END
                     ├─ pasted rows → rank → END
                     └─ failures → enrich → contact (skipped after 240 s or without email) → rank → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId (thread_id), so a follow-up sees the list, what was sent and the
time of the last run. The graph is compiled per turn because its nodes close over that turn's dependencies
(LLM client, gateway, inbound traceparent); the checkpointer is shared. Drafts, the picker and the channel
question leave the graph in the turn result; the approve → send walk runs outside it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import checks
import coverage
import drafting
import failures as fl
import picker
import prompts
import render
import scoring
import smalltalk
import steps
from deps import TurnDeps
from discovery import RECOVER_META, Catalog, classify, discover
from drafts import Draft
from fence import FencedGateway
from nasiko_hitl import question_metadata
from tool_health import ToolHealth

__all__ = ["RescuerAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("payment_rescuer.graph")

TURN_DEADLINE_SECS = 300
CHANNEL_KEY = "payment_rescuer_channel"
CHANNEL_HEADER = "Which channel should I post the digest to?"
OUT_OF_TIME = "I ran out of time before finishing this run. Try a shorter window or a minimum amount."
_HIGH_VALUE = re.compile(r"\bhigh[- ]value\b|\bbiggest\b", re.IGNORECASE)


class RescuerState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    items: list[dict]          # the list last shown, numbered: what "#2" refers to
    outcomes: dict[str, str]   # failure key (or "digest") → sent / skipped / denied / blocked / not_sent
    drafted: dict[str, str]    # failure key → the drafted body
    meta: dict                 # render.ListMeta of the list last shown
    last_run_at: str           # ISO time of the last fetch in this conversation
    channel: str               # the digest channel the user named
    request: dict              # this turn: the parsed request
    work: list[dict]           # this turn: failures moving through enrich/contact
    work_meta: dict            # this turn: counts and notes so far
    draft_numbers: list[str]
    drafts: list[dict]         # this turn: drafts ready for the approval walk
    pick: dict | None          # this turn: the "which should I draft?" question
    channel_question: dict | None
    envelope: dict | None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    drafts: tuple[Draft, ...] = ()
    pick: picker.Pick | None = None
    channel_question: picker.Pick | None = None
    envelope: dict | None = None


class _Scope:
    """One turn's read side, shared by the read steps (never persisted)."""

    def __init__(self, deps: TurnDeps):
        self.deps = deps
        self.reader: steps.Reader | None = None


class RescuerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps,
                   metadata: Mapping[str, Any] | None = None) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(_Scope(deps)).compile(checkpointer=self._checkpointer)
        request = fl.parse_request(query, metadata)
        fresh = {"query": query, "reply": "", "route": "", "request": _request_dict(request), "work": [],
                 "work_meta": {}, "draft_numbers": [], "drafts": [], "pick": None, "channel_question": None,
                 "envelope": None}
        try:
            # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
            final = await asyncio.wait_for(graph.ainvoke(fresh, _config(context_id), durability="exit"),
                                           TURN_DEADLINE_SECS)
        except TimeoutError:
            log.warning("turn passed its %ss deadline", TURN_DEADLINE_SECS)
            return TurnResult(OUT_OF_TIME)
        return _result(final)

    async def draft_picked(self, keys: Sequence[str], instruction: str, context_id: str,
                           deps: TurnDeps) -> TurnResult:
        """Drafts for the failures picked on the multi-select (by stable key), with any typed instruction."""
        values = await self._values(context_id)
        items = fl.from_state(values.get("items"))
        numbers = [f.number for k in keys for f in items if f.key == k]
        turn = await drafting.draft_turn(deps, instruction, numbers, items, _view(values, deps))
        await self._remember(context_id, {"drafted": {**dict(values.get("drafted") or {}), **turn.drafted}})
        return TurnResult(turn.reply, turn.drafts)

    async def post_to(self, channel: str, context_id: str, deps: TurnDeps) -> TurnResult:
        """The answer to the channel question: the digest for that channel."""
        values = await self._values(context_id)
        turn = await drafting.digest_turn(deps, channel, fl.from_state(values.get("items")), _view(values, deps))
        await self._remember(context_id, {"channel": channel})
        return TurnResult(turn.reply, turn.drafts)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember what happened to each draft, so later turns neither resend nor redraft it."""
        if not outcomes:
            return
        values = await self._values(context_id)
        await self._remember(context_id, {"outcomes": {**dict(values.get("outcomes") or {}), **dict(outcomes)},
                                          "drafts": []})

    async def _remember(self, context_id: str, update: Mapping[str, Any]) -> None:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        await graph.aupdate_state(_config(context_id), dict(update), as_node="rank")

    async def _values(self, context_id: str) -> dict:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _request_dict(request: fl.Request) -> dict:
    return {**request.__dict__, "customers": list(request.customers)}


def _request(state: RescuerState) -> fl.Request:
    raw = dict(state.get("request") or {})
    return fl.Request(**{**raw, "customers": tuple(raw.get("customers") or ())})


def _view(state: Mapping[str, Any], deps: TurnDeps) -> render.View:
    return render.View(now=deps.now, outcomes=dict(state.get("outcomes") or {}),
                       drafted=dict(state.get("drafted") or {}))


def _result(final: Mapping[str, Any]) -> TurnResult:
    drafts = tuple(Draft.from_dict(d) for d in final.get("drafts") or ())
    pick = final.get("pick")
    question = final.get("channel_question")
    return TurnResult(final.get("reply") or "", drafts, picker.Pick(**pick) if pick else None,
                      picker.Pick(**question) if question else None, final.get("envelope"))


def _build_graph(scope: _Scope | None) -> StateGraph:
    async def followup(state: RescuerState) -> dict:
        return await _followup(state, scope.deps)

    async def fetch(state: RescuerState) -> dict:
        return await _fetch(state, scope)

    async def enrich(state: RescuerState) -> dict:
        return await _enrich(state, scope)

    async def contact(state: RescuerState) -> dict:
        return await _contact(state, scope)

    async def rank(state: RescuerState) -> dict:
        return _rank(state, scope.deps)

    async def draft(state: RescuerState) -> dict:
        turn = await drafting.draft_turn(scope.deps, state["query"], state.get("draft_numbers") or (),
                                         fl.from_state(state.get("items")), _view(state, scope.deps))
        return {"reply": turn.reply, "drafts": [d.to_dict() for d in turn.drafts], "route": "",
                "drafted": {**dict(state.get("drafted") or {}), **turn.drafted}}

    async def post(state: RescuerState) -> dict:
        return await _post(state, scope)

    graph = StateGraph(RescuerState)
    for name, node in (("followup", followup), ("fetch", fetch), ("enrich", enrich), ("contact", contact),
                       ("rank", rank), ("draft", draft), ("post", post)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "fetch"])
    graph.add_conditional_edges("followup", _route, ["fetch", "draft", "post", END])
    graph.add_conditional_edges("fetch", _route, ["enrich", "rank", END])
    graph.add_edge("enrich", "contact")
    graph.add_edge("contact", "rank")
    for terminal in ("rank", "draft", "post"):
        graph.add_edge(terminal, END)
    return graph


def _entry(state: RescuerState) -> str:
    return "followup" if state.get("items") and not fl.pasted_rows(state["query"]) else "fetch"


def _route(state: RescuerState) -> str:
    return state.get("route") or END


# --- follow-ups -------------------------------------------------------------------------------------------

async def _followup(state: RescuerState, deps: TurnDeps) -> dict:
    query = state["query"]
    items = fl.from_state(state.get("items"))
    if fl.asks_to_post(query):  # parsed in code: no LLM call
        return {"route": "post"}
    if fl.asks_to_draft(query):
        return {"route": "draft", "draft_numbers": list(fl.numbers_in(query))}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    listing = render.render_for_model(items, _view(state, deps))
    # Only the fenced list, never earlier replies: those carry tool text outside a fence.
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(listing)}"
    text = await steps.ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.NEEDS_FETCH):
        return {"route": "fetch"}
    if text.startswith(prompts.POST_DIGEST):
        return {"route": "post"}
    if text.startswith(prompts.DRAFT_PREFIX):
        numbers = fl.numbers_in(" ".join(f"#{n}" for n in re.findall(r"\d+", text[len(prompts.DRAFT_PREFIX):])))
        if not numbers and _HIGH_VALUE.search(query):
            view = _view(state, deps)
            numbers = tuple(f.number for f in items if view.scored(f).high_value)
        return {"route": "draft", "draft_numbers": list(numbers)}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}


async def _post(state: RescuerState, scope: _Scope) -> dict:
    deps = scope.deps
    channel = _request(state).channel or state.get("channel")
    items = fl.from_state(state.get("items"))
    view = _view(state, deps)
    if channel:
        turn = await drafting.digest_turn(deps, channel, items, view)
        return {"reply": turn.reply, "drafts": [d.to_dict() for d in turn.drafts], "channel": channel, "route": ""}
    if not any(view.scored(f).high_value for f in items):
        return {"reply": prompts.NO_HIGH_VALUE, "route": ""}
    names: tuple[str, ...] = ()
    catalog, failure = await discover(deps, "read")
    if failure is None:
        names = await steps.list_channels(deps, _reader(deps, catalog))
    return {"channel_question": _channel_question(names), "route": ""}


def _channel_question(names: Sequence[str]) -> dict:
    text = f"{CHANNEL_HEADER} Pick one or type a channel name."
    hitl = question_metadata(CHANNEL_HEADER, [(f"#{n}", "") for n in names], allow_custom_input=True) if names \
        else None
    return {"text": text, "hitl": hitl, "metadata": {CHANNEL_KEY: {"asked": True}}}


# --- turn 1 ----------------------------------------------------------------------------------------------

async def _fetch(state: RescuerState, scope: _Scope) -> dict:
    deps = scope.deps
    request = _since_last_run(_request(state), state, deps)
    pasted = fl.pasted_rows(state["query"])
    if pasted:
        checked = checks.post_check(pasted, now=deps.now, request=request, seen_text=state["query"], pasted=True)
        meta = {"window_days": request.window_days, "dropped": checked.dropped, "filtered": checked.filtered,
                "capped": checked.capped, "notes": [prompts.PASTED_NOTE], "pasted": True,
                "request": _request_dict(request)}
        return {"work": fl.to_state(steps.unchecked_contact(checked.failures)), "work_meta": meta, "route": "rank"}
    catalog, failure = await discover(deps, "read")
    if failure is not None:
        return {"reply": _refusal(failure, state["query"]), "route": ""}
    scope.reader = _reader(deps, catalog)
    outcome = await steps.fetch_failures(deps, scope.reader, state["query"], request)
    if outcome.reply is not None:  # a refusal or an empty result keeps the previous list
        return {"reply": _refusal(outcome.reply, state["query"]), "route": ""}
    checked = outcome.checked
    meta = {"window_days": request.window_days, "dropped": checked.dropped, "filtered": checked.filtered,
            "capped": checked.capped, "notes": list(outcome.notes), "livemode": outcome.livemode,
            "salvaged": outcome.salvaged, "request": _request_dict(request)}
    return {"work": fl.to_state(checked.failures), "work_meta": meta, "route": "enrich"}


def _refusal(reply: str, query: str) -> str:
    """A refusal that ends the turn before any model call still declines an off-topic message as such."""
    unreadable = reply.startswith((prompts.NO_PAYMENTS_TOOL, prompts.GATEWAY_UNREACHABLE))
    return smalltalk.OFF_TOPIC_REPLY if unreadable and not fl.mentions_domain(query) else reply


def _since_last_run(request: fl.Request, state: RescuerState, deps: TurnDeps) -> fl.Request:
    if not request.since_last_run or not state.get("last_run_at"):
        return request
    last = checks.parse_time(state["last_run_at"])
    return fl.Request(**{**request.__dict__, "window_days": fl.days_since(last, deps.now)}) if last else request


async def _enrich(state: RescuerState, scope: _Scope) -> dict:
    items, ran = await steps.enrich(scope.deps, scope.reader, fl.from_state(state["work"]))
    meta = dict(state.get("work_meta") or {})
    notes = list(meta.get("notes") or []) + ([] if ran else [prompts.HISTORY_SKIPPED_NOTE])
    return {"work": fl.to_state(items), "work_meta": {**meta, "history_ran": ran, "notes": notes}}


async def _contact(state: RescuerState, scope: _Scope) -> dict:
    deps, reader = scope.deps, scope.reader
    items = fl.from_state(state["work"])
    meta = dict(state.get("work_meta") or {})
    note = None
    if not reader.tools_for("email"):
        note = prompts.NO_EMAIL_READ_NOTE
    elif deps.elapsed() > steps.CONTACT_TIME_BUDGET_SECS:
        log.info("history ended after %.0fs; skipping the contact step", deps.elapsed())
        note = prompts.CONTACT_SKIPPED_NOTE
    checked = None if note else await steps.contact(deps, reader, items)
    if checked is None:
        status = reader.status("email")
        note = note or (f"Prior contact not checked. {coverage.line(status)}" if not status.ok
                        else "Prior contact not checked: the email search did not finish.")
        notes = list(meta.get("notes") or []) + [note]
        return {"work": fl.to_state(steps.unchecked_contact(items)), "work_meta": {**meta, "notes": notes}}
    return {"work": fl.to_state(checked), "work_meta": {**meta, "contact_ran": True}}


def _reader(deps: TurnDeps, catalog: Catalog) -> steps.Reader:
    tools = catalog.read_tools()
    log.info("read tools offered: %s", [t.name for t in tools])
    fenced = FencedGateway(deps.gateway)
    # Strict: a tool counts for a source only by its own words, never by which search query found it.
    return steps.Reader(tools=tools, fenced=fenced, health=ToolHealth(fenced), source_of=classify,
                        helpers=frozenset({RECOVER_META}))


def _rank(state: RescuerState, deps: TurnDeps) -> dict:
    work_meta = dict(state.get("work_meta") or {})
    request = fl.Request(**{**(work_meta.get("request") or {}),
                            "customers": tuple((work_meta.get("request") or {}).get("customers") or ())})
    ranked = scoring.rank(fl.from_state(state.get("work")), deps.now)
    meta = render.ListMeta.from_dict(work_meta, fl.DEFAULT_WINDOW_DAYS)
    view = render.View(now=deps.now, outcomes={}, drafted={})
    env = render.envelope(ranked, meta, view)
    base = {"items": fl.to_state(ranked), "outcomes": {}, "drafted": {}, "meta": meta.to_dict(), "route": "",
            "last_run_at": deps.now.isoformat(), "envelope": render.envelope_dict(env), "work": []}
    if request.json_mode:
        return {**base, "reply": render.json_block(env), "pick": None}
    pick = picker.question(ranked, view)
    return {**base, "reply": render.render_list(ranked, meta, view),
            "pick": {"text": pick.text, "hitl": pick.hitl, "metadata": pick.metadata} if pick else None}
