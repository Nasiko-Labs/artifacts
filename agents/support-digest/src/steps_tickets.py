"""The ticket steps. `current`: tickets created in the period, read by a bounded tool loop and post-checked
in code (verify.check_tickets), then the period filter, the request's filters and the 300-ticket cap, all in
code. `previous`: one count of the previous period, for the trend, verified against the tool output."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import coverage
import prompts
import records
import replies
import stats
import verify
from deps import TurnDeps
from discovery import discover
from nasiko_mcp import McpTool
from period import Period
from reader import TurnReader
from records import Ticket, parse_time
from request import Filters

log = logging.getLogger("support_digest.tickets")

CURRENT_LOOP_CALLS = 6   # plus one JSON repair = 7
PREVIOUS_LOOP_CALLS = 2


@dataclass(frozen=True)
class CurrentOutcome:
    """Either a reply that ends the turn, or the checked tickets with what the checks did."""

    reply: str | None = None
    tickets: tuple[Ticket, ...] = ()
    tools: tuple[McpTool, ...] = ()
    reported_total: int | None = None
    extracted: int = 0
    dropped: int = 0
    capped: bool = False
    salvaged: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreviousOutcome:
    count: int | None = None
    sampled: bool = False


def newest(tickets: Sequence[Ticket], most: int) -> tuple[tuple[Ticket, ...], bool]:
    """The newest `most` tickets by creation time; whether the cap cut any."""
    ordered = sorted(tickets, key=lambda t: (-(parse_time(t.created_at).timestamp()), t.id))
    return tuple(ordered[:most]), len(ordered) > most


def select(tickets: Sequence[Ticket], p: Period, filters: Filters, most: int) -> tuple[tuple[Ticket, ...], bool]:
    return newest([t for t in stats.in_period(tickets, p) if filters.keeps(t)], most)


async def current_step(deps: TurnDeps, turn: TurnReader, p: Period, filters: Filters) -> CurrentOutcome:
    catalog, failure = await discover(deps, "tickets")
    if failure is not None:
        return CurrentOutcome(replies.NO_DESK_PASTE_HINT if failure == replies.NO_TOOLS else failure)
    tools = catalog.source_tools("desk")
    log.info("desk tools offered: %s", [t.name for t in tools])
    if not coverage.assess("desk", tools, catalog.source_of).ok:
        return CurrentOutcome(replies.NO_DESK_PASTE_HINT)
    cfg = deps.config
    run = await turn.run_json(deps, tools, prompts.current_system(p, cfg.max_tickets, filters.describe()),
                              f"Read the support tickets created from {p.start.isoformat()} to {p.end.isoformat()}.",
                              CURRENT_LOOP_CALLS, cfg.current_tool_calls, records.parse_tickets)
    desk = coverage.assess("desk", tools, catalog.source_of, turn.health.dropped)
    if not desk.ok:
        return CurrentOutcome(f"{replies.NO_DESK_PASTE_HINT}\n\n{coverage.line(desk)}")
    if run.status == "paused":
        return CurrentOutcome(replies.READ_NEEDS_APPROVAL)
    if run.parsed is None:
        return CurrentOutcome(replies.STEP_LIMIT_NOTE if run.status in ("limit", "max_iterations")
                              else replies.UNREADABLE_ANSWER)
    found, total = run.parsed
    checked = verify.check_tickets(found, turn.seen_text)
    if checked.dropped:
        log.info("ticket check dropped %d candidate(s)", checked.dropped)
    kept, capped = select(checked.kept, p, filters, cfg.max_tickets)
    # The desk's own total only stands in for the count when the sample is cut (cap or call budget).
    cut = capped or run.capped or len(kept) >= cfg.max_tickets
    reported = verify.check_total(total, turn.seen_text) if cut and not filters.active else None
    return CurrentOutcome(tickets=kept, tools=tools, reported_total=reported, extracted=len(found),
                          dropped=checked.dropped, capped=capped or run.capped, salvaged=run.salvaged,
                          notes=run.notes)


async def previous_step(deps: TurnDeps, turn: TurnReader, tools: Sequence[McpTool], p: Period,
                        filters: Filters) -> PreviousOutcome:
    """The previous period's count: the desk's reported total when no filter is set; otherwise (or when no
    total was reported) the verified tickets it returned, counted and filtered in code."""
    before = len(turn.seen_text)
    run = await turn.run_json(deps, tools, prompts.previous_system(p, filters.describe()),
                              f"Count the support tickets created from {p.start.isoformat()} to {p.end.isoformat()}.",
                              PREVIOUS_LOOP_CALLS, deps.config.previous_tool_calls, records.parse_previous)
    if run.parsed is None:
        return PreviousOutcome()
    seen = turn.seen_text[before:]
    prev = verify.check_previous(run.parsed, seen)
    listed = [t for t in stats.in_period(prev.tickets, p) if filters.keeps(t)]
    if prev.count is not None and not filters.active:
        return PreviousOutcome(count=prev.count, sampled=run.capped)
    if listed or prev.tickets:
        return PreviousOutcome(count=len(listed), sampled=run.capped or run.status != "completed")
    return PreviousOutcome(count=0 if prev.count == 0 else None)
