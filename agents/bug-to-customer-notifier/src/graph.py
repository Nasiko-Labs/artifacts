"""The bug-to-customer-notifier LangGraph.

    START ─┬─ entry "draft" (ticket pick answered) ─────────────────→ draft → END
           ├─ entry "ship" (bug pick answered) ──────────→ ship ─┐
           ├─ (a result was given in this context) → followup ─┬─ answered → END
           │                                                    ├─ DRAFT:<ids> → draft → END
           │                                                    └─ NEEDS_PULL ─┐
           └─ (nothing yet, or a new issue key) ───────────────────→ bug ←─────┘
      bug ─┬─ reply / sweep list (+ bug pick pause) → END
           └─ one bug → ship ─┬─ not shipped → report → END (no drafts)
                              └─ shipped → tickets → report (+ ticket pick pause) → END

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId; the graph
is compiled per turn because its nodes close over that turn's dependencies. Pauses and drafts leave
the graph in the turn result; the executor ends the turn with them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import pick
import render
import replies
import smalltalk
from approval import Draft
from deps import TurnDeps
from draft_check import Facts
from drafting import draft_turn
from reader import TurnReader
from records import Bug, ShipVerdict, Ticket, from_state, to_state
from steps_bug import bug_step, requested_keys
from steps_followup import followup_step
from steps_ship import ship_step
from steps_tickets import tickets_step

__all__ = ["NotifierAgent", "TurnDeps", "TurnResult"]


class NotifierState(TypedDict, total=False):
    query: str
    entry: str                 # this turn: "", "ship" (bug picked) or "draft" (tickets picked)
    selected_key: str
    draft_ids: list[str]
    instruction: str
    reply: str
    route: str
    choices: list[dict]        # sweep bugs offered for a pick
    bug: dict | None
    verdict: dict | None
    tickets: list[dict]        # the checked tickets last shown
    outcomes: dict[str, str]   # ticket id → sent / solved / skipped / denied / blocked / not_sent
    notes: list[str]
    pause: dict | None         # this turn: the pick pause to end on
    drafts: list[dict]         # this turn: drafts for the approval walk
    facts: dict | None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: pick.Pause | None = None
    drafts: tuple[Any, ...] = ()
    facts: Facts | None = None


class NotifierAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, *, entry: str = "", selected_key: str = "",
                   draft_ids: tuple[str, ...] = (), instruction: str = "") -> TurnResult:
        if not entry:
            canned = smalltalk.reply_to(query)
            if canned is not None:  # greeting / small talk: no tool call, no LLM call, state untouched
                return TurnResult(canned)
        graph = _build_graph(deps, TurnReader(deps)).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "entry": entry, "selected_key": selected_key, "draft_ids": list(draft_ids),
                 "instruction": instruction, "reply": "", "route": "", "notes": [], "pause": None, "drafts": [],
                 "facts": None}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        pause = final.get("pause")
        return TurnResult(final.get("reply") or "", pick.Pause(**pause) if pause else None,
                          tuple(Draft.from_dict(d) for d in final.get("drafts") or ()),
                          Facts(**final["facts"]) if final.get("facts") else None)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember what happened to each reply, so later turns neither repost nor redraft it."""
        if not outcomes:
            return
        graph = _build_graph(None, None).compile(checkpointer=self._checkpointer)
        current = await graph.aget_state(_config(context_id))
        merged = {**dict(current.values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "drafts": [], "pause": None},
                                  as_node="draft")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps | None, turn: TurnReader | None) -> StateGraph:
    async def followup(state: NotifierState) -> dict:
        return await followup_step(deps, state)

    async def bug(state: NotifierState) -> dict:
        return await _bug(deps, turn, state)

    async def ship(state: NotifierState) -> dict:
        return await _ship(deps, turn, state)

    async def tickets(state: NotifierState) -> dict:
        return await _tickets(deps, turn, state)

    async def draft(state: NotifierState) -> dict:
        return await _draft(deps, state)

    graph = StateGraph(NotifierState)
    for name, node in (("followup", followup), ("bug", bug), ("ship", ship), ("tickets", tickets), ("draft", draft)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "bug", "ship", "draft"])
    graph.add_conditional_edges("followup", _route, ["bug", "draft", END])
    graph.add_conditional_edges("bug", _route, ["ship", END])
    graph.add_conditional_edges("ship", _route, ["tickets", END])
    graph.add_edge("tickets", END)
    graph.add_edge("draft", END)
    return graph


