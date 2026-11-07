"""The exec-brief LangGraph.

    START → intake ─┬─ fixed reply / code-only edit / pause ──────────────→ END
                    └─ work → compose (model + checks + render, commit) → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId (thread_id): the pasted sections, the instruction, the last
checked brief and (for large inputs) the checked map candidates, so "add this too", "drop risk 2",
"show sources" and revisions work on later turns. A pause keeps its pending paste here too; the
question itself lives on the A2A task. The graph is compiled per turn because the compose node
closes over that turn's model access; the checkpointer is shared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import brief as b
import compose
import inputs
import intake
import prompts
import render
import sections as sec
import smalltalk
from llm import CallBudgetError, Llm, MissingTraceparentError

log = logging.getLogger("exec_brief.graph")


class BriefState(TypedDict, total=False):
    sections: list[dict]
    instruction: str
    brief: dict | None
    candidates: list[dict] | None
    pending: list[dict]
    pending_instruction: str
    # this turn only
    query: str
    resume: str
    files: list[dict]
    skipped: list[str]
    reply: str
    pause: str
    route: str
    work: dict


@dataclass(frozen=True)
class TurnDeps:
    llm: Llm
    org: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: str = ""


class BriefAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: inputs.Incoming, context_id: str, deps: TurnDeps, resume: str = "") -> TurnResult:
        if not resume and not incoming.files:
            canned = smalltalk.reply_to(incoming.text)
            if canned is not None:  # a greeting or small talk: no model call, state untouched
                return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": incoming.text, "resume": resume, "files": sec.to_state(incoming.files),
                 "skipped": list(incoming.skipped), "reply": "", "pause": "", "route": "", "work": {}}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        return TurnResult(final.get("reply") or "", final.get("pause") or "")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def intake_node(state: BriefState) -> dict:
        return intake.decide(state)

    async def compose_node(state: BriefState) -> dict:
        return await _compose(state, deps)

    graph = StateGraph(BriefState)
    graph.add_node("intake", intake_node)
    graph.add_node("compose", compose_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["compose", END])
    graph.add_edge("compose", END)
    return graph


async def _compose(state: BriefState, deps: TurnDeps) -> dict:
    work = state.get("work") or {}
    chosen = sec.from_state(work.get("sections"))
    revise = bool(work.get("revise"))
    request = compose.Request(
        sections=chosen, instruction=work.get("instruction") or "", org=deps.org,
        prior=_prior_view(state.get("brief")) if revise else None,
        candidates=tuple(state.get("candidates") or ()) if revise and state.get("candidates") else None,
    )
    try:
        outcome = await compose.write(deps.llm, request)
    except MissingTraceparentError:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    except CallBudgetError:
        log.warning("call budget spent before a brief was written")
        outcome = compose.Outcome("unreadable")
    if outcome.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if work.get("scope_only"):
        return {"reply": prompts.TOO_SHORT}
    if outcome.brief is None:
        return {"reply": prompts.UNREADABLE}
    notes = [*(work.get("notes") or ()), *filter(None, [inputs.skipped_note(tuple(state.get("skipped") or ()))])]
    reply = render.render(outcome.brief, chosen, extra_notes=notes)
    return {"reply": reply, "sections": sec.to_state(chosen), "brief": b.to_dict(outcome.brief),
            "candidates": list(outcome.candidates) if outcome.candidates is not None else None,
            "instruction": work.get("store_instruction", work.get("instruction") or "")}


def _prior_view(data: dict | None) -> dict | None:
    """The earlier brief as the model sees it on a revision: lines and their section ids, no notes."""
    prior = b.from_dict(data)
    if prior is None:
        return None

    def line(row: b.Line) -> dict:
        return {"text": row.text, "sections": sorted({c.section for c in row.cites})}

    return {"headline": line(prior.headline) if prior.headline else None,
            "risks": [line(r) | {"severity": r.severity} for r in prior.risks],
            "decisions": [line(d) for d in prior.decisions]}
