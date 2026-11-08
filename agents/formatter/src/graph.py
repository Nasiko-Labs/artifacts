"""The formatter LangGraph (code only, zero model calls).

    START -> intake -+- render (JSON or Markdown -> the target) -> END
                     +- fixed reply / pause ----------------------> END

State is checkpointed per A2A contextId (thread_id): the last input (parsed JSON or pasted Markdown)
and its options, so "as slack", "as email" or "compact" re-render it without a re-paste. The graph is
compiled per turn because its nodes close over that turn's input; the checkpointer is shared. A pause
leaves the graph as a `pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import envelope
import followup
import hitl
import intake
import output
from inputs import Incoming


class FormatterState(TypedDict, total=False):
    values: list | None      # the last parsed JSON input (one entry per envelope)
    md_source: str | None    # or the last pasted Markdown
    opts: dict               # the options it was last rendered with
    route: str               # this turn only
    reply: str               # this turn only
    envelope: dict | None    # this turn only
    pause: dict | None       # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None
    pause: hitl.Pending | None = None


class FormatterAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, pending: hitl.Pending | None = None) -> TurnResult:
        graph = _build_graph(incoming, pending).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "envelope": None, "pause": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", final.get("envelope"),
                          hitl.Pending.from_metadata(final.get("pause")))


def _build_graph(incoming: Incoming, pending: hitl.Pending | None) -> StateGraph:
    scope: dict[str, intake.Plan] = {}

    async def intake_node(state: FormatterState) -> dict:
        plan = intake.plan_resume(pending, incoming) if pending else intake.plan_turn(incoming, dict(state))
        scope["plan"] = plan
        if plan.route == "pause":
            return {"route": "", "pause": plan.pending.to_metadata()}
        if plan.route == "reply":
            return {"route": "", "reply": _with_notes(plan.reply, incoming.notes)}
        return {"route": "render"}

    async def render_node(state: FormatterState) -> dict:
        return _render(scope["plan"], incoming)

    graph = StateGraph(FormatterState)
    graph.add_node("intake", intake_node)
    graph.add_node("render", render_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["render", END])
    graph.add_edge("render", END)
    return graph


def _render(plan: intake.Plan, incoming: Incoming) -> dict:
    if plan.route == "markdown":
        rendering = envelope.render_markdown(plan.md_source, plan.opts)
        stored = {"md_source": plan.md_source, "values": None}
    else:
        rendering = envelope.render_values(list(plan.values), plan.opts)
        stored = {"values": list(plan.values), "md_source": None}
    result = output.reply(rendering, as_json=plan.as_json, plain_version=plan.plain_version)
    return {**stored, "opts": followup.options_to_state(plan.opts),
            "reply": _with_notes(result.text, incoming.notes), "envelope": result.envelope}


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
