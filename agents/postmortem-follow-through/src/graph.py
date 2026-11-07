"""The postmortem follow-through LangGraph.

    START ─┬─ (a table exists in this context) → followup ─┬─ answered / filtered ─────────────→ END
           │                                               ├─ nudge / reopen #n → act ───────→ END
           │                                               ├─ match <incident> → match ──────→ END
           │                                               └─ refresh ──┐
           └─ (no table yet) ──────────────────────────→ intake ←───────┘
       intake → gather (find ∥ find_docs) → extract → status → repeats → report → END

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId, so "#2"
in a later turn means the row numbered 2 in the table shown last. Writes leave the graph as actions
(or rows to pick from); the approve → write walk runs outside it, its state on the A2A task.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake as intake_
import nudge
import prompts
import reads
import reopen
import smalltalk
import table_render
import turn
from actions import Action
from assess import Row
from deps import TurnDeps
from discovery import discover
from records import Postmortem, from_state
from steps import Deadline, ask_once
from tool_health import ToolHealth

__all__ = ["PostmortemAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("postmortem.graph")


class State(TypedDict, total=False):
    query: str
    reply: str
    route: str
    filter: str | None
    match_ref: str | None
    match_text: str | None
    notes: list[str]
    dropped: int
    months: int
    pms: list[dict]
    items: list[dict]
    incidents: list[dict]
    rows: list[dict]          # the table last shown, numbered: what "#2" refers to
    wont_do: list[str]
    untracked: list[str]
    open_items: dict          # postmortem id → [[key, idle days]] of its items that are not done
    outcomes: dict[str, str]  # "nudge:KEY" / "reopen:KEY" → nudged / reopened / skipped / denied / ...
    actions: list[dict]       # this turn: writes ready for the approval walk
    select: list[dict]        # this turn: rows to pick from before nudging
    command: dict             # this turn: the follow-up command for `act`


@dataclass(frozen=True)
class TurnResult:
    reply: str
    actions: tuple[Action, ...] = ()
    select: tuple[Row, ...] = ()


def rows_of(state: Mapping[str, Any]) -> tuple[Row, ...]:
    return tuple(Row(**{**r, "flags": tuple(r.get("flags") or ())}) for r in state.get("rows") or ())


def row_dicts(rows: Sequence[Row]) -> list[dict]:
    return [{**r.__dict__, "flags": list(r.flags)} for r in rows]


class PostmortemAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        scope = turn.Scope(deps, Deadline(deps.limits.turn_secs))
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "actions": [], "select": [], "notes": [], "command": {}}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        return TurnResult(final.get("reply") or "", tuple(Action.from_dict(a) for a in final.get("actions") or ()),
                          rows_of({"rows": final.get("select")}))

    async def outcomes(self, context_id: str) -> dict[str, str]:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        current = await graph.aget_state(_config(context_id))
        return dict(current.values.get("outcomes") or {})

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember what happened to each write, so later turns neither repeat nor retry it."""
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        current = await graph.aget_state(_config(context_id))
        merged = {**dict(current.values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "actions": [], "select": []},
                                  as_node="report")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(scope: turn.Scope | None) -> StateGraph:
    async def report(state: State) -> dict:
        out = turn.report(state, scope)
        return {**out, "rows": row_dicts(rows_of(out))}

    def bound(step):
        async def node(state: State) -> dict:
            return await step(state, scope)
        return node

    nodes = {
        "followup": bound(_followup), "intake": bound(turn.intake_node), "gather": bound(turn.gather),
        "extract": bound(turn.extract_node), "status": bound(turn.status_node), "repeats": bound(turn.repeats_node),
        "report": report, "act": bound(_act), "match": bound(_match),
    }
    graph = StateGraph(State)
    for name, node in nodes.items():
        graph.add_node(name, node)
    graph.add_conditional_edges(START, lambda s: "followup" if s.get("rows") is not None and s.get("pms")
                                else "intake", ["followup", "intake"])
    graph.add_conditional_edges("followup", _route, ["intake", "act", "match", END])
    graph.add_conditional_edges("intake", _route, ["gather", END])
    graph.add_conditional_edges("gather", _route, ["extract", END])
    for a, b in (("extract", "status"), ("status", "repeats"), ("repeats", "report")):
        graph.add_edge(a, b)
    for name in ("report", "act", "match"):
        graph.add_edge(name, END)
    return graph


def _route(state: State) -> str:
    return state.get("route") or END


