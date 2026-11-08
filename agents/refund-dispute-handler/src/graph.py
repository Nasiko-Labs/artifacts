"""The refund-dispute-handler LangGraph.

    START ─┬─ (a case is in this context) → followup ─┬─ answered ─────────────→ END
           │                                          ├─ REPLY → reply → END (approval walk)
           │                                          └─ NEEDS_FETCH ─┐
           └─ (no case / a new one) → parse ─┬─ pasted facts ──────────┼─────────────→ decide → END
                                             └─ discover → dispute? → payment → customer → mail → decide → END

`decide` is policy (code) + evidence (code) + the reply draft (one LLM call) + render (code). A message that
is only a greeting, or asks the agent to move money, never enters the graph.

State is checkpointed per A2A contextId (thread_id), so a follow-up sees the checked case, what was sent and
whether the user confirmed a refund. The graph is compiled per turn because its nodes close over that turn's
dependencies (LLM client, gateway, inbound traceparent); the checkpointer is shared. Questions and drafts
leave the graph in the turn result; the approve → send walk runs outside it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import case as cs
import checks
import coverage
import drafting
import evidence as ev
import facts as fx
import policy as pol
import prompts
import render
import smalltalk
import steps
from deps import TurnDeps
from discovery import RECOVER_META, classify, discover
from drafts import Draft, decision_question, read_decision
from fence import FencedGateway
from nasiko_hitl import question_metadata
from tool_health import ToolHealth

__all__ = ["CaseAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("refund_dispute.graph")

TURN_DEADLINE_SECS = 300
PICK_KEY = "refund_dispute_pick"
DECISION_KEY = "refund_dispute_decision"
OUT_OF_TIME = "I ran out of time before finishing this case. Try again, or name the charge id."
NO_DECISION_TEXT = "OK, no reply drafted. Say \"draft the reply\" whenever you want one."
ALREADY_SENT = "The reply for this case was already sent, so I won't draft it again."
CUSTOMER_HEADER = "Which customer is this about?"
DISPUTE_HEADER = "Which dispute do you mean?"


class CaseState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    policy: dict
    case: dict | None          # the checked case last shown (envelope + recipient), what follow-ups read
    confirmed: bool            # the user said they refunded it, for the case in context
    outcomes: dict             # draft key → sent / skipped / denied / blocked / not_sent
    request: dict              # this turn: the parsed request
    work: dict                 # this turn: records moving through the read steps
    question: dict | None      # this turn: a pick or the decision question
    drafts: list[dict]         # this turn: drafts for the approval walk
    envelope: dict | None


@dataclass(frozen=True)
class Question:
    text: str
    hitl: dict
    metadata: dict


@dataclass(frozen=True)
class TurnResult:
    reply: str
    drafts: tuple[Draft, ...] = ()
    question: Question | None = None
    envelope: dict | None = None


class _Scope:
    """One turn's read side (never persisted)."""

    def __init__(self, deps: TurnDeps):
        self.deps = deps
        self.reader: steps.Reader | None = None


class CaseAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, metadata: Mapping[str, Any] | None = None,
                   org_text: str = "") -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        if cs.asks_to_issue(query):  # moving money is never this agent's: answered in code
            return TurnResult(await self._never_refunds(context_id))
        query = cs.envelope_input(query) or query
        graph = _build_graph(_Scope(deps)).compile(checkpointer=self._checkpointer)
        request = cs.parse_request(query, metadata)
        fresh = {"query": query, "reply": "", "route": "", "request": request.to_dict(), "work": {},
                 "policy": cs.parse_policy(org_text).to_dict(), "question": None, "drafts": [], "envelope": None}
        try:
            final = await asyncio.wait_for(graph.ainvoke(fresh, _config(context_id), durability="exit"),
                                           TURN_DEADLINE_SECS)
        except TimeoutError:
            log.warning("turn passed its %ss deadline", TURN_DEADLINE_SECS)
            return TurnResult(OUT_OF_TIME)
        question = final.get("question")
        return TurnResult(final.get("reply") or "", tuple(Draft.from_dict(d) for d in final.get("drafts") or ()),
                          Question(**question) if question else None, final.get("envelope"))

    async def decided(self, answer: str, context_id: str, deps: TurnDeps) -> TurnResult:
        """The answer to "what do you want to tell the customer?": the reply for that decision."""
        values = await self._values(context_id)
        stored = values.get("case") or {}
        recommended = (stored.get("recommendation") or {}).get("decision") or "needs_human"
        decision, instruction = read_decision(answer, recommended)
        if decision is None or not stored:
            return TurnResult(NO_DECISION_TEXT)
        turn = await _reply(deps, stored, decision, instruction, bool(values.get("confirmed")),
                            dict(values.get("outcomes") or {}))
        return TurnResult(turn.reply, turn.drafts)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        if not outcomes:
            return
        values = await self._values(context_id)
        await self._remember(context_id, {"outcomes": {**dict(values.get("outcomes") or {}), **dict(outcomes)}})

    async def _never_refunds(self, context_id: str) -> str:
        stored = (await self._values(context_id)).get("case") or {}
        if not stored.get("summary"):
            return prompts.NEVER_REFUNDS_NOTE
        return f"{prompts.NEVER_REFUNDS_NOTE}\n\nThe recommendation for this case: {stored['summary']}."

    async def _remember(self, context_id: str, update: Mapping[str, Any]) -> None:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        await graph.aupdate_state(_config(context_id), dict(update), as_node="decide")

    async def _values(self, context_id: str) -> dict:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(scope: _Scope | None) -> StateGraph:
    def node(fn):
        async def run(state: CaseState) -> dict:
            return await fn(state, scope)
        return run

    graph = StateGraph(CaseState)
    for name, fn in (("followup", _followup), ("reply", _reply_node), ("parse", _parse), ("discover", _discover),
                     ("dispute", _dispute), ("payment", _payment), ("customer", _customer), ("mail", _mail),
                     ("decide", _decide)):
        graph.add_node(name, node(fn))
    graph.add_conditional_edges(START, _entry, ["followup", "parse"])
    graph.add_conditional_edges("followup", _route, ["parse", "reply", END])
    graph.add_conditional_edges("parse", _route, ["discover", "decide", END])
    graph.add_conditional_edges("discover", _route, ["dispute", "payment", END])
    graph.add_conditional_edges("dispute", _route, ["payment", END])
    graph.add_conditional_edges("payment", _route, ["customer", END])
    graph.add_edge("customer", "mail")
    graph.add_edge("mail", "decide")
    graph.add_edge("decide", END)
    graph.add_edge("reply", END)
    return graph


def _route(state: CaseState) -> str:
    return state.get("route") or END


def _entry(state: CaseState) -> str:
    stored = state.get("case") or {}
    if not stored or cs.pasted_facts(state["query"]):
        return "parse"
    request = cs.Request.from_dict(state.get("request"))
    known = (stored.get("summary") or "").casefold() + " " + str(stored.get("facts") or "").casefold()
    new_target = request.list_disputes or any(x.casefold() not in known for x in (*request.emails, *request.ids))
    return "parse" if new_target else "followup"


# --- follow-ups ---------------------------------------------------------------------------------------------

async def _followup(state: CaseState, scope: _Scope) -> dict:
    deps, query, stored = scope.deps, state["query"], state.get("case") or {}
    if cs.confirms_refund(query):
        return {"confirmed": True, "reply": prompts.CONFIRMED_NOTE, "route": ""}
    if cs.asks_to_draft(query):
        return {"route": "reply"}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_case(render.model_case(stored))}"
    text = await steps.ask_once(deps, ({"role": "system", "content": system},
                                       {"role": "user", "content": prompts.fence_request(query)}))
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.NEEDS_FETCH):
        return {"route": "parse", "case": None, "confirmed": False}
    if text.startswith(prompts.REPLY):
        return {"route": "reply"}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}


async def _reply_node(state: CaseState, scope: _Scope) -> dict:
    stored = state.get("case") or {}
    decision = (stored.get("recommendation") or {}).get("decision") or "needs_human"
    turn = await _reply(scope.deps, stored, decision, "", bool(state.get("confirmed")),
                        dict(state.get("outcomes") or {}))
    return {"reply": turn.reply, "drafts": [d.to_dict() for d in turn.drafts], "route": ""}


