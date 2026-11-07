"""One turn's work: analyze newly pasted IaC, resume from the paste question, or answer a follow-up on
the stored result. Only a new analysis (narrate) and an unrecognised message (scope check) call the model."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import analysis
import detect
import envelope
import followup
import hitl
import llm
import narrate
import options
import prompts
import render
import smalltalk
from inputs import Incoming, split
from llm import TurnDeps

MAX_STORED_SOURCE = 200_000
NOT_IAC_MIN_CHARS = 300
RECOMPUTED = "_Recomputed in code with no model call; the summary is the code template._"
_IAC_WORDS = re.compile(r"\b(?:terraform|cloudformation|cfn|hcl|iac|plan|template|tf|cost[- ]?check|estimate|"
                        r"infra(?:structure)?|stack|monthly cost|price|pricing)\b", re.IGNORECASE)


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None
    stored: dict | None = None


def reply(text: str, notes: tuple[str, ...] = ()) -> TurnResult:
    return TurnResult("\n\n".join(p for p in (text, *notes) if p))


def finish(result: Mapping[str, Any], incoming: Incoming, view: str = "default", stored: dict | None = None,
           notes: tuple[str, ...] = ()) -> TurnResult:
    data = envelope.build(result)
    text = render.as_json(data) if incoming.wants_json else render.render(result, view, (*notes, *incoming.notes))
    return TurnResult(text, data=data, stored=stored)


async def analyze(incoming: Incoming, deps: TurnDeps, source: str, instruction: str) -> TurnResult:
    parsed = detect.parse_source(prompts.strip_markers(source))
    if parsed is None:
        return reply(prompts.NOT_IAC, incoming.notes)
    if not parsed["resources"] and not parsed.get("before"):
        return reply(prompts.NO_RESOURCES, tuple(parsed["notes"]) + incoming.notes)
    if not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    opts = options.merge(None, options.parse(instruction))
    result = analysis.run(parsed, opts, source)
    narrated = await narrate.narrate(deps, result, instruction, incoming.context, source)
    if narrated.status == "off_topic":
        return reply(smalltalk.OFF_TOPIC_REPLY)
    kept = source if len(source) <= MAX_STORED_SOURCE else ""
    stored = {"parsed": parsed, "opts": opts, "source": kept, "result": narrated.result}
    return finish(narrated.result, incoming, stored=stored)


async def resume(pending: hitl.Pending, incoming: Incoming, deps: TurnDeps) -> TurnResult:
    if not incoming.file_text and hitl.is_cancel(incoming.text):
        return reply(prompts.CANCELLED)
    instruction, source = split(incoming.text)
    source = "\n".join(s for s in (source, incoming.file_text) if s)
    if not source:
        return reply(prompts.NOT_IAC, incoming.notes)
    return await analyze(incoming, deps, source, " ".join(s for s in (pending.instruction, instruction) if s))


def parse_classify(data: Any) -> dict | None:
    if isinstance(data, Mapping) and data.get("status") in ("off_topic", "in_scope"):
        return {"status": data["status"]}
    return None


async def classify(deps: TurnDeps, incoming: Incoming, has_result: bool) -> str | None:
    """off_topic | in_scope, or None when no model call could be made."""
    if not deps.traceparent:
        return None
    try:
        parsed = await llm.complete_json(deps, prompts.classify_messages(incoming.text, has_result, incoming.context),
                                         prompts.CLASSIFY_CONTRACT, parse_classify)
    except llm.LlmError:
        return None
    return parsed["status"]


async def nothing_stored(incoming: Incoming, deps: TurnDeps) -> TurnResult:
    """No IaC in the message and no stored result: ask for the paste, unless it is out of scope."""
    if len(incoming.text) >= NOT_IAC_MIN_CHARS and "\n" in incoming.text.strip():
        return reply(prompts.NOT_IAC, incoming.notes)
    if not _IAC_WORDS.search(incoming.text):
        status = await classify(deps, incoming, False)
        if status is None and not deps.traceparent:
            return reply(prompts.NO_TRACE_CONTEXT)
        if status != "in_scope":
            return reply(smalltalk.OFF_TOPIC_REPLY)
    instruction = prompts.strip_markers(incoming.text)[:300]
    return TurnResult("", pause=hitl.Pending(hitl.NEED_SOURCE, instruction))


async def on_stored(incoming: Incoming, deps: TurnDeps, stored: Mapping[str, Any]) -> TurnResult:
    result = stored["result"]
    command = followup.parse(incoming.text)
    if command is None and incoming.wants_json:
        command = followup.Command("view")
    if command is None:
        answer = followup.about_line(incoming.text, result)
        if answer is not None:
            return reply(answer, incoming.notes)
        status = await classify(deps, incoming, True)
        if status is None and not deps.traceparent:
            return reply(prompts.NO_TRACE_CONTEXT)
        if status != "in_scope":
            return reply(smalltalk.OFF_TOPIC_REPLY)
        return reply(prompts.FOLLOWUPS, incoming.notes)
    if command.kind == "view":
        return finish(result, incoming, command.view)
    opts = options.merge(stored.get("opts"), command.changes)
    fresh = narrate.with_template(analysis.run(stored["parsed"], opts, stored.get("source") or ""))
    return finish(fresh, incoming, command.view, stored={**stored, "opts": opts, "result": fresh}, notes=(RECOMPUTED,))
