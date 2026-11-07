"""One turn's work on new alert data: parse (with the column question when the header is ambiguous),
classify, render; or resume from an answer to a pause. Follow-ups on a stored result live in
followup_turn.py."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import detect
import envelope
import hitl
import pipeline
import prompts
import render
import smalltalk
from inputs import Incoming
from llm import TurnDeps

MIN_FIRES = 3


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None
    stored: dict | None = None          # {"result": {...}} to checkpoint


def reply(text: str, notes: tuple[str, ...] = ()) -> TurnResult:
    return TurnResult("\n\n".join(p for p in (text, *notes) if p))


def finish(result: dict, incoming: Incoming, text: str | None = None, stored: dict | None = None,
           extra: tuple[str, ...] = ()) -> TurnResult:
    data = envelope.build(result)
    if incoming.wants_json:
        body = render.as_json(data)
    else:
        body = text if text is not None else render.full(result, (*extra, *incoming.notes))
    return TurnResult(body, data=data, stored=stored)


async def analyze(incoming: Incoming, deps: TurnDeps, data: str, instruction: str,
                  overrides: Mapping[str, str] | None = None, pauses: int = 0) -> TurnResult:
    parsed = detect.parse(data, overrides)
    if parsed.old_envelope:
        return reply(prompts.OLD_ENVELOPE, incoming.notes)
    if parsed.missing:
        return _ask_column(incoming, data, instruction, parsed, overrides or {}, pauses)
    if not parsed.fires:
        return reply(prompts.NO_ROWS, incoming.notes)
    if len(parsed.fires) < MIN_FIRES:
        return reply(f"{prompts.TOO_FEW}\n\n{smalltalk.INTRO}", incoming.notes)
    if not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    result = {**await pipeline.run(parsed, data, deps, incoming.context), "instruction": instruction}
    return finish(result, incoming, stored={"result": result})


def _ask_column(incoming: Incoming, data: str, instruction: str, parsed: detect.Parsed,
                overrides: Mapping[str, str], pauses: int) -> TurnResult:
    stored = hitl.storable(data)
    if pauses >= hitl.MAX_PAUSES or not stored or not parsed.headers:
        return reply(prompts.NO_COLUMN, incoming.notes)
    return TurnResult("", pause=hitl.Pending(hitl.COLUMN, instruction, stored, parsed.missing, parsed.headers,
                                             dict(overrides), pauses + 1))


async def resume(pending: hitl.Pending, incoming: Incoming, deps: TurnDeps) -> TurnResult:
    answer = hitl.interpret(pending, incoming.text)
    if answer.action == "cancel":
        return reply(prompts.CANCELLED)
    if pending.kind == hitl.NEED_SOURCE:
        req = detect.split(answer.text)
        source = incoming.file_text or req.data
        if not source:
            return reply(prompts.NO_ROWS)
        return await analyze(incoming, deps, source, pending.instruction or req.instruction, pauses=pending.pauses)
    if answer.action != "column":
        return reply(prompts.NO_COLUMN)
    overrides = {**pending.overrides, pending.role: answer.header}
    return await analyze(incoming, deps, pending.data, pending.instruction, overrides, pending.pauses)