async def _reply(deps: TurnDeps, stored: Mapping, decision: str, instruction: str, confirmed: bool,
                 outcomes: Mapping[str, str]) -> drafting.ReplyTurn:
    key = stored.get("key") or "reply"
    if stored.get("case_type") == "dispute" or not stored.get("customer_email"):
        return drafting.ReplyTurn(prompts.NOTHING_TO_DRAFT)
    if outcomes.get(key) in drafting.FINAL_OUTCOMES:
        return drafting.ReplyTurn(ALREADY_SENT)
    if not deps.traceparent:
        return drafting.ReplyTurn(prompts.NO_TRACE_CONTEXT)
    rec = pol.Recommendation.from_dict(stored.get("recommendation"))
    f = fx.Facts.from_dict(stored.get("facts"))
    chosen = pol.alternative(rec, decision, f) if rec else None
    amount = chosen.amount_minor if chosen else None
    ready = stored.get("reply_draft") or {}
    if (not instruction and ready.get("decision") == decision and bool(ready.get("confirmed")) == confirmed
            and ready.get("body")):
        written = drafting.Written(ready.get("subject") or "", ready["body"], decision, confirmed)
    else:
        written = await drafting.write(deps, stored, decision, amount, instruction, confirmed)
    return await drafting.to_walk(deps, stored, written, key)


# --- a fresh case --------------------------------------------------------------------------------------------

async def _parse(state: CaseState, scope: _Scope) -> dict:
    deps = scope.deps
    pasted = cs.pasted_facts(state["query"])
    if pasted is None:
        return {"route": "discover"}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    charge, file = steps.pasted_case(pasted, deps.now)
    if charge is None:
        return {"reply": prompts.NO_PAYMENTS_TOOL, "route": ""}
    work = {"charge": charge.model_dump(), "file": file.model_dump(), "recent": [], "messages": [],
            "customer_ran": False, "mail_ran": False, "pasted": True, "notes": [prompts.PASTED_NOTE]}
    return {"work": work, "route": "decide"}


async def _discover(state: CaseState, scope: _Scope) -> dict:
    deps = scope.deps
    catalog, failure = await discover(deps, "read")
    if failure is not None:
        return {"reply": _refusal(failure, state["query"]), "route": ""}
    tools = catalog.read_tools()
    log.info("read tools offered: %s", [t.name for t in tools])
    fenced = FencedGateway(deps.gateway)
    scope.reader = steps.Reader(tools=tools, fenced=fenced, health=ToolHealth(fenced), source_of=classify,
                                helpers=frozenset({RECOVER_META}))
    if not scope.reader.tools_for("payments"):
        return {"reply": _refusal(prompts.NO_PAYMENTS_TOOL, state["query"]), "route": ""}
    request = cs.Request.from_dict(state.get("request"))
    work = {"notes": [], "recent": [], "messages": []}
    return {"work": work, "route": "dispute" if request.wants_dispute or request.list_disputes else "payment"}


def _refusal(reply: str, query: str) -> str:
    """A refusal that ends the turn before any model call still declines an off-topic message as such."""
    unreadable = reply.startswith((prompts.NO_PAYMENTS_TOOL, prompts.GATEWAY_UNREACHABLE))
    return smalltalk.OFF_TOPIC_REPLY if unreadable and not cs.mentions_domain(query) else reply


async def _dispute(state: CaseState, scope: _Scope) -> dict:
    deps, query = scope.deps, state["query"]
    request = cs.Request.from_dict(state.get("request"))
    wanted = request.dispute_ids
    found = await steps.disputes(deps, scope.reader, query, wanted, request.list_disputes or not wanted)
    if found is None:
        return {"reply": prompts.UNREADABLE_ANSWER, "route": ""}
    open_ones = tuple(d for d in found if d.status in fx.OPEN_DISPUTE_STATUSES or not d.status)
    if request.list_disputes:
        return {"reply": render.render_dispute_list(open_ones, deps.now) if open_ones else prompts.NO_OPEN_DISPUTES,
                "route": ""}
    chosen = [d for d in found if d.id in wanted] if wanted else list(open_ones)
    if not chosen:
        return {"reply": prompts.NO_OPEN_DISPUTES if not wanted else prompts.CASE_NOT_FOUND, "route": ""}
    if len(chosen) > 1:
        options = {f"{d.id} {cs.money(d.amount_minor, d.currency)} {ev.reason_of(d)}"[:190]: f"dispute {d.id}"
                   for d in chosen[:10]}
        return {"question": _pick(DISPUTE_HEADER, query, options), "route": ""}
    work = {**dict(state.get("work") or {}), "dispute": chosen[0].model_dump(), "dispute_ran": True}
    return {"work": work, "route": "payment"}


