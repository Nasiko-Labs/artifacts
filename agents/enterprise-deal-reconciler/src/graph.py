"""The enterprise-deal-reconciler LangGraph.

    START ─┬─ (a result in this context, no new input) → followup ─┬─ answered → END
           │                                                       └─ NEEDS_FETCH ─┐
           ├─ (a pause answer: document / pasted terms) ───────────────────────────→ contract
           ├─ (a pause answer: customer) ──────────────────────────────────────────→ billing
           └─ (otherwise) ─────────────────────────────────────────────────────────→ contract
    contract (parse → discover → find + read → extract per chunk, checked in code) ─┬─ reply / pause → END
                                                                                    └─ billing
    billing (read or pasted → select → compare in code → render) → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId (thread_id), so a follow-up sees the last result. The graph is
compiled per turn because its nodes close over that turn's dependencies (LLM client, gateway, inbound
traceparent); the checkpointer is shared.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import billing
import compare
import coverage
import picker
import prompts
import render
import smalltalk
import steps
import terms as t
from deps import TurnDeps
from discovery import Catalog, discover
from request import Request, parse, wants_json

__all__ = ["DealAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("deal_reconciler.graph")

BILLING_TIME_BUDGET_SECS = 240  # the contract step must end by then for the billing step to run
# Words that make a message plainly about this job; anything else gets one tool-less scope check first,
# so an off-topic message is declined even when no tool is connected.
_DOMAIN = re.compile(r"contract|order form|agreement|\bmsa\b|\bsow\b|deal|reconcil|billing|subscription|invoice|"
                     r"seat|licen[cs]e|discount|coupon|price|pricing|renew|payment terms|\bnet \d|customer|quote",
                     re.IGNORECASE)
_SCHEDULE = re.compile(r"\b(?:daily|every (?:day|morning|week)|weekly|on a schedule|automatically|monitor|watch)\b",
                       re.IGNORECASE)
_WRITE_ASK = re.compile(r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:set|change|update|apply|add|remove|fix|"
                        r"create|cancel|make|correct|adjust)\b", re.IGNORECASE)


class DealState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    resume: dict | None        # this turn: the answered pause {kind, request, options, answer, ...}
    request: dict              # this turn: the parsed request
    terms: dict                # this turn: the checked contract terms
    meta: dict                 # this turn: render.RunMeta
    pause: dict | None         # this turn: the question to pause on
    envelope: dict | None      # this turn: the structured envelope
    json_mode: bool
    result: dict | None        # kept: the last envelope (what follow-ups answer from)
    result_text: str           # kept: the last rendered result
    last_request: dict         # kept: the request behind the last result ("check it again")
    scoped: bool               # this turn: the follow-up call already judged the message in scope


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: picker.Pause | None = None
    envelope: dict | None = None


class _Scope:
    """One turn's read side (never persisted)."""

    def __init__(self, deps: TurnDeps):
        self.deps = deps
        self.catalog: Catalog | None = None


class DealAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        return await self._run(query, None, context_id, deps)

    async def resume(self, pause: Mapping[str, Any], answer: str, context_id: str, deps: TurnDeps) -> TurnResult:
        """The answer to one of this agent's own questions (picker.py)."""
        if picker.is_cancel(answer):
            return TurnResult(picker.CANCELLED)
        return await self._run(answer, {**dict(pause), "answer": answer}, context_id, deps)

    async def _run(self, query: str, resume: dict | None, context_id: str, deps: TurnDeps) -> TurnResult:
        graph = _build_graph(_Scope(deps)).compile(checkpointer=self._checkpointer)
        request = Request.from_state(resume.get("request")) if resume else None
        fresh = {"query": query, "reply": "", "route": "", "resume": resume, "scoped": False, "request": {}, "terms": {}, "meta": {},
                 "pause": None, "envelope": None,
                 "json_mode": request.json_mode if request else wants_json(query)}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pause = final.get("pause")
        return TurnResult(final.get("reply") or "", picker.Pause(**pause) if pause else None, final.get("envelope"))