async def _followup(state: State, scope: turn.Scope) -> dict:
    deps = scope.deps
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    command = intake_.parse_command(state["query"])
    if command.kind == "other":
        command = await _ask_followup(state, deps)
    if command.kind == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if command.kind == "answer":
        return {"reply": command.arg or prompts.UNREADABLE_ANSWER, "route": ""}
    if command.kind == "refresh":
        return {"route": "intake"}
    if command.kind == "filter":
        return {"reply": _filtered(state, command.arg or ""), "route": ""}
    if command.kind == "match":
        return {"match_ref": intake_.incident_ref(command.arg or ""), "match_text": command.arg, "route": "match"}
    return {"route": "act", "command": {"kind": command.kind, "ids": list(command.ids), "arg": command.arg}}


async def _ask_followup(state: State, deps: TurnDeps) -> intake_.Command:
    listing = table_render.render_for_followup(rows_of(state), state.get("outcomes") or {},
                                               state.get("wont_do") or (), state.get("untracked") or ())
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(listing)}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": state["query"]}))
    for prefix, kind in ((prompts.NUDGE_PREFIX, "nudge"), (prompts.REOPEN_PREFIX, "reopen")):
        if text.startswith(prefix):
            return intake_.Command(kind, intake_.numbers(text[len(prefix):]))
    if text.startswith(prompts.MATCH_PREFIX):
        return intake_.Command("match", arg=text[len(prompts.MATCH_PREFIX):].strip())
    if text.startswith(prompts.REFRESH):
        return intake_.Command("refresh")
    if text.startswith(prompts.OFF_TOPIC):
        return intake_.Command("off_topic")
    return intake_.Command("answer", arg=text)


def _filtered(state: State, wanted: str) -> str:
    needle = wanted.casefold()
    rows = [r for r in rows_of(state) if needle in f"{r.key} {r.text} {r.pm_label}".casefold()]
    if not rows:
        return f"None of the slipped items mention \"{wanted}\"."
    return f"Slipped items mentioning \"{wanted}\" (numbers as before):\n\n" + \
        table_render.render_table(rows, state.get("outcomes") or {})


async def _act(state: State, scope: turn.Scope) -> dict:
    deps = scope.deps
    command = state.get("command") or {}
    kind, ids, arg = command.get("kind"), list(command.get("ids") or ()), command.get("arg") or ""
    rows = rows_of(state)
    outcomes = state.get("outcomes") or {}
    if kind == "nudge_all":
        return _nudge_all(rows, arg, outcomes)
    by_n = {r.n: r for r in rows}
    unknown = [f"I don't have #{i} in the table." for i in ids if i not in by_n]
    chosen = [by_n[i] for i in ids if i in by_n]
    if not chosen:
        return {"reply": " ".join(unknown) or prompts.NO_PRIOR_TABLE, "route": ""}
    prepared = await (nudge.prepare if kind == "nudge" else reopen.prepare)(deps, chosen, outcomes)
    notes = [*unknown, *prepared.notes]
    return {"reply": "\n\n".join(notes), "actions": [a.to_dict() for a in prepared.actions], "route": ""}


def _nudge_all(rows: Sequence[Row], category: str, outcomes: Mapping[str, str]) -> dict:
    wanted = {"unassigned": "unassigned", "untouched": "untouched", "stale": "untouched", "idle": "untouched",
              "closed": "no_evidence"}.get(category or "")
    picked = [r for r in rows if wanted is None or wanted in r.flags]
    if wanted == "unassigned":
        return {"reply": f"The {len(picked)} unassigned items have no owner, so there is no one to message. "
                         "Assign them in the tracker, or nudge the items that do have owners.", "route": ""}
    nudgeable = [r for r in picked if r.owner_email and outcomes.get(f"nudge:{r.key}") not in nudge.FINAL]
    if not nudgeable:
        return {"reply": "None of those items has an owner I can message.", "route": ""}
    return {"reply": "", "select": row_dicts(nudgeable), "route": ""}


async def _match(state: State, scope: turn.Scope) -> dict:
    deps = scope.deps
    pms = from_state(Postmortem, state.get("pms"))
    named = None
    if state.get("match_ref"):
        catalog, failure = await discover(deps, ("repeats", "find"))
        if failure is not None:
            return {"reply": failure, "route": ""}
        tools = catalog.tools_for("repeats") if catalog.has_source("repeats") else catalog.tools_for("find")
        since = deps.today.isoformat()
        found = await reads.repeats(deps, tools, ToolHealth(deps.gateway), turn.window_of(state, deps.today),
                                    since, state["match_ref"], deps.limits.repeats_secs)
        named = found.named
    text = turn.match_text(state, pms, rows_of(state), state.get("open_items") or {}, named)
    return {"reply": text, "route": ""}
