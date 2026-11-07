"""The invoice-chaser LangGraph.

    START ─┬─ (a list was given in this context) → followup ─┬─ answered ──────────→ END
           │                                                 ├─ DRAFT:<ids> → draft → END (walk)
           │                                                 └─ NEEDS_FETCH ─┐
           └─ (no list yet) ─────────────────────────────────→ fetch ←───────┘
              fetch (parse → discover → invoices → customer) ─┬─ fixed reply → END
                                                              ├─ reminders → ladder (code) → END
                                                              └─ no email / out of time → ladder → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId (thread_id), so a follow-up sees the list and what was already
sent. The graph is compiled per turn because its nodes close over that turn's dependencies (LLM client,
gateway, inbound traceparent); the checkpointer is shared. Drafts, the picker and the address question
leave the graph in the turn result; the approve → send walk runs outside it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import checks
import coverage
import drafting
import invoices as inv
import ladder
import picker
import prompts
import render
import smalltalk
import steps
from deps import TurnDeps
from discovery import discover
from drafts import Draft

__all__ = ["ChaserAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("invoice_chaser.graph")

REMINDERS_TIME_BUDGET_SECS = 240  # invoices + customer must end by then for the reminders step to run
MAX_KNOWN_ADDRESSES = 200
# Words that make a message plainly about this job; anything else gets one tool-less scope check first,
# so an off-topic message is declined even when no payments tool is connected.
_DOMAIN = re.compile(r"invoice|overdue|past due|late|chas|remind|dunning|pay|paid|owe|owing|outstanding|receivable|"
                     r"customer|client|bill|collect|\bdue\b|draft|follow|nudge|escalat|amount|balance|refresh|"
                     r"check again|#\d", re.IGNORECASE)
_SCHEDULE = re.compile(r"\b(?:daily|every (?:day|morning|week)|weekly|on a schedule|schedule|automatically)\b", re.IGNORECASE)


class ChaserState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    items: list[dict]          # the list last shown, numbered: what "#2" refers to
    outcomes: dict[str, str]   # entry key → sent / skipped / denied / blocked / not_sent / noted
    meta: dict                 # render.ListMeta of the list last shown
    known: list[str]           # addresses seen in this conversation's tool output (send targets allowed)
    checked: list[dict]        # this turn: entries after the invoices/customer steps
    turn_meta: dict            # this turn: the ListMeta being built
    draft_ids: list[str]
    drafts: list[dict]         # this turn: drafts ready for the approval walk
    draft_texts: dict          # this turn: entry key → drafted text (envelope)
    pick: dict | None          # this turn: the "which reminders?" question
    address: dict | None       # this turn: the "which address?" question
    envelope: dict | None      # this turn: the structured envelope
    json_mode: bool


@dataclass(frozen=True)
class TurnResult:
    reply: str
    drafts: tuple[Draft, ...] = ()
    pick: picker.Pick | None = None
    address: drafting.AddressAsk | None = None
    envelope: dict | None = None
    outcomes: Mapping[str, str] | None = None


class _Scope:
    """One turn's read side (never persisted)."""

    def __init__(self, deps: TurnDeps):
        self.deps = deps
        self.email: steps.Reader | None = None


class ChaserAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(_Scope(deps)).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "checked": [], "turn_meta": {}, "draft_ids": [],
                 "drafts": [], "draft_texts": {}, "pick": None, "address": None, "envelope": None,
                 "json_mode": inv.wants_json(query)}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        return _result(final)

    async def draft_picked(self, keys: Sequence[str], instruction: str, context_id: str,
                           deps: TurnDeps) -> TurnResult:
        """Drafts for the invoices picked on the multi-select (by stable key)."""
        entries = inv.from_state((await self._values(context_id)).get("items"))
        ids = [e.id for k in keys for e in entries if e.key == k]
        return await self.draft_named(ids, instruction, context_id, deps)

    async def draft_named(self, ids: Sequence[str], instruction: str, context_id: str, deps: TurnDeps,
                          addresses: Mapping[str, str | None] | None = None) -> TurnResult:
        """Drafts for invoices by list number, with any address the user typed for one without an email."""
        values = await self._values(context_id)
        turn = await drafting.draft_turn(deps, instruction, ids, inv.from_state(values.get("items")),
                                         _view(values, deps), values.get("known") or (), addresses)
        if turn.outcomes:
            await self.record_outcomes(context_id, turn.outcomes)
        return TurnResult(turn.reply, turn.drafts, address=turn.ask)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember what happened to each reminder, so later turns neither resend nor redraft it."""
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        values = await self._values(context_id)
        merged = {**dict(values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "drafts": []}, as_node="ladder")

    async def _values(self, context_id: str) -> dict:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _view(state: Mapping[str, Any], deps: TurnDeps) -> render.View:
    return render.View(today=deps.today, outcomes=dict(state.get("outcomes") or {}))


def _result(final: Mapping[str, Any]) -> TurnResult:
    pick, address = final.get("pick"), final.get("address")
    return TurnResult(final.get("reply") or "", tuple(Draft.from_dict(d) for d in final.get("drafts") or ()),
                      picker.Pick(**pick) if pick else None, drafting.AddressAsk(**address) if address else None,
                      final.get("envelope"))


def _build_graph(scope: _Scope | None) -> StateGraph:
    async def followup(state: ChaserState) -> dict:
        return await _followup(state, scope.deps)

    async def fetch(state: ChaserState) -> dict:
        return await _fetch(state, scope)

    async def reminders(state: ChaserState) -> dict:
        return await _reminders(state, scope)

    async def rank(state: ChaserState) -> dict:
        return _ladder(state, scope.deps)

    async def draft(state: ChaserState) -> dict:
        view = _view(state, scope.deps)
        turn = await drafting.draft_turn(scope.deps, state["query"], state.get("draft_ids") or (),
                                         inv.from_state(state.get("items")), view, state.get("known") or ())
        ask = turn.ask
        address = {"text": ask.text, "hitl": ask.hitl, "metadata": ask.metadata} if ask else None
        outcomes = {**dict(state.get("outcomes") or {}), **dict(turn.outcomes)}
        return {"reply": turn.reply, "drafts": [d.to_dict() for d in turn.drafts], "address": address,
                "outcomes": outcomes, "route": ""}

    graph = StateGraph(ChaserState)
    for name, node in (("followup", followup), ("fetch", fetch), ("reminders", reminders), ("ladder", rank),
                       ("draft", draft)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "fetch"])
    graph.add_conditional_edges("followup", _route, ["fetch", "draft", END])
    graph.add_conditional_edges("fetch", _route, ["reminders", "ladder", END])
    graph.add_edge("reminders", "ladder")
    for terminal in ("ladder", "draft"):
        graph.add_edge(terminal, END)
    return graph


def _entry(state: ChaserState) -> str:
    return "followup" if state.get("items") else "fetch"


def _route(state: ChaserState) -> str:
    return state.get("route") or END


async def _followup(state: ChaserState, deps: TurnDeps) -> dict:
    if inv.envelope_invoice_ids(state["query"]) or inv.pasted_rows(state["query"]):
        return {"route": "fetch"}  # new input data: a fresh read
    named = drafting.ids_in_request(state["query"])
    if named:  # "draft the next reminder for #2", parsed in code: no LLM call
        return {"route": "draft", "draft_ids": list(named)}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    listing = render.render_for_model(inv.from_state(state.get("items")), _view(state, deps))
    # Only the fenced list, never earlier replies: those carry tool text outside a fence.
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(listing)}"
    text = await steps.ask_once(deps, ({"role": "system", "content": system},
                                       {"role": "user", "content": state["query"]}))
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.NEEDS_FETCH):
        return {"route": "fetch"}
    if text.startswith(prompts.DRAFT_PREFIX):
        return {"route": "draft", "draft_ids": list(drafting.requested_ids(text))}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}


async def _fetch(state: ChaserState, scope: _Scope) -> dict:
    deps = scope.deps
    request = inv.parse_request(state["query"])
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    if request.pasted:
        return await _from_pasted(request, scope)
    if not request.invoice_ids and not _DOMAIN.search(state["query"]) and await _off_topic(state["query"], deps):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    catalog, failure = await discover(deps, "read")
    if failure is not None:
        return {"reply": failure, "route": ""}
    payments = steps.reader_for(deps, catalog, "payments")
    if not payments.status.ok:
        return {"reply": prompts.NO_PAYMENTS_TOOL, "route": ""}
    outcome = await steps.read_invoices(deps, payments, state["query"])
    if outcome.reply is not None:
        return {"reply": outcome.reply, "route": ""}
    meta = render.ListMeta(livemode=outcome.livemode, dropped=outcome.dropped, no_due_date=outcome.no_due_date,
                           salvaged=outcome.salvaged, notes=_schedule_note(state["query"])
                           + ((prompts.STEP_LIMIT_NOTE,) if outcome.salvaged else ()))
    chosen, filtered = _filtered(outcome.invoices, request, deps)
    if chosen and steps.needs_customer(chosen):
        chosen = await steps.fill_customers(deps, payments, chosen)
    chosen, by_customer = _by_customer(chosen, request)
    meta = replace(meta, filtered=filtered + by_customer)
    if not chosen:
        return {"reply": "\n\n".join([prompts.nothing_overdue(request.min_days), *meta.notes]), "route": ""}
    scope.email = steps.reader_for(deps, catalog, "email")
    known = checks.addresses_in(payments.seen_text)
    return _to_reminders(scope, _entries(chosen), meta, known, request)


async def _off_topic(query: str, deps: TurnDeps) -> bool:
    verdict = await steps.ask_once(deps, ({"role": "system", "content": prompts.SCOPE_CHECK_SYSTEM},
                                          {"role": "user", "content": query}))
    return verdict.startswith(prompts.OFF_TOPIC)


async def _from_pasted(request: inv.Request, scope: _Scope) -> dict:
    """Rows the user pasted are the source; email is still read for reminders when it can be."""
    deps = scope.deps
    checked = checks.post_check_invoices(request.pasted, today=deps.today, seen_text=request.pasted_text, trusted=True)
    meta = render.ListMeta(source="pasted", dropped=checked.dropped, notes=_schedule_note(request.pasted_text))
    if not checked.invoices:
        return {"reply": prompts.nothing_overdue(request.min_days), "route": ""}
    catalog = None
    if deps.gateway is not None:
        catalog, _ = await discover(deps, "read")
    scope.email = steps.reader_for(deps, catalog, "email") if catalog else None
    known = checks.addresses_in(request.pasted_text)
    chosen, filtered = _filtered(checked.invoices, request, deps)
    meta = replace(meta, filtered=filtered)
    if not chosen:
        return {"reply": prompts.nothing_overdue(request.min_days), "route": ""}
    return _to_reminders(scope, _entries(chosen), meta, known, request)


def _to_reminders(scope: _Scope, entries: Sequence[inv.Entry], meta: render.ListMeta, known: Sequence[str],
                  request: inv.Request) -> dict:
    base = {"checked": inv.to_state(entries), "known": list(known)[:MAX_KNOWN_ADDRESSES], "json_mode": request.json_mode}
    email = scope.email
    if email is None or not email.status.ok:
        note = coverage.reminders_note(email.status) if email is not None else prompts.NO_EMAIL_NOTE
        return {**base, "turn_meta": replace(meta, notes=(*meta.notes, note)).to_dict(), "route": "ladder"}
    if scope.deps.elapsed() > REMINDERS_TIME_BUDGET_SECS:
        log.info("invoices ended after %.0fs; skipping the reminders step", scope.deps.elapsed())
        return {**base, "turn_meta": replace(meta, notes=(*meta.notes, prompts.REMINDERS_SKIPPED_NOTE)).to_dict(),
                "route": "ladder"}
    return {**base, "turn_meta": meta.to_dict(), "route": "reminders"}


async def _reminders(state: ChaserState, scope: _Scope) -> dict:
    entries = inv.from_state(state.get("checked"))
    meta = render.ListMeta.from_dict(state.get("turn_meta"))
    judged = await steps.read_reminders(scope.deps, scope.email, entries)
    if judged is None:
        status = scope.email.status
        note = coverage.reminders_note(status) if not status.ok else prompts.REMINDERS_UNCHECKED_NOTE
        return {"turn_meta": replace(meta, notes=(*meta.notes, note)).to_dict(), "route": "ladder"}
    seen = checks.addresses_in(scope.email.seen_text)
    known = list(dict.fromkeys([*(state.get("known") or ()), *seen]))[:MAX_KNOWN_ADDRESSES]
    return {"checked": inv.to_state(judged), "known": known,
            "turn_meta": replace(meta, reminders_ran=True).to_dict(), "route": "ladder"}


def _ladder(state: ChaserState, deps: TurnDeps) -> dict:
    view = render.View(today=deps.today, outcomes={})
    ranked = ladder.rank(inv.from_state(state.get("checked")), deps.today)
    meta = replace(render.ListMeta.from_dict(state.get("turn_meta")), capped=ranked.capped)
    entries = ranked.entries
    data = render.envelope(entries, meta, view, render.summary_line(entries))
    json_mode = bool(state.get("json_mode"))
    pick = None if json_mode else picker.question(entries, view)
    reply = render.as_json(data) if json_mode else render.render_list(entries, meta, view)
    return {"reply": reply, "items": inv.to_state(entries), "route": "", "outcomes": {}, "meta": meta.to_dict(),
            "envelope": data,
            "pick": {"text": pick.text, "hitl": pick.hitl, "metadata": pick.metadata} if pick else None}


def _filtered(found: Sequence[inv.Invoice], request: inv.Request, deps: TurnDeps) -> tuple[tuple[inv.Invoice, ...], int]:
    """Minimum days, minimum amount and envelope ids, applied in code."""
    def keep(i: inv.Invoice) -> bool:
        days_ok = ladder.days_overdue(i.due_day, deps.today) >= request.min_days
        amount_ok = request.min_amount is None or ladder.major_units(i.amount_due_minor, i.currency) >= request.min_amount
        ids_ok = not request.invoice_ids or i.id in request.invoice_ids or i.number in request.invoice_ids
        return days_ok and amount_ok and ids_ok
    kept = tuple(i for i in found if keep(i))
    return kept, len(found) - len(kept)


def _by_customer(found: Sequence[inv.Invoice], request: inv.Request) -> tuple[tuple[inv.Invoice, ...], int]:
    if not request.customer:
        return tuple(found), 0
    wanted = request.customer.casefold()
    kept = tuple(i for i in found if any(wanted in (v or "").casefold()
                                         for v in (i.customer_name, i.customer_email, i.customer_id)))
    return kept, len(found) - len(kept)


def _entries(found: Sequence[inv.Invoice]) -> tuple[inv.Entry, ...]:
    """Stable keys i1..in, oldest due date first."""
    ordered = sorted(found, key=lambda i: (i.due, i.id))
    return tuple(inv.Entry(invoice=i, key=f"i{n}") for n, i in enumerate(ordered, start=1))


def _schedule_note(query: str) -> tuple[str, ...]:
    return (prompts.NO_SCHEDULE,) if _SCHEDULE.search(query or "") else ()
