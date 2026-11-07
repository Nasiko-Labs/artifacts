"""The thread-untangler LangGraph.

    START → intake ─┬─ untangle (extract → check → reversals → render) → END
                    ├─ followup (your name, re-render, or one question)  → END
                    └─ fixed reply / pause ──────────────────────────────→ END

State is checkpointed per A2A contextId (thread_id): the parsed thread, its checked result and the
user's name, so "I'm Priya" or "only my items" re-render with zero calls. The graph is compiled per turn
because its nodes close over that turn's input and dependencies; the checkpointer is shared. A pause
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
import prompts
import render
import smalltalk
import thread as thread_parser
import untangle as untangle_step
from inputs import Incoming
from llm import TurnDeps
from model import Untangled


class ThreadState(TypedDict, total=False):
    thread: dict         # Thread.to_state(): the messages last untangled
    result: dict         # Untangled.to_dict(): what re-render commands draw from
    my_name: str         # the user's name, as they told it (per conversation)
    instruction: str
    route: str           # this turn only
    reply: str           # this turn only
    envelope: dict | None  # this turn only
    pause: dict | None   # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    envelope: dict | None = None


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake.Plan | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.plan: intake.Plan | None = None


class ThreadUntanglerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to one of this agent's questions, on the paused task."""
        plan = intake.plan_resume(hitl.interpret(pending, incoming.text), pending.as_json or incoming.wants_json)
        return await self._run(_Scope(deps, incoming, forced=plan), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "envelope": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pause = hitl.Pending.from_metadata(final.get("pause"))
        return TurnResult(final.get("reply") or "", pause, final.get("envelope"))


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(state: ThreadState) -> dict:
        return _intake(state, scope)

    async def untangle_node(state: ThreadState) -> dict:
        return await _untangle(state, scope)

    async def followup_node(state: ThreadState) -> dict:
        return await _followup(state, scope)

    graph = StateGraph(ThreadState)
    graph.add_node("intake", intake_node)
    graph.add_node("untangle", untangle_node)
    graph.add_node("followup", followup_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["untangle", "followup", END])
    graph.add_edge("untangle", END)
    graph.add_edge("followup", END)
    return graph


def _intake(state: ThreadState, scope: _Scope) -> dict:
    stored = thread_parser.Thread.from_state(state.get("thread"))
    speakers = stored.speakers if stored else ()
    plan = scope.forced or intake.plan_turn(scope.incoming, stored is not None, speakers)
    scope.plan = plan
    if plan.route == "pause":
        return {"route": "", "pause": plan.pending.to_metadata()}
    if plan.route == "reply":
        return {"route": "", "reply": _with_notes(plan.reply, plan.notes)}
    return {"route": "untangle" if plan.route == "untangle" else "followup"}


async def _untangle(state: ThreadState, scope: _Scope) -> dict:
    plan = scope.plan
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    parsed = thread_parser.parse(plan.source)
    outcome = await untangle_step.untangle(scope.deps, parsed, plan.instruction, scope.incoming.context,
                                           (*_format_notes(parsed), *plan.notes))
    if outcome.status != "ok":  # the stored thread, if any, is kept
        fixed = smalltalk.OFF_TOPIC_REPLY if outcome.status == "off_topic" else prompts.MODEL_FAILED
        return {"reply": _with_notes(fixed, plan.notes)}
    my_name = plan.my_name or state.get("my_name") or ""
    reply, env = _show(outcome.result, plan.command, my_name, parsed.speakers)
    return {"reply": reply, "envelope": env, "thread": parsed.to_state(), "result": outcome.result.to_dict(),
            "my_name": my_name, "instruction": plan.instruction}


async def _followup(state: ThreadState, scope: _Scope) -> dict:
    plan = scope.plan
    stored = thread_parser.Thread.from_state(state.get("thread"))
    result = Untangled.from_dict(state.get("result"))
    if plan.route == "name":
        reply, env = _show(result, followup.Command(), plan.my_name, stored.speakers)
        return {"reply": f"Got it, {plan.my_name}.\n\n{reply}", "envelope": env, "my_name": plan.my_name}
    if plan.route == "rerender":
        reply, env = _show(result, plan.command, state.get("my_name") or "", stored.speakers, plan.notes)
        return {"reply": reply, "envelope": env}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    if plan.route == "scope":
        return {"reply": _with_notes(await followup.scope_check(scope.deps, scope.incoming.text,
                                                                scope.incoming.context), plan.notes)}
    answer = await followup.ask(scope.deps, scope.incoming.text, stored, result, scope.incoming.context)
    return {"reply": _with_notes(answer, plan.notes)}


def _show(result: Untangled, command: followup.Command, my_name: str, speakers, notes=()) -> tuple[str, dict]:
    env = envelope.build(result, my_name, speakers)
    if command.as_json:
        return envelope.as_text(env), env
    return render.render(result, command.view, command.sources, my_name, speakers, notes), env


def _format_notes(parsed: thread_parser.Thread) -> tuple[str, ...]:
    if parsed.fmt != "paragraphs":
        return ()
    return (prompts.NOT_A_THREAD,) if len(parsed.content) <= 1 else (prompts.PARAGRAPHS_NOTE,)


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
