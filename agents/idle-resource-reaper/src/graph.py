"""The idle-reaper LangGraph.

    START ─┬─ (inventory pasted) → analyse: parse → rules → cost (code) → owners (tools) → group (code) → END
           ├─ (a list was given in this context) → followup ─┬─ answer ───────────────→ END
           │                                                 ├─ DRAFT:<owners> → draft → END (walk)
           │                                                 └─ NEEDS_OWNERS → owners → END
           └─ (nothing pasted) → intake: paste instructions, or the off-topic decline → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py). State is
checkpointed per A2A contextId (thread_id), so a follow-up sees the list. The graph is compiled per turn
because its nodes close over that turn's dependencies; the checkpointer is shared. Drafts and questions
leave the graph in the turn result; the approve → send walk runs outside it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import drafting
import findings
import owners
import picker
import prompts
import render
import smalltalk
import steps
from deps import TurnDeps
from drafting import Typed
from drafts import Draft
from findings import Row
from inventory import find_inventory
from reader import ask_once
from request import (
    asks_to_destroy,
    drafts_all_known,
    numbered,
    parse_request,
    wants_draft,
)

__all__ = ["ReaperAgent", "TurnDeps", "TurnResult"]

# Words that put a message plainly in scope: no model call is needed to keep it.
_IN_SCOPE = re.compile(r"\b(?:idle|unused|orphan\w*|unattached|inventor\w*|volumes?|snapshots?|instances?|"
                       r"resources?|cloud|ec2|ebs|elastic ip|load balancers?|nat|cost\w*|spend\w*|waste\w*|"
                       r"clean\s*up|cleanup|reap\w*|owners?)\b", re.IGNORECASE)


class ReaperState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    items: list[dict]          # the rows last shown, numbered: what "#2" refers to
    outcomes: dict[str, str]   # owner key → sent / skipped / denied / blocked / not_sent
    meta: dict                 # render.ListMeta of the list last shown
    draft_keys: list[str]
    typed: dict | None
    drafts: list[dict]         # this turn: drafts ready for the approval walk
    pick: dict | None          # this turn: a question to pause on
    envelope: dict | None      # this turn: the envelope of a list shown


@dataclass(frozen=True)
class TurnResult:
    reply: str
    drafts: tuple[Draft, ...] = ()
    pick: picker.Pick | None = None
    envelope: dict | None = None


class ReaperAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        fresh = {"query": query, "reply": "", "route": "", "draft_keys": [], "typed": None, "drafts": [],
                 "pick": None, "envelope": None}
        return await self._invoke(fresh, context_id, deps)

    async def draft_for(self, keys: Sequence[str], instruction: str, context_id: str, deps: TurnDeps,
                        typed: Typed | None = None) -> TurnResult:
        """Drafts for owners picked on the multi-select (or named in a contact answer), by owner key."""
        values = await self._values(context_id)
        rows = findings.from_state(values.get("items"))
        meta = render.ListMeta.from_dict(values.get("meta"))
        grace = _grace(instruction, meta)
        turn = await drafting.draft_turn(deps, instruction, keys, rows, dict(values.get("outcomes") or {}), grace,
                                         typed)
        return TurnResult(turn.reply, turn.drafts, turn.ask_contact)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember what happened to each owner's message, so later turns neither resend nor redraft it."""
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        values = await self._values(context_id)
        merged = {**dict(values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "drafts": []}, as_node="analyse")

    async def _invoke(self, fresh: dict, context_id: str, deps: TurnDeps) -> TurnResult:
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        drafts = tuple(Draft.from_dict(d) for d in final.get("drafts") or ())
        pick = final.get("pick")
        return TurnResult(final.get("reply") or "", drafts, picker.Pick(**pick) if pick else None,
                          final.get("envelope") or None)

    async def _values(self, context_id: str) -> dict:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _grace(text: str, meta: render.ListMeta) -> int:
    req = parse_request(text)
    return req.grace_days if req.explicit_grace else meta.grace_days


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    async def analyse(state: ReaperState) -> dict:
        return await _analyse(state, deps)

    async def followup(state: ReaperState) -> dict:
        return await _followup(state, deps)

    async def intake(state: ReaperState) -> dict:
        return {"reply": await _intake(state["query"], deps), "route": ""}

    async def owners_node(state: ReaperState) -> dict:
        return await _relookup(state, deps)

    async def draft(state: ReaperState) -> dict:
        rows = findings.from_state(state.get("items"))
        grace = _grace(state["query"], render.ListMeta.from_dict(state.get("meta")))
        turn = await drafting.draft_turn(deps, state["query"], state.get("draft_keys") or (), rows,
                                         dict(state.get("outcomes") or {}), grace)
        return {"reply": turn.reply, "drafts": [d.to_dict() for d in turn.drafts], "route": "",
                "pick": _pick_state(turn.ask_contact)}

    graph = StateGraph(ReaperState)
    for name, node in (("analyse", analyse), ("followup", followup), ("intake", intake), ("owners", owners_node),
                       ("draft", draft)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["analyse", "followup", "intake"])
    graph.add_conditional_edges("followup", _route, ["draft", "owners", END])
    for terminal in ("analyse", "intake", "owners", "draft"):
        graph.add_edge(terminal, END)
    return graph


def _entry(state: ReaperState) -> str:
    if find_inventory(state.get("query") or "") is not None:
        return "analyse"
    return "followup" if state.get("items") else "intake"


def _route(state: ReaperState) -> str:
    return state.get("route") or END


