"""The revenue-anomaly-explainer LangGraph.

    START -+- (a check in this context) -> followup -+- answered / OFF_TOPIC ----------------------------> END
           |                                         +- POST -> post -+- channel named: approval walk -> END
           |                                         |                +- no channel: channel pick ------> END
           |                                         +- NEEDS_FETCH / a day or baseline named -+
           +- (no check yet, or pasted rows) ----------------------------------------------> check -> END (| post)

check = parse (code) -> discover -> subscriptions (paged) -> events (plan changes + failed payments; skipped after
270 s) -> series (code) -> detect (code) -> accounts (code) + customer names -> headline (1 call + checks) -> post
(code) -> render (code). Pasted daily rows skip the gateway. A greeting never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId, so a follow-up and a post use the check already shown. The graph is
compiled per turn because its nodes close over that turn's dependencies; the checkpointer is shared. The post leaves
the graph in the turn result; the approve -> post walk runs outside it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import accounts
import detect
import pasted
import picker
import post
import prompts
import render
import request as requests
import sender
import series as serieses
import smalltalk
import steps
import walk
from deps import EVENTS_CUTOFF_SECS, TURN_DEADLINE_SECS, TurnDeps
from discovery import discover
from report import Flag, Report, day_check

__all__ = ["AnomalyAgent", "TurnDeps", "outcome_of"]

log = logging.getLogger("revenue_anomaly.graph")

OUT_OF_TIME = "I ran out of time before finishing this check. Try fewer days or a shorter baseline."


class AnomalyState(TypedDict, total=False):
    query: str
    request: dict
    report: dict             # report.Report of the check last shown
    channel: str             # the channel label picked earlier in this conversation
    posted: list[str]        # channels this check was posted to
    route: str
    reply: str
    envelope: dict | None    # this turn: the envelope (completed check turns)
    pause: dict | None       # this turn: the channel question
    walk: dict | None        # this turn: the post to put on the approval card


class AnomalyAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps,
                   metadata: Mapping[str, Any] | None = None) -> walk.Outcome:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return walk.Outcome("complete", canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        req = requests.parse(query, deps.today, metadata)
        fresh = {"query": query, "request": req.to_dict(), "route": "", "reply": "", "envelope": None, "pause": None,
                 "walk": None}
        try:  # one checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call
            final = await asyncio.wait_for(graph.ainvoke(fresh, _config(context_id), durability="exit"),
                                           TURN_DEADLINE_SECS)
        except TimeoutError:
            log.warning("turn passed its %ss deadline", TURN_DEADLINE_SECS)
            return walk.Outcome("complete", OUT_OF_TIME)
        return outcome_of(final)

    async def picked_channel(self, target: picker.Target, context_id: str, deps: TurnDeps) -> walk.Outcome:
        """The channel-pick answer: remember the channel and put the post on the approval card."""
        values = await self._values(context_id)
        if not values.get("report"):
            return walk.Outcome("complete", prompts.NO_POST_YET)
        await self._remember(context_id, {"channel": target.label})
        return outcome_of(await plan_post(deps, Report.from_dict(values["report"]), target))

    async def record_post(self, context_id: str, channel: str) -> None:
        values = await self._values(context_id)
        await self._remember(context_id, {"posted": [*(values.get("posted") or []), channel], "channel": channel})

    async def _remember(self, context_id: str, update: Mapping[str, Any]) -> None:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        await graph.aupdate_state(_config(context_id), dict(update), as_node="check")

    async def _values(self, context_id: str) -> dict:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def outcome_of(final: Mapping[str, Any]) -> walk.Outcome:
    reply = final.get("reply") or ""
    planned = final.get("walk")
    if planned:
        post_ = sender.Post.from_dict(planned.get("post"))
        if post_ is not None:
            return walk.start(post_, planned.get("allowed") or (), reply)
    pause = final.get("pause")
    if pause:
        text = "\n\n".join(p for p in (reply, pause["text"]) if p)
        return walk.Outcome("input_required", text, pause["metadata"], pause["hitl"])
    return walk.Outcome("complete", reply or prompts.NO_RESULT_YET, data=final.get("envelope"))


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    async def followup(state: AnomalyState) -> dict:
        return await _followup(state, deps)

    async def check(state: AnomalyState) -> dict:
        return await _check(state, deps)

    async def post_node(state: AnomalyState) -> dict:
        return await _post(state, deps)

    graph = StateGraph(AnomalyState)
    for name, node in (("followup", followup), ("check", check), ("post", post_node)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "check"])
    graph.add_conditional_edges("followup", _route, ["check", "post", END])
    graph.add_conditional_edges("check", _route, ["post", END])
    graph.add_edge("post", END)
    return graph


def _entry(state: AnomalyState) -> str:
    return "followup" if state.get("report") and not pasted.find(state["query"]) else "check"


def _route(state: AnomalyState) -> str:
    return state.get("route") or END


def _request(state: Mapping[str, Any]) -> requests.Request:
    return requests.Request.from_dict(dict(state.get("request") or {}))


# --- follow-ups -------------------------------------------------------------------------------------------------

async def _followup(state: AnomalyState, deps: TurnDeps) -> dict:
    query, req = state["query"], _request(state)
    if req.names_days or req.names_baseline:  # a new check, parsed in code: no LLM call
        return {"route": "check"}
    if req.wants_post or req.channel:
        return {"route": "post"}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    report = Report.from_dict(state["report"])
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(_prior(report))}"
    text = await steps.ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.NEEDS_FETCH):
        return {"route": "check"}
    if text.startswith(prompts.POST):
        return {"route": "post"}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}


def _prior(report: Report) -> str:
    return f"{render.markdown(report, post_hint=False)}\n\nDraft chat post:\n{report.post}"


# --- the check ----------------------------------------------------------------------------------------------------

async def _check(state: AnomalyState, deps: TurnDeps) -> dict:
    req = _request(state)
    previous = state.get("report")
    if previous and not req.names_baseline:  # a follow-up check keeps the baseline already in use
        req = replace(req, baseline_days=int(previous.get("baseline_days") or req.baseline_days))
    rows = pasted.find(state["query"])
    if rows is not None:
        report = _pasted_report(rows, req)
    else:
        built = await _from_payments(state["query"], req, deps)
        if isinstance(built, str):
            return {"reply": built, "route": ""}
        report = built
    return await _finish(state, deps, replace(report, channel=state.get("channel") or ""), req)


async def _from_payments(query: str, req: requests.Request, deps: TurnDeps) -> Report | str:
    """The checked report, or the fixed reply that ends the turn."""
    catalog, failure = await discover(deps, "read")
    if failure is not None:
        return _refusal(failure, query)
    reader = steps.reader_for(deps, catalog)
    first = min(req.days) - timedelta(days=req.baseline_days)
    since = serieses.midnight(first)
    found = await steps.fetch_subscriptions(deps, reader, query, since)
    if found.reply is not None:
        return smalltalk.OFF_TOPIC_REPLY if found.reply == prompts.OFF_TOPIC else _refusal(found.reply, query)
    window_start = deps.now - timedelta(days=prompts.EVENTS_WINDOW_DAYS)
    notes: tuple[str, ...] = ()
    if deps.elapsed() > EVENTS_CUTOFF_SECS:
        log.info("subscriptions ended after %.0fs; skipping events", deps.elapsed())
        events, notes = steps.EventsOutcome(), (prompts.EVENTS_SKIPPED_NOTE,)
    else:
        events = await steps.fetch_events(deps, reader, found.subs, max(since, window_start))
        notes = () if events.read else (prompts.EVENTS_UNREAD_NOTE,)
    built = serieses.build(found.subs, events.changes, events.failures, first, max(req.days),
                           changes_known=events.read)
    checks_, flags_needed = _verdicts(built, req)
    names = await steps.fetch_customers(deps, reader, accounts.ids_to_name(
        [a for v in flags_needed for a in v.anomalies], _days_by_key(built), events.failures))
    day_checks = tuple(day_check(v, tuple(Flag(a, accounts.behind(a, v.metrics, found.subs, events.failures, names))
                                          for a in v.anomalies)) for v in checks_)
    reconciled = all(serieses.reconciles(s.days) for s in built)
    approximate = events.read and since < window_start
    notes = (*notes, *((prompts.APPROXIMATE_NOTE,) if approximate else ()),
             *(() if reconciled else (prompts.RECONCILE_NOTE,)),
             *((prompts.STEP_LIMIT_NOTE,) if found.pages_left_out else ()),
             *((prompts.dropped_note(found.dropped),) if found.dropped else ()))
    return Report(checks=day_checks, baseline_days=req.baseline_days, livemode=found.livemode,
                  changes_known=events.read, pages_left_out=found.pages_left_out,
                  unsupported_items=sum(s.unsupported_items for s in built), dropped=found.dropped,
                  reconciled=reconciled, approximate=approximate, notes=notes)


def _verdicts(built, req: requests.Request) -> tuple[tuple[detect.Verdict, ...], tuple[detect.Verdict, ...]]:
    verdicts = tuple(v for s in built for d in req.days
                     if (v := detect.check_day(s, d, req.baseline_days)) is not None)
    return verdicts, tuple(v for v in verdicts if v.anomalies)


def _days_by_key(built) -> dict[tuple[str, str], serieses.DayMetrics]:
    return {(m.day.isoformat(), s.currency): m for s in built for m in s.days}


def _pasted_report(rows: pasted.Pasted, req: requests.Request) -> Report:
    s = rows.series
    days = req.days if req.names_days and all(s.day(d) for d in req.days) else _last_days(s, req)
    verdicts = tuple(v for d in days if (v := detect.check_day(s, d, req.baseline_days)) is not None)
    checks_ = tuple(day_check(v, tuple(Flag(a, accounts.Behind(())) for a in v.anomalies)) for v in verdicts)
    extra = {"bad_rows": str(rows.bad_rows)} if rows.bad_rows else {}
    return Report(checks=checks_, baseline_days=req.baseline_days, source="pasted", extra=extra)


def _last_days(s: serieses.Series, req: requests.Request) -> tuple[date, ...]:
    """Without a named day in the rows: the last row, or the last N rows for "last N days"."""
    count = len(req.days) if req.names_days else 1
    return tuple(m.day for m in s.days[-count:])


async def _finish(state: AnomalyState, deps: TurnDeps, report: Report, req: requests.Request) -> dict:
    if not report.checks:
        return {"reply": "There are no days with data to check in that range.", "route": ""}
    if deps.traceparent:
        headline, checked = await steps.write_headline(deps, report)
    else:
        headline, checked = post.code_headline(report), False
    report = replace(report, post=post.assemble(headline, report), headline_checked=checked)
    env = render.envelope(report)
    reply = render.json_block(env) if req.json_mode else render.markdown(report)
    route = "post" if req.wants_post or req.channel else ""
    return {"report": report.to_dict(), "posted": [], "reply": reply, "envelope": env, "route": route}


def _refusal(reply: str, query: str) -> str:
    """A refusal that ends the turn before any model call still declines an off-topic message as such."""
    unreadable = reply.startswith((prompts.NO_PAYMENTS_TOOL, prompts.GATEWAY_UNREACHABLE)) or \
        prompts.NO_PAYMENTS_TOOL in reply
    return smalltalk.OFF_TOPIC_REPLY if unreadable and not requests.mentions_domain(query) else reply


# --- the post -----------------------------------------------------------------------------------------------------

async def _post(state: AnomalyState, deps: TurnDeps) -> dict:
    lead = state.get("reply") or ""
    report = Report.from_dict(state.get("report") or {"checks": [], "baseline_days": 0})
    if not report.checks or not report.post:
        return {"reply": "\n\n".join(p for p in (lead, prompts.NO_POST_YET) if p), "route": ""}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    name = _request(state).channel or (state.get("channel") or "").lstrip("#") or None
    catalog, failure = await discover(deps, "read")
    if failure is not None:
        return {"reply": render.copyable(f"#{name}" if name else "your channel", report.post, failure), "route": ""}
    reader = steps.reader_for(deps, catalog)
    channels = await steps.find_channels(deps, reader, name)
    if name:
        target = picker.matching(name, channels) or picker.typed(name)
        planned = await plan_post(deps, report, target)
        return {**planned, "channel": target.label, "reply": "\n\n".join(p for p in (lead, planned.get("reply")) if p),
                "route": ""}
    targets = picker.options(channels)
    if not targets:
        return {"reply": "\n\n".join(p for p in (lead, prompts.TYPE_CHANNEL) if p), "route": ""}
    pause = picker.question(targets)
    return {"reply": lead, "pause": {"text": pause.text, "hitl": pause.hitl, "metadata": pause.metadata},
            "route": ""}


async def plan_post(deps: TurnDeps, report: Report, target: picker.Target) -> dict:
    """{"walk": ...} when a post tool exists, else the copyable post."""
    tool, why = await sender.find_send_tool(deps)
    if tool is None:
        log.info("no post tool: %s", why)
        reason = prompts.NO_CHAT_TOOL if "not granted" in why else why
        return {"reply": render.copyable(target.label, report.post, reason)}
    planned = sender.Post(target=target.label, recipient=target.recipient, body=report.post, tool=tool.name)
    return {"walk": {"post": planned.to_dict(), "allowed": sorted(post.allowed_numbers(report))}}
