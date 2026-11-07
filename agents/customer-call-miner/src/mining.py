"""The mining nodes: calls → match → transcripts → extract → cluster → render. Each takes the graph state
and this turn's scope (dependencies and readers, never persisted) and returns a state update."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping
from typing import Any

import accounts as acc
import calls_step
import cluster as cl
import coverage
import extract as ex
import match_step
import render
import transcripts_step
from deps import TurnDeps
from discovery import discover
from reader import Reader
from records import Account, Call, account_from, call_from, dump_all, item_from
from windows import Window, parse_instant, window_for

log = logging.getLogger("call_miner.mining")
TIME_PROBLEM = "not analysed (time)"


class Scope:
    """One turn's dependencies and readers (never persisted)."""

    def __init__(self, deps: TurnDeps):
        self.deps = deps
        self.calls_reader: Reader | None = None
        self.turns: dict = {}


def calls_of(state: Mapping[str, Any]) -> dict[str, Call]:
    return {c["id"]: call_from(c) for c in state.get("calls") or ()}


def accounts_of(state: Mapping[str, Any]) -> dict[str, Account]:
    return {a["company_id"]: account_from(a) for a in state.get("accounts") or ()}


def window_of(state: Mapping[str, Any], deps: TurnDeps) -> Window:
    return Window.from_dict(state.get("window"), deps.today)


async def calls_node(state: Mapping[str, Any], scope: Scope) -> dict:
    deps = scope.deps
    window = window_for(state["query"], deps.today)
    catalog, failure = await discover(deps, "calls")
    if failure is not None:
        return {"reply": failure, "route": ""}
    scope.calls_reader = Reader.for_catalog(deps, catalog)
    outcome = await calls_step.list_calls(deps, scope.calls_reader, state["query"], window)
    if outcome.reply is not None:  # a refusal or an empty period keeps the previous results
        return {"reply": outcome.reply, "route": ""}
    return {"window": window.to_dict(), "calls": dump_all(outcome.calls), "internal_only": outcome.internal_only,
            "processed": [], "problems": {}, "partly": {}, "items": [], "dropped": {}, "written": {},
            "accounts": [], "matches": {}, "route": "match"}


async def match_node(state: Mapping[str, Any], scope: Scope) -> dict:
    deps = scope.deps
    calls = list(calls_of(state).values())
    found: tuple[Account, ...] = ()
    catalog, failure = await discover(deps, "accounts")
    status = coverage.SourceStatus("crm", "missing")
    if failure is None:
        reader = Reader.for_catalog(deps, catalog)
        if reader.tools:
            found = (await match_step.match_accounts(deps, reader, acc.lookup_domains(calls, deps.config))).accounts
        status = reader.status()
    accounts = {a.company_id: a for a in found}
    matches = {c.id: acc.match_call(c, found, deps.config).account_id or "" for c in calls}
    ordered = sorted(calls, key=lambda c: (-(accounts[matches[c.id]].arr or 0.0) if matches[c.id] else 0.0,
                                           -_started(c)))
    return {"accounts": dump_all(found), "matches": matches, "calls": dump_all(ordered),
            "arr_available": status.ok, "crm_line": "" if status.ok else coverage.line(status),
            "route": "transcripts"}


def _started(call: Call) -> float:
    instant = parse_instant(call.started_at)
    return instant.timestamp() if instant else 0.0


def next_batch(state: Mapping[str, Any], max_calls: int) -> list[str]:
    done = set(state.get("processed") or ())
    return [c["id"] for c in state.get("calls") or () if c["id"] not in done][:max_calls]