def _build_graph(scope: _Scope) -> StateGraph:
    async def followup(state: DealState) -> dict:
        return await _followup(state, scope.deps)

    async def contract(state: DealState) -> dict:
        return await _contract(state, scope)

    async def bill(state: DealState) -> dict:
        return await _billing(state, scope)

    graph = StateGraph(DealState)
    for name, node in (("followup", followup), ("contract", contract), ("billing", bill)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "contract", "billing"])
    graph.add_conditional_edges("followup", _route, ["contract", END])
    graph.add_conditional_edges("contract", _route, ["billing", END])
    graph.add_edge("billing", END)
    return graph


def _entry(state: DealState) -> str:
    resume = state.get("resume")
    if resume:
        return "billing" if resume.get("kind") == "customer" else "contract"
    request = parse(state["query"])
    new_input = request.contract_text or request.billing_fields or request.envelope
    return "followup" if state.get("result") and not new_input else "contract"


def _route(state: DealState) -> str:
    return state.get("route") or END


def _reply(text: str) -> dict:
    return {"reply": text, "route": ""}


def _pause(p: picker.Pause) -> dict:
    return {"reply": "", "route": "", "pause": asdict(p)}


# --- follow-up ------------------------------------------------------------------------------------

async def _followup(state: DealState, deps: TurnDeps) -> dict:
    result = state.get("result") or {}
    if _WRITE_ASK.search(state["query"]):  # "set the quantity to 250": never done, the fix is shown instead
        return _reply(_read_only(result))
    if not deps.traceparent:
        return _reply(prompts.NO_TRACE_CONTEXT)
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(state.get('result_text') or '')}"
    text = await steps.ask_once(deps, ({"role": "system", "content": system},
                                       {"role": "user", "content": state["query"]}))
    if text.startswith(prompts.OFF_TOPIC):
        return _reply(smalltalk.OFF_TOPIC_REPLY)
    if text.startswith(prompts.NEEDS_FETCH):
        return {"route": "contract", "scoped": True}
    return _reply(text or prompts.UNREADABLE_ANSWER)


def _read_only(result: Mapping[str, Any]) -> str:
    lines = render.fixes(result.get("mismatches") or ())
    return "\n".join([prompts.READ_ONLY_REPLY, *lines]) if lines else f"{prompts.READ_ONLY_REPLY} {prompts.NO_FIX_KNOWN}"


# --- contract -------------------------------------------------------------------------------------

async def _contract(state: DealState, scope: _Scope) -> dict:
    deps = scope.deps
    resume = state.get("resume") or {}
    request, document_id = _request_for(state, resume)
    if not deps.traceparent:
        return _reply(prompts.NO_TRACE_CONTEXT)
    if not resume:
        early = await _gatekeep(state["query"], request, deps, bool(state.get("scoped")))
        if early is not None:
            return early
    needs_docs, needs_billing = request.contract_text is None, not request.billing_fields
    if needs_docs or needs_billing:
        scope.catalog, failure = await discover(deps)
        if failure is not None:
            return _reply(failure)
        if needs_billing and not scope.catalog.for_source("payments"):
            return _reply(prompts.NO_PAYMENTS_TOOL)
    if needs_docs:
        return await _read_document(state, scope, request, document_id)
    meta = render.RunMeta(contract_source="pasted", notes=_schedule_note(state["query"]))
    return await _extracted(deps, request, request.contract_text, meta)


def _request_for(state: DealState, resume: Mapping[str, Any]) -> tuple[Request, str | None]:
    if not resume:
        parsed = parse(state["query"])
        last = state.get("last_request")
        if not parsed.has_target and last and state.get("result"):  # "check it again": the same deal
            return replace(Request.from_state(last), json_mode=parsed.json_mode), None
        return parsed, None
    request = Request.from_state(resume.get("request"))
    answer = str(resume.get("answer") or "")
    if resume.get("kind") == "paste":
        return replace(request, contract_text=answer.strip()), None
    chosen, typed = picker.read_choice(answer, resume.get("options") or {})
    return (request, chosen) if chosen else (replace(request, document=typed or request.document), None)


