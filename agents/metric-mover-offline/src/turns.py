"""The graph's node bodies: analyse a table, follow up on a stored result, or scope-check a message that came
with no table. Each returns the state update; `scope` carries this turn's input and the envelope data part,
which is never persisted on its own."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import events as ev
import followup
import hitl
import llm
import pipeline
import prompts
import render
import request as rq
import smalltalk
from frame import Frame
from inputs import Incoming
from intake import Plan
from text import cell

FIXED = {"off_topic": smalltalk.OFF_TOPIC_REPLY, "too_few": prompts.TOO_FEW_SEGMENTS,
         "no_change": prompts.NO_CHANGE}


@dataclass
class Scope:
    deps: llm.TurnDeps
    incoming: Incoming
    plan: Plan | None = None
    data: dict | None = None
    notes: list[str] = field(default_factory=list)


def _reply(scope: Scope, envelope: dict, view: str = "default") -> str:
    scope.data = envelope
    if scope.incoming.wants_json:
        return render.as_json(envelope)
    return render.render(envelope, view, scope.incoming.text, extra_notes=scope.notes)


def _year(frame: Frame) -> int | None:
    span = ev.window(frame.labels)
    return span[0].year if span else None


async def _run(scope: Scope, frame: Frame, request: rq.Request, events: list[ev.Event]) -> dict:
    outcome = await pipeline.run(scope.deps, frame, request, events, scope.incoming.context, scope.incoming.text,
                                 scope.incoming.text)
    if outcome.status != "ok":
        return {"reply": FIXED[outcome.status]}
    return {"reply": _reply(scope, outcome.envelope), "frame": frame.to_dict(), "request": request.to_dict(),
            "events": [e.to_dict() for e in events], "envelope": outcome.envelope}


async def analyze(state: Mapping[str, Any], scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    frame = scope.plan.frame
    request = rq.parse(scope.plan.text, _year(frame))
    return await _run(scope, frame, request, list(request.events))


async def follow(state: Mapping[str, Any], scope: Scope) -> dict:
    plan = scope.plan
    if not state.get("envelope") or not state.get("frame"):
        return {"reply": prompts.NO_TABLE_STORED}
    envelope = state["envelope"]
    if plan.route == "rerender":
        return {"reply": _reply(scope, envelope, plan.view)}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    frame = Frame.from_dict(state["frame"])
    request = rq.Request.from_dict(state.get("request"))
    events = [ev.Event.from_dict(e) for e in state.get("events") or []]
    if plan.route == "add_event":
        added = ev.parse(plan.text, len(events) + 1, _year(frame))
        if not added:
            return {"reply": "I couldn't read an event in that. Try: add event: Oct 14 deploy of the new checkout"}
        return await _run(scope, frame, request, [*events, *added])
    if plan.route == "ignore":
        if not plan.targets:
            names = ", ".join(sorted(frame.segment_names())[:20])
            return {"reply": f"I couldn't find that segment in the table. Segments: {cell(names)}."}
        return await _run(scope, frame.without(plan.targets), request, events)
    return {"reply": await followup.ask(scope.deps, plan.text, envelope, scope.incoming.context)}


async def scope_check(state: Mapping[str, Any], scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    try:
        parsed = await llm.complete_json(scope.deps, prompts.scope_messages(scope.plan.text, scope.incoming.context),
                                         prompts.SCOPE_CONTRACT, _scope_status)
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if parsed["status"] == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return {"pause": hitl.Pending(hitl.NEED_SOURCE, question=scope.plan.text).to_metadata()}


def _scope_status(data: Any) -> dict | None:
    if isinstance(data, Mapping) and data.get("status") in ("off_topic", "needs_table"):
        return {"status": data["status"]}
    return None