async def _payment(state: CaseState, scope: _Scope) -> dict:
    deps, query = scope.deps, state["query"]
    work = dict(state.get("work") or {})
    request = cs.Request.from_dict(state.get("request"))
    dispute = cs.Dispute.model_validate(work["dispute"]) if work.get("dispute") else None
    outcome = await steps.payment(deps, scope.reader, query, request, dispute.charge_id if dispute else None)
    if outcome.reply == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if outcome.reply is not None:
        return {"reply": _refusal(outcome.reply, query), "route": ""}
    if outcome.charge is None and dispute is None:
        if outcome.candidates:
            options = {f"{c.name or c.email} <{c.email}>"[:190]: f"customer {c.email}" for c in outcome.candidates}
            return {"question": _pick(CUSTOMER_HEADER, query, options), "route": ""}
        return {"reply": prompts.CASE_NOT_FOUND, "route": ""}
    notes = list(work.get("notes") or []) + ([prompts.STEP_LIMIT_NOTE] if outcome.salvaged else [])
    candidate = outcome.candidates[0] if len(outcome.candidates) == 1 else None
    work = {**work, "charge": outcome.charge.model_dump() if outcome.charge else None,
            "recent": [c.model_dump() for c in outcome.recent], "notes": notes,
            "candidate_email": candidate.email if candidate else None}
    return {"work": work, "route": "customer"}


async def _customer(state: CaseState, scope: _Scope) -> dict:
    deps, query = scope.deps, state["query"]
    work = dict(state.get("work") or {})
    request = cs.Request.from_dict(state.get("request"))
    charge = cs.Charge.model_validate(work["charge"]) if work.get("charge") else None
    email = (request.emails[0] if request.emails else None) or work.get("candidate_email")
    customer_id = charge.customer_id if charge else None
    if not customer_id and not email:
        return {"work": {**work, "file": cs.CustomerFile().model_dump(), "customer_ran": False,
                         "notes": [*work.get("notes", []), "Customer history not checked: no customer on the charge."]}}
    file = await steps.customer(deps, scope.reader, query, customer_id, email)
    if file is None:
        note = "Customer history not checked: the customer lookup did not finish."
        return {"work": {**work, "file": cs.CustomerFile().model_dump(), "customer_ran": False,
                         "notes": [*work.get("notes", []), note]}}
    return {"work": {**work, "file": file.model_dump(), "customer_ran": True}}


async def _mail(state: CaseState, scope: _Scope) -> dict:
    deps, reader, query = scope.deps, scope.reader, state["query"]
    work = dict(state.get("work") or {})
    file = cs.CustomerFile.model_validate(work.get("file") or {})
    request = cs.Request.from_dict(state.get("request"))
    email = (file.customer.email if file.customer else None) or (request.emails[0] if request.emails else None)
    note = None
    if not email:
        note = "Email history not checked: no customer address."
    elif not reader.tools_for("email"):
        note = prompts.NO_MAIL_READ_NOTE
    elif deps.elapsed() > steps.MAIL_TIME_BUDGET_SECS:
        log.info("read steps took %.0fs; skipping the mail step", deps.elapsed())
        note = prompts.MAIL_SKIPPED_NOTE
    found = None if note else await steps.mail(deps, reader, query, email)
    if found is None:
        status = reader.status("email")
        note = note or (f"Email history not checked. {coverage.line(status)}" if not status.ok
                        else "Email history not checked: the email search did not finish.")
        return {"work": {**work, "mail_ran": False, "notes": [*work.get("notes", []), note]}}
    return {"work": {**work, "messages": [m.model_dump() for m in found], "mail_ran": True}}


# --- decide: policy, evidence, reply draft, render ----------------------------------------------------------