async def _gatekeep(query: str, request: Request, deps: TurnDeps, scoped: bool) -> dict | None:
    """Write asks, off-topic messages and requests that name nothing, before any tool call."""
    if _WRITE_ASK.search(query) and not request.contract_text and not request.billing_fields:
        return _reply(_read_only({}))
    if not scoped and not _DOMAIN.search(query) and not request.contract_text and not request.billing_fields:
        verdict = await steps.ask_once(deps, ({"role": "system", "content": prompts.SCOPE_CHECK_SYSTEM},
                                              {"role": "user", "content": query}))
        if verdict.startswith(prompts.OFF_TOPIC):
            return _reply(smalltalk.OFF_TOPIC_REPLY)
    if not request.has_target:
        return _reply(prompts.NEED_CUSTOMER)
    return None


async def _read_document(state: DealState, scope: _Scope, request: Request, document_id: str | None) -> dict:
    deps = scope.deps
    reader = steps.reader_for(deps, scope.catalog, "documents")
    if not reader.status.ok:
        return _pause(picker.paste_question(prompts.NO_DOC_TOOL_ASK_PASTE, request.to_state(), prompts.PASTE_HEADER))
    read = await steps.read_contract(deps, reader, request, document_id)
    if read.status == "unavailable":
        text = f"{coverage.documents_note(reader.status)}\n\n{prompts.NO_DOC_TOOL_ASK_PASTE}"
        return _pause(picker.paste_question(text, request.to_state(), prompts.PASTE_HEADER))
    if read.status == "multiple":
        return _pause(picker.document_question(read.candidates, request.to_state()))
    if read.status == "not_text":
        return _pause(picker.paste_question(prompts.NOT_TEXT_ASK_PASTE, request.to_state(), prompts.PASTE_HEADER))
    fixed = {"paused": prompts.READ_NEEDS_APPROVAL, "off_topic": smalltalk.OFF_TOPIC_REPLY,
             "not_found": prompts.CONTRACT_NOT_FOUND, "unreadable": prompts.UNREADABLE_ANSWER}
    if read.status in fixed:
        return _reply(fixed[read.status])
    notes = (*_schedule_note(state["query"]), *((prompts.STEP_LIMIT_NOTE,) if read.salvaged else ()))
    meta = render.RunMeta(contract_title=read.title, contract_link=read.link, salvaged=read.salvaged, notes=notes)
    return await _extracted(deps, request, read.text, meta)


async def _extracted(deps: TurnDeps, request: Request, text: str, meta: render.RunMeta) -> dict:
    found = await steps.extract(deps, text)
    checked = found.terms
    if not checked.terms and not checked.ambiguous:
        return _reply(prompts.NO_TERMS_FOUND)
    notes = meta.notes
    if not found.complete:
        notes = (*notes, f"Contract read in part: {found.read_chars:,} of {found.total_chars:,} characters"
                         + (f", {found.failed_chunks} part(s) unreadable" if found.failed_chunks else "")
                         + "; terms in the rest were not checked.")
    customer = request.customer or (checked.get("customer").value if checked.get("customer") else None)
    meta = replace(meta, customer=customer, contract_complete=found.complete, notes=notes)
    return {"request": request.to_state(), "terms": t.to_state(checked), "meta": _meta_state(meta),
            "route": "billing"}


# --- billing + comparison -------------------------------------------------------------------------

