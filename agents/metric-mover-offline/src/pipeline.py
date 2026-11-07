"""One analysis: decompose (code) → evidence (code) → candidates (code) → hypotheses (1 model call, + 1
repair at most) → checks and ranking (code) → envelope. If the model can't be reached, code's own wording of
the supported hypotheses is used, with a note."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

import analysis as an
import candidates as cands
import envelope
import events as ev
import evidence as evd
import hypotheses
import llm
from checks import check
from frame import Frame
from request import Request, metric_label, type_note

MIN_SEGMENTS = 3
MODEL_DOWN = "The language model was unavailable, so the hypotheses below use code's own wording."


@dataclass(frozen=True)
class Outcome:
    status: str                  # ok | off_topic | too_few | no_change
    envelope: dict | None = None


async def run(deps: llm.TurnDeps, frame: Frame, request: Request, events: Sequence[ev.Event],
              context: Sequence[str] = (), user_text: str = "", source: str = "") -> Outcome:
    if len(frame.rows) < MIN_SEGMENTS:
        return Outcome("too_few")
    result = await asyncio.to_thread(an.analyse, frame, metric_label(request, frame), request.min_n)
    if not result.top.conc.gross and not result.top.delta:
        return Outcome("no_change")
    evidence = evd.build(result)
    span = ev.window(frame.labels)
    used = [e for e in events if ev.in_window(e, span)]
    ignored = [e for e in events if not ev.in_window(e, span)]
    supported = cands.build(result, evidence, used)
    drafted = await hypotheses.draft(deps, result, evidence, used, supported, request.instruction, context)
    if drafted.status == "off_topic":
        return Outcome("off_topic")
    evidence_map = {e.id: e.text for e in evidence}
    effects = {e.id: e.effect for e in evidence if e.segment and e.effect}
    checked = check(list(drafted.hypotheses), supported, evidence_map, {e.id: e.text for e in used},
                    user_text, source, effects)
    notes = [type_note(request, frame), *frame.notes]
    if drafted.status == "failed":
        notes.append(MODEL_DOWN)
    built = envelope.build(result, evidence, checked, used, ignored, span is not None, notes)
    return Outcome("ok", built)
