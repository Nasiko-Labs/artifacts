"""A turn with records: period filter, limits, pauses, the model-backed pipeline, then the code-rendered reply.
"Add these" processes only the new records and merges them into the stored result."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import attribute
import envelope
import options
import pipeline
import prompts
import render
import view as view_mod
from inputs import Incoming
from llm import TurnDeps
from parse_records import Parsed
from pauses import NO_PEOPLE, OVER_CAP, Pending
from pipeline import Stored
from records import counters_of, renumber

ADD = re.compile(r"\b(?:add (?:these|this|the following|more)|also add|append|more records|here are more)\b",
                 re.IGNORECASE)
RANKING = re.compile(r"\b(?:who(?:'s| is| was| are)? (?:the )?(?:best|top|strongest|weakest|most|least)|"
                     r"who (?:\w+ )?(?:contributed|did|shipped|helped|worked) (?:the )?(?:most|least|more)|"
                     r"rank\w*|leaderboard|scor(?:e|es|ing)|rate (?:them|my|each|the)|rating|compare|comparison|"
                     r"top (?:contributor|performer|engineer)s?|best (?:engineer|performer|contributor)|"
                     r"mvp|most valuable|stack rank)\b", re.IGNORECASE)
REVIEW = re.compile(r"\b(?:write|draft)\b.{0,30}\breviews?\b", re.IGNORECASE)
ALL_DUPLICATES = "Those records are all already collected; nothing new was added."
NO_ROOM = f"The stored result already has {options.MAX_RECORDS} records; start a new conversation for more."


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None
    stored: dict | None = None      # Stored.to_state() to checkpoint
    pause: Pending | None = None


def reply(text: str, notes: tuple[str, ...] = ()) -> TurnResult:
    return TurnResult("\n\n".join(p for p in (text, *notes) if p))


def parse_notes(parsed: Parsed) -> list[str]:
    out = []
    if parsed.unrecognized:
        out.append(f"{parsed.unrecognized} line{'s were' if parsed.unrecognized != 1 else ' was'} not recognised "
                   "as a record and skipped.")
    if parsed.dropped:
        out.append(f"{parsed.dropped} row{'s' if parsed.dropped != 1 else ''} without a title skipped.")
    if parsed.duplicates:
        out.append(f"{parsed.duplicates} duplicate PR{'s' if parsed.duplicates != 1 else ''} (same number or link) "
                   "counted once.")
    return out


def instruction_notes(instruction: str) -> tuple[str, ...]:
    notes = [prompts.RANKING_NOTE] if RANKING.search(instruction) else []
    return (*notes, prompts.NO_REVIEWS_NOTE) if REVIEW.search(instruction) else tuple(notes)


async def run_paste(incoming: Incoming, deps: TurnDeps, parsed: Parsed, prior: Stored | None, *, add: bool = False,
                    use_recent: bool = False, names_given: bool = False) -> TurnResult:
    if not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    if len(incoming.text) > options.MAX_CHARS:
        return reply(prompts.TOO_LONG)
    add = add and prior is not None
    records, notes = options.by_period(parsed.records, parsed.options, deps.today)
    if add:
        seen = set().union(*(r.keys for r in prior.records))
        records = [r for r in records if not (r.keys & seen)]
        if not records:
            return reply(ALL_DUPLICATES)
    elif len(records) < options.MIN_RECORDS:
        return reply(prompts.TOO_FEW, incoming.notes)
    held = len(prior.records) if add else 0
    if held + len(records) > options.MAX_RECORDS:
        if not use_recent:
            return TurnResult("", pause=Pending(OVER_CAP, incoming.text, held + len(records), add))
        room = options.MAX_RECORDS - held
        if room <= 0:
            return reply(NO_ROOM)
        notes.append(f"Only the {room} most recent records were used ({len(records) - room} left out).")
        records = options.most_recent(records, room)
    alias_map = {**(prior.aliases if add else {}), **parsed.aliases}
    if not names_given and not attribute.has_any_name(records):
        return TurnResult("", pause=Pending(NO_PEOPLE, incoming.text, len(records), add))
    records = renumber(records, counters_of(prior.records) if add else None)
    view = view_mod.View(people=options.people(parsed.options), min_examples=options.min_examples(parsed.options))
    if add and not parsed.options:
        view = prior.view
    new = await pipeline.process(deps, records, alias_map, view.people, incoming.context,
                                 prior.records if add else ())
    new = replace(new, view=view, notes=(*parse_notes(parsed), *notes))
    stored = replace(prior.merged_with(new), view=view) if add else new
    return finish(stored, incoming, view, (*instruction_notes(parsed.instruction), *incoming.notes))


def finish(stored: Stored, incoming: Incoming, view: view_mod.View, notes: tuple[str, ...] = ()) -> TurnResult:
    shown = view_mod.apply(pipeline.result(stored), view)
    data = envelope.build(shown)
    text = render.as_json(data) if incoming.wants_json else render.render(shown, view, notes)
    return TurnResult(text, data, stored.to_state())