async def _billing(state: DealState, scope: _Scope) -> dict:
    deps = scope.deps
    resume = state.get("resume") or {}
    customer_id = None
    if resume.get("kind") == "customer":
        request = Request.from_state(resume.get("request"))
        checked = t.from_state(resume.get("terms")) or t.ContractTerms({})
        meta = _meta_of(resume.get("meta"))
        customer_id, typed = picker.read_choice(str(resume.get("answer") or ""), resume.get("options") or {})
        request = request if customer_id else replace(request, customer=typed or request.customer, email=None)
        if not deps.traceparent:
            return _reply(prompts.NO_TRACE_CONTEXT)
    else:
        request = Request.from_state(state.get("request"))
        checked = t.from_state(state.get("terms")) or t.ContractTerms({})
        meta = _meta_of(state.get("meta"))
    json_mode = bool(state.get("json_mode"))
    if request.billing_fields:
        config = billing.from_pasted(request.billing_fields)
        if config is None:
            return _reply(prompts.NO_PAYMENTS_TOOL)
        return _report(request, checked, config, replace(meta, billing_source="pasted"), deps, json_mode)
    if deps.elapsed() > BILLING_TIME_BUDGET_SECS:
        log.info("contract step ended after %.0fs; skipping billing", deps.elapsed())
        return _report(request, checked, None, replace(meta, notes=(*meta.notes, prompts.BILLING_SKIPPED_NOTE)), deps,
                       json_mode)
    return await _read_billing(scope, request, checked, meta, customer_id, json_mode)


async def _read_billing(scope: _Scope, request: Request, checked: t.ContractTerms, meta: render.RunMeta,
                        customer_id: str | None, json_mode: bool) -> dict:
    deps = scope.deps
    if scope.catalog is None:
        scope.catalog, failure = await discover(deps)
        if failure is not None:
            return _reply(failure)
    reader = steps.reader_for(deps, scope.catalog, "payments")
    if not reader.status.ok:
        return _reply(coverage.payments_failed_reply(reader.status) if reader.tools else prompts.NO_PAYMENTS_TOOL)
    read = await steps.read_billing(deps, reader, request, checked, customer_id)
    if read.status == "unavailable":
        return _reply(coverage.payments_failed_reply(reader.status))
    if read.status == "multiple_customers":
        return _pause(picker.customer_question(read.candidates, request.to_state(), t.to_state(checked),
                                               _meta_state(meta)))
    fixed = {"paused": prompts.READ_NEEDS_APPROVAL, "off_topic": smalltalk.OFF_TOPIC_REPLY,
             "customer_not_found": prompts.CUSTOMER_NOT_FOUND, "unreadable": prompts.UNREADABLE_ANSWER}
    if read.status in fixed:
        return _reply(fixed[read.status])
    config = read.config
    notes = (*meta.notes, *((prompts.STEP_LIMIT_NOTE,) if read.salvaged and not meta.salvaged else ()))
    meta = replace(meta, customer=meta.customer or config.customer_name, billing_source="tool",
                   livemode=config.livemode, billing_dropped=config.dropped, salvaged=meta.salvaged or read.salvaged,
                   notes=notes)
    return _report(request, checked, config, meta, deps, json_mode)


def _report(request: Request, checked: t.ContractTerms, config: billing.BillingConfig | None, meta: render.RunMeta,
            deps: TurnDeps, json_mode: bool) -> dict:
    report = compare.reconcile(checked, config, deps.today, meta.customer) if config is not None else None
    data = render.envelope(report, checked, meta)
    text = render.render(report, checked, meta)
    return {"reply": render.as_json(data) if json_mode else text, "route": "", "envelope": data, "result": data,
            "result_text": text, "last_request": request.to_state()}


def _meta_state(meta: render.RunMeta) -> dict:
    return {**asdict(meta), "notes": list(meta.notes)}


def _meta_of(data: Mapping[str, Any] | None) -> render.RunMeta:
    d = dict(data or {})
    known = {k: d[k] for k in render.RunMeta.__dataclass_fields__ if k in d}
    known["notes"] = tuple(str(n) for n in known.get("notes") or ())
    if "billing_dropped" in known:
        known["billing_dropped"] = int(known["billing_dropped"] or 0)
    return render.RunMeta(**known)


def _schedule_note(query: str) -> tuple[str, ...]:
    return (prompts.NO_SCHEDULE,) if _SCHEDULE.search(query or "") else ()
