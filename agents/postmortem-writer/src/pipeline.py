"""One postmortem: parse_times (code) -> extract (LLM, map) -> check + merge (code) -> metrics (code) ->
write (LLM) -> blameless pass -> assemble (code). Budget: <= 4 calls single pass, <= 14 for a long log."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import assemble
import blameless
import inputs
import llm
import metrics as metrics_step
import write
from extract import extract
from records import Postmortem
from times import parse_lines, speakers


@dataclass(frozen=True)
class Outcome:
    status: str                       # ok | off_topic | not_incident | failed | nothing
    postmortem: Postmortem | None = None
    notes: tuple[str, ...] = ()


async def run(deps: llm.TurnDeps, log: str, instruction: str, context: Sequence[str] = ()) -> Outcome:
    lines = parse_lines(log)
    names = speakers(lines)
    extracted = await extract(deps, lines, instruction, names, context)
    if extracted.status != "ok":
        return Outcome(extracted.status, notes=extracted.notes())
    merged = extracted.merged
    if not merged.events:
        return Outcome("nothing", notes=extracted.notes())
    metrics = metrics_step.compute(merged.events)
    haystack = "\n".join((log, instruction, *metrics.values.values()))
    prose = await write.write(deps, merged, metrics, instruction, haystack, context)
    prose, factors, _ = await blameless.apply(deps, prose, merged.factors, merged.events, names, haystack)
    postmortem = assemble.build(merged, metrics, prose, factors, inputs.title_of(instruction),
                                inputs.severity_of(instruction), extracted.notes(), extracted.partial)
    return Outcome("ok", postmortem)
