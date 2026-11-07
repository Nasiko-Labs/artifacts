"""The tickets step: support-desk tickets linked to the bug (its links, or a search for its key) and
tickets matching its keywords, post-checked in code (checks.check_tickets). Keywords come from one
tool-less call and are kept only when they occur in the bug's own text."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import checks
import coverage
import prompts
import records
import replies
from budget import TurnLimitReached
from deps import TurnDeps
from discovery import discover
from reader import TurnReader, ask_once
from records import Bug, Ticket

log = logging.getLogger("bug_notifier.tickets")

TICKET_LOOP_CALLS = 6  # plus one JSON repair = 7; the keyword call is one more


@dataclass(frozen=True)
class TicketOutcome:
    tickets: tuple[Ticket, ...] = ()
    searched: bool = False   # the support desk could be searched at all
    dropped: int = 0
    capped: int = 0
    unquoted: int = 0
    keywords: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


async def tickets_step(deps: TurnDeps, turn: TurnReader, bug: Bug) -> TicketOutcome:
    catalog, failure = await discover(deps, "tickets")
    if failure is not None:
        return TicketOutcome(notes=(failure,))
    tools = catalog.read_tools()
    desk = coverage.assess("desk", tools, catalog.source_of)
    if not desk.ok:
        return TicketOutcome(notes=(coverage.line(desk),))
    keywords = await _keywords(deps, bug)
    system = prompts.tickets_system(bug.key, bug.remote_links, keywords)
    run = await turn.run_json(deps, tools, system, f"Find the open support tickets about {bug.key}.",
                              TICKET_LOOP_CALLS, records.parse_tickets)
    desk = coverage.assess("desk", tools, catalog.source_of, turn.health.dropped)
    if not desk.ok:
        return TicketOutcome(notes=(coverage.line(desk),), keywords=keywords)
    if run.status == "paused":
        return TicketOutcome(notes=(replies.READ_NEEDS_APPROVAL,), keywords=keywords)
    if run.parsed is None:
        notes = run.notes or (replies.UNREADABLE_ANSWER,)
        return TicketOutcome(searched=True, keywords=keywords, notes=notes)
    checked = checks.check_tickets(run.parsed, seen_text=turn.seen_text, bug=bug, keywords=keywords,
                                   today=deps.today, config=deps.config)
    if checked.dropped:
        log.info("ticket check dropped %d candidate(s)", checked.dropped)
    return TicketOutcome(checked.kept, True, checked.dropped, checked.capped, checked.unquoted, keywords, run.notes)


async def _keywords(deps: TurnDeps, bug: Bug) -> tuple[str, ...]:
    text = f"Summary: {bug.summary}\nDescription: {bug.description_excerpt}"
    messages = ({"role": "system", "content": prompts.KEYWORDS_SYSTEM},
                {"role": "user", "content": prompts.fence(text)})
    try:
        answer = await ask_once(deps, messages)
    except TurnLimitReached:
        return ()
    return checks.kept_keywords(records.parse_keywords(answer), bug)