async def transcripts_node(state: Mapping[str, Any], scope: Scope) -> dict:
    deps = scope.deps
    batch_ids = next_batch(state, deps.config.max_calls)
    calls = calls_of(state)
    batch = [calls[i] for i in batch_ids]
    if scope.calls_reader is None:
        catalog, failure = await discover(deps, "calls")
        if failure is not None:
            return {"reply": failure, "route": ""}
        scope.calls_reader = Reader.for_catalog(deps, catalog)
    fetched = await transcripts_step.fetch(deps, scope.calls_reader, batch)
    scope.turns = fetched.turns
    problems = {**dict(state.get("problems") or {}), **fetched.problems}
    for cid in batch_ids:
        if cid in fetched.turns:
            problems.pop(cid, None)
    processed = [*(state.get("processed") or ()), *fetched.problems]
    return {"batch": batch_ids, "problems": problems, "processed": processed, "route": "extract"}


async def extract_node(state: Mapping[str, Any], scope: Scope) -> dict:
    deps = scope.deps
    calls = calls_of(state)
    turns = scope.turns
    batch = [calls[i] for i in state.get("batch") or () if i in turns]
    matches = {k: (v or None) for k, v in (state.get("matches") or {}).items()}
    result = await ex.extract(deps, batch, turns, matches)
    problems = dict(state.get("problems") or {})
    problems.update({cid: TIME_PROBLEM for cid in result.timed_out})
    partly = {**dict(state.get("partly") or {}), **{k: list(v) for k, v in result.partly.items()}}
    dropped = Counter(state.get("dropped") or {})
    dropped.update(result.dropped)
    return {"items": [*(state.get("items") or ()), *dump_all(result.items)],
            "processed": [*(state.get("processed") or ()), *result.done], "problems": problems,
            "partly": partly, "dropped": dict(dropped), "route": "cluster"}


async def cluster_node(state: Mapping[str, Any], scope: Scope) -> dict:
    items = [item_from(i) for i in state.get("items") or ()]
    accounts = accounts_of(state)
    arr = {a: (x.arr or 0.0) for a, x in accounts.items()}
    groupings = await cl.cluster(scope.deps, items, arr) if items else {}
    return {"groupings": groupings, "route": "render"}


def render_node(state: Mapping[str, Any], scope: Scope) -> dict:
    deps = scope.deps
    calls = calls_of(state)
    report = render.Report(window_label=window_of(state, deps).label, calls=calls, accounts=accounts_of(state),
                           items=[item_from(i) for i in state.get("items") or ()],
                           groupings=state.get("groupings") or {},
                           analysed=len([c for c in state.get("processed") or () if c not in (state.get("problems") or {})]),
                           total_calls=len(calls), arr_available=bool(state.get("arr_available")),
                           notes=coverage_notes(state, calls))
    text = render.render_report(report, deps.today)
    return {"reply": text, "report": text, "route": "offer"}


def coverage_notes(state: Mapping[str, Any], calls: Mapping[str, Call]) -> list[str]:
    notes: list[str] = []
    problems = state.get("problems") or {}
    remaining = [c for c in calls if c not in set(state.get("processed") or ())]
    timed = [c for c in remaining if problems.get(c) == TIME_PROBLEM]
    if timed:
        notes.append(f"{len(timed)} call(s) not analysed (time): say \"continue\" to analyse them.")
    if len(remaining) > len(timed):
        notes.append(f"{len(remaining) - len(timed)} more call(s) not analysed yet (highest-ARR accounts went first): "
                     "say \"continue\" for the rest.")
    for cid, (done, total) in (state.get("partly") or {}).items():
        notes.append(f"{_title(calls, cid)}: partly analysed, {done} of {total} parts.")
    notes += [f"{_title(calls, cid)}: skipped, {why}." for cid, why in problems.items() if why != TIME_PROBLEM]
    notes += [f"{n} item(s) dropped: {why}." for why, n in (state.get("dropped") or {}).items() if n]
    if state.get("internal_only"):
        notes.append(f"{state['internal_only']} internal-only call(s) left out.")
    if state.get("crm_line"):
        notes.append(state["crm_line"])
    return notes


def _title(calls: Mapping[str, Call], call_id: str) -> str:
    call = calls.get(call_id)
    return f"\"{call.title}\"" if call and call.title else f"call {call_id}"
