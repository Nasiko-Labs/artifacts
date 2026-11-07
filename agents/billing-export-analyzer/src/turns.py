"""One turn's work: analyze a new export (with the column question when the header is ambiguous), or
resume from an answer. Follow-ups on a stored analysis live in followup_turn.py."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import analysis
import chart_png
import columns
import envelope
import followup
import hitl
import narrate
import prompts
import render
import smalltalk
from frame import headers_of, load, to_canonical_csv
from inputs import Incoming, split
from llm import TurnDeps
from stats import parse_budget
from windows import user_window

MAX_CANONICAL_CHARS = 500_000
DEFAULT_REQUEST = "Analyze this billing export."
PNG_UNAVAILABLE = "_PNG unavailable._"
PNG_ATTACHED = "_PNG attached as spend-by-service.png (API clients; the chat shows the text chart above)._"


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None
    png: bytes | None = None
    stored: dict | None = None          # {"canonical": {"csv": ...}, "result": {...}} to checkpoint
    notes: tuple[str, ...] = field(default_factory=tuple)


def reply(text: str, notes: tuple[str, ...] = ()) -> TurnResult:
    return TurnResult("\n\n".join(p for p in (text, *notes) if p))


def options_from(instruction: str) -> analysis.Options:
    command = followup.parse(instruction) if instruction else None
    top = command.top if command and command.top else analysis.stats.TOP_MOVERS
    return analysis.Options(window_days=user_window(instruction), top=top, instruction=instruction,
                            budget=parse_budget(instruction))


async def analyze(incoming: Incoming, deps: TurnDeps, csv_text: str, instruction: str,
                  overrides: Mapping[str, str] | None = None, pauses: int = 0, guessed: tuple[str, ...] = ()
                  ) -> TurnResult:
    headers = headers_of(csv_text)
    if len(headers) < 3:
        return reply(prompts.NOT_CSV, incoming.notes)
    cols = columns.detect(headers, overrides)
    if cols.missing:
        return _ask_or_guess(incoming, csv_text, instruction, cols, pauses, guessed)
    if not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    frame = load(csv_text, cols)
    if frame.df.empty:
        return reply(prompts.NO_ROWS, (*frame.notes, *incoming.notes))
    result = analysis.run(frame, options_from(instruction), guessed)
    narrated = await narrate.narrate(deps, result, instruction or DEFAULT_REQUEST, incoming.context, csv_text)
    if narrated.status == "off_topic":
        return reply(smalltalk.OFF_TOPIC_REPLY)
    canonical = to_canonical_csv(frame)
    stored = {"canonical": {"csv": canonical if len(canonical) <= MAX_CANONICAL_CHARS else ""},
              "result": narrated.result}
    return finish(narrated.result, incoming, stored=stored)


def finish(result: dict, incoming: Incoming, view: str = "default", stored: dict | None = None,
           notes: tuple[str, ...] = ()) -> TurnResult:
    data = envelope.build(result)
    png = None
    extra = list(notes) + list(incoming.notes)
    if incoming.wants_png:
        p = result["period"]
        png = chart_png.render(result["chart_data"], f"Spend by service, {p['start']} to {p['end']}")
        extra.append(PNG_ATTACHED if png else PNG_UNAVAILABLE)
    text = render.as_json(data) if incoming.wants_json else render.render(result, view, extra)
    return TurnResult(text, data=data, png=png, stored=stored)


def _ask_or_guess(incoming: Incoming, csv_text: str, instruction: str, cols: columns.Columns,
                  pauses: int, guessed: tuple[str, ...]):
    role = cols.missing[0]
    candidates = tuple(cols.ambiguous.get(role, ()))
    stored_csv = hitl.storable(csv_text)
    if pauses < hitl.MAX_PAUSES and stored_csv and candidates:
        overrides = {r: getattr(cols, r) for r in columns.ROLES if getattr(cols, r) and r != role}
        return TurnResult("", pause=hitl.Pending(hitl.COLUMN, instruction, stored_csv, role, candidates,
                                                 overrides, pauses + 1))
    return reply(prompts.NO_COLUMN, incoming.notes)


async def resume(pending: hitl.Pending, incoming: Incoming, deps: TurnDeps) -> TurnResult:
    if pending.kind == hitl.NEED_SOURCE:
        answer = hitl.interpret(pending, incoming.text, [])
        if answer.action == "cancel":
            return reply(prompts.CANCELLED)
        req = split(answer.text)
        source = incoming.file_text or req.csv_text
        if not source:
            return reply(prompts.NOT_CSV)
        return await analyze(incoming, deps, source, pending.instruction or req.instruction, pauses=pending.pauses)
    headers = headers_of(pending.csv_text)
    answer = hitl.interpret(pending, incoming.text, headers)
    if answer.action == "cancel":
        return reply(prompts.CANCELLED)
    overrides = dict(pending.overrides)
    guessed: tuple[str, ...] = ()
    if answer.action == "column":
        overrides[pending.role] = answer.header
    else:
        real = [c for c in pending.candidates if c in headers and _is_known(pending.role, c)]
        if not real:
            return reply(prompts.NO_COLUMN)
        overrides[pending.role], guessed = real[0], (pending.role,)
    return await analyze(incoming, deps, pending.csv_text, pending.instruction, overrides, pending.pauses, guessed)


def _is_known(role: str, header: str) -> bool:
    tier1, tier2 = columns.REQUIRED[role]
    return columns.norm(header) in tier1 | tier2
