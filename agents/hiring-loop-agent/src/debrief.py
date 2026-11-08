"""From checked feedback blocks to the debrief: tidy the blocks in code (compensation lines removed,
duplicate submissions resolved, at most 8 interviewers), transcribe each interviewer, analyse in code,
label the prefiltered claim pairs, render."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

import analysis
import render
from checks import redact
from deps import TurnDeps
from extract import CHUNK_CHARS, label_pairs, transcribe
from records import Block, DebriefReport
from request import Request

log = logging.getLogger("hiring_loop.debrief")

MAX_INTERVIEWERS = 8
LONG_FEEDBACK_CHARS = 40_000


@dataclass(frozen=True)
class Outcome:
    reply: str
    data: dict
    report: str


def tidy(blocks: Sequence[Block]) -> tuple[tuple[Block, ...], tuple[str, ...]]:
    """(blocks to read, notes): compensation lines removed, one submission per interviewer (the latest by date
    when every copy has one, else the last given), at most MAX_INTERVIEWERS in alphabetical order."""
    notes: list[str] = []
    redacted_total = 0
    by_name: dict[str, list[Block]] = {}
    for block in blocks:
        clean, removed = redact(block.text)
        redacted_total += removed
        by_name.setdefault(block.interviewer.casefold(), []).append(replace(block, text=clean))
    kept = []
    for copies in by_name.values():
        if len(copies) > 1:
            dated = all(c.date for c in copies)
            chosen = max(copies, key=lambda c: c.date) if dated else copies[-1]
            notes.append(f"{chosen.interviewer} submitted feedback {len(copies)} times; the "
                         f"{'latest' if dated else 'last one given'} is used.")
            kept.append(chosen)
        else:
            kept.append(copies[0])
    ordered = sorted(kept, key=lambda b: b.interviewer.casefold())
    if len(ordered) > MAX_INTERVIEWERS:
        left = ", ".join(b.interviewer for b in ordered[MAX_INTERVIEWERS:])
        notes.append(f"Only {MAX_INTERVIEWERS} interviewers are read per run; not covered: {left}.")
        ordered = ordered[:MAX_INTERVIEWERS]
    total = sum(len(b.text) for b in ordered)
    if total > LONG_FEEDBACK_CHARS:
        parts = sum(max(1, -(-len(b.text) // CHUNK_CHARS)) for b in ordered)
        notes.append(f"The feedback was {total:,} characters, so it was read one interviewer at a time "
                     f"({parts} parts); every interviewer above was read in full.")
    if redacted_total:
        notes.append(f"{redacted_total} compensation line(s) were removed before reading.")
    return tuple(ordered), tuple(notes)


async def build(deps: TurnDeps, blocks: Sequence[Block], req: Request, candidate: str,
                coverage_notes: Sequence[str] = (), model_split: bool = False,
                any_source_failed: bool = False) -> Outcome:
    tidied, notes = tidy(blocks)
    records = []
    for block in tidied:
        if deps.past_deadline():
            notes = (*notes, f"Not read (time limit): {block.interviewer}.")
            continue
        records.append(await transcribe(deps, block))
    records = tuple(records)
    report = await _analyse(deps, records, tidied, req, candidate, model_split)
    report = replace(report, coverage=tuple(coverage_notes), notes=(*report.notes, *notes))
    text, scrubbed = render.markdown(report)
    data = render.envelope(report, text, any_source_failed, scrubbed)
    log.info("debrief built: %d interviewers, %d contradictions, %d gaps, %d flags", len(records),
             len(report.contradictions), len(report.gaps), len(report.quality_flags))
    return Outcome(render.as_json(data) if req.wants_json else text, data, text)


async def _analyse(deps: TurnDeps, records: tuple, blocks: Sequence[Block], req: Request, candidate: str,
                   model_split: bool) -> DebriefReport:
    found = [c for c in (analysis.recommendation_split(records),) if c is not None]
    found += analysis.competency_conflicts(records)
    overall = {frozenset(c.interviewers) for c in found}
    # Two interviewers who sat one interview and disagree overall are one conflict, not two.
    found += [c for c in analysis.pair_interview_conflicts(records) if frozenset(c.interviewers) not in overall]
    pairs, over_cap = analysis.claim_pairs(records)
    labels = await label_pairs(deps, pairs) if pairs and not deps.past_deadline() else {}
    found += analysis.claim_conflicts(pairs, labels, found)
    notes = (f"{over_cap} more opposite-claim pairs were not checked (limit {analysis.MAX_PAIRS}).",) if over_cap else ()
    sources = tuple(dict.fromkeys(b.source for b in blocks))
    return DebriefReport(
        candidate=candidate, role=req.role, records=records, contradictions=tuple(found),
        agreements=analysis.agreements(records), gaps=analysis.gaps(records, req.expected, req.panel),
        quality_flags=analysis.quality_flags(blocks), sources=sources, model_split=model_split, notes=notes)
