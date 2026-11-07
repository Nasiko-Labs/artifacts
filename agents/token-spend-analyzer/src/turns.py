"""One turn's work: analyze a new export (with the column question when a role can't be resolved), or resume from
an answer. Follow-ups on a stored analysis live in followup_turn.py."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import analysis
import detect
import envelope
import followup
import hitl
import prices
import prompts
import recommend
import render
import smalltalk
from frame import Frame, from_canonical_csv, normalise, to_canonical_csv
from inputs import Incoming, kind_of, split
from llm import TurnDeps

MAX_CANONICAL_CHARS = 2_000_000
DEFAULT_REQUEST = "Analyze this usage export."


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None
    stored: dict | None = None          # {"canonical": {...}, "result": {...}} to checkpoint


def reply(text: str, notes: tuple[str, ...] = ()) -> TurnResult:
    return TurnResult("\n\n".join(p for p in (text, *notes) if p))


def options_from(instruction: str) -> analysis.Options:
    command = followup.parse(instruction) if instruction else None
    top = command.top if command and command.top else analysis.aggregate.TOP_N
    return analysis.Options(top=top, instruction=instruction)


def store_frame(frame: Frame) -> dict:
    text = to_canonical_csv(frame)
    meta = {k: v for k, v in frame.meta.items() if k != "price_table"}
    return {"csv": text if len(text) <= MAX_CANONICAL_CHARS else "", "shape": frame.shape, "meta": meta,
            "dims": list(frame.dims), "has_status": frame.has_status, "rows_read": frame.rows_read,
            "dropped": dict(frame.dropped)}


def load_frame(canonical: Mapping, instruction: str) -> Frame | None:
    if not canonical or not canonical.get("csv"):
        return None
    meta = {**dict(canonical.get("meta") or {}), "price_table": prices.parse_overrides(instruction)}
    return from_canonical_csv(canonical["csv"], str(canonical.get("shape") or ""), meta,
                              tuple(canonical.get("dims") or ()), bool(canonical.get("has_status")),
                              int(canonical.get("rows_read") or 0), dict(canonical.get("dropped") or {}))


async def analyze(incoming: Incoming, deps: TurnDeps, source: str, kind: str, instruction: str,
                  overrides: Mapping[str, str] | None = None, pauses: int = 0) -> TurnResult:
    kind = kind or kind_of(source)
    loaded = detect.load(source, kind, overrides)
    if loaded.error:
        return reply(prompts.NOT_READABLE, incoming.notes)
    if loaded.missing:
        asked = _ask(source, kind, instruction, loaded, overrides or {}, pauses)
        if asked is not None:
            return asked
        if loaded.missing == "cost":
            return reply(prompts.NO_COLUMN, incoming.notes)
        loaded = detect.load(source, kind, {**(overrides or {}), "model": detect.NONE_OPTION})
    if not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    frame = normalise(loaded, prices.parse_overrides(instruction))
    if frame.df.empty:
        return reply(prompts.NO_ROWS, incoming.notes)
    result = analysis.run(frame, options_from(instruction))
    rec = await recommend.recommend(deps, result, instruction or DEFAULT_REQUEST, incoming.context, source)
    if rec.status == "off_topic":
        return reply(smalltalk.OFF_TOPIC_REPLY)
    return finish(rec.result, incoming, stored={"canonical": store_frame(frame), "result": rec.result})


def finish(result: dict, incoming: Incoming, view: str = "default", stored: dict | None = None,
           notes: tuple[str, ...] = (), what_if: dict | None = None) -> TurnResult:
    data = envelope.build(result, what_if)
    if incoming.wants_json:
        text = render.as_json(data)
    else:
        text = render.render(result, view, [*notes, *incoming.notes])
    return TurnResult(text, data=data, stored=stored)


def _ask(source: str, kind: str, instruction: str, loaded: detect.Loaded, overrides: Mapping[str, str],
         pauses: int) -> TurnResult | None:
    stored = hitl.storable(source)
    if pauses >= hitl.MAX_PAUSES or not stored or not loaded.candidates:
        return None
    return TurnResult("", pause=hitl.Pending(hitl.COLUMN, instruction, stored, kind, loaded.missing,
                                             loaded.candidates, dict(overrides), pauses + 1))


async def resume(pending: hitl.Pending, incoming: Incoming, deps: TurnDeps) -> TurnResult:
    if pending.kind == hitl.NEED_SOURCE:
        answer = hitl.interpret(pending, incoming.text, [])
        if answer.action == "cancel":
            return reply(prompts.CANCELLED)
        req = split(answer.text)
        source = incoming.file_text or req.source
        if not source:
            return reply(prompts.NOT_READABLE)
        kind = kind_of(source) if incoming.file_text else req.kind
        return await analyze(incoming, deps, source, kind, pending.instruction or req.instruction,
                             pauses=pending.pauses)
    loaded = detect.load(pending.source, pending.source_kind, pending.overrides)
    headers = [str(c) for c in loaded.df.columns] if loaded.df is not None else []
    answer = hitl.interpret(pending, incoming.text, headers)
    if answer.action == "cancel":
        return reply(prompts.CANCELLED)
    overrides = dict(pending.overrides)
    if answer.action == "column":
        overrides[pending.role] = answer.header
    elif answer.action == "none" or pending.role == "model":
        overrides["model"] = detect.NONE_OPTION
    else:
        return reply(prompts.NO_COLUMN)
    return await analyze(incoming, deps, pending.source, pending.source_kind, pending.instruction, overrides,
                         pending.pauses)
