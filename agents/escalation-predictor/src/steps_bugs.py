"""The bugs step: known open issues in the tracker for the top at-risk tickets, searched with keywords
taken from each ticket's own subject (chosen in code), read by a bounded tool loop and post-checked in
code (verify.check_issues). Matching is code (matching.py)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import coverage
import matching
import prompts
import records
import replies
import verify
from deps import TurnDeps
from discovery import discover
from reader import TurnReader
from records import Issue, Ticket

log = logging.getLogger("escalation.bugs")

BUG_LOOP_CALLS = 5  # plus one JSON repair = 6


@dataclass(frozen=True)
class BugsOutcome:
    issues: tuple[Issue, ...] = ()
    checked: bool = False         # the tracker was read
    extracted: int = 0
    dropped: int = 0
    capped: bool = False
    salvaged: bool = False
    notes: tuple[str, ...] = ()


def queries(tickets: Sequence[Ticket]) -> str:
    lines = (f"- ticket {t.id}: {' '.join(matching.keywords(t))}" for t in tickets if matching.keywords(t))
    return "\n".join(lines)


async def bugs_step(deps: TurnDeps, turn: TurnReader, tickets: Sequence[Ticket]) -> BugsOutcome:
    asked = queries(tickets)
    if not asked:
        return BugsOutcome(notes=(f"{replies.TRACKER_NOT_CHECKED}: no ticket subject had searchable words.",))
    catalog, failure = await discover(deps, "bugs")
    if failure is not None:
        reason = "no tracker tool is available to this agent" if failure == replies.NO_TOOLS else failure
        return BugsOutcome(notes=(f"{replies.TRACKER_NOT_CHECKED}: {reason}",))
    tools = catalog.source_tools("tracker")
    log.info("tracker tools offered: %s", [t.name for t in tools])
    tracker = coverage.assess("tracker", tools, catalog.source_of)
    if not tracker.ok:
        return BugsOutcome(notes=(f"{replies.TRACKER_NOT_CHECKED}. {coverage.line(tracker)}",))
    run = await turn.run_json(deps, tools, prompts.bugs_system(asked), "Find known open issues for these tickets.",
                              BUG_LOOP_CALLS, deps.config.bug_tool_calls, records.parse_issues)
    tracker = coverage.assess("tracker", tools, catalog.source_of, turn.health.dropped)
    if not tracker.ok:
        return BugsOutcome(notes=(f"{replies.TRACKER_NOT_CHECKED}. {coverage.line(tracker)}",))
    if run.status == "paused":
        return BugsOutcome(notes=(f"{replies.TRACKER_NOT_CHECKED}. {replies.READ_NEEDS_APPROVAL}",))
    if run.parsed is None:
        return BugsOutcome(notes=(f"{replies.TRACKER_NOT_CHECKED}: the tracker answer could not be verified.",
                                  *run.notes))
    kept, dropped = verify.check_issues(run.parsed, turn.seen_text)
    return BugsOutcome(kept, True, len(run.parsed), dropped, run.capped, run.salvaged, run.notes)
