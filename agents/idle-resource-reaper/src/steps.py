"""The owner lookup: one bounded tool loop (8 calls + 1 JSON repair) over the code host and chat tools
this request discovered, for the unknown-owner resources (top 20 by cost) and the tag owners with no chat
id. Every owner it returns is checked in code against this turn's tool output (owners.py). It degrades,
never fails the turn: no trace context, no gateway, no tools, a dead tool or an unreadable answer leave
the owners from tags, with one coverage line saying why."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import coverage
import owners
import prompts
from deps import TurnDeps
from discovery import NO_TOOLS, discover
from findings import Row
from reader import Reader, ask_once

log = logging.getLogger("reaper.steps")

OWNER_CALLS = 8
MAX_LOOKUP_RESOURCES = 20
TIME_BUDGET_SECS = 200  # owner lookup is skipped once a turn has run this long (turn deadline 300 s)
_PAUSED = ("approval_pending", "authorization_required")
READ_NEEDS_APPROVAL = "A lookup tool is set to require approval, so owners were not looked up this time."
UNREADABLE_LOOKUP = "The owner lookup gave no answer I could verify, so owners come from tags only."


@dataclass(frozen=True)
class LookupOutcome:
    rows: tuple[Row, ...]
    notes: tuple[str, ...] = ()
    dropped: int = 0          # owners the model gave that failed the verbatim check
    complete: bool = True     # False when the lookup was wanted but did not run to a checked answer


async def lookup_owners(deps: TurnDeps, rows: Sequence[Row]) -> LookupOutcome:
    rows = tuple(rows)
    candidates = owners.lookup_candidates(rows, MAX_LOOKUP_RESOURCES)
    people = owners.contact_candidates(rows)
    if not candidates and not people:
        return LookupOutcome(rows)
    if not deps.traceparent:
        return LookupOutcome(rows, (prompts.NO_TRACE_NOTE,), complete=False)
    if deps.elapsed() > TIME_BUDGET_SECS:
        return LookupOutcome(rows, (prompts.TIME_SKIPPED_NOTE,), complete=False)
    wanted = (*(("code",) if candidates else ()), *(("chat",) if people else ()))
    catalog, failure = await discover(deps, "read")
    if failure is not None:
        missing = {s: coverage.SourceStatus(s, "missing") for s in coverage.SOURCES}
        note = coverage.notes(missing, wanted) if failure == NO_TOOLS else (failure,)
        return LookupOutcome(rows, note, complete=False)
    reader = Reader.over(deps, catalog)
    tools = reader.owner_tools()
    if not tools:
        return LookupOutcome(rows, coverage.notes(reader.statuses(), wanted), complete=False)
    log.info("owner lookup tools offered: %s", [t.name for t in tools])
    return await _run(deps, reader, tools, rows, candidates, people, wanted)


async def _run(deps: TurnDeps, reader: Reader, tools, rows: tuple[Row, ...], candidates: Sequence[Row],
               people: Sequence[str], wanted: Sequence[str]) -> LookupOutcome:
    listing = json.dumps({"resources": [{"resource_id": r.resource_id, "name": r.name or None,
                                         "kind": r.kind, "region": r.region or None} for r in candidates],
                          "people": list(people)}, ensure_ascii=False)
    system = prompts.owners_system(deps.today)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": prompts.fence_candidates(listing)})
    result = await reader.loop(deps, messages, tools, OWNER_CALLS)
    notes = coverage.notes(reader.statuses(), wanted)
    if result.status in _PAUSED:
        return LookupOutcome(rows, (*notes, READ_NEEDS_APPROVAL), complete=False)
    hit_limit = result.status == "max_iterations"
    if hit_limit:
        notes = (*notes, prompts.STEP_LIMIT_NOTE)
    seen = reader.fenced.seen_text
    parsed = None if hit_limit else owners.parse_lookup(result.text)
    if parsed is None and seen:
        parsed = await _repair(deps, messages, result.text, seen)
    if parsed is None:
        return LookupOutcome(rows, (*notes, UNREADABLE_LOOKUP) if not hit_limit else notes, complete=False)
    code_owners, dropped = owners.check_code_owners(parsed[0], [r.resource_id for r in candidates], seen)
    contacts = owners.check_contacts(parsed[1], people, seen)
    if dropped:
        log.info("dropped %d owner(s) without verbatim evidence in tool output", dropped)
    return LookupOutcome(owners.apply_lookup(rows, code_owners, contacts), notes, dropped, not hit_limit)


async def _repair(deps: TurnDeps, messages, answer: str, seen: str):
    """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
    log.info("owner lookup answer was not the agreed JSON; asking once for a rewrite")
    repair = (*messages, {"role": "user", "content": f"Tool results from this turn:\n{prompts.fence(seen)}\n\n"
                                                      f"Your previous answer:\n{prompts.fence(answer)}\n\n"
                                                      f"{prompts.REPAIR_INSTRUCTION}"})
    return owners.parse_lookup(await ask_once(deps, repair))
