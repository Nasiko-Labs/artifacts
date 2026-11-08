"""The tickets step: open support-desk tickets in the window with their comments, organization tags and
reopen counts, read by a bounded tool loop and post-checked in code (verify.check_tickets). Then the
window filter and the most-recently-updated cap, both in code."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import coverage
import prompts
import records
import replies
import verify
from deps import TurnDeps
from discovery import discover
from facts import parse_time
from reader import TurnReader
from records import Ticket

log = logging.getLogger("escalation.tickets")

TICKET_LOOP_CALLS = 6  # plus one JSON repair = 7


@dataclass(frozen=True)
class TicketsOutcome:
    """Either a reply that ends the turn, or the checked tickets with what the checks did."""

    reply: str | None = None
    tickets: tuple[Ticket, ...] = ()
    extracted: int = 0
    dropped: int = 0
    capped: bool = False
    salvaged: bool = False
    notes: tuple[str, ...] = ()


async def tickets_step(deps: TurnDeps, turn: TurnReader, days: int) -> TicketsOutcome:
    catalog, failure = await discover(deps, "tickets")
    if failure is not None:
        return TicketsOutcome(replies.NO_DESK_PASTE_HINT if failure == replies.NO_TOOLS else failure)
    tools = catalog.source_tools("desk")
    log.info("desk tools offered: %s", [t.name for t in tools])
    desk = coverage.assess("desk", tools, catalog.source_of)
    if not desk.ok:
        return TicketsOutcome(replies.NO_DESK_PASTE_HINT)
    w = prompts.window(deps.today, days)
    cfg = deps.config
    run = await turn.run_json(deps, tools, prompts.tickets_system(w, cfg.max_tickets, cfg.max_comments),
                              f"Read the open support tickets updated since {w.since.isoformat()}.",
                              TICKET_LOOP_CALLS, cfg.ticket_tool_calls, records.parse_tickets)
    desk = coverage.assess("desk", tools, catalog.source_of, turn.health.dropped)
    if not desk.ok:
        return TicketsOutcome(f"{replies.NO_DESK_PASTE_HINT}\n\n{coverage.line(desk)}")
    if run.status == "paused":
        return TicketsOutcome(replies.READ_NEEDS_APPROVAL)
    if run.parsed is None:
        return TicketsOutcome(replies.STEP_LIMIT_NOTE if run.status in ("limit", "max_iterations")
                              else replies.UNREADABLE_ANSWER)
    checked = verify.check_tickets(run.parsed, turn.seen_text, cfg.max_comments)
    if checked.dropped:
        log.info("ticket check dropped %d candidate(s)", checked.dropped)
    kept, capped = select(checked.kept, w.since, cfg.max_tickets)
    return TicketsOutcome(tickets=kept, extracted=len(run.parsed), dropped=checked.dropped,
                          capped=capped or run.capped, salvaged=run.salvaged, notes=run.notes)


def _last_touch(ticket: Ticket):
    return parse_time(ticket.updated_at) or parse_time(ticket.created_at)


def select(tickets: tuple[Ticket, ...], since: date, most: int) -> tuple[tuple[Ticket, ...], bool]:
    """Tickets created or updated in the window (unknown dates kept), the most recently updated first,
    capped at `most`. Returns the kept tickets and whether the cap cut any."""
    touched = [(t, _last_touch(t)) for t in tickets]
    in_window = [(t, when) for t, when in touched if when is None or when.date() >= since]
    ordered = sorted(in_window, key=lambda pair: (pair[1] is None, -pair[1].timestamp() if pair[1] else 0.0))
    return tuple(t for t, _ in ordered[:most]), len(ordered) > most