async def _intake(query: str, deps: TurnDeps) -> str:
    """Nothing pasted and no list yet: the paste instructions, or the decline for an off-topic message
    (one tool-less scope check, only when the message is not plainly in scope)."""
    if asks_to_destroy(query):
        return f"{prompts.NEVER_DELETES_NOTE}\n\n{prompts.PASTE_INSTRUCTIONS}"
    if _IN_SCOPE.search(query or "") or not deps.traceparent:
        return prompts.PASTE_INSTRUCTIONS
    verdict = await ask_once(deps, ({"role": "system", "content": prompts.SCOPE_CHECK},
                                    {"role": "user", "content": query}))
    off = verdict.startswith(prompts.OFF_TOPIC) and "IN_SCOPE" not in verdict
    return smalltalk.OFF_TOPIC_REPLY if off else prompts.PASTE_INSTRUCTIONS


async def _analyse(state: ReaperState, deps: TurnDeps) -> dict:
    query = state["query"]
    req = parse_request(query)
    parsed = find_inventory(query)
    analysis = findings.analyse(parsed, deps.today, req)
    lookup = await steps.lookup_owners(deps, analysis.rows) if analysis.rows else steps.LookupOutcome(())
    notes = (*lookup.notes, *((prompts.NO_TRACE_NOTE,) if not deps.traceparent and analysis.rows
                              and prompts.NO_TRACE_NOTE not in lookup.notes else ()))
    groups, rows = owners.group(lookup.rows, req.owner)
    meta = render.ListMeta(resources=analysis.resources, unreadable=analysis.unreadable,
                           total_rows=analysis.total_rows, over_limit=analysis.over_limit,
                           kept_by_tag=analysis.kept_by_tag, below_min_cost=analysis.below_min_cost,
                           min_cost=str(req.min_cost) if req.min_cost is not None else "", owner_filter=req.owner,
                           not_evaluated=analysis.not_evaluated, notes=notes, lookup_done=lookup.complete,
                           grace_days=req.grace_days)
    return _shown(rows, groups, meta, {}, deps, lead=prompts.NEVER_DELETES_NOTE if asks_to_destroy(query) else "")


def _shown(rows: Sequence[Row], groups, meta: render.ListMeta, outcomes: Mapping[str, str], deps: TurnDeps,
           lead: str = "") -> dict:
    can_draft = bool(deps.traceparent)
    reply = render.render_list(rows, groups, meta, outcomes, can_draft=can_draft)
    pick = picker.question(groups, outcomes) if can_draft and rows else None
    return {"reply": "\n\n".join(p for p in (lead, reply) if p), "items": findings.to_state(rows),
            "meta": meta.to_dict(), "outcomes": dict(outcomes), "route": "",
            "envelope": render.envelope(rows, groups, meta), "pick": _pick_state(pick)}


async def _relookup(state: ReaperState, deps: TurnDeps) -> dict:
    rows = findings.from_state(state.get("items"))
    meta = render.ListMeta.from_dict(state.get("meta"))
    lookup = await steps.lookup_owners(deps, rows)
    groups, renumbered = owners.group(lookup.rows, meta.owner_filter)
    kept = tuple(n for n in meta.notes if n not in _LOOKUP_NOTES)
    fresh = render.ListMeta(**{**meta.to_dict(), "notes": (*kept, *lookup.notes), "lookup_done": lookup.complete})
    return _shown(renumbered, groups, fresh, dict(state.get("outcomes") or {}), deps)


_LOOKUP_NOTES = frozenset({prompts.NO_TRACE_NOTE, prompts.TIME_SKIPPED_NOTE, prompts.STEP_LIMIT_NOTE,
                           prompts.GATEWAY_UNREACHABLE, steps.UNREADABLE_LOOKUP, steps.READ_NEEDS_APPROVAL})


async def _followup(state: ReaperState, deps: TurnDeps) -> dict:
    query = state["query"]
    rows = findings.from_state(state.get("items"))
    groups = owners.regroup(rows)
    if wants_draft(query):
        keys = _draft_keys(query, rows, groups)
        if keys:  # "draft for #1", "draft for priya", "draft for all known owners": parsed in code
            return {"route": "draft", "draft_keys": list(keys)}
    if asks_to_destroy(query):
        return {"reply": "\n\n".join(p for p in (prompts.NEVER_DELETES_NOTE, render.never_deletes_reply(groups)) if p),
                "route": ""}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    meta = render.ListMeta.from_dict(state.get("meta"))
    listing = render.render_for_model(rows, meta, dict(state.get("outcomes") or {}))
    # Only the fenced list, never earlier replies.
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(listing)}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.NEEDS_OWNERS):
        return {"route": "owners"}
    if text.startswith(prompts.DRAFT_PREFIX):
        keys = _keys_of_numbers(drafting.requested_numbers(text), rows)
        return {"route": "draft", "draft_keys": list(keys)} if keys else {"reply": prompts.NOTHING_TO_DRAFT,
                                                                           "route": ""}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}


def _keys_of_numbers(numbers: Sequence[str], rows: Sequence[Row]) -> tuple[str, ...]:
    by_id = {r.id: r.owner_key for r in rows}
    return tuple(dict.fromkeys(by_id[n] for n in numbers if n in by_id))


def _draft_keys(query: str, rows: Sequence[Row], groups) -> tuple[str, ...]:
    if drafts_all_known(query):
        return tuple(g.key for g in groups if g.known)[:drafting.MAX_DRAFTS_PER_TURN]
    by_number = _keys_of_numbers(numbered(query), rows)
    if by_number:
        return by_number
    lowered = query.casefold()
    named = [g.key for g in groups if g.known and g.owner and re.search(rf"\b{re.escape(g.owner.casefold())}\b",
                                                                         lowered)]
    return tuple(named)


def _pick_state(pick: picker.Pick | None) -> dict | None:
    return {"text": pick.text, "hitl": pick.hitl, "metadata": pick.metadata} if pick else None