async def _decide(state: CaseState, scope: _Scope) -> dict:
    deps = scope.deps
    work = dict(state.get("work") or {})
    request = cs.Request.from_dict(state.get("request"))
    records = _records(work)
    charge, file, recent, dispute, messages = records
    policy = cs.Policy.from_dict(state.get("policy"))
    facts = fx.compute(charge, file, recent, dispute, messages, deps.now, policy.window_days)
    test_mode = scope.reader is not None and checks.livemode(scope.reader.fenced.seen_text) is False
    view = _view(records, facts, policy, work, test_mode, deps)
    stored_case = {**render.envelope(view), "customer_email": view.customer_email, "customer_name": view.customer_name,
                   "thread_ref": _thread(messages), "key": f"reply:{facts.charge_id or 'case'}"}
    written = None
    if view.case_type == "refund_request" and view.customer_email and deps.traceparent:
        rec = view.recommendation
        written = await drafting.write(deps, stored_case, rec.decision, rec.amount_minor, "", False)
    if written is not None and written.problem is None:
        view = _with_draft(view, written.to_dict())
        stored_case = {**stored_case, "reply_draft": written.to_dict()}
    env = render.envelope(view)
    base = {"case": stored_case, "confirmed": False, "outcomes": {}, "envelope": env, "work": {}, "route": ""}
    if request.json_mode:
        return {**base, "reply": render.json_block(env), "question": None}
    question = None
    if view.case_type == "refund_request" and view.customer_email:
        rec = view.recommendation
        text, hitl = decision_question(rec.decision, f"{rec.decision}{render.amount_text(rec, facts.currency)}")
        question = {"text": text, "hitl": hitl, "metadata": {DECISION_KEY: {"recommended": rec.decision}}}
    return {**base, "reply": render.render_case(view), "question": question}


def _records(work: Mapping) -> tuple:
    charge = cs.Charge.model_validate(work["charge"]) if work.get("charge") else None
    file = cs.CustomerFile.model_validate(work.get("file") or {})
    recent = tuple(cs.Charge.model_validate(c) for c in work.get("recent") or ())
    dispute = cs.Dispute.model_validate(work["dispute"]) if work.get("dispute") else None
    messages = tuple(cs.Msg.model_validate(m) for m in work.get("messages") or ())
    return charge, file, recent, dispute, messages


def _view(records: tuple, facts: fx.Facts, policy: cs.Policy, work: Mapping, test_mode: bool,
          deps: TurnDeps) -> render.CaseView:
    charge, file, recent, dispute, messages = records
    if dispute is not None:
        when = ev.deadline(dispute, deps.now)
        items = ev.checklist(dispute, charge, file, messages, policy)
        rec = ev.recommend_dispute(dispute, charge, file, messages, items, when)
        packet = ev.packet(dispute, items, when)
        case_type = "dispute"
    else:
        when, packet, case_type = None, None, "refund_request"
        rec = pol.recommend(facts, policy)
        items = ev.refund_items(charge, file, recent, messages, facts)
    customer = file.customer
    confidence = _confidence(charge, work, rec, dispute)
    needs_human = rec.needs_human or rec.decision == "needs_human" or bool(when and when.urgent)
    return render.CaseView(case_type=case_type, facts=facts, recommendation=rec, evidence=items, policy=policy,
                           customer_email=customer.email if customer and customer.email else None,
                           customer_name=customer.name if customer else None,
                           charge_link=charge.link if charge else None, dispute=dispute, deadline=when, packet=packet,
                           notes=tuple(work.get("notes") or ()), test_mode=test_mode, confidence=confidence,
                           needs_human=needs_human)


def _confidence(charge: cs.Charge | None, work: Mapping, rec: pol.Recommendation, dispute: cs.Dispute | None) -> str:
    if charge is None:
        return "low"
    if work.get("pasted") or not work.get("mail_ran") or not work.get("customer_ran"):
        return "medium"
    if dispute is not None and not work.get("dispute_ran"):
        return "medium"
    return "medium" if rec.rule == "insufficient_data" else "high"


def _with_draft(view: render.CaseView, draft: dict) -> render.CaseView:
    return replace(view, reply_draft=draft)


def _thread(messages: tuple[cs.Msg, ...]) -> str | None:
    inbound = next((m for m in messages if m.direction == "in" and m.thread_ref), None)
    return inbound.thread_ref if inbound else None


def _pick(header: str, query: str, options: Mapping[str, str]) -> dict:
    hitl = question_metadata(header, [(label, "") for label in options], allow_custom_input=True)
    text = f"{header} Pick one or type the email address or id."
    return {"text": text, "hitl": hitl, "metadata": {PICK_KEY: {"query": query, "options": dict(options)}}}