def _entry(state: NotifierState) -> str:
    if state.get("entry") in ("ship", "draft"):
        return state["entry"]
    stored = state.get("bug")
    if not stored or not state.get("verdict"):
        return "bug"
    keys = requested_keys(state.get("query") or "")
    return "bug" if keys and stored.get("key") not in keys else "followup"


def _route(state: NotifierState) -> str:
    return state.get("route") or END


async def _bug(deps: TurnDeps, turn: TurnReader, state: NotifierState) -> dict:
    found = await bug_step(deps, turn, state["query"])
    if found.reply is not None:  # a refusal or nothing found keeps the previous result
        return {"reply": found.reply, "route": ""}
    cleared = {"verdict": None, "tickets": [], "outcomes": {}}
    if found.bug is not None:
        return {**cleared, "bug": asdict(found.bug), "choices": [], "notes": list(found.notes), "route": "ship"}
    table = render.sweep_table(found.choices, found.no_fix_version, found.window_days or deps.config.sweep_days,
                               found.notes)
    pause = pick.bug_pause(table, found.choices, deps.config.max_offered) if found.choices else None
    return {**cleared, "bug": None, "choices": to_state(found.choices), "reply": table, "route": "",
            "pause": asdict(pause) if pause else None}


async def _ship(deps: TurnDeps, turn: TurnReader, state: NotifierState) -> dict:
    bug = from_state(Bug, state.get("bug"))
    if state.get("entry") == "ship":
        bug = next((b for b in from_state(Bug, state.get("choices") or []) if b.key == state.get("selected_key")), None)
        if bug is None:
            return {"reply": replies.UNKNOWN_BUG_CHOICE, "route": ""}
    verdict, notes = await ship_step(deps, turn, bug)
    update = {"bug": asdict(bug), "verdict": asdict(verdict), "tickets": [], "outcomes": {},
              "notes": [*(state.get("notes") or []), *notes]}
    if not verdict.shipped:
        return {**update, "reply": render.report(bug, verdict, None, deps.config, deps.today, update["notes"]),
                "route": ""}
    return {**update, "route": "tickets"}


async def _tickets(deps: TurnDeps, turn: TurnReader, state: NotifierState) -> dict:
    bug, verdict = from_state(Bug, state["bug"]), from_state(ShipVerdict, state["verdict"])
    found = await tickets_step(deps, turn, bug)
    notes = [*(state.get("notes") or []), *found.notes]
    if found.unquoted:
        notes.append(f"{found.unquoted} complaint quote(s) couldn't be verified, so they are not shown.")
    report = render.report(bug, verdict, found, deps.config, deps.today, notes)
    pause = pick.tickets_pause(report, found.tickets, deps.config) if found.tickets else None
    return {"tickets": to_state(found.tickets), "reply": report, "pause": asdict(pause) if pause else None,
            "route": ""}


async def _draft(deps: TurnDeps, state: NotifierState) -> dict:
    if not state.get("bug") or not state.get("verdict"):
        return {"reply": replies.NO_LIST_YET, "route": ""}
    turn = await draft_turn(deps, from_state(Bug, state["bug"]), from_state(ShipVerdict, state["verdict"]),
                            from_state(Ticket, state.get("tickets") or []), state.get("draft_ids") or (),
                            state.get("instruction") or "", state.get("outcomes") or {})
    return {"reply": turn.reply, "drafts": [d.to_dict() for d in turn.drafts],
            "facts": asdict(turn.facts) if turn.facts else None, "route": ""}
